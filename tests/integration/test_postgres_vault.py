"""Provenance on PostgreSQL. Skipped unless CT_TEST_POSTGRES_URL is set, for example:

    CT_TEST_POSTGRES_URL=postgresql+psycopg://user:pass@host:5432/ct_toolkit_test

The database must be disposable: each test drops and recreates the provenance tables in it.
"""

import os
import time

import pytest

from ct_toolkit.core.exceptions import ChainIntegrityError
from ct_toolkit.provenance.log import ProvenanceLog
from ct_toolkit.provenance.tool_calls import query_page

URL = os.environ.get("CT_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set CT_TEST_POSTGRES_URL to run the PostgreSQL vault tests")


def _fresh_log() -> ProvenanceLog:
    from sqlalchemy import create_engine, text

    engine = create_engine(URL, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS provenance"))
        conn.execute(text("DROP TABLE IF EXISTS provenance_counters"))
    engine.dispose()
    return ProvenanceLog(vault_type="postgres", vault_path=URL)


def _tool(log, tool="bash", verdict="passed", agent=None):
    meta = {"event": "tool_call", "tool": tool, "verdict": verdict, "mode": "observe", "findings": "", "judge": ""}
    if agent:
        meta["agent_id"] = agent
    return log.record("q", f"{tool}(...)", metadata=meta)


def _divergence(log, score, tier="ok"):
    return log.record("q", "a", divergence_score=score, metadata={"tier": tier, "model": "m"})


@pytest.fixture
def log():
    return _fresh_log()


def test_write_counters_and_generation(log):
    before = log.generation()
    first = _tool(log, verdict="passed", agent="a1")
    _tool(log, verdict="blocked", agent="a1")
    _divergence(log, 0.2)
    counters = log.counters()
    assert counters["tool:total"] == 2
    assert counters["tool:verdict:blocked"] == 1
    assert log.generation() > before

    generation = log.generation()
    log.rollback("a1", first)
    assert log.generation() > generation
    assert log.counters()["tool:total"] == 1


def test_sql_paging_filtering_and_sorting(log):
    for tool, verdict in [("Bash", "passed"), ("shell_exec", "blocked"), ("bash", "flagged")]:
        _tool(log, tool, verdict)
    page = query_page(log, verdict="blocked")
    assert page.total == 1 and page.items[0].tool == "shell_exec"

    page = query_page(log, tool="BASH", sort="tool", order="asc")
    assert [row.tool for row in page.items] == ["Bash", "bash"]

    page = query_page(log, page=9, page_size=2)
    assert (page.page, page.pages, page.total, len(page.items)) == (2, 2, 3, 1)
    assert page.summary["by_verdict"] == {"passed": 1, "flagged": 1, "blocked": 1}


def test_wildcards_in_user_text_are_literal(log):
    _tool(log, "a_b")
    _tool(log, "axb")
    assert query_page(log, tool="a_b").total == 1


def test_recent_entries_separate_divergence_from_tool_calls(log):
    _divergence(log, 0.1)
    _tool(log)
    _divergence(log, 0.2)
    assert [e.divergence_score for e in log.recent_entries(None, limit=10)] == [0.2, 0.1]
    assert len(log.recent_entries("tool_call", limit=10)) == 1


def test_incremental_verification_and_tamper_detection(log):
    _divergence(log, 0.1)
    assert log.verify_incremental()["mode"] == "full"
    _divergence(log, 0.2)
    assert log.verify_incremental()["mode"] == "incremental"

    from sqlalchemy import create_engine, text

    engine = create_engine(URL, future=True)
    with engine.begin() as conn:
        conn.execute(text("UPDATE provenance SET divergence_score = 0.9 WHERE divergence_score = 0.1"))
    engine.dispose()
    with pytest.raises(ChainIntegrityError):
        log.verify_incremental(force_full=True)


def test_background_verifier_publishes_a_result(log):
    _divergence(log, 0.1)
    log.start_verifier(interval_s=0.05)
    try:
        deadline = time.monotonic() + 5
        while log.integrity_status() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        status = log.integrity_status()
        assert status is not None and status["chain_ok"] is True
    finally:
        log.stop_verifier()


def test_older_schema_is_upgraded_on_open():
    """A PostgreSQL vault created before the derived columns and counters existed."""
    from sqlalchemy import create_engine, text

    engine = create_engine(URL, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS provenance"))
        conn.execute(text("DROP TABLE IF EXISTS provenance_counters"))
        conn.execute(
            text(
                "CREATE TABLE provenance (id TEXT PRIMARY KEY, timestamp DOUBLE PRECISION NOT NULL, "
                "request_hash TEXT NOT NULL, response_hash TEXT NOT NULL, divergence_score DOUBLE PRECISION, "
                "metadata TEXT NOT NULL, prev_entry_hash TEXT NOT NULL, hmac_signature TEXT NOT NULL, "
                "status TEXT NOT NULL DEFAULT 'active')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO provenance VALUES ('t1', 1.0, 'h', 'h', NULL, "
                "'{\"event\": \"tool_call\", \"tool\": \"bash\", \"verdict\": \"passed\"}', 'x', 'x', 'active')"
            )
        )
    engine.dispose()
    upgraded = ProvenanceLog(vault_type="postgres", vault_path=URL)
    assert upgraded.counters()["tool:total"] == 1
    assert query_page(upgraded, verdict="passed").total == 1
