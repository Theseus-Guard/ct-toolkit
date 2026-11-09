"""
ct_toolkit.server
-----------------
FastAPI server for LiteLLM Generic Guardrail API.
Exposes /guardrail/check to LiteLLM for pre-call and post-call validation.
"""
from __future__ import annotations

import hmac
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
import uvicorn

from ct_toolkit.core.wrapper import TheseusWrapper
from ct_toolkit.provenance.log import ProvenanceLog
from ct_toolkit.provenance.log import DIVERGENCE_TIERS, resolve_tier
from ct_toolkit.provenance.tool_calls import query_page
from ct_toolkit.utils.cache import GenerationCache
from ct_toolkit.core import tool_guard_settings as guard_settings
from ct_toolkit.core.tool_guard_settings import SettingsWatcher, apply_to_guard
from ct_toolkit.core.tool_guard import DEFAULT_ALLOWED_HOSTS
from ct_toolkit.utils.logger import get_logger
from ct_toolkit.core.exceptions import (
    ConfigurationTamperingError,
    ChainIntegrityError,
    CriticalSandboxDivergenceError,
)

logger = get_logger(__name__)

# This will be injected by the CLI when starting the server
_wrapper_instance: Optional[TheseusWrapper] = None
_server_api_key: Optional[str] = None
_dashboard_vault_path: str = "./ct_provenance.db"
_dashboard_vault_type: str = "local"

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
_DB_CREDENTIAL_RE = re.compile(r"(://[^:/?#\s]+:)([^@/?#\s]+)(@)")
# LIMIT value meaning "every row": get_entries(limit) only ever returns the oldest `limit` rows.

_SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def configure_server_auth(api_key: Optional[str]) -> None:
    """Configures optional API Key / Bearer Token authentication for the guardrail server."""
    global _server_api_key
    _server_api_key = api_key


def configure_dashboard_vault(vault_path: str, vault_type: str = "local", full_verify_interval_s: Optional[float] = None) -> None:
    """Configures the Provenance Log vault inspected by the Web Audit Dashboard.
    `full_verify_interval_s` sets how often a full chain verification runs (default: 300 s)."""
    global _dashboard_vault_path, _dashboard_vault_type, _full_verify_interval_s
    _dashboard_vault_path = vault_path
    _dashboard_vault_type = vault_type
    _full_verify_interval_s = full_verify_interval_s


def _sanitize_vault_display(vault_path: str) -> str:
    """Redacts database passwords and strips absolute filesystem paths for safe UI display."""
    raw = str(vault_path or "")
    if "://" in raw:
        return _DB_CREDENTIAL_RE.sub(r"\1***\3", raw)
    p = Path(raw)
    if p.is_absolute():
        return p.name
    return raw


def _validate_bind_security(host: str, api_key: Optional[str]) -> None:
    """Fail-closed: refuse binding to non-loopback interfaces unless an API key is configured."""
    effective_key = api_key or _server_api_key or os.environ.get("CT_SERVER_API_KEY")
    if host.strip().lower() not in _LOOPBACK_HOSTS and not effective_key:
        raise ValueError(
            f"Refusing to bind CT Toolkit Server to non-loopback host '{host}' without authentication. "
            "Pass --api-key or set the CT_SERVER_API_KEY environment variable, or bind to 127.0.0.1."
        )


async def _verify_api_key(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> None:
    """Validates Bearer token or X-API-Key header, and restricts unauthenticated access to loopback."""
    expected_key = _server_api_key or os.environ.get("CT_SERVER_API_KEY")
    client_host = (request.client.host if request.client else "127.0.0.1").lower()

    if not expected_key:
        if client_host not in _LOOPBACK_HOSTS:
            raise HTTPException(
                status_code=403,
                detail="Forbidden: Non-localhost access requires CT_SERVER_API_KEY to be configured.",
            )
        return

    provided_token: Optional[str] = None
    if authorization:
        parts = authorization.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            provided_token = parts[1].strip()
        else:
            provided_token = authorization.strip()
    elif x_api_key:
        provided_token = x_api_key.strip()

    if not provided_token or not hmac.compare_digest(provided_token, expected_key):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Invalid or missing API key / Bearer token.",
        )


app = FastAPI(
    title="CT-Toolkit Guardrail & Audit Server",
    description="Identity Continuity Guardrails & Web Telemetry Dashboard for Agentic Systems.",
    version="1.0.0"
)

class GuardrailRequest(BaseModel):
    """LiteLLM Generic Guardrail API Request Schema."""
    texts: Optional[List[str]] = Field(None, description="Extracted text from the request.")
    input_type: str = Field(..., description="Phase of the call: 'request' or 'response'.")
    structured_messages: Optional[List[Dict[str, Any]]] = Field(None, description="Full messages in OpenAI format.")
    images: Optional[List[str]] = None
    tools: Optional[List[Any]] = None
    tool_calls: Optional[List[Any]] = None
    request_data: Optional[Dict[str, Any]] = None
    litellm_call_id: Optional[str] = None
    litellm_trace_id: Optional[str] = None

class GuardrailResponse(BaseModel):
    """LiteLLM Generic Guardrail API Response Schema."""
    action: str = Field(..., description="'BLOCKED' | 'NONE' | 'GUARDRAIL_INTERVENED'")
    blocked_reason: Optional[str] = Field(None, description="Reason for denial (required if BLOCKED).")
    texts: Optional[List[str]] = Field(None, description="Optional array of modified text strings.")

@app.post("/guardrail/check", response_model=GuardrailResponse, dependencies=[Depends(_verify_api_key)])
async def check_guardrail(request: GuardrailRequest):
    """
    Main endpoint for LiteLLM Generic Guardrail API.
    """
    global _wrapper_instance
    if not _wrapper_instance:
        raise HTTPException(status_code=500, detail="Server not initialized with a Constitutional Kernel.")

    logger.info(f"Received {request.input_type} check (call_id={request.litellm_call_id})")

    try:
        if request.input_type == "request":
            return await _handle_pre_call(request)
        elif request.input_type == "response":
            return await _handle_post_call(request)
        else:
            logger.warning(f"Unknown input_type: {request.input_type}")
            return GuardrailResponse(action="NONE")
    except (ConfigurationTamperingError, ChainIntegrityError, CriticalSandboxDivergenceError) as e:
        # Critical security errors must BLOCK the request
        logger.critical(f"CRITICAL security error during guardrail check: {e}")
        return GuardrailResponse(
            action="BLOCKED",
            blocked_reason=f"Critical security violation: {type(e).__name__}: {e}"
        )
    except Exception as e:
        logger.error(f"Error during guardrail check: {e}")
        # Default to NONE to avoid breaking production flows on server error
        return GuardrailResponse(action="NONE")

async def _handle_pre_call(request: GuardrailRequest) -> GuardrailResponse:
    """Validate user message against Constitutional Kernel before LLM call."""
    user_text = ""
    if request.texts is not None:
        user_text = " ".join(request.texts)
    elif request.structured_messages is not None:
        user_text = _extract_user_text(request.structured_messages)

    if user_text:
        try:
            _wrapper_instance.validate_user_rule(user_text)
            return GuardrailResponse(action="NONE")
        except Exception as e:
            logger.warning(f"Pre-call kernel violation: {e}")
            return GuardrailResponse(
                action="BLOCKED",
                blocked_reason=f"Identity constraint violation: {e}"
            )
    return GuardrailResponse(action="NONE")

async def _handle_post_call(request: GuardrailRequest) -> GuardrailResponse:
    """Analyze LLM response for divergence after call."""
    user_text = ""
    if request.structured_messages is not None:
        user_text = _extract_user_text(request.structured_messages)

    # Before the text check: a response can consist of tool calls alone.
    tool_block_reason = _review_guardrail_tool_calls(request, user_text)
    if tool_block_reason:
        return GuardrailResponse(action="BLOCKED", blocked_reason=tool_block_reason)

    response_text = ""
    if request.texts is not None:
        response_text = " ".join(request.texts)

    if not response_text:
        return GuardrailResponse(action="NONE")

    model = "unknown"
    if request.request_data:
        # LiteLLM might not send the model directly here, but we can try to extract from metadata
        pass

    interaction_count = 0
    if _wrapper_instance._config.log_requests:
        interaction_count = _wrapper_instance._provenance_log.get_interaction_count(
            template=_wrapper_instance._config.template,
            kernel_name=_wrapper_instance.kernel.name,
            model=model,
        )

    # Run Divergence Engine asynchronously
    div_result = await _wrapper_instance._arun_divergence_engine(
        message=user_text,
        response=response_text,
        interaction_count=interaction_count,
    )

    # Record to Provenance Log
    if _wrapper_instance._config.log_requests:
        _wrapper_instance._provenance_log.record(
            request_text=user_text or "[Generic Guardrail Interaction]",
            response_text=response_text,
            divergence_score=div_result.l1_score,
            metadata={
                "middleware": "generic_guardrail_api",
                "model": model,
                "tier": div_result.tier.value,
                "template": _wrapper_instance._config.template,
                "kernel": _wrapper_instance.kernel.name,
                "litellm_call_id": request.litellm_call_id,
            },
        )

    # Handle cascade_blocked
    if getattr(div_result, 'cascade_blocked', False):
         return GuardrailResponse(
            action="BLOCKED",
            blocked_reason=f"Identity drift detected (score={div_result.l1_score:.4f})"
        )

    return GuardrailResponse(action="NONE")

