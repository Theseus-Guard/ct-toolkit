"""
ct_toolkit.proxy
----------------
A local reverse proxy between an agent harness and its model API. Point the harness's base
URL at the proxy and nothing else changes. Every model response's tool calls go through the
ToolGuard, and each verdict goes into the provenance chain. A successful response without tool calls
(a plain answer or a refusal) is recorded as one model-turn entry: its text is hashed, not stored.

Reviewed: OpenAI Chat Completions and Anthropic Messages bodies (anything with `messages`).
Everything else is passed through untouched.

Observe mode forwards responses as they come and only records findings.

Enforce mode removes blocked calls before the harness sees them. In a JSON response that is a
copy without the call. In a stream, text is forwarded live until the first tool-call event; from
there the events are held until the stream ends, then the blocked calls are dropped from them and
the rest is sent. The harness never receives a blocked call and the stream still completes.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ct_toolkit.core.tool_guard import ToolCallDecision, ToolGuard, ToolGuardMode, tool_calls_of, without_calls
from ct_toolkit.core.tool_guard_settings import SettingsWatcher, apply_to_guard
from ct_toolkit.provenance.log import API_CALL_EVENT, MODEL_TURN_EVENT, ProvenanceLog
from ct_toolkit.utils.logger import get_logger

logger = get_logger(__name__)

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
SKIP_REQUEST = {"host", "content-length", "accept-encoding", "connection", "transfer-encoding"}
SKIP_RESPONSE = {"content-length", "content-encoding", "transfer-encoding", "connection"}


def request_context(request: dict) -> tuple[str, str]:
    """(conversation, task) for a model-turn request. The conversation is everything but the model's own turns;
    the task is the user's own text."""
    context = dict(request)
    if isinstance(context.get("messages"), list):
        context["messages"] = [m for m in context["messages"] if not (isinstance(m, dict) and m.get("role") == "assistant")]
    conversation = json.dumps(context, ensure_ascii=False)
    if isinstance(request.get("input"), str):  # Responses API, plain text input
        return conversation, request["input"]
    task_parts = []
    for message in request.get("messages") or request.get("input") or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            task_parts.append(content)
        elif isinstance(content, list):
            task_parts.extend(b["text"] for b in content if isinstance(b, dict) and b.get("type") in ("text", "input_text") and isinstance(b.get("text"), str))
    return conversation, "\n\n".join(task_parts)


@dataclass
class Blocked:
    """The blocked calls of one stream, by the index or call id each format uses."""

    openai: set[int] = field(default_factory=set)
    anthropic: set[int] = field(default_factory=set)
    responses: set[int] = field(default_factory=set)
    response_ids: set[str] = field(default_factory=set)


