import sqlite3
from unittest.mock import patch

import pytest

from ct_toolkit.core.exceptions import ChainIntegrityError
from ct_toolkit.provenance import log as provenance_log
from ct_toolkit.provenance.log import ProvenanceLog
from ct_toolkit.provenance.tool_calls import query_page


def _tool(log, tool, verdict, mode="observe", agent=None):
    meta = {"event": "tool_call", "tool": tool, "verdict": verdict, "mode": mode, "findings": "", "judge": "", "provider": "openai", "llm_model": "m"}
    if agent:
        meta["agent_id"] = agent
    return log.record("q", f"{tool}(...)", metadata=meta)


def _divergence(log, score, tier="ok"):
    return log.record("q", "a", divergence_score=score, metadata={"tier": tier, "model": "m"})


@pytest.fixture
def log(tmp_path):
    return ProvenanceLog(vault_path=str(tmp_path / "v.db"))


def test_counters_follow_writes_and_rollbacks(log):
    _tool(log, "bash", "passed", agent="a1")
    shell = _tool(log, "shell", "blocked", agent="a1")
    _divergence(log, 0.2)  # divergence entries have no tool counters
    counters = log.counters()
    assert counters["tool:total"] == 2
    assert counters["tool:verdict:blocked"] == 1
    assert counters["tool:tool:bash"] == 1

    _tool(log, "bash", "flagged", agent="a1")
    assert log.counters()["tool:total"] == 3
    # Roll back to the shell call: the entry after it for agent a1 (the flagged one) is no longer active.
    log.rollback("a1", shell)
    counters = log.counters()
    assert counters["tool:total"] == 2
    assert counters.get("tool:verdict:flagged", 0) == 0


def test_page_filters_sorts_and_clamps(log):
    _tool(log, "Bash", "passed")
    _tool(log, "shell_exec", "blocked")
    _tool(log, "bash", "flagged")
    entries, total, page, pages = log.tool_call_page(verdict="blocked")
    assert (total, page, pages) == (1, 1, 1) and entries[0].metadata["tool"] == "shell_exec"

    entries, total, _, _ = log.tool_call_page(tool="BASH", sort="tool", order="asc")
    assert total == 2 and [e.metadata["tool"] for e in entries] == ["Bash", "bash"]

    entries, total, _, _ = log.tool_call_page(search="shell")
    assert total == 1

    entries, total, page, pages = log.tool_call_page(page=9, page_size=2)
    assert (page, pages, total) == (2, 2, 3)  # clamped to the last page
    assert len(entries) == 1


def test_like_wildcards_in_user_text_are_literal(log):
    _tool(log, "a_b", "passed")
    _tool(log, "axb", "passed")
    _, total, _, _ = log.tool_call_page(tool="a_b")
    assert total == 1


def test_recent_entries_split_divergence_and_tool_calls(log):
    _divergence(log, 0.1)
    _tool(log, "bash", "passed")
    _divergence(log, 0.2)
    divergence = log.recent_entries(None, limit=10)
    assert [e.divergence_score for e in divergence] == [0.2, 0.1]
    assert len(log.recent_entries("tool_call", limit=10)) == 1


def test_incremental_verification_reads_only_new_entries(log):
    _divergence(log, 0.1)
    first = log.verify_incremental()
    assert first["mode"] == "full" and first["verified_count"] == 1
    _divergence(log, 0.2)
    second = log.verify_incremental()
    assert second["mode"] == "incremental" and second["verified_count"] == 2


def test_incremental_check_misses_old_edits_but_a_full_pass_finds_them(log, tmp_path):
    _divergence(log, 0.1)
    _divergence(log, 0.2)
    log.verify_incremental()
    with sqlite3.connect(str(tmp_path / "v.db")) as conn:
        conn.execute("UPDATE provenance SET divergence_score = 0.9 WHERE divergence_score = 0.1")
        conn.commit()
    assert log.verify_incremental()["mode"] == "incremental"  # already-verified entries are not re-read
    with pytest.raises(ChainIntegrityError):
        log.verify_incremental(force_full=True)


def test_a_full_pass_becomes_due_after_the_interval(log):
    _divergence(log, 0.1)
    log.verify_incremental()
    with patch.object(provenance_log, "FULL_VERIFY_INTERVAL_S", 0.0):
        assert log.verify_incremental()["mode"] == "full"


def test_find_tool_calls_by_id_prefix(log):
    entry_id = _tool(log, "bash", "passed")
    assert [e.id for e in log.find_tool_calls(entry_id[:8])] == [entry_id]
    assert log.find_tool_calls("no-such-prefix") == []


def test_older_vault_gets_derived_columns_and_counters(tmp_path):
    """A vault written before the derived columns existed is upgraded on open."""
    path = tmp_path / "old.db"
    ProvenanceLog(vault_path=str(path))  # creates the key file next to the vault
    with sqlite3.connect(str(path)) as conn:
        conn.execute("DROP TABLE provenance")
        conn.execute("DROP TABLE provenance_counters")
        conn.execute(
            "CREATE TABLE provenance (id TEXT PRIMARY KEY, timestamp REAL NOT NULL, request_hash TEXT NOT NULL, "
            "response_hash TEXT NOT NULL, divergence_score REAL, metadata TEXT NOT NULL, prev_entry_hash TEXT NOT NULL, "
            "hmac_signature TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active')"
        )
        conn.execute(
            "INSERT INTO provenance VALUES ('t1', 1.0, 'h', 'h', NULL, "
            "'{\"event\": \"tool_call\", \"tool\": \"bash\", \"verdict\": \"passed\"}', 'x', 'x', 'active')"
        )
        conn.commit()
    upgraded = ProvenanceLog(vault_path=str(path))
    assert upgraded.counters()["tool:total"] == 1
    page = query_page(upgraded, verdict="passed")
    assert page.total == 1 and page.items[0].tool == "bash"


def test_second_writer_on_the_same_vault_keeps_the_chain_valid(tmp_path):
    """Two log objects on one vault, as two processes would have: the first one's cached tip goes stale."""
    path = str(tmp_path / "shared.db")
    first = ProvenanceLog(vault_path=path)
    second = ProvenanceLog(vault_path=path)
    first.record("q", "a")
    second.record("q", "b")  # moves the tip under `first`
    first.record("q", "c")  # its cached tip is stale: the conditional write retries on the current tip
    assert first.verify_chain() is True
    assert len(first.export_log()) == 3
