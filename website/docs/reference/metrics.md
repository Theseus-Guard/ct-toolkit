# Metrics & Monitoring

Thread-safe metrics collection for monitoring and observability.

The MetricsCollector provides counters, gauges, and histograms to track LLM request performance, error rates, divergence scores, and circuit breaker state.

## Basic Usage

```python
from ct_toolkit.utils.metrics import get_metrics_collector

metrics = get_metrics_collector()

# Record metrics
metrics.increment("llm.requests.total", tags={"provider": "openai"})
metrics.record("llm.latency.seconds", 0.5, tags={"model": "gpt-4"})

# Timer context manager
with metrics.timer("llm.latency", tags={"model": "gpt-4"}):
    response = wrapper.chat("Hello!")

# Export metrics
all_metrics = metrics.get_all()
```

## Metric Types

### Counter

A monotonically increasing counter for events like request counts.

```python
metrics.counter("llm.requests", tags={"provider": "openai"})
metrics.increment("llm.requests", tags={"provider": "openai"})
```

### Gauge

A metric that can go up or down, useful for tracking current state like circuit breaker status.

```python
metrics.set_gauge("circuit_breaker.state", 1.0, tags={"name": "openai", "state": "open"})
metrics.increment_gauge("active.connections", tags={"provider": "openai"})
metrics.decrement_gauge("active.connections", tags={"provider": "openai"})
```

### Histogram

Tracks distribution of values (min, max, average), ideal for latencies and scores.

```python
metrics.histogram("llm.latency", tags={"model": "gpt-4"})
metrics.record("llm.latency", 0.5, tags={"model": "gpt-4"})
```

## Pre-defined LLM Metrics

| Metric Name | Type | Description |
|-------------|------|-------------|
| `llm.requests.total` | Counter | Total LLM requests |
| `llm.requests.success` | Counter | Successful LLM requests |
| `llm.requests.failure` | Counter | Failed LLM requests |
| `llm.requests.async.total` | Counter | Total async LLM requests |
| `llm.requests.async.success` | Counter | Successful async requests |
| `llm.requests.async.failure` | Counter | Failed async requests |
| `llm.requests.async.circuit_breaker` | Counter | Requests rejected by circuit breaker |
| `llm.latency.seconds` | Histogram | LLM request latency |
| `llm.latency.async.seconds` | Histogram | Async LLM request latency |
| `llm.errors.total` | Counter | Total LLM errors |
| `divergence.score` | Histogram | Identity divergence scores |
| `divergence.evaluations.total` | Counter | Divergence evaluations |
| `circuit_breaker.state` | Gauge | Circuit breaker state |

## Export Format

Metrics are exported as a dictionary suitable for Prometheus/OpenTelemetry integration:

```python
{
    "counters": {
        "ct_toolkit.llm.requests.total": {
            "value": 150,
            "tags": {"provider": "openai", "model": "gpt-4"}
        }
    },
    "gauges": {
        "ct_toolkit.circuit_breaker.state": {
            "value": 1.0,
            "tags": {"name": "openai", "state": "open"}
        }
    },
    "histograms": {
        "ct_toolkit.llm.latency.seconds": {
            "count": 150,
            "sum": 75.5,
            "min": 0.2,
            "max": 2.1,
            "avg": 0.503,
            "tags": {"model": "gpt-4"}
        }
    }
}
```

## Helper Methods

### `record_llm_request(provider, model, latency, success)`

Record a complete LLM request with timing:

```python
metrics.record_llm_request(
    provider="openai",
    model="gpt-4",
    latency=0.5,
    success=True
)
```

### `record_llm_error(provider, error_type, model)`

Record an LLM API error:

```python
metrics.record_llm_error(
    provider="openai",
    error_type="RateLimitError",
    model="gpt-4"
)
```

### `record_divergence_score(template, kernel, score)`

Record a divergence score:

```python
metrics.record_divergence_score(
    template="finance",
    kernel="finance",
    score=0.15
)
```

## Integration with TheseusWrapper

Metrics are automatically collected when using the wrapper with metrics enabled (default):

```python
from ct_toolkit.core.async_wrapper import AsyncTheseusWrapper

wrapper = AsyncTheseusWrapper(
    provider="openai",
    metrics_enabled=True,  # Enable automatic metrics collection
    enable_circuit_breaker=True,
)

# Metrics are automatically recorded
response = await wrapper.chat("Hello!", model="gpt-4")

# Get collected metrics
stats = wrapper.get_metrics()
```

---

## Native OpenTelemetry (`ct_toolkit.telemetry`)

In addition to the built-in thread-safe `MetricsCollector`, CT Toolkit includes first-class **OpenTelemetry** tracing and metrics via `ct_toolkit/telemetry.py`. When `opentelemetry-api` is not installed, all telemetry calls gracefully degrade to zero-overhead no-ops.

### Enabling OpenTelemetry

```bash
pip install "ct-toolkit[observability]"
```

```python
from ct_toolkit import TheseusWrapper, WrapperConfig

wrapper = TheseusWrapper(
    provider="openai",
    config=WrapperConfig(otel_enabled=True),
)
```

### Emitted Spans & Instruments

| Instrument / Span | Type | Description |
| :--- | :--- | :--- |
| `ct.chat` | Trace Span | Wraps each `chat()` / `achat()` call with provider, model, template, kernel, and divergence attributes |
| `ct.requests.total` | Counter | Total LLM requests processed through `TheseusWrapper` |
| `ct.divergence.l1.score` | Histogram | Distribution of L1 Embedding Cosine Similarity (ECS) divergence scores |
| `ct.divergence.alerts.total` | Counter | Total L1/L2/L3 divergence alerts triggered |
| `ct.circuit_breaker.trips.total` | Counter | Total L3 circuit breaker trips |