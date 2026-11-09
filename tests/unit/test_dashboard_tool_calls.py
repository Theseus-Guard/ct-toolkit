"""Web dashboard "Tool calls" section: GET /api/dashboard/tool-calls, the divergence telemetry that ignores
tool-call entries, the newest-first ledger, and the dashboard markup."""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from ct_toolkit import server
from ct_toolkit.provenance.log import ProvenanceLog


def _tool_call(
    prov: ProvenanceLog,
    tool: str,
    verdict: str,
    *,
    mode: str = "enforce",
    findings: str = "",
    judge: str = "",
    model: str = "gpt-4o-mini",
) -> None:
    prov.record(
        request_text=f"guard review of {tool}",
        response_text=verdict,
        metadata={
            "event": "tool_call",
            "tool": tool,
            "verdict": verdict,
            "mode": mode,
            "findings": findings,
            "judge": judge,
            "provider": "openai",
            "llm_model": model,
        },
    )


def _divergence(prov: ProvenanceLog, score: float, tier: str) -> None:
    prov.record(
        request_text="Check kernel alignment",
        response_text="Responding.",
        divergence_score=score,
        metadata={"kernel": "general", "tier": tier, "model": "gpt-4o-mini"},
    )


@pytest.fixture
def dashboard(tmp_path, monkeypatch) -> Iterator[tuple[TestClient, ProvenanceLog]]:
    monkeypatch.delenv("CT_SERVER_API_KEY", raising=False)
    prev_wrapper = server._wrapper_instance
    server._wrapper_instance = None
    server.configure_server_auth(None)
    db_path = tmp_path / "v.db"
    server.configure_dashboard_vault(vault_path=str(db_path), vault_type="local")
    prov = ProvenanceLog(vault_path=str(db_path))
    try:
        yield TestClient(server.app), prov
    finally:
        server._wrapper_instance = prev_wrapper
        server.configure_server_auth(None)
        server.configure_dashboard_vault(vault_path="./ct_provenance.db", vault_type="local")


@pytest.fixture
def seeded(dashboard):
    """Five tool-call decisions and two divergence entries, recorded in that order."""
    client, prov = dashboard
    _tool_call(prov, "shell", "passed", mode="monitor", model="gpt-4o-mini")
    _divergence(prov, 0.06, "ok")
    _tool_call(prov, "read_file", "flagged", mode="monitor", findings="reads ~/.ssh", judge="sensitive path", model="gemma-4-E4B-it")
    _tool_call(prov, "shell", "blocked", findings="curl | sh", judge="pipes a remote script")
    _divergence(prov, 0.34, "l2_judge")
    _tool_call(prov, "Write_File", "passed", mode="monitor", model="claude-sonnet")
    _tool_call(prov, "delete_db", "blocked", findings="drops tables", judge="destructive")
    return client


def test_default_listing_has_only_tool_calls(seeded) -> None:
    resp = seeded.get("/api/dashboard/tool-calls")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert data["pages"] == 1
    assert len(data["items"]) == 5
    assert all(item["verdict"] in ("passed", "flagged", "blocked") for item in data["items"])
    # Default order is newest first.
    timestamps = [item["timestamp"] for item in data["items"]]
    assert timestamps == sorted(timestamps, reverse=True)
    assert data["summary"]["total"] == 5
    assert data["summary"]["by_verdict"] == {"passed": 2, "flagged": 1, "blocked": 2}
    assert data["summary"]["by_tool"]["shell"] == 2


def test_row_fields(seeded) -> None:
    items = seeded.get("/api/dashboard/tool-calls", params={"tool": "read_file"}).json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["tool"] == "read_file"
    assert item["verdict"] == "flagged"
    assert item["mode"] == "monitor"
    assert item["findings"] == "reads ~/.ssh"
    assert item["judge"] == "sensitive path"
    assert item["provider"] == "openai"
    assert item["llm_model"] == "gemma-4-E4B-it"
    assert item["id"]


def test_verdict_filter(seeded) -> None:
    data = seeded.get("/api/dashboard/tool-calls", params={"verdict": "blocked"}).json()
    assert data["total"] == 2
    assert {item["verdict"] for item in data["items"]} == {"blocked"}
    # The summary is over all tool calls, not only the filtered ones.
    assert data["summary"]["total"] == 5


def test_tool_filter_is_case_insensitive_substring(seeded) -> None:
    data = seeded.get("/api/dashboard/tool-calls", params={"tool": "SHELL"}).json()
    assert data["total"] == 2
    assert {item["tool"] for item in data["items"]} == {"shell"}


def test_search_covers_findings_judge_and_model(seeded) -> None:
    by_findings = seeded.get("/api/dashboard/tool-calls", params={"q": "ssh"}).json()
    assert [item["tool"] for item in by_findings["items"]] == ["read_file"]
    by_judge = seeded.get("/api/dashboard/tool-calls", params={"q": "remote script"}).json()
    assert [item["tool"] for item in by_judge["items"]] == ["shell"]
    by_model = seeded.get("/api/dashboard/tool-calls", params={"q": "gemma"}).json()
    assert [item["tool"] for item in by_model["items"]] == ["read_file"]


