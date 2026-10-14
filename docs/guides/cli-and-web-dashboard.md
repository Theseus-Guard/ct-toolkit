# CLI & Web Audit Dashboard Guide

CT Toolkit provides both a **Rich Terminal CLI** and a self-hosted **Interactive Web Audit & Telemetry Dashboard** for inspecting cryptographic provenance logs, L1/L2/L3 divergence tiers, and staged sandbox rules.

![CT Toolkit Web Audit & Telemetry Dashboard](../ct_toolkit_web_dashboard.png)

---

## Prerequisites

Install the CLI and Server extras:

```bash
pip install "ct-toolkit[cli,server]"
# Or install all optional features (including PostgreSQL & OpenTelemetry):
pip install "ct-toolkit[all]"
```

---

## 1. Terminal Audit & Telemetry (`ct-toolkit status` / `dashboard`)

To inspect a local SQLite provenance vault or an enterprise PostgreSQL database directly in your terminal:

```bash
# Inspect a local SQLite vault
ct-toolkit status --vault ./ct_provenance.db --limit 10

# Or inspect an enterprise PostgreSQL / SQLAlchemy vault
ct-toolkit status --vault-url "postgresql+psycopg2://user:pass@localhost:5432/ct_audit"
```

What it displays:
- **Chain Integrity**: Cryptographic HMAC-SHA256 verification across the entire hash chain (`VALID` or `TAMPERED / BROKEN`).
- **Divergence Summary**: Verified entry count, average and peak divergence scores, and the current chain head hash.
- **Divergence Tier Breakdown**: Count and percentage distribution across `OK`, `L1_WARNING`, `L2_JUDGE`, and `L3_ICM`.
- **Staged Kernel Rules (Sandbox Cooldown)**: Any plastic kernel commitments currently undergoing staged shadow verification and their remaining cooldown time.
- **Recent Provenance Entries**: Timestamp, entry ID, model/provider, tier, divergence score, and hash prefix.

---

## 2. Interactive Web Audit & Telemetry Dashboard (`--web`)

Launch the modern web dashboard with `--web` (or `-w`):

```bash
ct-toolkit dashboard --web --vault ./ct_provenance.db --port 8001
```

Then open **`http://127.0.0.1:8001/dashboard`** in your browser.

### Key Web UI Features

- **Real-Time Auto-Refresh**: Automatically polls `/api/dashboard/status` every 5 seconds (can be toggled on/off or manually refreshed).
- **KPI Telemetry Cards**: Live cryptographic chain verification badge, active entry counter, average/max divergence score with visual meter, and staged cooldown counter.
- **Visual Tier Breakdown**: Stacked bar chart and proportional breakdown of `OK`, `L1_WARNING`, `L2_JUDGE`, and `L3_ICM` events.
- **Interactive Log Explorer**: Filter provenance entries by divergence tier (`All`, `OK`, `L1`, `L2`, `L3`) and click any row to inspect the full request/response SHA-256 hashes, previous entry hash, and JSON metadata.

---

## 3. Security Architecture (Localhost-Only by Default)

The Web UI and sidecar server (`ct-toolkit serve`) are designed to **fail closed**:

1. **Localhost Binding by Default (`127.0.0.1`)**:
   - By default, `--host` is set to `127.0.0.1`. Only processes on the local machine can connect.
2. **Mandatory API Key for Non-Loopback Hosts**:
   - Attempting to bind to `0.0.0.0` or a public/LAN interface without providing `--api-key` (or setting `CT_SERVER_API_KEY`) immediately aborts startup with a `ValueError`.
   - Even if a reverse proxy forwards a non-loopback client IP while no API key is configured, the server rejects the request with `403 Forbidden`.
3. **Credential & Filesystem Path Redaction**:
   - Database passwords in `--vault-url` are automatically masked (`postgresql://user:***@host:5432/db`) before being returned by `/api/dashboard/status`.
   - Local SQLite vault paths are reduced to their filename (`ct_provenance.db`) so internal server directory paths are never leaked.
4. **HTTP Security Headers**:
   - Every response includes `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and `Cache-Control: no-store`.

---

## 4. Standalone Endpoint Auditor (`ct-toolkit audit`)

Stress-test any OpenAI-compatible or local Ollama endpoint against the L3 Identity Consistency Metric (ICM) probe battery without writing code:

```bash
# Audit a local Ollama or LM Studio endpoint using the defense kernel
ct-toolkit audit --url http://localhost:11434/v1 --model llama3 --kernel defense --template defense
```

---

## 5. Sidecar Guardrail Server (`ct-toolkit serve`)

Run CT Toolkit as a standalone microservice exposing both `/v1/analyze` (for polyglot services) and `/dashboard`:

```bash
# Localhost development
ct-toolkit serve --host 127.0.0.1 --port 8000

# Production / container deployment (requires API key)
export CT_SERVER_API_KEY="your-strong-secret-key"
ct-toolkit serve --host 0.0.0.0 --port 8000
```
