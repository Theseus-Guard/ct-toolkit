# WrapperConfig Reference

`WrapperConfig` is the configuration object for `TheseusWrapper`. All fields have sensible defaults — most use cases only need 2–3 overrides.

```python
from ct_toolkit import TheseusWrapper, WrapperConfig

config = WrapperConfig(
    template="finance",
    kernel_name="finance",
    divergence_l1_threshold=0.12,
    log_requests=True,
)

wrapper = TheseusWrapper(provider="openai", config=config)
```

---

## All parameters

### Identity & Semantic Rule Validation

| Parameter                            | Type                           | Default     | Description                                                                                 |
| :----------------------------------- | :----------------------------- | :---------- | :------------------------------------------------------------------------------------------ |
| `template`                           | `str`                          | `"general"` | Identity template name. Defines positive reference and negative anchor vectors.             |
| `kernel_name`                        | `str`                          | `"default"` | Constitutional Kernel to load. Built-ins: `default`, `defense`, `finance`.                  |
| `kernel_path`                        | `str \| Path \| None`          | `None`      | Explicit path to a custom kernel YAML. Overrides `kernel_name`.                             |
| `project_root`                       | `str \| Path \| None`          | `None`      | Path to your project root. CT Toolkit looks for `config/*.yaml` here.                       |
| `parent_kernel`                      | `ConstitutionalKernel \| None` | `None`      | Propagated kernel from a parent agent. Its anchors become read-only axioms.                 |
| `rule_conflict_similarity_threshold` | `float`                        | `0.78`      | Cosine similarity threshold for semantic rule conflict detection when embeddings are used.  |

### Divergence thresholds & Background Circuit Breaker

| Parameter                      | Type    | Default | Description                                                                                 |
| :----------------------------- | :------ | :------ | :------------------------------------------------------------------------------------------ |
| `divergence_l1_threshold`      | `float` | `0.15`  | L1 Contrastive ECS score above which an `l1_warning` is issued.                             |
| `divergence_l2_threshold`      | `float` | `0.30`  | L1 score above which the L2 LLM-as-judge path is triggered.                                 |
| `divergence_l3_threshold`      | `float` | `0.50`  | L1 score above which the L3 ICM battery is triggered.                                       |
| `l3_background_execution`      | `bool`  | `True`  | Run L3 ICM probe batteries asynchronously in the background without blocking latency.       |
| `l3_circuit_breaker_threshold` | `int`   | `2`     | Consecutive failed L3 probe runs required to trip the circuit breaker and block requests.   |
| `shadow_background_execution`  | `bool`  | `True`  | Run Staged Approval shadow requests asynchronously in the background.                       |

!!! tip "Stricter thresholds for critical systems"
    For medical, financial, or defense applications, use tighter thresholds:
    ```python
    config = WrapperConfig(
        divergence_l1_threshold=0.10,
        divergence_l2_threshold=0.20,
        divergence_l3_threshold=0.40,
    )
    ```

### Judge and embedding clients

| Parameter            | Type          | Default                    | Description                                                                                        |
| :------------------- | :------------ | :------------------------- | :------------------------------------------------------------------------------------------------- |
| `judge_client`       | `Any`         | `None`                     | Separate LLM client for L2/L3 analysis.                                                            |
| `judge_provider`     | `str \| None` | `None`                     | Explicit provider for judge calls (for example, `"ollama"`).                                       |
| `judge_model`        | `str \| None` | `None`                     | Explicit model for judge calls (shared by L2 and L3 judge paths).                                  |
| `embedding_provider` | `str \| None` | `None`                     | Embedding backend: `"litellm"`, `"ollama"`, `"fastembed"`, `"openai"`, or `"local"`.               |
| `embedding_client`   | `Any`         | `None`                     | Client for real embedding API calls (L1). Falls back to keyword vectors if `None`.                 |
| `embedding_model`    | `str`         | `"text-embedding-3-small"` | Embedding model name (e.g. `"BAAI/bge-small-en-v1.5"` for FastEmbed or `"nomic-embed-text"`).      |
| `strict_embedding`   | `bool`        | `False`                    | If `True`, raises `RuntimeError` when embedding API fails instead of falling back.                 |

### Provenance, Database Vault & OpenTelemetry

| Parameter            | Type          | Default                | Description                                                                             |
| :------------------- | :------------ | :--------------------- | :-------------------------------------------------------------------------------------- |
| `vault_type`         | `str`         | `"local"`              | Storage backend (`"local"` for SQLite or `"sqlalchemy"` when `vault_url` is set).       |
| `vault_path`         | `str`         | `"./ct_provenance.db"` | Path to the SQLite provenance database.                                                 |
| `vault_url`          | `str \| None` | `None`                 | SQLAlchemy database URL (e.g. `"postgresql+psycopg2://user:pass@host:5432/db"`).        |
| `log_requests`       | `bool`        | `True`                 | Whether to write every interaction to the provenance log.                               |
| `auto_inject_kernel` | `bool`        | `True`                 | Whether to inject kernel rules into the system prompt automatically.                    |
| `otel_enabled`       | `bool`        | `False`                | Emit OpenTelemetry traces (`ct.chat`, `ct.divergence.*`) and metrics when enabled.      |

