# OpenCode with AWS Bedrock

This guide puts CT-Toolkit between [OpenCode](https://opencode.ai) and your Bedrock models. Every tool call the model asks OpenCode to run is reviewed by the tool-call guard and recorded in the provenance chain, and the records appear in the web dashboard.

```
OpenCode  ──►  CT-Toolkit proxy :8788  ──►  LiteLLM :4000  ──►  AWS Bedrock
                 (review, record)          (OpenAI-compatible)
                        │
                        └──► provenance vault ◄── dashboard :8001
```

OpenCode's built-in Bedrock provider talks the Bedrock Converse protocol directly. The CT-Toolkit proxy reads OpenAI Chat Completions and Anthropic Messages, so Bedrock is reached through LiteLLM, which exposes it as an OpenAI-compatible API. Nothing in OpenCode's code changes; only its configuration does.

## 1. Sign in to AWS

Your AWS session must be valid for LiteLLM to call Bedrock:

```bash
aws login --profile theseus-guard
```

## 2. Start the chain

The example files are in `examples/opencode-bedrock/`:

```bash
cd examples/opencode-bedrock
CT_MODE=observe ./start.sh
```

This starts three processes and prints their addresses:

| Process | Address | Role |
| :--- | :--- | :--- |
| LiteLLM | `http://127.0.0.1:4000/v1` | Bedrock as an OpenAI-compatible API (`litellm_config.yaml`) |
| CT-Toolkit proxy | `http://127.0.0.1:8788/v1` | Reviews and records tool calls (`--tool-guard-mode`) |
| Dashboard | `http://127.0.0.1:8001/dashboard` | Shows the records from the same vault |

Logs go to `examples/opencode-bedrock/logs/`.

## 3. Point OpenCode at the proxy

Add the provider from `examples/opencode-bedrock/opencode.json` to your OpenCode configuration (`~/.config/opencode/opencode.json`) and select its model:

```json
"provider": {
  "ct-bedrock": {
    "npm": "@ai-sdk/openai-compatible",
    "options": { "baseURL": "http://127.0.0.1:8788/v1", "apiKey": "unused" },
    "models": { "global.openai.gpt-6-luna": { "name": "GPT-6 Luna (Bedrock, guarded)", "tool_call": true } }
  }
}
```

and `"model": "ct-bedrock/global.openai.gpt-6-luna"`. Use the same Bedrock model id in `litellm_config.yaml`.

## 4. See the records

Work in OpenCode as usual, then open the dashboard. The **Tool calls** section lists every tool call the model asked for, with its verdict (`passed`, `flagged`, `blocked`), the findings and the model. Filter by verdict or tool, and sort by any column.

The **Tool-Call Guard Settings** panel changes the mode without a restart: the proxy reads the settings file on every request. Enforce mode removes high-severity calls before OpenCode runs them.

## What is recorded, and what is not

- **Recorded:** each tool-call decision, with the tool name, verdict, mode, findings and model. The call's arguments are not stored; the chain keeps only their hash.
- **Not recorded yet:** plain model turns (prompt and answer without tool calls) and identity drift. Drift is measured by the library's `TheseusWrapper`, which the proxy does not wrap. Recording model turns through the proxy is a possible next step.

## Verification status

- The proxy, the dashboard and the shared vault were tested end to end with a stand-in OpenAI-compatible upstream that streams tool calls, including a risky one. The risky call was flagged and recorded, and it appeared in the dashboard API.
- The Bedrock leg (LiteLLM with your AWS profile) has not been run in this repository, because it needs a valid AWS session. Run `start.sh` once and check the LiteLLM log for the first request.

## Enforce mode with OpenCode

In enforce mode the blocked call is removed from the stream: the text around it is still sent, and OpenCode never receives the call, so it cannot run it. Start with observe mode until you have looked at the records for a day or two.
