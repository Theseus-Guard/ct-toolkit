# CLI Reference

The **CT Toolkit CLI** (`ct-toolkit`) provides command-line tools for inspecting provenance chains, launching the interactive Web Audit Dashboard, auditing LLM endpoints, managing kernels, and serving guardrails as an API.

---

## Commands

### `status` / `dashboard`
Display the **Audit & Telemetry Dashboard** in the terminal (via Rich) or launch the **Interactive Web Audit Dashboard** (`--web`).

**Usage:**
```bash
# Terminal output
ct-toolkit status --vault ./ct_provenance.db

# Interactive Web UI at http://127.0.0.1:8001/dashboard
ct-toolkit dashboard --web --vault ./ct_provenance.db --port 8001
```

**Options:**
*   `--vault`: Path to the local SQLite provenance log database (Default: `./ct_provenance.db`).
*   `--vault-url`: SQLAlchemy database URL (e.g., `postgresql+psycopg2://...`) for enterprise vaults.
*   `--limit`, `-n`: Number of recent provenance log entries to display (Default: `10` in CLI, up to `50` in Web UI).
*   `--web`, `-w`: Launch the interactive Web Audit & Telemetry Dashboard instead of terminal output.
*   `--host`: Host to bind the web dashboard server to when `--web` is used (Default: `127.0.0.1`).
*   `--port`: Port to bind the web dashboard server to when `--web` is used (Default: `8001`).
*   `--api-key`: Optional `X-CT-API-Key` required to access the web dashboard API (Mandatory when binding to a non-loopback host such as `0.0.0.0`).

---

### `audit`
Run an independent Identity Consistency Metric (ICM) audit against an LLM endpoint.

**Usage:**
```bash
ct-toolkit audit --url <URL> --kernel <KERNEL> [OPTIONS]
```

**Options:**
*   `--url`: Target LLM API base URL (Required).
*   `--api-key`: API Key for the provider (Default: `no-key`).
*   `--provider`: LLM provider (`openai`, `anthropic`, `ollama`) (Default: `openai`).
*   `--kernel`: Name of the Constitutional Kernel to use (Default: `general`).
*   `--template`: Name of the Identity Template to use (Default: `general`).
*   `--policy-environment`: Policy environment override (`dev`, `test`, `prod`) (Default: `prod`).
*   `--model`: Specific model ID to test.
*   `--max-probes`: Max number of probes to run.

---

### `serve`
Starts a FastAPI server that implements the LiteLLM Generic Guardrail API (`/v1/analyze`) and also hosts the Web Audit Dashboard (`/dashboard`).

> [!TIP]
> If a `config/` directory exists in your project root, CT Toolkit will automatically look there for custom kernels and templates (e.g., `config/defense.yaml`).

**Usage:**
```bash
ct-toolkit serve --kernel <KERNEL> --port <PORT> [OPTIONS]
```

**Options:**
*   `--host`: Host to bind the server to (Default: `127.0.0.1`). Non-loopback hosts require `--api-key` or `CT_SERVER_API_KEY`.
*   `--port`: Port to bind the server to (Default: `8001`).
*   `--api-key`: API key for authenticating requests via the `X-CT-API-Key` header (or set `CT_SERVER_API_KEY`).
*   `--kernel`: Name of the Constitutional Kernel to use (Default: `general`).
*   `--template`: Name of the Identity Template to use (Default: `general`).
*   `--vault`: Path to the provenance log database (Default: `./ct_provenance.db`).
*   `--policy-environment`: Policy environment override (`dev`, `test`, `prod`) (Default: `prod`).
*   `--judge-provider`: LLM provider for L2/L3 judge calls (Default: `openai`).
*   `--judge-model`: Specific model ID for L2/L3 judge calls.

**Example Request (Pre-call):**
```json
{
  "texts": ["extracted text"],
  "input_type": "request",
  "structured_messages": [{"role": "user", "content": "Hello"}]
}
```

**Example Response (Block):**
```json
{
  "action": "BLOCKED",
  "blocked_reason": "Identity constraint violation: ..."
}
```

**Example Response (None):**
```json
{
  "action": "NONE"
}
```

---

### `setup`
Download and verify a kernel/template profile against the trusted SHA-256 manifest.

```bash
ct-toolkit setup personal_kernel --dest ./config
```

---

### `list-kernels`
List all available Constitutional Kernels.

```bash
ct-toolkit list-kernels
```

---

### `list-templates`
List all available Identity Templates.

```bash
ct-toolkit list-templates
```

---

## Global Options
*   `--version`, `-v`: Show the version and exit.
*   `--help`: Show help message and exit.