class StreamToolCalls:
    """Rebuilds the tool calls of an SSE stream, in the non-streamed shape the ToolGuard reads."""

    def __init__(self) -> None:
        self._openai: dict[int, dict[str, Any]] = {}
        self._anthropic: dict[int, dict[str, Any]] = {}
        self._responses: dict[int, dict[str, Any]] = {}
        self._text: list[str] = []
        self.finish: Optional[str] = None  # the finish reason the stream ends with, if it says

    def feed(self, line: str) -> None:
        """Feeds one SSE line; only `data:` lines carry tool calls."""
        if line.startswith("data:"):
            self.feed_event(_json_or_none(line[len("data:") :].strip()))

    def text(self) -> str:
        """The text the model streamed, without tool-call arguments."""
        return "".join(self._text)

    def feed_event(self, event: Any) -> None:
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "content_block_start" and isinstance(event.get("content_block"), dict):
            block = event["content_block"]
            if block.get("type") == "tool_use":
                self._anthropic[event.get("index", 0)] = {"id": block.get("id"), "name": block.get("name"), "input": ""}
        elif kind == "content_block_delta" and isinstance(event.get("delta"), dict):
            delta = event["delta"]
            if delta.get("type") == "input_json_delta" and event.get("index") in self._anthropic:
                self._anthropic[event["index"]]["input"] += delta.get("partial_json") or ""
            elif delta.get("type") == "text_delta":
                self._text.append(delta.get("text") or "")
        elif kind == "message_delta" and isinstance(event.get("delta"), dict):
            self.finish = event["delta"].get("stop_reason") or self.finish
        elif kind == "response.output_text.delta":
            self._text.append(event.get("delta") or "")
        elif kind in ("response.completed", "response.incomplete") and isinstance(event.get("response"), dict):
            self.finish = event["response"].get("status") or self.finish
        elif kind == "response.output_item.added" and (event.get("item") or {}).get("type") == "function_call":
            item = event["item"]
            self._responses[event.get("output_index", 0)] = {
                "id": item.get("call_id") or item.get("id"),
                "name": item.get("name") or "",
                "arguments": item.get("arguments") or "",
            }
        elif kind == "response.function_call_arguments.delta" and event.get("output_index") in self._responses:
            self._responses[event["output_index"]]["arguments"] += event.get("delta") or ""
        for choice in event.get("choices") or []:
            choice = choice or {}
            delta = choice.get("delta") or {}
            if isinstance(delta.get("content"), str):
                self._text.append(delta["content"])
            if choice.get("finish_reason"):
                self.finish = choice["finish_reason"]
            for call in delta.get("tool_calls") or []:
                slot = self._openai.setdefault(call.get("index", 0), {"id": None, "name": "", "arguments": ""})
                slot["id"] = call.get("id") or slot["id"]
                function = call.get("function") or {}
                slot["name"] += function.get("name") or ""
                slot["arguments"] += function.get("arguments") or ""

    def response(self) -> dict[str, Any]:
        if self._openai:
            calls = [
                {"id": slot["id"], "type": "function", "function": {"name": slot["name"], "arguments": slot["arguments"]}}
                for _, slot in sorted(self._openai.items())
            ]
            return {"choices": [{"message": {"tool_calls": calls}}]}
        if self._responses:
            return {
                "output": [
                    {"type": "function_call", "call_id": slot["id"], "name": slot["name"], "arguments": slot["arguments"]}
                    for _, slot in sorted(self._responses.items())
                ]
            }
        blocks = [
            {"type": "tool_use", "id": slot["id"], "name": slot["name"], "input": slot["input"] or "{}"}
            for _, slot in sorted(self._anthropic.items())
        ]
        return {"content": blocks}

    def blocked(self, positions: Iterable[int]) -> Blocked:
        """The stream's own indexes and call ids for review positions (numbered as `tool_calls_of` numbers them)."""
        openai_keys, anthropic_keys, response_keys = sorted(self._openai), sorted(self._anthropic), sorted(self._responses)
        result = Blocked()
        for position in positions:
            if position < len(openai_keys):
                result.openai.add(openai_keys[position])
            if position < len(anthropic_keys):
                result.anthropic.add(anthropic_keys[position])
            if position < len(response_keys):
                result.responses.add(response_keys[position])
                call_id = self._responses[response_keys[position]]["id"]
                if call_id:
                    result.response_ids.add(call_id)
        return result


def _json_or_none(text: str) -> Any:
    if not text or text == "[DONE]":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def response_turn(response: Any) -> tuple[str, Optional[str]]:
    """The text and the finish reason of a model response, in Chat Completions, Messages or Responses shape."""
    if not isinstance(response, dict):
        return "", None
    choices = response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        content = message.get("content")
        return (content if isinstance(content, str) else ""), choices[0].get("finish_reason")
    if isinstance(response.get("content"), list):
        text = "".join(b.get("text") or "" for b in response["content"] if isinstance(b, dict) and b.get("type") == "text")
        return text, response.get("stop_reason")
    if isinstance(response.get("output"), list):
        parts = [
            part.get("text") or ""
            for item in response["output"]
            if isinstance(item, dict) and item.get("type") == "message"
            for part in item.get("content") or []
            if isinstance(part, dict) and part.get("type") == "output_text"
        ]
        return "".join(parts), response.get("status")
    return "", None


