from types import SimpleNamespace
from unittest.mock import MagicMock

from ct_toolkit.core.wrapper import TheseusWrapper, WrapperConfig
from tests.unit.test_tool_guard import OPENAI_TOOLS, openai_response


class TestWrapperIntegration:
    def _wrapper(self, tmp_path, **config_kwargs):
        client = MagicMock()
        client.__class__.__module__ = "openai"
        config = WrapperConfig(vault_path=str(tmp_path / "vault.db"), **config_kwargs)
        return TheseusWrapper(client, config)

    def test_review_records_every_decision_in_provenance_chain(self, tmp_path):
        wrapper = self._wrapper(tmp_path)
        raw = openai_response(("bash", '{"command": "ls"}'), ("shell_exec", "{}"))
        returned, decisions = wrapper._review_tool_calls(
            raw, "list files", None, None, "gpt-4o-mini", {"tools": OPENAI_TOOLS}
        )

        assert returned is raw  # observe mode: nothing removed
        assert [d.verdict for d in decisions] == ["passed", "flagged"]
        events = [entry for entry in wrapper.export_provenance_log() if entry["metadata"].get("event") == "tool_call"]
        assert [e["metadata"]["verdict"] for e in events] == ["passed", "flagged"]
        assert events[1]["metadata"]["tool"] == "shell_exec"
        assert wrapper._provenance_log.verify_chain()

    def test_enforce_mode_returns_copy_without_blocked_call(self, tmp_path):
        wrapper = self._wrapper(tmp_path, tool_guard_mode="enforce")
        raw = openai_response(("shell_exec", "{}"))
        returned, decisions = wrapper._review_tool_calls(
            raw, "do it", None, None, "gpt-4o-mini", {"tools": OPENAI_TOOLS}
        )

        assert decisions[0].blocked
        assert returned["choices"][0]["message"]["tool_calls"] == []
        assert len(raw["choices"][0]["message"]["tool_calls"]) == 1

    def test_guard_disabled_skips_review_and_audit(self, tmp_path):
        wrapper = self._wrapper(tmp_path, tool_guard=False)
        raw = openai_response(("shell_exec", "{}"))
        returned, decisions = wrapper._review_tool_calls(raw, "x", None, None, "m", {})
        assert returned is raw and decisions == []
        assert [e for e in wrapper.export_provenance_log() if e["metadata"].get("event") == "tool_call"] == []

    def test_tool_entries_do_not_inflate_interaction_count(self, tmp_path):
        wrapper = self._wrapper(tmp_path)
        wrapper._review_tool_calls(openai_response(("shell_exec", "{}")), "x", None, None, "gpt-4o-mini", {})
        assert wrapper._provenance_log.get_interaction_count("general", "default", "gpt-4o-mini") == 0


class TestServerGuardrail:
    def _wrapper(self, tmp_path, **config_kwargs):
        client = MagicMock()
        client.__class__.__module__ = "openai"
        return TheseusWrapper(client, WrapperConfig(vault_path=str(tmp_path / "v.db"), **config_kwargs))

    def _request(self, tool_calls):
        from ct_toolkit.server import GuardrailRequest

        return GuardrailRequest(input_type="response", tools=OPENAI_TOOLS, tool_calls=tool_calls)

    def test_tool_only_response_is_blocked_in_enforce(self, tmp_path, monkeypatch):
        from ct_toolkit import server

        monkeypatch.setattr(server, "_wrapper_instance", self._wrapper(tmp_path, tool_guard_mode="enforce"))
        calls = [{"id": "c", "type": "function", "function": {"name": "shell_exec", "arguments": "{}"}}]
        reason = server._review_guardrail_tool_calls(self._request(calls), "do it")
        assert reason is not None and "shell_exec" in reason

    def test_tool_only_response_is_only_recorded_in_observe(self, tmp_path, monkeypatch):
        from ct_toolkit import server

        wrapper = self._wrapper(tmp_path)
        monkeypatch.setattr(server, "_wrapper_instance", wrapper)
        calls = [{"id": "c", "type": "function", "function": {"name": "shell_exec", "arguments": "{}"}}]
        assert server._review_guardrail_tool_calls(self._request(calls), "do it") is None
        assert any(e["metadata"].get("event") == "tool_call" for e in wrapper.export_provenance_log())


