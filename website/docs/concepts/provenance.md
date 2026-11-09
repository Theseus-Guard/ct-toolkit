# Provenance & Compliance

CT Toolkit's **Provenance Log** creates an immutable, cryptographically-signed record of every agent interaction.

## HMAC Hash Chain

Each entry includes:
- SHA-256 hash of the request and response
- Reference to the previous entry's hash
- HMAC-SHA256 signature

Breaking any entry invalidates the entire chain — making tampering detectable.

## Integrity checks and performance

Each provenance entry is checked against the chain in the background, not on each dashboard request:

- New entries are checked every 5 seconds, at a cost proportional to the number of new entries.
- A full pass over the whole chain runs every `--full-verify-interval` seconds (default 300, env `CT_FULL_VERIFY_INTERVAL`). An edit to an old entry therefore shows within that time. Set a shorter interval if that is too long for your use.
- Dashboard reads do not touch the chain for integrity; they show the last published result.

Divergence figures on the dashboard are computed over the newest entries (the `limit` parameter), and tool-call listings run as queries against indexed columns that are filled in when each entry is written. Older vaults are upgraded automatically the first time they are opened. See [Performance](../guides/performance.md) for measured numbers.

## Tool-call decisions

When the [Tool-Call Guard](tool-call-guard.md) reviews a tool call, the decision is written to the chain as a metadata event with `event="tool_call"`. It records the tool name, verdict, mode, findings, judge answer, provider and model.

Only hashes of the call text are stored, as for every entry. The call's arguments are **not** in the audit trail.

## Compliance use case

Enterprises can use the Provenance Log to prove to regulators that their deployed agent has maintained consistent identity since day one.

See [ProvenanceLog reference](../reference/provenance-log.md) for the full API.
