import re

from typer.testing import CliRunner

from ct_toolkit.cli import app
from tests.unit.test_tool_guard import OPENAI_TOOLS, openai_response
from tests.unit.test_trajectory_scrub import KEY


class TestCli:
    def _write(self, tmp_path, response, name="saved.json"):
        import json

        path = tmp_path / name
        path.write_text(json.dumps({"tools": OPENAI_TOOLS, "message": "run tests", "response": response}))
        return str(path)

    def test_check_exits_1_on_high_severity_and_0_when_clean(self, tmp_path):
        from typer.testing import CliRunner

        from ct_toolkit.cli import app

        runner = CliRunner()
        bad = self._write(tmp_path, openai_response(("shell_exec", "{}")), "bad.json")
        clean = self._write(tmp_path, openai_response(("bash", '{"command": "pytest -q"}')), "clean.json")
        assert runner.invoke(app, ["tool-guard", "check", bad]).exit_code == 1
        assert runner.invoke(app, ["tool-guard", "check", clean]).exit_code == 0

    def test_check_rejects_unknown_mode(self, tmp_path):
        from typer.testing import CliRunner

        from ct_toolkit.cli import app

        path = self._write(tmp_path, openai_response(("bash", "{}")))
        result = CliRunner().invoke(app, ["tool-guard", "check", path, "--mode", "bogus"])
        assert result.exit_code == 2
        # CI forces colour, which splits option names with escape codes; compare the plain text.
        assert "--mode" in re.sub(r"\x1b\[[0-9;]*m", "", result.output)


class TestScrubCli:
    def test_dry_run_reports_without_writing(self, tmp_path):
        run = tmp_path / "run"
        run.mkdir()
        (run / "log.txt").write_text(f"key {KEY}\n", encoding="utf-8")
        result = CliRunner().invoke(app, ["scrub", str(run), "--no-env"])
        assert result.exit_code == 0
        assert "credential" in result.output
        assert "Dry run" in result.output
        assert (run / "log.txt").read_text() == f"key {KEY}\n"
