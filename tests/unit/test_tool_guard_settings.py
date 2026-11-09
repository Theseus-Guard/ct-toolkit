import json
import os
import time

import httpx
import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from ct_toolkit.cli import app
from ct_toolkit.core import tool_guard_settings as gs
from ct_toolkit.core.tool_guard import DEFAULT_ALLOWED_HOSTS, ToolCallJudge, ToolGuard
from ct_toolkit.proxy import create_app

TOOLS = [{"type": "function", "function": {"name": "bash", "parameters": {}}}]
BAD_CALL = {"id": "c", "type": "function", "function": {"name": "shell_exec", "arguments": "{}"}}


def _completion(*calls):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": list(calls)}, "finish_reason": "tool_calls"}]}


def _touch_later(path, text):
    """Writes the file with a modification time that is surely newer than the previous write."""
    path.write_text(text, encoding="utf-8")
    later = time.time() + 5
    os.utime(path, (later, later))


class TestValidation:
    def test_defaults_when_empty(self):
        assert gs.validate({}) == gs.ToolGuardSettings()

    def test_hosts_are_normalised_and_deduplicated(self):
        settings = gs.validate({"extra_hosts": ["API.Example.com", " api.example.com ", "10.0.0.5"]})
        assert settings.extra_hosts == ("10.0.0.5", "api.example.com")
        assert set(DEFAULT_ALLOWED_HOSTS) <= set(settings.allowed_hosts())

    @pytest.mark.parametrize(
        "data, message",
        [
            ({"mode": "turbo"}, "mode must be"),
            ({"extra_hosts": ["bad host!"]}, "not a valid host name"),
            ({"extra_hosts": "api.example.com"}, "extra_hosts must be a list"),
            ({"judge_api_base": "ftp://x"}, "http:// or https://"),
            ({"judge_fail_closed": "yes"}, "true or false"),
            ({"judge_model": 3}, "must be a string"),
            ({"unknown": 1}, "unknown setting"),
        ],
    )
    def test_invalid_values_are_rejected_with_a_reason(self, data, message):
        with pytest.raises(ValueError, match=message):
            gs.validate(data)


class TestFile:
    def test_save_and_load_round_trip_with_private_permissions(self, tmp_path):
        path = tmp_path / "tool_guard_settings.json"
        saved = gs.validate({"mode": "enforce", "extra_hosts": ["api.example.com"], "judge_model": "openai/m"})
        gs.save(path, saved)
        assert gs.load(path) == saved
        assert os.stat(path).st_mode & 0o777 == 0o600

    def test_missing_file_loads_as_none(self, tmp_path):
        assert gs.load(tmp_path / "absent.json") is None

    def test_invalid_file_is_reported(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text('{"mode": "turbo"}', encoding="utf-8")
        with pytest.raises(ValueError, match="bad.json"):
            gs.load(path)

    def test_watcher_keeps_last_good_settings_when_the_file_breaks(self, tmp_path):
        path = tmp_path / "s.json"
        gs.save(path, gs.validate({"mode": "enforce"}))
        watcher = gs.SettingsWatcher(path)
        assert watcher.current().mode == "enforce"
        _touch_later(path, '{"mode": "turbo"}')
        assert watcher.current().mode == "enforce"
        assert watcher.last_error is not None

    def test_watcher_picks_up_a_change(self, tmp_path):
        path = tmp_path / "s.json"
        gs.save(path, gs.validate({"mode": "observe"}))
        watcher = gs.SettingsWatcher(path)
        _touch_later(path, json.dumps({"mode": "enforce"}))
        assert watcher.current().mode == "enforce"
        assert watcher.source == "file"

    def test_explicit_overrides_win_over_the_file(self, tmp_path):
        path = tmp_path / "s.json"
        gs.save(path, gs.validate({"mode": "observe", "extra_hosts": ["file.example.com"]}))
        watcher = gs.SettingsWatcher(path, overrides={"mode": "enforce"})
        _touch_later(path, json.dumps({"mode": "observe", "extra_hosts": ["other.example.com"]}))
        current = watcher.current()
        assert current.mode == "enforce"  # the flag still wins after the file changed
        assert current.extra_hosts == ("other.example.com",)


class TestApplyToGuard:
    def test_guard_follows_settings_and_rebuilds_judge_only_on_change(self):
        guard = ToolGuard()
        gs.apply_to_guard(guard, gs.validate({"mode": "enforce", "judge_model": "m", "judge_api_base": "http://x/v1"}))
        assert guard.mode.value == "enforce"
        judge = guard.judge
        assert isinstance(judge, ToolCallJudge) and judge.model == "m"
        gs.apply_to_guard(guard, gs.validate({"mode": "observe", "judge_model": "m", "judge_api_base": "http://x/v1"}))
        assert guard.judge is judge  # same judge configuration: kept
        gs.apply_to_guard(guard, gs.validate({}))
        assert guard.judge is None


class TestProxyPicksUpChanges:
    def test_a_change_in_the_file_applies_to_the_next_request(self, tmp_path):
        path = tmp_path / "s.json"
        gs.save(path, gs.validate({"mode": "observe"}))
        watcher = gs.SettingsWatcher(path)
        guard = ToolGuard("observe")

        def upstream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_completion(BAD_CALL))

        client = TestClient(create_app("http://upstream.test", guard, None, transport=httpx.MockTransport(upstream), settings=watcher))
        body = {"model": "m", "tools": TOOLS, "messages": [{"role": "user", "content": "go"}]}

        first = client.post("/v1/chat/completions", json=body).json()
        assert first["choices"][0]["message"]["tool_calls"]  # observe: the call passes

        _touch_later(path, json.dumps({"mode": "enforce"}))
        second = client.post("/v1/chat/completions", json=body).json()
        assert second["choices"][0]["message"]["tool_calls"] == []  # enforce: removed, without a restart


class TestCli:
    def test_config_set_then_show(self, tmp_path):
        path = tmp_path / "s.json"
        runner = CliRunner()
        result = runner.invoke(app, ["tool-guard", "config", "set", "--settings", str(path), "--mode", "enforce", "--allow-host", "api.example.com"])
        assert result.exit_code == 0, result.output
        assert gs.load(path).mode == "enforce"
        shown = runner.invoke(app, ["tool-guard", "config", "show", "--settings", str(path)])
        assert "enforce" in shown.output and "api.example.com" in shown.output

    def test_config_set_rejects_invalid_values_and_keeps_the_file(self, tmp_path):
        path = tmp_path / "s.json"
        gs.save(path, gs.validate({"mode": "observe"}))
        result = CliRunner().invoke(app, ["tool-guard", "config", "set", "--settings", str(path), "--judge-api-base", "ftp://x"])
        assert result.exit_code == 2
        assert gs.load(path).mode == "observe"

    def test_clear_judge_removes_model_and_base(self, tmp_path):
        path = tmp_path / "s.json"
        gs.save(path, gs.validate({"judge_model": "m", "judge_api_base": "http://x/v1"}))
        CliRunner().invoke(app, ["tool-guard", "config", "set", "--settings", str(path), "--clear-judge"])
        settings = gs.load(path)
        assert settings.judge_model is None and settings.judge_api_base is None
