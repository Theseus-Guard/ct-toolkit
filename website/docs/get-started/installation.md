# Installation

## Requirements

- **Python** 3.11 or later
- **pip** or **uv** package manager

---

## Basic installation

```bash
pip install ct-toolkit
```

This installs CT Toolkit's lightweight core dependencies. For CLI tools, the Web Audit Dashboard, PostgreSQL vaults, local ONNX embeddings, or OpenTelemetry, install the desired optional extras below or use `ct-toolkit[all]`:

```bash
pip install "ct-toolkit[all]"
```

---

## Feature & Framework Extras

Install optional extras based on the features and frameworks you use:

=== "CLI & Web Dashboard"

    ```bash
    pip install "ct-toolkit[cli,server]"
    ```

    Enables `ct-toolkit status`, `ct-toolkit dashboard --web` (Interactive Web UI), `ct-toolkit audit`, and `ct-toolkit serve` (`click`, `rich`, `fastapi`, `uvicorn`).

=== "Enterprise (PostgreSQL) & Observability (OTel)"

    ```bash
    pip install "ct-toolkit[enterprise,observability]"
    ```

    Enables SQLAlchemy / PostgreSQL provenance vaults (`vault_url`) and OpenTelemetry tracing/metrics (`opentelemetry-api`, `opentelemetry-sdk`).

=== "Local Embeddings (FastEmbed)"

    ```bash
    pip install "ct-toolkit[embeddings]"
    ```

    Enables local ONNX embeddings (`fastembed`) for offline Contrastive L1 divergence analysis without external API calls.

=== "LangChain / CrewAI / AutoGen"

    ```bash
    pip install "ct-toolkit[langchain,crewai,autogen]"
    ```

    Enables `TheseusChatModel`, `TheseusLangChainCallback`, `TheseusCrewMiddleware`, and `TheseusAutoGenMiddleware`.

=== "ML / Fine-tuning"

    ```bash
    pip install "ct-toolkit[ml]"
    ```

    Enables `DivergencePenaltyLoss` for PyTorch training loops (`torch >= 2.0`).

=== "All extras"

    ```bash
    pip install "ct-toolkit[all]"
    ```

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
print(ct_toolkit.__version__)  # e.g. 0.4.1
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
    CT Toolkit's core does **not** require PyTorch, FastAPI, or SQLAlchemy unless you opt into `[ml]`, `[server]`, or `[enterprise]`.

---

## Next: Quickstart

[:octicons-arrow-right-24: Build your first protected agent](quickstart.md)
