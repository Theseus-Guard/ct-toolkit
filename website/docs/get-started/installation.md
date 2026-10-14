# Installation

## Requirements

- **Python** 3.11 or later
- **pip** or **uv** package manager

---

## Basic installation

```bash
pip install ct-toolkit
```

This installs CT Toolkit with all core dependencies. No additional packages are needed to get started with OpenAI, Anthropic, or Ollama.

---

## Framework extras

Install optional extras based on the frameworks you use:

=== "LangChain"

    ```bash
    pip install "ct-toolkit[langchain]"
    ```

    Enables `TheseusChatModel` and `TheseusLangChainCallback`.
    Requires `langchain-core >= 1.2`.

=== "CrewAI"

    ```bash
    pip install "ct-toolkit[crewai]"
    ```

    Enables `TheseusCrewMiddleware`.
    Requires `crewai >= 1.10`.

=== "AutoGen"

    ```bash
    pip install "ct-toolkit[autogen]"
    ```

    Enables `TheseusAutoGenMiddleware`.
    Requires `pyautogen >= 0.4`.

=== "ML / Fine-tuning"

    ```bash
    pip install "ct-toolkit[ml]"
    ```

    Enables `DivergencePenaltyLoss` for PyTorch training loops.
    Requires `torch >= 2.0`.

=== "All extras"

    ```bash
    pip install "ct-toolkit[langchain,crewai,autogen,ml]"
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
pip install -e ".[dev,langchain,crewai,autogen]"
```

Run the tests:

```bash
pytest tests/ -v
# 242 passed, 8 skipped
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

    pip install ct-toolkit
    ```

=== "uv"

    ```bash
    uv venv
    source .venv/bin/activate  # or .venv\Scripts\activate on Windows
    uv add ct-toolkit
    ```

=== "conda"

    ```bash
    conda create -n myenv python=3.11
    conda activate myenv
    pip install ct-toolkit
    ```

---

## Verifying the installation

```python
import ct_toolkit
print(ct_toolkit.__version__)  # e.g. 0.3.3
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

CT Toolkit's core dependencies:

| Package        | Purpose                                                   |
| :------------- | :-------------------------------------------------------- |
| `litellm`      | Unified transport layer (OpenAI, Anthropic, Ollama, etc.) |
| `instructor`   | Structured L2/L3 judge responses                          |
| `numpy`        | L1 embedding calculations                                 |
| `pyyaml`       | Kernel and template YAML loading                          |
| `pydantic`     | Config validation                                         |
| `cryptography` | HMAC key management                                       |
| `jinja2`       | System prompt templating                                  |

!!! note "No heavy ML dependency"
CT Toolkit's core does **not** require PyTorch or any ML framework. The `[ml]` extra is only needed for training-time `DivergencePenaltyLoss`.

---

## Next: Quickstart

[:octicons-arrow-right-24: Build your first protected agent](quickstart.md)
