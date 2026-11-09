
import sqlite3
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from ct_toolkit.provenance import log as provenance_log
from ct_toolkit.provenance.log import ProvenanceLog
from ct_toolkit.server import app, configure_dashboard_vault
from ct_toolkit.utils.cache import GenerationCache


def _tool(log, tool="bash", verdict="passed"):
    return log.record("q", f"{tool}(...)", metadata={"event": "tool_call", "tool": tool, "verdict": verdict, "mode": "observe"})


def test_generation_changes_on_write_and_rollback(tmp_path):
    log = ProvenanceLog(vault_path=str(tmp_path / "v.db"))
    start = log.generation()
    first = log.record("q", "a", metadata={"event": "tool_call", "tool": "bash", "verdict": "passed", "agent_id": "a1"})
    assert log.generation() > start
    log.record("q", "b", metadata={"event": "tool_call", "tool": "bash", "verdict": "passed", "agent_id": "a1"})
    before_rollback = log.generation()
    log.rollback("a1", first)
    assert log.generation() > before_rollback


def test_cache_hits_until_the_generation_changes():
    cache = GenerationCache(maxsize=4, max_age_s=60)
    calls = []

    def compute():
        calls.append(1)
        return len(calls)

    assert cache.get_or_compute("k", 1, compute) == 1
    assert cache.get_or_compute("k", 1, compute) == 1  # same generation: cached
    assert cache.get_or_compute("k", 2, compute) == 2  # generation moved: recomputed
    assert len(calls) == 2


def test_cache_entries_expire_after_max_age():
    cache = GenerationCache(maxsize=4, max_age_s=10)
    with patch("ct_toolkit.utils.cache.time.monotonic", return_value=100.0):
        cache.get_or_compute("k", 1, lambda: "old")
    with patch("ct_toolkit.utils.cache.time.monotonic", return_value=200.0):
        assert cache.get_or_compute("k", 1, lambda: "new") == "new"


def test_cache_is_lru_bounded():
    cache = GenerationCache(maxsize=2, max_age_s=60)
    for key in ("a", "b", "c"):
        cache.get_or_compute(key, 1, lambda: key)
    assert len(cache) == 2


def test_background_verifier_reports_an_edit_without_a_poll(tmp_path):
    path = tmp_path / "v.db"
    log = ProvenanceLog(vault_path=str(path))
    log.record("q", "a", divergence_score=0.1, metadata={"tier": "ok"})
    log.record("q", "b", divergence_score=0.2, metadata={"tier": "ok"})
    with patch.object(provenance_log, "FULL_VERIFY_INTERVAL_S", 0.0):
        log.start_verifier(interval_s=0.02)
        try:
            deadline = time.monotonic() + 3
            while log.integrity_status() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert log.integrity_status()["chain_ok"] is True
            with sqlite3.connect(str(path)) as conn:
                conn.execute("UPDATE provenance SET divergence_score = 0.9 WHERE divergence_score = 0.1")
                conn.commit()
            deadline = time.monotonic() + 3
            while log.integrity_status()["chain_ok"] and time.monotonic() < deadline:
                time.sleep(0.02)
            assert log.integrity_status()["chain_ok"] is False
        finally:
            log.stop_verifier()


def test_tool_call_api_serves_repeat_reads_from_the_cache(tmp_path):
    import ct_toolkit.server as server

    path = tmp_path / "v.db"
    configure_dashboard_vault(str(path), "local")
    server._dashboard_log_cache = None
    log = server._dashboard_log()
    _tool(log)
    server._read_cache.clear()
    client = TestClient(app)
    # The generation is re-read at most once per interval; zero it so the write below shows at once.
    with patch.object(server, "GENERATION_CHECK_INTERVAL_S", 0.0), patch.object(server, "query_page", wraps=server.query_page) as spy:
        assert client.get("/api/dashboard/tool-calls").status_code == 200
        assert client.get("/api/dashboard/tool-calls").status_code == 200
        assert spy.call_count == 1  # second read: no write in between, served from the cache
        _tool(log, verdict="blocked")
        data = client.get("/api/dashboard/tool-calls").json()
        assert spy.call_count == 2  # a write moved the generation
        assert data["total"] == 2
    log.stop_verifier()


def test_entries_without_tier_or_score_are_not_counted_as_ok(tmp_path):
    import ct_toolkit.server as server

    path = tmp_path / "t.db"
    configure_dashboard_vault(str(path), "local")
    server._dashboard_log_cache = None
    log = server._dashboard_log()
    log.record("q", "a", divergence_score=0.1, metadata={"tier": "ok"})
    log.record("q", "b", metadata={"note": "no tier and no score"})
    data = server.get_dashboard_telemetry(limit=10)
    assert data["tier_counts"]["ok"] == 1
    assert data["entries_analyzed"] == 2  # still listed as an entry analyzed
    log.stop_verifier()


def test_full_verify_interval_option_is_on_serve_and_dashboard():
    # Read the option from the command definition: help output depends on the terminal width and colour.
    from typer.main import get_command

    from ct_toolkit.cli import app

    group = get_command(app)
    for command in ("serve", "dashboard"):
        options = {opt for param in group.commands[command].params for opt in param.opts}
        assert "--full-verify-interval" in options


def test_generation_is_read_at_most_once_per_interval(tmp_path):
    import ct_toolkit.server as server

    log = ProvenanceLog(vault_path=str(tmp_path / "g.db"))
    with patch.object(server, "GENERATION_CHECK_INTERVAL_S", 60.0):
        server._generation_seen.pop(id(log), None)
        with patch.object(log, "generation", return_value=7) as read:
            assert server._generation(log) == 7
            assert server._generation(log) == 7
            assert read.call_count == 1
