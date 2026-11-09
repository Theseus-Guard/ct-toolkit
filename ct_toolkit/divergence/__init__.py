"""
ct_toolkit.divergence
=====================
Divergence detection and correction engine.

This module provides:
- DivergenceEngine: Multi-tier divergence detection (L1 ECS, L2 Judge, L3 ICM)
- LLMJudge: L2 LLM-as-judge detector
- ICMRunner: L3 Identity Consistency Metric probe battery
- PolicyDriftAnalyzer: Longitudinal drift analysis
- DivergencePenaltyLoss: Training loss function
- ElasticityScheduler: Dynamic threshold scheduling
"""

from ct_toolkit.divergence.analysis import PolicyDriftAnalyzer, DriftReport, SSCSeverityCalculator
from ct_toolkit.divergence.engine import DivergenceEngine
from ct_toolkit.divergence.l2_judge import LLMJudge, JudgeVerdict, JudgeResponse, JudgeResult
from ct_toolkit.divergence.l3_icm import (
    ICMRunner,
    ICMReport,
    ProbeResult,
    BehaviorClassifier,
    ProbeResponse,
    ICM_TIMEOUT_SECONDS,
    ICM_MAX_RETRIES,
)
from ct_toolkit.divergence.scheduler import RiskProfile, ElasticityScheduler

# DivergencePenaltyLoss and compute_alignment_loss need torch, an optional dependency (the `ml` extra).
# They are imported on first use, so the core package installs and imports without torch.
_TORCH_LOSS_NAMES = ("DivergencePenaltyLoss", "compute_alignment_loss")


def __getattr__(name: str):
    if name in _TORCH_LOSS_NAMES:
        from ct_toolkit.divergence import loss

        return getattr(loss, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Engine
    "DivergenceEngine",
    # L2 Judge
    "LLMJudge",
    "JudgeVerdict",
    "JudgeResponse",
    "JudgeResult",
    # L3 ICM
    "ICMRunner",
    "ICMReport",
    "ProbeResult",
    "BehaviorClassifier",
    "ProbeResponse",
    "ICM_TIMEOUT_SECONDS",
    "ICM_MAX_RETRIES",
    # Analysis
    "PolicyDriftAnalyzer",
    "DriftReport",
    "SSCSeverityCalculator",
    # Loss
    "DivergencePenaltyLoss",
    "compute_alignment_loss",
    # Scheduler
    "RiskProfile",
    "ElasticityScheduler",
]