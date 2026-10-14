"""
ct_toolkit.server
-----------------
FastAPI server for LiteLLM Generic Guardrail API.
Exposes /guardrail/check to LiteLLM for pre-call and post-call validation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

from ct_toolkit.core.wrapper import TheseusWrapper
from ct_toolkit.utils.logger import get_logger
from ct_toolkit.core.exceptions import (
    ConfigurationTamperingError,
    ChainIntegrityError,
    CriticalSandboxDivergenceError,
)

logger = get_logger(__name__)

# This will be injected by the CLI when starting the server
_wrapper_instance: Optional[TheseusWrapper] = None

app = FastAPI(
    title="CT-Toolkit Guardrail Server",
    description="Identity Continuity Guardrails for Agentic Systems via LiteLLM Generic Guardrail API.",
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

@app.post("/guardrail/check", response_model=GuardrailResponse)
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

def start_server(wrapper: TheseusWrapper, host: str, port: int):
    """Inject wrapper and start uvicorn server."""
    global _wrapper_instance
    _wrapper_instance = wrapper
    logger.info(f"Starting Guardrail Server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
