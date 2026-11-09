"""Test modules that need an optional package are skipped when that package is missing.

The core install has no CLI extras, no web layer, no ML and no framework middleware. A test module that needs one
of them is left out of collection instead of failing the run.
"""

import importlib.util

# Optional package -> test modules (relative to tests/) that import it.
_NEEDS = {
    "typer": [
        "test_cli.py",
        "test_cli_extended.py",
        "test_distributed_headers_and_cli.py",
        "unit/test_cli_extensions.py",
        "unit/test_cli_tool_guard_records.py",
    ],
    "fastapi": [
        "test_distributed_headers_and_cli.py",
        "unit/test_dashboard_reads.py",
        "unit/test_dashboard_tool_calls.py",
    ],
    "starlette": ["unit/test_proxy.py"],
    "torch": ["test_loss.py"],
    "langchain_core": [
        "test_deep_agents_integration.py",
        "test_summarization_drift.py",
        "unit/test_middleware_langchain.py",
        "unit/test_middleware_litellm.py",
    ],
}

collect_ignore = [
    path for package, paths in _NEEDS.items() if importlib.util.find_spec(package) is None for path in paths
]
