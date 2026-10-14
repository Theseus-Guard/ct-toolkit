"""
ct_toolkit.middleware.langchain
-------------------------------
LangChain integrations: Callback handlers and ChatModel wrapper.

Provides two integration points:

1. ``TheseusLangChainCallback`` — a drop-in LangChain callback handler that
   validates prompts/messages against the Constitutional Kernel and records
   every response in the Provenance Log.

2. ``TheseusChatModel`` — a first-class ``BaseChatModel`` implementation
   that routes every call through ``TheseusWrapper``, giving identity-
   continuity guarantees even without callbacks.

Compatible with langchain-core >= 1.2.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from pydantic import Field

from ct_toolkit.core.wrapper import TheseusWrapper, WrapperConfig
from ct_toolkit.utils.logger import get_logger

logger = get_logger(__name__)


# ── Callback Handler ──────────────────────────────────────────────────────────


class TheseusLangChainCallback(BaseCallbackHandler):
    """
    LangChain callback handler that enforces identity-continuity guardrails.

    Attaches to any LangChain LLM or ChatModel via the ``callbacks`` argument.
    Validates every prompt/message against the Constitutional Kernel **before**
    the call, and runs the Divergence Engine + Provenance Log **after** the call.

    Usage::

        from ct_toolkit import TheseusWrapper
        from ct_toolkit.middleware.langchain import TheseusLangChainCallback
        from langchain_openai import ChatOpenAI

        wrapper = TheseusWrapper(provider="openai")
        cb = TheseusLangChainCallback(wrapper)

        llm = ChatOpenAI(model="gpt-4o-mini", callbacks=[cb])
        response = llm.invoke("Hello!")
    """

    def __init__(self, wrapper: TheseusWrapper) -> None:
        self.wrapper = wrapper
        # Per-run context storage: maps run_id → original prompt text
        # so on_llm_end can pass the right context to the divergence engine.
        self._run_prompts: Dict[UUID, str] = {}

    # ── Input validation ────────────────────────────────────────────────────

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Validate raw text prompts against the kernel before the LLM call."""
        self._run_prompts[run_id] = "\n".join(prompts)
        for prompt in prompts:
            self.wrapper.validate_user_rule(prompt)

    def on_chat_model_start(
        self,
        serialized: Dict[str, Any],
        messages: List[List[BaseMessage]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Validate chat messages against the kernel before the ChatModel call."""
        user_texts: List[str] = []
        for message_list in messages:
            for message in message_list:
                if isinstance(message.content, str):
                    self.wrapper.validate_user_rule(message.content)
                    user_texts.append(message.content)
        self._run_prompts[run_id] = " ".join(user_texts)

    # ── Output processing ──────────────────────────────────────────────────

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Run divergence analysis on every generated output and record provenance."""
        original_prompt = self._run_prompts.pop(run_id, "")

        for generations in response.generations:
            for generation in generations:
                # ChatGeneration stores content in .message.content;
                # plain Generation stores it in .text
                if hasattr(generation, "message"):
                    content: str = generation.message.content or ""  # type: ignore[union-attr]
                else:
                    content = generation.text  # type: ignore[assignment]

                if not content:
                    continue

                div_result = self.wrapper._run_divergence_engine(
                    message=original_prompt,
                    response=content,
                )

                if div_result.cascade_blocked:
                    logger.warning(
                        "CT Toolkit | cascade_blocked=True detected via LangChain callback. "
                        "Consider halting downstream sub-agents to prevent SSC propagation."
                    )

                if self.wrapper._config.log_requests:
                    self.wrapper._provenance_log.record(
                        request_text=original_prompt or "[LangChain Interaction]",
                        response_text=content,
                        divergence_score=div_result.l1_score,
                        metadata={
                            "middleware": "langchain",
                            "tier": div_result.tier.value,
                            "cascade_blocked": div_result.cascade_blocked,
                            "template": self.wrapper._config.template,
                            "kernel": self.wrapper.kernel.name,
                        },
                    )

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Log LLM-level errors and clean up run context."""
        self._run_prompts.pop(run_id, None)
        logger.error(f"LangChain LLM error (run_id={run_id}): {error}")


# ── Chat Model Wrapper ────────────────────────────────────────────────────────


class TheseusChatModel(BaseChatModel):
    """
    A first-class LangChain ``BaseChatModel`` that routes calls through
    ``TheseusWrapper``. Kernel injection, divergence analysis, and provenance
    logging all happen transparently — no additional callbacks needed.

    Usage::

        from ct_toolkit.middleware.langchain import TheseusChatModel

        llm = TheseusChatModel(provider="openai", model="gpt-4o-mini")
        response = llm.invoke("What is identity continuity?")
        print(response.content)

    Chain usage::

        from langchain_core.prompts import ChatPromptTemplate
        prompt = ChatPromptTemplate.from_messages([("human", "{question}")])
        chain = prompt | TheseusChatModel(provider="openai")
        chain.invoke({"question": "Tell me about AI safety."})
    """

    # Pydantic model fields (BaseChatModel is Pydantic-based in langchain-core v1.2)
    wrapper: Any = None
    model: str = "gpt-4o-mini"
    bound_tools: List[Dict[str, Any]] = Field(default_factory=list)
    bound_tool_choice: Any = None
    allow_tools: bool = True

    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        *,
        provider: str = "openai",
        model: str = "gpt-4o-mini",
        wrapper: Optional[TheseusWrapper] = None,
        wrapper_config: Optional[WrapperConfig] = None,
        **kwargs: Any,
    ) -> None:
        _wrapper = wrapper or TheseusWrapper(
            provider=provider,
            config=wrapper_config,
        )
        super().__init__(**kwargs)
        self.wrapper = _wrapper
        self.model = model

    @property
    def compression_guard(self) -> Any:
        """Access the underlying ContextCompressionGuard."""
        return self.wrapper._compression_guard

    @property
    def policy_metadata(self) -> Dict[str, Any]:
        """Expose resolved CT policy metadata for external middleware consumers."""
        return self.wrapper.propagate_policy_metadata(
            model=self.model,
            role=self.wrapper._config.policy_role,
        )

    @property
    def _llm_type(self) -> str:
        return "theseus-chat-model"

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_user_text(messages: List[BaseMessage]) -> str:
        """Concatenate non-system message content into a single user string."""
        return " ".join(
            msg.content
            for msg in messages
            if not isinstance(msg, SystemMessage) and isinstance(msg.content, str)
        )

    @staticmethod
    def _extract_system(messages: List[BaseMessage]) -> Optional[str]:
        for msg in messages:
            if isinstance(msg, SystemMessage) and isinstance(msg.content, str):
                return msg.content
        return None

    @staticmethod
    def _format_tool(tool: Any) -> Dict[str, Any]:
        """Normalize a tool into OpenAI-compatible tool schema."""
        try:
            from langchain_core.utils.function_calling import convert_to_openai_tool

            return convert_to_openai_tool(tool)
        except Exception:
            if isinstance(tool, dict):
                return tool
            if hasattr(tool, "name"):
                return {
                    "type": "function",
                    "function": {
                        "name": str(getattr(tool, "name")),
                        "description": str(getattr(tool, "description", "")),
                        "parameters": getattr(tool, "args_schema", {}) or {},
                    },
                }
            raise TypeError("Unsupported tool format for TheseusChatModel.bind_tools")

    @staticmethod
    def _extract_tool_calls(raw_response: Any) -> List[Dict[str, Any]]:
        """Extract tool calls from LiteLLM/OpenAI-like responses into LangChain format."""
        tool_calls: Any = None

        if isinstance(raw_response, dict):
            choices = raw_response.get("choices") or []
            if choices:
                tool_calls = (choices[0].get("message") or {}).get("tool_calls")
        elif hasattr(raw_response, "choices") and raw_response.choices:
            message = getattr(raw_response.choices[0], "message", None)
            if message is not None:
                tool_calls = getattr(message, "tool_calls", None)

        if not tool_calls:
            return []

        parsed_calls: List[Dict[str, Any]] = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                raw_args = fn.get("arguments", "{}")
                call_id = tc.get("id")
                name = fn.get("name", "")
            else:
                fn = getattr(tc, "function", None)
                raw_args = getattr(fn, "arguments", "{}") if fn is not None else "{}"
                call_id = getattr(tc, "id", None)
                name = getattr(fn, "name", "") if fn is not None else ""

            args: Dict[str, Any]
            if isinstance(raw_args, str):
                try:
                    loaded = json.loads(raw_args) if raw_args else {}
                    args = loaded if isinstance(loaded, dict) else {"_raw": raw_args}
                except json.JSONDecodeError:
                    args = {"_raw": raw_args}
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {"_raw": str(raw_args)}

            parsed_calls.append(
                {
                    "id": call_id or f"tool_call_{len(parsed_calls) + 1}",
                    "name": name,
                    "args": args,
                    "type": "tool_call",
                }
            )

        return parsed_calls

    def bind_tools(
        self,
        tools: List[Any],
        *,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> "TheseusChatModel":
        """
        Bind tools to the model for agent/tool-calling workflows.

        This keeps tool support in the model layer while preserving CT guardrails.
        """
        _ = kwargs
        formatted_tools = [self._format_tool(tool) for tool in tools]
        return self.model_copy(
            update={
                "bound_tools": formatted_tools,
                "bound_tool_choice": tool_choice,
            }
        )

    # ── core generation ────────────────────────────────────────────────────

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        _ = run_manager
        user_text = self._extract_user_text(messages)
        system_prompt = self._extract_system(messages)

        call_kwargs: Dict[str, Any] = dict(kwargs)
        if stop:
            call_kwargs["stop"] = stop

        resolved_policy = self.wrapper.resolve_llm_policy(model=self.model)
        tool_call_allowed = bool(resolved_policy["effective"].get("tool_call", False)) and self.allow_tools

        if tool_call_allowed:
            if self.bound_tools:
                call_kwargs["tools"] = self.bound_tools
            if self.bound_tool_choice is not None:
                call_kwargs["tool_choice"] = self.bound_tool_choice
        else:
            if not self.allow_tools:
                provider = getattr(self.wrapper, "_provider", "unknown")
                if provider != "ollama":
                    call_kwargs.update(
                        {
                            "tools": [],
                            "tool_choice": "none",
                            "parallel_tool_calls": False,
                        }
                    )
            else:
                call_kwargs.update(self.wrapper.resolve_tool_control_kwargs(model=self.model))

        ct_response = self.wrapper.chat(
            user_text,
            model=self.model,
            system=system_prompt,
            **call_kwargs,
        )

        tool_calls = self._extract_tool_calls(ct_response.raw_response)

        ai_message = AIMessage(content=ct_response.content or "", tool_calls=tool_calls)
        generation = ChatGeneration(
            message=ai_message,
            generation_info={
                "divergence_score": ct_response.divergence_score,
                "divergence_tier": ct_response.divergence_tier,
                "provenance_id": ct_response.provenance_id,
                "ct_policy": self.policy_metadata,
                "tool_calls": tool_calls,
            },
        )
        return ChatResult(generations=[generation])

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        _ = run_manager
        user_text = self._extract_user_text(messages)
        system_prompt = self._extract_system(messages)

        call_kwargs: Dict[str, Any] = dict(kwargs)
        if stop:
            call_kwargs["stop"] = stop

        resolved_policy = self.wrapper.resolve_llm_policy(model=self.model)
        tool_call_allowed = bool(resolved_policy["effective"].get("tool_call", False)) and self.allow_tools

        if tool_call_allowed:
            if self.bound_tools:
                call_kwargs["tools"] = self.bound_tools
            if self.bound_tool_choice is not None:
                call_kwargs["tool_choice"] = self.bound_tool_choice
        else:
            if not self.allow_tools:
                provider = getattr(self.wrapper, "_provider", "unknown")
                if provider != "ollama":
                    call_kwargs.update(
                        {
                            "tools": [],
                            "tool_choice": "none",
                            "parallel_tool_calls": False,
                        }
                    )
            else:
                call_kwargs.update(self.wrapper.resolve_tool_control_kwargs(model=self.model))

        ct_response = await self.wrapper.achat(
            user_text,
            model=self.model,
            system=system_prompt,
            **call_kwargs,
        )

        tool_calls = self._extract_tool_calls(ct_response.raw_response)

        ai_message = AIMessage(content=ct_response.content or "", tool_calls=tool_calls)
        generation = ChatGeneration(
            message=ai_message,
            generation_info={
                "divergence_score": ct_response.divergence_score,
                "divergence_tier": ct_response.divergence_tier,
                "provenance_id": ct_response.provenance_id,
                "ct_policy": self.policy_metadata,
                "tool_calls": tool_calls,
            },
        )
        return ChatResult(generations=[generation])
