"""Web dashboard ledger and staged-rule tables: GET /api/dashboard/ledger and /api/dashboard/staged-rules, with paging,
sorting and filtering, and the markup that uses them."""
from __future__ import annotations

import time
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from ct_toolkit import server
from ct_toolkit.provenance.ledger import staged_page, staged_rules
from ct_toolkit.provenance.log import ProvenanceLog


def _turn(prov: ProvenanceLog, endpoint: str, status: int = 200, finish: str = "stop") -> None:
    prov.record(
        request_text="hello",
        response_text="hi",
        metadata={"event": "model_turn", "endpoint": endpoint, "status": status, "finish_reason": finish, "tool_calls": 0, "provider": "proxy", "llm_model": "gpt-4o-mini"},
    )


def _tool_call(prov: ProvenanceLog, tool: str, verdict: str) -> None:
    prov.record(
        request_text="guard review",
        response_text=verdict,
        metadata={"event": "tool_call", "tool": tool, "verdict": verdict, "mode": "observe", "findings": "", "judge": "", "provider": "proxy", "llm_model": "gpt-4o-mini", "endpoint": "/v1/chat/completions"},
    )


def _divergence(prov: ProvenanceLog, score: float) -> None:
    prov.record(request_text="kernel check", response_text="ok", divergence_score=score, metadata={"kernel": "general", "tier": "ok", "model": "gpt-4o-mini"})


def _staged(prov: ProvenanceLog, eid: str, rule: str, cooldown_until: float, promoted: bool = False) -> None:
    prov.record(
        request_text="endorse",
        response_text=rule,
        metadata={"event_type": "reflective_endorsement", "decision": "staged", "endorsement_id": eid, "commitment_id": f"commit-{eid}", "rule_text": rule, "cooldown_until": cooldown_until},
    )
    if promoted:
        prov.record(request_text="promote", response_text=eid, metadata={"event_type": "staged_promotion", "endorsement_id": eid})


@pytest.fixture
def dashboard(tmp_path, monkeypatch) -> Iterator[tuple[TestClient, ProvenanceLog]]:
    monkeypatch.delenv("CT_SERVER_API_KEY", raising=False)
    prev_wrapper = server._wrapper_instance
    server._wrapper_instance = None
    server.configure_server_auth(None)
    # The caches key on id(log); a new test can reuse the id of a log that is gone, so start each test empty.
    server._read_cache.clear()
    server._generation_seen.clear()
    db_path = tmp_path / "v.db"
    server.configure_dashboard_vault(vault_path=str(db_path), vault_type="local")
    prov = ProvenanceLog(vault_path=str(db_path))
    try:
        yield TestClient(server.app), prov
    finally:
        server._wrapper_instance = prev_wrapper
        server._read_cache.clear()
        server._generation_seen.clear()


class TestLedgerApi:
    def test_lists_every_kind_newest_first(self, dashboard) -> None:
        client, prov = dashboard
        _divergence(prov, 0.1)
        _tool_call(prov, "shell", "passed")
        _turn(prov, "/v1/chat/completions")
        data = client.get("/api/dashboard/ledger").json()

        assert data["total"] == 3
        assert [item["kind"] for item in data["items"]] == ["model_turn", "tool_call", "divergence"]

    def test_only_the_divergence_rows_have_a_tier(self, dashboard) -> None:
        client, prov = dashboard
        _divergence(prov, 0.1)
        _tool_call(prov, "shell", "passed")
        _turn(prov, "/v1/chat/completions")
        tiers = {item["kind"]: item["tier"] for item in client.get("/api/dashboard/ledger").json()["items"]}

        assert tiers == {"divergence": "ok", "tool_call": None, "model_turn": None}

    def test_filters_by_kind(self, dashboard) -> None:
        client, prov = dashboard
        _divergence(prov, 0.1)
        _tool_call(prov, "shell", "passed")
        _turn(prov, "/v1/chat/completions")

        assert [i["kind"] for i in client.get("/api/dashboard/ledger", params={"kind": "tool_call"}).json()["items"]] == ["tool_call"]
        assert [i["kind"] for i in client.get("/api/dashboard/ledger", params={"kind": "divergence"}).json()["items"]] == ["divergence"]

    def test_search_reads_the_metadata_such_as_the_endpoint(self, dashboard) -> None:
        client, prov = dashboard
        _turn(prov, "/v1/chat/completions")
        _turn(prov, "/v1/responses")
        items = client.get("/api/dashboard/ledger", params={"q": "RESPONSES"}).json()["items"]

        assert [i["endpoint"] for i in items] == ["/v1/responses"]

    def test_sorts_by_score_and_order(self, dashboard) -> None:
        client, prov = dashboard
        for score in (0.3, 0.1, 0.2):
            _divergence(prov, score)
        _tool_call(prov, "shell", "passed")
        asc = client.get("/api/dashboard/ledger", params={"kind": "divergence", "sort": "score", "order": "asc"}).json()
        desc = client.get("/api/dashboard/ledger", params={"kind": "divergence", "sort": "score", "order": "desc"}).json()

        assert [i["divergence_score"] for i in asc["items"]] == [0.1, 0.2, 0.3]
        assert [i["divergence_score"] for i in desc["items"]] == [0.3, 0.2, 0.1]

    def test_sorts_by_kind(self, dashboard) -> None:
        client, prov = dashboard
        _divergence(prov, 0.1)
        _tool_call(prov, "shell", "passed")
        _turn(prov, "/v1/chat/completions")
        kinds = [i["kind"] for i in client.get("/api/dashboard/ledger", params={"sort": "kind", "order": "asc"}).json()["items"]]

        assert kinds == sorted(kinds)

    def test_pages_and_clamps_the_page_number(self, dashboard) -> None:
        client, prov = dashboard
        for _ in range(5):
            _turn(prov, "/v1/chat/completions")
        first = client.get("/api/dashboard/ledger", params={"page_size": 2, "page": 1}).json()
        last = client.get("/api/dashboard/ledger", params={"page_size": 2, "page": 99}).json()

        assert (first["total"], first["pages"], len(first["items"])) == (5, 3, 2)
        assert (last["page"], len(last["items"])) == (3, 1)

    def test_empty_vault_gives_an_empty_page(self, dashboard) -> None:
        client, _ = dashboard
        data = client.get("/api/dashboard/ledger").json()

        assert data["items"] == [] and data["total"] == 0 and data["pages"] == 1

    @pytest.mark.parametrize("params", [{"kind": "nope"}, {"sort": "verdict"}, {"order": "up"}])
    def test_rejects_unknown_kind_sort_or_order(self, dashboard, params) -> None:
        client, _ = dashboard

        assert client.get("/api/dashboard/ledger", params=params).status_code == 400

    def test_ledger_sees_new_entries_after_a_write(self, dashboard, monkeypatch) -> None:
        client, prov = dashboard
        # The dashboard asks the vault for its write generation at most once per interval; a write from another
        # process shows after that interval. Zero makes the test see the write at once.
        monkeypatch.setattr(server, "GENERATION_CHECK_INTERVAL_S", 0.0)
        assert client.get("/api/dashboard/ledger").json()["total"] == 0
        _turn(prov, "/v1/chat/completions")

        assert client.get("/api/dashboard/ledger").json()["total"] == 1


