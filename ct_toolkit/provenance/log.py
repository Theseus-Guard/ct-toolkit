"""
ct_toolkit.provenance.log
--------------------------
Provenance Log cryptographically signed with HMAC hash chain.

Each entry:
  - References the hash of the previous entry (chain integrity)
  - Signs content with HMAC-SHA256 (tamper detection)
  - Written persistently to SQLite or SQLAlchemy/PostgreSQL

Chain verification: verify_chain() checks the log incrementally in batches.
"""
from __future__ import annotations

import contextlib
import json
import threading
import time
import uuid
import hmac
import hashlib
import sqlite3
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterator, Optional

from ct_toolkit.core.exceptions import ChainIntegrityError, VaultError
from ct_toolkit.utils.logger import get_logger
from ct_toolkit.utils.sensitive_masker import SensitiveDataMasker, LogSanitizer

logger = get_logger(__name__)

# HMAC secret key stored in user's vault.
# In a real app this would be read from a vault adapter.
_DEFAULT_KEY_ENV = "CT_HMAC_SECRET"

_SQLALCHEMY_URL_PREFIXES = (
    "postgresql://",
    "postgres://",
    "postgresql+",
    "sqlite:///",
    "mysql://",
    "mysql+",
)


TOOL_CALL_EVENT = "tool_call"
# A model response without tool calls, recorded by the proxy. Like tool calls, it has no divergence score.
MODEL_TURN_EVENT = "model_turn"
# A request to the model API that is not a model turn (a model listing, a token count), recorded by the proxy.
API_CALL_EVENT = "api_call"
# Columns copied out of metadata at write time, so queries can filter and sort without parsing JSON.
DERIVED_COLUMNS = ("event", "verdict", "tool", "mode", "event_type")
STAGED_EVENT_TYPES = ("staged_promotion", "reflective_endorsement")
DIVERGENCE_TIERS = ("ok", "l1_warning", "l2_judge", "l3_icm")
ENTRY_COLUMNS = (
    "id, timestamp, request_hash, response_hash, divergence_score, metadata, prev_entry_hash, hmac_signature, status"
)
INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_provenance_status_ts ON provenance (status, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_provenance_event_ts ON provenance (event, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_provenance_event_verdict ON provenance (event, verdict)",
    "CREATE INDEX IF NOT EXISTS idx_provenance_eventtype_ts ON provenance (event_type, timestamp)",
)
# Sort keys for tool-call listings. Keep in step with ct_toolkit.provenance.tool_calls.SORT_KEYS.
TOOL_CALL_SORT = {
    "time": "timestamp",
    "tool": "LOWER(tool)",
    "verdict": "CASE verdict WHEN 'passed' THEN 0 WHEN 'flagged' THEN 1 WHEN 'blocked' THEN 2 ELSE 3 END",
    "mode": "mode",
}
# Sort keys of the ledger: the columns of the chain that the ledger shows, in the database.
LEDGER_SORT = {
    "time": "timestamp",
    "kind": "COALESCE(event, 'divergence')",
    "score": "divergence_score",
}
FULL_VERIFY_INTERVAL_S = 300.0
GENERATION_COUNTER = "meta:generation"  # bumped by every write and rollback: a cache key for readers
DERIVED_MARKER = "meta:derived"
# Set once the `event_type` column is filled for old entries. 0.5.0 filled the other derived columns but not this one.
EVENT_TYPE_MARKER = "meta:event_type"
_ADD_COUNTER = (
    "INSERT INTO provenance_counters (name, value) VALUES (:name, :value) "
    "ON CONFLICT (name) DO UPDATE SET value = provenance_counters.value + excluded.value"
)
_INSERT_ENTRY = (
    "INSERT INTO provenance (id, timestamp, request_hash, response_hash, divergence_score, metadata, "
    "prev_entry_hash, hmac_signature, status, event, verdict, tool, mode, event_type) VALUES (:id, :timestamp, "
    ":request_hash, :response_hash, :divergence_score, :metadata, :prev_entry_hash, :hmac_signature, "
    ":status, :event, :verdict, :tool, :mode, :event_type)"
)


def resolve_tier(metadata: dict[str, Any], divergence_score: float | None) -> str:
    """The divergence tier of an entry: its recorded tier, else the tier its score falls in."""
    recorded = str(metadata.get("tier") or metadata.get("divergence_tier") or "").lower()
    if recorded in DIVERGENCE_TIERS:
        return recorded
    if divergence_score is None:
        return "ok"
    if divergence_score >= 0.50:
        return "l3_icm"
    if divergence_score >= 0.30:
        return "l2_judge"
    if divergence_score >= 0.15:
        return "l1_warning"
    return "ok"


def _escape_like(text: str) -> str:
    """Escapes a user string for LIKE ... ESCAPE '!', lower-cased for case-insensitive matching."""
    return text.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")


@dataclass
class ProvenanceEntry:
    """A single log entry."""
    id: str
    timestamp: float
    request_hash: str
    response_hash: str
    divergence_score: float | None
    metadata: dict[str, Any]
    prev_entry_hash: str          # Content hash of the previous entry
    status: str = field(default="active") # "active" or "rolled_back"
    hmac_signature: str = field(default="", repr=False)

    def content_hash(self) -> str:
        """Content hash of this entry (excluding HMAC)."""
        payload = json.dumps(
            {
                "id": self.id,
                "timestamp": self.timestamp,
                "request_hash": self.request_hash,
                "response_hash": self.response_hash,
                "divergence_score": self.divergence_score,
                "metadata": self.metadata,
                "prev_entry_hash": self.prev_entry_hash,
                "status": self.status,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class ProvenanceLog:
    """
    HMAC hash chain Provenance Log supporting local SQLite and SQLAlchemy/PostgreSQL.

    Usage:
        # Local SQLite
        log = ProvenanceLog(vault_type="local", vault_path="./ct.db")

        # Enterprise PostgreSQL / SQLAlchemy URL
        log = ProvenanceLog(vault_type="postgres", vault_path="postgresql://user:pass@host/db")

        entry_id = log.record(request_text="...", response_text="...", divergence_score=0.05)
        log.verify_chain(batch_size=500)  # Paginated verification
    """

    GENESIS_HASH = "0" * 64  # prev_hash of the first entry
    
    # Maximum sizes for request/response text to prevent log bloat
    MAX_REQUEST_LOG_LENGTH = 2000
    MAX_RESPONSE_LOG_LENGTH = 4000

    def __init__(
        self,
        vault_type: str = "local",
        vault_path: str = "./ct_provenance.db",
        hmac_key: bytes | None = None,
        mask_sensitive_data: bool = True,
    ) -> None:
        self._vault_type = vault_type.lower()
        self._raw_vault_path = str(vault_path)
        self._use_sqlalchemy = (
            self._vault_type in ("postgres", "postgresql", "sqlalchemy", "db")
            or self._raw_vault_path.startswith(_SQLALCHEMY_URL_PREFIXES)
        )

        if self._use_sqlalchemy:
            self._db_url = self._normalize_db_url(self._raw_vault_path)
            # For key file fallback when using a URL, use current working directory
            self._vault_path = Path("./ct_provenance.db")
        else:
            self._db_url = ""
            self._vault_path = Path(vault_path)

        self._hmac_key = hmac_key or self._load_or_generate_key()
        self._lock = threading.RLock()
        
        # Sensitive data masking
        self._mask_sensitive_data = mask_sensitive_data
        self._masker = SensitiveDataMasker(mask_pii=True) if mask_sensitive_data else None
        self._log_sanitizer = LogSanitizer()

        self._engine: Any = None
        self._conn: sqlite3.Connection | None = None

        if self._use_sqlalchemy:
            self._engine = self._init_sqlalchemy_db()
        else:
            self._conn = self._init_db()
        self._verified_tip: dict[str, Any] | None = None
        self._verified_at = 0.0
        self._chain_tip: tuple[str, str] | None = None  # (id, content hash) of the newest entry this process wrote
        # Seconds between full verification passes. None uses FULL_VERIFY_INTERVAL_S.
        self.full_verify_interval_s: float | None = None
        self._verify_lock = threading.RLock()
        self._status_lock = threading.Lock()
        self._integrity: dict[str, Any] | None = None
        self._verifier: threading.Thread | None = None
        self._verifier_stop = threading.Event()
        self._migrate()

        logger.info(
            f"ProvenanceLog initialized | vault={vault_path} | "
            f"backend={'sqlalchemy' if self._use_sqlalchemy else 'sqlite3'} | "
            f"masking={mask_sensitive_data}"
        )

    @staticmethod
    def _normalize_db_url(vault_path: str) -> str:
        if vault_path.startswith("postgres://"):
            return "postgresql://" + vault_path[len("postgres://"):]
        if not vault_path.startswith(_SQLALCHEMY_URL_PREFIXES):
            # File path passed with vault_type="sqlalchemy"
            resolved = Path(vault_path).resolve()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{resolved}"
        return vault_path

    # ── Record ──────────────────────────────────────────────────────────────────

    def record(
        self,
        request_text: str,
        response_text: str,
        divergence_score: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Creates a new entry, signs with HMAC, and writes to database.
        Returns: entry ID
        """
        with self._lock:
            for _ in range(2):
                # The cached tip is trusted only for PostgreSQL, where the write itself checks it. SQLite reads it.
                tip_id, prev_hash = self._chain_tip if (self._chain_tip and self._dialect() != "sqlite") else self._read_chain_tip()

                # Mask sensitive data in metadata before storing
                safe_metadata = self._sanitize_metadata(metadata or {})

                # Sanitize and truncate text for logging
                safe_request = self._sanitize_text_for_log(request_text, self.MAX_REQUEST_LOG_LENGTH)
                safe_response = self._sanitize_text_for_log(response_text, self.MAX_RESPONSE_LOG_LENGTH)

                entry = ProvenanceEntry(
                    id=str(uuid.uuid4()),
                    timestamp=time.time(),
                    request_hash=self._hash_text(safe_request),
                    response_hash=self._hash_text(safe_response),
                    divergence_score=divergence_score,
                    metadata=safe_metadata,
                    prev_entry_hash=prev_hash,
                    status="active",
                )

                content_hash = entry.content_hash()
                entry.hmac_signature = self._compute_hmac(content_hash)

                if self._write_entry(entry, expected_tip_id=tip_id):
                    self._chain_tip = (entry.id, content_hash)
                    break
                # Another writer appended after our tip: read it again and sign the entry on top of that.
                self._chain_tip = None
            else:
                raise VaultError("the chain tip kept changing while writing; try again")
        logger.debug(f"Provenance recorded | id={entry.id} | divergence={divergence_score}")
        return entry.id
    
    def _sanitize_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Sanitize metadata dictionary by masking sensitive values."""
        if not self._masker:
            return metadata
        return self._masker.mask_metadata(metadata)
    
    def _sanitize_text_for_log(self, text: str, max_length: int = 4000) -> str:
        """Sanitize text for safe logging."""
        if not text:
            return ""
        
        # First mask sensitive data
        if self._masker:
            text = self._masker.mask_text(text)
        
        # Then sanitize for log injection
        text = self._log_sanitizer.sanitize(text)
        
        # Truncate if too long
        if len(text) > max_length:
            text = text[:max_length] + "...[TRUNCATED]"
        
        return text

    # ── Verification (Paginated & Checkpoint-capable) ────────────────────────────

    def verify_chain(
        self,
        batch_size: int = 500,
        start_offset: int = 0,
        expected_prev_hash: str | None = None,
    ) -> bool:
        """
        Verifies the active chain from start (or checkpoint offset) to finish
        using paginated batches to avoid loading the entire table into memory.
        - Is each entry's HMAC valid?
        - Does each entry correctly reference the previous one?
        - Ignores 'rolled_back' entries.

        Returns: True (valid) | raises ChainIntegrityError
        """
        if batch_size <= 0:
            batch_size = 500

        expected_prev = expected_prev_hash if expected_prev_hash is not None else self.GENESIS_HASH
        offset = max(0, start_offset)
        total_verified = 0

        while True:
            with self._lock:
                batch = self._load_entries_batch(
                    limit=batch_size,
                    offset=offset,
                    include_rolled_back=False,
                )
            if not batch:
                break

            for entry in batch:
                expected_hmac = self._compute_hmac(entry.content_hash())
                if not hmac.compare_digest(entry.hmac_signature, expected_hmac):
                    raise ChainIntegrityError(entry_id=entry.id)

                if entry.prev_entry_hash != expected_prev:
                    raise ChainIntegrityError(entry_id=entry.id)

                expected_prev = entry.content_hash()
                total_verified += 1

            if len(batch) < batch_size:
                break
            offset += len(batch)

        logger.info(f"Chain verified: {total_verified} active entries valid (batch_size={batch_size}).")
        return True

    def get_chain_checkpoint(self, batch_size: int = 2000) -> dict[str, Any]:
        """
        Verifies the chain in batches and returns a checkpoint dictionary
        (`offset`, `last_entry_id`, `last_content_hash`) that can be passed to
        future `verify_chain(start_offset=..., expected_prev_hash=...)` calls.
        """
        if batch_size <= 0:
            batch_size = 500

        expected_prev = self.GENESIS_HASH
        total_verified = 0
        last_entry_id: str | None = None
        last_timestamp: float | None = None
        cursor_ts, cursor_id = -1.0, ""  # keyset position: the batch starts after this entry

        while True:
            with self._lock:
                batch = self._select(
                    "status = 'active' AND (timestamp > :ts OR (timestamp = :ts AND id > :id))",
                    {"ts": cursor_ts, "id": cursor_id},
                    "timestamp ASC, id ASC",
                    batch_size,
                )
            if not batch:
                break

            for entry in batch:
                content = entry.content_hash()  # computed once: it is both the HMAC input and the next link
                expected_hmac = self._compute_hmac(content)
                if not hmac.compare_digest(entry.hmac_signature, expected_hmac):
                    raise ChainIntegrityError(entry_id=entry.id)
                if entry.prev_entry_hash != expected_prev:
                    raise ChainIntegrityError(entry_id=entry.id)
                expected_prev = content
                last_entry_id = entry.id
                last_timestamp = entry.timestamp
                total_verified += 1
            cursor_ts, cursor_id = batch[-1].timestamp, batch[-1].id

            if len(batch) < batch_size:
                break

        return {
            "verified_count": total_verified,
            "next_offset": total_verified,
            "last_entry_id": last_entry_id,
            "last_timestamp": last_timestamp,
            "last_content_hash": expected_prev,
        }

    # ── Derived data: schema, counters and queries ─────────────────────────────

    def _migrate(self) -> None:
        """Adds the derived columns, indexes and counters table, and fills them once for older vaults."""
        with self._lock, self._transaction() as run:
            if self._dialect() == "sqlite":
                existing = {row[1] for row in run("PRAGMA table_info(provenance)")}
                for column in DERIVED_COLUMNS:
                    if column not in existing:
                        run(f"ALTER TABLE provenance ADD COLUMN {column} TEXT")
            else:
                for column in DERIVED_COLUMNS:
                    run(f"ALTER TABLE provenance ADD COLUMN IF NOT EXISTS {column} TEXT")
            run(
                "CREATE TABLE IF NOT EXISTS provenance_counters "
                "(name TEXT PRIMARY KEY, value DOUBLE PRECISION NOT NULL)"
            )
            for ddl in INDEX_DDL:
                run(ddl)
            if not run(f"SELECT name FROM provenance_counters WHERE name = '{DERIVED_MARKER}'"):
                self._backfill_derived(run)
                self._rebuild_counters(run)
                run(_ADD_COUNTER, {"name": DERIVED_MARKER, "value": 1.0})
            if not run(f"SELECT name FROM provenance_counters WHERE name = '{EVENT_TYPE_MARKER}'"):
                self._backfill_event_type(run)
                run(_ADD_COUNTER, {"name": EVENT_TYPE_MARKER, "value": 1.0})

    def _backfill_derived(self, run: Any) -> None:
        for row_id, meta_raw in run("SELECT id, metadata FROM provenance"):
            metadata = json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw)
            run(
                "UPDATE provenance SET event = :event, verdict = :verdict, tool = :tool, mode = :mode, event_type = :event_type "
                "WHERE id = :id",
                {**self._derived(metadata), "id": row_id},
            )

    def _backfill_event_type(self, run: Any) -> None:
        for row_id, meta_raw in run("SELECT id, metadata FROM provenance WHERE event_type IS NULL"):
            metadata = json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw)
            if metadata.get("event_type") is not None:
                run("UPDATE provenance SET event_type = :event_type WHERE id = :id",
                    {"event_type": str(metadata["event_type"]), "id": row_id})

    def _rebuild_counters(self, run: Any) -> None:
        """Recomputes the tool-call counters from the active entries. Used on migration and after a rollback."""
        run(f"DELETE FROM provenance_counters WHERE name NOT IN ('{DERIVED_MARKER}', '{EVENT_TYPE_MARKER}', '{GENERATION_COUNTER}')")
        totals: dict[str, float] = {}
        for (meta_raw,) in run("SELECT metadata FROM provenance WHERE status = 'active' AND event = :event",
                               {"event": TOOL_CALL_EVENT}):
            metadata = json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw)
            for name, delta in self._counter_deltas(metadata, +1):
                totals[name] = totals.get(name, 0.0) + delta
        for name, value in totals.items():
            run(_ADD_COUNTER, {"name": name, "value": value})

    def _select(
        self,
        where: str,
        params: dict[str, Any],
        order: str,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ProvenanceEntry]:
        sql = f"SELECT {ENTRY_COLUMNS} FROM provenance WHERE {where} ORDER BY {order}"
        if limit is not None:
            sql += " LIMIT :limit OFFSET :offset"
            params = {**params, "limit": int(limit), "offset": int(offset)}
        with self._transaction() as run:
            rows = run(sql, params)
        return [self._row_to_entry(tuple(row)) for row in rows]

    def counters(self) -> dict[str, float]:
        """Pre-computed tool-call counters (`tool:*`) and the schema marker (`meta:derived`)."""
        with self._lock, self._transaction() as run:
            return {name: float(value) for name, value in run("SELECT name, value FROM provenance_counters")}

    def generation(self) -> int:
        """Changes after every write or rollback made through this vault. A reader caches results per generation."""
        with self._lock, self._transaction() as run:
            rows = run(f"SELECT value FROM provenance_counters WHERE name = '{GENERATION_COUNTER}'")
        return int(rows[0][0]) if rows else 0

    def recent_entries(self, event: Optional[str] = None, limit: int = 25) -> list[ProvenanceEntry]:
        """Newest active entries first. `event=None` gives the divergence entries (those without an event)."""
        with self._lock:
            if event is None:
                return self._select("status = 'active' AND event IS NULL", {}, "timestamp DESC, id DESC", limit)
            return self._select("status = 'active' AND event = :event", {"event": event}, "timestamp DESC, id DESC", limit)

    def recent_activity(self, limit: int = 25) -> list[ProvenanceEntry]:
        """Newest active entries of every kind: divergence entries, tool-call decisions, model turns and API calls."""
        with self._lock:
            return self._select("status = 'active'", {}, "timestamp DESC, id DESC", limit)

    def recent_staged_entries(self, limit: int = 500) -> list[ProvenanceEntry]:
        """Newest staged-rule entries (promotions and staged endorsements), newest first."""
        with self._lock:
            return self._select(
                "status = 'active' AND event_type IN (:promotion, :endorsement)",
                {"promotion": STAGED_EVENT_TYPES[0], "endorsement": STAGED_EVENT_TYPES[1]},
                "timestamp DESC, id DESC",
                limit,
            )

    def _select_with_total(
        self, where: str, params: dict[str, Any], order: str, limit: int, offset: int, total_sql: str
    ) -> tuple[list[ProvenanceEntry], int | None]:
        """One page of entries and the total, in one statement: `total_sql` is a scalar subquery, evaluated once.
        The total is None when the page has no rows or the subquery has no value."""
        sql = (
            f"SELECT {ENTRY_COLUMNS}, ({total_sql}) AS total FROM provenance WHERE {where} "
            f"ORDER BY {order} LIMIT :limit OFFSET :offset"
        )
        with self._transaction() as run:
            rows = run(sql, {**params, "limit": int(limit), "offset": int(offset)})
        if not rows:
            return [], None
        total = rows[0][9]
        return [self._row_to_entry(tuple(row[:9])) for row in rows], None if total is None else int(total)

    def tool_call_page(
        self,
        *,
        verdict: Optional[str] = None,
        tool: Optional[str] = None,
        search: Optional[str] = None,
        sort: str = "time",
        order: str = "desc",
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[ProvenanceEntry], int, int, int]:
        """(entries, total, page, pages) of tool-call entries, filtered, sorted and paged in the database.
        The caller validates the sort, order, verdict and page size."""
        if sort not in TOOL_CALL_SORT:
            raise ValueError(f"unknown sort key {sort!r}")
        where = ["status = 'active'", "event = :event"]
        params: dict[str, Any] = {"event": TOOL_CALL_EVENT}
        if verdict is not None:
            where.append("verdict = :verdict")
            params["verdict"] = verdict
        if tool:
            where.append("LOWER(tool) LIKE :tool ESCAPE '!'")
            params["tool"] = f"%{_escape_like(tool)}%"
        if search:
            where.append("(LOWER(tool) LIKE :q ESCAPE '!' OR LOWER(metadata) LIKE :q ESCAPE '!')")
            params["q"] = f"%{_escape_like(search)}%"
        clause = " AND ".join(where)
        direction = "ASC" if order == "asc" else "DESC"
        order_by = f"{TOOL_CALL_SORT[sort]} {direction}, timestamp {direction}, id {direction}"
        requested = max(1, page)
        filtered = verdict is not None or bool(tool) or bool(search)
        # Unfiltered, the total is the pre-computed counter; filtered, it is a count over the same filter.
        total_sql = (
            f"SELECT COUNT(*) FROM provenance WHERE {clause}" if filtered
            else "SELECT value FROM provenance_counters WHERE name = 'tool:total'"
        )
        with self._lock:
            entries, total = self._select_with_total(clause, params, order_by, page_size, (requested - 1) * page_size, total_sql)
            if total is None:
                # The page has no rows: it is empty, or it lies past the last page. Count, then clamp.
                with self._transaction() as run:
                    total = int(run(f"SELECT COUNT(*) FROM provenance WHERE {clause}", params)[0][0])
            pages = max(1, -(-total // page_size))
            page = min(requested, pages)
            if page != requested and total > 0:
                entries = self._select(clause, params, order_by, page_size, (page - 1) * page_size)
        return entries, total, page, pages

    def activity_page(
        self,
        *,
        kind: Optional[str] = None,
        search: Optional[str] = None,
        sort: str = "time",
        order: str = "desc",
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[ProvenanceEntry], int, int, int]:
        """(entries, total, page, pages) of active entries, of one kind or all, filtered, sorted and paged in the
        database. `kind` is "divergence" for entries without an event. The caller validates sort, order and kind."""
        if sort not in LEDGER_SORT:
            raise ValueError(f"unknown sort key {sort!r}")
        where = ["status = 'active'"]
        params: dict[str, Any] = {}
        if kind == "divergence":
            where.append("event IS NULL")
        elif kind is not None:
            where.append("event = :event")
            params["event"] = kind
        if search:
            where.append("LOWER(metadata) LIKE :q ESCAPE '!'")
            params["q"] = f"%{_escape_like(search)}%"
        clause = " AND ".join(where)
        direction = "ASC" if order == "asc" else "DESC"
        order_by = f"{LEDGER_SORT[sort]} {direction}, timestamp {direction}, id {direction}"
        with self._lock, self._transaction() as run:
            total = int(run(f"SELECT COUNT(*) FROM provenance WHERE {clause}", params)[0][0])
        pages = max(1, -(-total // page_size))
        page = min(max(1, page), pages)
        entries = self._select(clause, params, order_by, page_size, (page - 1) * page_size) if total else []
        return entries, total, page, pages

    def find_tool_calls(self, id_prefix: str, limit: int = 2) -> list[ProvenanceEntry]:
        """Active tool-call entries whose id starts with `id_prefix`."""
        with self._lock:
            return self._select(
                "status = 'active' AND event = :event AND id LIKE :id ESCAPE '!'",
                {"event": TOOL_CALL_EVENT, "id": f"{_escape_like(id_prefix)}%"},
                "timestamp ASC",
                limit,
            )

    def verify_incremental(self, batch_size: int = 500, force_full: bool = False) -> dict[str, Any]:
        """Verifies the active chain from the last verified position. Only entries after that position are
        checked, so a run costs the new entries, not the whole chain.

        A full pass runs when there is no position yet, after a rollback, when `force_full` is set, or once every
        FULL_VERIFY_INTERVAL_S seconds. The full pass also catches an entry written with a timestamp earlier
        than the position. The chain lock is held per batch only, so writers are not blocked during a pass.
        Raises ChainIntegrityError on a mismatch."""
        with self._verify_lock:
            tip = self._verified_tip
            interval = self.full_verify_interval_s if self.full_verify_interval_s is not None else FULL_VERIFY_INTERVAL_S
            due = force_full or tip is None or (time.monotonic() - self._verified_at) > interval
            mode = "full" if due else "incremental"
            try:
                if due:
                    checkpoint = self.get_chain_checkpoint(batch_size=batch_size)
                    tip = {
                        "timestamp": checkpoint["last_timestamp"] or 0.0,
                        "id": checkpoint["last_entry_id"] or "",
                        "hash": checkpoint["last_content_hash"],
                        "count": checkpoint["verified_count"],
                    }
                    self._verified_at = time.monotonic()
                assert tip is not None
                while True:
                    with self._lock:
                        batch = self._select(
                            "status = 'active' AND (timestamp > :ts OR (timestamp = :ts AND id > :id))",
                            {"ts": tip["timestamp"], "id": tip["id"]},
                            "timestamp ASC, id ASC",
                            batch_size,
                        )
                    for entry in batch:
                        content = entry.content_hash()
                        expected = self._compute_hmac(content)
                        if not hmac.compare_digest(entry.hmac_signature, expected) or entry.prev_entry_hash != tip["hash"]:
                            raise ChainIntegrityError(entry_id=entry.id)
                        tip = {
                            "timestamp": entry.timestamp,
                            "id": entry.id,
                            "hash": content,
                            "count": tip["count"] + 1,
                        }
                    if len(batch) < batch_size:
                        break
            except ChainIntegrityError:
                self._verified_tip = None
                raise
            self._verified_tip = tip
        return {"verified_count": tip["count"], "last_content_hash": tip["hash"], "mode": mode}

    def check_integrity(self) -> dict[str, Any]:
        """Runs one verification and publishes the result for `integrity_status()`. Never raises on tampering."""
        checked_at = time.time()
        try:
            result = self.verify_incremental()
            status = {
                "chain_ok": True,
                "tampered_entry_id": None,
                "verified_count": int(result["verified_count"]),
                "last_hash": str(result["last_content_hash"]),
                "mode": result["mode"],
                "checked_at": checked_at,
            }
        except ChainIntegrityError as exc:
            status = {
                "chain_ok": False,
                "tampered_entry_id": getattr(exc, "entry_id", str(exc)),
                "verified_count": 0,
                "last_hash": self.GENESIS_HASH,
                "mode": "failed",
                "checked_at": checked_at,
            }
        with self._status_lock:
            self._integrity = status
        return dict(status)

    def integrity_status(self) -> Optional[dict[str, Any]]:
        """The last published verification result, or None before the first check. Reads no rows."""
        with self._status_lock:
            return dict(self._integrity) if self._integrity is not None else None

    def start_verifier(self, interval_s: float = 5.0) -> None:
        """Runs `check_integrity()` in a daemon thread every `interval_s` seconds: cheap incremental checks, with
        a full pass every FULL_VERIFY_INTERVAL_S. Calling it again while the thread runs does nothing."""
        with self._status_lock:
            if self._verifier is not None and self._verifier.is_alive():
                return
            self._verifier_stop.clear()
            self._verifier = threading.Thread(
                target=self._verifier_loop, args=(interval_s,), name="ct-provenance-verifier", daemon=True
            )
            self._verifier.start()

    def stop_verifier(self) -> None:
        self._verifier_stop.set()
        verifier = self._verifier
        if verifier is not None and verifier is not threading.current_thread():
            verifier.join(timeout=5)

    def _verifier_loop(self, interval_s: float) -> None:
        while not self._verifier_stop.is_set():
            self.check_integrity()
            self._verifier_stop.wait(interval_s)

    def get_entries(self, limit: int = 100, include_rolled_back: bool = False) -> list[ProvenanceEntry]:
        """Returns up to `limit` entries ordered chronologically."""
        with self._lock:
            return self._load_all_entries(limit=limit, include_rolled_back=include_rolled_back)

    def get_entry(self, entry_id: str) -> ProvenanceEntry | None:
        with self._lock:
            if self._use_sqlalchemy:
                from sqlalchemy import text
                with self._engine.connect() as conn:
                    row = conn.execute(
                        text(
                            "SELECT id, timestamp, request_hash, response_hash, "
                            "divergence_score, metadata, prev_entry_hash, hmac_signature, status "
                            "FROM provenance WHERE id = :id"
                        ),
                        {"id": entry_id},
                    ).fetchone()
                    return self._row_to_entry(tuple(row)) if row else None

            assert self._conn is not None
            cursor = self._conn.execute(
                "SELECT * FROM provenance WHERE id = ?", (entry_id,)
            )
            row = cursor.fetchone()
            return self._row_to_entry(row) if row else None

    def get_interaction_count(
        self, template: str, kernel_name: str, model: str
    ) -> int:
        """
        Calculates the number of active interactions matching the current context.
        This provides the experience metric for Stability-Plasticity Scheduling.
        """
        if self._use_sqlalchemy:
            from sqlalchemy import text
            dialect = self._engine.dialect.name
            try:
                with self._engine.connect() as conn:
                    if dialect == "postgresql":
                        query = text("""
                            SELECT COUNT(*) FROM provenance
                            WHERE (metadata::jsonb ->> 'template') = :template
                              AND (metadata::jsonb ->> 'kernel') = :kernel
                              AND (metadata::jsonb ->> 'model') = :model
                              AND status = 'active'
                        """)
                        row = conn.execute(
                            query,
                            {"template": template, "kernel": kernel_name, "model": model},
                        ).fetchone()
                        return int(row[0]) if row else 0
                    elif dialect == "sqlite":
                        query = text("""
                            SELECT COUNT(*) FROM provenance
                            WHERE json_extract(metadata, '$.template') = :template
                              AND json_extract(metadata, '$.kernel') = :kernel
                              AND json_extract(metadata, '$.model') = :model
                              AND status = 'active'
                        """)
                        row = conn.execute(
                            query,
                            {"template": template, "kernel": kernel_name, "model": model},
                        ).fetchone()
                        return int(row[0]) if row else 0
                    else:
                        rows = conn.execute(
                            text("SELECT metadata FROM provenance WHERE status = 'active'")
                        ).fetchall()
                        count = 0
                        for (meta_raw,) in rows:
                            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
                            if (
                                meta.get("template") == template
                                and meta.get("kernel") == kernel_name
                                and meta.get("model") == model
                            ):
                                count += 1
                        return count
            except Exception as e:
                logger.warning(
                    f"Could not count interactions via SQLAlchemy: {e}. Returning 0."
                )
                return 0

        query = """
            SELECT COUNT(*) FROM provenance 
            WHERE json_extract(metadata, '$.template') = ?
              AND json_extract(metadata, '$.kernel') = ?
              AND json_extract(metadata, '$.model') = ?
              AND status = 'active'
        """
        try:
            assert self._conn is not None
            row = self._conn.execute(query, (template, kernel_name, model)).fetchone()
            return row[0] if row else 0
        except sqlite3.Error as e:
            logger.warning(
                f"Could not count interactions (JSON query failed): {e}. "
                "Returning 0 to avoid inflating ElasticityScheduler thresholds."
            )
            return 0

    def export_log(self, include_rolled_back: bool = False) -> list[dict[str, Any]]:
        """
        Exports the verified log as a list of dictionaries.
        By default, only active entries are exported.

        Args:
            include_rolled_back: If True, include all entries.

        Returns:
            A list of all log entries, where each entry is a dictionary.

        Raises:
            ChainIntegrityError: If the active chain is invalid.
        """
        self.verify_chain()  # Ensure integrity before export
        all_entries = self._load_all_entries(include_rolled_back=include_rolled_back)
        return [asdict(entry) for entry in all_entries]

    def get_read_only_connection(self) -> sqlite3.Connection:
        """
        Returns a read-only connection to the SQLite database.
        """
        try:
            db_uri = self._vault_path.as_uri() + "?mode=ro"
            return sqlite3.connect(db_uri, uri=True, check_same_thread=False)
        except sqlite3.OperationalError as e:
            raise VaultError(
                "Could not open read-only connection. "
                "Ensure the database file exists and has correct permissions."
            ) from e

    def rollback(self, agent_id: str, entry_id: str) -> None:
        """
        Rolls back the log to the specified entry ID for a given agent.
        All subsequent entries for that agent will be marked as 'rolled_back'.
        """
        with self._lock:
            entry = self.get_entry(entry_id)
            if not entry:
                raise ValueError(f"Entry with ID '{entry_id}' not found.")

            if entry.metadata.get("agent_id") != agent_id:
                raise ValueError("Entry does not belong to the specified agent.")

            try:
                with self._transaction() as run:
                    rows = run(
                        "SELECT id, metadata FROM provenance WHERE timestamp > :ts AND status = 'active'",
                        {"ts": entry.timestamp},
                    )
                    for row_id, meta_raw in rows:
                        metadata = json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw)
                        if metadata.get("agent_id") == agent_id:
                            run("UPDATE provenance SET status = 'rolled_back' WHERE id = :id", {"id": row_id})
                    self._rebuild_counters(run)
                    run(_ADD_COUNTER, {"name": GENERATION_COUNTER, "value": 1.0})
            except Exception as e:
                backend = "SQLAlchemy" if self._use_sqlalchemy else "SQLite"
                raise VaultError(f"{backend} rollback error: {e}") from e
            self._verified_tip = None
            self._chain_tip = None
            logger.info(f"Provenance log rolled back for agent '{agent_id}' to entry '{entry_id}'")

    # ── Database ──────────────────────────────────────────────────────────────

    def _init_sqlalchemy_db(self) -> Any:
        try:
            from sqlalchemy import create_engine, text
        except ImportError as e:
            raise VaultError(
                "SQLAlchemy is required for enterprise database backends. "
                "Install it via `pip install sqlalchemy`."
            ) from e

        try:
            engine = create_engine(self._db_url, future=True)
            with engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS provenance (
                        id TEXT PRIMARY KEY,
                        timestamp DOUBLE PRECISION NOT NULL,
                        request_hash TEXT NOT NULL,
                        response_hash TEXT NOT NULL,
                        divergence_score DOUBLE PRECISION,
                        metadata TEXT NOT NULL,
                        prev_entry_hash TEXT NOT NULL,
                        hmac_signature TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active'
                    )
                """))
            return engine
        except Exception as e:
            raise VaultError(f"SQLAlchemy database initialization error: {e}") from e

    def _init_db(self) -> sqlite3.Connection:
        self._vault_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._vault_path.exists():
            self._vault_path.touch()
            self._vault_path.chmod(0o600)
        
        conn = sqlite3.connect(str(self._vault_path), check_same_thread=False)

        # Create table if it doesn't exist
        conn.execute("""
            CREATE TABLE IF NOT EXISTS provenance (
                id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                request_hash TEXT NOT NULL,
                response_hash TEXT NOT NULL,
                divergence_score REAL,
                metadata TEXT NOT NULL,
                prev_entry_hash TEXT NOT NULL,
                hmac_signature TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            )
        """)

        # Add status column for migration from older versions
        try:
            conn.execute("SELECT status FROM provenance LIMIT 1").fetchall()
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE provenance ADD COLUMN status TEXT DEFAULT 'active'")
            logger.info("Migrated database: Added 'status' column to provenance table.")

        conn.commit()
        return conn

    def _dialect(self) -> str:
        """"sqlite" for the sqlite3 backend, otherwise the SQLAlchemy dialect name (e.g. "postgresql")."""
        return self._engine.dialect.name if self._use_sqlalchemy else "sqlite"

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[Any]:
        """One transaction. Yields `run(sql, params)`, which returns the rows of a SELECT (none otherwise)."""
        if self._use_sqlalchemy:
            from sqlalchemy import text

            with self._engine.begin() as conn:

                def run_sa(sql: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
                    result = conn.execute(text(sql), params or {})
                    return [tuple(row) for row in result.fetchall()] if result.returns_rows else []

                yield run_sa
            return

        conn = self._conn
        assert conn is not None
        try:
            yield lambda sql, params=None: conn.execute(sql, params or {}).fetchall()
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @staticmethod
    def _derived(metadata: dict[str, Any]) -> dict[str, str | None]:
        event_type = metadata.get("event_type")
        event_type = None if event_type is None else str(event_type)
        if metadata.get("event") is None:
            return {"event": None, "verdict": None, "tool": None, "mode": None, "event_type": event_type}
        return {
            "event": str(metadata["event"]),
            "verdict": str(metadata.get("verdict") or ""),
            "tool": str(metadata.get("tool") or ""),
            "mode": str(metadata.get("mode") or ""),
            "event_type": event_type,
        }

    @staticmethod
    def _counter_deltas(metadata: dict[str, Any], sign: int) -> list[tuple[str, float]]:
        """Counter changes for one entry: sign +1 when it is written, -1 when it is rolled back.
        Only tool-call decisions have counters; divergence figures are read from a bounded window."""
        if metadata.get("event") != TOOL_CALL_EVENT:
            return []
        derived = ProvenanceLog._derived(metadata)
        return [
            ("tool:total", sign),
            (f"tool:verdict:{derived['verdict']}", sign),
            (f"tool:tool:{derived['tool']}", sign),
        ]

    def _write_entry(self, entry: ProvenanceEntry, expected_tip_id: str | None = None) -> bool:
        """Writes the entry and its counters. Returns False when the chain tip is no longer `expected_tip_id`
        (PostgreSQL only; SQLite is written unconditionally, under the vault lock)."""
        derived = self._derived(entry.metadata)
        params: dict[str, Any] = {
            "id": entry.id,
            "timestamp": entry.timestamp,
            "request_hash": entry.request_hash,
            "response_hash": entry.response_hash,
            "divergence_score": entry.divergence_score,
            "metadata": json.dumps(entry.metadata),
            "prev_entry_hash": entry.prev_entry_hash,
            "hmac_signature": entry.hmac_signature,
            "status": entry.status,
            **derived,
        }
        counters = [*self._counter_deltas(entry.metadata, +1), (GENERATION_COUNTER, 1.0)]
        try:
            if self._dialect() == "sqlite":
                with self._transaction() as run:
                    run(_INSERT_ENTRY, params)
                    for name, delta in counters:
                        run(_ADD_COUNTER, {"name": name, "value": delta})
                return True

            # One statement, so one network round trip. The entry is inserted only if the newest active entry is
            # still the expected one, and the counters only if the entry was inserted.
            params["expected_tip"] = expected_tip_id
            columns = (
                "id, timestamp, request_hash, response_hash, divergence_score, metadata, prev_entry_hash, "
                "hmac_signature, status, event, verdict, tool, mode, event_type"
            )
            casts = {
                "id": "TEXT", "timestamp": "DOUBLE PRECISION", "request_hash": "TEXT", "response_hash": "TEXT",
                "divergence_score": "DOUBLE PRECISION", "metadata": "TEXT", "prev_entry_hash": "TEXT",
                "hmac_signature": "TEXT", "status": "TEXT", "event": "TEXT", "verdict": "TEXT", "tool": "TEXT",
                "mode": "TEXT", "event_type": "TEXT",
            }
            select_list = ", ".join(f"CAST(:{name} AS {casts[name]})" for name in columns.replace(" ", "").split(","))
            value_rows = []
            for index, (name, delta) in enumerate(counters):
                params[f"cname{index}"] = name
                params[f"cvalue{index}"] = delta
                value_rows.append(f"(CAST(:cname{index} AS TEXT), CAST(:cvalue{index} AS DOUBLE PRECISION))")
            sql = (
                "WITH entry AS ("
                f"INSERT INTO provenance ({columns}) SELECT {select_list} "
                "WHERE (SELECT id FROM provenance WHERE status = 'active' ORDER BY timestamp DESC, id DESC LIMIT 1) "
                "IS NOT DISTINCT FROM CAST(:expected_tip AS TEXT) RETURNING id) "
                "INSERT INTO provenance_counters (name, value) SELECT c.name, c.value "
                f"FROM (VALUES {', '.join(value_rows)}) AS c(name, value) WHERE EXISTS (SELECT 1 FROM entry) "
                "ON CONFLICT (name) DO UPDATE SET value = provenance_counters.value + excluded.value "
                "RETURNING name"
            )
            with self._transaction() as run:
                return bool(run(sql, params))
        except Exception as e:
            backend = "SQLAlchemy" if self._use_sqlalchemy else "SQLite"
            raise VaultError(f"{backend} write error: {e}") from e

    def _load_entries_batch(
        self,
        limit: int,
        offset: int = 0,
        include_rolled_back: bool = False,
    ) -> list[ProvenanceEntry]:
        """Loads a single page of entries ordered by timestamp ASC."""
        if self._use_sqlalchemy:
            from sqlalchemy import text
            where_clause = "" if include_rolled_back else "WHERE status = 'active'"
            query = text(
                f"SELECT id, timestamp, request_hash, response_hash, "
                f"divergence_score, metadata, prev_entry_hash, hmac_signature, status "
                f"FROM provenance {where_clause} ORDER BY timestamp ASC LIMIT :limit OFFSET :offset"
            )
            with self._engine.connect() as conn:
                rows = conn.execute(query, {"limit": int(limit), "offset": int(offset)}).fetchall()
                return [self._row_to_entry(tuple(r)) for r in rows]

        assert self._conn is not None
        where_clause = "" if include_rolled_back else "WHERE status = 'active'"
        query = f"SELECT * FROM provenance {where_clause} ORDER BY timestamp ASC LIMIT ? OFFSET ?"
        rows = self._conn.execute(query, (int(limit), int(offset))).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def _load_all_entries(self, limit: int | None = None, include_rolled_back: bool = False) -> list[ProvenanceEntry]:
        if self._use_sqlalchemy:
            from sqlalchemy import text
            where_clause = "" if include_rolled_back else "WHERE status = 'active'"
            limit_clause = f" LIMIT {int(limit)}" if limit else ""
            query = text(
                f"SELECT id, timestamp, request_hash, response_hash, "
                f"divergence_score, metadata, prev_entry_hash, hmac_signature, status "
                f"FROM provenance {where_clause} ORDER BY timestamp ASC{limit_clause}"
            )
            with self._engine.connect() as conn:
                rows = conn.execute(query).fetchall()
                return [self._row_to_entry(tuple(r)) for r in rows]

        query = "SELECT * FROM provenance"
        conditions = []
        if not include_rolled_back:
            conditions.append("status = 'active'")
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY timestamp ASC"
        params: tuple[int, ...] = ()
        if limit:
            query += " LIMIT ?"
            params = (int(limit),)

        assert self._conn is not None
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def _read_chain_tip(self) -> tuple[str | None, str]:
        """(id, content hash) of the newest active entry; (None, genesis) for an empty chain."""
        entries = self._select("status = 'active'", {}, "timestamp DESC, id DESC", 1)
        if not entries:
            return None, self.GENESIS_HASH
        return entries[0].id, entries[0].content_hash()

    @staticmethod
    def _row_to_entry(row: tuple[Any, ...]) -> ProvenanceEntry:
        meta_raw = row[5]
        metadata = json.loads(meta_raw) if isinstance(meta_raw, str) else dict(meta_raw)
        return ProvenanceEntry(
            id=row[0],
            timestamp=float(row[1]),
            request_hash=row[2],
            response_hash=row[3],
            divergence_score=float(row[4]) if row[4] is not None else None,
            metadata=metadata,
            prev_entry_hash=row[6],
            hmac_signature=row[7],
            status=row[8],
        )

    # ── Cryptography ────────────────────────────────────────────────────────────

    def _compute_hmac(self, content: str) -> str:
        return hmac.new(
            self._hmac_key,
            content.encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def _load_or_generate_key(self) -> bytes:
        """
        Loads the HMAC key from the vault or environment.
        Raises VaultError if key is missing to prevent silent data loss.
        """
        import os
        key_env = os.environ.get(_DEFAULT_KEY_ENV)
        if key_env:
            return key_env.encode()

        key_path = self._vault_path.parent / ".ct_hmac_key"
        if key_path.exists():
            return key_path.read_bytes()

        # Initial setup: generate new key (only for first-time setup)
        key = hashlib.sha256(uuid.uuid4().bytes).digest()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        logger.info(
            f"Initial HMAC key generated and saved: {key_path}. "
            "IMPORTANT: Back up this key file. If lost, the provenance chain "
            "cannot be verified. Set CT_HMAC_SECRET env var or restore "
            f"'{key_path}' from backup."
        )
        return key