def _parse_event(lines: list[str]) -> tuple[str, Any]:
    """(event name, parsed data) of one SSE event given as its lines."""
    name = ""
    data_parts = []
    for line in lines:
        if line.startswith("event:"):
            name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_parts.append(line[len("data:") :].lstrip())
    return name, _json_or_none("\n".join(data_parts))


async def _sse_events(upstream_response: httpx.Response) -> AsyncIterator[list[str]]:
    """The lines of each SSE event, without the blank lines that separate them."""
    lines: list[str] = []
    async for line in upstream_response.aiter_lines():
        if line == "":
            if lines:
                yield lines
                lines = []
        else:
            lines.append(line)
    if lines:
        yield lines


def _is_tool_event(data: Any) -> bool:
    """Whether an event starts or carries tool calls, in OpenAI, Anthropic or Responses streaming format."""
    if not isinstance(data, dict):
        return False
    kind = data.get("type")
    if kind == "content_block_start" and (data.get("content_block") or {}).get("type") == "tool_use":
        return True
    if kind == "content_block_delta" and (data.get("delta") or {}).get("type") == "input_json_delta":
        return True
    if kind == "message_delta" and (data.get("delta") or {}).get("stop_reason") == "tool_use":
        return True
    if kind == "response.output_item.added" and (data.get("item") or {}).get("type") == "function_call":
        return True
    if kind == "response.function_call_arguments.delta":
        return True
    return any(
        (choice or {}).get("finish_reason") == "tool_calls" or ((choice or {}).get("delta") or {}).get("tool_calls")
        for choice in data.get("choices") or []
    )


def _drop_blocked(name: str, lines: list[str], data: Any, blocked: Blocked, calls_left: int) -> Optional[list[str]]:
    """The event without its blocked calls, or None when nothing of it is left to send."""
    if not isinstance(data, dict):
        return lines
    kind = data.get("type")
    changed = False
    if kind in ("content_block_start", "content_block_delta", "content_block_stop") and data.get("index") in blocked.anthropic:
        return None
    if kind in ("response.output_item.added", "response.output_item.done") and (data.get("item") or {}).get("type") == "function_call":
        if data.get("output_index") in blocked.responses:
            return None
    if kind in ("response.function_call_arguments.delta", "response.function_call_arguments.done") and data.get("output_index") in blocked.responses:
        return None
    if kind == "response.completed" and isinstance(data.get("response"), dict):
        output = data["response"].get("output")
        if isinstance(output, list):
            kept = [item for item in output if not (isinstance(item, dict) and item.get("call_id") in blocked.response_ids)]
            if len(kept) != len(output):
                data["response"]["output"] = kept
                changed = True
    if kind == "message_delta" and (data.get("delta") or {}).get("stop_reason") == "tool_use" and calls_left == 0:
        data["delta"]["stop_reason"] = "end_turn"
        changed = True
    empty = True
    for choice in data.get("choices") or []:
        delta = (choice or {}).get("delta") or {}
        calls = delta.get("tool_calls")
        if calls:
            kept_calls = [call for call in calls if call.get("index", 0) not in blocked.openai]
            if len(kept_calls) != len(calls):
                changed = True
            if kept_calls:
                delta["tool_calls"] = kept_calls
            else:
                delta.pop("tool_calls")
        if choice.get("finish_reason") == "tool_calls" and calls_left == 0:
            choice["finish_reason"] = "stop"
            changed = True
        if delta or choice.get("finish_reason"):
            empty = False
    if data.get("choices") and empty and changed:
        return None
    if not changed:
        return lines
    return ([f"event: {name}"] if name else []) + [f"data: {json.dumps(data, ensure_ascii=False)}"]


