
from typer.testing import CliRunner

from ct_toolkit.cli import app
from ct_toolkit.provenance.log import ProvenanceLog

WIDE = {"COLUMNS": "200"}  # Rich wraps tables to the terminal width; keep the tests on a wide one


def _vault(tmp_path):
    path = tmp_path / "vault.db"
    log = ProvenanceLog(vault_path=str(path))
    decisions = [
        ("bash", "passed", ""),
        ("shell_exec", "blocked", "secrets.credential_file (high)"),
        ("bash", "flagged", "harness.env_dump (medium)"),
    ]
    for tool, verdict, findings in decisions:
        log.record(
            request_text="run the tests",
            response_text=f"{tool}(...)",
            metadata={"event": "tool_call", "tool": tool, "verdict": verdict, "mode": "observe", "findings": findings, "judge": "", "provider": "openai", "llm_model": "gpt-4o-mini"},
        )
    # A divergence entry must never show up in tool-call listings.
    log.record(request_text="hi", response_text="hello", metadata={"template": "general", "kernel": "default", "model": "m"})
    return path


def test_list_shows_only_tool_call_rows_and_filters_by_verdict(tmp_path):
    result = CliRunner().invoke(app, ["tool-guard", "list", "--vault", str(_vault(tmp_path)), "--verdict", "blocked"], env=WIDE)
    assert result.exit_code == 0
    assert "shell_exec" in result.output and "1 match" in result.output


def test_list_sorts_by_tool_ascending(tmp_path):
    result = CliRunner().invoke(app, ["tool-guard", "list", "--vault", str(_vault(tmp_path)), "--sort", "tool", "--order", "asc"], env=WIDE)
    assert result.exit_code == 0
    assert result.output.index("bash") < result.output.index("shell_exec")
    assert "3 match" in result.output


def test_list_rejects_unknown_sort_key(tmp_path):
    result = CliRunner().invoke(app, ["tool-guard", "list", "--vault", str(_vault(tmp_path)), "--sort", "bogus"])
    assert result.exit_code == 2


def test_stats_counts_by_verdict_and_tool(tmp_path):
    result = CliRunner().invoke(app, ["tool-guard", "stats", "--vault", str(_vault(tmp_path))])
    assert result.exit_code == 0
    assert "3 decision(s) in total." in result.output


def test_show_takes_a_unique_prefix(tmp_path):
    vault = _vault(tmp_path)
    rows = CliRunner().invoke(app, ["tool-guard", "list", "--vault", str(vault), "--verdict", "blocked"])
    assert rows.exit_code == 0
    entry_id = ProvenanceLog(vault_path=str(vault)).tool_call_page(verdict="blocked")[0][0].id
    result = CliRunner().invoke(app, ["tool-guard", "show", entry_id[:12], "--vault", str(vault)])
    assert result.exit_code == 0
    assert "shell_exec" in result.output and "blocked" in result.output
    assert "Arguments are not stored" in result.output


def test_missing_vault_is_an_error(tmp_path):
    result = CliRunner().invoke(app, ["tool-guard", "stats", "--vault", str(tmp_path / "nope.db")])
    assert result.exit_code == 1
