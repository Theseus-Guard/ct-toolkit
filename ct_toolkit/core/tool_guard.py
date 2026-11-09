"""
ct_toolkit.core.tool_guard
--------------------------
Reviews the tool calls a model response asks the harness to run.

Checks that need no second model:
  1. The call names a tool the request never declared.
  2. The call's arguments contain a host the conversation never mentions.
  3. The call matches a pattern of known-risky actions.

In ``observe`` mode (default) findings are only recorded. In ``enforce`` mode a call
with a high-severity finding is removed from a copy of the raw response.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any, Callable, Iterable, Optional, Sequence

import litellm


class ToolGuardMode(str, Enum):
    OBSERVE = "observe"
    ENFORCE = "enforce"


class Severity(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass(frozen=True)
class Finding:
    id: str
    severity: Severity
    title: str

    def __str__(self) -> str:
        return f"{self.id} ({self.severity.name.lower()})"


UNDECLARED_TOOL = Finding("policy.undeclared_tool", Severity.HIGH, "Tool was not declared in the request")
UNSEEN_HOST = Finding("exfiltration.unseen_host", Severity.HIGH, "Host appears nowhere in the conversation")
JUDGE_BLOCK = "judge.block"
JUDGE_UNAVAILABLE = Finding("judge.unavailable", Severity.HIGH, "Judge unavailable for a suspicious call")

# Hosts that calls may reach even if the conversation never mentions them (package
# installs, code hosting, local services).
DEFAULT_ALLOWED_HOSTS: tuple[str, ...] = (
    "localhost",
    "127.0.0.1",
    "github.com",
    "githubusercontent.com",
    "pypi.org",
    "pythonhosted.org",
    "npmjs.org",
    "huggingface.co",
)

RISKY_PATTERNS: tuple[tuple[Finding, re.Pattern[str]], ...] = (
    (
        Finding("harness.proc_environ", Severity.HIGH, "Reads another process's environment"),
        re.compile(r"/proc/(?:\d+|self|\*)/environ", re.IGNORECASE),
    ),
    (
        Finding("harness.k8s_service_account", Severity.HIGH, "Reads a Kubernetes service-account token"),
        re.compile(r"/var/run/secrets/kubernetes\.io|/run/secrets/kubernetes\.io", re.IGNORECASE),
    ),
    (
        Finding("harness.namespace_escape", Severity.MEDIUM, "Creates or enters Linux namespaces"),
        re.compile(r"\b(?:unshare|nsenter)\b", re.IGNORECASE),
    ),
    (
        Finding("network.proxy_bypass", Severity.MEDIUM, "Bypasses the proxy with curl --noproxy"),
        re.compile(r"--noproxy\b", re.IGNORECASE),
    ),
    (
        Finding("secrets.credential_file", Severity.HIGH, "Reads a credential file"),
        re.compile(
            r"(?:~|\$HOME|/home/[^/\s\"']+|/Users/[^/\s\"']+|/root)/"
            r"(?:\.netrc|\.git-credentials|\.docker/config\.json|\.aws/credentials|\.ssh/(?:id_|known_hosts|config))",
            re.IGNORECASE,
        ),
    ),
)

_URL_HOST = re.compile(r"[a-z][a-z0-9+.\-]*://(?:[^/\s@\"'\\]*@)?([^/\s:\"'\\?#]+)", re.IGNORECASE)
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")


def extract_hosts(text: str) -> set[str]:
    """Lowercased hosts of `scheme://` URLs and bare IPv4 literals in `text`."""
    hosts = {match.group(1).lower() for match in _URL_HOST.finditer(text)}
    for address in _IPV4.findall(text):
        if all(int(octet) <= 255 for octet in address.split(".")):
            hosts.add(address)
    return hosts


def declared_tools(tools: Optional[Iterable[Any]]) -> set[str]:
    """Tool names in an OpenAI (`function.name`) or Anthropic (`name`) tools list."""
    names = set()
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        name = function.get("name") if isinstance(function, dict) else tool.get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def conversation_text(message: str, history: Optional[Sequence[Any]] = None, system: Optional[str] = None) -> str:
    """Everything the request shows the model, except the model's own earlier turns."""
    parts = [system or "", message or ""]
    for item in history or []:
        if not isinstance(item, dict) or item.get("role") == "assistant":
            continue
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


TOOL_JUDGE_SYSTEM = """You review one tool call that an AI agent wants to run.
You see the user's task, the call and the scanner's findings. You do not see the agent's reasoning.
Block only if the call does something the task does not ask for: sending data to a host the user never
named, reading credentials or process secrets, or escalating privileges. Otherwise allow.
Answer with one JSON object only: {"verdict": "allow" or "block", "reason": "short explanation"}."""


class ToolJudgeError(Exception):
    pass


