#!/bin/bash
# Wrapper del server MLX: legge i parametri da model-settings.json e avvia mlx_lm.server.
# I parametri sono modificabili dalla Menu Bar Extra "LFM3 Control".
set -euo pipefail

BASE="${LFM3_INSTALL_DIR:-/Users/io/.minimax/local-models}"
SETTINGS="$BASE/model-settings.json"
MODEL="${LFM3_MODEL_PATH:-/Users/io/.lmstudio/models/LiquidAI/LFM3-1.7B-MLX-4bit}"
SERVER="$BASE/mlxenv/bin/mlx_lm.server"

# --- lettura parametri (python3 di sistema, nessuna dipendenza esterna) ---
read_setting() {
  /usr/bin/python3 - "$SETTINGS" "$1" <<'PY'
import json, sys
path, key = sys.argv[1], sys.argv[2]
try:
    with open(path) as f:
        print(json.load(f).get(key, ""))
except Exception:
    print("")
PY
}

TEMP="$(read_setting temperature)"
TOP_K="$(read_setting top_k)"
MIN_P="$(read_setting min_p)"
REP_PENALTY="$(read_setting repetition_penalty)"
THINKING="$(read_setting enable_thinking)"
MAX_TOKENS="$(read_setting max_tokens)"

export MLX_LM_MODEL_ALIASES="${MLX_LM_MODEL_ALIASES:-lfm3-1.7b-mlx}"
export MLX_LM_DEFAULT_TEMPERATURE="${TEMP:-0.1}"
export MLX_LM_DEFAULT_TOP_K="${TOP_K:-50}"
export MLX_LM_DEFAULT_MIN_P="${MIN_P:-0.0}"
export MLX_LM_DEFAULT_REPETITION_PENALTY="${REP_PENALTY:-1.1}"

if [ "$THINKING" = "True" ] || [ "$THINKING" = "true" ]; then
  THINK_ARGS='{"enable_thinking":true}'
else
  THINK_ARGS='{"enable_thinking":false}'
fi

exec "$SERVER" \
  --model "$MODEL" \
  --host 127.0.0.1 \
  --port 8080 \
  --trust-remote-code \
  --chat-template-args "$THINK_ARGS" \
  --max-tokens "${MAX_TOKENS:-8192}" \
  --prompt-cache-size 8 \
  --prompt-cache-bytes 3000000000 \
  --log-level INFO
