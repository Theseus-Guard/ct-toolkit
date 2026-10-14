# Computational Theseus Toolkit

## Project Overview & Roadmap

---

## Project Overview

CT Toolkit is an open-source Python library that brings the **Nested Agentic Architecture (NAA)** framework — proposed in Hakan Damar's _"The Computational Theseus: Engineering Identity Continuity as a Guardrail Against Sequential Self-Compression in Multi-Agent AGI Systems"_ (2026 - 2nd edition) — into engineering practice.

**Current Version:** `v0.3.40`

### The Problem It Solves

An agentic system can drift from its initial value commitments over time. The paper defines this phenomenon as **Sequential Self-Compression (SSC)**: the model progressively compresses its prior normative states in service of immediate objective functions. This risk is significant in single-model systems — and **systemic** in multi-agent architectures, where an SSC event at the orchestrator level cascades across the entire sub-agent population.

### The Approach

CT Toolkit addresses this by treating **identity continuity as a first-class system constraint**. It is installed not inside the model, but around the **orchestration layer**, making it compatible with both open-source and proprietary models.

### Current State

| Area                                   | Status                                                                                                                                                                                                                                               |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python library (pip install)           | ✅ Complete (Modular optional extras: `cli`, `server`, `enterprise`, `embeddings`, `observability`, `all`)                                                                                                                                           |
| OpenAI / Anthropic / Ollama support    | ✅ Complete (`chat` & async `achat` + `stream=True` guardrails)                                                                                                                                                                                      |
| Constitutional Kernel                  | ✅ Complete (Lexical + Contrastive Semantic conflict detection)                                                                                                                                                                                      |
| Reflective Endorsement                 | ✅ Complete                                                                                                                                                                                                                                          |
| Provenance Log (HMAC hash chain)       | ✅ Complete (SQLite + PostgreSQL/SQLAlchemy + paginated $O(1)$-memory verification)                                                                                                                                                                  |
| Divergence Engine (L1+L2+L3)           | ✅ Complete (Contrastive Semantic L1 + Background L3 ICM + Circuit Breaker)                                                                                                                                                                          |
| Policy-Drift & SSC Measurement         | ✅ Phase 3 Complete                                                                                                                                                                                                                                  |
| Template + Kernel compatibility matrix | ✅ Complete                                                                                                                                                                                                                                          |
| Context-Compression Passive Guard      | ✅ Core Integrated (v0.3.6)                                                                                                                                                                                                                          |
| Staged Approval (Cooldown)             | ✅ Complete (Non-blocking background sandbox shadow analysis)                                                                                                                                                                                        |
| Distributed Multi-Agent Propagation    | ✅ Complete (HMAC-SHA256 signed HTTP headers + replay window verification)                                                                                                                                                                           |
| Audit & Telemetry Dashboard            | ✅ Complete (`ct-toolkit status`, `ct-toolkit dashboard --web` Interactive Web UI + OpenTelemetry spans/metrics)                                                                                                                                     |
| Documentation                          | ✅ Complete (Added [Live Examples](https://theseus-guard.github.io/ct-toolkit/examples/), [CLI & Web Dashboard Guide](guides/cli-and-web-dashboard.md), and [FastAPI validation example](https://github.com/hakandamar/ct-toolkit-fastapi))          |
| Test coverage                          | ✅ 426 passed, 3 skipped (90%+ code coverage)                                                                                                                                                                                                        |

---

## Developer Validation Example

For developers who want to test CT Toolkit inside a minimal application instead of only running isolated scripts, see [**ct-toolkit-fastapi**](https://github.com/hakandamar/ct-toolkit-fastapi). It provides a FastAPI-based integration suite that exercises L1/L2/L3 guardrails and provenance logging against local LM Studio and Ollama services.

---

## Roadmap

The roadmap covers all mechanisms defined in the paper and the research directions outlined in `Future Work` (Section 10). Each item is cross-referenced with the relevant paper section.

---

### PHASE 0 — MVP Core Infrastructure

> _Paper: Section 3, 4, 5 — NAA Framework_

- [x] **TheseusWrapper** — Sync (`chat`), Async (`achat`), and Streaming (`stream=True`) API proxy, kernel injection, provider dispatch
- [x] **Constitutional Identity Kernel (CIK)** — Axiomatic anchor + plastic commitment separation
- [x] **Kernel conflict detection** — Hard reject (axiomatic) / RE flow (plastic) with Unicode normalization, word-boundary matching, and semantic similarity detection
- [x] **Template + Kernel compatibility matrix** — Native / Compatible / Conflicting levels
- [x] **Kernel priority rule** — Kernel always takes precedence over template
- [x] **Domain templates** — general, medical, finance, defense (with positive and negative semantic anchors)
- [x] **Domain kernels** — default, defense
- [x] **Provider support** — OpenAI, Anthropic, Ollama, LiteLLM

---

### PHASE 1 — Identity Continuity Mechanisms

> _Paper: Section 3.1 (Identity Core), 5.1 (Divergence Penalty), 5.2 (Provenance Log)_

- [x] **L1 Contrastive ECS** — Positive reference + negative anchor cosine similarity, real-time on every query
- [x] **L2 LLM-as-judge** — Independent frozen model, JSON verdict
- [x] **L3 ICM Probe Battery** — Fixed ethical scenario battery, health score, non-blocking background execution, and automatic Circuit Breaker
- [x] **Divergence Engine** — L1→L2→L3 cascaded orchestration + rigorous enterprise mode
- [x] **Provenance Log** — HMAC hash chain, SQLite & PostgreSQL/SQLAlchemy vaults, paginated tamper detection
- [x] **Reflective Endorsement protocol** — Conflict record, signed approval, ICM flag
- [x] **Staged Approval (Cooldown)** — _(Paper 6)_ Non-blocking background sandbox shadow requests + dynamic penalty/RPM duration
- [x] **Approval channels** — CLI, auto-approve, auto-reject, staged, custom callback
- [x] **Provider-independent embedding integration** — `litellm`, `ollama`, local `fastembed` (ONNX), `openai`, and deterministic local fallback
- [x] **Stability-Plasticity Scheduling** — _(Paper 5.3)_ Dynamic threshold scaling with `RiskProfile` adjustments
- [x] **Context-Compression Identity Guard** — Tracks silent provider summarization via passive detection

---

### PHASE 2 — Multi-Agent Hierarchy Support

> _Paper: Section 2.2 (SSC in Multi-Agent), 3.3 (Hierarchical Propagation)_

- [x] **Hierarchical Kernel Propagation** — Propagating the mother agent's CIK to sub-agents as a read-only constraint
- [x] **Distributed HTTP Header Propagation** — HMAC-SHA256 signed `X-CT-Kernel`, `X-CT-Signature`, and `X-CT-Timestamp` headers (`propagate_headers` / `from_headers`) with replay protection
- [x] **Sub-agent constraint enforcement** — Logic for sub-agents to reject and log instructions from the mother agent that conflict with the propagated kernel
- [x] **Cascade compression detection** — Blocking SSC events at the orchestrator before propagation to sub-agents
- [x] **LangChain / LangGraph middleware** — Complete: `TheseusLangChainCallback` + `TheseusChatModel` + `wrap_deep_agent_factory`
- [x] **CrewAI integration** — Complete: `apply_to_crew` for hierarchical wrap
- [x] **AutoGen integration** — Complete: `register_reply` + `post_send_hook`
- [ ] **Topology-aware propagation** — _(Based on ValueFlow [5] findings)_ Propagation mechanism that accounts for non-uniform drift intensity shaped by network topology

---

### PHASE 3 — ICM and Measurement Infrastructure

> _Paper: Section 10.1 (ICM), 10.2 (Policy-Drift Measurement), 10.4 (SSC Severity)_

- [x] **ICM base probe battery** — 10 general ethical scenarios
- [x] **Domain probe batteries** — defense, finance
- [x] **BehaviorClassifier** — Response classification (reject / comply / refuse_and_explain)
- [x] **Identity Health Score** — 0.0-1.0 normalized score + risk level
- [x] **Reasoning chain analysis** — _(Paper 10.1)_ Distinguishing legitimate maturation from SSC-driven drift via `<think>` tag capture
- [x] **Policy-drift measurement** — _(Paper 10.2)_ Distributional shift calculation (velocity, variance) over the Provenance Log
- [x] **SSC severity operationalization** — _(Paper 10.4)_ Risk-normalized severity index considering model capabilities (tools, vision, MCP)
- [ ] **Cross-checkpoint ICM** — ICM score comparison across successive model checkpoints
- [ ] **Probe battery expansion** — medical, legal, research domain probes

---

### PHASE 4 — Open-Source Model Support

> _Paper: Section 5.1 (Divergence Penalty), 10.3 (CIK Enforcement Experiments)_

- [x] **Divergence Penalty — Loss Function module** — _(Paper 5.1)_ PyTorch training loop integration; Complete in `divergence/loss.py`
- [x] **Llama 3 / Mistral / Qwen integration** — Verified with live Qwen-3 local endpoint (LM Studio)
- [x] **Context-Compression Identity Guard** — Verified with real embeddings (`text-embedding-qwen3`)
- [x] **Elasticity threshold calibration** — _(Paper 8)_ Empirically determining the balance between identity continuity and the capacity for external correction

---

### PHASE 5 — Vault, Enterprise Observability & Security Infrastructure

> _Paper: Section 5.2 (Provenance Log), 6 (Corrigibility)_

- [x] **Local SQLite vault** — For open-source and edge deployments
- [x] **Enterprise SQLAlchemy / PostgreSQL vault** — Production relational backend (`vault_url`) with paginated chain verification
- [x] **HMAC key management** — Environment variable (`CT_HMAC_SECRET`) or restricted `0600` local key file
- [x] **OpenTelemetry (OTel) Observability** — Distributed tracing spans (`ct.chat`, `ct.divergence.*`) and metrics (`ct.requests.total`, `ct.divergence.l1.score`, `ct.circuit_breaker.trips.total`)
- [ ] **External Vault integration** — Generic adapter for hardware/cloud KMS key management
- [x] **Read-only external Provenance Log access** — _(Paper 5.2)_ Making the log available in read-only form to an oversight mechanism (`ct-toolkit status` & Web Audit Dashboard)
- [x] **Rollback mechanism** — _(Paper 5.2)_ Reverting to a prior normative state if a deployed update is subsequently found to be misaligned
- [x] **Anomaly detection and alerting** — `drift_alert_callback` support + automatic L3 Circuit Breaker in `TheseusWrapper`
- [x] **Autonomous Self-Correction** — _(Paper 6)_ Active L2->L1 feedback loop that retries and corrects divergent responses before they reach the user

---

### PHASE 6 — Stand-alone Auditor & Telemetry Dashboard (CLI & Web UI)

> _Project Desc: Model 3 — Independent Auditor_

- [x] **Identity Sentinel LLM** — Custom LLM for CIK enforcement and L2 LLM-as-Judge divergence detection
- [x] **Auditor CLI (`ct-toolkit audit`)** — Stress-testing an existing system via API endpoint without modifying any code
- [x] **CLI Telemetry & Status (`ct-toolkit status` / `dashboard`)** — Terminal dashboard displaying HMAC chain status, L1/L2/L3 tier breakdown, staged cooldown rules, and recent provenance entries
- [x] **Interactive Web Audit Dashboard (`ct-toolkit dashboard --web`)** — Self-hosted Web UI (`/dashboard`) with live auto-refresh, tier filtering, expandable metadata inspection, localhost-only default binding, and credential redaction
- [x] **Sidecar Guardrail Server (`ct-toolkit serve`)** — FastAPI analysis server with `X-CT-API-Key` authentication and fail-closed non-loopback protection
- [x] **Identity Health Score report** — Ethical scenario battery and consistency report
- [x] **Comparative checkpoint analysis** — Normative drift report between two model versions
- [x] **Export formats** — Terminal Rich Table & Interactive Web UI
- [ ] **CIK enforcement experiments** — _(Paper 10.3)_ Control group vs CIK-equipped model; ICM score comparison under aggressively capability-optimized training conditions

---

### PHASE 7 — MAS Integration and Early Warning

> _Paper: Section 6, ref [6] Moral Anchor System_

- [ ] **MAS (Moral Anchor System) integration** — _(Chen et al. 2025)_ Combining Bayesian network and LSTM-based value drift prediction with CT Toolkit's upstream prevention mechanism
- [x] **Early warning signals** — Connecting MAS-detected drift signals to Provenance Log triggers. (Implemented via Passive Compression Guard & Background Circuit Breaker).
- [ ] **ValueFlow integration** — _(Luo et al. 2025)_ Incorporating beta-sensitivity and System Sensitivity metrics into the Divergence Engine
- [ ] **Phi-3 sub-agent prototype** — Sub-agent constraint testing on small models
- [ ] **Gemma research integration** — For academic SSC experiments
- [ ] **AISAI-style game-theoretic probes** — _(Paper ref [7], Kim 2025)_

---

## Final Results

- **Total Passing Tests:** 426 passed, 3 skipped (0 failed)
- **Overall Code Coverage:** 90%+

### Verification Recording

All backend and dashboard features are verified using the automated test suite:

```bash
uv run pytest --cov=ct_toolkit tests/
# Result: 426 passed, 3 skipped, 0 failures
```

---

## Summary Table

| Phase | Scope                                           | Status                                       |
| ----- | ----------------------------------------------- | -------------------------------------------- |
| **0** | MVP Core Infrastructure                         | ✅ Complete                                  |
| **1** | Identity Continuity Mechanisms                  | ✅ Complete                                  |
| **2** | Multi-Agent Hierarchy & Distributed Propagation | ✅ Complete                                  |
| **3** | ICM and Measurement                             | ✅ Complete                                  |
| **4** | Open-Source Model Support                       | ✅ Complete (Drift Loss + Live Verification) |
| **5** | Vault, Observability & Security Infrastructure  | ✅ Complete (SQLite + PostgreSQL + OTel)     |
| **6** | Auditor Mode & Web/CLI Telemetry Dashboard      | ✅ Complete (CLI + Interactive Web UI)       |
| **7** | MAS / Early Warning                             | 🔶 In Progress (Active)                      |

---

## Paper → Code Mapping

| Paper Mechanism                    | CT Toolkit Implementation                      | Phase |
| ---------------------------------- | ---------------------------------------------- | ----- |
| Constitutional Identity Kernel     | `core/kernel.py`                               | ✅ 0  |
| Axiomatic Anchors                  | `kernels/*.yaml → axiomatic_anchors`           | ✅ 0  |
| Plastic Commitments                | `kernels/*.yaml → plastic_commitments`         | ✅ 0  |
| Reflective Endorsement             | `endorsement/reflective.py`                    | ✅ 1  |
| Staged Approval (Cooldown)         | `core/wrapper.py`, `endorsement/reflective.py` | ✅ 1  |
| Divergence Penalty (API level)     | `divergence/engine.py`, `identity/embedding.py`| ✅ 1  |
| Divergence Penalty (loss function) | `divergence/loss.py`                           | ✅ 4  |
| Provenance Log (SQLite & Postgres) | `provenance/log.py`                            | ✅ 1/5|
| Stability-Plasticity Scheduling    | `divergence/scheduler.py`                      | ✅ 1  |
| Hierarchical & HTTP Propagation    | `core/kernel.py`, `core/wrapper.py`            | ✅ 2  |
| Identity Consistency Metric        | `divergence/l3_icm.py`                         | ✅ 3  |
| Policy-Drift Measurement           | `divergence/analysis.py`                       | ✅ 3  |
| SSC Severity Operationalization    | `divergence/analysis.py`                       | ✅ 3  |
| OpenTelemetry Observability        | `telemetry.py`                                 | ✅ 5  |
| Audit & Telemetry Dashboard        | `cli.py`, `server.py`                          | ✅ 6  |
| MAS Integration                    | —                                              | 🔲 7  |
| ValueFlow Integration              | —                                              | 🔲 7  |