def _review_guardrail_tool_calls(request: GuardrailRequest, user_text: str) -> Optional[str]:
    """Runs the tool-call guard on a response's tool calls. Returns a block reason in enforce mode.
    The guardrail API can only block the whole response, not single calls."""
    if not request.tool_calls:
        return None
    _sync_guard_settings()
    raw_response = {"choices": [{"message": {"tool_calls": request.tool_calls}}]}
    model = str((request.request_data or {}).get("model") or "unknown")
    _, decisions = _wrapper_instance._review_tool_calls(
        raw_response, user_text, None, None, model, {"tools": request.tools}
    )
    blocked = [decision for decision in decisions if decision.blocked]
    if not blocked:
        return None
    reasons = "; ".join(f"{d.call.name}: {', '.join(str(f) for f in d.findings)}" for d in blocked)
    logger.warning(f"Tool guard blocked response (call_id={request.litellm_call_id}): {reasons}")
    return f"Tool guard: {reasons}"

def _extract_user_text(messages: List[Dict[str, Any]]) -> str:
    """Extract non-system message content from messages list."""
    parts = []
    for msg in messages:
        if msg.get("role") != "system":
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
    return " ".join(parts)

def _extract_response_text(response_obj: Any) -> str:
    """Extract text content from LiteLLM response object."""
    try:
        if isinstance(response_obj, dict):
            choices = response_obj.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
    except Exception:
        pass
    return ""


def _apply_integrity_settings(log: ProvenanceLog) -> None:
    """Sets the full-pass interval and starts the background verifier on a log the dashboard reads."""
    if _full_verify_interval_s is not None:
        log.full_verify_interval_s = _full_verify_interval_s
    log.start_verifier(DASHBOARD_POLL_INTERVAL_S)


_dashboard_log_cache: Optional[tuple[tuple[str, str], ProvenanceLog]] = None
DASHBOARD_POLL_INTERVAL_S = 5.0  # background integrity check interval
_full_verify_interval_s: Optional[float] = None
_tool_guard_settings_path: Optional[str] = None
_guard_watcher: Optional[SettingsWatcher] = None
_guard_applied: Optional[guard_settings.ToolGuardSettings] = None


def _sync_guard_settings() -> None:
    """Applies the settings file to the guardrail server's tool guard whenever the file has changed."""
    global _guard_applied
    if _guard_watcher is None or _wrapper_instance is None:
        return
    current = _guard_watcher.current()
    if current != _guard_applied:
        apply_to_guard(_wrapper_instance._tool_guard, current)
        _guard_applied = current
_read_cache = GenerationCache(maxsize=128, max_age_s=30.0)
GENERATION_CHECK_INTERVAL_S = 1.0  # how often a process asks the database for the generation (one round trip)
_generation_seen: dict[int, tuple[int, float]] = {}


def _generation(log: ProvenanceLog) -> int:
    """The vault's write generation, read from the database at most once per GENERATION_CHECK_INTERVAL_S.
    Writes by other processes therefore show within that interval."""
    now = time.monotonic()
    seen = _generation_seen.get(id(log))
    if seen is not None and now - seen[1] < GENERATION_CHECK_INTERVAL_S:
        return seen[0]
    value = log.generation()
    _generation_seen[id(log)] = (value, now)
    return value


def _dashboard_log() -> ProvenanceLog:
    """The provenance log the dashboard reads: the running wrapper's log, else the configured vault.
    The vault's log is kept between polls, so its incremental integrity position survives."""
    global _dashboard_log_cache
    if _wrapper_instance is not None and getattr(_wrapper_instance, "_provenance_log", None) is not None:
        _apply_integrity_settings(_wrapper_instance._provenance_log)
        return _wrapper_instance._provenance_log
    key = (str(_dashboard_vault_type), str(_dashboard_vault_path))
    if _dashboard_log_cache is None or _dashboard_log_cache[0] != key:
        if _dashboard_log_cache is not None:
            _dashboard_log_cache[1].stop_verifier()
        _dashboard_log_cache = (key, ProvenanceLog(vault_type=_dashboard_vault_type, vault_path=_dashboard_vault_path))
    _apply_integrity_settings(_dashboard_log_cache[1])
    return _dashboard_log_cache[1]


def _is_staged_record(meta: Dict[str, Any]) -> bool:
    """Staged-rule bookkeeping (a promotion or a staged endorsement), not a divergence sample."""
    return meta.get("event_type") == "staged_promotion" or (
        meta.get("event_type") == "reflective_endorsement" and meta.get("decision") == "staged"
    )


def _telemetry_figures(log: ProvenanceLog, limit: int, active_kernel: str, active_template: str) -> Dict[str, Any]:
    """Divergence figures, the ledger and the chart series, read from a bounded window of the newest entries."""
    window_all = log.recent_entries(None, limit=limit)
    scores = [e.divergence_score for e in window_all if e.divergence_score is not None]

    promoted_ids: set[str] = set()
    staged_records: List[Dict[str, Any]] = []
    for entry in reversed(log.recent_staged_entries()):
        meta = entry.metadata or {}
        if meta.get("event_type") == "staged_promotion":
            if meta.get("endorsement_id"):
                promoted_ids.add(str(meta["endorsement_id"]))
        elif _is_staged_record(meta):
            staged_records.append(meta)

    window = [e for e in window_all if not _is_staged_record(e.metadata or {})]
    tier_counts: Dict[str, int] = {tier: 0 for tier in DIVERGENCE_TIERS}
    for entry in window:
        meta = entry.metadata or {}
        recorded = str(meta.get("tier") or meta.get("divergence_tier") or "").lower()
        # Only an entry with a known tier or a score is a sample of a tier; the rest is not counted.
        if recorded in DIVERGENCE_TIERS or entry.divergence_score is not None:
            tier_counts[resolve_tier(meta, entry.divergence_score)] += 1

    ledger: List[Dict[str, Any]] = []
    for entry in window[:25]:
        meta = entry.metadata or {}
        ledger.append(
            {
                "id": entry.id,
                "timestamp": entry.timestamp,
                "divergence_score": entry.divergence_score,
                "tier": resolve_tier(meta, entry.divergence_score),
                "kernel": str(meta.get("kernel", active_kernel)),
                "template": str(meta.get("template", active_template)),
                "model": str(meta.get("model", "unknown")),
                "prev_entry_hash": entry.prev_entry_hash,
                "content_hash": entry.content_hash(),
                "hmac_signature": entry.hmac_signature,
            }
        )
    # The score chart is drawn oldest to newest.
    score_series: List[Dict[str, Any]] = [
        {
            "id": entry.id[:8],
            "timestamp": entry.timestamp,
            "score": round(float(entry.divergence_score), 4),
            "tier": resolve_tier(entry.metadata or {}, entry.divergence_score),
            "kernel": str((entry.metadata or {}).get("kernel", active_kernel)),
            "model": str((entry.metadata or {}).get("model", "unknown")),
        }
        for entry in reversed(window)
        if entry.divergence_score is not None
    ]
    return {
        "entries_analyzed": len(window_all),
        "avg_score": (sum(scores) / len(scores)) if scores else 0.0,
        "max_score": max(scores) if scores else 0.0,
        "tier_counts": tier_counts,
        "score_series": score_series,
        "recent_entries": ledger,
        "promoted_ids": promoted_ids,
        "staged_records": staged_records,
    }


