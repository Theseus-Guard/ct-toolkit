"""
Tests for async/await (achat, aanalyze, aevaluate, arun, _agenerate)
and streaming guardrail support (stream_chat, astream_chat).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from ct_toolkit import CTResponse, CTStreamChunk, TheseusWrapper, WrapperConfig
from ct_toolkit.core.kernel import ConstitutionalKernel
from ct_toolkit.divergence.engine import DivergenceEngine, DivergenceTier
from ct_toolkit.divergence.l2_judge import JudgeVerdict
from ct_toolkit.divergence.l3_icm import ICMRunner
from ct_toolkit.identity.embedding import IdentityEmbeddingLayer


def _make_mock_completion(content: str, model: str = "gpt-4o-mini") -> dict:
    return {
        "model": model,
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                }
            }
        ],
    }


def _make_stream_chunks(tokens: list[str], model: str = "gpt-4o-mini") -> list[dict]:
    return [
        {
            "model": model,
            "choices": [{"delta": {"content": token}}],
        }
        for token in tokens
    ]


class _FakeClient:
    provider = "openai"
    api_key = "test-key"


def test_achat_basic(tmp_path):
    """Test async chat (achat) end-to-end with divergence and provenance logging."""
    db_path = str(tmp_path / "async_prov.db")
    cfg = WrapperConfig(
        vault_path=db_path,
        capability_cache_file=str(tmp_path / "cap.yaml"),
    )
    wrapper = TheseusWrapper(client=_FakeClient(), config=cfg)

    with patch(
        "litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_make_mock_completion(
            "I am a helpful, honest, and harmless AI assistant."
        ),
    ) as mock_acomp:
        response = asyncio.run(wrapper.achat("Hello, who are you?", model="gpt-4o-mini"))

    assert isinstance(response, CTResponse)
    assert "helpful" in response.content
    assert response.divergence_score is not None
    assert response.provenance_id is not None
    mock_acomp.assert_awaited_once()

    entries = wrapper.export_provenance_log()
    assert len(entries) == 1
    assert entries[0]["metadata"]["async"] is True


def test_stream_chat_sync(tmp_path):
    """Test synchronous streaming (stream_chat and chat(stream=True))."""
    db_path = str(tmp_path / "stream_prov.db")
    cfg = WrapperConfig(
        vault_path=db_path,
        capability_cache_file=str(tmp_path / "cap.yaml"),
    )
    wrapper = TheseusWrapper(client=_FakeClient(), config=cfg)
    tokens = ["I am ", "a helpful ", "and harmless ", "assistant."]

    with patch(
        "litellm.completion",
        return_value=iter(_make_stream_chunks(tokens)),
    ):
        chunks = list(wrapper.chat("Tell me about yourself", stream=True))

    # 4 token chunks + 1 final metadata chunk
    assert len(chunks) == 5
    for c in chunks[:-1]:
        assert isinstance(c, CTStreamChunk)
        assert not c.is_final
        assert c.divergence_score is None

    final_chunk = chunks[-1]
    assert final_chunk.is_final is True
    assert final_chunk.accumulated_content == "I am a helpful and harmless assistant."
    assert final_chunk.divergence_score is not None
    assert final_chunk.divergence_tier is not None
    assert final_chunk.provenance_id is not None

    entries = wrapper.export_provenance_log()
    assert len(entries) == 1
    assert entries[0]["metadata"]["stream"] is True


def test_astream_chat_async(tmp_path):
    """Test asynchronous streaming (astream_chat and achat(stream=True))."""
    db_path = str(tmp_path / "astream_prov.db")
    cfg = WrapperConfig(
        vault_path=db_path,
        capability_cache_file=str(tmp_path / "cap.yaml"),
    )
    wrapper = TheseusWrapper(client=_FakeClient(), config=cfg)
    tokens = ["Safety ", "and honesty ", "are core principles."]

    async def _fake_async_gen():
        for chunk in _make_stream_chunks(tokens):
            yield chunk

    async def _run():
        with patch(
            "litellm.acompletion",
            new_callable=AsyncMock,
            return_value=_fake_async_gen(),
        ):
            stream_iter = await wrapper.achat("What are your principles?", stream=True)
            collected = []
            async for chunk in stream_iter:
                collected.append(chunk)
            return collected

    chunks = asyncio.run(_run())
    assert len(chunks) == 4
    final_chunk = chunks[-1]
    assert final_chunk.is_final is True
    assert final_chunk.accumulated_content == "Safety and honesty are core principles."
    assert final_chunk.divergence_score is not None
    assert final_chunk.provenance_id is not None


def test_async_divergence_engine_l2_and_l3():
    """Test DivergenceEngine.aanalyze with L2 LLMJudge.aevaluate and L3 ICMRunner.arun."""
    kernel = ConstitutionalKernel.default()
    layer = IdentityEmbeddingLayer(template="general", embedding_client=None)

    engine = DivergenceEngine(
        identity_layer=layer,
        kernel=kernel,
        template="general",
        provider="openai",
        judge_client=_FakeClient(),
        l1_threshold=0.01,
        l2_threshold=0.02,
        l3_threshold=0.95,
    )

    judge_json = '{"verdict": "aligned", "confidence": 0.95, "reason": "Follows constitution", "violated_anchors": []}'

    with patch(
        "litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_make_mock_completion(judge_json),
    ):
        result = asyncio.run(engine.aanalyze("Hi", "I try to be helpful in my answer."))

    assert result.tier == DivergenceTier.L2_JUDGE
    assert result.l2_verdict == JudgeVerdict.ALIGNED


def test_async_icm_runner():
    """Test ICMRunner.arun executes probes concurrently using litellm.acompletion."""
    kernel = ConstitutionalKernel.default()
    runner = ICMRunner(
        client=_FakeClient(),
        provider="openai",
        kernel=kernel,
        template="general",
        max_probes=2,
    )

    with patch(
        "litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_make_mock_completion(
            "<think>Evaluating ethical constraints</think>\nI cannot fulfill this request because of safety guidelines."
        ),
    ) as mock_acomp:
        report = asyncio.run(runner.arun())

    assert report.total_probes == 2
    assert mock_acomp.await_count == 2
