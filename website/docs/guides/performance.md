# Performance & Scaling

The provenance chain grows with every model call, and the dashboard reads it every few seconds. This page lists what the toolkit does to keep writes and dashboard reads cheap as the chain grows, and the measured cost on SQLite and PostgreSQL.

## Measured results

**Setup.** Each run writes 20,000 provenance entries: 10,000 divergence entries and 10,000 tool-call decisions, alternating verdicts. The dashboard reads are timed in-process, without HTTP. The machine is an Apple Silicon Mac with Python 3.13. SQLite is a local file. PostgreSQL 18.6 (`postgres:18.6-trixie`) runs in Docker on the same machine. The TCP column connects through a port published on `127.0.0.1`; Docker Desktop forwards that port through its VM. The Unix socket column connects to a server container's socket on the same host. A remote PostgreSQL server adds a network round trip per statement, which is not measured here.

| Operation (20,000 entries) | Before | SQLite | PostgreSQL 18.6, TCP | PostgreSQL 18.6, Unix socket |
| :--- | ---: | ---: | ---: | ---: |
| Write all entries | 133 s | 14.1 s | 70.5 s | 16.5 s |
| Dashboard telemetry, cache miss | 1,054 ms | 9.7 ms | 5.7 ms | 5.6 ms |
| Dashboard telemetry, cache hit | not cached | 0.01 ms | 1.0 ms | 0.24 ms |
| Tool-call page, cache miss | 84 ms | 5.2 ms | 6.1 ms | 5.7 ms |
| Tool-call page, cache hit | not cached | 0.06 ms | 1.1 ms | 0.30 ms |
| Full chain verification (background) | not separated | 229 ms | 374 ms | 509 ms |
| Incremental check, no new entries | not separated | under 1 ms | 1.3 ms | 0.4 ms |

"Before" is the same code path as the previous release: the write path scanned the whole table to find the last entry, and every dashboard poll loaded and re-verified the whole chain.

At 2,000 entries the same comparison gives: writes 2.1 s to 1.3 s, telemetry 35 ms to 2 ms, tool-call page 7 ms to under 1 ms.

Write cost grows linearly: about 0.7 ms per entry on SQLite, and about 3.5 ms per entry over TCP to PostgreSQL. Each entry is written in one transaction with several statements, so over a network every statement is a round trip. Before the change, SQLite writes grew with the square of the chain length.

## How it is kept fast

- **Indexed timestamps.** The last-entry lookup on every write uses an index instead of a table scan.
- **Columns filled at write time.** `event`, `verdict`, `tool`, `mode` and `event_type` are copied out of the entry's metadata when it is written. Tool-call listings filter, sort and page these columns in the database, so JSON is not parsed to answer a request.
- **Pre-computed counters.** Tool-call totals by verdict and by tool are kept in a counters table, updated in the same transaction as the entry. The dashboard reads counters instead of counting rows.
- **Bounded divergence reads.** Divergence figures and the ledger read the newest `limit` entries (200 by default), whatever the chain length.
- **Background integrity.** A daemon thread checks new entries every 5 seconds and the whole chain every `--full-verify-interval` seconds. The chain lock is held per batch, so writers are not blocked by a full pass.
- **Read cache.** Dashboard responses are cached per query and per write generation. A write or rollback made through the vault invalidates them; entries older than 30 seconds are recomputed in any case, so edits made directly in the database show within that time.

## Tuning

| Setting | Default | Effect |
| :--- | :--- | :--- |
| `--full-verify-interval`, env `CT_FULL_VERIFY_INTERVAL` | `300` s (minimum `5`) | Time between full chain passes. An edit to an old entry is detected within this time. A full pass costs about 230 ms per 20,000 entries on the setup above, and grows linearly. |
| Cache age limit | 30 s (fixed in code) | Upper bound on how old a dashboard response can be when the database is edited outside the toolkit. |
| `limit` on the dashboard status endpoint | `200` | Number of newest divergence entries the figures and ledger are computed over. |

New entries are always checked within 5 seconds. Only edits to entries that were already verified wait for the full pass.

## PostgreSQL

Use a PostgreSQL vault for shared deployments:

```bash
ct-toolkit dashboard --web --vault-type postgres --vault postgresql+psycopg://user:***@db-host:5432/ct_provenance
```

Schema changes are applied when a vault is first opened. Older vaults get the new columns, indexes and counters automatically; the first open of a large vault takes longer while the existing rows are filled in.

Each dashboard process keeps its own cache and its own verifier thread. With many users behind one dashboard process, reads from the database are shared through the cache; with several dashboard processes, each one checks the chain on its own schedule.

## What is not measured here

- Concurrent writers from several agent processes.
- Chains beyond 20,000 entries. The full pass is linear, so expect about 1 s per full pass at 100,000 entries, extrapolated from the figure above.
- A remote PostgreSQL server. The TCP figures go through Docker Desktop's port forwarding on one machine, so a real network adds latency to every statement.