def get_dashboard_telemetry(limit: int = 200) -> Dict[str, Any]:
    """Collects Provenance Chain integrity, L1/L2/L3 divergence metrics, and staged rules.

    Integrity comes from the background verifier (no rows are read for it). The figures are cached per vault
    generation; staged-rule countdowns are computed on every call, since they depend on the clock."""
    if _wrapper_instance is not None and getattr(_wrapper_instance, "_provenance_log", None) is not None:
        log = _wrapper_instance._provenance_log
        vault_path = getattr(_wrapper_instance._config, "vault_path", _dashboard_vault_path)
        vault_type = getattr(_wrapper_instance._config, "vault_type", _dashboard_vault_type)
        active_kernel = _wrapper_instance.kernel.name
        active_template = _wrapper_instance._config.template
    else:
        vault_path = _dashboard_vault_path
        vault_type = _dashboard_vault_type
        log = _dashboard_log()
        active_kernel = "general"
        active_template = "general"

    _apply_integrity_settings(log)
    status = log.integrity_status() or log.check_integrity()
    figures = _read_cache.get_or_compute(
        ("telemetry", id(log), limit, active_kernel, active_template),
        _generation(log),
        lambda: _telemetry_figures(log, limit, active_kernel, active_template),
    )

    now = time.time()
    staged_rules_out: List[Dict[str, Any]] = []
    for s_meta in figures["staged_records"]:
        eid = str(s_meta.get("endorsement_id", "unknown"))
        commitment = str(s_meta.get("commitment_id", "unknown"))
        rule_text = str(s_meta.get("rule_text", ""))
        cooldown_until = s_meta.get("cooldown_until")
        if eid in figures["promoted_ids"]:
            status_code = "PROMOTED"
            remaining_seconds = 0
        elif isinstance(cooldown_until, (int, float)) and now < cooldown_until:
            status_code = "ACTIVE_COOLDOWN"
            remaining_seconds = int(cooldown_until - now)
        else:
            status_code = "READY_FOR_PROMOTION"
            remaining_seconds = 0
        staged_rules_out.append(
            {
                "endorsement_id": eid,
                "commitment_id": commitment,
                "rule_text": rule_text,
                "status": status_code,
                "remaining_seconds": remaining_seconds,
            }
        )

    return {
        "vault_path": _sanitize_vault_display(str(vault_path)),
        "vault_type": str(vault_type),
        "active_kernel": active_kernel,
        "active_template": active_template,
        "chain_ok": bool(status["chain_ok"]),
        "tampered_entry_id": status["tampered_entry_id"],
        "verified_count": int(status["verified_count"]),
        "entries_analyzed": figures["entries_analyzed"],
        "last_hash": str(status["last_hash"]),
        "integrity_checked_at": status["checked_at"],
        "avg_score": round(figures["avg_score"], 4),
        "max_score": round(figures["max_score"], 4),
        "tier_counts": figures["tier_counts"],
        "staged_rules": staged_rules_out,
        "score_series": figures["score_series"],
        "recent_entries": figures["recent_entries"],
        "timestamp": now,
    }

@app.get("/api/dashboard/status", dependencies=[Depends(_verify_api_key)])
async def dashboard_status_api(limit: int = Query(200, ge=1, le=2000)) -> JSONResponse:
    """JSON Telemetry endpoint for the CT Toolkit Web Audit Dashboard."""
    try:
        payload = get_dashboard_telemetry(limit=limit)
        return JSONResponse(content=payload, headers=_SECURITY_HEADERS)
    except Exception as exc:
        logger.error(f"Dashboard status API error: {exc}")
        raise HTTPException(status_code=500, detail="Failed to read provenance telemetry.")


