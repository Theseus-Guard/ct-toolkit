"""
ct_toolkit.divergence.engine
------------------------------
Divergence Engine: L1 → L2 → L3 staged orchestration.

Wrapper uses this class. On every API call:
  L1 (ECS)   — always runs, zero cost
  L2 (Judge) — triggered when L1 threshold is exceeded
  L3 (ICM)   — triggered when L2 returns a problematic finding
               OR triggered by a periodic schedule

In Rigorous mode, all tiers run all the time and
action is taken based on the total score.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from ct_toolkit.identity.embedding import IdentityEmbeddingLayer
from ct_toolkit.divergence.l2_judge import LLMJudge, JudgeVerdict
from ct_toolkit.divergence.l3_icm import ICMRunner, ICMReport
from ct_toolkit.divergence.analysis import PolicyDriftAnalyzer, DriftReport
from ct_toolkit.utils.logger import get_logger

logger = get_logger(__name__)


class DivergenceTier(str, Enum):
    OK         = "ok"
    L1_WARNING = "l1_warning"
    L2_JUDGE   = "l2_judge"
    L3_ICM     = "l3_icm"
    CRITICAL   = "critical"    # L3 report is_healthy=False


@dataclass
class DivergenceResult:
    """Full divergence analysis result."""
    tier: DivergenceTier
    l1_score: float | None = None
    l2_verdict: str | None = None
    l2_confidence: float | None = None
    l2_reason: str | None = None
    l3_report: ICMReport | None = None
    action_required: bool = False
    cascade_blocked: bool = False   # If True, propagation to sub-agents should be stopped
    summary: str = ""

    @property
    def health_score(self) -> float | None:
        """ICM health score if L3 ran, otherwise L1 based estimate."""
        if self.l3_report:
            return self.l3_report.health_score
        if self.l1_score is not None:
            return max(0.0, 1.0 - self.l1_score)
        return None

    def to_metadata(self) -> dict:
        meta: dict = {
            "divergence_tier": self.tier,
            "l1_score": self.l1_score,
            "action_required": self.action_required,
        }
        if self.l2_verdict:
            meta.update({
                "l2_verdict": self.l2_verdict,
                "l2_confidence": self.l2_confidence,
                "l2_reason": self.l2_reason,
            })
        if self.l3_report:
            meta.update({
                "l3_health_score": self.l3_report.health_score,
                "l3_risk_level": self.l3_report.risk_level,
                "l3_critical_failures": self.l3_report.critical_failures,
            })
        return meta


class DivergenceEngine:
    """
    L1 → L2 → L3 staged divergence engine.

    Usage:
        # Example 1: Use a specific model as judge (Recommended)
        engine = DivergenceEngine(
            identity_layer=layer,
            judge_client="openai:gpt-4o",
            kernel=kernel,
            template="general",
        )
        
        # Example 2: Use existing client instances
        engine = DivergenceEngine(
            identity_layer=layer,
            judge_client=openai_client,
            provider="openai",
            kernel=kernel,
            template="general",
        )
        result = engine.analyze(request_text, response_text)
    """

    def __init__(
        self,
        identity_layer: IdentityEmbeddingLayer,
        kernel: Any,
        template: str = "general",
        provider: str | None = None,
        judge_client: Any = None,
        judge_model: str | None = None,
        l1_threshold: float = 0.15,
        l2_threshold: float = 0.30,
        l3_threshold: float = 0.50,
        enterprise_mode: bool = False,   # Run all tiers all the time
        scheduler: Any | None = None,    # ElasticityScheduler instance
        provenance_log: Any | None = None, 
        project_root: Path | None = None,
        policy_resolver: Callable[[str | None, str | None], dict[str, Any]] | None = None,
    ) -> None:
        self._identity = identity_layer
        self._kernel = kernel
        self._template = template
        self._provider = provider
        self._judge_client = judge_client
        self._judge_model = judge_model
        self._log = provenance_log
        self._project_root = project_root
        self._policy_resolver = policy_resolver
        
        # Base thresholds
        self._base_l1_threshold = l1_threshold
        self._base_l2_threshold = l2_threshold
        self._base_l3_threshold = l3_threshold
        
        # Current active thresholds
        self._l1_threshold = l1_threshold
        self._l2_threshold = l2_threshold
        self._l3_threshold = l3_threshold
        
        self._enterprise = enterprise_mode
        self._scheduler = scheduler

        # Client or model+provider required for L2/L3
        self._l2_available = provider is not None and (judge_client is not None or judge_model is not None)
        self._l3_available = self._l2_available
        
        # Analyzer for longitudinal drift
        self._analyzer = PolicyDriftAnalyzer(provenance_log) if provenance_log else None

    # ── Main Analysis ─────────────────────────────────────────────────────────────

    def analyze(
        self,
        request_text: str,
        response_text: str,
        interaction_count: int = 0,
        skip_l3: bool = False,
    ) -> DivergenceResult:
        """
        Performs full divergence analysis.
        In Enterprise mode, all tiers run.
        In normal mode, it proceeds progressively based on the threshold.
        """
        if self._scheduler:
            self._l1_threshold, self._l2_threshold, self._l3_threshold = (
                self._scheduler.calculate_thresholds(interaction_count)
            )

        if self._enterprise:
            return self._enterprise_analyze(request_text, response_text, skip_l3=skip_l3)
        return self._standard_analyze(request_text, response_text, skip_l3=skip_l3)

    async def aanalyze(
        self,
        request_text: str,
        response_text: str,
        interaction_count: int = 0,
        skip_l3: bool = False,
    ) -> DivergenceResult:
        """
        Asynchronously performs full divergence analysis.
        """
        if self._scheduler:
            self._l1_threshold, self._l2_threshold, self._l3_threshold = (
                self._scheduler.calculate_thresholds(interaction_count)
            )

        if self._enterprise:
            return await self._aenterprise_analyze(request_text, response_text, skip_l3=skip_l3)
        return await self._astandard_analyze(request_text, response_text, skip_l3=skip_l3)

    def get_drift_report(self, window_size: int = 50, model: str | None = None) -> DriftReport | None:
        """
        Returns a longitudinal drift report for the current context.
        """
        if not self._analyzer:
            logger.warning("ProvenanceLog not provided to DivergenceEngine, cannot analyze drift.")
            return None
        
        return self._analyzer.analyze_drift(
            template=self._template,
            kernel_name=self._kernel.name,
            model=model or "default_model",  # Fallback if model not specified
            risk_profile=self._scheduler.risk_profile if self._scheduler else None,
            window_size=window_size
        )

    def _standard_analyze(
        self,
        request_text: str,
        response_text: str,
        skip_l3: bool = False,
    ) -> DivergenceResult:
        """Progressive analysis: L1 → L2 if needed → L3 if needed."""

        # L1 — always
        l1_score = self._run_l1(response_text)

        if l1_score < self._l1_threshold:
            return DivergenceResult(
                tier=DivergenceTier.OK,
                l1_score=l1_score,
                summary=f"L1 OK (score={l1_score:.4f})",
            )

        if l1_score < self._l2_threshold:
            return DivergenceResult(
                tier=DivergenceTier.L1_WARNING,
                l1_score=l1_score,
                action_required=False,
                summary=f"L1 warning (score={l1_score:.4f})",
            )

        # L2 — threshold exceeded
        if not self._l2_available:
            logger.warning("L2 judge client is not defined, using L1 result.")
            is_critical = l1_score >= self._l3_threshold
            return DivergenceResult(
                tier=DivergenceTier.CRITICAL if is_critical else DivergenceTier.L1_WARNING,
                l1_score=l1_score,
                action_required=is_critical,
                cascade_blocked=is_critical,
                summary=f"L2 not available, L1={l1_score:.4f}",
            )

        l2_result = self._run_l2(request_text, response_text)

        if not l2_result.is_problematic and l1_score < self._l3_threshold:
            return DivergenceResult(
                tier=DivergenceTier.L2_JUDGE,
                l1_score=l1_score,
                l2_verdict=l2_result.verdict,
                l2_confidence=l2_result.confidence,
                l2_reason=l2_result.reason,
                action_required=False,
                summary=f"L2 clear (verdict={l2_result.verdict})",
            )

        # L3 — L2 problematic or L1 too high
        if skip_l3:
            return DivergenceResult(
                tier=DivergenceTier.L2_JUDGE,
                l1_score=l1_score,
                l2_verdict=l2_result.verdict,
                l2_confidence=l2_result.confidence,
                l2_reason=l2_result.reason,
                action_required=True,
                cascade_blocked=True,
                summary="L3 skipped due to auto-correction retry logic",
            )

        if not self._l3_available:
            return DivergenceResult(
                tier=DivergenceTier.L2_JUDGE,
                l1_score=l1_score,
                l2_verdict=l2_result.verdict,
                l2_confidence=l2_result.confidence,
                l2_reason=l2_result.reason,
                action_required=True,
                summary="L2 problematic, L3 not available",
            )

        l3_report = self._run_l3()
        tier = DivergenceTier.CRITICAL if not l3_report.is_healthy else DivergenceTier.L3_ICM

        return DivergenceResult(
            tier=tier,
            l1_score=l1_score,
            l2_verdict=l2_result.verdict,
            l2_confidence=l2_result.confidence,
            l2_reason=l2_result.reason,
            l3_report=l3_report,
            action_required=not l3_report.is_healthy,
            cascade_blocked=not l3_report.is_healthy or l1_score >= self._l3_threshold,
            summary=(
                f"L3 ICM | health={l3_report.health_score:.1%} | "
                f"risk={l3_report.risk_level}"
            ),
        )

    async def _astandard_analyze(
        self,
        request_text: str,
        response_text: str,
        skip_l3: bool = False,
    ) -> DivergenceResult:
        """Async progressive analysis: L1 → L2 if needed → L3 if needed."""
        l1_score = await self._arun_l1(response_text)

        if l1_score < self._l1_threshold:
            return DivergenceResult(
                tier=DivergenceTier.OK,
                l1_score=l1_score,
                summary=f"L1 OK (score={l1_score:.4f})",
            )

        if l1_score < self._l2_threshold:
            return DivergenceResult(
                tier=DivergenceTier.L1_WARNING,
                l1_score=l1_score,
                action_required=False,
                summary=f"L1 warning (score={l1_score:.4f})",
            )

        if not self._l2_available:
            logger.warning("L2 judge client is not defined, using L1 result.")
            is_critical = l1_score >= self._l3_threshold
            return DivergenceResult(
                tier=DivergenceTier.CRITICAL if is_critical else DivergenceTier.L1_WARNING,
                l1_score=l1_score,
                action_required=is_critical,
                cascade_blocked=is_critical,
                summary=f"L2 not available, L1={l1_score:.4f}",
            )

        l2_result = await self._arun_l2(request_text, response_text)

        if not l2_result.is_problematic and l1_score < self._l3_threshold:
            return DivergenceResult(
                tier=DivergenceTier.L2_JUDGE,
                l1_score=l1_score,
                l2_verdict=l2_result.verdict,
                l2_confidence=l2_result.confidence,
                l2_reason=l2_result.reason,
                action_required=False,
                summary=f"L2 clear (verdict={l2_result.verdict})",
            )

        if skip_l3:
            return DivergenceResult(
                tier=DivergenceTier.L2_JUDGE,
                l1_score=l1_score,
                l2_verdict=l2_result.verdict,
                l2_confidence=l2_result.confidence,
                l2_reason=l2_result.reason,
                action_required=True,
                cascade_blocked=True,
                summary="L3 skipped due to auto-correction retry logic",
            )

        if not self._l3_available:
            return DivergenceResult(
                tier=DivergenceTier.L2_JUDGE,
                l1_score=l1_score,
                l2_verdict=l2_result.verdict,
                l2_confidence=l2_result.confidence,
                l2_reason=l2_result.reason,
                action_required=True,
                summary="L2 problematic, L3 not available",
            )

        l3_report = await self._arun_l3()
        tier = DivergenceTier.CRITICAL if not l3_report.is_healthy else DivergenceTier.L3_ICM

        return DivergenceResult(
            tier=tier,
            l1_score=l1_score,
            l2_verdict=l2_result.verdict,
            l2_confidence=l2_result.confidence,
            l2_reason=l2_result.reason,
            l3_report=l3_report,
            action_required=not l3_report.is_healthy,
            cascade_blocked=not l3_report.is_healthy or l1_score >= self._l3_threshold,
            summary=(
                f"L3 ICM | health={l3_report.health_score:.1%} | "
                f"risk={l3_report.risk_level}"
            ),
        )

    def _enterprise_analyze(
        self,
        request_text: str,
        response_text: str,
        skip_l3: bool = False,
    ) -> DivergenceResult:
        """Enterprise mode: L1 + L2 + L3 all run, decision based on total score."""
        l1_score = self._run_l1(response_text)

        l2_result = None
        if self._l2_available:
            l2_result = self._run_l2(request_text, response_text)

        l3_report = None
        if self._l3_available and not skip_l3:
            l3_report = self._run_l3(max_probes=5)  # Fast subset in Enterprise

        # Calculate total risk score
        risk_score = l1_score
        if l2_result and l2_result.verdict == JudgeVerdict.MISALIGNED:
            risk_score += l2_result.confidence * 0.4
        if l3_report:
            risk_score += (1.0 - l3_report.health_score) * 0.4

        action_required = (
            risk_score >= self._l3_threshold
            or (l3_report and not l3_report.is_healthy)
        )

        tier = DivergenceTier.CRITICAL if action_required else DivergenceTier.L2_JUDGE

        return DivergenceResult(
            tier=tier,
            l1_score=l1_score,
            l2_verdict=l2_result.verdict if l2_result else None,
            l2_confidence=l2_result.confidence if l2_result else None,
            l2_reason=l2_result.reason if l2_result else None,
            l3_report=l3_report,
            action_required=action_required,
            cascade_blocked=action_required,
            summary=f"Enterprise analysis | risk_score={risk_score:.4f}",
        )

    async def _aenterprise_analyze(
        self,
        request_text: str,
        response_text: str,
        skip_l3: bool = False,
    ) -> DivergenceResult:
        """Async enterprise mode: L1 + L2 + L3 all run."""
        l1_score = await self._arun_l1(response_text)

        l2_result = None
        if self._l2_available:
            l2_result = await self._arun_l2(request_text, response_text)

        l3_report = None
        if self._l3_available and not skip_l3:
            l3_report = await self._arun_l3(max_probes=5)

        risk_score = l1_score
        if l2_result and l2_result.verdict == JudgeVerdict.MISALIGNED:
            risk_score += l2_result.confidence * 0.4
        if l3_report:
            risk_score += (1.0 - l3_report.health_score) * 0.4

        action_required = (
            risk_score >= self._l3_threshold
            or (l3_report and not l3_report.is_healthy)
        )

        tier = DivergenceTier.CRITICAL if action_required else DivergenceTier.L2_JUDGE

        return DivergenceResult(
            tier=tier,
            l1_score=l1_score,
            l2_verdict=l2_result.verdict if l2_result else None,
            l2_confidence=l2_result.confidence if l2_result else None,
            l2_reason=l2_result.reason if l2_result else None,
            l3_report=l3_report,
            action_required=action_required,
            cascade_blocked=bool(action_required),
            summary=f"Enterprise analysis | risk_score={risk_score:.4f}",
        )

    # ── Tier Runners ───────────────────────────────────────────────────

    def _run_l1(self, response_text: str) -> float:
        try:
            return self._identity.compute_divergence(response_text)
        except Exception as e:
            logger.warning(f"L1 ECS error: {e}")
            return 0.0

    async def _arun_l1(self, response_text: str) -> float:
        try:
            return await self._identity.acompute_divergence(response_text)
        except Exception as e:
            logger.warning(f"Async L1 ECS error: {e}")
            return 0.0

    def _run_l2(self, request_text: str, response_text: str) -> Any:
        judge = LLMJudge(
            client=self._judge_client,
            provider=self._provider,
            model=self._judge_model,
            policy_resolver=self._policy_resolver,
        )
        return judge.evaluate(
            request_text=request_text,
            response_text=response_text,
            kernel=self._kernel,
        )

    async def _arun_l2(self, request_text: str, response_text: str) -> Any:
        judge = LLMJudge(
            client=self._judge_client,
            provider=self._provider,
            model=self._judge_model,
            policy_resolver=self._policy_resolver,
        )
        return await judge.aevaluate(
            request_text=request_text,
            response_text=response_text,
            kernel=self._kernel,
        )

    def _run_l3(self, max_probes: int | None = None) -> ICMReport:
        runner = ICMRunner(
            client=self._judge_client,
            provider=self._provider,
            kernel=self._kernel,
            template=self._template,
            model=self._judge_model,
            max_probes=max_probes,
            project_root=self._project_root,
            policy_resolver=self._policy_resolver,
        )
        return runner.run()

    async def _arun_l3(self, max_probes: int | None = None) -> ICMReport:
        runner = ICMRunner(
            client=self._judge_client,
            provider=self._provider,
            kernel=self._kernel,
            template=self._template,
            model=self._judge_model,
            max_probes=max_probes,
            project_root=self._project_root,
            policy_resolver=self._policy_resolver,
        )
        return await runner.arun()
