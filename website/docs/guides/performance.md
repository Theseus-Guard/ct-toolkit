# Performance & Scaling

The provenance chain grows with every model call, and the dashboard reads it every few seconds. This page lists what the toolkit does to keep writes and dashboard reads cheap as the chain grows, and the measured cost on SQLite and PostgreSQL.

## Measured results

**Setup.** Each run writes 20,000 provenance entries: 10,000 divergence entries and 10,000 tool-call decisions, alternating verdicts. The dashboard reads are timed in-process, without HTTP. The machine is an Apple Silicon Mac with Python 3.13. SQLite is a local file. PostgreSQL 18.6 (`postgres:18.6-trixie`) runs in a container on the same machine and is reached over TCP on `127.0.0.1`; Docker Desktop forwards that port through its VM, so each statement costs about 1 ms of round trip. A remote PostgreSQL server adds its own network latency per statement, which is not measured here.

**Method.** Writes and the full verification pass are single runs. Dashboard read paths are the best of seven runs; the cache-miss rows clear the cache before each run.

| Operation (20,000 entries) | Previous release (SQLite) | SQLite | PostgreSQL 18.6 (TCP) |
| :--- | ---: | ---: | ---: |
| Write all entries | 133 s | 14.8 s | 28.8 s |
| Dashboard telemetry, cache miss | 1,054 ms | 10.2 ms | 4.1 ms |
| Dashboard telemetry, cache hit | not cached | 0.00 ms | 0.00 ms |
| Tool-call page, cache miss | 84 ms | 0.21 ms | 3.1 ms |
| Tool-call page, cache hit | not cached | 0.05 ms | 0.06 ms |
| Full chain verification (background) | not separated | 152 ms | 320 ms |
| Incremental check, no new entries | not separated | under 0.1 ms | 3.4 ms |

"Previous release" is the code before the performance work: the write path scanned the whole table to find the last entry, and every dashboard poll loaded and re-verified the whole chain.

At 2,000 entries, the previous release against the current code on SQLite: writes 2.1 s to 1.3 s, telemetry 35 ms to 2 ms, tool-call page 7 ms to under 1 ms.

### Cost of one write on PostgreSQL

Each record is written in one statement. Over TCP, one statement costs about one round trip, so the number of statements per record is what matters.

| Record | Statements, before | Statements, now | Time, before | Time, now |
| :--- | ---: | ---: | ---: | ---: |
| Tool-call decision | 6 | 1 | 2.98 ms | 1.12 ms |
| Divergence entry | 3 | 1 | 2.79 ms | 1.28 ms |

Writes on SQLite cost about 0.7 ms per entry and grow linearly. On PostgreSQL over TCP the 20,000-entry run takes 28.8 s, against 70.5 s before the statement changes.

## How it is kept fast

- **One statement per write (PostgreSQL).** The entry and its counters are written in one statement. The entry is inserted only if the newest active entry is still the one this process last wrote; otherwise another writer has appended, the process reads the new tip and signs on top of it. Several processes can therefore share one vault without forking the chain.
- **Indexed timestamps.** The last-entry lookup uses an index instead of a table scan.
- **Columns filled at write time.** `event`, `verdict`, `tool`, `mode` and `event_type` are copied out of the entry's metadata when it is written. Tool-call listings filter, sort and page these columns in the database, so JSON is not parsed to answer a request.
- **Pre-computed counters.** Tool-call totals by verdict and by tool are kept in a counters table, updated in the same statement as the entry. The dashboard reads counters instead of counting rows.
- **One statement per page.** A tool-call page returns its rows and the total together. Unfiltered, the total is the counter; filtered, it is a count over the same filter, evaluated in the same statement.
- **Bounded divergence reads.** Divergence figures and the ledger read the newest `limit` entries (200 by default), whatever the chain length.
- **Background integrity.** A daemon thread checks new entries every 5 seconds and the whole chain every `--full-verify-interval` seconds, in batches of 2,000 by keyset paging. The chain lock is held per batch, so writers are not blocked by a full pass. The full pass runs in the same process as the dashboard, so while it runs, a request can wait for the interpreter lock; that is why a single cold request can show a higher miss time.
- **Read cache.** Dashboard responses are cached per query and per write generation. A write or rollback made through the vault invalidates them. The generation is read from the database at most once per second per process, so a write by another process shows within a second. Entries older than 30 seconds are recomputed in any case, so edits made directly in the database show within that time.

## Tuning

| Setting | Default | Effect |
| :--- | :--- | :--- |
| `--full-verify-interval`, env `CT_FULL_VERIFY_INTERVAL` | `300` s (minimum `5`) | Time between full chain passes. An edit to an old entry is detected within this time. A full pass costs about 150 ms on SQLite and 320 ms on PostgreSQL per 20,000 entries, and grows linearly. |
| Cache age limit | 30 s (fixed in code) | Upper bound on how old a dashboard response can be when the database is edited outside the toolkit. |
| Generation check interval | 1 s (fixed in code) | How often a dashboard process asks the database whether anything was written. |
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

- Concurrent writers from several agent processes under load. The write path handles a concurrent writer correctly, and the test suite covers it, but its throughput is not measured.
- Chains beyond 20,000 entries. The full pass is linear, so expect about 1 s per full pass at 100,000 entries on PostgreSQL, extrapolated from the figure above.
- A remote PostgreSQL server. The TCP figures go through Docker Desktop's port forwarding on one machine; a real network adds its own latency to every statement.