def _chunk(content=None, tool_call=None, finish=None, model="gpt-4o-mini"):
    """A streaming chunk shaped like an OpenAI delta; `tool_call` is (index, id, name, arguments)."""
    calls = None
    if tool_call is not None:
        index, call_id, name, arguments = tool_call
        calls = [SimpleNamespace(index=index, id=call_id, function=SimpleNamespace(name=name, arguments=arguments))]
    delta = SimpleNamespace(content=content, tool_calls=calls)
    return SimpleNamespace(model=model, choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


def _stream_chunks():
    return [
        _chunk(content="Running the tests."),
        _chunk(tool_call=(0, "c1", "bash", '{"command": "pytest -q"}')),
        _chunk(tool_call=(1, "c2", "shell_exec", "{}")),
        _chunk(finish="tool_calls"),
    ]


def _stub_wrapper(tmp_path, mode):
    client = MagicMock()
    client.__class__.__module__ = "openai"
    wrapper = TheseusWrapper(client, WrapperConfig(vault_path=str(tmp_path / "s.db"), tool_guard_mode=mode))
    wrapper._run_divergence_engine = MagicMock(return_value=SimpleNamespace(l1_score=0.0, tier=SimpleNamespace(value="ok")))
    return wrapper


class TestStreamingToolCalls:
    def test_observe_yields_everything_and_reports_at_the_end(self, tmp_path):
        wrapper = _stub_wrapper(tmp_path, "observe")
        wrapper._call_provider = MagicMock(return_value=iter(_stream_chunks()))
        chunks = list(wrapper.stream_chat("run the tests", tools=OPENAI_TOOLS))

        final = chunks[-1]
        assert final.is_final
        assert [d.verdict for d in final.tool_decisions] == ["passed", "flagged"]
        assert sum(1 for c in chunks if c.raw_chunk is not None and c.raw_chunk.choices[0].delta.tool_calls) == 2

    def test_enforce_releases_held_chunks_without_the_blocked_call(self, tmp_path):
        wrapper = _stub_wrapper(tmp_path, "enforce")
        wrapper._call_provider = MagicMock(return_value=iter(_stream_chunks()))
        chunks = list(wrapper.stream_chat("run the tests", tools=OPENAI_TOOLS))

        # The text before the first tool call streams live, untouched.
        assert chunks[0].delta == "Running the tests."
        released = [c.raw_chunk.choices[0].delta.tool_calls for c in chunks if c.raw_chunk and c.raw_chunk.choices[0].delta.tool_calls]
        assert [[call.function.name for call in calls] for calls in released] == [["bash"]]
        assert chunks[-1].tool_decisions[1].blocked
        # One call is still allowed, so the finish reason stays.
        assert chunks[-2].raw_chunk.choices[0].finish_reason == "tool_calls"

    def test_enforce_turns_finish_reason_to_stop_when_all_calls_are_blocked(self, tmp_path):
        wrapper = _stub_wrapper(tmp_path, "enforce")
        only_bad = [_chunk(tool_call=(0, "c2", "shell_exec", "{}")), _chunk(finish="tool_calls")]
        wrapper._call_provider = MagicMock(return_value=iter(only_bad))
        chunks = list(wrapper.stream_chat("run the tests", tools=OPENAI_TOOLS))

        assert all(not (c.raw_chunk and c.raw_chunk.choices[0].delta.tool_calls) for c in chunks)
        assert [c.raw_chunk.choices[0].finish_reason for c in chunks if c.raw_chunk and c.raw_chunk.choices[0].finish_reason] == ["stop"]

    def test_async_stream_enforces_the_same_way(self, tmp_path):
        import asyncio

        wrapper = _stub_wrapper(tmp_path, "enforce")

        async def chunks_async():
            for chunk in _stream_chunks():
                yield chunk

        async def fake_acall(*args, **kwargs):
            return chunks_async()

        wrapper._acall_provider = fake_acall

        async def collect():
            return [c async for c in wrapper.astream_chat("run the tests", tools=OPENAI_TOOLS)]

        chunks = asyncio.run(collect())
        assert chunks[0].delta == "Running the tests."
        assert chunks[-1].tool_decisions[1].blocked
        names = [call.function.name for c in chunks if c.raw_chunk and c.raw_chunk.choices[0].delta.tool_calls for call in c.raw_chunk.choices[0].delta.tool_calls]
        assert names == ["bash"]
