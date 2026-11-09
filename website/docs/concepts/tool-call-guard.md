# Tool-Call Guard

The **tool-call guard** reviews the tool calls a model response asks the harness to run, before the harness runs them. It is aimed at the case where an agent is steered, by a prompt injection or a bad plan, into calling a tool it was never given, sending data to a host the user never named, or reading credentials.

It works on the tool calls only, not on the text of the answer. By default it needs no second model: the checks are deterministic. An optional judge model can review the calls that look suspicious.

---

## The three checks

| Check | Severity | What it catches |
| :---- | :------- | :-------------- |
| `policy.undeclared_tool` | HIGH | The call names a tool that the request never declared in its `tools` list. |
| `exfiltration.unseen_host` | HIGH | The call's arguments contain a host (a URL host or an IPv4 literal) that appears nowhere in the conversation and is not on the allowlist. |
| Risky-action patterns | HIGH or MEDIUM | Known risky commands and paths, listed below. |

The risky-action patterns:

| Finding | Severity | Platform | Matches |
| :------ | :------- | :------- | :------ |
| `harness.proc_environ` | HIGH | Linux | Reads another process's environment (`/proc/<pid>/environ`). |
| `harness.k8s_service_account` | HIGH | any | Reads a Kubernetes service-account token. |
| `harness.namespace_escape` | MEDIUM | Linux | `unshare`, `nsenter`. |
| `harness.env_dump` | MEDIUM | any | Dumps the environment: `printenv`, `Get-ChildItem Env:`, `GetEnvironmentVariables(`. |
| `network.proxy_bypass` | MEDIUM | any | `curl --noproxy`. |
| `secrets.credential_file` | HIGH | Linux, macOS, Windows | Reads `.netrc`, `.git-credentials`, `.docker/config.json`, `.aws/credentials` or `.ssh` keys, under a home directory in any of its path forms. |
| `secrets.windows_credential_store` | HIGH | Windows | Reads the Windows credential store (`AppData\...\Microsoft\Credentials`, `Vault`, `Protect`, or `cmdkey /list`). |

Matching runs on the decoded string values of the JSON arguments, so escapes are read as the shell will see them. It also runs on a normalized form with quotes, backslashes and `$IFS` removed, so `cat "/proc/1/environ"` is caught.

The allowlist for the host check covers `localhost`, `127.0.0.1`, `github.com`, `githubusercontent.com`, `pypi.org`, `pythonhosted.org`, `npmjs.org` and `huggingface.co`. Add more with `--tool-guard-allow-host` on the CLI commands. In the library, `tool_guard_allowed_hosts` replaces the list, so pass the defaults too.

!!! warning "Platform scope"
    The `/proc`, `unshare` and `nsenter` patterns only apply on Linux. The Windows credential and environment forms are matched as strings; the test suite runs on Linux and does not exercise Windows shell behaviour.

---

## Modes

| Mode | Behaviour |
| :--- | :-------- |
| `observe` (default) | Records every finding. Calls are never removed. |
| `enforce` | A call with a HIGH finding is removed from the response. |

Set the mode with `--tool-guard-mode observe|enforce` on the CLI commands, with the `CT_TOOL_GUARD_MODE` environment variable, or with `WrapperConfig(tool_guard_mode=...)`.

How enforcement reaches the harness:

- **Non-streamed responses:** the harness gets a copy of the response without the blocked calls.
- **Streamed responses (proxy and library):** text is forwarded live. From the first tool-call event on, the events are held until the stream ends, then the blocked calls are dropped and the rest is sent. The stream still completes.

Each call gets one verdict:

| Verdict | Meaning |
| :------ | :------ |
| `passed` | No finding. |
| `flagged` | At least one finding, and the call was not removed (observe mode, or only MEDIUM/LOW findings). |
| `blocked` | Enforce mode and a HIGH finding: the call was removed. |

---

## Judge model (optional, off by default)

A judge model can review the calls that the scanner flagged with findings below HIGH. Calls with a HIGH finding are not sent to the judge, and clean calls are never sent.

- The judge answers allow or block with a short reason. It can **add** a block; it cannot lift a HIGH finding.
- Recommended model: `gemma-4-E4B-it` (`RECOMMENDED_JUDGE_MODEL`). Serve it from any OpenAI-compatible server (oMLX, Ollama `/v1`, vLLM, ...) and pass the name as `openai/gemma-4-E4B-it` together with the server's base URL. The name must match what the server serves.
- **Failure handling:** if the judge gives no usable answer, the call is not blocked by default (fail-open). The error is kept in the judge field of the audit record. With fail-closed on (`tool_guard_judge_fail_closed=True` in the library, or `--tool-guard-judge-fail-closed` for `serve` and `proxy`) the call gets a HIGH `judge.unavailable` finding and is blocked in enforce mode.

```bash
ct-toolkit proxy --upstream https://api.openai.com \
  --tool-guard-judge-model openai/gemma-4-E4B-it \
  --tool-guard-judge-api-base http://localhost:11434/v1
```

---

## Audit trail

Every decision is written to the provenance chain as a metadata event with `event="tool_call"`. The fields are `tool`, `verdict`, `mode`, `findings`, `judge`, `provider` and `llm_model`.

Only hashes of the call text are stored, as for every provenance entry. **The call's arguments are not in the audit trail.** Tool-call entries do not change the interaction counts used for scheduling.

The proxy writes them to the vault given with `--vault`. The library and the guardrail server write them through the wrapper, so they are written only when `log_requests` is enabled (the default).

Read them with `ct-toolkit tool-guard list`, `stats` and `show`, or in the **Tool calls** section of the web dashboard. See the [CLI reference](../reference/cli.md#tool-guard).

---

## Where each entry point fits

| Entry point | Use it when | Removes single calls? |
| :---------- | :---------- | :-------------------- |
| Library: `TheseusWrapper` / `AsyncTheseusWrapper` with `WrapperConfig` | You call the model from your own Python code. Covers `chat`, `achat`, and streaming. | Yes, in enforce mode. |
| Proxy: `ct-toolkit proxy` | Your agent harness already speaks the OpenAI Chat Completions, OpenAI Responses or Anthropic Messages API. Point its base URL at the proxy; nothing else changes. | Yes, in enforce mode. |
| Guardrail server: `ct-toolkit serve` (LiteLLM Generic Guardrail API) | You route traffic through a LiteLLM proxy and want its guardrail hook. | No. It can only block the whole response. |
| CLI: `ct-toolkit tool-guard check` | You have a saved response (for example in CI) and want to review its calls without a model. | Reports only. Exits 1 if any call has a HIGH finding. |

The guardrail server reviews tool calls on the response phase too. LiteLLM applies only the `BLOCKED` action of a guardrail, so when a response contains a risky call the whole response is blocked. For removing single calls, use the proxy or the library.

The proxy binds to loopback only and has no authentication. Do not expose it on a network interface.

---

## Known limits

- **Regex checks can be evaded.** A determined command can avoid the patterns. That is why the judge and the host check are separate layers, and why the audit trail records each decision.
- **The host check looks at what the conversation mentions.** A host that appears anywhere the request shows the model (system prompt, user turns, tool results), other than in the model's own earlier turns, passes the check, even if it is not what the user meant.
- **The library stream review reads OpenAI-style tool-call deltas.** The Responses API is covered by the proxy only, not by the library.
- **The guardrail server cannot remove single calls.** It blocks the whole response.
- **Linux is the tested platform.** Windows patterns are matched on strings and Windows shell behaviour is not exercised by the tests.
