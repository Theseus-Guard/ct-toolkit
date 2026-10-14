"""Tests for TASK-3: Background execution of Shadow Requests and L3 ICM with Circuit Breaker."""
import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from ct_toolkit.core.circuit_breaker import CircuitBreakerError
from ct_toolkit.core.kernel import ConstitutionalKernel
from ct_toolkit.core.wrapper import TheseusWrapper, WrapperConfig
from ct_toolkit.divergence.engine import DivergenceEngine, DivergenceTier
from ct_toolkit.divergence.l3_icm import ICMReport
from ct_toolkit.endorsement.reflective import EndorsementDecision, EndorsementRecord


def _make_mock_llm_response(content: str = "Safe compliant response.") -> MagicMock:
    mock_res = MagicMock()
    mock_res.choices = [MagicMock(message=MagicMock(content=content))]
    mock_res.model = "gpt-4o-mini"
    return mock_res


def _stage_dummy_rule(wrapper: TheseusWrapper) -> EndorsementRecord:
    commitment_id = wrapper.kernel.commitments[0].id
    record = EndorsementRecord(
        id="staged-test-1",
        timestamp=time.time(),
        conflict_id="conf-1",
        rule_text="Always include polite greeting.",
        commitment_id=commitment_id,
        decision=EndorsementDecision.STAGED,
        operator_id="admin",
        rationale="Testing shadow execution",
        kernel_name=wrapper.kernel.name,
        cooldown_duration_s=3600,
        cooldown_until=time.time() + 3600,
    )
    wrapper._staged_manager.register(record)
    return record


def test_shadow_requests_run_in_background_without_blocking(tmp_path) -> None:
    """When shadow_background=True, slow shadow requests do not block main chat()."""
    config = WrapperConfig(
        vault_path=str(tmp_path / "prov.db"),
        shadow_background=True,
        divergence_l1_threshold=0.95,
        divergence_l2_threshold=0.99,
    )
    wrapper = TheseusWrapper(client=MagicMock(), config=config, provider="openai")
    _stage_dummy_rule(wrapper)

    call_count = 0
    shadow_completed = False

    def slow_shadow_provider(messages, model=None, **kwargs):
        nonlocal call_count, shadow_completed
        call_count += 1
        if call_count == 1:
            return _make_mock_llm_response("Main response is immediate.")
        time.sleep(0.25)
        shadow_completed = True
        return _make_mock_llm_response("Shadow response polite greeting.")

    with (
        patch.object(wrapper, "_call_provider", side_effect=slow_shadow_provider),
        patch.object(wrapper._identity_layer, "compute_divergence", return_value=0.05),
    ):
        start = time.perf_counter()
        response = wrapper.chat("Hello there")
        elapsed = time.perf_counter() - start

        # Main response returns before the 0.25s shadow request finishes
        assert response.content == "Main response is immediate."
        assert elapsed < 0.20
        assert shadow_completed is False

        wrapper.wait_for_background_tasks(timeout=2.0)
        assert shadow_completed is True
        assert call_count == 2


@pytest.mark.asyncio
async def test_async_shadow_requests_run_in_background(tmp_path) -> None:
    """When shadow_background=True, slow async shadow requests do not block achat()."""
    config = WrapperConfig(
        vault_path=str(tmp_path / "prov_async.db"),
        shadow_background=True,
        divergence_l1_threshold=0.95,
        divergence_l2_threshold=0.99,
    )
    wrapper = TheseusWrapper(client=MagicMock(), config=config, provider="openai")
    _stage_dummy_rule(wrapper)

    call_count = 0
    shadow_completed = False

    async def slow_async_provider(messages, model=None, **kwargs):
        nonlocal call_count, shadow_completed
        call_count += 1
        if call_count == 1:
            return _make_mock_llm_response("Fast async main response.")
        await asyncio.sleep(0.25)
        shadow_completed = True
        return _make_mock_llm_response("Concise shadow response.")

    with (
        patch.object(wrapper, "_acall_provider", side_effect=slow_async_provider),
        patch.object(wrapper._identity_layer, "acompute_divergence", return_value=0.05),
    ):
        start = time.perf_counter()
        response = await wrapper.achat("Hello async")
        elapsed = time.perf_counter() - start

        assert response.content == "Fast async main response."
        assert elapsed < 0.20
        assert shadow_completed is False

        await wrapper.await_background_tasks(timeout=2.0)
        assert shadow_completed is True
        assert call_count == 2


