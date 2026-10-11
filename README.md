# Computational Theseus Toolkit (CT Toolkit)

> **Identity Continuity Guardrails for Agentic Systems**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![PyPI version](https://img.shields.io/pypi/v/ct-toolkit.svg)](https://pypi.org/project/ct-toolkit/)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue.svg)](https://hakandamar.github.io/ct-toolkit/)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/ct-toolkit?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=MAGENTA&left_text=downloads)](https://pepy.tech/projects/ct-toolkit)
[![codecov](https://codecov.io/gh/hakandamar/ct-toolkit/graph/badge.svg?token=2OTO27N02C)](https://codecov.io/gh/hakandamar/ct-toolkit)

CT Toolkit is an open-source security layer designed to preserve the **identity continuity** of AI agents over time. It implements the **Nested Agentic Architecture (NAA)** framework to prevent **Sequential Self-Compression (SSC)** in multi-agent hierarchies.

---

## 📖 Official Documentation

For full API reference, architecture details, examples, and integration guides, visit our documentation site:
👉 [**https://hakandamar.github.io/ct-toolkit/**](https://hakandamar.github.io/ct-toolkit/)

- [**Live Examples & Case Studies**](https://hakandamar.github.io/ct-toolkit/examples/): Real-world scenarios like Financial Auditor Integrity.
- [**FastAPI Integration Test Project**](https://github.com/hakandamar/ct-toolkit-fastapi): Reference FastAPI app for validating CT Toolkit end-to-end across L1/L2/L3 guardrails and provenance logs with local model infrastructure.
- [**Deep Agents Integration Test Project**](https://github.com/hakandamar/ct-toolkit-deep-agents): Reference Deep Agents app for validating CT Toolkit end-to-end with hierarchical agent workflows, L1/L2/L3 guardrails, and provenance logs.

---

## Why CT Toolkit?

In complex agentic workflows, LLMs tend to "drift" from their original instructions. CT Toolkit provides the mathematical and cryptographic guardrails to ensure your agents remain aligned with their core constitution, even across deep hierarchies.

- **Staged Approval (Cooldown)**: Verify risky kernel updates in a sandbox via shadow requests before production promotion.
- **Passive Context Compression Detection**: Automatically detects silent provider-side history compression (e.g., OpenAI/Anthropic).
- **Constitutional Kernels**: Axiomatic identity anchors.
- **Standalone Auditor CLI**: Rapidly audit any LLM endpoint for identity drift without writing code.
- **Autonomous Self-Correction**: Active L2->L1 feedback loop that retries and corrects divergent responses before they reach the user.
- **Divergence Engine**: Multi-tiered drift analysis (L1/L2/L3).
- **Hierarchical Propagation**: Mother-to-child constraint inheritance.
- **Provenance Log**: Immutable HMAC-signed interaction history.

---

## Quick Start

```bash
pip install ct-toolkit
```

```python
from ct_toolkit import TheseusWrapper, WrapperConfig

# Protect against silent provider context compression
config = WrapperConfig(compression_passive_detection=True)

# One-line injection for any LLM provider
client = TheseusWrapper(provider="openai", config=config)

# Guardrails and drift analysis applied automatically
response = client.chat("What are your core security axioms?")

print(response.content)
print(f"Divergence Score: {response.divergence_score}")
```

### 🔍 Standalone Auditor (CLI)

Audit any LLM endpoint (OpenAI, Ollama, LM Studio) directly from your terminal:

```bash
# Audit a local Ollama model
ct-toolkit audit --url http://localhost:11434/v1 --kernel defense

# List available kernels and templates
ct-toolkit list-kernels
ct-toolkit list-templates

# Download a verified profile from the official repository
ct-toolkit setup personal_kernel --dest ./config
```

If you want a runnable application example instead of a CLI-only flow, see [**ct-toolkit-fastapi**](https://github.com/hakandamar/ct-toolkit-fastapi), a small FastAPI validation project that developers can use to test CT Toolkit locally with automated endpoints and pytest coverage.

For Deep Agents workflows, see [**ct-toolkit-deep-agents**](https://github.com/hakandamar/ct-toolkit-deep-agents), a reference integration project for validating CT Toolkit in multi-agent orchestration scenarios.

### Security Hardening

The profile setup command fails closed by default. Downloaded kernel, identity, and probe files must match the trusted SHA-256 manifest packaged with CT Toolkit before they are written to disk. HTTPS certificate validation is enabled and redirects to another host or scheme are rejected.

The L2 judge does not evaluate Jinja2 expressions in user requests or model responses. Rule validation normalizes Unicode input and requires configured conflict keywords to match complete words, preventing simple substring and keyword-boundary bypasses.

---

## 🚦 Project Health & Status

| Metric           | Status                                                                                                                                                                                                              |
| :--------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Tests**        | ✅ 397 passed, 3 skipped (100% success rate, 90% coverage)                                                                                                                                                          |
| **Downloads**    | [![PyPI Downloads](https://static.pepy.tech/personalized-badge/ct-toolkit?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=MAGENTA&left_text=downloads)](https://pepy.tech/projects/ct-toolkit) |
| **Last Phase**   | ✅ v0.3.40: fsspec 2026.9.0 fixes ReferenceFileSystem template-injection RCE and multidict 6.9.1 fixes items-view reference leak (Dependabot #72/#73)                                                     |
| **Current Goal** | 🔶 Phase 7: Multi-Agent Synchronization (Integration)                                                                                                                                                               |

### Framework & Model Support

Seamlessly integrate with your favorite frameworks and local models:

- **Local Models**: Support for LM Studio, Ollama, and local Qwen/Llama endpoints.
- **LangChain & Deep Agents**: `wrap_deep_agent_factory`.
- **CrewAI**: `TheseusCrewMiddleware.apply_to_crew`.
- **AutoGen**: `register_reply` hooks.

---

## Theoretical Foundation

Translating the framework proposed in [**The Computational Theseus (2025)**](https://hakandamar.com/the-computational-theseus-engineering-identity-continuity-as-a-guardrail-against-sequential-963918c1720d) into engineering practice.

---

## Community

- [**Contributing Guide**](CONTRIBUTING.md): How to propose changes, run tests, and submit pull requests.
- [**Security Policy**](SECURITY.md): How to report vulnerabilities responsibly.
- [**Code of Conduct**](CODE_OF_CONDUCT.md): Expected behavior for contributors and maintainers.
- [**Support Guide**](SUPPORT.md): Where to ask questions, report bugs, and request features.

---

## License

Apache License 2.0.
