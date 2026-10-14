# Local Models

CT Toolkit works with any OpenAI-compatible endpoint, including **Ollama** and **LM Studio**. No API key required.

---

## Ollama

### 1. Install and start Ollama

=== "macOS"

    ```bash
    brew install ollama
    ollama serve
    ```

=== "Linux"

    ```bash
    curl -fsSL https://ollama.ai/install.sh | sh
    ollama serve
    ```

=== "Windows"

    Download from [ollama.ai](https://ollama.ai) and run the installer.

### 2. Pull a model

```bash
ollama pull llama3          # Meta Llama 3 8B
ollama pull mistral         # Mistral 7B
ollama pull qwen2.5:7b      # Alibaba Qwen 2.5
```

### 3. Use with CT Toolkit

```python
import openai
from ct_toolkit import TheseusWrapper

# Point to local Ollama endpoint (/v1 and non-/v1 are both supported)
client = openai.OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama",  # Required by the SDK, not validated by Ollama
)

wrapper = TheseusWrapper(client=client, provider="ollama")

response = wrapper.chat(
    "What are your core values?",
    model="llama3"
)

print(response.content)
print(f"Divergence: {response.divergence_score:.4f}")
```

!!! tip "Using Ollama's embedding API for better L1 accuracy"
If you have an embedding model pulled, pass it explicitly:

    ```python
    from ct_toolkit import TheseusWrapper, WrapperConfig

    embedding_client = openai.OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
    )

    config = WrapperConfig(
        embedding_client=embedding_client,
        embedding_model="nomic-embed-text",  # ollama pull nomic-embed-text
    )

    wrapper = TheseusWrapper(client=embedding_client, provider="ollama", config=config)
    ```

---

## LM Studio

### 1. Download and start LM Studio

Download from [lmstudio.ai](https://lmstudio.ai). Load a model and start the **Local Server** (default port: `1234`).

### 2. Use with CT Toolkit

```python
import openai
from ct_toolkit import TheseusWrapper, WrapperConfig

LM_STUDIO_URL = "http://localhost:1234/v1"
LLM_MODEL     = "unsloth/qwen3.5-9B"
EMBED_MODEL   = "text-embedding-qwen3-embedding-0.6b"

base_client = openai.OpenAI(
    base_url=LM_STUDIO_URL,
    api_key="lm-studio",
)

config = WrapperConfig(
    template="general",
    embedding_client=base_client,
    embedding_model=EMBED_MODEL,
    divergence_l1_threshold=0.15,
    log_requests=True,
)

wrapper = TheseusWrapper(client=base_client, config=config)

response = wrapper.chat(
    "Explain your constitutional constraints.",
    model=LLM_MODEL,
)

print(response.content)
print(f"Divergence : {response.divergence_score:.4f} ({response.divergence_tier})")
```

---

## Without any embedding client

If you don't have an embedding model available, CT Toolkit automatically falls back to a **keyword frequency vector** — no API calls, no external dependencies:

```python
from ct_toolkit import TheseusWrapper

# No embedding_client specified — keyword fallback is used automatically
wrapper = TheseusWrapper(provider="openai")
```

!!! note "Embedding quality"
The keyword fallback is sufficient for basic drift detection. For production deployments or high-stakes systems, we recommend providing a real embedding client for more accurate L1 scores.

---

## Tested local models

| Model                  | Provider  | Notes                             |
| :--------------------- | :-------- | :-------------------------------- |
| `llama3`               | Ollama    | Fully tested, good baseline       |
| `mistral`              | Ollama    | Fast, good for L2/L3 judge paths  |
| `qwen2.5:7b`           | Ollama    | Strong reasoning chain support    |
| `unsloth/qwen3.5-9B` | LM Studio | Tested with `<think>` tag parsing |
| `phi3`                 | Ollama    | Lightweight, good for sub-agents  |

---

## Provider string reference

When using `TheseusWrapper` without a client instance:

```python
# These are equivalent
wrapper = TheseusWrapper(provider="openai")
wrapper = TheseusWrapper(provider="anthropic")
wrapper = TheseusWrapper(provider="ollama")
```

For local endpoints, pass the client directly:

```python
import openai

local_client = openai.OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
wrapper = TheseusWrapper(client=local_client, provider="ollama")
```
