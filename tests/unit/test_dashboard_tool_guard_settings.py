"""Web dashboard "Tool-Call Guard Settings" panel: GET and PUT /api/dashboard/tool-guard, the audit entry a saved
change leaves in the provenance chain, and the dashboard markup."""
from __future__ import annotations

import json
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from ct_toolkit import server
from ct_toolkit.provenance.log import ProvenanceLog

SETTINGS_NAME = "tool_guard_settings.json"


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.delenv("CT_SERVER_API_KEY", raising=False)
    monkeypatch.setattr(server, "_wrapper_instance", None)
    server.configure_server_auth(None)
    server.configure_dashboard_vault(vault_path=str(tmp_path / "v.db"), vault_type="local")
    server._dashboard_log_cache = None
    monkeypatch.setattr(server, "_tool_guard_settings_path", str(tmp_path / SETTINGS_NAME))
    try:
        yield TestClient(server.app)
    finally:
        if server._dashboard_log_cache is not None:
            server._dashboard_log_cache[1].stop_verifier()
        server._dashboard_log_cache = None
        server.configure_server_auth(None)
        server.configure_dashboard_vault(vault_path="./ct_provenance.db", vault_type="local")


def _settings_file(tmp_path):
    return tmp_path / SETTINGS_NAME


def _changed_entries(tmp_path):
    return ProvenanceLog(vault_path=str(tmp_path / "v.db")).recent_entries(event="tool_guard_settings_changed")


def test_get_without_a_file_reports_the_defaults(client) -> None:
    resp = client.get("/api/dashboard/tool-guard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "defaults"
    assert data["file_error"] is None
    assert data["modes"] == ["observe", "enforce"]
    assert data["settings"] == {
        "mode": "observe",
        "extra_hosts": [],
        "judge_model": None,
        "judge_api_base": None,
        "judge_fail_closed": False,
    }
    assert isinstance(data["default_allowed_hosts"], list) and data["default_allowed_hosts"]
    assert data["path"].endswith(SETTINGS_NAME)


def test_put_valid_body_writes_the_file_with_mode_0600(client, tmp_path) -> None:
    body = {
        "mode": "enforce",
        "extra_hosts": ["internal.example.com"],
        "judge_model": "openai/gemma-4-E4B-it",
        "judge_api_base": "http://localhost:8090/v1",
        "judge_fail_closed": True,
    }
    resp = client.put("/api/dashboard/tool-guard", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "file"
    assert data["settings"]["mode"] == "enforce"
    assert data["settings"]["extra_hosts"] == ["internal.example.com"]
    path = _settings_file(tmp_path)
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["mode"] == "enforce"
    assert saved["judge_fail_closed"] is True


def test_get_after_put_reflects_the_saved_settings(client) -> None:
    client.put("/api/dashboard/tool-guard", json={"mode": "enforce", "extra_hosts": ["b.example.org", "a.example.com"]})
    data = client.get("/api/dashboard/tool-guard").json()
    assert data["source"] == "file"
    assert data["file_error"] is None
    assert data["settings"]["mode"] == "enforce"
    assert data["settings"]["extra_hosts"] == ["a.example.com", "b.example.org"]


def test_get_reports_a_broken_file_as_file_error(client, tmp_path) -> None:
    _settings_file(tmp_path).write_text("{not json", encoding="utf-8")
    data = client.get("/api/dashboard/tool-guard").json()
    assert data["source"] == "defaults"
    assert data["file_error"]
    assert "not valid JSON" in data["file_error"]


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "panic"},
        {"extra_hosts": ["bad host!"]},
        {"judge_api_base": "ftp://example.com/v1"},
        {"judge_fail_closed": "yes"},
        {"bogus": 1},
        ["not", "an", "object"],
    ],
)
def test_put_invalid_input_returns_400_and_writes_nothing(client, tmp_path, body) -> None:
    resp = client.put("/api/dashboard/tool-guard", json=body)
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert isinstance(detail, str) and detail
    assert not _settings_file(tmp_path).exists()


def test_put_body_that_is_not_json_returns_400(client, tmp_path) -> None:
    resp = client.put(
        "/api/dashboard/tool-guard", content=b"mode=enforce", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 400
    assert "JSON" in resp.json()["detail"]
    assert not _settings_file(tmp_path).exists()


def test_put_unknown_key_names_the_key(client) -> None:
    resp = client.put("/api/dashboard/tool-guard", json={"mode": "observe", "bogus": 1})
    assert resp.status_code == 400
    assert "bogus" in resp.json()["detail"]


def test_put_that_changes_the_mode_is_recorded_in_provenance(client, tmp_path) -> None:
    assert client.put("/api/dashboard/tool-guard", json={"mode": "enforce"}).status_code == 200
    entries = _changed_entries(tmp_path)
    assert len(entries) == 1
    meta = entries[0].metadata
    assert meta["event"] == "tool_guard_settings_changed"
    assert meta["mode"] == "enforce"
    assert "mode" in meta["changed"].split(",")


def test_put_without_a_changed_value_writes_no_entry(client, tmp_path) -> None:
    assert client.put("/api/dashboard/tool-guard", json={"mode": "observe"}).status_code == 200
    assert _changed_entries(tmp_path) == []
    assert client.put("/api/dashboard/tool-guard", json={"mode": "enforce"}).status_code == 200
    assert len(_changed_entries(tmp_path)) == 1
    # Saving the same values again changes nothing, so nothing new is recorded.
    assert client.put("/api/dashboard/tool-guard", json={"mode": "enforce"}).status_code == 200
    assert len(_changed_entries(tmp_path)) == 1


def test_dashboard_markup_has_the_tool_guard_panel() -> None:
    html = server._DASHBOARD_HTML
    assert 'id="tool-guard-card"' in html
    assert "Tool-Call Guard Settings" in html
    for element_id in ("tg-mode", "tg-hosts", "tg-judge-model", "tg-judge-base", "tg-fail-closed"):
        assert f'id="{element_id}"' in html
    assert "function escapeHtml" in html
    assert "/api/dashboard/tool-guard" in html
    # The existing endpoints stay in the page.
    assert "/api/dashboard/status" in html
    assert "/api/dashboard/tool-calls" in html
