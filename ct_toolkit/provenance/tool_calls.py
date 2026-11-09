"""
ct_toolkit.provenance.tool_calls
--------------------------------
Reads the tool-call decisions the guard wrote to the provenance chain (metadata `event="tool_call"`) for the
CLI and the Web UI. Filtering, sorting and paging run in the database, and the counts come from pre-computed
counters, so a page costs the same however long the chain is.

The chain stores hashes of the call text, not the text, so a row carries the tool name, verdict, findings
and judge answer, but not the call's arguments.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional

from ct_toolkit.provenance.log import TOOL_CALL_EVENT, TOOL_CALL_SORT, ProvenanceEntry, ProvenanceLog

SORT_KEYS = tuple(TOOL_CALL_SORT)
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


def rows_from_entries(entries: list[ProvenanceEntry]) -> list[ToolCallRow]:
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


def check_query(sort: str, order: str, verdict: Optional[str]) -> None:
    """Raises ValueError for a sort key, order or verdict the listing does not know."""
    if sort not in SORT_KEYS:
        raise ValueError(f"sort must be one of {', '.join(SORT_KEYS)}")
    if order not in ("asc", "desc"):
        raise ValueError("order must be asc or desc")
    if verdict is not None and verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")


def summary_from_counters(counters: Mapping[str, float]) -> dict[str, Any]:
    """Counts by verdict and by tool over all tool-call decisions, read from the pre-computed counters."""
    by_tool = {
        name[len("tool:tool:") :]: int(value)
        for name, value in counters.items()
        if name.startswith("tool:tool:") and value > 0
    }
    return {
        "total": int(counters.get("tool:total", 0)),
        "by_verdict": {verdict: int(counters.get(f"tool:verdict:{verdict}", 0)) for verdict in VERDICTS},
        "by_tool": dict(sorted(by_tool.items(), key=lambda item: (-item[1], item[0]))),
    }


def query_page(
    log: ProvenanceLog,
    *,
    verdict: Optional[str] = None,
    tool: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "time",
    order: str = "desc",
    page: int = 1,
    page_size: int = 10,
) -> ToolCallPage:
    """One page of tool-call decisions. Raises ValueError for an unknown sort, order or verdict."""
    check_query(sort, order, verdict)
    page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
    entries, total, page, pages = log.tool_call_page(
        verdict=verdict,
        tool=tool,
        search=search,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )
    return ToolCallPage(
        items=rows_from_entries(entries),
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
        summary=summary_from_counters(log.counters()),
    )
