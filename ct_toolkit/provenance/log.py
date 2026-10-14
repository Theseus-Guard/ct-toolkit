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

import json
import time
import uuid
import hmac
import hashlib
import sqlite3
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

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
            prev_hash = self._get_last_entry_hash()

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

            self._write_entry(entry)
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

    def get_chain_checkpoint(self, batch_size: int = 500) -> dict[str, Any]:
        """
        Verifies the chain in batches and returns a checkpoint dictionary
        (`offset`, `last_entry_id`, `last_content_hash`) that can be passed to
        future `verify_chain(start_offset=..., expected_prev_hash=...)` calls.
        """
        if batch_size <= 0:
            batch_size = 500

        expected_prev = self.GENESIS_HASH
        offset = 0
        total_verified = 0
        last_entry_id: str | None = None

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
                last_entry_id = entry.id
                total_verified += 1

            if len(batch) < batch_size:
                break
            offset += len(batch)

        return {
            "verified_count": total_verified,
            "next_offset": total_verified,
            "last_entry_id": last_entry_id,
            "last_content_hash": expected_prev,
        }

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

            if self._use_sqlalchemy:
                from sqlalchemy import text
                try:
                    with self._engine.begin() as conn:
                        rows = conn.execute(
                            text(
                                "SELECT id, metadata FROM provenance "
                                "WHERE timestamp > :ts AND status = 'active'"
                            ),
                            {"ts": entry.timestamp},
                        ).fetchall()
                        ids_to_rollback = []
                        for r_id, meta_raw in rows:
                            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
                            if meta.get("agent_id") == agent_id:
                                ids_to_rollback.append(r_id)
                        for r_id in ids_to_rollback:
                            conn.execute(
                                text("UPDATE provenance SET status = 'rolled_back' WHERE id = :id"),
                                {"id": r_id},
                            )
                    logger.info(f"Provenance log rolled back for agent '{agent_id}' to entry '{entry_id}'")
                    return
                except Exception as e:
                    raise VaultError(f"SQLAlchemy rollback error: {e}") from e

            try:
                assert self._conn is not None
                self._conn.execute(
                    """
                    UPDATE provenance
                    SET status = 'rolled_back'
                    WHERE timestamp > ? AND json_extract(metadata, '$.agent_id') = ?
                    """,
                    (entry.timestamp, agent_id),
                )
                self._conn.commit()
                logger.info(f"Provenance log rolled back for agent '{agent_id}' to entry '{entry_id}'")
            except sqlite3.Error as e:
                raise VaultError(f"SQLite rollback error: {e}") from e

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

    def _write_entry(self, entry: ProvenanceEntry) -> None:
        if self._use_sqlalchemy:
            from sqlalchemy import text
            try:
                with self._engine.begin() as conn:
                    conn.execute(
                        text("""
                            INSERT INTO provenance
                            (id, timestamp, request_hash, response_hash,
                             divergence_score, metadata, prev_entry_hash, hmac_signature, status)
                            VALUES (:id, :timestamp, :request_hash, :response_hash,
                                    :divergence_score, :metadata, :prev_entry_hash, :hmac_signature, :status)
                        """),
                        {
                            "id": entry.id,
                            "timestamp": entry.timestamp,
                            "request_hash": entry.request_hash,
                            "response_hash": entry.response_hash,
                            "divergence_score": entry.divergence_score,
                            "metadata": json.dumps(entry.metadata),
                            "prev_entry_hash": entry.prev_entry_hash,
                            "hmac_signature": entry.hmac_signature,
                            "status": entry.status,
                        },
                    )
                return
            except Exception as e:
                raise VaultError(f"SQLAlchemy write error: {e}") from e

        try:
            assert self._conn is not None
            self._conn.execute(
                """
                INSERT INTO provenance
                (id, timestamp, request_hash, response_hash,
                 divergence_score, metadata, prev_entry_hash, hmac_signature, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.timestamp,
                    entry.request_hash,
                    entry.response_hash,
                    entry.divergence_score,
                    json.dumps(entry.metadata),
                    entry.prev_entry_hash,
                    entry.hmac_signature,
                    entry.status,
                ),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise VaultError(f"SQLite write error: {e}") from e

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
        if limit:
            query += f" LIMIT {limit}"
        
        assert self._conn is not None
        rows = self._conn.execute(query).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def _get_last_entry_hash(self) -> str:
        if self._use_sqlalchemy:
            from sqlalchemy import text
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT id, timestamp, request_hash, response_hash, "
                        "divergence_score, metadata, prev_entry_hash, hmac_signature, status "
                        "FROM provenance WHERE status = 'active' ORDER BY timestamp DESC LIMIT 1"
                    )
                ).fetchone()
                if not row:
                    return self.GENESIS_HASH
                return self._row_to_entry(tuple(row)).content_hash()

        assert self._conn is not None
        row = self._conn.execute(
            "SELECT * FROM provenance WHERE status = 'active' ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        if not row:
            return self.GENESIS_HASH
        return self._row_to_entry(row).content_hash()

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