class ProxyReviewer:
    """Runs the ToolGuard on responses and writes each verdict to the provenance chain."""

    def __init__(self, guard: ToolGuard, provenance: Optional[ProvenanceLog]) -> None:
        self.guard = guard
        self.provenance = provenance

    def review(self, request: dict, response: Any, endpoint: str = "") -> list[ToolCallDecision]:
        conversation, task = request_context(request)
        decisions = self.guard.review(response, tools=request.get("tools"), conversation=conversation, task=task)
        model = str(request.get("model") or "")
        if self.provenance is not None:
            for decision in decisions:
                self.provenance.record(
                    request_text=task,
                    response_text=f"{decision.call.name}({decision.call.arguments})",
                    metadata={**decision.audit_metadata("proxy", model), "endpoint": endpoint},
                )
        return decisions

    def blocked_indexes(self, decisions: list[ToolCallDecision]) -> set[int]:
        return {decision.index for decision in decisions if decision.blocked}

    def record_turn(
        self, request: dict, response: Any, text: str, finish_reason: Optional[str], endpoint: str, status: int
    ) -> None:
        """One entry per model request: its status, finish reason and tool-call count. The text is hashed, not stored.
        The tool calls themselves are recorded one by one by `review`."""
        if self.provenance is None:
            return
        _, task = request_context(request)
        calls = tool_calls_of(response) if isinstance(response, dict) else []
        self.provenance.record(
            request_text=task,
            response_text=text,
            metadata={
                "event": MODEL_TURN_EVENT,
                "endpoint": endpoint,
                "status": status,
                "finish_reason": finish_reason or "",
                "tool_calls": len(calls),
                "provider": "proxy",
                "llm_model": str(request.get("model") or ""),
            },
        )

    def record_call(self, method: str, endpoint: str, status: int) -> None:
        """One entry per request that is not a model request, such as a model listing or a token count."""
        if self.provenance is not None:
            self.provenance.record(
                request_text="",
                response_text="",
                metadata={"event": API_CALL_EVENT, "method": method, "endpoint": endpoint, "status": status, "provider": "proxy"},
            )


def is_model_turn(path: str, body: Any) -> bool:
    if not isinstance(body, dict) or path.endswith("/count_tokens"):
        return False
    return "messages" in body or ("input" in body and path.endswith("/responses"))


def create_app(
    upstream: str,
    guard: ToolGuard,
    provenance: Optional[ProvenanceLog] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
    settings: Optional[SettingsWatcher] = None,
) -> Starlette:
    """`settings`, when given, is re-read on every request: a change to the settings file takes effect at once."""
    upstream = upstream.rstrip("/")
    reviewer = ProxyReviewer(guard, provenance)
    applied: dict[str, object] = {}  # the settings last applied to the guard; None until the first request
    # No read timeout: streams can idle between events.
    client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(None, connect=10.0))

    async def forward(request: Request) -> Response:
        if settings is not None:
            current = settings.current()
            if current != applied.get("settings"):
                apply_to_guard(guard, current)
                applied["settings"] = current
        body = await request.body()
        try:
            request_json = json.loads(body) if body else None
        except ValueError:
            request_json = None
        review = is_model_turn(request.url.path, request_json)
        endpoint = request.url.path

        url = upstream + request.url.path
        if request.url.query:
            url += f"?{request.url.query}"
        headers = [(name, value) for name, value in request.headers.raw if name.lower().decode() not in SKIP_REQUEST]
        headers.append((b"accept-encoding", b"identity"))
        upstream_request = client.build_request(request.method, url, headers=headers, content=body)
        try:
            upstream_response = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as e:
            logger.warning(f"upstream error: {e}")
            if review:
                await asyncio.to_thread(reviewer.record_turn, request_json, {}, "", None, endpoint, 502)
            else:
                await asyncio.to_thread(reviewer.record_call, request.method, endpoint, 502)
            return JSONResponse({"error": {"type": "upstream_error", "message": str(e)}}, status_code=502)

        response_headers = {
            name: value for name, value in upstream_response.headers.items() if name.lower() not in SKIP_RESPONSE
        }
        close = BackgroundTask(upstream_response.aclose)
        is_sse = upstream_response.headers.get("content-type", "").startswith("text/event-stream")
        status = upstream_response.status_code

        if not review:
            await asyncio.to_thread(reviewer.record_call, request.method, endpoint, status)
            return StreamingResponse(upstream_response.aiter_bytes(), status, response_headers, background=close)

        if is_sse:
            if guard.mode is ToolGuardMode.ENFORCE:
                stream = _enforced_stream(upstream_response, request_json, reviewer, endpoint)
            else:
                stream = _observed_stream(upstream_response, request_json, reviewer, endpoint)
            return StreamingResponse(stream, status, response_headers, background=close)

        data = await upstream_response.aread()
        await upstream_response.aclose()
        try:
            response_json = json.loads(data)
        except ValueError:
            await asyncio.to_thread(reviewer.record_turn, request_json, {}, "", None, endpoint, status)
            return Response(data, status, response_headers)
        decisions = await asyncio.to_thread(reviewer.review, request_json, response_json, endpoint)
        text, finish = response_turn(response_json)
        await asyncio.to_thread(reviewer.record_turn, request_json, response_json, text, finish, endpoint, status)
        blocked = reviewer.blocked_indexes(decisions)
        if blocked:
            logger.warning(f"tool guard removed {len(blocked)} call(s)")
            response_json = without_calls(response_json, blocked)
            data = json.dumps(response_json, ensure_ascii=False).encode()
        return Response(data, status, response_headers)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with client:
            yield

    return Starlette(routes=[Route("/{path:path}", forward, methods=METHODS)], lifespan=lifespan)


