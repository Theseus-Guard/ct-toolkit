"""Tests for TASK-5: Core dependency slimming and optional dependency isolation."""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import tomllib
from unittest.mock import MagicMock

from ct_toolkit.core.kernel import ConstitutionalKernel


def test_pyproject_core_dependencies_slimmed() -> None:
    """Verifies heavy server/CLI/observability packages are moved out of core dependencies."""
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    core_deps = [dep.split(">=")[0].split("[")[0].strip().lower() for dep in data["project"]["dependencies"]]

    for moved_pkg in ("instructor", "fastapi", "uvicorn", "typer", "rich", "pillow", "langsmith"):
        assert moved_pkg not in core_deps, f"{moved_pkg} should be in optional-dependencies, not core dependencies"

    opt_deps = data["project"]["optional-dependencies"]
    assert "server" in opt_deps
    assert "cli" in opt_deps
    assert "observability" in opt_deps
    assert "structured" in opt_deps
    assert "all" in opt_deps


def test_core_toolkit_works_without_optional_packages(monkeypatch, tmp_path: Path) -> None:
    """Verifies core ct_toolkit modules initialize and run when optional packages are absent."""
    for mod_name in ("instructor", "fastapi", "uvicorn", "typer", "rich", "PIL", "langsmith"):
        monkeypatch.setitem(sys.modules, mod_name, None)

    import ct_toolkit.divergence.l3_icm as l3_icm

    importlib.reload(l3_icm)
    assert l3_icm.instructor is None

    kernel = ConstitutionalKernel.default()
    runner = l3_icm.ICMRunner(
        client=MagicMock(),
        provider="openai",
        kernel=kernel,
        template="general",
    )
    assert runner._instructor_client is None

    from ct_toolkit import TheseusWrapper, WrapperConfig

    cfg = WrapperConfig(
        vault_path=str(tmp_path / "minimal_core.db"),
        log_requests=True,
    )
    wrapper = TheseusWrapper(client=MagicMock(), provider="openai", config=cfg)
    assert wrapper.kernel is not None
