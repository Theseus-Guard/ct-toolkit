#!/usr/bin/env bash
# Starts the chain  OpenCode -> CT-Toolkit proxy (8788) -> LiteLLM (4000) -> AWS Bedrock,
# and the CT-Toolkit dashboard (8001) reading the same provenance vault.
#
#   CT_MODE=observe|enforce   tool-call guard mode (default observe)
#   CT_VAULT=/path/vault.db   provenance vault (default: ./ct_provenance.db next to this script)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VAULT="${CT_VAULT:-$HERE/ct_provenance.db}"
MODE="${CT_MODE:-observe}"
LOGS="$HERE/logs"
mkdir -p "$LOGS"

litellm --config "$HERE/litellm_config.yaml" --port 4000 > "$LOGS/litellm.log" 2>&1 &
ct-toolkit proxy --upstream http://127.0.0.1:4000/v1 --port 8788 --vault "$VAULT" \
  --tool-guard-mode "$MODE" > "$LOGS/proxy.log" 2>&1 &
ct-toolkit dashboard --web --vault "$VAULT" --port 8001 \
  --tool-guard-settings "$HERE/tool_guard_settings.json" > "$LOGS/dashboard.log" 2>&1 &

echo "LiteLLM      http://127.0.0.1:4000/v1"
echo "CT proxy     http://127.0.0.1:8788/v1   (mode: $MODE, vault: $VAULT)"
echo "Dashboard    http://127.0.0.1:8001/dashboard"
echo "Logs in $LOGS. Stop with: pkill -f 'litellm --config|ct-toolkit (proxy|dashboard)'"