async def _observed_stream(upstream_response: httpx.Response, request: dict, reviewer: ProxyReviewer, endpoint: str) -> AsyncIterator[bytes]:
    """Forwards the stream line by line as it arrives, then records the verdicts and the turn when it ends."""
    rebuilt = StreamToolCalls()
    async for line in upstream_response.aiter_lines():
        rebuilt.feed(line)
        yield (line + "\n").encode()
    await asyncio.to_thread(reviewer.review, request, rebuilt.response(), endpoint)
    await asyncio.to_thread(
        reviewer.record_turn, request, rebuilt.response(), rebuilt.text(), rebuilt.finish, endpoint, upstream_response.status_code
    )


async def _enforced_stream(upstream_response: httpx.Response, request: dict, reviewer: ProxyReviewer, endpoint: str) -> AsyncIterator[bytes]:
    """Enforce mode: forwards text live, holds the events from the first tool call to the end, then drops the
    blocked calls from them and forwards the rest. The turn is recorded when the stream has ended."""
    rebuilt = StreamToolCalls()
    held: Optional[list[tuple[str, list[str], Any]]] = None
    async for lines in _sse_events(upstream_response):
        name, data = _parse_event(lines)
        rebuilt.feed_event(data)
        if held is None and not _is_tool_event(data):
            yield ("\n".join(lines) + "\n\n").encode()
            continue
        if held is None:
            held = []
        held.append((name, lines, data))
    await upstream_response.aclose()
    status = upstream_response.status_code
    if held is None:
        await asyncio.to_thread(reviewer.record_turn, request, rebuilt.response(), rebuilt.text(), rebuilt.finish, endpoint, status)
        return

    decisions = await asyncio.to_thread(reviewer.review, request, rebuilt.response(), endpoint)
    positions = reviewer.blocked_indexes(decisions)
    if positions:
        logger.warning(f"tool guard removed {len(positions)} call(s) from a stream")
    blocked = rebuilt.blocked(positions)
    calls_left = len(decisions) - len(positions)

    for name, lines, data in held:
        kept = _drop_blocked(name, lines, data, blocked, calls_left)
        if kept is not None:
            yield ("\n".join(kept) + "\n\n").encode()
    await asyncio.to_thread(reviewer.record_turn, request, rebuilt.response(), rebuilt.text(), rebuilt.finish, endpoint, status)
