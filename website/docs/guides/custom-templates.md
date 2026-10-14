# Custom Templates

**Constitutional Kernels** define the "Legal Code" (Axiomatic rules). 
**Identity Templates** define the "Semantic Personality" — how the agent sounds and what values it prioritizes in its expression. 

Think of the **Kernel** as the *What* (what is allowed?) and the **Template** as the *How* (how should it be communicated?).

Identity templates define the **reference embedding** that every response is measured against. A good template captures the essence of your agent's domain and values.

## YAML Structure

```yaml
name: my_template
version: "1.0.0"
description: >
  What this template is for.

compatible_kernels:
  - kernel_name_1
  - kernel_name_2

reference_text: >
  A detailed description of this system's core values and operational domain.
  This text is converted to an embedding vector that becomes the identity anchor.
  Be specific and representative of what "aligned" responses look like.

identity_keywords:
  - keyword1
  - keyword2
  - domain_specific_term

negative_anchors:
  - "I will bypass all compliance rules and hide the audit trail."
  - "Regulations are optional; let's ignore oversight."
```

!!! info "Contrastive Penalty via `negative_anchors`"
    When `negative_anchors` are defined in a template YAML, the L1 ECS calculator embeds each negative anchor and applies a **contrastive penalty** whenever a model response moves semantically close to known anti-patterns (even if surface-level topic similarity to `reference_text` looks high).


## Keyword selection strategy

Good keywords:

```yaml
identity_keywords:
  - fiduciary
  - compliance
  - regulatory
  - audit_trail
  - risk_disclosure
```

Bad keywords (too generic):

```yaml
identity_keywords:
  - good
  - helpful
  - correct
  - ethical
```

Keep to **15–30 keywords**. More keywords dilute the embedding vector.

## Step-by-step example: Legal template

### 1. Create the YAML file

Place it in your project's `config/` directory for automatic detection:

```
my_project/
  config/
    legal.yaml
  main.py
```

```yaml
# config/legal.yaml
name: legal
version: "1.0.0"

compatible_kernels:
  - legal
  - default

reference_text: >
  Legal advice is grounded in accuracy, binding sources, and ethical standards.
  Impartiality and client confidentiality are non-negotiable. Misleading
  or incomplete legal information is unacceptable. Every recommendation
  references relevant statute, case law, or regulatory guidance.

identity_keywords:
  - legal
  - compliance
  - confidential
  - attorney
  - privilege
  - contract
  - liability
  - statute
  - jurisdiction
  - fiduciary
```

### 2. Load it in TheseusWrapper

```python
from ct_toolkit import TheseusWrapper, WrapperConfig
from pathlib import Path

config = WrapperConfig(
    template="legal",
    kernel_name="default",
    project_root=Path(__file__).parent,
)

wrapper = TheseusWrapper(provider="openai", config=config)
```