def test_l3_icm_background_and_circuit_breaker_trip(tmp_path) -> None:
    """When l3_background=True, critical L3 failure trips the circuit breaker for subsequent calls."""
    config = WrapperConfig(
        vault_path=str(tmp_path / "prov_l3.db"),
        l3_background=True,
        circuit_breaker_on_critical=True,
        divergence_l1_threshold=0.10,
        divergence_l2_threshold=0.20,
        divergence_l3_threshold=0.40,
        judge_client=MagicMock(),
    )
    wrapper = TheseusWrapper(client=MagicMock(), config=config, provider="openai")

    mock_l2_result = MagicMock()
    mock_l2_result.verdict = "critical_drift"
    mock_l2_result.confidence = 0.9
    mock_l2_result.reason = "Potential drift detected, escalating to L3."
    mock_l2_result.is_problematic = True

    unhealthy_report = ICMReport(
        timestamp=time.time(),
        kernel_name="default",
        template_name="general",
        total_probes=5,
        passed=1,
        failed=4,
        health_score=0.20,
        critical_failures=["p1"],
        results=[],
    )

    def slow_l3(*args, **kwargs):
        time.sleep(0.25)
        return unhealthy_report

    with (
        patch.object(wrapper, "_call_provider", return_value=_make_mock_llm_response("Suspicious output")),
        patch.object(wrapper._divergence_engine, "_run_l1", return_value=0.50),
        patch.object(wrapper._divergence_engine, "_run_l2", return_value=mock_l2_result),
        patch.object(wrapper._divergence_engine, "_run_l3", side_effect=slow_l3),
    ):
        start = time.perf_counter()
        res = wrapper.chat("Trigger background L3")
        elapsed = time.perf_counter() - start

        # Returned before slow L3 finished
        assert elapsed < 0.20
        assert res.divergence_tier == DivergenceTier.L3_ICM.value

        # Wait for background L3 to complete
        wrapper.wait_for_background_tasks(timeout=2.0)

        # Now the circuit breaker should be tripped!
        assert wrapper._divergence_engine.circuit_breaker_tripped is True
        assert wrapper._divergence_engine.last_l3_report is unhealthy_report

        with pytest.raises(CircuitBreakerError):
            wrapper.chat("Second request should be blocked by circuit breaker")

        # Reset circuit breaker and verify state is cleared
        wrapper.reset_circuit_breaker()
        assert wrapper._divergence_engine.circuit_breaker_tripped is False


@pytest.mark.asyncio
async def test_async_l3_icm_background_and_circuit_breaker() -> None:
    """Async aanalyze with l3_background=True runs L3 in background and trips circuit breaker."""
    kernel = ConstitutionalKernel.default()
    tripped_reports: list[ICMReport] = []
    mock_identity = MagicMock()

    engine = DivergenceEngine(
        identity_layer=mock_identity,
        kernel=kernel,
        provider="openai",
        judge_client=MagicMock(),
        l1_threshold=0.10,
        l2_threshold=0.20,
        l3_threshold=0.40,
        l3_background=True,
        on_critical_callback=lambda rep: tripped_reports.append(rep),
    )

    mock_l2_result = MagicMock()
    mock_l2_result.verdict = "critical_drift"
    mock_l2_result.confidence = 0.85
    mock_l2_result.reason = "Escalating to async L3."
    mock_l2_result.is_problematic = True

    unhealthy_report = ICMReport(
        timestamp=time.time(),
        kernel_name="default",
        template_name="general",
        total_probes=4,
        passed=0,
        failed=4,
        health_score=0.0,
        critical_failures=["p1"],
        results=[],
    )

    async def slow_arun_l3(*args, **kwargs):
        await asyncio.sleep(0.20)
        return unhealthy_report

    with (
        patch.object(engine, "_arun_l1", return_value=0.55),
        patch.object(engine, "_arun_l2", return_value=mock_l2_result),
        patch.object(engine, "_arun_l3", side_effect=slow_arun_l3),
    ):
        start = time.perf_counter()
        res = await engine.aanalyze(
            request_text="Test",
            response_text="Response",
        )
        elapsed = time.perf_counter() - start

        assert elapsed < 0.15
        assert res.tier == DivergenceTier.L3_ICM

        await engine.await_background_l3(timeout=2.0)

        assert engine.circuit_breaker_tripped is True
        assert len(tripped_reports) == 1
        assert tripped_reports[0].health_score == 0.0
