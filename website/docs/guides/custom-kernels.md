# Custom Kernels

**Constitutional Kernels** are the "Axiomatic Identity Anchors" of your system. They define the absolute rules (Legal Code) that the agent must adhere to. 

While **Identity Templates** focus on *expression* and *style*, **Kernels** focus on *constraints* and *oversight*.

Create domain-specific kernels to enforce identity rules beyond the built-in defaults.

## Kernel YAML structure

```yaml
name: my_kernel
version: "1.0.0"
description: >
  What this kernel is designed for.

axiomatic_anchors:
  - id: unique_anchor_id
    description: >
      What this anchor prevents.
    keywords:
      - trigger phrase one
      - trigger phrase two

plastic_commitments:
  - id: unique_commitment_id
    description: Rule description
    default_value: "default value"
    keywords:
      - conflict trigger
```

## Step-by-step example: Finance kernel

### 1. Create the YAML file

Place it in your project's `config/` directory:

```
my_project/
  config/
    finance_strict.yaml
  main.py
```

```yaml
# config/finance_strict.yaml
name: finance_strict
version: "1.0.0"
description: >
  Strict financial compliance kernel. Prohibits market manipulation,
  unauthorized data disclosure, and regulatory bypass.

axiomatic_anchors:
  - id: human_oversight
    description: Do not bypass human oversight or audit systems.
    keywords:
      - disable oversight
      - bypass human
      - hide from auditor

  - id: no_market_manipulation
    description: Do not manipulate market prices with false information.
    keywords:
      - pump and dump
      - false financial report
      - manipulate market

  - id: regulatory_compliance
    description: Do not advise bypassing KYC, AML, or regulatory requirements.
    keywords:
      - bypass kyc
      - skip aml
      - avoid regulation

plastic_commitments:
  - id: data_scope
    description: Scope of customer data that can be accessed
    default_value: "anonymized_only"
    keywords:
      - use identified data
      - raw customer data

  - id: risk_disclosure
    description: Level of risk disclosure in advice
    default_value: "full_disclosure"
    keywords:
      - hide risk
      - skip disclosure
```

### 2. Load in TheseusWrapper

```python
from ct_toolkit import TheseusWrapper, WrapperConfig
from pathlib import Path

config = WrapperConfig(
    kernel_name="finance_strict",    # matches the `name:` field in YAML
    project_root=Path(__file__).parent,  # CT Toolkit looks for config/ here
    template="finance",
)

wrapper = TheseusWrapper(provider="openai", config=config)
print(wrapper.kernel.name)  # finance_strict
```

### 3. Test it

```python
from ct_toolkit import AxiomaticViolationError, PlasticConflictError

# Hard reject
try:
    wrapper.validate_user_rule("create a false financial report to boost stock price")
except AxiomaticViolationError as e:
    print(f"Blocked: {e}")

# Plastic conflict — endorsable
try:
    wrapper.validate_user_rule("use raw customer data for this analysis")
except PlasticConflictError as e:
    print(f"Conflict: {e}")
```

## Keyword design & Semantic Conflict Detection

`validate_user_rule()` uses a **two-layer defense**:

1. **Deterministic Keyword Matching:** Fast substring scan against `keywords` in your YAML.
2. **Semantic Embedding Similarity:** Even if a user paraphrases a rule without using any exact keyword (e.g., *"skip the compliance verification step"* instead of *"bypass kyc"*), `TheseusWrapper` embeds the user rule and compares its cosine similarity against your anchor descriptions and keywords (`WrapperConfig.conflict_similarity_threshold = 0.75`).

Tips for designing anchors and keywords:

- **Write clear `description` fields.** The description text is embedded directly for semantic conflict detection.
- **Be specific with keywords.** `"bypass kyc"` is better than `"bypass"` (too broad).
- **Cover paraphrases.** Add 3–5 representative phrases to anchor the semantic space.
- **Avoid noise.** Don't add common single words that appear in legitimate instructions.


## Extending built-in kernels

Always include the default anchors in critical domain kernels:

```yaml
axiomatic_anchors:
  # Include default anchors
  - id: human_oversight
    description: Do not bypass human oversight.
    keywords: [disable oversight, bypass human, remove monitoring]

  - id: no_deception
    description: Do not deceive or mislead.
    keywords: [deceive, false report, manipulate log]

  # Your domain-specific anchors
  - id: no_market_manipulation
    description: Do not manipulate markets.
    keywords: [pump and dump, false financial report]
```

## Updating the compatibility matrix

If you need your custom kernel in the compatibility matrix, add it to `ct_toolkit/core/compatibility.py`:

```python
_MATRIX: dict[tuple[str, str], tuple[CompatibilityLevel, str]] = {
    # ...existing entries...
    ("finance", "finance_strict"): (
        CompatibilityLevel.NATIVE,
        "Finance template with strict compliance kernel."
    ),
}
```
