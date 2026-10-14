---
hide:
  - navigation
  - toc
---

<div class="ct-hero" markdown>

# Computational Theseus Toolkit

**Identity Continuity & Hierarchical Guardrails for the Post-Drift AI Era.**

CT Toolkit is an open-source security layer that prevents **Sequential Self-Compression (SSC)** in agentic systems — ensuring your AI agents remain who they were on day one, even after thousands of interactions.

<div class="ct-install-tabs" markdown>

=== "pip"

    ```bash
    pip install ct-toolkit
    ```

=== "uv (recommended)"

    ```bash
    uv add ct-toolkit
    ```

=== "from source"

    ```bash
    git clone https://github.com/Theseus-Guard/ct-toolkit
    cd ct-toolkit
    pip install -e "."
    ```

</div>

[Get Started with Python](get-started/quickstart.md){ .md-button .md-button--primary }
[View on GitHub](https://github.com/Theseus-Guard/ct-toolkit){ .md-button }

<div class="provider-badges">
<span class="provider-badge"><img src="assets/logos/openai.svg" alt="OpenAI" width="24" style="vertical-align: middle; margin-right: 8px;">OpenAI</span>
<span class="provider-badge"><img src="assets/logos/anthropic.svg" alt="Anthropic" width="24" style="vertical-align: middle; margin-right: 8px;">Anthropic</span>
<span class="provider-badge"><img src="assets/logos/ollama.svg" alt="Ollama" width="24" style="vertical-align: middle; margin-right: 8px;">Ollama</span>
<span class="provider-badge"><img src="assets/logos/google.svg" alt="Google" width="24" style="vertical-align: middle; margin-right: 8px;">Google</span>
<span class="provider-badge"><img src="assets/logos/cohere.svg" alt="Cohere" width="24" style="vertical-align: middle; margin-right: 8px;">Cohere</span>
<span class="provider-badge"><img src="assets/logos/groq.svg" alt="Groq" width="24" style="vertical-align: middle; margin-right: 8px;">Groq</span>
</div>

</div>

---

## Two lines of code. Full identity protection.

```python
# Before
# response = openai.OpenAI().chat.completions.create(...)

# After — guardrails, drift detection, and audit log, all automatic
client = TheseusWrapper(provider="openai")

response = client.chat("What are your core values?")
print(f"Divergence Score: {response.divergence_score:.4f}")  # 0.0 = aligned, 1.0 = drifted
```

---

## Why CT Toolkit?

<div class="grid cards" markdown>
<ul>
  <li>
    <p><strong>Constitutional Kernels</strong></p>
    <hr />
    <p>Define immutable Axiomatic Anchors that never change, and Plastic Commitments that evolve through formal approval.</p>
    <p><a href="concepts/kernels/">Learn about Kernels</a></p>
  </li>
  <li>
    <p><strong>3-Tier Divergence Engine</strong></p>
    <hr />
    <p>Layered monitoring from zero-cost L1 embeddings to full L3 identity probes. Detect and block identity drift before it becomes systemic.</p>
    <p><a href="concepts/divergence/">Understand Divergence</a></p>
  </li>
  <li>
    <p><strong>Hierarchical Safety</strong></p>
    <hr />
    <p>Mother agent constraints propagate to sub-agents as read-only axioms. Prevent small orchestrator deviations from cascading into massive fleet-wide drift.</p>
    <p><a href="concepts/multi-agent/">Explore Multi-Agent Safety</a></p>
  </li>
  <li>
    <p><strong>Cryptographic Provenance</strong></p>
    <hr />
    <p>Every interaction is signed with HMAC-SHA256 and chained. Provide a regulator-ready audit trail of your agent's identity continuity.</p>
    <p><a href="concepts/provenance/">View Compliance Tools</a></p>
  </li>
  <li>
    <p><strong>Framework Middleware</strong></p>
    <hr />
    <p>Drop-in support for LangChain, CrewAI, and AutoGen. Add identity protection to your existing agent stack without rewriting a single chain.</p>
    <p><a href="integrations/">Browse Integrations</a></p>
  </li>
  <li>
    <p><strong>Production Ready</strong></p>
    <hr />
    <p>Tested with latest frontier models and local endpoints. 90%+ test coverage and enterprise-grade security defaults.</p>
    <p><a href="reference/project-status/">Project Roadmap</a></p>
  </li>
</ul>

</div>

---

## Existing guardrails aren't enough

|                               | Llama-Guard / Rule Engines | **CT Toolkit**                                |
| ----------------------------- | -------------------------- | --------------------------------------------- |
| **Stateful drift detection**  | ✗ Stateless per-prompt     | ✓ Tracks identity over thousands of calls     |
| **Multi-agent hierarchies**   | ✗ No hierarchy awareness   | ✓ Propagates kernel constraints to sub-agents |
| **Formal rule evolution**     | ✗ Binary block/allow       | ✓ Reflective Endorsement with signed approval |
| **Cryptographic audit trail** | ✗ No provenance            | ✓ HMAC hash chain, regulator-ready            |
| **Fine-tuning safety**        | ✗ No training constraints  | ✓ `DivergencePenaltyLoss` for PyTorch         |

[:octicons-arrow-right-24: Read the full rationale](concepts/why-ct-toolkit.md)

---

## Project status

| Metric        | Status                                                                                                                                                                                                              |
| :------------ | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Tests**     | ✅ 293/296 passing                                                                                                                                                                                                  |
| **Coverage**  | ✅ 89%                                                                                                                                                                                                              |
| **PyPI**      | ✅ `pip install ct-toolkit`                                                                                                                                                                                         |
| **Downloads** | [![PyPI Downloads](https://static.pepy.tech/personalized-badge/ct-toolkit?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=MAGENTA&left_text=downloads)](https://pepy.tech/projects/ct-toolkit) |
| **License**   | Apache 2.0                                                                                                                                                                                                          |
| **Python**    | 3.11+                                                                                                                                                                                                               |

[:octicons-arrow-right-24: Full roadmap and status](reference/project-status.md)
