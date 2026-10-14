# ConstitutionalKernel

## Loading

```python
from ct_toolkit import ConstitutionalKernel
from pathlib import Path

# Built-in kernels
kernel = ConstitutionalKernel.default()
kernel = ConstitutionalKernel.from_yaml(Path("ct_toolkit/kernels/defense.yaml"))

# From dict
kernel = ConstitutionalKernel.from_dict(data)
```

## Properties

| Property | Type | Description |
|:---|:---|:---|
| `name` | `str` | Kernel name |
| `anchors` | `list[AxiomaticAnchor]` | Immutable rules |
| `commitments` | `list[PlasticCommitment]` | Endorsable rules |

## Methods

### `validate_user_rule(rule_text, *, embedding_fn=None, similarity_threshold=0.75)` → raises on conflict

Checks `rule_text` against all `AxiomaticAnchor` and `PlasticCommitment` rules using:

1. **Keyword matching:** Fast deterministic substring scan.
2. **Semantic similarity:** When `embedding_fn` is provided (automatically wired by `TheseusWrapper`), computes cosine similarity between the user rule embedding and each anchor/commitment description + keyword phrase to catch paraphrased bypasses.

### `ConstitutionalKernel.from_headers(headers, secret_key=None, *, base_kernel=None, max_age_seconds=300) → ConstitutionalKernel`

Reconstructs and verifies a parent kernel received over HTTP headers (`X-CT-Kernel`, `X-CT-Signature`, `X-CT-Timestamp`):

- Verifies the HMAC-SHA256 signature (`hmac.compare_digest`) using `secret_key` or `CT_PROPAGATION_SECRET`.
- Enforces a replay window (`max_age_seconds=300` by default).
- If `base_kernel` is provided, returns `base_kernel.merge(parent_kernel)` so parent constraints become axiomatic in the sub-agent.

### `update_commitment(commitment_id, new_value)` → updates a plastic commitment

### `merge(other) → ConstitutionalKernel`

Creates a merged kernel where the other kernel's rules become axiomatic:

```python
merged = base_kernel.merge(parent_kernel)
```

### `to_dict() / from_dict()` — serialization

### `get_system_prompt_injection() → str` — formatted system prompt block

