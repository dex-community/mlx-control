#!/bin/bash
# Wrapper del server MLX: legge i parametri da model-settings.json e avvia mlx_lm.server.
# I parametri sono modificabili dalla Menu Bar Extra "LFM3 Control".
set -euo pipefail

BASE="${LFM3_INSTALL_DIR:-/Users/io/.minimax/local-models}"
SETTINGS="$BASE/model-settings.json"
MODEL_FILE="$BASE/current-model.txt"

# Modello attivo: current-model.txt (impostato dalla Menu Bar Extra) o il default.
if [ -f "$MODEL_FILE" ]; then
  MODEL="$(cat "$MODEL_FILE")"
else
  MODEL="${LFM3_MODEL_PATH:-$HOME/.lmstudio/models/LiquidAI/LFM3-1.7B-MLX-4bit}"
fi
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

# Hint di sistema: tiene i modelli piccoli ancorati alla working directory reale.
# I modelli 1.7B tendono a copiare nel contesto il percorso dell'allegato
# (…/.minimax/v2/assets/…) e a usarlo come directory di lavoro.
export MLX_SYSTEM_HINT="${MLX_SYSTEM_HINT:-Working directory: usa SEMPRE percorsi relativi alla directory di lavoro corrente della sessione. Non usare mai come directory di lavoro i percorsi degli asset allegati (/Users/io/.minimax/v2/assets/...), delle cache o di /tmp: sono file allegati, non il progetto. Se un comando fallisce, correggi il percorso invece di ripeterelo.}"

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
