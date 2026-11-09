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
*   `--full-verify-interval`: Seconds between full chain verifications when `--web` is used (Default: `300`, minimum `5`). Env: `CT_FULL_VERIFY_INTERVAL`. An edit to an old entry shows within this time; new entries are checked every 5 seconds. See [Performance](../guides/performance.md).

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
*   `--full-verify-interval`: Seconds between full chain verifications (Default: `300`, minimum `5`). Env: `CT_FULL_VERIFY_INTERVAL`. See [Performance](../guides/performance.md).
*   `--kernel`: Name of the Constitutional Kernel to use (Default: `general`).
*   `--template`: Name of the Identity Template to use (Default: `general`).
*   `--vault`: Path to the provenance log database (Default: `./ct_provenance.db`).
*   `--policy-environment`: Policy environment override (`dev`, `test`, `prod`) (Default: `prod`).
*   `--judge-provider`: LLM provider for L2/L3 judge calls (Default: `openai`).
*   `--judge-model`: Specific model ID for L2/L3 judge calls.
*   `--tool-guard-mode`: `observe` (default) only records tool-call findings; `enforce` blocks a response that contains a high-severity tool call. Env: `CT_TOOL_GUARD_MODE`. See [Tool-Call Guard](../concepts/tool-call-guard.md).
*   `--tool-guard-allow-host`: Extra host that tool-call arguments may reach without the conversation naming it. Repeatable.
*   `--tool-guard-judge-model`: LiteLLM model that reviews suspicious tool calls (Default: off). Recommended: `openai/gemma-4-E4B-it`. Env: `CT_TOOL_GUARD_JUDGE_MODEL`.
*   `--tool-guard-judge-api-base`: API base of the judge's OpenAI-compatible server, e.g. `http://localhost:11434/v1`.
*   `--tool-guard-judge-fail-closed`: Block suspicious calls when the judge gives no usable answer (Default: let them through).

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

### `proxy`
Runs a local reverse proxy between an agent harness and its model API. Point the harness's base URL at the proxy and nothing else changes. Every model response's tool calls are reviewed by the [Tool-Call Guard](../concepts/tool-call-guard.md), and each verdict is written to the vault.

The proxy covers OpenAI Chat Completions, OpenAI Responses API and Anthropic Messages request bodies, streamed or not. Other requests are passed through untouched.

> [!WARNING]
> The proxy has no authentication. It binds to loopback only (`127.0.0.1`, `localhost` or `::1`) and refuses any other `--host`.

**Usage:**
```bash
ct-toolkit proxy --upstream https://api.openai.com --port 8788 --tool-guard-mode enforce

# Then set the harness's base URL to http://127.0.0.1:8788 (the request path is appended to --upstream)
```

**Options:**
*   `--upstream`: Model API base URL, e.g. `https://api.openai.com/v1` (Required).
*   `--host`: Loopback address to listen on (Default: `127.0.0.1`).
*   `--port`: Port to listen on (Default: `8788`).
*   `--tool-guard-mode`: `observe` (default) records findings; `enforce` removes calls with a high-severity finding from the response before the harness sees them. Env: `CT_TOOL_GUARD_MODE`.
*   `--vault`: Provenance log that receives the verdicts (Default: `./ct_provenance.db`).
*   `--tool-guard-allow-host`: Extra host tool calls may reach without the conversation naming it. Repeatable.
*   `--tool-guard-judge-model`: LiteLLM model that reviews suspicious calls (Default: off). Recommended: `openai/gemma-4-E4B-it`. Env: `CT_TOOL_GUARD_JUDGE_MODEL`.
*   `--tool-guard-judge-api-base`: API base of the judge's OpenAI-compatible server, e.g. `http://localhost:11434/v1`.
*   `--tool-guard-judge-fail-closed`: Block suspicious calls when the judge gives no usable answer (Default: let them through).

---

### `scrub`
Redacts credentials, email addresses, home directory names (including `C:\Users\<name>`), public IPv4 addresses and secret-looking environment values from agent trajectories (JSON, JSON lines or plain text). A dry run unless `--out` is given. Redaction is best-effort: read the report before publishing a trajectory.

**Usage:**
```bash
# Dry run: report only
ct-toolkit scrub ./trajectories --show

# Write scrubbed copies
ct-toolkit scrub ./trajectories --out ./trajectories_clean --redact my-internal-name
```

