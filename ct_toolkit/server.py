"""
ct_toolkit.server
-----------------
FastAPI server for LiteLLM Generic Guardrail API.
Exposes /guardrail/check to LiteLLM for pre-call and post-call validation.
"""
from __future__ import annotations

import hmac
import os
import time
from typing import Any, Dict, List, Optional
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
import uvicorn

from ct_toolkit.core.wrapper import TheseusWrapper
from ct_toolkit.provenance.log import ProvenanceLog
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


def configure_server_auth(api_key: Optional[str]) -> None:
    """Configures optional API Key / Bearer Token authentication for the guardrail server."""
    global _server_api_key
    _server_api_key = api_key


def configure_dashboard_vault(vault_path: str, vault_type: str = "local") -> None:
    """Configures the Provenance Log vault inspected by the Web Audit Dashboard."""
    global _dashboard_vault_path, _dashboard_vault_type
    _dashboard_vault_path = vault_path
    _dashboard_vault_type = vault_type


async def _verify_api_key(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> None:
    """Validates Bearer token or X-API-Key header when server authentication is enabled."""
    expected_key = _server_api_key or os.environ.get("CT_SERVER_API_KEY")
    if not expected_key:
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


def get_dashboard_telemetry(limit: int = 200) -> Dict[str, Any]:
    """Collects Provenance Chain integrity, L1/L2/L3 divergence metrics, and staged rules."""
    if _wrapper_instance is not None and getattr(_wrapper_instance, "_provenance_log", None) is not None:
        log = _wrapper_instance._provenance_log
        vault_path = getattr(_wrapper_instance._config, "vault_path", _dashboard_vault_path)
        vault_type = getattr(_wrapper_instance._config, "vault_type", _dashboard_vault_type)
        active_kernel = _wrapper_instance.kernel.name
        active_template = _wrapper_instance._config.template
    else:
        vault_path = _dashboard_vault_path
        vault_type = _dashboard_vault_type
        log = ProvenanceLog(vault_type=vault_type, vault_path=vault_path)
        active_kernel = "general"
        active_template = "general"

    chain_ok = True
    tampered_entry_id: Optional[str] = None
    verified_count = 0
    last_hash = ProvenanceLog.GENESIS_HASH

    try:
        checkpoint = log.get_chain_checkpoint(batch_size=500)
        verified_count = int(checkpoint["verified_count"])
        last_hash = str(checkpoint["last_content_hash"])
    except ChainIntegrityError as exc:
        chain_ok = False
        tampered_entry_id = getattr(exc, "entry_id", str(exc))

    entries = log.get_entries(limit=limit, include_rolled_back=False)
    scores = [e.divergence_score for e in entries if e.divergence_score is not None]
    avg_score = (sum(scores) / len(scores)) if scores else 0.0
    max_score = max(scores) if scores else 0.0

    tier_counts: Dict[str, int] = {
        "ok": 0,
        "l1_warning": 0,
        "l2_judge": 0,
        "l3_icm": 0,
    }
    promoted_ids: set[str] = set()
    staged_records: List[Dict[str, Any]] = []
    score_series: List[Dict[str, Any]] = []
    recent_entries: List[Dict[str, Any]] = []

    for entry in entries:
        meta = entry.metadata or {}
        event_type = meta.get("event_type")
        if event_type == "staged_promotion":
            eid = meta.get("endorsement_id")
            if eid:
                promoted_ids.add(str(eid))
            continue
        if event_type == "reflective_endorsement" and meta.get("decision") == "staged":
            staged_records.append(meta)
            continue

        raw_tier = str(meta.get("tier") or meta.get("divergence_tier") or "").lower()
        resolved_tier = raw_tier
        if raw_tier in tier_counts:
            tier_counts[raw_tier] += 1
        elif entry.divergence_score is not None:
            if entry.divergence_score >= 0.50:
                tier_counts["l3_icm"] += 1
                resolved_tier = "l3_icm"
            elif entry.divergence_score >= 0.30:
                tier_counts["l2_judge"] += 1
                resolved_tier = "l2_judge"
            elif entry.divergence_score >= 0.15:
                tier_counts["l1_warning"] += 1
                resolved_tier = "l1_warning"
            else:
                tier_counts["ok"] += 1
                resolved_tier = "ok"
        else:
            resolved_tier = "ok"

        if entry.divergence_score is not None:
            score_series.append(
                {
                    "id": entry.id[:8],
                    "timestamp": entry.timestamp,
                    "score": round(float(entry.divergence_score), 4),
                    "tier": resolved_tier,
                    "kernel": str(meta.get("kernel", active_kernel)),
                    "model": str(meta.get("model", "unknown")),
                }
            )

        if len(recent_entries) < 25:
            recent_entries.append(
                {
                    "id": entry.id,
                    "timestamp": entry.timestamp,
                    "divergence_score": entry.divergence_score,
                    "tier": resolved_tier,
                    "kernel": str(meta.get("kernel", active_kernel)),
                    "template": str(meta.get("template", active_template)),
                    "model": str(meta.get("model", "unknown")),
                    "prev_entry_hash": entry.prev_entry_hash,
                    "content_hash": entry.content_hash(),
                    "hmac_signature": entry.hmac_signature,
                }
            )

    # Reverse score_series so chart renders chronologically (oldest -> newest)
    score_series.reverse()

    now = time.time()
    staged_rules_out: List[Dict[str, Any]] = []
    for s_meta in staged_records:
        eid = str(s_meta.get("endorsement_id", "unknown"))
        commitment = str(s_meta.get("commitment_id", "unknown"))
        rule_text = str(s_meta.get("rule_text", ""))
        cooldown_until = s_meta.get("cooldown_until")
        if eid in promoted_ids:
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
        "vault_path": str(vault_path),
        "vault_type": str(vault_type),
        "active_kernel": active_kernel,
        "active_template": active_template,
        "chain_ok": chain_ok,
        "tampered_entry_id": tampered_entry_id,
        "verified_count": verified_count,
        "entries_analyzed": len(entries),
        "last_hash": last_hash,
        "avg_score": round(avg_score, 4),
        "max_score": round(max_score, 4),
        "tier_counts": tier_counts,
        "staged_rules": staged_rules_out,
        "score_series": score_series,
        "recent_entries": recent_entries,
        "timestamp": now,
    }


