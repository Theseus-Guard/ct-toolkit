#!/usr/bin/env bash
# Starts the chain  OpenCode -> CT-Toolkit proxy (8788) -> LiteLLM (4000) -> AWS Bedrock,
# and the CT-Toolkit dashboard (8001) reading the same provenance vault.
#
#   CT_MODE=observe|enforce   tool-call guard mode (default observe)
#   CT_VAULT=/path/vault.db   provenance vault (default: ./ct_provenance.db next to this script)
#   CT_CMD="..."              how to run CT-Toolkit (default: ct-toolkit on PATH, else the repo's .venv, else python -m ct_toolkit)
#   LITELLM_CMD="..."         how to run LiteLLM (default: litellm)
#   CT_AWS_PROFILE=name       the AWS profile you signed in with (aws login --profile <name>); required
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VAULT="${CT_VAULT:-$HERE/ct_provenance.db}"
MODE="${CT_MODE:-observe}"
LOGS="$HERE/logs"
mkdir -p "$LOGS"

if [ -z "${CT_CMD:-}" ]; then
  if command -v ct-toolkit >/dev/null 2>&1; then CT_CMD="ct-toolkit"
  elif [ -x "$HERE/../../.venv/bin/ct-toolkit" ]; then CT_CMD="$HERE/../../.venv/bin/ct-toolkit"
  else CT_CMD="python3 -m ct_toolkit"; fi
fi
LITELLM_CMD="${LITELLM_CMD:-litellm}"

# LiteLLM refuses to start without a master key. Create a local one on first run; OpenCode sends the same key.
KEY_FILE="$HERE/.litellm_master_key"
if [ ! -s "$KEY_FILE" ]; then
  (umask 077; echo "sk-ct-$(openssl rand -hex 16)" > "$KEY_FILE")
fi
export LITELLM_MASTER_KEY="$(cat "$KEY_FILE")"

# Bedrock credentials come from the AWS profile that `aws login` signed in with.
if [ -z "${CT_AWS_PROFILE:-}" ]; then
  echo "set CT_AWS_PROFILE to the AWS profile you signed in with, e.g. CT_AWS_PROFILE=<profile> ./start.sh" >&2
  exit 1
fi
export AWS_PROFILE="$CT_AWS_PROFILE"

# Refuse to start on a port that is already taken: that is the usual reason OpenCode gets ECONNREFUSED or stale answers.
for port in 4000 8788 8001; do
  if lsof -iTCP:"$port" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
    echo "port $port is already in use; stop that process first (lsof -iTCP:$port -sTCP:LISTEN)" >&2
    exit 1
  fi
done

$LITELLM_CMD --config "$HERE/litellm_config.yaml" --host 127.0.0.1 --port 4000 > "$LOGS/litellm.log" 2>&1 &
$CT_CMD proxy --upstream http://127.0.0.1:4000 --port 8788 --vault "$VAULT" \
  --tool-guard-mode "$MODE" --tool-guard-settings "$HERE/tool_guard_settings.json" > "$LOGS/proxy.log" 2>&1 &
$CT_CMD dashboard --web --vault "$VAULT" --port 8001 \
  --tool-guard-settings "$HERE/tool_guard_settings.json" > "$LOGS/dashboard.log" 2>&1 &

sleep 3
echo "LiteLLM      http://127.0.0.1:4000/v1"
echo "CT proxy     http://127.0.0.1:8788/v1   (mode: $MODE, vault: $VAULT)"
echo "Dashboard    http://127.0.0.1:8001/dashboard"
echo "OpenCode apiKey: the value in $KEY_FILE (the same key LiteLLM was started with)"
echo "Logs in $LOGS. If a process failed, read its log there."
