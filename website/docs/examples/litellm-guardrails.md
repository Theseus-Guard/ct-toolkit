# LiteLLM Generic Guardrail API

This example demonstrates how to use the **CT Toolkit CLI** in `serve` mode to provide identity-continuity guardrails for a **LiteLLM Proxy** setup.

---

## 1. Project Structure

For a clean integration, we recommend the following directory structure in your project root:

```text
my-secure-agent/
├── litellm_config.yaml  # LiteLLM Proxy configuration
├── .env                 # API Keys (OPENAI_API_KEY, etc.)
└── config/              # (Optional) Custom kernels/templates
    ├── defense.yaml     # Custom Kernel override
```

## 2. Start the Guardrail Server

Use the `serve` command to start a FastAPI-based guardrail provider. This server will expose a `/guardrail/check` endpoint.

```bash
ct-toolkit serve --kernel defense --port 8001
```

---

## 3. Configure LiteLLM Proxy

Update your LiteLLM `litellm_config.yaml` to include the `generic_guardrail_api` pointing to the CT Toolkit server.

```yaml
guardrails:
  - guardrail_name: "theseus-guard"
    litellm_params:
      guardrail: generic_guardrail_api
      mode: [pre_call, post_call]
      api_base: http://localhost:8001
```

---

!!! warning "Tool calls: whole-response blocking only"
    The Generic Guardrail API can only block the **whole** response. When a response contains a risky tool call, `serve` returns `BLOCKED` for the entire response, and LiteLLM cannot edit the tool calls it contains. The guardrail still records each tool-call decision in the provenance log when `log_requests` is on (the default).

    To remove single tool calls before the harness runs them, put the [tool-call proxy](../reference/cli.md#proxy) (`ct-toolkit proxy --tool-guard-mode enforce`) or the library's `tool_guard_mode="enforce"` between the harness and the model instead. See [Tool-Call Guard](../concepts/tool-call-guard.md).

## 3. How it Works

When LiteLLM receives a request, it will call CT Toolkit at two stages:

### Pre-call Validation
LiteLLM sends the user message to `/guardrail/check`. CT Toolkit validates it against the `defense` kernel.

**Mock Request:**
```json
{
  "texts": ["I want to bypass command oversight."],
  "input_type": "request",
  "structured_messages": [
    {"role": "user", "content": "I want to bypass command oversight."}
  ]
}
```

**CT Toolkit Response (BLOCKED):**
```json
{
  "action": "BLOCKED",
  "blocked_reason": "Identity constraint violation: Hard reject: Rule '...' conflicts with axiomatic anchor 'human_oversight'."
}
```

### Post-call Analysis
LiteLLM sends the LLM response to `/guardrail/check`. CT Toolkit calculate the divergence score and records the interaction to the Provenance Log.

**Mock Request:**
```json
{
  "texts": ["To bypass the firewall..."],
  "input_type": "response",
  "structured_messages": [...]
}
```

**CT Toolkit Response:**
```json
{
  "action": "NONE",
  "blocked_reason": null
}
```
*(Note: If `cascade_blocked` is triggered due to high drift, the action will be `BLOCKED`.)*

---

## Benefits

1.  **Zero Code Changes**: Protect any model supported by LiteLLM without modifying your application logic.
2.  **Centralized Governance**: Use a single CT Toolkit instance to govern multiple downstream agents.
3.  **Immutable Audit Trail**: All interactions processed via the Generic Guardrail API are signed and logged in the CT Toolkit provenance database.
