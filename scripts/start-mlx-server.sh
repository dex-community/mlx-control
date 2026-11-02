#!/bin/bash
# Wrapper del server MLX: legge i parametri da model-settings.json e avvia mlx_lm.server.
# I parametri sono modificabili dalla Menu Bar Extra "LFM3 Control".
set -euo pipefail

HOME_DIR="${HOME:-$(echo ~)}"
BASE="${LFM3_INSTALL_DIR:-${HOME_DIR}/.minimax/local-models}"
CONTROL_DIR="${MLX_CONTROL_DIR:-${HOME_DIR}/.mlxControl}"
SETTINGS="$BASE/model-settings.json"
MODEL_FILE="$BASE/current-model.txt"

# Modello attivo: current-model.txt (impostato dalla Menu Bar Extra) o il default.
if [ -f "$MODEL_FILE" ]; then
  MODEL="$(cat "$MODEL_FILE")"
else
  MODEL="${LFM3_MODEL_PATH:-${CONTROL_DIR}/models/LiquidAI/LFM3-1.7B-MLX-4bit}"
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

# --- Budget di memoria -------------------------------------------------
# Il server MLX gira sulla memoria unificata: pesi + cache KV + cache dei
# prompt condividono la RAM con il resto del sistema. I valori qui sotto
# limitano quanto resta "appiccicato" al processo dopo una compattazione.
#
# prompt_cache_bytes : tetto massimo alle cache KV conservate tra le richieste.
#                      3 GB (vecchio default) -> 512 MB: dopo la compattazione
#                      il server non trattiene piu' il contesto completo.
# prompt_cache_size  : quante cache di prompt tenere (8 -> 2).
#
# kv_bits            : quantizzazione della KV cache (4 o 8 bit). DISATTIVATO di
#                      default: i modelli ibridi come LFM3 usano un
#                      RotatingKVCache, per cui la quantizzazione non e'
#                      implementata e la generazione va in crash
#                      ("RotatingKVCache Quantization NYI"). Si lascia impostabile
#                      per modelli con cache standard.
PROMPT_CACHE_SIZE="$(read_setting prompt_cache_size)"
PROMPT_CACHE_BYTES="$(read_setting prompt_cache_bytes)"
KV_BITS="$(read_setting kv_bits)"
KV_GROUP_SIZE="$(read_setting kv_group_size)"
KV_START="$(read_setting quantized_kv_start)"

export MLX_LM_MODEL_ALIASES="${MLX_LM_MODEL_ALIASES:-lfm3-1.7b-mlx}"

# Alias aggiuntivi: "nome=percorso" espone un secondo modello (es. quello veloce
# usato per il checkpoint durante la compattazione).
FAST_PATH="$(read_setting fast_model_path)"
if [ -z "$FAST_PATH" ] && [ -d "$CONTROL_DIR/models/mlx-community/Qwen2.5-0.5B-Instruct-4bit" ]; then
  FAST_PATH="$CONTROL_DIR/models/mlx-community/Qwen2.5-0.5B-Instruct-4bit"
fi
if [ -n "$FAST_PATH" ] && [ -d "$FAST_PATH" ]; then
  export MLX_LM_MODEL_ALIASES="${MLX_LM_MODEL_ALIASES},fast-summary=${FAST_PATH}"
fi

export MLX_LM_DEFAULT_TEMPERATURE="${TEMP:-0.1}"
export MLX_LM_DEFAULT_TOP_K="${TOP_K:-50}"
export MLX_LM_DEFAULT_MIN_P="${MIN_P:-0.0}"
export MLX_LM_DEFAULT_REPETITION_PENALTY="${REP_PENALTY:-1.1}"

# Hint di sistema: tiene i modelli piccoli ancorati alla working directory reale.
# I modelli 1.7B tendono a copiare nel contesto il percorso dell'allegato
# (…/.minimax/v2/assets/…) e a usarlo come directory di lavoro.
export MLX_SYSTEM_HINT="${MLX_SYSTEM_HINT:-Working directory: usa SEMPRE percorsi relativi alla directory di lavoro corrente della sessione. Non usare mai come directory di lavoro i percorsi degli asset allegati (${HOME_DIR}/.minimax/v2/assets/...), delle cache o di /tmp: sono file allegati, non il progetto. Se un comando fallisce, correggi il percorso invece di ripeterelo.}"

if [ "$THINKING" = "True" ] || [ "$THINKING" = "true" ]; then
  THINK_ARGS='{"enable_thinking":true}'
else
  THINK_ARGS='{"enable_thinking":false}'
fi

# Argomenti di memoria (opzionali: se vuoi tornare ai valori precedenti basta
# mettere "null" nel model-settings.json o rimuovere le chiavi).
MEM_ARGS=()
case "${PROMPT_CACHE_SIZE:-2}" in
  null|"") ;;
  *) MEM_ARGS+=(--prompt-cache-size "$PROMPT_CACHE_SIZE") ;;
esac
case "${PROMPT_CACHE_BYTES:-536870912}" in
  null|"") ;;
  *) MEM_ARGS+=(--prompt-cache-bytes "$PROMPT_CACHE_BYTES") ;;
esac
case "${KV_BITS:-null}" in
  null|"") ;;
  *) MEM_ARGS+=(--kv-bits "$KV_BITS"
                --kv-group-size "${KV_GROUP_SIZE:-64}"
                --quantized-kv-start "${KV_START:-8192}") ;;
esac

exec "$SERVER" \
  --model "$MODEL" \
  --host 127.0.0.1 \
  --port 8080 \
  --trust-remote-code \
  --chat-template-args "$THINK_ARGS" \
  --max-tokens "${MAX_TOKENS:-8192}" \
  ${MEM_ARGS[@]+"${MEM_ARGS[@]}"} \
  --log-level INFO