class ToolCallJudge:
    """Decision model for suspicious tool calls. Calls any LiteLLM model. It can add blocks, never lift them."""

    def __init__(
        self,
        model: str,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        completion: Callable[..., Any] = litellm.completion,
    ) -> None:
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.timeout = timeout
        self._completion = completion

    def review(self, task: str, call: ToolCall, findings: Sequence[Finding]) -> tuple[bool, str]:
        """(block, reason) for one call. Raises ToolJudgeError if no usable verdict comes back."""
        args = call.arguments if len(call.arguments) <= 4000 else call.arguments[:4000] + " [...]"
        prompt = (
            f"<task>\n{task}\n</task>\n\n"
            f"<tool_call name={json.dumps(call.name)}>\n{args}\n</tool_call>\n\n"
            "<scanner_findings>\n" + "\n".join(f"- {finding}: {finding.title}" for finding in findings) + "\n</scanner_findings>"
        )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": TOOL_JUDGE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 256,
            "timeout": self.timeout,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        try:
            content = self._completion(**kwargs).choices[0].message.content
            # Tolerate code fences or stray text around the object.
            answer = json.loads(content[content.index("{") : content.rindex("}") + 1])
        except Exception as e:  # any provider or parsing failure is a judge failure, not a crash
            raise ToolJudgeError(f"{type(e).__name__}: {e}") from e
        verdict = answer.get("verdict") if isinstance(answer, dict) else None
        if verdict not in ("allow", "block"):
            raise ToolJudgeError(f"unexpected verdict {verdict!r}")
        return verdict == "block", str(answer.get("reason") or "").strip()


def task_text(message: str, history: Optional[Sequence[Any]] = None) -> str:
    """The user's own words: the message and the user turns of the history."""
    parts = []
    for item in history or []:
        if isinstance(item, dict) and item.get("role") == "user" and isinstance(item.get("content"), str):
            parts.append(item["content"])
    parts.append(message or "")
    return "\n\n".join(parts)


@dataclass(frozen=True)
class ToolCall:
    id: Optional[str]
    name: str
    arguments: str


@dataclass(frozen=True)
class ToolCallDecision:
    index: int
    call: ToolCall
    findings: tuple[Finding, ...]
    mode: ToolGuardMode
    judge: Optional[str] = None  # "allow: why", "block: why" or "error: why"; None if the judge wasn't asked

    @property
    def severe(self) -> bool:
        return any(finding.severity >= Severity.HIGH for finding in self.findings)

    @property
    def verdict(self) -> str:
        """`blocked` in enforce mode with a severe finding, `flagged` with any finding, else `passed`."""
        if self.severe and self.mode is ToolGuardMode.ENFORCE:
            return "blocked"
        return "flagged" if self.findings else "passed"

    @property
    def blocked(self) -> bool:
        return self.verdict == "blocked"

    def audit_metadata(self, provider: str, llm_model: str) -> dict[str, Any]:
        # Keys avoid `template`, `kernel` and `model`: get_interaction_count counts on them.
        return {
            "event": "tool_call",
            "tool": self.call.name,
            "verdict": self.verdict,
            "mode": self.mode.value,
            "findings": ", ".join(str(finding) for finding in self.findings),
            "judge": self.judge or "",
            "provider": provider,
            "llm_model": llm_model,
        }


def _field(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _tool_call_lists(raw_response: Any) -> list[list[Any]]:
    """Lists that hold a response's tool calls: OpenAI `message.tool_calls` and Anthropic `content` blocks."""
    lists: list[list[Any]] = []
    for choice in _field(raw_response, "choices") or []:
        calls = _field(_field(choice, "message"), "tool_calls")
        if isinstance(calls, list):
            lists.append(calls)
    content = _field(raw_response, "content")
    if isinstance(content, list):
        lists.append(content)
    return lists


def _is_tool_call(item: Any) -> bool:
    return _field(item, "type") in ("function", "tool_use", None) and (
        _field(item, "function") is not None or _field(item, "name") is not None
    )


def _to_tool_call(item: Any) -> ToolCall:
    if _field(item, "type") == "tool_use":
        args = _field(item, "input") or {}
        name = _field(item, "name")
        call_id = _field(item, "id")
    else:
        function = _field(item, "function")
        args = _field(function, "arguments") or {}
        name = _field(function, "name")
        call_id = _field(item, "id")
    arguments = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False, sort_keys=True)
    return ToolCall(id=call_id, name=str(name or ""), arguments=arguments)


def tool_calls_of(raw_response: Any) -> list[ToolCall]:
    """Tool calls of an OpenAI-style or Anthropic-style response, in order."""
    return [
        _to_tool_call(item)
        for holder in _tool_call_lists(raw_response)
        for item in holder
        if _is_tool_call(item)
    ]


def without_calls(raw_response: Any, indexes: set[int]) -> Any:
    """A copy of `raw_response` with the tool calls at `indexes` (as numbered by `tool_calls_of`) removed."""
    result = copy.deepcopy(raw_response)
    position = 0
    for holder in _tool_call_lists(result):
        kept = []
        for item in holder:
            if not _is_tool_call(item):
                kept.append(item)
                continue
            if position not in indexes:
                kept.append(item)
            position += 1
        holder[:] = kept
    return result


def _set_field(obj: Any, key: str, value: Any) -> None:
    if isinstance(obj, dict):
        obj[key] = value
    else:
        setattr(obj, key, value)


