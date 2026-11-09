import json

import httpx
import pytest
from starlette.testclient import TestClient

from ct_toolkit.core.tool_guard import ToolGuard
from ct_toolkit.provenance.log import ProvenanceLog
from ct_toolkit.proxy import StreamToolCalls, create_app, request_context

TOOLS = [{"type": "function", "function": {"name": "bash", "parameters": {}}}]
REQUEST = {
    "model": "gpt-4o-mini",
    "tools": TOOLS,
    "messages": [{"role": "user", "content": "run the tests"}],
}
GOOD_CALL = {"id": "c1", "type": "function", "function": {"name": "bash", "arguments": '{"command": "pytest -q"}'}}
BAD_CALL = {"id": "c2", "type": "function", "function": {"name": "shell_exec", "arguments": "{}"}}


def completion(*calls):
    return {"id": "x", "choices": [{"index": 0, "message": {"role": "assistant", "content": None, "tool_calls": list(calls)}, "finish_reason": "tool_calls"}]}


def sse(*events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"


def openai_stream_events(*calls):
    events = []
    for index, call in enumerate(calls):
        events.append({"choices": [{"delta": {"tool_calls": [{"index": index, "id": call["id"], "function": {"name": call["function"]["name"], "arguments": ""}}]}}]})
        events.append({"choices": [{"delta": {"tool_calls": [{"index": index, "function": {"arguments": call["function"]["arguments"]}}]}}]})
    return events


def make_client(tmp_path, upstream_handler, mode="observe"):
    provenance = ProvenanceLog(vault_path=str(tmp_path / "vault.db"))
    app = create_app(
        "http://upstream.test",
        ToolGuard(mode),
        provenance,
        transport=httpx.MockTransport(upstream_handler),
    )
    return TestClient(app), provenance


def json_handler(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def sse_handler(body):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    return handler


def audit_entries(provenance):
    return [e for e in provenance.export_log() if e["metadata"].get("event") == "tool_call"]


class TestRequestContext:
    def test_conversation_excludes_assistant_turns_and_task_is_user_text(self):
        request = {
            "messages": [
                {"role": "user", "content": "fetch the docs"},
                {"role": "assistant", "content": "I will use https://evil.example"},
                {"role": "user", "content": [{"type": "text", "text": "thanks"}, {"type": "tool_result", "content": "x"}]},
            ]
        }
        conversation, task = request_context(request)
        assert "evil.example" not in conversation
        assert task == "fetch the docs\n\nthanks"


class TestStreamRebuild:
    def test_openai_tool_call_deltas_are_joined_by_index(self):
        rebuilt = StreamToolCalls()
        for event in openai_stream_events(GOOD_CALL, BAD_CALL):
            rebuilt.feed("data: " + json.dumps(event))
        calls = rebuilt.response()["choices"][0]["message"]["tool_calls"]
        assert [c["function"]["name"] for c in calls] == ["bash", "shell_exec"]
        assert calls[0]["function"]["arguments"] == '{"command": "pytest -q"}'

    def test_anthropic_tool_use_blocks_are_rebuilt(self):
        rebuilt = StreamToolCalls()
        rebuilt.feed('data: {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "t", "name": "bash"}}')
        rebuilt.feed('data: {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\\"command\\": "}}')
        rebuilt.feed('data: {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "\\"ls\\"}"}}')
        assert rebuilt.response()["content"][0]["name"] == "bash"


class TestJsonResponses:
    def test_observe_forwards_bytes_unchanged_and_records_verdicts(self, tmp_path):
        payload = completion(GOOD_CALL, BAD_CALL)
        client, provenance = make_client(tmp_path, json_handler(payload), "observe")
        response = client.post("/v1/chat/completions", json=REQUEST)

        assert response.status_code == 200
        assert response.json() == payload
        verdicts = [e["metadata"]["verdict"] for e in audit_entries(provenance)]
        assert verdicts == ["passed", "flagged"]

    def test_enforce_removes_blocked_call_before_the_harness_sees_it(self, tmp_path):
        client, provenance = make_client(tmp_path, json_handler(completion(GOOD_CALL, BAD_CALL)), "enforce")
        response = client.post("/v1/chat/completions", json=REQUEST)

        names = [c["function"]["name"] for c in response.json()["choices"][0]["message"]["tool_calls"]]
        assert names == ["bash"]
        assert [e["metadata"]["verdict"] for e in audit_entries(provenance)] == ["passed", "blocked"]

    def test_anthropic_body_is_reviewed_too(self, tmp_path):
        payload = {"id": "m", "content": [{"type": "text", "text": "ok"}, {"type": "tool_use", "id": "t", "name": "shell_exec", "input": {}}]}
        client, provenance = make_client(tmp_path, json_handler(payload), "enforce")
        response = client.post("/v1/messages", json={"model": "claude", "tools": TOOLS, "messages": [{"role": "user", "content": "hi"}]})

        assert response.json()["content"] == [{"type": "text", "text": "ok"}]
        assert audit_entries(provenance)[0]["metadata"]["verdict"] == "blocked"

    def test_non_model_requests_pass_through_without_review(self, tmp_path):
        client, provenance = make_client(tmp_path, json_handler({"data": ["m1"]}), "enforce")
        response = client.get("/v1/models")

        assert response.json() == {"data": ["m1"]}
        assert audit_entries(provenance) == []

    def test_upstream_errors_are_passed_through(self, tmp_path):
        client, _ = make_client(tmp_path, json_handler({"error": "rate limited"}, status=429), "observe")
        response = client.post("/v1/chat/completions", json=REQUEST)
        assert response.status_code == 429


class TestStreamedResponses:
    def test_observe_streams_everything_and_records_at_the_end(self, tmp_path):
        body = sse(*openai_stream_events(GOOD_CALL, BAD_CALL))
        client, provenance = make_client(tmp_path, sse_handler(body), "observe")
        response = client.post("/v1/chat/completions", json={**REQUEST, "stream": True})

        assert response.status_code == 200
        assert '"shell_exec"' in response.text and "[DONE]" in response.text
        assert [e["metadata"]["verdict"] for e in audit_entries(provenance)] == ["passed", "flagged"]

    def test_enforce_drops_blocked_call_from_stream_and_keeps_text(self, tmp_path):
        text_event = {"choices": [{"delta": {"content": "Running the tests now."}}]}
        finish = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
        body = sse(text_event, *openai_stream_events(GOOD_CALL, BAD_CALL), finish)
        client, provenance = make_client(tmp_path, sse_handler(body), "enforce")
        response = client.post("/v1/chat/completions", json={**REQUEST, "stream": True})

        assert response.status_code == 200
        assert "Running the tests now." in response.text
        assert "pytest -q" in response.text and "shell_exec" not in response.text
        assert '"finish_reason": "tool_calls"' in response.text  # one call is still allowed
        assert "[DONE]" in response.text
        assert [e["metadata"]["verdict"] for e in audit_entries(provenance)] == ["passed", "blocked"]

    def test_enforce_turns_finish_reason_to_stop_when_every_call_is_blocked(self, tmp_path):
        finish = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
        body = sse(*openai_stream_events(BAD_CALL), finish)
        client, _ = make_client(tmp_path, sse_handler(body), "enforce")
        response = client.post("/v1/chat/completions", json={**REQUEST, "stream": True})

        assert response.status_code == 200
        assert "shell_exec" not in response.text and '"tool_calls": [' not in response.text
        assert '"finish_reason": "stop"' in response.text

    def test_enforce_handles_anthropic_stream(self, tmp_path):
        events = [
            {"type": "message_start", "message": {"id": "m"}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Checking."}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "t", "name": "shell_exec", "input": {}}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{}"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
            {"type": "message_stop"},
        ]
        body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        client, provenance = make_client(tmp_path, sse_handler(body), "enforce")
        response = client.post("/v1/messages", json={"model": "claude", "tools": TOOLS, "stream": True, "messages": [{"role": "user", "content": "hi"}]})

        assert response.status_code == 200
        assert "Checking." in response.text and "shell_exec" not in response.text
        assert '"stop_reason": "end_turn"' in response.text and "message_stop" in response.text
        assert audit_entries(provenance)[0]["metadata"]["verdict"] == "blocked"

    def test_enforce_forwards_a_clean_stream(self, tmp_path):
        body = sse(*openai_stream_events(GOOD_CALL))
        client, _ = make_client(tmp_path, sse_handler(body), "enforce")
        response = client.post("/v1/chat/completions", json={**REQUEST, "stream": True})

        assert response.status_code == 200
        assert "pytest -q" in response.text


@pytest.mark.parametrize("mode", ["observe", "enforce"])
def test_proxy_records_entries_that_do_not_count_as_interactions(tmp_path, mode):
    client, provenance = make_client(tmp_path, json_handler(completion(BAD_CALL)), mode)
    client.post("/v1/chat/completions", json=REQUEST)
    assert provenance.get_interaction_count("general", "default", "gpt-4o-mini") == 0


RESPONSES_REQUEST = {"model": "gpt-5", "tools": TOOLS, "input": "run the tests"}


def responses_completion(*calls):
    """A Responses API body whose output holds function_call items."""
    return {"id": "r", "object": "response", "output": [{"type": "function_call", "call_id": cid, "name": name, "arguments": args} for cid, name, args in calls]}


class TestResponsesApi:
    def test_enforce_removes_blocked_function_call_from_json(self, tmp_path):
        payload = responses_completion(("c1", "bash", '{"command": "pytest -q"}'), ("c2", "shell_exec", "{}"))
        client, provenance = make_client(tmp_path, json_handler(payload), "enforce")
        response = client.post("/v1/responses", json=RESPONSES_REQUEST)

        assert [item["call_id"] for item in response.json()["output"]] == ["c1"]
        assert [e["metadata"]["verdict"] for e in audit_entries(provenance)] == ["passed", "blocked"]

    def test_plain_text_input_is_the_task(self):
        conversation, task = request_context(RESPONSES_REQUEST)
        assert task == "run the tests"

    def test_enforce_drops_blocked_call_events_and_filters_completed_output(self, tmp_path):
        events = [
            {"type": "response.output_item.added", "output_index": 0, "item": {"type": "function_call", "call_id": "c1", "name": "bash", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"command": "pytest -q"}'},
            {"type": "response.output_item.added", "output_index": 1, "item": {"type": "function_call", "call_id": "c2", "name": "shell_exec", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": "{}"},
            {"type": "response.output_item.done", "output_index": 1, "item": {"type": "function_call", "call_id": "c2", "name": "shell_exec", "arguments": "{}"}},
            {"type": "response.completed", "response": {"output": [
                {"type": "function_call", "call_id": "c1", "name": "bash", "arguments": '{"command": "pytest -q"}'},
                {"type": "function_call", "call_id": "c2", "name": "shell_exec", "arguments": "{}"},
            ]}},
        ]
        body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        client, provenance = make_client(tmp_path, sse_handler(body), "enforce")
        response = client.post("/v1/responses", json={**RESPONSES_REQUEST, "stream": True})

        assert response.status_code == 200
        assert "pytest -q" in response.text
        assert "shell_exec" not in response.text
        assert [e["metadata"]["verdict"] for e in audit_entries(provenance)] == ["passed", "blocked"]


def turn_entries(provenance):
    return [e for e in provenance.export_log() if e["metadata"].get("event") == "model_turn"]


class TestModelTurns:
    def test_plain_json_answer_is_recorded_as_one_turn(self, tmp_path):
        payload = {"id": "x", "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi there"}, "finish_reason": "stop"}]}
        client, provenance = make_client(tmp_path, json_handler(payload))
        client.post("/v1/chat/completions", json=REQUEST)

        entries = turn_entries(provenance)
        assert len(entries) == 1
        assert entries[0]["metadata"] == {
            "event": "model_turn",
            "endpoint": "/v1/chat/completions",
            "status": 200,
            "finish_reason": "stop",
            "tool_calls": 0,
            "provider": "proxy",
            "llm_model": "gpt-4o-mini",
        }
        assert entries[0]["response_hash"] and entries[0]["divergence_score"] is None
        assert audit_entries(provenance) == []

    def test_response_with_tool_calls_is_one_turn_plus_one_entry_per_call(self, tmp_path):
        client, provenance = make_client(tmp_path, json_handler(completion(GOOD_CALL)))
        client.post("/v1/chat/completions", json=REQUEST)

        turns = turn_entries(provenance)
        assert [t["metadata"]["tool_calls"] for t in turns] == [1]
        assert len(audit_entries(provenance)) == 1
        assert audit_entries(provenance)[0]["metadata"]["endpoint"] == "/v1/chat/completions"

    def test_upstream_error_is_recorded_with_its_status(self, tmp_path):
        client, provenance = make_client(tmp_path, json_handler({"error": {"message": "throttled"}}, status=429))
        client.post("/v1/chat/completions", json=REQUEST)

        turns = turn_entries(provenance)
        assert [(t["metadata"]["status"], t["metadata"]["finish_reason"]) for t in turns] == [(429, "")]

    def test_model_listing_is_recorded_as_an_api_call(self, tmp_path):
        client, provenance = make_client(tmp_path, json_handler({"data": []}))
        client.get("/v1/models")

        calls = [e for e in provenance.export_log() if e["metadata"].get("event") == "api_call"]
        assert [c["metadata"] for c in calls] == [{"event": "api_call", "method": "GET", "endpoint": "/v1/models", "status": 200, "provider": "proxy"}]
        assert turn_entries(provenance) == []

    @pytest.mark.parametrize("mode", ["observe", "enforce"])
    def test_openai_text_stream_is_recorded_with_its_finish_reason(self, tmp_path, mode):
        events = [
            {"choices": [{"delta": {"role": "assistant", "content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
        ]
        client, provenance = make_client(tmp_path, sse_handler(sse(*events)), mode)
        client.post("/v1/chat/completions", json={**REQUEST, "stream": True})

        entries = turn_entries(provenance)
        assert len(entries) == 1
        assert entries[0]["metadata"]["finish_reason"] == "stop"

    def test_anthropic_text_stream_is_recorded(self, tmp_path):
        events = [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Done."}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        ]
        client, provenance = make_client(tmp_path, sse_handler(sse(*events)), "enforce")
        client.post("/v1/messages", json={**REQUEST, "stream": True})

        entries = turn_entries(provenance)
        assert len(entries) == 1
        assert entries[0]["metadata"]["finish_reason"] == "end_turn"

    def test_responses_api_plain_message_is_recorded(self, tmp_path):
        payload = {"id": "r", "object": "response", "status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}]}
        client, provenance = make_client(tmp_path, json_handler(payload))
        client.post("/v1/responses", json=RESPONSES_REQUEST)

        entries = turn_entries(provenance)
        assert len(entries) == 1
        assert entries[0]["metadata"]["finish_reason"] == "completed"

    def test_turns_stay_out_of_divergence_figures_and_tool_call_listing(self, tmp_path):
        payload = {"id": "x", "choices": [{"index": 0, "message": {"role": "assistant", "content": "no"}, "finish_reason": "stop"}]}
        client, provenance = make_client(tmp_path, json_handler(payload))
        client.post("/v1/chat/completions", json=REQUEST)

        assert len(turn_entries(provenance)) == 1
        assert provenance.recent_entries(None) == []
        assert provenance.tool_call_page(page=1, page_size=10)[1] == 0
