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
    # Or with CLI, Web Dashboard, Enterprise & Observability extras:
    pip install "ct-toolkit[all]"
    ```

=== "uv (recommended)"

    ```bash
    uv add "ct-toolkit[all]"
    ```

=== "from source"

    ```bash
    git clone https://github.com/Theseus-Guard/ct-toolkit
    cd ct-toolkit
    pip install -e ".[all]"
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

# After — sync (`chat`), async (`achat`), and streaming (`stream=True`) guardrails
client = TheseusWrapper(provider="openai")

response = client.chat("What are your core values?")
print(f"Divergence Score: {response.divergence_score:.4f}")  # 0.0 = aligned, 1.0 = drifted
```

---

## Audit & Telemetry Dashboard (CLI & Web UI)

Inspect HMAC provenance chain integrity, L1/L2/L3 divergence tier distribution, and staged sandbox rules in your terminal (`ct-toolkit status`) or via the local interactive Web Audit Dashboard (`ct-toolkit dashboard --web`):

![CT Toolkit Web Audit & Telemetry Dashboard](assets/ct_toolkit_web_dashboard.png)

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
    <p><strong>3-Tier Contrastive Divergence Engine</strong></p>
    <hr />
    <p>Layered monitoring from contrastive L1 embeddings (positive + negative anchors) to non-blocking background L3 identity probes with automatic circuit breaking.</p>
    <p><a href="concepts/divergence/">Understand Divergence</a></p>
  </li>
  <li>
    <p><strong>Hierarchical & Distributed Safety</strong></p>
    <hr />
    <p>Mother agent constraints propagate to sub-agents in-process or via HMAC-SHA256 signed HTTP headers as read-only axioms.</p>
    <p><a href="concepts/multi-agent/">Explore Multi-Agent Safety</a></p>
  </li>
  <li>
    <p><strong>Cryptographic Provenance & OTel</strong></p>
    <hr />
    <p>Every interaction is signed with HMAC-SHA256 and chained in SQLite or PostgreSQL, paired with OpenTelemetry spans and metrics.</p>
    <p><a href="concepts/provenance/">View Compliance Tools</a></p>
  </li>
  <li>
    <p><strong>Framework Middleware</strong></p>
    <hr />
    <p>Drop-in support for LangChain, Deep Agents, CrewAI, and AutoGen. Add identity protection to your existing agent stack without rewriting a single chain.</p>
    <p><a href="integrations/">Browse Integrations</a></p>
  </li>
  <li>
    <p><strong>CLI & Interactive Web Dashboard</strong></p>
    <hr />
    <p>Monitor provenance chains, divergence tiers, and sandbox cooldowns via Rich CLI or the localhost-bound Web Audit Dashboard.</p>
    <p><a href="guides/cli-auditor/">Explore CLI & Web UI</a></p>
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
| **Version**   | ✅ `v0.3.40`                                                                                                                                                                                                        |
| **Tests**     | ✅ 426 passed, 3 skipped                                                                                                                                                                                            |
| **Coverage**  | ✅ 90%+                                                                                                                                                                                                             |
| **PyPI**      | ✅ `pip install ct-toolkit`                                                                                                                                                                                         |
| **Downloads** | [![PyPI Downloads](https://static.pepy.tech/personalized-badge/ct-toolkit?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=MAGENTA&left_text=downloads)](https://pepy.tech/projects/ct-toolkit) |
| **License**   | Apache 2.0                                                                                                                                                                                                          |
| **Python**    | 3.11+                                                                                                                                                                                                               |

[:octicons-arrow-right-24: Full roadmap and status](reference/project-status.md)