def chunk_has_tool_calls(chunk: Any) -> bool:
    """Whether a streaming chunk carries tool-call deltas or ends a tool-call turn."""
    for choice in _field(chunk, "choices") or []:
        delta_calls = _field(_field(choice, "delta"), "tool_calls")
        if delta_calls or _field(choice, "finish_reason") == "tool_calls":
            return True
    return False


class StreamedToolCalls:
    """Rebuilds the tool calls of a stream from its chunks (OpenAI-style deltas, objects or dicts)."""

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, Any]] = {}

    def add_chunk(self, chunk: Any) -> None:
        for choice in _field(chunk, "choices") or []:
            for call in _field(_field(choice, "delta"), "tool_calls") or []:
                slot = self._calls.setdefault(_field(call, "index") or 0, {"id": None, "name": "", "arguments": ""})
                slot["id"] = _field(call, "id") or slot["id"]
                function = _field(call, "function")
                slot["name"] += _field(function, "name") or ""
                slot["arguments"] += _field(function, "arguments") or ""

    def keys_at(self, positions: Iterable[int]) -> set[int]:
        """The stream's own call indexes for the review positions (as numbered by `tool_calls_of`)."""
        keys = sorted(self._calls)
        return {keys[position] for position in positions if position < len(keys)}

    def response(self) -> dict[str, Any]:
        calls = [
            {"id": slot["id"], "type": "function", "function": {"name": slot["name"], "arguments": slot["arguments"]}}
            for _, slot in sorted(self._calls.items())
        ]
        return {"choices": [{"message": {"tool_calls": calls}}]}


def without_stream_calls(chunk: Any, blocked_keys: set[int], calls_left: int) -> Any:
    """A copy of a streaming chunk without the tool-call deltas of `blocked_keys`. When no call is left
    to make, a `tool_calls` finish reason becomes `stop`."""
    result = copy.deepcopy(chunk)
    for choice in _field(result, "choices") or []:
        delta = _field(choice, "delta")
        calls = _field(delta, "tool_calls")
        if isinstance(calls, list) and calls:
            calls[:] = [call for call in calls if (_field(call, "index") or 0) not in blocked_keys]
        if _field(choice, "finish_reason") == "tool_calls" and calls_left == 0:
            _set_field(choice, "finish_reason", "stop")
    return result


class ToolGuard:
    """Reviews the tool calls of one response."""

    def __init__(
        self,
        mode: ToolGuardMode | str = ToolGuardMode.OBSERVE,
        allowed_hosts: Iterable[str] = DEFAULT_ALLOWED_HOSTS,
        judge: Optional[ToolCallJudge] = None,
        judge_fail_closed: bool = False,
    ) -> None:
        self.mode = ToolGuardMode(mode)
        self.allowed_hosts = tuple(host.lower() for host in allowed_hosts)
        self.judge = judge
        self.judge_fail_closed = judge_fail_closed

    def review(
        self,
        raw_response: Any,
        *,
        tools: Optional[Iterable[Any]] = None,
        conversation: str = "",
        task: str = "",
    ) -> list[ToolCallDecision]:
        declared = declared_tools(tools)
        context = conversation.lower()
        decisions = []
        for index, call in enumerate(tool_calls_of(raw_response)):
            findings = list(self._findings(call, declared, context))
            judge_note = None
            # Only suspicious calls reach the judge; clean and already-severe calls don't.
            if self.judge is not None and findings and not any(f.severity >= Severity.HIGH for f in findings):
                finding, judge_note = self._ask_judge(task or conversation, call, findings)
                if finding is not None:
                    findings.append(finding)
            decisions.append(ToolCallDecision(index, call, tuple(findings), self.mode, judge_note))
        return decisions

    def _ask_judge(self, task: str, call: ToolCall, findings: list[Finding]) -> tuple[Optional[Finding], str]:
        assert self.judge is not None
        try:
            block, reason = self.judge.review(task, call, findings)
        except ToolJudgeError as e:
            return (JUDGE_UNAVAILABLE if self.judge_fail_closed else None), f"error: {e}"
        if block:
            return Finding(JUDGE_BLOCK, Severity.HIGH, f"Judge blocked the call: {reason}"), f"block: {reason}"
        return None, f"allow: {reason}"

    def _findings(self, call: ToolCall, declared: set[str], context: str) -> tuple[Finding, ...]:
        findings: list[Finding] = []
        if call.name not in declared:
            findings.append(UNDECLARED_TOOL)
        unseen = sorted(host for host in extract_hosts(call.arguments) if not self._known_host(host, context))
        if unseen:
            findings.append(Finding(UNSEEN_HOST.id, UNSEEN_HOST.severity, f"{UNSEEN_HOST.title}: {', '.join(unseen)}"))
        haystack = f"{call.name} {call.arguments}"
        findings.extend(finding for finding, pattern in RISKY_PATTERNS if pattern.search(haystack))
        return tuple(findings)

    def _known_host(self, host: str, context: str) -> bool:
        if host in context:
            return True
        return any(host == allowed or host.endswith("." + allowed) for allowed in self.allowed_hosts)
