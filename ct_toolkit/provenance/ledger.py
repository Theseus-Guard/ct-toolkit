"""
ct_toolkit.provenance.ledger
----------------------------
What the dashboard shows of the Immutable Provenance Chain: every active entry of the chain (divergence samples,
tool-call decisions, model turns and API calls), and the staged-rule records. Both are paged, sorted and filtered
here, so the dashboard and the CLI read the same rows.

The ledger is read in the database. Staged rules are few and their status depends on the clock, so they are
filtered in memory.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from ct_toolkit.provenance.log import (
    API_CALL_EVENT,
    LEDGER_SORT,
    MODEL_TURN_EVENT,
    TOOL_CALL_EVENT,
    ProvenanceEntry,
    ProvenanceLog,
    resolve_tier,
)

DIVERGENCE_KIND = "divergence"
LEDGER_KINDS = (DIVERGENCE_KIND, TOOL_CALL_EVENT, MODEL_TURN_EVENT, API_CALL_EVENT)
STAGED_STATUSES = ("PROMOTED", "ACTIVE_COOLDOWN", "READY_FOR_PROMOTION")
STAGED_SORT = ("status", "rule", "commitment", "remaining")
MAX_PAGE_SIZE = 200


def kind_of(metadata: Mapping[str, Any]) -> str:
    """The kind of an entry: its event, or "divergence" for an entry without one."""
    event = metadata.get("event")
    return DIVERGENCE_KIND if event is None else str(event)


@dataclass(frozen=True)
class LedgerRow:
    id: str
    timestamp: float
    kind: str
    detail: str
    endpoint: str
    model: str
    divergence_score: Optional[float]
    tier: Optional[str]
    prev_entry_hash: str
    content_hash: str
    hmac_signature: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "kind": self.kind,
            "detail": self.detail,
            "endpoint": self.endpoint,
            "model": self.model,
            "divergence_score": self.divergence_score,
            "tier": self.tier,
            "prev_entry_hash": self.prev_entry_hash,
            "content_hash": self.content_hash,
            "hmac_signature": self.hmac_signature,
        }


def row_from_entry(entry: ProvenanceEntry) -> LedgerRow:
    """One ledger row. Only a divergence sample has a tier; other kinds show none, so they are never counted as OK."""
    meta = entry.metadata or {}
    kind = kind_of(meta)
    endpoint = str(meta.get("endpoint") or "")
    if kind == TOOL_CALL_EVENT:
        detail, model, tier = f"{meta.get('tool', '')} · {meta.get('verdict', '')}", str(meta.get("llm_model") or ""), None
    elif kind == MODEL_TURN_EVENT:
        finish = meta.get("finish_reason") or "—"
        detail = f"{meta.get('status', '')} · finish {finish} · {meta.get('tool_calls', 0)} tool call(s)"
        model, tier = str(meta.get("llm_model") or ""), None
    elif kind == API_CALL_EVENT:
        detail, model, tier = f"{meta.get('method', '')} · {meta.get('status', '')}", "", None
    else:
        detail = ""
        model = str(meta.get("model") or "")
        recorded = str(meta.get("tier") or meta.get("divergence_tier") or "").lower()
        tier = resolve_tier(meta, entry.divergence_score) if recorded or entry.divergence_score is not None else None
    return LedgerRow(
        id=entry.id,
        timestamp=entry.timestamp,
        kind=kind,
        detail=detail,
        endpoint=endpoint,
        model=model,
        divergence_score=entry.divergence_score,
        tier=tier,
        prev_entry_hash=entry.prev_entry_hash,
        content_hash=entry.content_hash(),
        hmac_signature=entry.hmac_signature,
    )


@dataclass(frozen=True)
class Page:
    items: List[Dict[str, Any]]
    total: int
    page: int
    page_size: int
    pages: int

    def to_dict(self) -> Dict[str, Any]:
        return {"items": self.items, "total": self.total, "page": self.page, "page_size": self.page_size, "pages": self.pages}


def check_ledger_query(kind: Optional[str], sort: str, order: str) -> None:
    """Raises ValueError for a kind, sort key or order the ledger does not know."""
    if kind is not None and kind not in LEDGER_KINDS:
        raise ValueError(f"kind must be one of {', '.join(LEDGER_KINDS)}")
    if sort not in LEDGER_SORT:
        raise ValueError(f"sort must be one of {', '.join(LEDGER_SORT)}")
    if order not in ("asc", "desc"):
        raise ValueError("order must be asc or desc")


def ledger_page(
    log: ProvenanceLog,
    *,
    kind: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "time",
    order: str = "desc",
    page: int = 1,
    page_size: int = 10,
) -> Page:
    """One page of the ledger. Raises ValueError for an unknown kind, sort key or order."""
    check_ledger_query(kind, sort, order)
    page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
    entries, total, page, pages = log.activity_page(
        kind=kind,
        search=search,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )
    return Page([row_from_entry(e).to_dict() for e in entries], total, page, page_size, pages)


def staged_rules(staged_records: List[Dict[str, Any]], promoted_ids: set, now: float) -> List[Dict[str, Any]]:
    """Each staged rule with its status at `now`: promoted, cooling down, or ready for promotion."""
    rules = []
    for meta in staged_records:
        eid = str(meta.get("endorsement_id", "unknown"))
        cooldown_until = meta.get("cooldown_until")
        if eid in promoted_ids:
            status, remaining = "PROMOTED", 0
        elif isinstance(cooldown_until, (int, float)) and now < cooldown_until:
            status, remaining = "ACTIVE_COOLDOWN", int(cooldown_until - now)
        else:
            status, remaining = "READY_FOR_PROMOTION", 0
        rules.append(
            {
                "endorsement_id": eid,
                "commitment_id": str(meta.get("commitment_id", "unknown")),
                "rule_text": str(meta.get("rule_text", "")),
                "status": status,
                "remaining_seconds": remaining,
            }
        )
    return rules


def staged_page(
    rules: List[Dict[str, Any]],
    *,
    status: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "status",
    order: str = "asc",
    page: int = 1,
    page_size: int = 10,
) -> Page:
    """One page of staged rules, filtered by status and a search over the rule text and commitment, then sorted."""
    if status is not None and status not in STAGED_STATUSES:
        raise ValueError(f"status must be one of {', '.join(STAGED_STATUSES)}")
    if sort not in STAGED_SORT:
        raise ValueError(f"sort must be one of {', '.join(STAGED_SORT)}")
    if order not in ("asc", "desc"):
        raise ValueError("order must be asc or desc")
    page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
    needle = (search or "").lower()
    matching = [
        r for r in rules
        if (status is None or r["status"] == status)
        and (not needle or needle in r["rule_text"].lower() or needle in r["commitment_id"].lower())
    ]
    keys = {
        "status": lambda r: (STAGED_STATUSES.index(r["status"]), r["endorsement_id"]),
        "rule": lambda r: (r["rule_text"].lower(), r["endorsement_id"]),
        "commitment": lambda r: (r["commitment_id"].lower(), r["endorsement_id"]),
        "remaining": lambda r: (r["remaining_seconds"], r["endorsement_id"]),
    }
    matching.sort(key=keys[sort], reverse=order == "desc")
    total = len(matching)
    pages = max(1, -(-total // page_size))
    page = min(max(1, page), pages)
    start = (page - 1) * page_size
    return Page(matching[start : start + page_size], total, page, page_size, pages)