### Auto-correction loop

| Parameter                | Type   | Default | Description                                                |
| :----------------------- | :----- | :------ | :--------------------------------------------------------- |
| `auto_correction`        | `bool` | `False` | Enable the L2→L1 self-correction loop.                     |
| `max_correction_retries` | `int`  | `1`     | Maximum retry attempts before returning the last response. |

```python
config = WrapperConfig(
    auto_correction=True,
    max_correction_retries=2,
    judge_client=openai.OpenAI(base_url="http://localhost:11434/v1", api_key="ollama"),
    judge_provider="ollama",
    judge_model="mistral",
)
```

### Alerting

| Parameter              | Type                             | Default | Description                                                      |
| :--------------------- | :------------------------------- | :------ | :--------------------------------------------------------------- |
| `drift_alert_callback` | `Callable[[dict], None] \| None` | `None`  | Called when a drift event or context compression failure occurs. |

```python
def my_alert(payload: dict):
    print(f"ALERT: drift={payload['similarity']:.2f}")
    # send to Slack, PagerDuty, etc.

config = WrapperConfig(drift_alert_callback=my_alert)
```

### Elasticity scheduling

| Parameter                   | Type                                 | Default | Description                                                       |
| :-------------------------- | :----------------------------------- | :------ | :---------------------------------------------------------------- |
| `elasticity_max_thresholds` | `tuple[float, float, float] \| None` | `None`  | Maximum `(L1, L2, L3)` thresholds an experienced agent can reach. |
| `elasticity_growth_rate`    | `float \| None`                      | `None`  | Rate at which divergence tolerance increases with experience.     |
| `risk_profile`              | `RiskProfile \| None`                | `None`  | Structural risk profile affecting elasticity growth rate.         |

```python
from ct_toolkit.divergence.scheduler import RiskProfile

config = WrapperConfig(
    elasticity_max_thresholds=(0.25, 0.45, 0.70),
    elasticity_growth_rate=0.001,
    risk_profile=RiskProfile(has_tool_calling=True, mcp_server_count=2),
)
```

### Context Compression (v0.3.6+)

| Parameter                       | Type    | Default | Description                                                                             |
| :------------------------------ | :------ | :------ | :-------------------------------------------------------------------------------------- |
| `compression_passive_detection` | `bool`  | `True`  | Automatically detect silent provider-side history compression via shrinkage heuristics. |
| `compression_threshold`         | `float` | `0.80`  | Similarity score floor (0.0 to 1.0) for summarization drift analysis.                   |

```python
config = WrapperConfig(
    compression_passive_detection=True,
    compression_threshold=0.88,
    drift_alert_callback=my_handler  # Triggered on drift
)
```

### Staged Approval (Cooldown) (v0.3.8+)

| Parameter                      | Type  | Default | Description                                     |
| :----------------------------- | :---- | :------ | :---------------------------------------------- |
| `endorsement_cooldown_base`    | `int` | `300`   | Minimum cooldown duration in seconds (5 min).   |
| `endorsement_cooldown_max`     | `int` | `3600`  | Maximum cooldown duration (1 hour).             |
| `endorsement_no_probe_penalty` | `int` | `600`   | Penalty added if no probes are found (+10 min). |

```python
config = WrapperConfig(
    endorsement_cooldown_base=600, # 10 min
    endorsement_no_probe_penalty=1200, # +20 min
)
```

### Capability Handshake & Policy Control (v0.3.20+)

| Parameter                            | Type   | Default                        | Description                                                                      |
| :----------------------------------- | :----- | :----------------------------- | :------------------------------------------------------------------------------- |
| `capability_cache_file`              | `str`  | `"config/llm_capability.yaml"` | Path to startup capability cache and policy registry YAML.                       |
| `capability_refresh_interval_s`      | `int`  | `3600`                         | Refresh interval for provider/model capability refresh.                          |
| `capability_enable_active_handshake` | `bool` | `False`                        | If enabled, attempts lightweight active capability checks via provider metadata. |
| `capability_force_refresh`           | `bool` | `False`                        | Forces capability refresh on access instead of waiting for interval expiry.      |
| `policy_role`                        | `str`  | `"main"`                       | Role selector used by policy resolution (`main`, `sub`, `judge`, `l3`).          |
| `policy_environment`                 | `str`  | `"prod"`                       | Environment selector for policy overrides (`dev`, `test`, `prod`).               |

```python
config = WrapperConfig(
    capability_cache_file="config/llm_capability.yaml",
    policy_role="sub",
    policy_environment="test",
)
```

---

## Rigorous Analysis Mode

Enables simultaneous L1, L2, and L3 scanning to ensure maximum identity alignment:

`rigorous_mode` is the preferred flag. `enterprise_mode` is still accepted for backward compatibility.

```python
config = WrapperConfig(
    rigorous_mode=True,
    judge_provider="openai",
    judge_model="gpt-4o-mini",
)
```

In rigorous mode, a combined risk score is calculated:

```
risk = l1_score
     + (l2_confidence × 0.4  if misaligned)
     + ((1 - l3_health) × 0.4  if L3 ran)

action_required = risk >= l3_threshold OR L3 not healthy
```
