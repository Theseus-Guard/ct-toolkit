# TheseusWrapper

The main entry point. A transparent proxy that wraps any LLM client with identity protection.

## Initialization

```python
from ct_toolkit import TheseusWrapper, WrapperConfig

# Simplest form — provider-only
wrapper = TheseusWrapper(provider="openai")

# With full config
wrapper = TheseusWrapper(
    provider="openai",
    config=WrapperConfig(
        template="finance",
        kernel_name="finance",
    )
)

# Provider string only — client created from env vars
wrapper = TheseusWrapper(provider="openai")
```

## Methods

### `chat(message, *, model, system, history, stream=False, **kwargs) → CTResponse | Iterator[str]`

Send a message and receive an enriched response (or a token generator when `stream=True`).

```python
response = wrapper.chat(
    "What are your core values?",
    model="gpt-4o-mini",
    system="Additional context.",
    history=[{"role": "user", "content": "Hello"}],
)

# Streaming mode — divergence & provenance run automatically when stream completes
for chunk in wrapper.chat("Explain your axioms.", stream=True):
    print(chunk, end="", flush=True)
```

### `await achat(message, *, model, system, history, stream=False, **kwargs) → CTResponse | AsyncIterator[str]`

Asynchronous counterpart to `chat()`. Offloads synchronous provider SDK calls via `asyncio.to_thread` when native async clients aren't used, and runs `DivergenceEngine.aanalyze()` without blocking the event loop.

```python
response = await wrapper.achat("What are your core values?", model="gpt-4o-mini")

# Async streaming mode
async for chunk in await wrapper.achat("Explain your axioms.", stream=True):
    print(chunk, end="", flush=True)
```

### `validate_user_rule(rule_text) → None`

Validate a rule against the kernel using both deterministic keyword matching and semantic embedding similarity (`conflict_similarity_threshold`). Raises `AxiomaticViolationError` or `PlasticConflictError` on conflict.

### `endorse_rule(rule_text, *, operator_id, approval_channel, commitment_new_value) → EndorsementRecord`

Initiate the Reflective Endorsement flow for a plastic conflict.

### `export_provenance_log() → list[dict]`

Verify chain integrity and export all log entries.

### `resolve_llm_policy(model=None, role=None) → dict[str, Any]`

Resolve effective role policy from startup capability registry + environment overrides.

### `propagate_policy_metadata(model=None, role=None) → dict[str, Any]`

Return middleware-friendly policy metadata payload:

```python
{
    "role": "sub",
    "environment": "test",
    "effective": {
        "tool_call": False,
        "reasoning": True,
        "multimodal": False,
        ...
    }
}
```

### `propagate_headers(*, secret_key=None, model=None, role=None) → dict[str, str]`

Generate cryptographically signed HTTP headers for sub-agent kernel & policy propagation across network boundaries:

- `X-CT-Kernel` (base64-encoded canonical JSON of the parent kernel)
- `X-CT-Timestamp` (Unix epoch seconds for replay-window protection)
- `X-CT-Signature` (HMAC-SHA256 signature over `<timestamp>.<base64_kernel>` using `secret_key` or `CT_PROPAGATION_SECRET`)
- `X-CT-Policy-Role`
- `X-CT-Policy-Environment`
- `X-CT-Policy` (base64 JSON payload)

## Properties

| Property            | Type                      | Description                            |
| :------------------ | :------------------------ | :------------------------------------- |
| `kernel`            | `ConstitutionalKernel`    | The active kernel                      |
| `compatibility`     | `CompatibilityResult`     | Template + kernel compatibility result |
| `divergence_engine` | `DivergenceEngine`        | The active divergence engine           |
| `compression_guard` | `ContextCompressionGuard` | The active context compression guard   |
| `staged_manager`    | `StagedUpdateManager`     | Tracks staged (cooldown) endorsements  |

## CTResponse fields

| Field                | Type            | Description                                              |
| :------------------- | :-------------- | :------------------------------------------------------- |
| `content`            | `str`           | Response text                                            |
| `provider`           | `str`           | Provider used                                            |
| `model`              | `str`           | Model used                                               |
| `divergence_score`   | `float \| None` | L1 score (0.0–1.0)                                       |
| `divergence_tier`    | `str \| None`   | `ok`, `l1_warning`, `l2_judge`, `l3_icm`, `critical`     |
| `provenance_id`      | `str \| None`   | UUID of the log entry                                    |
| `raw_response`       | `Any`           | Raw SDK response object                                  |
| `sandbox_divergence` | `float \| None` | Divergence score of the shadow/sandbox agent (if staged) |