@app.get("/api/dashboard/tool-calls", dependencies=[Depends(_verify_api_key)])
def dashboard_tool_calls_api(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    sort: str = Query("time", description="One of: time, tool, verdict, mode."),
    order: str = Query("desc", description="asc or desc."),
    verdict: Optional[str] = Query(None, description="passed, flagged or blocked."),
    tool: Optional[str] = Query(None, max_length=256, description="Substring of the tool name."),
    q: Optional[str] = Query(None, max_length=256, description="Free text over tool, findings, judge and model."),
) -> JSONResponse:
    """Paged, sorted and filtered guard tool-call decisions from the provenance vault."""
    try:
        log = _dashboard_log()
        result = _read_cache.get_or_compute(
            ("tool-calls", id(log), verdict, tool, q, sort, order, page, page_size),
            _generation(log),
            lambda: query_page(
                log,
                verdict=verdict,
                tool=tool,
                search=q,
                sort=sort,
                order=order,
                page=page,
                page_size=page_size,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Dashboard tool-calls API error: {exc}")
        raise HTTPException(status_code=500, detail="Failed to read provenance tool calls.")
    return JSONResponse(content=result.to_dict(), headers=_SECURITY_HEADERS)


def _tool_guard_file() -> Path:
    return guard_settings.settings_path(_tool_guard_settings_path)


def _tool_guard_payload(path: Path, stored: Optional[guard_settings.ToolGuardSettings], error: Optional[str]) -> Dict[str, Any]:
    effective = stored or guard_settings.ToolGuardSettings()
    return {
        "path": str(path),
        "source": "file" if stored is not None else "defaults",
        "settings": effective.to_dict(),
        "default_allowed_hosts": list(DEFAULT_ALLOWED_HOSTS),
        "modes": ["observe", "enforce"],
        "file_error": error,
    }


@app.get("/api/dashboard/tool-guard", dependencies=[Depends(_verify_api_key)])
async def dashboard_tool_guard_get() -> JSONResponse:
    """The tool-call guard settings in effect for the proxy and the guardrail server, and where they come from."""
    path = _tool_guard_file()
    try:
        stored = guard_settings.load(path)
        error = None
    except ValueError as exc:
        stored, error = None, str(exc)
    return JSONResponse(content=_tool_guard_payload(path, stored, error), headers=_SECURITY_HEADERS)


@app.put("/api/dashboard/tool-guard", dependencies=[Depends(_verify_api_key)])
async def dashboard_tool_guard_put(request: Request) -> JSONResponse:
    """Validates and saves the tool-call guard settings, and records which settings changed in the provenance chain."""
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="the request body must be JSON")
    try:
        new = guard_settings.validate(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    path = _tool_guard_file()
    try:
        previous = guard_settings.load(path) or guard_settings.ToolGuardSettings()
    except ValueError:
        previous = guard_settings.ToolGuardSettings()
    changed = [name for name in ("mode", "extra_hosts", "judge_model", "judge_api_base", "judge_fail_closed")
               if getattr(previous, name) != getattr(new, name)]
    try:
        guard_settings.save(path, new)
    except OSError as exc:
        logger.error(f"Could not save tool-call guard settings: {exc}")
        raise HTTPException(status_code=500, detail="could not write the settings file")

    if changed:
        try:
            _dashboard_log().record(
                request_text="tool-call guard settings",
                response_text="settings changed: " + ", ".join(changed),
                metadata={
                    "event": "tool_guard_settings_changed",
                    "mode": new.mode,
                    "changed": ",".join(changed),
                    "extra_hosts": len(new.extra_hosts),
                    "judge_model": new.judge_model or "",
                    "judge_fail_closed": new.judge_fail_closed,
                },
            )
        except Exception as exc:
            logger.error(f"Tool-call guard settings saved but not recorded in the provenance chain: {exc}")
    return JSONResponse(content=_tool_guard_payload(path, new, None), headers=_SECURITY_HEADERS)


@app.get("/", include_in_schema=False)
async def root_redirect() -> RedirectResponse:
    """Redirect root URL to the interactive Web Audit Dashboard."""
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_ui(request: Request) -> HTMLResponse:
    """Serves the interactive CT Toolkit Web Audit & Telemetry Dashboard."""
    expected_key = _server_api_key or os.environ.get("CT_SERVER_API_KEY")
    client_host = (request.client.host if request.client else "127.0.0.1").lower()
    if not expected_key and client_host not in _LOOPBACK_HOSTS:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Web Dashboard is restricted to localhost unless CT_SERVER_API_KEY is configured.",
        )
    return HTMLResponse(content=_DASHBOARD_HTML, headers=_SECURITY_HEADERS)


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>CT Toolkit — Audit & Telemetry Dashboard</title>
  <style>
    :root {
      --bg-primary: #090d16;
      --bg-card: #111827;
      --bg-card-hover: #172033;
      --border: #1f2937;
      --border-highlight: #334155;
      --text-main: #f3f4f6;
      --text-muted: #9ca3af;
      --accent-cyan: #06b6d4;
      --accent-indigo: #6366f1;
      --ok-green: #10b981;
      --warn-amber: #f59e0b;
      --judge-orange: #f97316;
      --icm-red: #ef4444;
      --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      --font-mono: 'JetBrains Mono', 'Fira Code', ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg-primary);
      background-image:
        radial-gradient(at 0% 0%, rgba(6, 182, 212, 0.08) 0px, transparent 50%),
        radial-gradient(at 100% 0%, rgba(99, 102, 241, 0.08) 0px, transparent 50%);
      color: var(--text-main);
      font-family: var(--font-sans);
      min-height: 100vh;
      line-height: 1.5;
    }
    .container {
      max-width: 1360px;
      margin: 0 auto;
      padding: 24px 28px 48px;
    }
    header {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 24px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .brand-icon {
      width: 42px;
      height: 42px;
      border-radius: 10px;
      background: linear-gradient(135deg, rgba(6,182,212,0.2), rgba(99,102,241,0.25));
      border: 1px solid rgba(6,182,212,0.4);
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: var(--font-mono);
      font-weight: 700;
      color: var(--accent-cyan);
      font-size: 18px;
      box-shadow: 0 0 20px rgba(6,182,212,0.15);
    }
    .brand-title h1 {
      font-size: 1.25rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .brand-title p {
      font-size: 0.82rem;
      color: var(--text-muted);
    }
    .controls {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .pill-btn, .api-input {
      background: var(--bg-card);
      border: 1px solid var(--border-highlight);
      color: var(--text-main);
      padding: 7px 13px;
      border-radius: 8px;
      font-size: 0.82rem;
      font-family: var(--font-sans);
      transition: all 0.15s ease;
    }
    .pill-btn {
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 7px;
      font-weight: 500;
    }
    .pill-btn:hover {
      border-color: var(--accent-cyan);
      background: var(--bg-card-hover);
    }
    .pill-btn.active {
      border-color: rgba(16, 185, 129, 0.5);
      background: rgba(16, 185, 129, 0.1);
      color: #34d399;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--ok-green);
      display: inline-block;
      box-shadow: 0 0 8px var(--ok-green);
    }
    .dot.paused {
      background: var(--text-muted);
      box-shadow: none;
    }
    .banner-alert {
      display: none;
      padding: 14px 18px;
      border-radius: 10px;
      margin-bottom: 20px;
      font-size: 0.88rem;
      font-weight: 500;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .banner-alert.error {
      display: flex;
      background: rgba(239, 68, 68, 0.12);
      border: 1px solid rgba(239, 68, 68, 0.45);
      color: #fca5a5;
    }
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
      margin-bottom: 22px;
    }
    .card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px 20px;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.22);
    }
    .kpi-label {
      font-size: 0.76rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-muted);
      margin-bottom: 8px;
      font-weight: 600;
    }
    .kpi-value {
      font-size: 1.55rem;
      font-weight: 700;
      font-family: var(--font-mono);
      letter-spacing: -0.02em;
      margin-bottom: 6px;
    }
    .kpi-sub {
      font-size: 0.78rem;
      color: var(--text-muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 700;
      font-family: var(--font-mono);
      letter-spacing: 0.03em;
    }
    .badge.valid {
      background: rgba(16, 185, 129, 0.14);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.35);
    }
    .badge.compromised {
      background: rgba(239, 68, 68, 0.16);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.45);
    }
    .badge.tier-ok { background: rgba(16,185,129,0.14); color: #34d399; }
    .badge.tier-l1_warning { background: rgba(245,158,11,0.15); color: #fbbf24; }
    .badge.tier-l2_judge { background: rgba(249,115,22,0.16); color: #fb923c; }
    .badge.tier-l3_icm { background: rgba(239,68,68,0.18); color: #f87171; }
    .badge.status-promoted { background: rgba(16,185,129,0.15); color: #34d399; }
    .badge.status-cooldown { background: rgba(245,158,11,0.16); color: #fbbf24; }
    .badge.status-ready { background: rgba(59,130,246,0.16); color: #60a5fa; }

    .main-grid {
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 18px;
      margin-bottom: 22px;
    }
    @media (max-width: 960px) {
      .main-grid { grid-template-columns: 1fr; }
    }
    .section-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
    }
    .section-title {
      font-size: 0.95rem;
      font-weight: 600;
      color: var(--text-main);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .section-meta {
      font-size: 0.76rem;
      color: var(--text-muted);
      font-family: var(--font-mono);
    }
    .chart-box {
      width: 100%;
      height: 230px;
      position: relative;
    }
    .tier-list {
      display: flex;
      flex-direction: column;
      gap: 14px;
      margin-top: 8px;
    }
    .tier-row {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .tier-row-top {
      display: flex;
      justify-content: space-between;
      font-size: 0.82rem;
    }
    .tier-name {
      font-weight: 600;
      font-family: var(--font-mono);
    }
    .tier-stats {
      color: var(--text-muted);
      font-family: var(--font-mono);
    }
    .progress-track {
      width: 100%;
      height: 8px;
      background: #1e293b;
      border-radius: 999px;
      overflow: hidden;
    }
    .progress-bar {
      height: 100%;
      border-radius: 999px;
      transition: width 0.35s ease;
    }
    .table-wrap {
      overflow-x: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.83rem;
    }
    th, td {
      padding: 11px 12px;
      text-align: left;
      border-bottom: 1px solid var(--border);
    }
    th {
      color: var(--text-muted);
      font-weight: 600;
      text-transform: uppercase;
      font-size: 0.72rem;
      letter-spacing: 0.05em;
      background: rgba(15, 23, 42, 0.5);
    }
    tr:hover td {
      background: rgba(255, 255, 255, 0.015);
    }
    .mono {
      font-family: var(--font-mono);
      font-size: 0.79rem;
    }
    .hash-pill {
      font-family: var(--font-mono);
      font-size: 0.75rem;
      color: #94a3b8;
      background: rgba(30, 41, 59, 0.65);
      padding: 2px 7px;
      border-radius: 5px;
      border: 1px solid rgba(51, 65, 85, 0.6);
    }
    .empty-state {
      text-align: center;
      padding: 26px 12px;
      color: var(--text-muted);
      font-size: 0.84rem;
    }
    .section-stack {
      display: flex;
      flex-direction: column;
      gap: 20px;
    }
    .badge.verdict-passed { background: rgba(16,185,129,0.14); color: #34d399; }
    .badge.verdict-flagged { background: rgba(245,158,11,0.15); color: #fbbf24; }
    .badge.verdict-blocked { background: rgba(239,68,68,0.18); color: #f87171; }
    .badge.verdict-unknown { background: rgba(148,163,184,0.12); color: var(--text-muted); }
    .tc-stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .tc-stats .kpi-value { font-size: 1.3rem; }
    .tone-passed { color: #34d399; }
    .tone-flagged { color: #fbbf24; }
    .tone-blocked { color: #f87171; }
    .tc-chart { width: 100%; height: 100px; margin-bottom: 16px; }
    .tc-controls {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
      margin-bottom: 14px;
    }
    .tc-pager {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 14px;
      font-size: 0.82rem;
      color: var(--text-muted);
    }
    .pill-btn:disabled { opacity: 0.4; cursor: default; }
    .pill-btn:disabled:hover { border-color: var(--border-highlight); background: var(--bg-card); }
    th.sortable { cursor: pointer; user-select: none; white-space: nowrap; }
    th.sortable:hover { color: var(--text-main); }
    .sort-ind { color: var(--accent-cyan); margin-left: 4px; font-family: var(--font-mono); }
    .text-cell { max-width: 320px; white-space: normal; word-break: break-word; }
    .tg-summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 14px; }
    .tg-summary .kpi-value { font-size: 1.05rem; word-break: break-all; }
    .tg-form { display: flex; flex-direction: column; gap: 16px; }
    .tg-field { border: 0; padding: 0; margin: 0; min-width: 0; }
    .tg-label, .tg-legend { display: block; font-size: 0.84rem; font-weight: 600; color: var(--text-main); margin-bottom: 6px; padding: 0; }
    .tg-help { font-size: 0.78rem; color: var(--text-muted); margin-top: 6px; line-height: 1.45; }
    .tg-input { width: 100%; max-width: 520px; box-sizing: border-box; font-family: var(--font-mono); font-size: 0.82rem; }
    textarea.tg-input { resize: vertical; }
    .tg-choice { display: flex; align-items: center; gap: 8px; font-size: 0.86rem; margin: 4px 0; cursor: pointer; }
    .tg-box { display: block; padding: 12px 14px; border-radius: 10px; font-size: 0.84rem; line-height: 1.5; }
    .tg-box p { margin: 0 0 8px; }
    .tg-box.warn { background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.45); color: #fcd34d; }
    .tg-box.error { background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.45); color: #fca5a5; }
    .tg-box.warn .tg-choice { margin-bottom: 0; }
    .tg-summary + .tg-box { margin-bottom: 14px; }
    .tg-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .tg-status { font-size: 0.84rem; color: #34d399; }

    footer {
      margin-top: 28px;
      padding-top: 18px;
      border-top: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      font-size: 0.83rem;
      color: var(--text-muted);
    }
    footer a {
      color: var(--accent-cyan);
      font-weight: 600;
      text-decoration: none;
      transition: color 0.15s ease;
    }
    footer a:hover {
      color: #67e8f9;
      text-decoration: underline;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <div class="brand-icon">CT</div>
        <div class="brand-title">
          <h1>Computational Theseus Toolkit <span class="badge valid" id="kernel-pill">general</span></h1>
          <p>Nested Agentic Architecture (NAA) — Provenance Chain &amp; Divergence Telemetry</p>
        </div>
      </div>
      <div class="controls">
        <input type="password" id="api-key-input" class="api-input" placeholder="API Key (if required)" style="width: 170px;" />
        <button class="pill-btn active" id="toggle-live-btn" onclick="toggleAutoRefresh()">
          <span class="dot" id="live-dot"></span>
          <span id="live-label">Live (5s)</span>
        </button>
        <button class="pill-btn" onclick="fetchTelemetry()">&#x21bb; Refresh Now</button>
      </div>
    </header>

    <div class="banner-alert" id="alert-banner"></div>

    <div class="kpi-grid">
      <div class="card">
        <div class="kpi-label">Provenance Chain Integrity</div>
        <div class="kpi-value" id="kpi-chain-status">CHECKING...</div>
        <div class="kpi-sub" id="kpi-vault-info">Vault: —</div>
      </div>
      <div class="card">
        <div class="kpi-label">Verified Active Entries</div>
        <div class="kpi-value" id="kpi-verified-count">0</div>
        <div class="kpi-sub" id="kpi-head-hash">Head: —</div>
      </div>
      <div class="card">
        <div class="kpi-label">Divergence Score (Avg / Max)</div>
        <div class="kpi-value" id="kpi-scores">0.0000 / 0.0000</div>
        <div class="kpi-sub">Thresholds: L1=0.15 · L2=0.30 · L3=0.50</div>
      </div>
      <div class="card">
        <div class="kpi-label">Staged Kernel Rules</div>
        <div class="kpi-value" id="kpi-staged-count">0</div>
        <div class="kpi-sub" id="kpi-staged-sub">Reflective Endorsement Sandbox</div>
      </div>
    </div>

    <div class="main-grid">
      <div class="card">
        <div class="section-header">
          <div class="section-title">L1 / L2 / L3 Divergence Score Timeline</div>
          <div class="section-meta" id="chart-meta">Last 0 interactions</div>
        </div>
        <div class="chart-box" id="chart-container"></div>
      </div>

      <div class="card">
        <div class="section-header">
          <div class="section-title">Divergence Tier Breakdown</div>
          <div class="section-meta" id="tier-total-meta">0 total</div>
        </div>
        <div class="tier-list" id="tier-breakdown"></div>
      </div>
    </div>

    <div class="section-stack">
      <div class="card">
        <div class="section-header">
          <div class="section-title">Staged Kernel Rules (Sandbox Cooldown)</div>
          <div class="section-meta">Reflective Endorsement Queue</div>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Endorsement ID</th>
                <th>Commitment ID</th>
                <th>Staged Rule Text</th>
                <th>Sandbox Status</th>
              </tr>
            </thead>
            <tbody id="staged-rules-tbody">
              <tr><td colspan="4" class="empty-state">Loading staged rules...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <div class="section-header">
          <div class="section-title">Immutable Provenance Chain Ledger (Recent Entries)</div>
          <div class="section-meta" id="ledger-meta">HMAC-SHA256 Verified</div>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Entry ID</th>
                <th>Timestamp</th>
                <th>Kernel / Model</th>
                <th>Divergence</th>
                <th>Tier</th>
                <th>Prev Hash &#x2192; Content Hash</th>
                <th>HMAC Signature</th>
              </tr>
            </thead>
            <tbody id="ledger-tbody">
              <tr><td colspan="7" class="empty-state">Loading provenance ledger...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="card" id="tool-calls-card">
        <div class="section-header">
          <div class="section-title">Tool Calls (Guard Decisions)</div>
          <div class="section-meta" id="tc-meta">0 recorded</div>
        </div>
        <div class="tc-stats">
          <div class="card">
            <div class="kpi-label">Total</div>
            <div class="kpi-value" id="tc-total">0</div>
          </div>
          <div class="card">
            <div class="kpi-label">Passed</div>
            <div class="kpi-value tone-passed" id="tc-passed">0</div>
          </div>
          <div class="card">
            <div class="kpi-label">Flagged</div>
            <div class="kpi-value tone-flagged" id="tc-flagged">0</div>
          </div>
          <div class="card">
            <div class="kpi-label">Blocked</div>
            <div class="kpi-value tone-blocked" id="tc-blocked">0</div>
          </div>
        </div>
        <div class="tc-chart" id="tc-chart"></div>
        <div class="tc-controls">
          <select id="tc-verdict" class="api-input" aria-label="Filter by verdict">
            <option value="">All verdicts</option>
            <option value="passed">passed</option>
            <option value="flagged">flagged</option>
            <option value="blocked">blocked</option>
          </select>
          <input type="text" id="tc-tool" class="api-input" placeholder="Tool name contains..." aria-label="Filter by tool" maxlength="256" style="width: 180px;" />
          <input type="search" id="tc-search" class="api-input" placeholder="Search findings, judge, model..." aria-label="Search tool calls" maxlength="256" style="width: 240px;" />
          <select id="tc-page-size" class="api-input" aria-label="Rows per page">
            <option value="10">10 / page</option>
            <option value="25" selected>25 / page</option>
            <option value="50">50 / page</option>
            <option value="100">100 / page</option>
          </select>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th class="sortable" data-sort="time">Time<span class="sort-ind"></span></th>
                <th class="sortable" data-sort="tool">Tool<span class="sort-ind"></span></th>
                <th class="sortable" data-sort="verdict">Verdict<span class="sort-ind"></span></th>
                <th>Findings</th>
                <th>Judge</th>
                <th class="sortable" data-sort="mode">Mode<span class="sort-ind"></span></th>
                <th>Model</th>
                <th>ID</th>
              </tr>
            </thead>
            <tbody id="tc-tbody">
              <tr><td colspan="8" class="empty-state">Loading tool calls...</td></tr>
            </tbody>
          </table>
        </div>
        <div class="tc-pager">
          <div id="tc-count">0 matching</div>
          <div class="controls">
            <button class="pill-btn" id="tc-prev" type="button">&#x2039; Prev</button>
            <span class="section-meta" id="tc-page-label">page 1 of 1</span>
            <button class="pill-btn" id="tc-next" type="button">Next &#x203A;</button>
          </div>
        </div>
      </div>

      <div class="card" id="tool-guard-card">
        <div class="section-header">
          <div class="section-title">Tool-Call Guard Settings</div>
        </div>
        <div class="tg-summary" aria-live="polite">
          <div class="card">
            <div class="kpi-label">Effective mode</div>
            <div class="kpi-value" id="tg-sum-mode">&#x2014;</div>
          </div>
          <div class="card">
            <div class="kpi-label">Source</div>
            <div class="kpi-value" id="tg-sum-source">&#x2014;</div>
          </div>
          <div class="card">
            <div class="kpi-label">Settings file</div>
            <div class="kpi-value mono" id="tg-sum-path">&#x2014;</div>
          </div>
        </div>
        <div class="tg-box warn" id="tg-file-error" role="alert" style="display: none;"></div>
        <div class="tg-box error" id="tg-load-error" role="alert" style="display: none; margin-bottom: 14px;"></div>
        <form id="tg-form" class="tg-form" novalidate>
          <fieldset class="tg-field" id="tg-mode">
            <legend class="tg-legend">Mode</legend>
            <label class="tg-choice" for="tg-mode-observe">
              <input type="radio" name="tg-mode" id="tg-mode-observe" value="observe" checked />
              Observe (default): records tool calls only
            </label>
            <label class="tg-choice" for="tg-mode-enforce">
              <input type="radio" name="tg-mode" id="tg-mode-enforce" value="enforce" />
              Enforce: removes high-severity tool calls before the agent runs them
            </label>
            <div class="tg-box warn" id="tg-enforce-warning" style="display: none; margin-top: 10px;">
              <p>Enforce changes what agents can run. High-severity tool calls are removed before the agent runs them.</p>
              <label class="tg-choice" for="tg-ack">
                <input type="checkbox" id="tg-ack" />
                I understand
              </label>
            </div>
          </fieldset>
          <div class="tg-field">
            <div class="tg-help">Already allowed by default: <span class="mono" id="tg-default-hosts">loading...</span></div>
            <label class="tg-label" for="tg-hosts">Extra allowed hosts</label>
            <textarea id="tg-hosts" class="api-input tg-input" rows="4" placeholder="one host per line, or comma separated" aria-describedby="tg-hosts-help" spellcheck="false"></textarea>
            <div class="tg-help" id="tg-hosts-help">Hosts that tool calls may reach beyond the defaults, for example internal.example.com.</div>
          </div>
          <div class="tg-field">
            <label class="tg-label" for="tg-judge-model">Judge model</label>
            <input type="text" id="tg-judge-model" class="api-input tg-input" placeholder="openai/gemma-4-E4B-it" maxlength="500" autocomplete="off" aria-describedby="tg-judge-model-help" />
            <div class="tg-help" id="tg-judge-model-help">The judge is off when this is empty.</div>
          </div>
          <div class="tg-field">
            <label class="tg-label" for="tg-judge-base">Judge API base URL</label>
            <input type="text" id="tg-judge-base" class="api-input tg-input" placeholder="http://localhost:8090/v1" maxlength="500" autocomplete="off" aria-describedby="tg-judge-base-help" />
            <div class="tg-help" id="tg-judge-base-help">OpenAI-compatible base URL, e.g. http://localhost:8090/v1</div>
          </div>
          <div class="tg-field">
            <label class="tg-choice" for="tg-fail-closed">
              <input type="checkbox" id="tg-fail-closed" aria-describedby="tg-fail-closed-help" />
              Fail closed
            </label>
            <div class="tg-help" id="tg-fail-closed-help">Block a suspicious call when the judge gives no answer.</div>
          </div>
          <div class="tg-actions">
            <button type="submit" class="pill-btn" id="tg-save" disabled>Save</button>
            <button type="button" class="pill-btn" id="tg-reset">Reset</button>
            <span class="tg-status" id="tg-status" role="status"></span>
          </div>
          <div class="tg-box error" id="tg-error" role="alert" style="display: none;"></div>
        </form>
      </div>
    </div>

    <footer>
      <div>Computational Theseus Toolkit (CT Toolkit) — Open-Source Identity Continuity Guardrails</div>
      <div>For Enterprise Solution: <a href="https://www.theseusguard.com" target="_blank" rel="noopener noreferrer">Theseus Guard &#x2197;</a></div>
    </footer>
  </div>

  <script>
    let autoRefresh = true;
    let refreshTimer = null;

    const apiKeyInput = document.getElementById('api-key-input');
    apiKeyInput.value = sessionStorage.getItem('ct_dashboard_api_key') || '';
    apiKeyInput.addEventListener('change', () => {
      sessionStorage.setItem('ct_dashboard_api_key', apiKeyInput.value.trim());
      refreshDashboard();
    });

    function escapeHtml(str) {
      return String(str ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function showBanner(msg) {
      const el = document.getElementById('alert-banner');
      if (!msg) {
        el.className = 'banner-alert';
        el.innerHTML = '';
        return;
      }
      el.className = 'banner-alert error';
      el.innerHTML = `<span>${escapeHtml(msg)}</span>`;
    }

    function renderChart(series) {
      const container = document.getElementById('chart-container');
      document.getElementById('chart-meta').textContent = `Last ${series.length} scored interactions`;
      if (!series || series.length === 0) {
        container.innerHTML = `<div class="empty-state" style="padding-top:75px;">No scored interactions recorded in provenance vault yet.</div>`;
        return;
      }
      const w = container.clientWidth || 720;
      const h = 225;
      const padL = 42, padR = 16, padT = 16, padB = 28;
      const plotW = Math.max(w - padL - padR, 100);
      const plotH = h - padT - padB;
      const maxVal = Math.max(0.65, ...series.map(d => d.score));

      const xFor = (i) => series.length === 1 ? padL + plotW / 2 : padL + (i / (series.length - 1)) * plotW;
      const yFor = (v) => padT + plotH - Math.min(Math.max(v / maxVal, 0), 1) * plotH;

      const thresholds = [
        { val: 0.15, label: 'L1 (0.15)', color: '#f59e0b' },
        { val: 0.30, label: 'L2 (0.30)', color: '#f97316' },
        { val: 0.50, label: 'L3 (0.50)', color: '#ef4444' }
      ];

      let threshSvg = '';
      for (const t of thresholds) {
        const y = yFor(t.val);
        threshSvg += `
          <line x1="${padL}" y1="${y}" x2="${w - padR}" y2="${y}" stroke="${t.color}" stroke-dasharray="4 4" stroke-width="1" opacity="0.55" />
          <text x="${padL - 6}" y="${y + 3}" text-anchor="end" fill="${t.color}" font-size="10" font-family="monospace">${t.val.toFixed(2)}</text>
        `;
      }

      const pts = series.map((d, i) => `${xFor(i).toFixed(1)},${yFor(d.score).toFixed(1)}`).join(' ');
      const areaPts = `${xFor(0).toFixed(1)},${(padT + plotH).toFixed(1)} ${pts} ${xFor(series.length - 1).toFixed(1)},${(padT + plotH).toFixed(1)}`;

      let circles = '';
      series.forEach((d, i) => {
        const cx = xFor(i).toFixed(1);
        const cy = yFor(d.score).toFixed(1);
        let cColor = '#10b981';
        if (d.score >= 0.50) cColor = '#ef4444';
        else if (d.score >= 0.30) cColor = '#f97316';
        else if (d.score >= 0.15) cColor = '#f59e0b';
        circles += `<circle cx="${cx}" cy="${cy}" r="3.6" fill="${cColor}" stroke="#090d16" stroke-width="1.5"><title>[${escapeHtml(d.id)}] Score: ${d.score} (${escapeHtml(d.tier.toUpperCase())})</title></circle>`;
      });

      container.innerHTML = `
        <svg width="100%" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
          <defs>
            <linearGradient id="scoreGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#06b6d4" stop-opacity="0.32"/>
              <stop offset="100%" stop-color="#06b6d4" stop-opacity="0.0"/>
            </linearGradient>
          </defs>
          <line x1="${padL}" y1="${padT + plotH}" x2="${w - padR}" y2="${padT + plotH}" stroke="#1f2937" stroke-width="1" />
          <text x="${padL - 6}" y="${padT + plotH + 3}" text-anchor="end" fill="#6b7280" font-size="10" font-family="monospace">0.00</text>
          ${threshSvg}
          <polygon points="${areaPts}" fill="url(#scoreGrad)" />
          <polyline fill="none" stroke="#06b6d4" stroke-width="2.2" points="${pts}" />
          ${circles}
        </svg>
      `;
    }

    function renderTiers(tierCounts) {
      const items = [
        { key: 'ok', label: 'OK (< 0.15)', color: '#10b981' },
        { key: 'l1_warning', label: 'L1_WARNING (0.15–0.30)', color: '#f59e0b' },
        { key: 'l2_judge', label: 'L2_JUDGE (0.30–0.50)', color: '#f97316' },
        { key: 'l3_icm', label: 'L3_ICM (≥ 0.50)', color: '#ef4444' }
      ];
      const total = Object.values(tierCounts || {}).reduce((a, b) => a + b, 0);
      document.getElementById('tier-total-meta').textContent = `${total} classified`;

      const container = document.getElementById('tier-breakdown');
      container.innerHTML = items.map(item => {
        const count = (tierCounts && tierCounts[item.key]) || 0;
        const pct = total > 0 ? (count / total * 100) : 0;
        return `
          <div class="tier-row">
            <div class="tier-row-top">
              <span class="tier-name" style="color:${item.color}">${escapeHtml(item.label)}</span>
              <span class="tier-stats">${count} (${pct.toFixed(1)}%)</span>
            </div>
            <div class="progress-track">
              <div class="progress-bar" style="width:${pct.toFixed(1)}%; background:${item.color};"></div>
            </div>
          </div>
        `;
      }).join('');
    }

    function renderStagedRules(rules) {
      const tbody = document.getElementById('staged-rules-tbody');
      document.getElementById('kpi-staged-count').textContent = String((rules || []).length);
      if (!rules || rules.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" class="empty-state">No staged rules recorded in provenance log.</td></tr>`;
        return;
      }
      tbody.innerHTML = rules.map(r => {
        let badge = `<span class="badge status-ready">READY FOR PROMOTION</span>`;
        if (r.status === 'PROMOTED') {
          badge = `<span class="badge status-promoted">PROMOTED</span>`;
        } else if (r.status === 'ACTIVE_COOLDOWN') {
          badge = `<span class="badge status-cooldown">ACTIVE COOLDOWN (${r.remaining_seconds}s left)</span>`;
        }
        return `
          <tr>
            <td class="mono">${escapeHtml(String(r.endorsement_id).slice(0, 12))}</td>
            <td class="mono">${escapeHtml(r.commitment_id)}</td>
            <td>${escapeHtml(r.rule_text)}</td>
            <td>${badge}</td>
          </tr>
        `;
      }).join('');
    }

    function renderLedger(entries) {
      const tbody = document.getElementById('ledger-tbody');
      if (!entries || entries.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="empty-state">Provenance ledger is currently empty.</td></tr>`;
        return;
      }
      tbody.innerHTML = entries.map(e => {
        const dt = new Date(e.timestamp * 1000).toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
        const scoreStr = e.divergence_score !== null && e.divergence_score !== undefined
          ? Number(e.divergence_score).toFixed(4)
          : '—';
        const tierClass = `tier-${e.tier || 'ok'}`;
        return `
          <tr>
            <td class="mono">${escapeHtml(String(e.id).slice(0, 10))}…</td>
            <td class="mono">${escapeHtml(dt)}</td>
            <td class="mono">${escapeHtml(e.kernel)} / ${escapeHtml(e.model)}</td>
            <td class="mono">${escapeHtml(scoreStr)}</td>
            <td><span class="badge ${escapeHtml(tierClass)}">${escapeHtml(String(e.tier || 'ok').toUpperCase())}</span></td>
            <td>
              <span class="hash-pill">${escapeHtml(String(e.prev_entry_hash).slice(0, 10))}…</span>
              &#x2192;
              <span class="hash-pill">${escapeHtml(String(e.content_hash).slice(0, 10))}…</span>
            </td>
            <td><span class="hash-pill">${escapeHtml(String(e.hmac_signature).slice(0, 14))}…</span></td>
          </tr>
        `;
      }).join('');
    }

    function dashboardHeaders() {
      const headers = {};
      const key = apiKeyInput.value.trim();
      if (key) {
        headers['Authorization'] = `Bearer ${key}`;
      }
      return headers;
    }

    async function fetchTelemetry() {
      try {
        const res = await fetch('/api/dashboard/status?limit=200', { headers: dashboardHeaders() });
        if (res.status === 401) {
          showBanner('Authentication required (401 Unauthorized): Enter your Server API Key in the top-right box.');
          return;
        }
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          showBanner(`Telemetry error (${res.status}): ${err.detail || res.statusText}`);
          return;
        }
        const data = await res.json();
        showBanner('');

        document.getElementById('kernel-pill').textContent = `kernel: ${data.active_kernel}`;
        const chainEl = document.getElementById('kpi-chain-status');
        if (data.chain_ok) {
          chainEl.innerHTML = `<span class="badge valid">&#x2713; VALID (HMAC VERIFIED)</span>`;
        } else {
          chainEl.innerHTML = `<span class="badge compromised">&#x26A0; COMPROMISED (${escapeHtml(data.tampered_entry_id)})</span>`;
          showBanner(`CRITICAL: Provenance Hash Chain tampering detected at entry ${data.tampered_entry_id}!`);
        }

        document.getElementById('kpi-vault-info').textContent = `Vault: ${data.vault_path} (${data.vault_type})`;
        document.getElementById('kpi-verified-count').textContent = `${data.verified_count}`;
        document.getElementById('kpi-head-hash').textContent = `Head: ${String(data.last_hash).slice(0, 24)}…`;
        document.getElementById('kpi-scores').textContent = `${Number(data.avg_score).toFixed(4)} / ${Number(data.max_score).toFixed(4)}`;

        renderChart(data.score_series || []);
        renderTiers(data.tier_counts || {});
        renderStagedRules(data.staged_rules || []);
        renderLedger(data.recent_entries || []);
      } catch (err) {
        showBanner(`Failed to connect to CT Toolkit Server: ${err.message}`);
      }
    }

    // Tool-call table state. It survives the 5 s auto-refresh: refreshDashboard() re-requests with the same query.
    const TOOL_CALL_COLORS = { passed: '#10b981', flagged: '#f59e0b', blocked: '#ef4444' };
    const toolCallState = { page: 1, pageSize: 25, sort: 'time', order: 'desc', verdict: '', tool: '', q: '' };
    const toolQueryTimers = {};
    let toolCallRequestSeq = 0;

    function verdictClass(verdict) {
      // Only known verdicts get a colour class; anything else from the log falls back to a neutral badge.
      return Object.prototype.hasOwnProperty.call(TOOL_CALL_COLORS, verdict) ? `verdict-${verdict}` : 'verdict-unknown';
    }

    function renderToolChart(byVerdict) {
      const container = document.getElementById('tc-chart');
      const keys = Object.keys(TOOL_CALL_COLORS);
      const counts = keys.map(k => Number((byVerdict && byVerdict[k]) || 0));
      const max = Math.max(1, ...counts);
      const w = container.clientWidth || 720;
      const labelW = 84, valueW = 56, rowH = 30, barH = 16;
      const barW = Math.max(w - labelW - valueW, 60);
      const rows = keys.map((k, i) => {
        const y = i * rowH + 4;
        const bw = (counts[i] / max) * barW;
        return `
          <text x="0" y="${y + 13}" fill="#9ca3af" font-size="12" font-family="monospace">${escapeHtml(k)}</text>
          <rect x="${labelW}" y="${y}" width="${barW}" height="${barH}" rx="4" fill="#1f2937" />
          <rect x="${labelW}" y="${y}" width="${bw.toFixed(1)}" height="${barH}" rx="4" fill="${TOOL_CALL_COLORS[k]}" />
          <text x="${labelW + barW + 8}" y="${y + 13}" fill="#f3f4f6" font-size="12" font-family="monospace">${counts[i]}</text>
        `;
      }).join('');
      container.innerHTML = `<svg width="100%" height="100" viewBox="0 0 ${w} 100" preserveAspectRatio="none">${rows}</svg>`;
    }

    function renderToolCallRows(items) {
      const tbody = document.getElementById('tc-tbody');
      if (!items.length) {
        tbody.innerHTML = `<tr><td colspan="8" class="empty-state">No tool calls match these filters.</td></tr>`;
        return;
      }
      tbody.innerHTML = items.map(r => {
        const dt = new Date(r.timestamp * 1000).toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
        const verdict = String(r.verdict || '—');
        return `
          <tr>
            <td class="mono">${escapeHtml(dt)}</td>
            <td class="mono">${escapeHtml(r.tool || '—')}</td>
            <td><span class="badge ${escapeHtml(verdictClass(r.verdict))}">${escapeHtml(verdict.toUpperCase())}</span></td>
            <td class="text-cell">${escapeHtml(r.findings || '—')}</td>
            <td class="text-cell">${escapeHtml(r.judge || '—')}</td>
            <td class="mono">${escapeHtml(r.mode || '—')}</td>
            <td class="mono">${escapeHtml(r.llm_model || '—')}</td>
            <td><span class="hash-pill" title="${escapeHtml(r.id)}">${escapeHtml(String(r.id).slice(0, 10))}…</span></td>
          </tr>
        `;
      }).join('');
    }

    function renderToolCalls(data) {
      const summary = data.summary || {};
      const byVerdict = summary.by_verdict || {};
      document.getElementById('tc-meta').textContent = `${summary.total ?? 0} recorded`;
      document.getElementById('tc-total').textContent = String(summary.total ?? 0);
      document.getElementById('tc-passed').textContent = String(byVerdict.passed ?? 0);
      document.getElementById('tc-flagged').textContent = String(byVerdict.flagged ?? 0);
      document.getElementById('tc-blocked').textContent = String(byVerdict.blocked ?? 0);
      renderToolChart(byVerdict);

      renderToolCallRows(data.items || []);
      document.getElementById('tc-count').textContent = `${data.total} matching`;
      document.getElementById('tc-page-label').textContent = `page ${data.page} of ${data.pages}`;
      document.getElementById('tc-prev').disabled = data.page <= 1;
      document.getElementById('tc-next').disabled = data.page >= data.pages;

      document.querySelectorAll('th.sortable').forEach(th => {
        const active = th.dataset.sort === toolCallState.sort;
        th.querySelector('.sort-ind').textContent = active ? (toolCallState.order === 'asc' ? '▲' : '▼') : '';
      });
    }

    function renderToolCallError(message) {
      document.getElementById('tc-tbody').innerHTML = `<tr><td colspan="8" class="empty-state">${escapeHtml(message)}</td></tr>`;
    }

    async function fetchToolCalls() {
      const seq = ++toolCallRequestSeq;
      const params = new URLSearchParams({
        page: String(toolCallState.page),
        page_size: String(toolCallState.pageSize),
        sort: toolCallState.sort,
        order: toolCallState.order,
      });
      if (toolCallState.verdict) params.set('verdict', toolCallState.verdict);
      if (toolCallState.tool) params.set('tool', toolCallState.tool);
      if (toolCallState.q) params.set('q', toolCallState.q);
      try {
        const res = await fetch(`/api/dashboard/tool-calls?${params.toString()}`, { headers: dashboardHeaders() });
        if (seq !== toolCallRequestSeq) return;
        if (res.status === 401) {
          renderToolCallError('Authentication required (401): enter your Server API Key in the top-right box.');
          return;
        }
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          const detail = typeof err.detail === 'string' ? err.detail : res.statusText;
          renderToolCallError(`Tool calls error (${res.status}): ${detail}`);
          return;
        }
        const data = await res.json();
        if (seq !== toolCallRequestSeq) return;
        toolCallState.page = data.page;
        renderToolCalls(data);
      } catch (err) {
        if (seq === toolCallRequestSeq) renderToolCallError(`Failed to load tool calls: ${err.message}`);
      }
    }

    function refreshDashboard() {
      fetchTelemetry();
      fetchToolCalls();
      // Poll refreshes only the read-only summary; the form is filled on first load.
      fetchToolGuard(!toolGuardState.loaded);
    }

    function setToolQuery(key, value) {
      clearTimeout(toolQueryTimers[key]);
      toolQueryTimers[key] = setTimeout(() => {
        toolCallState[key] = value.trim();
        toolCallState.page = 1;
        fetchToolCalls();
      }, 250);
    }

    document.getElementById('tc-verdict').addEventListener('change', (e) => {
      toolCallState.verdict = e.target.value;
      toolCallState.page = 1;
      fetchToolCalls();
    });
    document.getElementById('tc-page-size').addEventListener('change', (e) => {
      toolCallState.pageSize = Number(e.target.value);
      toolCallState.page = 1;
      fetchToolCalls();
    });
    document.getElementById('tc-tool').addEventListener('input', (e) => setToolQuery('tool', e.target.value));
    document.getElementById('tc-search').addEventListener('input', (e) => setToolQuery('q', e.target.value));
    document.getElementById('tc-prev').addEventListener('click', () => {
      toolCallState.page = Math.max(1, toolCallState.page - 1);
      fetchToolCalls();
    });
    document.getElementById('tc-next').addEventListener('click', () => {
      toolCallState.page += 1;
      fetchToolCalls();
    });
    document.querySelectorAll('th.sortable').forEach(th => {
      th.addEventListener('click', () => {
        const key = th.dataset.sort;
        if (toolCallState.sort === key) {
          toolCallState.order = toolCallState.order === 'asc' ? 'desc' : 'asc';
        } else {
          toolCallState.sort = key;
          toolCallState.order = 'asc';
        }
        toolCallState.page = 1;
        fetchToolCalls();
      });
    });

    // Tool-call guard settings. The read-only summary refreshes with the poll. The form is filled only on first load,
    // after a Save and on Reset, so the poll never overwrites what the operator is typing.
    const toolGuardState = { loaded: false, saving: false };

    function setShown(el, shown) {
      el.style.display = shown ? '' : 'none';
    }

    function showToolGuardLoadError(message) {
      const box = document.getElementById('tg-load-error');
      box.textContent = message;
      setShown(box, Boolean(message));
    }

    function showToolGuardError(message) {
      const box = document.getElementById('tg-error');
      box.textContent = message;
      setShown(box, Boolean(message));
    }

    function showToolGuardStatus(text) {
      document.getElementById('tg-status').textContent = text;
    }

    function isToolGuardEnforce() {
      return document.getElementById('tg-mode-enforce').checked;
    }

    function splitToolGuardHosts(value) {
      return value.split(/[\n,]/).map(h => h.trim()).filter(h => h.length > 0);
    }

    function updateToolGuardControls() {
      const enforce = isToolGuardEnforce();
      const ack = document.getElementById('tg-ack');
      setShown(document.getElementById('tg-enforce-warning'), enforce);
      // Save stays off until the saved values are loaded, so an empty form can never overwrite the file.
      document.getElementById('tg-save').disabled = !toolGuardState.loaded || toolGuardState.saving || (enforce && !ack.checked);
    }

    function renderToolGuardSummary(data) {
      const mode = data.settings.mode;
      const modeEl = document.getElementById('tg-sum-mode');
      modeEl.textContent = mode;
      modeEl.className = `kpi-value ${mode === 'enforce' ? 'tone-flagged' : 'tone-passed'}`;
      document.getElementById('tg-sum-source').textContent = data.source === 'file' ? 'saved file' : 'built-in defaults';
      document.getElementById('tg-sum-path').textContent = data.path;
      document.getElementById('tg-default-hosts').textContent = (data.default_allowed_hosts || []).join(', ') || 'none';
      const fileError = document.getElementById('tg-file-error');
      fileError.textContent = data.file_error ? `Settings file error: ${data.file_error}. Saving will replace this file.` : '';
      setShown(fileError, Boolean(data.file_error));
    }

    function populateToolGuardForm(data) {
      const s = data.settings;
      document.getElementById(s.mode === 'enforce' ? 'tg-mode-enforce' : 'tg-mode-observe').checked = true;
      document.getElementById('tg-ack').checked = false;
      document.getElementById('tg-hosts').value = (s.extra_hosts || []).join('\n');
      document.getElementById('tg-judge-model').value = s.judge_model || '';
      document.getElementById('tg-judge-base').value = s.judge_api_base || '';
      document.getElementById('tg-fail-closed').checked = Boolean(s.judge_fail_closed);
      toolGuardState.loaded = true;
      updateToolGuardControls();
    }

    async function fetchToolGuard(populateForm) {
      try {
        const res = await fetch('/api/dashboard/tool-guard', { headers: dashboardHeaders() });
        if (res.status === 401) {
          showToolGuardLoadError('Authentication required (401): enter your Server API Key in the top-right box.');
          return;
        }
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          const detail = typeof err.detail === 'string' ? err.detail : res.statusText;
          showToolGuardLoadError(`Tool-call guard settings error (${res.status}): ${detail}`);
          return;
        }
        const data = await res.json();
        showToolGuardLoadError('');
        renderToolGuardSummary(data);
        if (populateForm) populateToolGuardForm(data);
      } catch (err) {
        showToolGuardLoadError(`Failed to load tool-call guard settings: ${err.message}`);
      }
    }

    async function saveToolGuard(event) {
      event.preventDefault();
      if (toolGuardState.saving || (isToolGuardEnforce() && !document.getElementById('tg-ack').checked)) return;
      const body = {
        mode: isToolGuardEnforce() ? 'enforce' : 'observe',
        extra_hosts: splitToolGuardHosts(document.getElementById('tg-hosts').value),
        judge_model: document.getElementById('tg-judge-model').value.trim() || null,
        judge_api_base: document.getElementById('tg-judge-base').value.trim() || null,
        judge_fail_closed: document.getElementById('tg-fail-closed').checked,
      };
      toolGuardState.saving = true;
      updateToolGuardControls();
      showToolGuardError('');
      showToolGuardStatus('Saving...');
      try {
        const res = await fetch('/api/dashboard/tool-guard', {
          method: 'PUT',
          headers: { ...dashboardHeaders(), 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({}));
        if (res.status === 401) {
          showToolGuardStatus('');
          showToolGuardError('Authentication required (401): enter your Server API Key in the top-right box.');
        } else if (!res.ok) {
          showToolGuardStatus('');
          showToolGuardError(typeof data.detail === 'string' ? data.detail : `Save failed (${res.status}): ${res.statusText}`);
        } else {
          renderToolGuardSummary(data);
          populateToolGuardForm(data);
          showToolGuardStatus('Saved');
        }
      } catch (err) {
        showToolGuardStatus('');
        showToolGuardError(`Failed to save: ${err.message}`);
      } finally {
        toolGuardState.saving = false;
        updateToolGuardControls();
      }
    }

    function resetToolGuardForm() {
      showToolGuardError('');
      showToolGuardStatus('');
      fetchToolGuard(true);
    }

    document.getElementById('tg-form').addEventListener('submit', saveToolGuard);
    document.getElementById('tg-reset').addEventListener('click', resetToolGuardForm);
    document.querySelectorAll('input[name="tg-mode"]').forEach(radio => {
      radio.addEventListener('change', () => {
        // Each switch to enforce needs a fresh acknowledgement.
        document.getElementById('tg-ack').checked = false;
        updateToolGuardControls();
      });
    });
    document.getElementById('tg-ack').addEventListener('change', updateToolGuardControls);

    function toggleAutoRefresh() {
      autoRefresh = !autoRefresh;
      const btn = document.getElementById('toggle-live-btn');
      const dot = document.getElementById('live-dot');
      const label = document.getElementById('live-label');
      if (autoRefresh) {
        btn.classList.add('active');
        dot.classList.remove('paused');
        label.textContent = 'Live (5s)';
      } else {
        btn.classList.remove('active');
        dot.classList.add('paused');
        label.textContent = 'Paused';
      }
    }

    refreshDashboard();
    refreshTimer = setInterval(() => {
      if (autoRefresh) refreshDashboard();
    }, 5000);
    window.addEventListener('resize', () => refreshDashboard());
  </script>
</body>
</html>
"""


def start_server(
    wrapper: TheseusWrapper,
    host: str,
    port: int,
    api_key: Optional[str] = None,
    full_verify_interval_s: Optional[float] = None,
    tool_guard_settings: Optional[str] = None,
    tool_guard_overrides: Optional[dict] = None,
):
    """Inject wrapper and start uvicorn server."""
    global _wrapper_instance, _full_verify_interval_s, _guard_watcher, _guard_applied
    _validate_bind_security(host, api_key)
    _full_verify_interval_s = full_verify_interval_s
    _guard_watcher = SettingsWatcher(guard_settings.settings_path(tool_guard_settings), overrides=tool_guard_overrides)
    _guard_applied = None
    _wrapper_instance = wrapper
    if api_key is not None:
        configure_server_auth(api_key)
    logger.info(f"Starting Guardrail Server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


def start_dashboard_server(
    vault_path: str = "./ct_provenance.db",
    vault_type: str = "local",
    host: str = "127.0.0.1",
    port: int = 8001,
    api_key: Optional[str] = None,
    full_verify_interval_s: Optional[float] = None,
    tool_guard_settings: Optional[str] = None,
) -> None:
    """Starts the standalone Web Audit & Telemetry Dashboard server."""
    global _tool_guard_settings_path
    _validate_bind_security(host, api_key)
    _tool_guard_settings_path = tool_guard_settings
    configure_dashboard_vault(vault_path=vault_path, vault_type=vault_type, full_verify_interval_s=full_verify_interval_s)
    if api_key is not None:
        configure_server_auth(api_key)
    logger.info(f"Starting CT Toolkit Web Audit Dashboard on http://{host}:{port}/dashboard")
    uvicorn.run(app, host=host, port=port)