**Options:**
*   `PATH...`: Trajectory files or directories (Required).
*   `--out`: Write scrubbed copies here, keeping the relative paths. Without it, nothing is written.
*   `--redact`: Literal value to mask everywhere. Repeatable.
*   `--no-env`: Don't mask the values of secret-looking environment variables (names containing `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD` or `CREDENTIAL`, with values of 8 or more characters).
*   `--show`: List each hit with its masked preview.

---

### `tool-guard`
Inspects the tool calls of a model response and the decisions recorded in a provenance vault. No model is needed, except for the optional judge.

#### `tool-guard check`
Reviews the tool calls in a saved response. Exits with code `1` if any call has a high-severity finding, in either mode, so it can gate a CI job.

```bash
ct-toolkit tool-guard check response.json --mode enforce
```

The input file is JSON with a `response` key (the model's raw response) and optional `tools`, `message`, `history` and `system` keys, which give the declared tools and the conversation.

**Options:**
*   `FILE`: JSON file to review (Required).
*   `--mode`: `observe` (default) or `enforce`. Env: `CT_TOOL_GUARD_MODE`. The exit code does not depend on it.
*   `--judge-model`: LiteLLM model that reviews suspicious calls (Default: off). Recommended: `openai/gemma-4-E4B-it`.
*   `--judge-api-base`: API base of the judge's OpenAI-compatible server, e.g. `http://localhost:11434/v1`.

#### `tool-guard list`
Lists the tool-call decisions in a vault, filtered, sorted and paged.

```bash
ct-toolkit tool-guard list --vault ./ct_provenance.db --verdict blocked --sort time --order desc
```

**Options:**
*   `--vault`: Provenance vault to read (Default: `./ct_provenance.db`).
*   `--verdict`: Only `passed`, `flagged` or `blocked`.
*   `--tool`: Tool name contains this text.
*   `--search`: Matches the tool, findings, judge or model.
*   `--sort`: `time` (default), `tool`, `verdict` or `mode`.
*   `--order`: `desc` (default) or `asc`.
*   `--page`: Page to show (Default: `1`).
*   `--page-size`: Rows per page, 1 to 200 (Default: `10`).

Rows carry the tool name, verdict, mode, findings, judge answer and model. They do not carry the call's arguments: the vault stores only hashes of the call text.

#### `tool-guard stats`
Counts the decisions in a vault by verdict and by tool.

```bash
ct-toolkit tool-guard stats --vault ./ct_provenance.db
```

**Options:**
*   `--vault`: Provenance vault to read (Default: `./ct_provenance.db`).

#### `tool-guard show`
Shows one decision. The id can be a unique prefix of the id listed by `tool-guard list`.

```bash
ct-toolkit tool-guard show 3f2a9c1e --vault ./ct_provenance.db
```

**Options:**
*   `ENTRY_ID`: Record id, or a unique prefix of it (Required).
*   `--vault`: Provenance vault to read (Default: `./ct_provenance.db`).

#### `tool-guard config`
Shows or changes the tool-call guard settings file. The proxy, the guardrail server and the dashboard read the same file, and the dashboard's **Tool-Call Guard Settings** panel edits it too.

The file is `./tool_guard_settings.json` unless `--settings` or `CT_TOOL_GUARD_SETTINGS` names another one. Explicit command-line flags (for example `--tool-guard-mode` on `serve` or `proxy`) override the file for that process.

```bash
ct-toolkit tool-guard config show
ct-toolkit tool-guard config set --mode enforce --allow-host api.example.com
ct-toolkit tool-guard config set --judge-model openai/gemma-4-E4B-it --judge-api-base http://localhost:8090/v1
ct-toolkit tool-guard config set --clear-judge
```

**Options (`set`):**
*   `--mode`: `observe` or `enforce`.
*   `--allow-host`: Extra allowed host. Repeatable; replaces the list.
*   `--judge-model`, `--judge-api-base`: Judge model and its OpenAI-compatible base URL.
*   `--clear-judge`: Turn the judge off.
*   `--fail-closed` / `--fail-open`: Block or let through suspicious calls when the judge gives no answer.

Changes made while `proxy` or `serve` runs take effect on the next request. The API key of a judge is never stored in the file; it comes from the environment.

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
