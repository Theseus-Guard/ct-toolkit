# Divergence Engine Reference

## DivergenceEngine

```python
from ct_toolkit.divergence.engine import DivergenceEngine

engine = DivergenceEngine(
    identity_layer=layer,
    kernel=kernel,
    template="general",
    l1_threshold=0.15,
    l2_threshold=0.30,
    l3_threshold=0.50,
    l3_background_execution=True,
    l3_circuit_breaker_threshold=2,
)

# Synchronous analysis
result = engine.analyze(request_text, response_text)

# Asynchronous analysis (offloads L2/L3 LLM calls without blocking event loop)
result = await engine.aanalyze(request_text, response_text)
```

### Non-Blocking L3 & Circuit Breaker

- **`l3_background_execution=True` (default):** When L3 ICM is triggered, the full probe battery runs in a background daemon thread (`ThreadPoolExecutor`) so user-facing latency is not blocked by 10+ sequential probe calls.
- **`l3_circuit_breaker_threshold=2`:** If consecutive background L3 runs fail or breach critical divergence thresholds, `engine.circuit_breaker_open` trips to `True` and subsequent `TheseusWrapper.chat()` / `achat()` requests raise `CriticalDivergenceError` immediately until `engine.reset_circuit_breaker()` is called.

!!! tip "Timeout & Retry"
    L2 Judge and L3 ICM calls are protected by automatic timeout and retry logic:
    
    - **L2 Judge:** 30-second timeout, 2 retries with exponential backoff
    - **L3 ICM:** 60-second timeout, 2 retries with exponential backoff
    
    Retries are performed only for transient errors (rate limit, timeout, connection error).

## DivergenceResult fields


| Field | Type | Description |
|:---|:---|:---|
| `tier` | `DivergenceTier` | Highest tier reached |
| `l1_score` | `float \| None` | ECS divergence score |
| `l2_verdict` | `str \| None` | `aligned`, `misaligned`, `uncertain` |
| `l2_confidence` | `float \| None` | Judge confidence (0–1) |
| `l2_reason` | `str \| None` | Judge explanation |
| `l3_report` | `ICMReport \| None` | Probe battery results |
| `action_required` | `bool` | Whether intervention is needed |
| `cascade_blocked` | `bool` | Whether to halt sub-agent propagation |

## LLMJudge

```python
from ct_toolkit.divergence.l2_judge import LLMJudge, JudgeVerdict

judge = LLMJudge(
    client=openai.OpenAI(),
    provider="openai",
    model="gpt-4o-mini",
)

result = judge.evaluate(
    request_text="user question",
    response_text="model response",
    kernel=kernel,
)

print(f"Verdict: {result.verdict}")
print(f"Confidence: {result.confidence}")
```

### Timeout & Retry Configuration

```python
# Module-level configuration
from ct_toolkit.divergence.l2_judge import JUDGE_TIMEOUT_SECONDS, JUDGE_MAX_RETRIES

print(f"Timeout: {JUDGE_TIMEOUT_SECONDS}s")  # 30 seconds
print(f"Max retries: {JUDGE_MAX_RETRIES}")    # 2
```

## ICMRunner

```python
from ct_toolkit.divergence.l3_icm import ICMRunner

runner = ICMRunner(
    client=openai.OpenAI(),
    provider="openai",
    kernel=kernel,
    template="general",
)

report = runner.run()
print(report.summary())
```

### Timeout & Retry Configuration

```python
from ct_toolkit.divergence.l3_icm import ICM_TIMEOUT_SECONDS, ICM_MAX_RETRIES

print(f"Timeout: {ICM_TIMEOUT_SECONDS}s")  # 60 seconds
print(f"Max retries: {ICM_MAX_RETRIES}")    # 2
```

## ElasticityScheduler

```python
from ct_toolkit.divergence.scheduler import ElasticityScheduler, RiskProfile

scheduler = ElasticityScheduler(
    base_thresholds=(0.15, 0.30, 0.50),
    max_thresholds=(0.25, 0.45, 0.70),
    growth_rate=0.001,
    risk_profile=RiskProfile(has_tool_calling=True, mcp_server_count=2),
)

l1, l2, l3 = scheduler.calculate_thresholds(interaction_count=500)
```
