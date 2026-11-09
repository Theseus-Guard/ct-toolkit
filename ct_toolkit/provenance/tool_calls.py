"""
ct_toolkit.provenance.tool_calls
--------------------------------
Reads the tool-call decisions the guard wrote to the provenance chain (metadata `event="tool_call"`) and
turns them into rows: listed, filtered, sorted and paged. The CLI and the Web UI share this module, so both
show the same rows for the same query.

The chain stores hashes of the call text, not the text, so a row carries the tool name, verdict, findings
and judge answer, but not the call's arguments.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional

from ct_toolkit.provenance.log import ProvenanceEntry

TOOL_CALL_EVENT = "tool_call"
SORT_KEYS = ("time", "tool", "verdict", "mode")
VERDICTS = ("passed", "flagged", "blocked")
MAX_PAGE_SIZE = 200


@dataclass(frozen=True)
class ToolCallRow:
    id: str
    timestamp: float
    tool: str
    verdict: str
    mode: str
    findings: str
    judge: str
    provider: str
    llm_model: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolCallPage:
    items: list[ToolCallRow]
    total: int
    page: int
    page_size: int
    pages: int
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [row.to_dict() for row in self.items],
            "total": self.total,
            "page": self.page,
            "page_size": self.page_size,
            "pages": self.pages,
            "summary": self.summary,
        }


def rows_from_entries(entries: Iterable[ProvenanceEntry]) -> list[ToolCallRow]:
    """One row per tool-call entry. Entries of other kinds are skipped."""
    rows = []
    for entry in entries:
        meta = entry.metadata or {}
        if meta.get("event") != TOOL_CALL_EVENT:
            continue
        rows.append(
            ToolCallRow(
                id=entry.id,
                timestamp=entry.timestamp,
                tool=str(meta.get("tool", "")),
                verdict=str(meta.get("verdict", "")),
                mode=str(meta.get("mode", "")),
                findings=str(meta.get("findings", "")),
                judge=str(meta.get("judge", "")),
                provider=str(meta.get("provider", "")),
                llm_model=str(meta.get("llm_model", "")),
            )
        )
    return rows


def summarize(rows: Iterable[ToolCallRow]) -> dict[str, Any]:
    """Counts by verdict and by tool, over the rows given (not only the page shown)."""
    rows = list(rows)
    by_verdict = Counter(row.verdict for row in rows)
    by_tool = Counter(row.tool for row in rows)
    return {
        "total": len(rows),
        "by_verdict": {verdict: by_verdict.get(verdict, 0) for verdict in VERDICTS},
        "by_tool": dict(by_tool.most_common()),
    }


def query_rows(
    rows: Iterable[ToolCallRow],
    *,
    verdict: Optional[str] = None,
    tool: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "time",
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
) -> ToolCallPage:
    """Filters, sorts and pages the rows.

    verdict: exact match (passed, flagged or blocked); tool: case-insensitive substring of the tool name;
    search: case-insensitive substring of tool, findings, judge or model. Unknown sort keys and orders raise
    ValueError, so callers can report them as bad input.
    """
    if sort not in SORT_KEYS:
        raise ValueError(f"sort must be one of {', '.join(SORT_KEYS)}")
    if order not in ("asc", "desc"):
        raise ValueError("order must be asc or desc")
    if verdict is not None and verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
    page = max(1, int(page))

    rows = list(rows)
    summary = summarize(rows)
    selected = rows
    if verdict is not None:
        selected = [row for row in selected if row.verdict == verdict]
    if tool:
        needle = tool.lower()
        selected = [row for row in selected if needle in row.tool.lower()]
    if search:
        needle = search.lower()
        selected = [
            row
            for row in selected
            if any(needle in text.lower() for text in (row.tool, row.findings, row.judge, row.llm_model))
        ]

    key = {
        "time": lambda row: row.timestamp,
        "tool": lambda row: row.tool.lower(),
        "verdict": lambda row: VERDICTS.index(row.verdict) if row.verdict in VERDICTS else len(VERDICTS),
        "mode": lambda row: row.mode,
    }[sort]
    selected = sorted(selected, key=key, reverse=(order == "desc"))

    total = len(selected)
    pages = max(1, -(-total // page_size))
    page = min(page, pages)  # a page past the end shows the last page, with its rows
    start = (page - 1) * page_size
    return ToolCallPage(
        items=selected[start : start + page_size],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
        summary=summary,
    )
