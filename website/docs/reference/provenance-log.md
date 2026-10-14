# ProvenanceLog Reference

```python
from ct_toolkit.provenance.log import ProvenanceLog

# Default: local SQLite with sensitive data masking enabled
log = ProvenanceLog(vault_path="./ct_provenance.db")

# Production: remote SQLAlchemy / PostgreSQL database
pg_log = ProvenanceLog(
    vault_url="postgresql+psycopg2://user:pass@db.internal:5432/ct_audit"
)

# Disable sensitive data masking (not recommended for production)
log = ProvenanceLog(
    vault_path="./ct_provenance.db",
    mask_sensitive_data=False,
)
```

## Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `vault_type` | `str` | `"local"` | Storage backend type (`"local"` or `"sqlalchemy"`) |
| `vault_path` | `str` | `"./ct_provenance.db"` | Local SQLite database file path |
| `vault_url` | `str \| None` | `None` | SQLAlchemy DB URL (e.g., `postgresql+psycopg2://...`). Overrides `vault_path` when set |
| `hmac_key` | `bytes \| None` | `None` | HMAC secret (auto-generated if not provided) |
| `mask_sensitive_data` | `bool` | `True` | Enable automatic sensitive data masking |

!!! tip "Sensitive Data Masking"
    ProvenanceLog automatically detects and masks sensitive data before storing entries:
    
    - **API Keys:** OpenAI (`sk-*`), Anthropic (`sk-ant-*`), Google (`AIza*`), AWS (`AKIA*`)
    - **Tokens:** Bearer tokens, auth headers
    - **PII:** Email addresses, phone numbers, SSNs, credit cards
    - **Credentials:** Passwords, secrets, client secrets
    
    Masked values are replaced with `[REDACTED:*]` placeholders in metadata.

## Methods

### `record(request_text, response_text, divergence_score, metadata) → str`

Returns the entry UUID.

### `verify_chain(batch_size: int = 1000) → bool`

Verifies the entire hash chain and HMAC signatures in paginated batches (`batch_size=1000`) with $O(1)$ memory usage. Raises `ChainIntegrityError` if any entry has been tampered with.


### `get_entries(limit, include_rolled_back) → list[ProvenanceEntry]`

### `get_entry(entry_id) → ProvenanceEntry | None`

### `export_log(include_rolled_back) → list[dict]`

Verifies chain before export. All sensitive data in metadata is masked.

### `get_read_only_connection() → sqlite3.Connection`

Returns a read-only SQLite connection for external auditors.

### `rollback(agent_id, entry_id) → None`

Marks all entries after `entry_id` for the given agent as `rolled_back`.

## ProvenanceEntry fields

| Field | Type |
|:---|:---|
| `id` | `str` (UUID) |
| `timestamp` | `float` |
| `request_hash` | `str` (SHA-256) |
| `response_hash` | `str` (SHA-256) |
| `divergence_score` | `float \| None` |
| `metadata` | `dict` |
| `prev_entry_hash` | `str` |
| `hmac_signature` | `str` |
| `status` | `"active"` \| `"rolled_back"` |