class TestStagedRulesApi:
    def test_lists_rules_with_their_status(self, dashboard) -> None:
        client, prov = dashboard
        now = time.time()
        _staged(prov, "e1", "Never read secrets", now + 3600)
        _staged(prov, "e2", "Keep logs short", now - 10)
        _staged(prov, "e3", "Pin versions", now + 60, promoted=True)
        data = client.get("/api/dashboard/staged-rules").json()

        assert data["total"] == 3
        assert {item["endorsement_id"]: item["status"] for item in data["items"]} == {
            "e1": "ACTIVE_COOLDOWN",
            "e2": "READY_FOR_PROMOTION",
            "e3": "PROMOTED",
        }

    def test_filters_by_status_and_search(self, dashboard) -> None:
        client, prov = dashboard
        now = time.time()
        _staged(prov, "e1", "Never read secrets", now + 3600)
        _staged(prov, "e2", "Keep logs short", now - 10)

        by_status = client.get("/api/dashboard/staged-rules", params={"status": "READY_FOR_PROMOTION"}).json()
        by_text = client.get("/api/dashboard/staged-rules", params={"q": "secrets"}).json()

        assert [i["endorsement_id"] for i in by_status["items"]] == ["e2"]
        assert [i["endorsement_id"] for i in by_text["items"]] == ["e1"]

    def test_sorts_by_rule_text(self, dashboard) -> None:
        client, prov = dashboard
        now = time.time()
        _staged(prov, "e1", "Zeta rule", now)
        _staged(prov, "e2", "Alpha rule", now)
        data = client.get("/api/dashboard/staged-rules", params={"sort": "rule", "order": "asc"}).json()

        assert [i["rule_text"] for i in data["items"]] == ["Alpha rule", "Zeta rule"]

    def test_rejects_unknown_status_or_sort(self, dashboard) -> None:
        client, _ = dashboard

        assert client.get("/api/dashboard/staged-rules", params={"status": "DONE"}).status_code == 400
        assert client.get("/api/dashboard/staged-rules", params={"sort": "owner"}).status_code == 400


class TestStagedPageUnit:
    def test_remaining_sort_orders_cooldowns(self) -> None:
        rules = [
            {"endorsement_id": "a", "commitment_id": "c", "rule_text": "x", "status": "ACTIVE_COOLDOWN", "remaining_seconds": 90},
            {"endorsement_id": "b", "commitment_id": "c", "rule_text": "y", "status": "ACTIVE_COOLDOWN", "remaining_seconds": 30},
        ]
        page = staged_page(rules, sort="remaining", order="asc")

        assert [r["endorsement_id"] for r in page.items] == ["b", "a"]

    def test_staged_rules_marks_promoted_rules_before_the_clock(self) -> None:
        records = [{"endorsement_id": "e1", "cooldown_until": time.time() + 100, "rule_text": "r", "commitment_id": "c"}]
        rules = staged_rules(records, {"e1"}, time.time())

        assert rules[0]["status"] == "PROMOTED" and rules[0]["remaining_seconds"] == 0


def test_dashboard_html_has_ledger_and_staged_tables(dashboard) -> None:
    client, _ = dashboard
    html = client.get("/dashboard").text
    for marker in ('id="ledger-card"', 'id="lg-kind"', 'id="lg-search"', 'id="staged-card"', 'id="sr-status"', 'id="sr-search"', "/api/dashboard/ledger", "/api/dashboard/staged-rules"):
        assert marker in html
