# Installation

## Requirements

- **Python** 3.11 or later
- **pip** or **uv** package manager

---

## Basic installation

```bash
pip install ct-toolkit
```

This installs the core: the `ct-toolkit` command, the Web Audit Dashboard, the tool-call guard and the tool-call proxy. No extra is needed for these.

For PostgreSQL vaults, OpenTelemetry, framework middleware and the other features below, install the extras or use `ct-toolkit[all]`. `[all]` does not include the ML stack (`[ml]`, which pulls in PyTorch) or AutoGen (`[autogen]`), because both are large; install them separately when you need them:

```bash
pip install "ct-toolkit[all]"
```

---

## Feature & Framework Extras

Install optional extras based on the features and frameworks you use:

=== "CLI & Web Dashboard"

    Included in the core install. There is nothing to add.

    Provides `ct-toolkit status`, `ct-toolkit dashboard --web` (interactive Web UI, including the tool-call guard settings), `ct-toolkit audit`, `ct-toolkit serve`, `ct-toolkit proxy` and `ct-toolkit tool-guard`.

=== "PostgreSQL vaults & Observability (OTel)"

    ```bash
    pip install "ct-toolkit[postgres,observability]"
    ```

    Enables PostgreSQL provenance vaults (`--vault-type postgres`, `vault_url`) and OpenTelemetry tracing/metrics (`opentelemetry-api`, `opentelemetry-sdk`).

=== "Local Embeddings (FastEmbed)"

    ```bash
    pip install "ct-toolkit[embeddings]"
    ```

    Enables local ONNX embeddings (`fastembed`) for offline Contrastive L1 divergence analysis without external API calls.

=== "LangChain / AutoGen"

    ```bash
    pip install "ct-toolkit[langchain,autogen]"
    ```

    CrewAI is not available in extras at the moment: its dependency conflicts with the current litellm pin.

    Enables `TheseusChatModel`, `TheseusLangChainCallback`, `TheseusCrewMiddleware`, and `TheseusAutoGenMiddleware`.

=== "ML / Fine-tuning"

    ```bash
    pip install "ct-toolkit[ml]"
    ```

    Enables `DivergencePenaltyLoss` for PyTorch training loops, and fastembed-based embeddings. Requires `torch >= 2.13`.

=== "All extras"

    ```bash
    pip install "ct-toolkit[all]"
    ```

    PostgreSQL, OpenTelemetry, structured outputs, vision, Ollama and LangChain. Not included: `[ml]` (PyTorch) and `[autogen]`.

---

## Development installation

For contributors or if you want to run the test suite:

```bash
git clone https://github.com/Theseus-Guard/ct-toolkit
cd ct-toolkit

# Using uv (recommended)
uv sync --all-extras

# Or with pip
pip install -e ".[dev,all]"
```

Run the tests:

```bash
pytest tests/ -v
# 426 passed, 3 skipped
```

---

## Virtual environment (recommended)

Always use a virtual environment in production:

=== "venv"

    ```bash
    python -m venv .venv

    # Activate — macOS / Linux
    source .venv/bin/activate

    # Activate — Windows
    .venv\Scripts\activate

    pip install "ct-toolkit[all]"
    ```

=== "uv"

    ```bash
    uv venv
    source .venv/bin/activate  # or .venv\Scripts\activate on Windows
    uv add "ct-toolkit[all]"
    ```

=== "conda"

    ```bash
    conda create -n myenv python=3.11
    conda activate myenv
    pip install "ct-toolkit[all]"
    ```

---

## Verifying the installation

```python
import ct_toolkit
print(ct_toolkit.__version__)  # e.g. 0.4.4
```

Or run a quick sanity check:

```python
from ct_toolkit import TheseusWrapper, ConstitutionalKernel

kernel = ConstitutionalKernel.default()
print(f"Kernel: {kernel.name}")
print(f"Anchors: {len(kernel.anchors)}")
print(f"Commitments: {len(kernel.commitments)}")
```

Expected output:

```
Kernel: default
Anchors: 4
Commitments: 4
```

---

## Dependencies overview

CT Toolkit's lightweight core dependencies:

| Package        | Purpose                                                   |
| :------------- | :-------------------------------------------------------- |
| `litellm`      | Unified transport layer (OpenAI, Anthropic, Ollama, etc.) |
| `instructor`   | Structured L2/L3 judge responses                          |
| `numpy`        | L1 embedding calculations                                 |
| `pyyaml`       | Kernel and template YAML loading                          |
| `pydantic`     | Config validation                                         |
| `cryptography` | HMAC key management                                       |
| `jinja2`       | System prompt templating                                  |

!!! note "Modular Optional Extras"
    The core does **not** require PyTorch. It is needed only for `[ml]`, which provides the divergence penalty loss. The CLI, the web dashboard (FastAPI, Uvicorn) and the tool-call guard are part of the core install.

---

## Next: Quickstart

[:octicons-arrow-right-24: Build your first protected agent](quickstart.md)