@app.get("/api/dashboard/status", dependencies=[Depends(_verify_api_key)])
async def dashboard_status_api(limit: int = Query(200, ge=1, le=2000)) -> Dict[str, Any]:
    """JSON Telemetry endpoint for the CT Toolkit Web Audit Dashboard."""
    try:
        return get_dashboard_telemetry(limit=limit)
    except Exception as exc:
        logger.error(f"Dashboard status API error: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to read provenance telemetry: {exc}")


@app.get("/", include_in_schema=False)
async def root_redirect() -> RedirectResponse:
    """Redirect root URL to the interactive Web Audit Dashboard."""
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_ui() -> HTMLResponse:
    """Serves the interactive CT Toolkit Web Audit & Telemetry Dashboard."""
    return HTMLResponse(content=_DASHBOARD_HTML)


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
      fetchTelemetry();
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

    async function fetchTelemetry() {
      const headers = {};
      const key = apiKeyInput.value.trim();
      if (key) {
        headers['Authorization'] = `Bearer ${key}`;
      }
      try {
        const res = await fetch('/api/dashboard/status?limit=200', { headers });
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

    fetchTelemetry();
    refreshTimer = setInterval(() => {
      if (autoRefresh) fetchTelemetry();
    }, 5000);
    window.addEventListener('resize', () => fetchTelemetry());
  </script>
</body>
</html>
"""


def start_server(
    wrapper: TheseusWrapper,
    host: str,
    port: int,
    api_key: Optional[str] = None,
):
    """Inject wrapper and start uvicorn server."""
    global _wrapper_instance
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
) -> None:
    """Starts the standalone Web Audit & Telemetry Dashboard server."""
    configure_dashboard_vault(vault_path=vault_path, vault_type=vault_type)
    if api_key is not None:
        configure_server_auth(api_key)
    logger.info(f"Starting CT Toolkit Web Audit Dashboard on http://{host}:{port}/dashboard")
    uvicorn.run(app, host=host, port=port)