def test_sort_by_tool_ascending(seeded) -> None:
    data = seeded.get(
        "/api/dashboard/tool-calls", params={"sort": "tool", "order": "asc", "page_size": 100}
    ).json()
    tools = [item["tool"].lower() for item in data["items"]]
    assert tools == ["delete_db", "read_file", "shell", "shell", "write_file"]
    assert [item["tool"] for item in data["items"]][-1] == "Write_File"


def test_paging(seeded) -> None:
    first = seeded.get("/api/dashboard/tool-calls", params={"page_size": 2, "sort": "tool", "order": "asc"}).json()
    assert first["total"] == 5
    assert first["pages"] == 3
    assert len(first["items"]) == 2

    last = seeded.get(
        "/api/dashboard/tool-calls", params={"page_size": 2, "page": 3, "sort": "tool", "order": "asc"}
    ).json()
    assert len(last["items"]) == 1
    assert last["page"] == 3

    # A page past the end is clamped to the last page.
    beyond = seeded.get("/api/dashboard/tool-calls", params={"page_size": 2, "page": 9}).json()
    assert beyond["page"] == 3
    assert len(beyond["items"]) == 1


def test_pages_do_not_overlap(seeded) -> None:
    params = {"page_size": 2, "sort": "time", "order": "asc"}
    ids = []
    for page in (1, 2, 3):
        ids += [item["id"] for item in seeded.get("/api/dashboard/tool-calls", params={**params, "page": page}).json()["items"]]
    assert len(ids) == 5
    assert len(set(ids)) == 5


@pytest.mark.parametrize(
    "params, fragment",
    [
        ({"sort": "bogus"}, "sort must be one of"),
        ({"order": "sideways"}, "order must be asc or desc"),
        ({"verdict": "maybe"}, "verdict must be one of"),
    ],
)
def test_bad_query_values_return_400(seeded, params, fragment) -> None:
    resp = seeded.get("/api/dashboard/tool-calls", params=params)
    assert resp.status_code == 400
    assert fragment in resp.json()["detail"]


def test_page_size_out_of_range_is_rejected(seeded) -> None:
    assert seeded.get("/api/dashboard/tool-calls", params={"page_size": 500}).status_code == 422
    assert seeded.get("/api/dashboard/tool-calls", params={"page": 0}).status_code == 422


def test_tool_calls_require_api_key_when_configured(dashboard) -> None:
    client, prov = dashboard
    _tool_call(prov, "shell", "passed")
    server.configure_server_auth("s3cret-token")
    assert client.get("/api/dashboard/tool-calls").status_code == 401
    ok = client.get("/api/dashboard/tool-calls", headers={"Authorization": "Bearer s3cret-token"})
    assert ok.status_code == 200
    assert ok.json()["total"] == 1


def test_divergence_telemetry_ignores_tool_calls(seeded) -> None:
    data = seeded.get("/api/dashboard/status").json()
    assert data["entries_analyzed"] == 2
    assert sum(data["tier_counts"].values()) == 2
    assert data["tier_counts"]["ok"] == 1
    assert data["tier_counts"]["l2_judge"] == 1
    assert [point["score"] for point in data["score_series"]] == [0.06, 0.34]
    assert data["max_score"] == 0.34
    # The ledger lists every kind of entry; the tiers and the chart still count divergence samples only.
    assert sorted(entry["kind"] for entry in data["recent_entries"]) == ["divergence", "divergence"] + ["tool_call"] * 5
    assert all(entry["tier"] is None for entry in data["recent_entries"] if entry["kind"] == "tool_call")
    # The chain itself still covers every entry, tool calls included.
    assert data["verified_count"] == 7


def test_ledger_shows_newest_entries_of_every_kind(dashboard) -> None:
    client, prov = dashboard
    scores = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06]
    for index, score in enumerate(scores):
        _divergence(prov, score, "ok")
        if index % 2 == 0:
            _tool_call(prov, "shell", "passed")

    data = client.get("/api/dashboard/status", params={"limit": 3}).json()
    assert data["entries_analyzed"] == 3
    # Oldest -> newest for the chart, newest first for the ledger, which is not cut down by the divergence window.
    assert [point["score"] for point in data["score_series"]] == [0.04, 0.05, 0.06]
    assert len(data["recent_entries"]) == 9
    assert data["recent_entries"][0]["kind"] == "divergence"
    assert [e["divergence_score"] for e in data["recent_entries"] if e["kind"] == "divergence"] == scores[::-1]


def test_dashboard_html_has_tool_calls_section(dashboard) -> None:
    client, _ = dashboard
    html = client.get("/dashboard").text
    assert 'id="tool-calls-card"' in html
    assert "Tool Calls (Guard Decisions)" in html
    assert "/api/dashboard/tool-calls" in html
    assert "/api/dashboard/status" in html
    # Untrusted fields (tool, findings, judge, model) are rendered through the escape helper.
    assert "function escapeHtml" in html
    assert "escapeHtml(r.tool" in html
    assert "escapeHtml(r.findings" in html
    assert "escapeHtml(r.judge" in html
    assert "escapeHtml(r.llm_model" in html
    for control in ("tc-verdict", "tc-tool", "tc-search", "tc-page-size", "tc-prev", "tc-next"):
        assert f'id="{control}"' in html
