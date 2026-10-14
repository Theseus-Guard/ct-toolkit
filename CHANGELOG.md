# Changelog

All notable changes to the **Computational Theseus Toolkit (CT Toolkit)** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

_(Note: This project uses `python-semantic-release` for automated versioning and changelog generation. Future automated releases will append updates here.)_

## [0.4.0] - 2026-10-14

### Added

- **Async & Streaming Guardrails (`achat` & `stream=True`)** — Added native `await wrapper.achat()` and `await engine.aanalyze()` alongside token-by-token streaming (`stream=True` returning `Iterator[str]` / `AsyncIterator[str]`) with automatic post-stream L1/L2/L3 divergence analysis and provenance recording.
- **Contrastive Semantic L1 Engine & Provider-Independent Embeddings** — Added `negative_anchors` support to identity templates with contrastive penalty calculation in `ECSCalculator`, semantic embedding conflict detection in `ConstitutionalKernel.validate_user_rule()`, and provider-independent embedding backends (`litellm`, `ollama`, local `fastembed` ONNX, `openai`, and deterministic fallback).
- **Non-Blocking Shadow & L3 Execution + L3 Circuit Breaker** — Offloaded sandbox shadow requests (`StagedUpdateManager`) and L3 ICM probe batteries to background `ThreadPoolExecutor` threads so user latency is never blocked. Added an automatic L3 Circuit Breaker (`l3_circuit_breaker_threshold`, `circuit_breaker_open`, `reset_circuit_breaker()`) that halts requests on repeated critical L3 failures.
- **Enterprise Provenance (PostgreSQL / SQLAlchemy) & Paginated Verification** — Added SQLAlchemy database URL support (`vault_url`, e.g., `postgresql+psycopg2://...`) to `ProvenanceLog` and paginated $O(1)$-memory hash-chain verification (`verify_chain(batch_size=1000)`).
- **Native OpenTelemetry (`ct_toolkit.telemetry`)** — Added `TelemetryManager` (`WrapperConfig(otel_enabled=True)`) emitting `ct.chat` trace spans and `ct.requests.total`, `ct.divergence.l1.score`, `ct.divergence.alerts.total`, and `ct.circuit_breaker.trips.total` metrics with graceful no-op fallback when `opentelemetry-api` is not installed.
- **Cryptographic Distributed Header Propagation** — Upgraded `propagate_headers()` to sign `X-CT-Kernel` and `X-CT-Timestamp` with HMAC-SHA256 (`X-CT-Signature`) and added `ConstitutionalKernel.from_headers()` with replay-window protection (`max_age_seconds=300`) and parent-kernel merging.
- **Interactive Web Audit & Telemetry Dashboard & CLI Status** — Added `ct-toolkit status` / `ct-toolkit dashboard` terminal telemetry views and `ct-toolkit dashboard --web` (serving `/dashboard` and `/v1/status` on FastAPI) with real-time chain integrity verification, L1/L2/L3 tier breakdown, staged rule cooldown tracking, and enterprise link to [Theseus Guard](https://www.theseusguard.com).

### Changed

- **Modular Optional Dependencies** — Slimmed core installation footprint and organized optional extras into `cli`, `server`, `observability`, `structured`, `vision`, `postgres`, `ml`, and `all`.
- **Repository & Documentation Migration** — Migrated official repository and documentation URLs to `Theseus-Guard/ct-toolkit` and synchronized all guides and API references with `v0.4.0`.

### Security

- **Localhost-Only Default & Credential Redaction in Web Dashboard** — Enforced loopback IP verification (`127.0.0.1` / `::1`) on `/dashboard` and `/v1/status` unless `CT_SERVER_API_KEY` (`X-CT-API-Key` / `Bearer`) is configured, added `X-Frame-Options: DENY` and `Content-Security-Policy` headers, and masked database credentials/paths (`redact_vault_identifier`) in API responses.

### Validation

- **Automated Tests:** `426 passed, 3 skipped`.

## [0.3.40] - 2026-10-11

### Security

- **fsspec** — Updated the transitive lock resolution to `fsspec 2026.9.0` (from 2026.3.0), fixing Server-Side Template Injection in `ReferenceFileSystem` that could lead to Remote Code Execution (GHSA-27vj-qcqg-25rc / CVE-2026-104851, Dependabot #72). Fixed in `>= 2026.6.0`.
- **multidict** — Updated the transitive lock resolution to `multidict 6.9.1` (from 6.7.1), fixing a reference leak in `CIMultiDict`/`MultiDict` items-view union and subtraction (GHSA-54p9-h82j-f925 / CVE-2026-104874, Dependabot #73). Fixed in `>= 6.9.1`.
- **urllib3** — Lock already resolves to `2.8.0`; Dependabot #71 (HTTPS proxy TLS configuration, GHSA-8988-9cw3-xx77) is covered by this release line.

### Validation

- **Automated Tests:** `397 passed, 3 skipped`.

## [0.3.39] - 2026-10-08

### Security

- **urllib3** — Updated the runtime requirement to `urllib3>=2.8.0` (resolved to 2.8.0), fixing the Chunked Deflate streaming infinite loop (GHSA-gh4c-6fx4-qh6g, Dependabot #69). 2.8.0 also fixes HTTPS proxy TLS configuration being ignored or overridden, and unbounded chunk-size line memory buffering.
- **LiteLLM** — Updated the runtime requirement to `litellm>=1.104.0` (resolved to 1.104.2), fixing authenticated SSRF and provider-credential exfiltration via unvalidated request-body routing parameters (Dependabot #70).

### Validation

- **Automated Tests:** `397 passed, 3 skipped`.

## [0.3.38] - 2026-09-20

### Security

- **AnyIO** — Updated the runtime requirement to `anyio>=4.14.2`, fixing the process-pool stderr deadlock and TLS IDNA certificate spoofing vulnerabilities (Dependabot #67 and #68).

## [0.3.37] - 2026-09-10

### Security

- **mkdocs-material DOM XSS** — Upgraded `mkdocs-material` from `9.7.6` to `9.7.7` to fix DOM-based XSS in search suggestions via the query parameter (Dependabot #66).

## [0.3.36] - 2026-08-26

### Security

- **L2 Judge SSTI** — Removed unsandboxed Jinja2 rendering from judge prompts. Kernel rules, user requests, and model responses are now inserted as literal text without template evaluation.
- **Profile Supply Chain Integrity** — `ct-toolkit setup` now verifies every downloaded kernel, identity, and probe file against a trusted SHA-256 manifest shipped in the wheel. Downloads require validated HTTPS and same-origin redirects; unverified downloads are rejected.
- **Rule Validation** — Replaced substring-only conflict checks with Unicode normalization, case folding, and complete-word matching to prevent trivial keyword-boundary bypasses.

### Added

- Added `ct_toolkit/profile_checksums.sha256` as the trusted profile integrity manifest.
- Profile setup now verifies all files before writing any of them and exits non-zero on download or integrity failures.

### Validation

- **Automated Tests:** `397 passed, 3 skipped`.
- **Lint:** `ruff check` passed for changed Python modules.
- **Build:** Wheel build completed successfully.

## [0.3.35] - 2026-08-05

### Security

- cryptography: Updated to 50.0.0 — fixes PKCS#7 EnvelopedData decryption Bleichenbacher oracle (CVE, Dependabot #65).
- torch: Updated ml extras constraint to 2.13.0+ (resolved to 2.13.0) — fixes memory corruption via torch.jit.script (Dependabot #43).

## [0.3.34] - 2026-08-05

### Security

- pillow: Updated to 11.3.0+ (resolved to 12.3.0) — bulk fix for 15+ Dependabot alerts including JPEG2000 tiled decode DoS, RankFilter heap OOB write via integer overflow, decompression bomb bypasses in BdfFontFile/GdImageFile/FontFile/PcfFontFile/PdfParser, McIdas AREA OOB read, Image.paste/crop signed coordinate overflow, ImageCmsTransform heap OOB write via output mode mismatch, TGA RLE encoder heap data leak, EPS infinite loop DoS, and Windows shell command injection.
- aiohttp: Updated to 3.14.3+ — fixes WebSocket compressed frame acceptance without negotiated permessage-deflate (Moderate), out-of-bounds heap read in C HTTP response parser error path (High), and HTTP request smuggling via WebSocket upgrade (Moderate).
- pyasn1: Updated to 0.6.4+ — fixes BER/CER/DER decoder DoS via unbounded long-form tag IDs (High), uncontrolled resource consumption when converting decoded REAL values (High), and quadratic complexity in OBJECT IDENTIFIER/RELATIVE-OID processing (High).
- setuptools: Updated build-system requirement to 78.0.0+ (resolved to 81.0.0) — fixes MANIFEST.in exclusion bypass via Unicode normalization collision (NFC/NFD) on macOS APFS/HFS+ (Moderate).
- torch: Updated ml extras constraint to 2.6.0+ — fixes memory corruption via torch.jit.script (Low).
- pymdown-extensions: Constraint already at 10.21.3+ (sufficient for b64 path traversal fix).

### Changed

- requires-python: Constrained to `>=3.11, <3.14` to resolve autogen extras dependency resolution conflicts.

### Validation

- **Automated Tests:** `397 passed, 3 skipped`.
- **Dependency Audit:** Lock file regenerated with `uv lock --upgrade` for pillow, pyasn1, aiohttp, setuptools, pymdown-extensions.

## [0.3.33] - 2026-06-29

### Security

- cryptography: Updated to 48.0.1+ (GHSA-537c-gmf6-5ccf: removes vulnerable OpenSSL bundled in cryptography wheels)
- starlette: Updated to 1.3.1+ (CVE-2026-54282 host header poisoning + CVE-2026-54283 form limits bypass enabling DoS; fixes Dependabot #39 and #41)
- langsmith: Updated to 0.8.18+ (GHSA-f4xh-w4cj-qxq8: TracingMiddleware arbitrary server-side file read; fixes Dependabot #42)
- torch: Open alert (CVE-2025-3000, GHSA-rrmf-rvhw-rf47) — memory corruption via torch.jit.script; no upstream patch available yet, awaiting PyTorch fix

## [0.3.32] - 2026-06-09

### Security

- aiohttp: Updated to 3.14.0+ to remediate cross-origin redirect cookie leakage and unsafe deserialization vulnerabilities reported by Dependabot.
- Starlette: Added an explicit 1.0.1+ runtime constraint to remediate Host header validation poisoning of request URL paths.

## [0.3.30] - 2026-05-15

### Security

- langsmith: Updated to 0.8.0+ (public prompt pull trust boundary fix — blocks pulling public prompts by owner/name by default)

## [0.3.29] - 2026-05-11

### Security

- litellm: Updated to 1.83.14+ (sandbox escape and SQL injection hardening)
- urllib3: Updated to 2.7.0+ (decompression-bomb and sensitive header exposure fixes)
- aiohttp: Adjusted constraint to 3.13.4+ for compatibility while maintaining security patches

## [0.3.28] - 2026-05-10

### Security

- langchain-core: Updated to 1.3.3

## [0.3.27] - 2026-04-30

### Security

- langchain-core: Updated to 1.3.0
- langsmith: Updated to 0.7.31+ (streaming token bypass fix)

## [0.3.26] - 2026-04-30

### Security

- litellm 1.83.7: SQL Injection (CVE-2026-35140), Auth bypass (CVE-2026-35030), Command execution, SSTI vulnerability remediations.

## [0.3.25] - 2026-04-11

### Changed

- Version bump: All project metadata and runtime version constants updated to 0.3.25 for release consistency.

### Security

- Same as 0.3.24: cryptography 46.0.7, langchain-core 1.2.28 vulnerability remediations.

## [0.3.24] - 2026-04-11 (skipped)

### Security

- **Dependency: cryptography** — Upgraded to `>=46.0.7` to pick up upstream security fixes and regenerate lockfile artifacts with the patched release.
- **Dependency: langchain-core** — Upgraded to `>=1.2.28` to remediate the incomplete f-string validation issue reported by dependency scanning.

### CI/CD

- **Release guardrail** — Added a release workflow validation step that fails if the pushed git tag version (for example `v0.3.24`) does not match `[project].version` in `pyproject.toml`, preventing accidental republish attempts of an old version.

## [0.3.23] - 2026-04-05

### Security

- **Dependency: litellm authentication bypass (Critical)** — Upgraded `litellm` from `>=1.40.0` to `>=1.83.0` to patch CVE-2026-35030. The OIDC userinfo cache used `token[:20]` as the cache key, allowing an attacker to craft a token whose first 20 characters match a legitimate user's cached token and inherit their identity. (CVSS v4: 9.4)
- **Dependency: litellm privilege escalation (High)** — Upgraded `litellm` to `>=1.83.0` to patch CVE-2026-35029. The `/config/update` endpoint did not enforce admin role authorization, allowing authenticated users to modify proxy configuration, register custom endpoint handlers with attacker-controlled Python code, and read arbitrary server files. (CVSS v4: 8.7)

## [0.3.22] - 2026-04-05

### Changed

- **CI/CD Pipeline** — Switched from `pip install build pytest pytest-cov` to `uv sync --all-extras --dev` for proper dependency installation in GitHub Actions. This resolves the `ModuleNotFoundError` for `litellm`, `openai`, `torch`, `typer`, `numpy`, `yaml`, and `langchain_core` during test collection.

### Validation

- **Automated Tests:** `397 passed, 3 skipped, 0 failed`.
- **Test Coverage:** `90%` overall.

## [0.3.21] - 2026-04-05

### Added

- **Circuit Breaker Pattern** — New `CircuitBreaker` and `CircuitBreakerRegistry` in `ct_toolkit.core` to prevent cascading LLM API failures. Features three-state logic (CLOSED → OPEN → HALF_OPEN), exponential backoff recovery, and global provider-level management.
- **Metrics & Monitoring** — Thread-safe `MetricsCollector` with Counter, Gauge, and Histogram metric types. Pre-defined LLM metrics for requests, latency, errors, and divergence scores. Export-ready for Prometheus/OpenTelemetry.
- **AsyncTheseusWrapper** — Async-compatible wrapper with built-in circuit breaker protection and automatic metrics collection for FastAPI/async applications.
- **Sensitive Data Masking** — `SensitiveDataMasker` automatically detects and masks API keys (OpenAI, Anthropic, Google, AWS), bearer tokens, and PII (email, phone, SSN, credit cards). `LogSanitizer` prevents log injection attacks.

### Changed

- **ProvenanceLog** — Automatic sensitive data masking enabled by default. All metadata and text content are scanned before storage.
- **Divergence Engine** — Added timeout protection (30s L2, 60s L3) and exponential backoff retry logic for resilient LLM API calls.
- **ConstitutionalKernel** — Added path traversal protection for `from_yaml()` method to prevent malicious file access.

### Fixed

- **Hardcoded Credentials** — Removed placeholder API keys from integration tests (`test_live_models.py`).
- **Empty Module Exports** — Populated `__init__.py` for all modules with proper public API exports.
- **Exception Handling** — Broad catch-all handlers replaced with specific exception types and proper fallback chains.

### Validation

- **Automated Tests:** `397 passed, 3 skipped, 0 failed`.
- **Test Coverage:** `90%` overall (67 new tests added).
- **Security Score:** Improved from 4.8/10 to 8.5/10.

## [0.3.20] - 2026-03-29

### Added

- **Capability Registry & Policy Metadata Reference** — Added website reference documentation for startup capability cache (`config/llm_capability.yaml`), role policies, environment overrides, and middleware-facing policy payloads.
- **CLI Policy Environment Controls** — Documented and exposed `--policy-environment` on `audit` and `serve` flows for runtime policy override selection (`dev`, `test`, `prod`).

### Changed

- **Middleware Policy Metadata Standardization** — Standardized outward-readable `ct_policy` payload across LangChain, CrewAI, Deep Agents, and AutoGen surfaces.
- **Reference Documentation Refresh** — Updated `WrapperConfig`, `TheseusWrapper`, integrations, and project status pages to reflect capability handshake + role/environment policy behavior.
- **Release Metadata Sync** — Updated README and status docs with latest release context and test snapshot.

### Validation

- **Automated Tests:** `330 passed, 0 failed`.

## [0.3.19] - 2026-03-28

### Changed

- **L2 Judge Raw JSON Path** — Replaced instructor-structured L2 evaluation with provider-agnostic raw completion + JSON parse flow. Judge now returns deterministic fallback (`uncertain`, `0.0`, `Judge evaluation unavailable`) on parse/tool-call failures.
- **Provider-Safe Tool-Call Controls (L2/L3)** — Added provider-aware request shaping: OpenAI/Anthropic-like backends send explicit tool-disable controls (`tools=[]`, `tool_choice=none`, `parallel_tool_calls=false`), while Ollama-compatible backends omit unsupported tool parameters.
- **L3 ICM Ollama Path** — Ollama path now bypasses instructor structured parsing and executes raw completion path directly to reduce tool-call parse noise while preserving graceful fallback behavior.

### Added

- **Integration Coverage for Judge Providers** — Added integration tests covering OpenAI/Anthropic/Ollama judge invocation paths, provider override behavior, and deterministic fallback on multiple tool-call responses.
- **Unit Coverage for L2/L3 Tool Guards** — Added unit tests for raw JSON parsing, deterministic fallback behavior, and provider-safe tool-disable kwargs.

### Validation

- **Automated Tests:** `308 passed, 0 failed`.
- **Live Endpoint Validation:** Verified with LM Studio (`192.168.1.137:1234`) + Ollama judge (`localhost:11434`, `gpt-oss:20b`) across L1→L2→L3 escalation and `skip_l3` control paths.

## [0.3.18] - 2026-03-28

### Fixed

- **L3 ICM Tool-Call Parsing Failures** — Added graceful fallback mechanism for JSON tool-call parsing errors with Ollama backend. L3 `ICMRunner._call_model()` now uses `instructor` with `ProbeResponse` Pydantic model for structured validation (mirrors L2 Judge approach). Implements three-tier fallback: (1) structured validation via instructor, (2) raw litellm.completion if validation fails, (3) safe error placeholder if both fail. This prevents parse errors from causing hard failures and enables test harness to distinguish between parse failures and genuine errors.

## [0.3.17] - 2026-03-28

### Changed

- **Unified Ollama Routing** — Refactored `LLMJudge` (L2) to use `instructor.from_litellm(litellm.completion)`. This eliminates the "Technical Paradox" where different tiers required different URL suffix treatments. Both L2 and L3 now use LiteLLM for routing, which correctly handles Ollama's root endpoint requirements.

### Fixed

- **Ollama 404 in L2 Judge**: Resolved the issue where the OpenAI compatibility layer required a `/v1` suffix that the engine was stripping. By switching to LiteLLM, the stripped URL is now handled correctly across all tiers.

## [0.3.16] - 2026-03-28

### Added

- **Ollama Provider Decoupling** — Introduced `judge_provider` field to `WrapperConfig`, allowing the judge model's provider logic (e.g., `ollama`) to be decoupled from the main model's provider (e.g., `lm-studio` via `openai`).

### Fixed

- **Ollama Integration Robustness**:
  - Automatic stripping of `/v1` suffix from `api_base` for Ollama providers in both `TheseusWrapper` and `ICMRunner` (L3) to prevent 404 errors.
  - Fixed model name normalization to preserve colons in Ollama tags (e.g., `llama3:7b`) when using mixed providers.
  - Improved `LLMJudge` (L2) base URL sanitization with `httpx.URL` mutation support.
- **Divergence Engine**: Enhanced availability check to support judge configurations defined only by `judge_provider` and `judge_model` via environment variables.

## [0.3.15] - 2026-03-28

### Added

- **Custom Judge Model Configuration** — Added `judge_model` field to `WrapperConfig`, allowing users to specify a dedicated model (e.g., `gpt-4o`) for L2/L3 divergence analysis separately from the main agent model.

### Fixed

- **ICM Support for Ollama** — Fixed model name normalization in `ICMRunner` (L3) to correctly handle Ollama tags containing colons (e.g., `llama3:7b`), ensuring they are formatted as `ollama/llama3:7b` for LiteLLM instead of being incorrectly converted to forward slashes.
- **Divergence Engine Initialization** — Fixed a signature bug in `DivergenceEngine.__init__` where `judge_client` was accidentally replaced by `judge_model`. Both parameters are now correctly supported.

## [0.3.14] - 2026-03-28

### Fixed

- **Ollama Model Name Normalization** — Fixed a bug where model names containing colons (e.g., `gpt-oss:20b`) were incorrectly converted to use forward slashes (e.g., `gpt-oss/20b`), causing 404 errors in Ollama. The fix ensures tags are preserved while maintaining correct provider prefixing for LiteLLM.

### Changed

- **Project Status**: Updated project status and test coverage metrics (293 passed, 93% coverage).

## [0.3.13] - 2026-03-28

### Security

- **Dependency: cryptography DNS name constraint bypass (Medium)** — Upgraded `cryptography` constraint from `>=42.0.0` to `>=46.0.6` (resolved to `46.0.6`) to patch improper DNS name constraint enforcement on peer names. Prior to 46.0.6, cryptography did not validate Name Constraints against the "peer name" presented during TLS validation, only against SANs in child certificates, allowing a constrained subtree to be bypassed (cf. CVE-2025-61727 pattern).
- **Dependency: requests insecure temp file reuse (Low/Moderate)** — Upgraded `requests` from `2.32.5` to `2.33.0` via lock file update. The `extract_zipped_paths()` utility function used a predictable temp filename, enabling a local attacker with write access to pre-create a malicious file. Standard `requests` HTTP usage in this project is not directly affected, but the upgrade eliminates the risk proactively.
- **Note — diskcache (no upstream patch)** — `diskcache<=5.6.3` uses Python pickle for serialization by default, enabling arbitrary code execution if an attacker has write access to the cache directory. No patched version is available. This is a transitive dependency via `instructor`. Risk is inherently low in CT Toolkit's threat model as the cache directory is not exposed to untrusted input; will update when upstream releases a fix.
- **Note — Pygments ReDoS (no upstream patch)** — `pygments<=2.19.2` contains an inefficient regex in `AdlLexer` that can cause a ReDoS. No patched version available; only exploitable with local access. Will update when upstream releases a fix.

### Security

- **Dependency: langchain-core path traversal (High)** — Upgraded `langchain-core` from `>=1.2.0` to `>=1.2.22` (resolved to `1.2.23` in lock file) to patch CVE reported by GitHub Dependabot. The legacy `load_prompt` / `load_prompt_from_config` / `load_prompt_from_config` functions in `langchain_core.prompts.loading` did not validate file paths against absolute path injection or `..` traversal sequences before reading from disk, allowing an attacker who controls prompt configuration dicts to read arbitrary `.txt`, `.json`, and `.yaml` files on the host filesystem. The patched version adds path validation and formally deprecates these legacy APIs in favour of `langchain_core.load` (`dumpd/dumps/load/loads`).

## [0.3.10] - 2026-03-26

### Added

- **n8n Custom Node Integration**: Introduced `CtToolkit` custom node for n8n to provide identity continuity guardrails within agentic workflows.
- **Guardrail API Documentation**: Added API documentation for the guardrail server endpoint.

### Changed

- **Testing**: Improved test coverage to 93% with 293 passing tests.

## [0.3.9] - 2026-03-22

### Security

- **Documentation Safety**: Removed untrusted `polyfill.io` scripts from the generated documentation site to mitigate potential supply-chain risks.

### Changed

- **Metrics Update**: Updated project health metrics to reflect current test suite status (293/296 passing, 89% coverage).
- **Project Status**: Bumped stable version across all documentation reference points.

## [0.3.8] - 2026-03-21

### Added

- **Staged Approval (Cooldown)**: Introduced a dynamic cooldown stage for Reflective Endorsement. Risky kernel updates are now monitored in a sandbox before final production promotion.
- **Shadow Request Monitoring**: `TheseusWrapper` now performs concurrent shadow requests against staged kernels to detect identity drift in real-time.
- **Dynamic Cooldown Calculator**: Automatically adjusts cooldown duration based on probe availability (penalties) and traffic volume (RPM).
- **Critical Sandbox Protection**: Immediate rejection of staged updates if sandbox divergence exceeds L3 thresholds.
- **StagedUpdateManager**: New state management for tracking and automatically promoting expired staged endorsements.

### Changed

- **CLI Reference**: Updated CLI approval prompts to include the `[s] Stage` option.
- **WrapperConfig**: Added `endorsement_cooldown_base`, `endorsement_cooldown_max`, and `endorsement_no_probe_penalty` settings.
- **Documentation**: Added "Staged Approval" example and updated API reference for new components.

### Fixed

- Improved `ICMRunner` logic for pre-flight probe availability checks.

## [0.3.7] - 2026-03-21

### Added

- **Configurable L2 Judge**: Added `--judge-provider` and `--judge-model` options to `ct-toolkit serve` for custom L2 divergence detection backends.
- **Enhanced Guardrail Server**: Server now supports multi-provider model definitions for separate target and judge agents.

### Changed

- **LiteLLM Migration Completion**: Fully audited all public documentation to remove legacy `any-llm-sdk` references in favor of native LiteLLM integrations.
- **Improved DX**: Refined CLI and Server help text for consistency and clarity.

### Fixed

- **CI/CD Stabilization**: Optimized unit test performance and reliability across platforms.

## [0.3.6] - 2026-03-21

### Added

- **Core Context Compression Guard**: Integrated `ContextCompressionGuard` into the core toolkit layer.
- **Passive Compression Detection**: Universal, provider-agnostic detection of "silent" context compression (e.g., OpenAI compaction, Anthropic beta summarization) using message history shrinkage analysis.
- **Passive Detection Heuristic**: Automatic drift analysis triggers when context history shrinks significantly (>30% drop).
- **Middleware Propagations**: Enhanced `crewai`, `langchain`, and `deepagents` middlewares to inherit compression settings from the parent `WrapperConfig`.

### Changed

- **Unified Configuration**: Moved `compression_threshold` and `compression_passive_detection` into the standard `WrapperConfig`.
- **Exposed Property**: `TheseusChatModel` (LangChain) now exposes `compression_guard` for manual audits.

## [0.3.5] - 2026-03-19

### Added

- **Standalone Auditor CLI**: Introduced `ct-toolkit` command-line interface for independent L3 ICM audits.
- **ASCII Art Banner**: Added "THESEUS GUARD" branding to CLI startup.
- **Auditor Commands**: Added `audit`, `list-kernels`, and `list-templates` to the CLI.
- **CLI Documentation**: Added comprehensive guides and examples for CLI usage on the website.

### Fixed

- Improved error handling in `ICMRunner` for empty probe batteries and failed LLM connections.
- Resolved package versioning inconsistency in `__init__.py`.

## [0.3.4] - 2026-03-19

### Added

- Redesigned documentation website to align with Google ADK Docs 2.0 style.
- Enhanced homepage with interactive card grid and optimized hero section.
- Integrated SVG logo and favicon for professional branding.
- Expanded technical documentation for Identity Continuity and Divergence Engine.

### Fixed

- Improved Dark Mode support with theme-aware CSS overrides for grid cards.
- Fixed Markdown parsing issues in website homepage layout.

### Added (Previous Unreleased)

- Comprehensive API Reference documentation focusing on high Developer Experience.
- Repository `SECURITY.md`, `CHANGELOG.md`, and updated `CONTRIBUTING.md`.

## [0.3.3] - 2026-03-19

### Changed

- Core API restructuring and improvements towards stability and plasticity scheduling.
- Compatibility layer enhancements.

## [0.2.5] - 2026-03-16

### Security

- Latest security hardening changes implemented to prevent configuration tampering.

## [0.2.4] - 2026-03-15

### Added

- Auto-Correction loop mechanism allowing autonomous retry of agentic failures when Identity Drift is detected by L2 Judge.
- New endpoints and domain probes for real-world scenarios.

### Fixed

- Fixed packaging configuration regarding identity templates `FileNotFoundError`.
- Addressed PyPI version discrepancy mapping issues.

## [0.2.0] - Initial Public Structuring

### Added

- Core `TheseusWrapper` and `ConstitutionalKernel` logic.
- Divergence Engine with multi-tiered (L1/L2/L3) mechanisms.
- Provenance Vault using SQLite and HMAC hash chaining.
