"""
ct_toolkit.core.wrapper
------------------------
TheseusWrapper: A proxy wrapping the existing LLM API client.

User only changes 2 lines:

    # Before
    client = openai.OpenAI()

    # After
    client = TheseusWrapper(openai.OpenAI())

In the background:
  1. Constitutional Kernel is injected into the system prompt.
  2. Each request/response is written to the Provenance Log with HMAC signature.
  3. Embedding cosine similarity (L1 ECS) is calculated.
  4. If the threshold is exceeded, L2/L3 is triggered.
  5. Staged Approval (Cooldown): Risky kernel updates are monitored via shadow requests
     before being promoted to production.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor, wait as wait_futures
import time
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Callable, Dict, List, Iterator, AsyncIterator

import litellm
import yaml

from ct_toolkit.core.kernel import ConstitutionalKernel
from ct_toolkit.core.compatibility import CompatibilityLayer, CompatibilityResult
from ct_toolkit.core.circuit_breaker import CircuitBreaker, CircuitBreakerError, CircuitState
from ct_toolkit.core.exceptions import (
    MissingClientError, 
    CriticalSandboxDivergenceError
)
from ct_toolkit.core.integrity import IntegrityMonitor
from ct_toolkit.divergence.scheduler import RiskProfile
from ct_toolkit.core.compression_guard import ContextCompressionGuard
from ct_toolkit.endorsement.reflective import (
    ReflectiveEndorsement, 
    StagedUpdateManager,
    CooldownCalculator
)
from ct_toolkit.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class WrapperConfig:
    """TheseusWrapper configuration."""
    project_root: str | Path | None = None
    kernel_path: str | Path | None = None
    template: str = "general"
    kernel_name: str = "default"
    divergence_l1_threshold: float = 0.15   # ECS — warning
    divergence_l2_threshold: float = 0.30   # Trigger LLM-as-judge
    divergence_l3_threshold: float = 0.50   # Trigger ICM battery
    vault_type: str = "local"               # "local" | "cloud"
    vault_path: str = "./ct_provenance.db"
    auto_inject_kernel: bool = True
    log_requests: bool = True
    drift_alert_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    judge_client: Any = None         # Separate provider client for L2/L3
    judge_model: str | None = None   # Custom model for L2/L3 judge
    judge_provider: str | None = None # Explicit provider for L2/L3 judge (e.g. "ollama")
    embedding_client: Any = None     # Client for L1 ECS embedding (falls back to main client if compatible)
    embedding_model: str = "text-embedding-3-small"
    embedding_provider: str | None = None
    use_fastembed: bool = False
    rigorous_mode: bool | None = None # Preferred name for running all tiers every call
    enterprise_mode: bool = False    # Deprecated alias for rigorous_mode
    parent_kernel: ConstitutionalKernel | None = None  # Propagated from mother agent
    
    # -- Auto-Correction Loop --
    auto_correction: bool = False
    max_correction_retries: int = 1

    # -- Dynamic Stability-Plasticity Scheduling --
    elasticity_max_thresholds: tuple[float, float, float] | None = None  # (max_l1, max_l2, max_l3)
    elasticity_growth_rate: float | None = None
    risk_profile: RiskProfile | None = None

    # -- Capability Handshake & Discovery --
    capability_cache_file: str = "config/llm_capability.yaml"
    capability_refresh_interval_s: int = 3600
    capability_enable_active_handshake: bool = False
    capability_force_refresh: bool = False
    policy_role: str = "main"
    policy_environment: str = "prod"

    strict_embedding: bool = False   # Raise error if embedding API fails

    # -- Context Compression Guard --
    compression_threshold: float = 0.85
    compression_passive_detection: bool = True

    # -- Staged Approval (Cooldown) --
    endorsement_cooldown_base: int = CooldownCalculator.DEFAULT_BASE_SECONDS
    endorsement_cooldown_max: int = CooldownCalculator.DEFAULT_MAX_SECONDS
    endorsement_no_probe_penalty: int = CooldownCalculator.DEFAULT_NO_PROBE_PENALTY_S

    # -- Background Execution & Circuit Breaker --
    shadow_background: bool = False
    l3_background: bool = False
    circuit_breaker_on_critical: bool = True
    circuit_breaker_recovery_timeout: float = 300.0


@dataclass
class CTResponse:
    """Enriched response returned by TheseusWrapper."""
    content: str
    provider: str
    model: str
    divergence_score: float | None = None
    divergence_tier: str | None = None      # "ok" | "l1_warning" | "l2_judge" | "l3_icm"
    provenance_id: str | None = None
    raw_response: Any = field(default=None, repr=False)
    # Sandbox metadata if staged updates are active
    sandbox_divergence: float | None = None

    def __str__(self) -> str:
        return self.content


@dataclass
class CTStreamChunk:
    """Enriched streaming chunk returned by TheseusWrapper.stream_chat / astream_chat."""
    delta: str
    accumulated_content: str
    provider: str
    model: str
    is_final: bool = False
    divergence_score: float | None = None
    divergence_tier: str | None = None
    provenance_id: str | None = None
    sandbox_divergence: float | None = None
    raw_chunk: Any = field(default=None, repr=False)

    def __str__(self) -> str:
        return self.delta


class TheseusWrapper:
    """
    Identity-continuity proxy wrapping the LLM API client.

    Supported providers: openai (default), anthropic, ollama, google, etc.
    (Any provider supported by LiteLLM)

    Usage:
        from ct_toolkit import TheseusWrapper
        
        # 1. Using a specific provider (Simplest)
        client = TheseusWrapper(provider="openai")
        
        # 2. Wrapping an existing client (Backward compatible)
        import openai
        client = TheseusWrapper(openai.OpenAI())

        response = client.chat("Hello, how can I help you?", model="gpt-4o-mini")
        print(response.content)
        print(f"Divergence: {response.divergence_score}")
    """

    def __init__(
        self,
        client: Any = None,
        config: WrapperConfig | None = None,
        *,
        provider: str | None = None,
        kernel_path: str | Path | None = None,
        template: str = "general",
        kernel_name: str = "default",
        project_root: str | Path | None = None,
    ) -> None:
        """
        Initializes TheseusWrapper.
        """
        self._config = config or WrapperConfig(
            kernel_path=kernel_path,
            template=template,
            kernel_name=kernel_name,
            project_root=project_root,
        )

        # Backward compatibility: rigorous_mode is the new name for enterprise_mode.
        if self._config.rigorous_mode is not None:
            if self._config.enterprise_mode != self._config.rigorous_mode:
                logger.warning(
                    "Both `rigorous_mode` and deprecated `enterprise_mode` were provided with different values. "
                    "Using `rigorous_mode` value."
                )
            self._config.enterprise_mode = self._config.rigorous_mode
        elif self._config.enterprise_mode:
            logger.warning(
                "`enterprise_mode` is deprecated and will be removed in a future release. "
                "Please use `rigorous_mode` instead."
            )
        
        if client is None and provider is None:
            # Default to openai if nothing specified
            provider = "openai"
            
        self._client = client
        self._provider = provider or self._detect_provider(client)
        self._config.policy_environment = (
            self._config.policy_environment
            or os.environ.get("CT_TOOLKIT_ENV")
            or os.environ.get("CT_ENV")
            or os.environ.get("ENV")
            or "prod"
        )
        self._project_root = (
            Path(self._config.project_root)
            if self._config.project_root
            else Path(os.getcwd())
        )

        # ── Capability Handshake / Discovery Cache ──
        self._capability_cache_path = self._resolve_capability_cache_path()
        self._capability_registry = self._initialize_capability_registry()

        # Auto-derive RiskProfile from discovered model capabilities unless explicitly provided.
        self._auto_risk_profile = self._config.risk_profile is None
        if self._auto_risk_profile:
            startup_caps = self._get_model_capabilities(model=None)
            self._apply_discovered_risk_profile(startup_caps)

        self._kernel = self._load_kernel()

        # If a parent kernel exists, merge it into our own kernel as axiomatic constraints
        if self._config.parent_kernel:
            logger.info(f"Merging parent kernel '{self._config.parent_kernel.name}' into current kernel.")
            self._kernel = self._kernel.merge(self._config.parent_kernel)
        self._compatibility: CompatibilityResult = CompatibilityLayer.check(
            template=self._config.template,
            kernel=self._config.kernel_name,
        )

        # ── Background Executor & Circuit Breaker ──
        self._bg_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ct_shadow_bg")
        self._background_futures: list[Future[Any]] = []
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._last_background_error: Exception | None = None
        self._circuit_breaker = CircuitBreaker(
            name="theseus_guard_breaker",
            failure_threshold=1,
            recovery_timeout=self._config.circuit_breaker_recovery_timeout,
        )

        self._provenance_log = self._init_provenance_log()
        self._identity_layer = self._init_identity_layer()
        self._divergence_engine = self._init_divergence_engine()
        self._last_model: str = "unknown"

        # ── Context Compression Guard ──
        self._compression_guard = ContextCompressionGuard(
            self, 
            threshold=self._config.compression_threshold
        )
        self._shadow_history: Optional[List[Dict[str, str]]] = None

        # ── Staged Approval (Cooldown) ──
        self._staged_manager = StagedUpdateManager()

        # ── Integrity Monitoring ──
        self._integrity_monitor = IntegrityMonitor()
        self._register_monitored_files()

        self._log_init()

    def _register_monitored_files(self):
        """
        Finds and registers all critical configuration files for integrity monitoring.
        """
        logger.debug("Registering configuration files for integrity monitoring.")
        
        # 1. Scan built-in package files
        try:
            from importlib.resources import files
            package_root = Path(str(files("ct_toolkit")))
            
            internal_patterns = [
                "kernels/**/*.yaml",
                "identity/templates/**/*.yaml",
                "endorsement/probes/**/*.json",
            ]

            for pattern in internal_patterns:
                for file_path in package_root.rglob(pattern):
                    if file_path.is_file():
                        self._integrity_monitor.register_file(file_path)

        except Exception:
            package_root = Path(__file__).parent.parent
            for pattern in internal_patterns:
                for file_path in package_root.rglob(pattern):
                    if file_path.is_file():
                        self._integrity_monitor.register_file(file_path)

        # 2. Scan user's custom config directory
        if self._project_root:
            user_config_dir = self._project_root / "config"
            if user_config_dir.is_dir():
                logger.debug(f"Scanning user config directory for integrity monitoring: {user_config_dir}")
                user_patterns = ["*_kernel.yaml", "*_identity.yaml", "*_probes.json", "*.yaml", "*.json"]
                for pattern in user_patterns:
                    for file_path in user_config_dir.rglob(pattern):
                        if file_path.is_file():
                            self._integrity_monitor.register_file(file_path)

    # ── Capability Handshake / Discovery ─────────────────────────────────────

    def _resolve_capability_cache_path(self) -> Path:
        """Resolve capability cache path, relative to project_root when needed."""
        cache_path = Path(self._config.capability_cache_file)
        if cache_path.is_absolute():
            return cache_path
        return self._project_root / cache_path

    def _initialize_capability_registry(self) -> dict[str, Any]:
        """Load or create capability cache and ensure provider defaults exist."""
        cache_path = self._capability_cache_path
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        registry = self._load_capability_registry_from_disk(cache_path)
        registry_roles = registry.setdefault("role_policies", {})
        default_roles = self._default_role_policies()
        for role_name, defaults in default_roles.items():
            role_entry = registry_roles.setdefault(role_name, {})
            for key, value in defaults.items():
                role_entry.setdefault(key, value)

        registry_environments = registry.setdefault("environment_overrides", {})
        default_environment_overrides = self._default_environment_role_overrides()
        for env_name, env_defaults in default_environment_overrides.items():
            env_entry = registry_environments.setdefault(env_name, {})
            env_role_policies = env_entry.setdefault("role_policies", {})
            for role_name, role_defaults in env_defaults.get("role_policies", {}).items():
                role_entry = env_role_policies.setdefault(role_name, {})
                for key, value in role_defaults.items():
                    role_entry.setdefault(key, value)

        providers = registry.setdefault("providers", {})
        provider_entry = providers.setdefault(self._provider, {})

        default_caps = self._perform_capability_handshake(model=None)
        provider_entry.setdefault("models", {})
        provider_entry["defaults"] = {
            "capabilities": default_caps,
            "source": "startup_handshake",
            "last_checked": time.time(),
        }
        provider_entry["refresh_interval_s"] = self._config.capability_refresh_interval_s

        self._write_capability_registry(cache_path, registry)
        return registry

    @staticmethod
    def _load_capability_registry_from_disk(cache_path: Path) -> dict[str, Any]:
        if not cache_path.exists():
            return {
                "version": 1,
                "generated_at": time.time(),
                "providers": {},
            }

        try:
            with cache_path.open("r", encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle) or {}
            if not isinstance(loaded, dict):
                raise ValueError("capability cache root must be a mapping")
            loaded.setdefault("version", 1)
            loaded.setdefault("generated_at", time.time())
            loaded.setdefault("providers", {})
            return loaded
        except Exception as exc:
            logger.warning(f"Capability cache could not be loaded, rebuilding: {exc}")
            return {
                "version": 1,
                "generated_at": time.time(),
                "providers": {},
            }

    def _write_capability_registry(self, cache_path: Path, registry: dict[str, Any]) -> None:
        try:
            registry["updated_at"] = time.time()
            with cache_path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(registry, handle, sort_keys=False, allow_unicode=False)
            if hasattr(self, "_integrity_monitor") and cache_path.is_file():
                self._integrity_monitor.register_file(cache_path)
        except Exception as exc:
            logger.warning(f"Capability cache write skipped: {exc}")

    @staticmethod
    def _default_role_policies() -> dict[str, dict[str, bool]]:
        return {
            "main": {
                "allow_tool_call": True,
                "allow_reasoning": True,
                "allow_multimodal": True,
            },
            "sub": {
                "allow_tool_call": True,
                "allow_reasoning": True,
                "allow_multimodal": True,
            },
            "judge": {
                "allow_tool_call": False,
                "allow_reasoning": False,
                "allow_multimodal": False,
            },
            "l3": {
                "allow_tool_call": False,
                "allow_reasoning": True,
                "allow_multimodal": False,
            },
        }

    @staticmethod
    def _default_environment_role_overrides() -> dict[str, dict[str, dict[str, dict[str, bool]]]]:
        return {
            "dev": {
                "role_policies": {
                    "main": {
                        "allow_tool_call": True,
                        "allow_reasoning": True,
                        "allow_multimodal": True,
                    },
                    "sub": {
                        "allow_tool_call": True,
                        "allow_reasoning": True,
                        "allow_multimodal": True,
                    },
                }
            },
            "test": {
                "role_policies": {
                    "main": {
                        "allow_tool_call": False,
                        "allow_reasoning": True,
                        "allow_multimodal": False,
                    },
                    "sub": {
                        "allow_tool_call": False,
                        "allow_reasoning": True,
                        "allow_multimodal": False,
                    },
                    "judge": {
                        "allow_tool_call": False,
                        "allow_reasoning": False,
                        "allow_multimodal": False,
                    },
                    "l3": {
                        "allow_tool_call": False,
                        "allow_reasoning": True,
                        "allow_multimodal": False,
                    },
                }
            },
            "prod": {
                "role_policies": {
                    "main": {
                        "allow_tool_call": True,
                        "allow_reasoning": True,
                        "allow_multimodal": True,
                    },
                    "sub": {
                        "allow_tool_call": True,
                        "allow_reasoning": True,
                        "allow_multimodal": True,
                    },
                    "judge": {
                        "allow_tool_call": False,
                        "allow_reasoning": False,
                        "allow_multimodal": False,
                    },
                    "l3": {
                        "allow_tool_call": False,
                        "allow_reasoning": True,
                        "allow_multimodal": False,
                    },
                }
            },
        }

    def _perform_capability_handshake(self, model: str | None) -> dict[str, bool]:
        """
        Determine model capabilities via static discovery + optional LiteLLM metadata.
        """
        capabilities = self._infer_capabilities(provider=self._provider, model=model)

        if not self._config.capability_enable_active_handshake:
            return capabilities

        model_for_lookup = model or self._default_model_for_provider(self._provider)
        if not model_for_lookup:
            return capabilities

        # Lightweight active handshake: use LiteLLM parameter metadata when available.
        try:
            get_params = getattr(litellm, "get_supported_openai_params", None)
            if callable(get_params):
                params = get_params(model=model_for_lookup, custom_llm_provider=self._provider)
                if isinstance(params, (list, tuple, set)):
                    param_set = {str(p) for p in params}
                else:
                    param_set = set()
                if "tools" in param_set or "tool_choice" in param_set:
                    capabilities["tool_call"] = True
                if "reasoning_effort" in param_set:
                    capabilities["reasoning"] = True
                if "modalities" in param_set:
                    capabilities["audio"] = True
                if "image" in param_set:
                    capabilities["image"] = True
        except Exception as exc:
            logger.debug(f"Capability active handshake metadata unavailable: {exc}")

        capabilities["multimodal"] = bool(
            capabilities.get("image")
            or capabilities.get("audio")
            or capabilities.get("video")
        )
        return capabilities

    @staticmethod
    def _default_model_for_provider(provider: str) -> str | None:
        defaults = {
            "openai": "gpt-4o-mini",
            "anthropic": "claude-3-5-sonnet-latest",
            "ollama": "llama3",
            "google": "gemini-1.5-flash",
        }
        return defaults.get(provider)

    @staticmethod
    def _infer_capabilities(provider: str, model: str | None) -> dict[str, bool]:
        """Heuristic capability discovery used for startup and fallback refresh."""
        model_name = (model or "").lower()
        provider_name = (provider or "unknown").lower()

        image = False
        audio = False
        video = False
        tool_call = False
        reasoning = False

        if provider_name in {"openai", "anthropic", "google"}:
            tool_call = True

        if provider_name == "openai":
            image = True
            audio = True

        if provider_name == "google":
            image = True
            audio = True
            video = True

        if provider_name == "ollama":
            image = bool(re.search(r"llava|moondream|vision", model_name))
            tool_call = False

        if re.search(r"o1|o3|r1|reason|thinking|deepseek-r1", model_name):
            reasoning = True

        multimodal = image or audio or video
        return {
            "text": True,
            "image": image,
            "audio": audio,
            "video": video,
            "multimodal": multimodal,
            "tool_call": tool_call,
            "reasoning": reasoning,
        }

    def _get_model_capabilities(self, model: str | None) -> dict[str, bool]:
        """Return cached capabilities for provider/model, refreshing on-demand."""
        providers = self._capability_registry.setdefault("providers", {})
        provider_entry = providers.setdefault(self._provider, {"models": {}})
        provider_entry.setdefault("models", {})
        provider_entry.setdefault("defaults", {"capabilities": self._infer_capabilities(self._provider, None)})

        refresh_interval = float(provider_entry.get("refresh_interval_s", self._config.capability_refresh_interval_s))
        should_refresh = self._config.capability_force_refresh

        if model:
            model_entry = provider_entry["models"].get(model)
            if model_entry is None:
                should_refresh = True
            else:
                last_checked = float(model_entry.get("last_checked", 0.0))
                if (time.time() - last_checked) >= refresh_interval:
                    should_refresh = True

            if should_refresh:
                caps = self._perform_capability_handshake(model=model)
                provider_entry["models"][model] = {
                    "capabilities": caps,
                    "source": "refresh_discovery",
                    "last_checked": time.time(),
                }
                self._apply_discovered_risk_profile(caps)
                self._write_capability_registry(self._capability_cache_path, self._capability_registry)
                return caps

            return dict(model_entry.get("capabilities", {}))

        default_entry = provider_entry.get("defaults", {})
        return dict(default_entry.get("capabilities", {}))

    @staticmethod
    def _risk_profile_from_capabilities(capabilities: dict[str, bool]) -> RiskProfile:
        return RiskProfile(
            has_tool_calling=bool(capabilities.get("tool_call", False)),
            has_vision_audio=bool(
                capabilities.get("image", False)
                or capabilities.get("audio", False)
                or capabilities.get("video", False)
                or capabilities.get("multimodal", False)
            ),
            mcp_server_count=0,
        )

    def _apply_discovered_risk_profile(self, capabilities: dict[str, bool]) -> None:
        """Apply auto-managed risk profile updates from discovered capabilities."""
        if not getattr(self, "_auto_risk_profile", False):
            return

        new_profile = self._risk_profile_from_capabilities(capabilities)
        old_profile = self._config.risk_profile
        changed = (
            old_profile is None
            or old_profile.has_tool_calling != new_profile.has_tool_calling
            or old_profile.has_vision_audio != new_profile.has_vision_audio
            or old_profile.mcp_server_count != new_profile.mcp_server_count
        )
        if not changed:
            return

        self._config.risk_profile = new_profile
        if hasattr(self, "_divergence_engine"):
            self._divergence_engine = self._init_divergence_engine()

    def resolve_llm_policy(
        self,
        model: str | None = None,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Resolve effective role policy using cached capabilities and YAML role rules."""
        role_name = (role or self._config.policy_role or "main").lower()
        environment_name = (self._config.policy_environment or "prod").lower()
        role_defaults = self._default_role_policies()
        registry_roles = self._capability_registry.setdefault("role_policies", {})
        resolved_role = dict(role_defaults.get(role_name, role_defaults["main"]))
        resolved_role.update(registry_roles.get(role_name, {}))

        environment_defaults = self._default_environment_role_overrides()
        registry_environments = self._capability_registry.setdefault("environment_overrides", {})
        environment_entry = registry_environments.get(environment_name, environment_defaults.get(environment_name, {}))
        environment_role_overrides = (environment_entry.get("role_policies", {}) if isinstance(environment_entry, dict) else {})
        resolved_role.update(environment_role_overrides.get(role_name, {}))

        capabilities = self._get_model_capabilities(model)
        effective = {
            "text": bool(capabilities.get("text", True)),
            "image": bool(capabilities.get("image", False) and resolved_role["allow_multimodal"]),
            "audio": bool(capabilities.get("audio", False) and resolved_role["allow_multimodal"]),
            "video": bool(capabilities.get("video", False) and resolved_role["allow_multimodal"]),
            "tool_call": bool(capabilities.get("tool_call", False) and resolved_role["allow_tool_call"]),
            "reasoning": bool(capabilities.get("reasoning", False) and resolved_role["allow_reasoning"]),
        }
        effective["multimodal"] = bool(
            effective["image"] or effective["audio"] or effective["video"]
        )

        return {
            "role": role_name,
            "environment": environment_name,
            "role_policy": resolved_role,
            "capabilities": capabilities,
            "effective": effective,
        }

    def propagate_policy_metadata(
        self,
        model: str | None = None,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Return serializable policy metadata for downstream middleware/frameworks."""
        resolved = self.resolve_llm_policy(model=model, role=role)
        return {
            "role": resolved["role"],
            "environment": resolved["environment"],
            "effective": resolved["effective"],
        }

    def resolve_tool_control_kwargs(
        self,
        model: str | None = None,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Return provider-safe tool control kwargs from resolved role policy."""
        policy = self.resolve_llm_policy(model=model, role=role)
        if policy["effective"]["tool_call"]:
            return {}
        if self._provider == "ollama":
            return {}
        return {
            "tools": [],
            "tool_choice": "none",
            "parallel_tool_calls": False,
        }

    def _record_passive_capability_discovery(self, model: str, raw_response: Any) -> None:
        """Update capability registry from observed responses without extra API calls."""
        discovered: dict[str, bool] = {}

        try:
            # Detect tool calling usage in observed output.
            if isinstance(raw_response, dict):
                choices = raw_response.get("choices") or []
                if choices:
                    message = (choices[0].get("message") or {})
                    if message.get("tool_calls"):
                        discovered["tool_call"] = True
                    content = message.get("content")
                    if isinstance(content, str) and content.strip().startswith("<think>"):
                        discovered["reasoning"] = True
            elif hasattr(raw_response, "choices") and raw_response.choices:
                msg = getattr(raw_response.choices[0], "message", None)
                if msg is not None:
                    if getattr(msg, "tool_calls", None):
                        discovered["tool_call"] = True
                    content = getattr(msg, "content", "")
                    if isinstance(content, str) and content.strip().startswith("<think>"):
                        discovered["reasoning"] = True
        except Exception:
            return

        if not discovered:
            return

        providers = self._capability_registry.setdefault("providers", {})
        provider_entry = providers.setdefault(self._provider, {"models": {}})
        provider_entry.setdefault("models", {})
        current = provider_entry["models"].get(model, {
            "capabilities": self._infer_capabilities(self._provider, model),
            "source": "passive_discovery",
            "last_checked": time.time(),
        })
        current_caps = dict(current.get("capabilities", {}))
        for key, value in discovered.items():
            current_caps[key] = bool(current_caps.get(key, False) or value)
        current_caps["multimodal"] = bool(
            current_caps.get("image") or current_caps.get("audio") or current_caps.get("video")
        )
        current["capabilities"] = current_caps
        current["source"] = "passive_discovery"
        current["last_checked"] = time.time()
        provider_entry["models"][model] = current
        self._apply_discovered_risk_profile(current_caps)
        self._write_capability_registry(self._capability_cache_path, self._capability_registry)


    # ── Factory / Init ─────────────────────────────────────────────────────────

    def _detect_provider(self, client: Any) -> str:
        if client is None:
            return "unknown"
            
        if isinstance(client, str):
            return client.lower()

        try:
            if hasattr(client, "provider") and isinstance(client.provider, str):
                return client.provider

            module = type(client).__module__.lower()
            if "openai" in module:
                return "openai"
            if "anthropic" in module:
                return "anthropic"
            if "ollama" in module:
                return "ollama"
            if "google" in module:
                return "google"
            if "cohere" in module:
                return "cohere"
            if "mistral" in module:
                return "mistral"
        except Exception:
            pass
        return "unknown"

    def _load_kernel(self) -> ConstitutionalKernel:
        if self._config.kernel_path:
            logger.debug(f"Loading kernel from explicit path: {self._config.kernel_path}")
            return ConstitutionalKernel.from_yaml(self._config.kernel_path)

        safe_name = os.path.basename(self._config.kernel_name)
        
        if self._project_root:
            user_config_path = self._project_root / "config" / f"{safe_name}.yaml"
            if user_config_path.exists():
                logger.debug(f"Loading kernel from user config: {user_config_path}")
                return ConstitutionalKernel.from_yaml(user_config_path)

        try:
            from importlib.resources import files
            kernel_resource = files("ct_toolkit.kernels").joinpath(f"{safe_name}.yaml")
            if kernel_resource.is_file():
                logger.debug(f"Loading built-in kernel: {safe_name}.yaml")
                return ConstitutionalKernel.from_yaml(kernel_resource)
        except (ImportError, FileNotFoundError):
            kernel_path = (
                Path(__file__).parent.parent
                / "kernels"
                / f"{safe_name}.yaml"
            )
            if kernel_path.exists():
                logger.debug(f"Loading built-in kernel (fallback): {kernel_path}")
                return ConstitutionalKernel.from_yaml(kernel_path)

        logger.warning(
            f"Kernel '{self._config.kernel_name}' not found in user config or built-ins. Using default."
        )
        return ConstitutionalKernel.default()

    def _init_provenance_log(self) -> Any:
        from ct_toolkit.provenance.log import ProvenanceLog
        return ProvenanceLog(
            vault_type=self._config.vault_type,
            vault_path=self._config.vault_path,
        )

    def _init_identity_layer(self) -> Any:
        from ct_toolkit.identity.embedding import IdentityEmbeddingLayer
        
        emb_client = self._config.embedding_client
        emb_provider = self._config.embedding_provider
        if emb_client is None:
            if self._provider == "openai":
                emb_client = self._client
                emb_provider = emb_provider or "openai"
            elif self._provider == "ollama" and self._client is not None:
                emb_client = self._client
                emb_provider = emb_provider or "ollama"
            else:
                logger.debug(f"Provider {self._provider} does not support default embeddings. Falling back to local.")

        return IdentityEmbeddingLayer(
            template=self._config.template,
            embedding_client=emb_client,
            embedding_model=self._config.embedding_model,
            project_root=self._project_root,
            strict_embedding=self._config.strict_embedding,
            embedding_provider=emb_provider,
            use_fastembed=self._config.use_fastembed,
        )

    def _init_divergence_engine(self) -> Any:
        from ct_toolkit.divergence.engine import DivergenceEngine
        from ct_toolkit.divergence.scheduler import ElasticityScheduler
        
        scheduler = None
        if self._config.elasticity_max_thresholds and self._config.elasticity_growth_rate:
            scheduler = ElasticityScheduler(
                base_thresholds=(
                    self._config.divergence_l1_threshold,
                    self._config.divergence_l2_threshold,
                    self._config.divergence_l3_threshold
                ),
                max_thresholds=self._config.elasticity_max_thresholds,
                growth_rate=self._config.elasticity_growth_rate,
                risk_profile=self._config.risk_profile
            )

        return DivergenceEngine(
            identity_layer=self._identity_layer,
            kernel=self._kernel,
            template=self._config.template,
            provider=self._config.judge_provider or (self._provider if self._config.judge_client else None),
            judge_client=self._config.judge_client,
            judge_model=self._config.judge_model,
            l1_threshold=self._config.divergence_l1_threshold,
            l2_threshold=self._config.divergence_l2_threshold,
            l3_threshold=self._config.divergence_l3_threshold,
            enterprise_mode=self._config.enterprise_mode,
            scheduler=scheduler,
            project_root=self._project_root,
            provenance_log=self._provenance_log,
            policy_resolver=self.resolve_llm_policy,
            l3_background=self._config.l3_background,
            on_critical_callback=self._on_critical_l3_report,
        )

    def _on_critical_l3_report(self, report: Any) -> None:
        """Callback invoked when L3 ICM detects an unhealthy/critical state."""
        if self._config.circuit_breaker_on_critical:
            self._circuit_breaker.record_failure()
        if self._config.drift_alert_callback:
            try:
                self._config.drift_alert_callback({
                    "event": "l3_critical",
                    "health_score": getattr(report, "health_score", None),
                    "risk_level": getattr(report, "risk_level", None),
                    "critical_failures": getattr(report, "critical_failures", []),
                })
            except Exception as exc:
                logger.warning(f"drift_alert_callback error: {exc}")

    def _check_circuit_breaker(self) -> None:
        """Raises CircuitBreakerError if circuit breaker has been tripped by a critical violation."""
        if not self._config.circuit_breaker_on_critical:
            return
        engine_tripped = getattr(self._divergence_engine, "circuit_breaker_tripped", False)
        if not self._circuit_breaker.allow_request() or engine_tripped:
            state = self._circuit_breaker.state if not self._circuit_breaker.allow_request() else CircuitState.OPEN
            retry_after = self._circuit_breaker.time_until_recovery()
            raise CircuitBreakerError(state, retry_after)

    def reset_circuit_breaker(self) -> None:
        """Resets the wrapper and divergence engine circuit breakers."""
        self._circuit_breaker.reset()
        if hasattr(self._divergence_engine, "reset_circuit_breaker"):
            self._divergence_engine.reset_circuit_breaker()
        self._last_background_error = None

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Returns the wrapper's circuit breaker instance."""
        return self._circuit_breaker

    @property
    def last_background_error(self) -> Exception | None:
        """Returns the last error raised by a background shadow task, if any."""
        return self._last_background_error

    def wait_for_background_tasks(self, timeout: float = 5.0) -> None:
        """Waits for pending background shadow requests and L3 ICM futures to finish."""
        if self._background_futures:
            _, not_done = wait_futures(self._background_futures, timeout=timeout)
            self._background_futures = list(not_done)
        if hasattr(self._divergence_engine, "wait_for_background_l3"):
            self._divergence_engine.wait_for_background_l3(timeout=timeout)

    async def await_background_tasks(self, timeout: float = 5.0) -> None:
        """Asynchronously waits for pending background shadow tasks and L3 ICM tasks."""
        if self._background_tasks:
            tasks_snapshot = list(self._background_tasks)
            await asyncio.wait(tasks_snapshot, timeout=timeout)
        if self._background_futures:
            _, not_done = wait_futures(self._background_futures, timeout=timeout)
            self._background_futures = list(not_done)
        if hasattr(self._divergence_engine, "await_background_l3"):
            await self._divergence_engine.await_background_l3(timeout=timeout)

    def _log_init(self) -> None:
        compat_note = (
            f" [{self._compatibility.level.value}]"
            + (f" — {self._compatibility.notes}" if self._compatibility.notes else "")
        )
        logger.info(
            f"TheseusWrapper initialized | "
            f"provider={self._provider} | "
            f"kernel={self._kernel.name} | "
            f"template={self._config.template}"
            f"{compat_note}"
        )

    # ── Main Chat Interface ─────────────────────────────────────────────────────

    def chat(
        self,
        message: str,
        *,
        model: str | None = None,
        system: str | None = None,
        history: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> CTResponse | Iterator[CTStreamChunk]:
        """
        Sends a single message, returning the response with identity protection.
        If stream=True is passed, returns an iterator of CTStreamChunk objects.
        """
        if kwargs.get("stream"):
            kwargs.pop("stream", None)
            return self.stream_chat(
                message,
                model=model,
                system=system,
                history=history,
                **kwargs,
            )

        # --- Pre-flight Checks ---
        self._check_circuit_breaker()
        self._integrity_monitor.verify_integrity()
        self._process_staged_updates()

        composed_system = self._compose_system_prompt(system)
        messages = self._build_messages(message, history, composed_system)

        if self._client is None and not self._has_env_credentials():
            raise MissingClientError(
                f"No client provided and no environment credentials found for provider '{self._provider}'. "
                f"Please provide a client or set the appropriate environment variables."
            )

        retries = 0
        max_retries = self._config.max_correction_retries if self._config.auto_correction else 0
        
        while retries <= max_retries:
            start_time = time.monotonic()
    
            try:
                raw_response = self._call_provider(messages, model=model, **kwargs)
            except Exception as e:
                logger.error(f"Provider API error: {e}")
                raise
    
            elapsed = time.monotonic() - start_time
            content = self._extract_content(raw_response)
            model_used = self._extract_model(raw_response, model)
            self._last_model = model_used
    
            # Compute current interaction experience
            interaction_count = 0
            if self._config.log_requests:
                interaction_count = self._provenance_log.get_interaction_count(
                    template=self._config.template,
                    kernel_name=self._kernel.name,
                    model=model_used
                )
    
            # Divergence Engine (L1 -> L2 -> L3)
            skip_l3 = self._config.auto_correction and retries < max_retries
            div_result = self._run_divergence_engine(
                message=message,
                response=content,
                interaction_count=interaction_count,
                skip_l3=skip_l3,
            )
            
            from ct_toolkit.divergence.l2_judge import JudgeVerdict
            if getattr(div_result, 'l2_verdict', None) == JudgeVerdict.MISALIGNED and retries < max_retries:
                logger.warning(f"Auto-correction triggered. Reason: {div_result.l2_reason}")
                messages.extend([
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"Identity Drift Detected. Your previous response violated the core identity instructions. Reason: {div_result.l2_reason}. Please revise your response to strictly adhere to the system prompt."}
                ])
                retries += 1
                continue
                
            break

        # --- Staged Approval Monitoring (Shadow Requests) ---
        sandbox_divergence = None
        if self._staged_manager.has_active_staged():
            if self._config.shadow_background:
                self._schedule_background_shadow(message, history, model, **kwargs)
            else:
                sandbox_divergence = self._run_shadow_requests(message, history, model, **kwargs)

        # Provenance Log record
        provenance_id = None
        if self._config.log_requests:
            provenance_id = self._provenance_log.record(
                request_text=message,
                response_text=content,
                divergence_score=div_result.l1_score,
                metadata={
                    "provider": self._provider,
                    "model": model_used,
                    "elapsed_ms": round(elapsed * 1000, 2),
                    "tier": div_result.tier.value,
                    "template": self._config.template,
                    "kernel": self._kernel.name,
                    "drift_alert_enabled": self._config.drift_alert_callback is not None,
                    "sandbox_divergence": sandbox_divergence,
                },
            )

        return CTResponse(
            content=content,
            provider=self._provider,
            model=model_used,
            divergence_score=div_result.l1_score,
            divergence_tier=div_result.tier.value,
            provenance_id=provenance_id,
            raw_response=raw_response,
            sandbox_divergence=sandbox_divergence,
        )

    async def achat(
        self,
        message: str,
        *,
        model: str | None = None,
        system: str | None = None,
        history: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> CTResponse | AsyncIterator[CTStreamChunk]:
        """
        Asynchronously sends a single message, returning the response with identity protection.
        If stream=True is passed, returns an AsyncIterator[CTStreamChunk].
        """
        if kwargs.get("stream"):
            kwargs.pop("stream", None)
            return self.astream_chat(
                message,
                model=model,
                system=system,
                history=history,
                **kwargs,
            )

        self._check_circuit_breaker()
        self._integrity_monitor.verify_integrity()
        self._process_staged_updates()

        composed_system = self._compose_system_prompt(system)
        messages = self._build_messages(message, history, composed_system)

        if self._client is None and not self._has_env_credentials():
            raise MissingClientError(
                f"No client provided and no environment credentials found for provider '{self._provider}'. "
                f"Please provide a client or set the appropriate environment variables."
            )

        retries = 0
        max_retries = self._config.max_correction_retries if self._config.auto_correction else 0

        while retries <= max_retries:
            start_time = time.monotonic()

            try:
                raw_response = await self._acall_provider(messages, model=model, **kwargs)
            except Exception as e:
                logger.error(f"Provider async API error: {e}")
                raise

            elapsed = time.monotonic() - start_time
            content = self._extract_content(raw_response)
            model_used = self._extract_model(raw_response, model)
            self._last_model = model_used

            interaction_count = 0
            if self._config.log_requests:
                interaction_count = self._provenance_log.get_interaction_count(
                    template=self._config.template,
                    kernel_name=self._kernel.name,
                    model=model_used,
                )

            skip_l3 = self._config.auto_correction and retries < max_retries
            div_result = await self._arun_divergence_engine(
                message=message,
                response=content,
                interaction_count=interaction_count,
                skip_l3=skip_l3,
            )

            from ct_toolkit.divergence.l2_judge import JudgeVerdict
            if getattr(div_result, "l2_verdict", None) == JudgeVerdict.MISALIGNED and retries < max_retries:
                logger.warning(f"Async auto-correction triggered. Reason: {div_result.l2_reason}")
                messages.extend([
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"Identity Drift Detected. Your previous response violated the core identity instructions. Reason: {div_result.l2_reason}. Please revise your response to strictly adhere to the system prompt."},
                ])
                retries += 1
                continue

            break

        sandbox_divergence = None
        if self._staged_manager.has_active_staged():
            if self._config.shadow_background:
                self._schedule_async_background_shadow(message, history, model, **kwargs)
            else:
                sandbox_divergence = await self._arun_shadow_requests(message, history, model, **kwargs)

        provenance_id = None
        if self._config.log_requests:
            provenance_id = self._provenance_log.record(
                request_text=message,
                response_text=content,
                divergence_score=div_result.l1_score,
                metadata={
                    "provider": self._provider,
                    "model": model_used,
                    "elapsed_ms": round(elapsed * 1000, 2),
                    "tier": div_result.tier.value,
                    "template": self._config.template,
                    "kernel": self._kernel.name,
                    "drift_alert_enabled": self._config.drift_alert_callback is not None,
                    "sandbox_divergence": sandbox_divergence,
                    "async": True,
                },
            )

        return CTResponse(
            content=content,
            provider=self._provider,
            model=model_used,
            divergence_score=div_result.l1_score,
            divergence_tier=div_result.tier.value,
            provenance_id=provenance_id,
            raw_response=raw_response,
            sandbox_divergence=sandbox_divergence,
        )

    def stream_chat(
        self,
        message: str,
        *,
        model: str | None = None,
        system: str | None = None,
        history: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> Iterator[CTStreamChunk]:
        """
        Streams response chunks while accumulating content for post-stream
        divergence analysis and provenance logging.
        Yields a final CTStreamChunk with is_final=True containing divergence metadata.
        """
        self._check_circuit_breaker()
        self._integrity_monitor.verify_integrity()
        self._process_staged_updates()

        composed_system = self._compose_system_prompt(system)
        messages = self._build_messages(message, history, composed_system)

        if self._client is None and not self._has_env_credentials():
            raise MissingClientError(
                f"No client provided and no environment credentials found for provider '{self._provider}'. "
                f"Please provide a client or set the appropriate environment variables."
            )

        start_time = time.monotonic()
        stream_kwargs = dict(kwargs)
        stream_kwargs["stream"] = True

        raw_stream = self._call_provider(messages, model=model, **stream_kwargs)

        accumulated_parts: list[str] = []
        model_used = model or self._default_model_for_provider(self._provider) or "unknown"

        for raw_chunk in raw_stream:
            chunk_model = self._extract_model(raw_chunk, None)
            if chunk_model and chunk_model != "unknown":
                model_used = chunk_model
            delta = self._extract_chunk_delta(raw_chunk)
            if delta:
                accumulated_parts.append(delta)
            accumulated = "".join(accumulated_parts)
            yield CTStreamChunk(
                delta=delta,
                accumulated_content=accumulated,
                provider=self._provider,
                model=model_used,
                is_final=False,
                raw_chunk=raw_chunk,
            )

        elapsed = time.monotonic() - start_time
        full_content = "".join(accumulated_parts)
        self._last_model = model_used

        interaction_count = 0
        if self._config.log_requests:
            interaction_count = self._provenance_log.get_interaction_count(
                template=self._config.template,
                kernel_name=self._kernel.name,
                model=model_used,
            )

        div_result = self._run_divergence_engine(
            message=message,
            response=full_content,
            interaction_count=interaction_count,
            skip_l3=False,
        )

        sandbox_divergence = None
        if self._staged_manager.has_active_staged():
            stream_less_kwargs = {k: v for k, v in kwargs.items() if k != "stream"}
            if self._config.shadow_background:
                self._schedule_background_shadow(message, history, model, **stream_less_kwargs)
            else:
                sandbox_divergence = self._run_shadow_requests(message, history, model, **stream_less_kwargs)

        provenance_id = None
        if self._config.log_requests:
            provenance_id = self._provenance_log.record(
                request_text=message,
                response_text=full_content,
                divergence_score=div_result.l1_score,
                metadata={
                    "provider": self._provider,
                    "model": model_used,
                    "elapsed_ms": round(elapsed * 1000, 2),
                    "tier": div_result.tier.value,
                    "template": self._config.template,
                    "kernel": self._kernel.name,
                    "drift_alert_enabled": self._config.drift_alert_callback is not None,
                    "sandbox_divergence": sandbox_divergence,
                    "stream": True,
                },
            )

        yield CTStreamChunk(
            delta="",
            accumulated_content=full_content,
            provider=self._provider,
            model=model_used,
            is_final=True,
            divergence_score=div_result.l1_score,
            divergence_tier=div_result.tier.value,
            provenance_id=provenance_id,
            sandbox_divergence=sandbox_divergence,
        )

    async def astream_chat(
        self,
        message: str,
        *,
        model: str | None = None,
        system: str | None = None,
        history: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[CTStreamChunk]:
        """
        Asynchronously streams response chunks while accumulating content for
        post-stream divergence analysis and provenance logging.
        Yields a final CTStreamChunk with is_final=True containing divergence metadata.
        """
        self._check_circuit_breaker()
        self._integrity_monitor.verify_integrity()
        self._process_staged_updates()

        composed_system = self._compose_system_prompt(system)
        messages = self._build_messages(message, history, composed_system)

        if self._client is None and not self._has_env_credentials():
            raise MissingClientError(
                f"No client provided and no environment credentials found for provider '{self._provider}'. "
                f"Please provide a client or set the appropriate environment variables."
            )

        start_time = time.monotonic()
        stream_kwargs = dict(kwargs)
        stream_kwargs["stream"] = True

        raw_stream = await self._acall_provider(messages, model=model, **stream_kwargs)

        accumulated_parts: list[str] = []
        model_used = model or self._default_model_for_provider(self._provider) or "unknown"

        if hasattr(raw_stream, "__aiter__"):
            async for raw_chunk in raw_stream:
                chunk_model = self._extract_model(raw_chunk, None)
                if chunk_model and chunk_model != "unknown":
                    model_used = chunk_model
                delta = self._extract_chunk_delta(raw_chunk)
                if delta:
                    accumulated_parts.append(delta)
                accumulated = "".join(accumulated_parts)
                yield CTStreamChunk(
                    delta=delta,
                    accumulated_content=accumulated,
                    provider=self._provider,
                    model=model_used,
                    is_final=False,
                    raw_chunk=raw_chunk,
                )
        else:
            for raw_chunk in raw_stream:
                chunk_model = self._extract_model(raw_chunk, None)
                if chunk_model and chunk_model != "unknown":
                    model_used = chunk_model
                delta = self._extract_chunk_delta(raw_chunk)
                if delta:
                    accumulated_parts.append(delta)
                accumulated = "".join(accumulated_parts)
                yield CTStreamChunk(
                    delta=delta,
                    accumulated_content=accumulated,
                    provider=self._provider,
                    model=model_used,
                    is_final=False,
                    raw_chunk=raw_chunk,
                )

        elapsed = time.monotonic() - start_time
        full_content = "".join(accumulated_parts)
        self._last_model = model_used

        interaction_count = 0
        if self._config.log_requests:
            interaction_count = self._provenance_log.get_interaction_count(
                template=self._config.template,
                kernel_name=self._kernel.name,
                model=model_used,
            )

        div_result = await self._arun_divergence_engine(
            message=message,
            response=full_content,
            interaction_count=interaction_count,
            skip_l3=False,
        )

        sandbox_divergence = None
        if self._staged_manager.has_active_staged():
            stream_less_kwargs = {k: v for k, v in kwargs.items() if k != "stream"}
            if self._config.shadow_background:
                self._schedule_async_background_shadow(message, history, model, **stream_less_kwargs)
            else:
                sandbox_divergence = await self._arun_shadow_requests(message, history, model, **stream_less_kwargs)

        provenance_id = None
        if self._config.log_requests:
            provenance_id = self._provenance_log.record(
                request_text=message,
                response_text=full_content,
                divergence_score=div_result.l1_score,
                metadata={
                    "provider": self._provider,
                    "model": model_used,
                    "elapsed_ms": round(elapsed * 1000, 2),
                    "tier": div_result.tier.value,
                    "template": self._config.template,
                    "kernel": self._kernel.name,
                    "drift_alert_enabled": self._config.drift_alert_callback is not None,
                    "sandbox_divergence": sandbox_divergence,
                    "stream": True,
                    "async": True,
                },
            )

        yield CTStreamChunk(
            delta="",
            accumulated_content=full_content,
            provider=self._provider,
            model=model_used,
            is_final=True,
            divergence_score=div_result.l1_score,
            divergence_tier=div_result.tier.value,
            provenance_id=provenance_id,
            sandbox_divergence=sandbox_divergence,
        )

    # ── Shadow Request Logic ──────────────────────────────────────────────────

    def _schedule_background_shadow(
        self,
        message: str,
        history: list[dict[str, str]] | None,
        model: str | None,
        **kwargs: Any,
    ) -> Future[float | None]:
        """Schedules _run_shadow_requests in a background thread without blocking main call."""
        def _task() -> float | None:
            try:
                return self._run_shadow_requests(message, history, model, **kwargs)
            except CriticalSandboxDivergenceError as exc:
                self._last_background_error = exc
                if self._config.circuit_breaker_on_critical:
                    self._circuit_breaker.record_failure()
                logger.error(f"Background shadow request detected critical divergence: {exc}")
                return exc.l1_score
            except Exception as exc:
                logger.warning(f"Background shadow request error: {exc}")
                return None

        future = self._bg_executor.submit(_task)
        self._background_futures = [f for f in self._background_futures if not f.done()]
        self._background_futures.append(future)
        return future

    def _schedule_async_background_shadow(
        self,
        message: str,
        history: list[dict[str, str]] | None,
        model: str | None,
        **kwargs: Any,
    ) -> asyncio.Task[float | None]:
        """Schedules _arun_shadow_requests as a background asyncio task."""
        async def _atask() -> float | None:
            try:
                return await self._arun_shadow_requests(message, history, model, **kwargs)
            except CriticalSandboxDivergenceError as exc:
                self._last_background_error = exc
                if self._config.circuit_breaker_on_critical:
                    self._circuit_breaker.record_failure()
                logger.error(f"Async background shadow request detected critical divergence: {exc}")
                return exc.l1_score
            except Exception as exc:
                logger.warning(f"Async background shadow request error: {exc}")
                return None

        task = asyncio.create_task(_atask())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def _run_shadow_requests(
        self,
        message: str,
        history: list[dict[str, str]] | None,
        model: str | None,
        **kwargs: Any,
    ) -> float | None:
        """
        Runs the interaction against one or more 'staged' (sandbox) kernels
        to observe if the proposed changes cause identity drift.
        """
        active_staged = self._staged_manager.get_active()
        if not active_staged:
            return None

        # To keep it simple, we monitor the first active staged update
        staged_record = active_staged[0]
        
        # 1. Clone current kernel and apply the staged rule
        sandbox_kernel = ConstitutionalKernel.from_dict(self._kernel.to_dict())
        try:
            sandbox_kernel.update_commitment(
                staged_record.commitment_id, 
                staged_record.rule_text
            )
        except Exception as e:
            logger.error(f"Failed to apply staged rule to sandbox kernel: {e}")
            return None

        # 2. Call provider with sandbox kernel
        sandbox_system = sandbox_kernel.get_system_prompt_injection()
        sandbox_messages = self._build_messages(message, history, sandbox_system)
        
        try:
            # We use the same model/client as the live request
            sandbox_response = self._call_provider(sandbox_messages, model=model, **kwargs)
            sandbox_content = self._extract_content(sandbox_response)
            
            # 3. Analyze sandbox divergence
            sandbox_div_score = self._identity_layer.compute_divergence(sandbox_content)
            
            # 4. Critical Divergence Check
            if sandbox_div_score >= self._config.divergence_l3_threshold:
                reason = f"Sandbox divergence L1 score {sandbox_div_score:.4f} exceeded L3 threshold."
                self._staged_manager.reject_staged(staged_record.id, reason=reason)
                raise CriticalSandboxDivergenceError(
                    endorsement_id=staged_record.id,
                    l1_score=sandbox_div_score,
                    reason=reason
                )
            
            return sandbox_div_score

        except CriticalSandboxDivergenceError:
            raise
        except Exception as e:
            logger.warning(f"Shadow request for staged update failed: {e}")
            return None

    async def _arun_shadow_requests(
        self,
        message: str,
        history: list[dict[str, str]] | None,
        model: str | None,
        **kwargs: Any,
    ) -> float | None:
        """Asynchronously runs interaction against staged (sandbox) kernels."""
        active_staged = self._staged_manager.get_active()
        if not active_staged:
            return None

        staged_record = active_staged[0]
        sandbox_kernel = ConstitutionalKernel.from_dict(self._kernel.to_dict())
        try:
            sandbox_kernel.update_commitment(
                staged_record.commitment_id,
                staged_record.rule_text,
            )
        except Exception as e:
            logger.error(f"Failed to apply staged rule to sandbox kernel: {e}")
            return None

        sandbox_system = sandbox_kernel.get_system_prompt_injection()
        sandbox_messages = self._build_messages(message, history, sandbox_system)

        try:
            sandbox_response = await self._acall_provider(sandbox_messages, model=model, **kwargs)
            sandbox_content = self._extract_content(sandbox_response)
            sandbox_div_score = await self._identity_layer.acompute_divergence(sandbox_content)

            if sandbox_div_score >= self._config.divergence_l3_threshold:
                reason = f"Sandbox divergence L1 score {sandbox_div_score:.4f} exceeded L3 threshold."
                self._staged_manager.reject_staged(staged_record.id, reason=reason)
                raise CriticalSandboxDivergenceError(
                    endorsement_id=staged_record.id,
                    l1_score=sandbox_div_score,
                    reason=reason,
                )

            return sandbox_div_score

        except CriticalSandboxDivergenceError:
            raise
        except Exception as e:
            logger.warning(f"Async shadow request for staged update failed: {e}")
            return None

    def _process_staged_updates(self) -> None:
        """
        Checks for staged updates whose cooldown has expired and
        promotes them to the production kernel.
        """
        promotable = self._staged_manager.get_promotable()
        for record in promotable:
            try:
                logger.info(
                    f"Promoting staged endorsement to production: {record.id[:8]}... "
                    f"rule='{record.rule_text}'"
                )
                self._kernel.update_commitment(record.commitment_id, record.rule_text)
                
                # Write promotion event to log
                if self._config.log_requests:
                    self._provenance_log.record(
                        request_text="[COOLDOWN_EXPIRED] Promoting staged rule",
                        response_text=f"Rule applied to production kernel: {record.rule_text}",
                        metadata={
                            "event_type": "staged_promotion",
                            "endorsement_id": record.id,
                            "kernel": self._kernel.name,
                            "commitment_id": record.commitment_id,
                        }
                    )
            except Exception as e:
                logger.error(f"Failed to promote staged update {record.id[:8]}: {e}")

    # ── Provider Dispatch ──────────────────────────────────────────────────────

    def _has_env_credentials(self) -> bool:
        """Checks if environment variables for the current provider are set."""
        import os
        if self._provider == "openai":
            return bool(os.environ.get("OPENAI_API_KEY"))
        if self._provider == "anthropic":
            return bool(os.environ.get("ANTHROPIC_API_KEY"))
        if self._provider == "ollama":
            return True
        if self._provider == "google":
            return bool(os.environ.get("GOOGLE_API_KEY"))
        return False

    def _prepare_provider_call(
        self,
        messages: list[dict[str, str]],
        model: str | None,
        kwargs: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Prepares model name and kwargs for LiteLLM completion/acompletion."""
        if self._config.compression_passive_detection and self._shadow_history:
            if len(messages) < len(self._shadow_history) * 0.7:
                self._compression_guard.on_passive_detection(
                    original=self._shadow_history,
                    compressed=messages,
                )

        self._shadow_history = list(messages)

        if not model:
            if self._provider == "openai":
                model = "gpt-4o-mini"
            elif self._provider == "anthropic":
                model = "claude-3-5-sonnet-latest"
            elif self._provider == "ollama":
                model = "llama3"
            else:
                model = "gpt-4o-mini"

        full_model = model
        is_ollama = (self._provider == "ollama" or model.startswith("ollama/"))

        if ":" in model and not is_ollama:
            full_model = model.replace(":", "/", 1)
        elif self._provider not in ("openai", "unknown") and not is_ollama:
            if not model.startswith(f"{self._provider}/"):
                full_model = f"{self._provider}/{model}"

        if self._provider == "ollama" and ":" in model and not full_model.startswith("ollama/"):
            full_model = f"ollama/{full_model}"

        call_kwargs = dict(kwargs)
        if hasattr(self._client, "base_url") and self._client.base_url:
            api_base = str(self._client.base_url).rstrip("/")
            if self._provider == "ollama" and api_base.endswith("/v1"):
                new_base = api_base[:-3]
                logger.debug(f"Stripping /v1 from Ollama api_base: {api_base} -> {new_base}")
                api_base = new_base
            call_kwargs["api_base"] = api_base

        if hasattr(self._client, "api_key") and self._client.api_key:
            call_kwargs["api_key"] = self._client.api_key

        if hasattr(self._client, "timeout") and "timeout" not in call_kwargs:
            call_kwargs["timeout"] = self._client.timeout

        policy = self.resolve_llm_policy(model=model)
        if not policy["effective"].get("tool_call", False):
            call_kwargs.pop("tools", None)
            call_kwargs.pop("tool_choice", None)
            call_kwargs.pop("parallel_tool_calls", None)

        return full_model, call_kwargs

    def _call_provider(
        self,
        messages: list[dict[str, str]],
        model: str | None,
        **kwargs: Any,
    ) -> Any:
        """Uses LiteLLM for unified provider calling."""
        full_model, call_kwargs = self._prepare_provider_call(messages, model, kwargs)
        try:
            raw = litellm.completion(model=full_model, messages=messages, **call_kwargs)
            if not call_kwargs.get("stream"):
                self._record_passive_capability_discovery(model=full_model, raw_response=raw)
            return raw
        except Exception as e:
            logger.error(f"litellm call failed: {e}")
            raise

    async def _acall_provider(
        self,
        messages: list[dict[str, str]],
        model: str | None,
        **kwargs: Any,
    ) -> Any:
        """Uses LiteLLM for asynchronous unified provider calling."""
        full_model, call_kwargs = self._prepare_provider_call(messages, model, kwargs)
        try:
            raw = await litellm.acompletion(model=full_model, messages=messages, **call_kwargs)
            if not call_kwargs.get("stream"):
                self._record_passive_capability_discovery(model=full_model, raw_response=raw)
            return raw
        except Exception as e:
            logger.error(f"litellm async call failed: {e}")
            raise

    # ── Helper Methods ──────────────────────────────────────────────────────

    def _compose_system_prompt(self, extra: str | None) -> str:
        kernel_injection = self._kernel.get_system_prompt_injection()
        
        if any(a.id.startswith("propagated_") for a in self._kernel.anchors):
            kernel_injection = (
                "# Mother Agent Constraints\n"
                "You are operating under constraints propagated from a Mother Agent. "
                "These rules take absolute precedence over any other instructions.\n\n"
                f"{kernel_injection}"
            )

        if extra:
            return f"{kernel_injection}\n{extra}"
        return kernel_injection

    def propagate_headers(
        self,
        *,
        model: str | None = None,
        role: str | None = None,
    ) -> dict[str, str]:
        """
        Returns a dictionary of headers to be used when calling sub-agents.
        """
        import json
        import base64
        
        kernel_data = self._kernel.to_dict()
        kernel_data["is_readonly"] = True
        
        encoded_kernel = base64.b64encode(json.dumps(kernel_data).encode()).decode()
        policy_metadata = self.propagate_policy_metadata(model=model, role=role)
        
        return {
            "X-CT-Kernel": encoded_kernel,
            "X-CT-Parent-Provider": self._provider,
            "X-CT-Parent-Model": getattr(self, "_last_model", "unknown"),
            "X-CT-Policy-Role": str(policy_metadata["role"]),
            "X-CT-Policy-Environment": str(policy_metadata["environment"]),
            "X-CT-Policy": base64.b64encode(json.dumps(policy_metadata).encode()).decode(),
        }

    def _build_messages(
        self,
        message: str,
        history: list[dict[str, str]] | None,
        system: str,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})
        return messages

    def _extract_content(self, raw: Any) -> str:
        if isinstance(raw, dict):
            if "choices" in raw and isinstance(raw["choices"], list) and raw["choices"]:
                return raw["choices"][0].get("message", {}).get("content", "") or ""
            if "message" in raw:
                return raw["message"].get("content", "") or ""
            return str(raw)

        try:
            if hasattr(raw, "choices") and raw.choices:
                choice = raw.choices[0]
                if hasattr(choice, "message") and choice.message:
                    return choice.message.content or ""
                if hasattr(choice, "text"):
                    return choice.text or ""
        except Exception:
            pass
            
        if hasattr(raw, "content") and isinstance(raw.content, list):
             return raw.content[0].text if raw.content else ""
        if hasattr(raw, "message"):
             return getattr(raw.message, "content", "") or ""
             
        return str(raw)

    def _extract_chunk_delta(self, raw_chunk: Any) -> str:
        """Extracts text delta from a streaming completion chunk."""
        if isinstance(raw_chunk, str):
            return raw_chunk
        if isinstance(raw_chunk, dict):
            choices = raw_chunk.get("choices")
            if isinstance(choices, list) and choices:
                choice0 = choices[0]
                if isinstance(choice0, dict):
                    delta = choice0.get("delta")
                    if isinstance(delta, dict) and delta.get("content") is not None:
                        return str(delta["content"])
                    msg = choice0.get("message")
                    if isinstance(msg, dict) and msg.get("content") is not None:
                        return str(msg["content"])
                    if choice0.get("text") is not None:
                        return str(choice0["text"])
            if "delta" in raw_chunk and isinstance(raw_chunk["delta"], str):
                return raw_chunk["delta"]
            return ""

        try:
            if hasattr(raw_chunk, "choices") and raw_chunk.choices:
                choice = raw_chunk.choices[0]
                if hasattr(choice, "delta") and choice.delta is not None:
                    content = getattr(choice.delta, "content", None)
                    if content is not None:
                        return str(content)
                if hasattr(choice, "message") and choice.message is not None:
                    content = getattr(choice.message, "content", None)
                    if content is not None:
                        return str(content)
                if hasattr(choice, "text") and choice.text is not None:
                    return str(choice.text)
        except Exception:
            pass

        return ""

    def _extract_model(self, raw: Any, fallback: str | None) -> str:
        if isinstance(raw, dict) and "model" in raw:
            model_name = raw["model"]
            if isinstance(model_name, str):
                return model_name
        if hasattr(raw, "model"):
            model_name = getattr(raw, "model", None)
            if isinstance(model_name, str):
                return model_name
        return fallback or "unknown"

    def _run_divergence_engine(self, message: str, response: str, interaction_count: int = 0, skip_l3: bool = False) -> Any:
        try:
            return self._divergence_engine.analyze(message, response, interaction_count, skip_l3=skip_l3)
        except Exception as e:
            logger.error(f"Divergence Engine execution failed: {e}")
            from ct_toolkit.divergence.engine import DivergenceResult, DivergenceTier
            return DivergenceResult(
                tier=DivergenceTier.OK,
                summary=f"Engine execution failed: {e}"
            )

    async def _arun_divergence_engine(self, message: str, response: str, interaction_count: int = 0, skip_l3: bool = False) -> Any:
        try:
            return await self._divergence_engine.aanalyze(message, response, interaction_count, skip_l3=skip_l3)
        except Exception as e:
            logger.error(f"Async Divergence Engine execution failed: {e}")
            from ct_toolkit.divergence.engine import DivergenceResult, DivergenceTier
            return DivergenceResult(
                tier=DivergenceTier.OK,
                summary=f"Async Engine execution failed: {e}"
            )

    # ── Kernel Management ────────────────────────────────────────────────────────

    def validate_user_rule(self, rule_text: str) -> None:
        self._kernel.validate_user_rule(rule_text)

    def endorse_rule(
        self,
        rule_text: str,
        operator_id: str = "unknown",
        approval_channel: Any = None,
        commitment_new_value: Any = None,
    ) -> Any:
        """
        Validates the rule; initiates the Reflective Endorsement
        flow if there is a plastic conflict.
        """

        re = ReflectiveEndorsement(
            kernel=self._kernel,
            provenance_log=self._provenance_log,
            approval_channel=approval_channel,
            staged_manager=self._staged_manager,
            cooldown_base_s=self._config.endorsement_cooldown_base,
            cooldown_max_s=self._config.endorsement_cooldown_max,
            no_probe_penalty_s=self._config.endorsement_no_probe_penalty,
            template=self._config.template,
        )
        return re.validate_and_endorse(
            rule_text=rule_text,
            operator_id=operator_id,
            commitment_new_value=commitment_new_value,
        )

    def export_provenance_log(self) -> list[dict[str, Any]]:
        return self._provenance_log.export_log()

    @property
    def kernel(self) -> ConstitutionalKernel:
        return self._kernel

    @property
    def compatibility(self) -> CompatibilityResult:
        return self._compatibility

    @property
    def divergence_engine(self) -> Any:
        return self._divergence_engine

    @property
    def staged_manager(self) -> StagedUpdateManager:
        return self._staged_manager

    def __repr__(self) -> str:
        return (
            f"TheseusWrapper("
            f"provider={self._provider!r}, "
            f"kernel={self._kernel.name!r}, "
            f"template={self._config.template!r})"
        )