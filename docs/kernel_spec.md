# Kernel Specification Guide

## Kernel YAML Structure

```yaml
name: my_kernel              # required, unique
version: "1.0.0"             # semantic version
description: >               # short description
  What is it designed for?

axiomatic_anchors:           # unmodifiable rules
  - id: unique_anchor_id
    description: >
      Describes what must not be done.
    keywords:                # keywords for conflict detection
      - trigger phrase
      - another trigger

plastic_commitments:         # modifiable rules
  - id: unique_commitment_id
    description: Rule description
    default_value: "default value"
    keywords:
      - conflict trigger
```

---

## Axiomatic Anchor Design

Each anchor defines: **"This system never does X."**

A good anchor:
- Targets a single type of violation
- `keywords` list is specific and covers offensive phrases
- Description is explanatory and clear (also used for semantic similarity conflict detection when an embedding provider is active)

```yaml
axiomatic_anchors:
  - id: no_mass_harm
    description: >
      Producing, planning, or facilitating content that could cause mass harm.
    keywords:
      - mass casualty
      - weapon of mass
      - mass harm
      - mass attack
```

---

## Plastic Commitment Design

Each commitment defines: **"This system acts as X by default, but can switch to Y with an endorsed change."**

```yaml
plastic_commitments:
  - id: data_retention
    description: User data retention policy
    default_value: "session_only"  # Only during the session
    keywords:
      - store user data permanently
      - store permanently
      - log everything
```

---

## Lexical & Semantic Conflict Detection

When `validate_user_rule(rule_text)` is called on a `ConstitutionalKernel`, two layers of conflict detection run:

1. **Unicode-Normalized Word-Boundary Matching**:
   - Input is normalized with `unicodedata.normalize("NFKC", ...)` to prevent homoglyph or zero-width character bypasses.
   - Configured `keywords` are matched using strict word boundaries (`\b`), avoiding false positives on partial substrings while catching exact phrase triggers.
2. **Semantic Similarity Conflict Detection**:
   - When an `embedding_fn` / `embedding_provider` is configured (`WrapperConfig(embedding_provider="fastembed" | "litellm" | "ollama", rule_conflict_similarity_threshold=0.78)`), the rule text is embedded and compared against each anchor's description and keywords.
   - Paraphrased violations that omit exact keywords (e.g., *"eliminate human supervision steps"*) are still caught and rejected as `AxiomaticViolationError` or routed to Reflective Endorsement as `PlasticConflictError`.

---

## Distributed HTTP Kernel Propagation

In multi-agent architectures spanning multiple processes or HTTP microservices, a Mother Agent can cryptographically sign and propagate its kernel headers:

```python
# On the Mother Agent:
headers = parent_client.propagate_headers(secret_key=os.environ["CT_HMAC_SECRET"])
# Produces: X-CT-Kernel, X-CT-Signature (HMAC-SHA256), X-CT-Timestamp

# On the Sub-Agent service receiving the HTTP request:
from ct_toolkit.core.kernel import ConstitutionalKernel

child_kernel = ConstitutionalKernel.from_headers(
    request.headers,
    secret_key=os.environ["CT_HMAC_SECRET"],
    max_age_seconds=300,  # Rejects replayed headers older than 5 minutes
)
```

All anchors inherited from the parent kernel are marked `read_only=True` on the sub-agent and cannot be overridden via Reflective Endorsement.

---

## Extending Built-in Kernels

`default.yaml` is the foundation of all kernels. When creating a new domain kernel, it is recommended to include default anchors:

```yaml
# finance.yaml
name: finance

axiomatic_anchors:
  # Manually include Default anchors
  - id: human_oversight
    description: >
      Blocking or bypassing human oversight.
    keywords: [disable oversight, bypass human, oversight bypass]

  - id: no_deception
    description: Deception or manipulation.
    keywords: [deceive, manipulate, misinformation]

  # Finance-specific anchors
  - id: no_market_manipulation
    description: >
      Manipulating market prices with false information.
    keywords:
      - pump and dump
      - false financial report
      - false report
```

---

## Updating Compatibility Matrix

If you added a new kernel, add combinations to `_MATRIX` in `core/compatibility.py`:

```python
# in core/compatibility.py
_MATRIX: dict[tuple[str, str], tuple[CompatibilityLevel, str]] = {
    # ...existing combinations...

    ("general", "my_kernel"): (
        CompatibilityLevel.COMPATIBLE,
        "General template used with my_kernel."
    ),
    ("entertainment", "my_kernel"): (
        CompatibilityLevel.CONFLICTING,
        "This combination is not supported."
    ),
}
```

---

## Example: Health Research Kernel

```yaml
name: health_research
version: "1.0.0"
description: >
  Kernel for clinical research and health data analysis.
  Focused on patient privacy and research ethics.

axiomatic_anchors:
  - id: human_oversight
    description: Blocking human oversight.
    keywords: [disable oversight, bypass human]

  - id: patient_privacy
    description: >
      Disclosing patient identity or sharing non-anonymized data.
    keywords:
      - share patient identity
      - de-anonymize
      - disclose identity
      - share patient name

  - id: research_integrity
    description: >
      Fabricating, manipulating, or making up research data.
    keywords:
      - fabricate data
      - falsify results
      - manipulate results

plastic_commitments:
  - id: data_scope
    description: Scope of data that can be analyzed
    default_value: "anonymized_only"
    keywords:
      - use identified data
      - identified data

  - id: disclosure_level
    description: Sharing level of research findings
    default_value: "team_only"
    keywords: []
```
