#!/bin/bash
# Installer automatico di MLX Control per MiniMax Code.
#
#   ./install.sh                 -> installazione completa (modello + Rizzo Flow)
#   ./install.sh --no-rizzo      -> senza Rizzo Flow (compattazione nativa)
#   ./install.sh --no-model      -> non scarica il modello MLX
#   ./install.sh --skip-deps     -> salta il controllo delle dipendenze
#
# Tutti i percorsi sono dinamici:
#   ~/.mlxControl              modelli, log e stato
#   ~/.minimax/local-models    script e configurazione del runtime
set -uo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${HOME:-$(echo ~)}"
CONTROL_DIR="${MLX_CONTROL_DIR:-${HOME_DIR}/.mlxControl}"
INSTALL_DIR="${LFM3_INSTALL_DIR:-${HOME_DIR}/.minimax/local-models}"
AGENTS="${HOME_DIR}/Library/LaunchAgents"
MODEL_REPO="${MLX_MODEL_REPO:-LiquidAI/LFM3-1.7B-MLX-4bit}"
MODEL_PATH="${CONTROL_DIR}/models/${MODEL_REPO}"
VENV="${INSTALL_DIR}/mlxenv"
AGENTS_URL="https://github.com/ml-explore/mlx-lm"

WITH_RIZZO=1
WITH_MODEL=1
SKIP_DEPS=0
for arg in "$@"; do
  case "$arg" in
    --no-rizzo) WITH_RIZZO=0 ;;
    --no-model) WITH_MODEL=0 ;;
    --skip-deps) SKIP_DEPS=1 ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0 ;;
  esac
done

step() { printf '\n\033[1m→ %s\033[0m\n' "$*"; }
ok()   { printf '   ✓ %s\n' "$*"; }
warn() { printf '   ! %s\n' "$*"; }
die()  { printf '\n   ERRORE: %s\n' "$*"; exit 1; }

# --------------------------------------------------------------- dipendenze
step "Controllo delle dipendenze"

if [ "$SKIP_DEPS" -eq 0 ]; then
  # 1) Homebrew
  if ! command -v brew >/dev/null 2>&1; then
    warn "Homebrew non trovato: tento l'installazione (può richiedere la password di amministratore)."
    if NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" >/dev/null 2>&1; then
      ok "Homebrew installato"
    else
      warn "Installazione automatica di Homebrew non riuscita."
      warn "Esegui manualmente: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    fi
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || true)"
  else
    ok "Homebrew: $(brew --version | head -1)"
  fi

  # 2) Xcode Command Line Tools (servono per compilare la Menu Bar Extra)
  if ! command -v swiftc >/dev/null 2>&1; then
    warn "swiftc non trovato: installo gli Xcode Command Line Tools."
    xcode-select --install 2>&1 | tail -1
    warn "Se compare una richiesta, completa l'installazione e rilancia ./install.sh"
  else
    ok "Compilatore Swift presente"
  fi

  # 3) Python 3.11+ (serve al server MLX)
  PY=""
  for candidate in python3.12 python3.13 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
        PY="$(command -v "$candidate")"
        break
      fi
    fi
  done
  if [ -z "$PY" ]; then
    warn "Python 3.11+ non trovato: installo python@3.12."
    if command -v brew >/dev/null 2>&1; then
      brew install python@3.12 >/dev/null 2>&1 || true
      PY="$(command -v python3.12 || true)"
    fi
  fi
  [ -n "$PY" ] || die "Serve Python 3.11 o superiore per il server MLX."
  ok "Python: $PY ($("$PY" -V 2>&1))"

  # 4) uv (gestore di ambienti Python, usato da Rizzo Flow)
  if ! command -v uv >/dev/null 2>&1; then
    warn "uv non trovato: lo installo."
    if command -v brew >/dev/null 2>&1; then
      brew install uv >/dev/null 2>&1 || true
    fi
    command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
    export PATH="${HOME_DIR}/.local/bin:${PATH}"
  fi
  command -v uv >/dev/null 2>&1 && ok "uv: $(uv --version 2>/dev/null | head -1)" || warn "uv non disponibile: Rizzo Flow non verrà installato."
fi

mkdir -p "$CONTROL_DIR/models" "$CONTROL_DIR/logs" "$CONTROL_DIR/state" "$INSTALL_DIR/scripts" "$AGENTS"

# ------------------------------------------------------------------ script
step "Installo gli script in ${INSTALL_DIR}"
install -m 755 "$SRC_DIR/scripts/lfm3-mlx.sh" "$INSTALL_DIR/lfm3-mlx.sh"
install -m 755 "$SRC_DIR/scripts/start-mlx-server.sh" "$INSTALL_DIR/start-mlx-server.sh"
install -m 755 "$SRC_DIR/scripts/rizzo-flow.sh" "$INSTALL_DIR/scripts/rizzo-flow.sh"
install -m 755 "$SRC_DIR/scripts/fast-compact.sh" "$INSTALL_DIR/scripts/fast-compact.sh"
install -m 755 "$SRC_DIR/scripts/models.py" "$INSTALL_DIR/scripts/models.py"
install -m 755 "$SRC_DIR/scripts/set-context.py" "$INSTALL_DIR/set-context.py"
install -m 755 "$SRC_DIR/scripts/set-default-model.py" "$INSTALL_DIR/scripts/set-default-model.py"
install -m 755 "$SRC_DIR/scripts/set-compression.py" "$INSTALL_DIR/scripts/set-compression.py"
install -m 755 "$SRC_DIR/scripts/register-provider.py" "$INSTALL_DIR/scripts/register-provider.py"
install -m 755 "$SRC_DIR/scripts/minimax_config.py" "$INSTALL_DIR/scripts/minimax_config.py"
install -m 755 "$SRC_DIR/scripts/fast_compact.py" "$INSTALL_DIR/fast_compact.py"
install -m 755 "$SRC_DIR/scripts/rizzo_compact.py" "$INSTALL_DIR/rizzo_compact.py"
# L'installer copia sé stesso: il pannello può rilanciarlo con un clic.
install -m 755 "$SRC_DIR/install.sh" "$INSTALL_DIR/install.sh"
mkdir -p "$INSTALL_DIR/config"
install -m 644 "$SRC_DIR/config/model-settings.json" "$INSTALL_DIR/config/model-settings.json" 2>/dev/null || true
install -m 644 "$SRC_DIR/config/fast-compact.json" "$INSTALL_DIR/config/fast-compact.json" 2>/dev/null || true
if [ ! -f "$INSTALL_DIR/model-settings.json" ] && [ -f "$INSTALL_DIR/config/model-settings.json" ]; then
  install -m 644 "$INSTALL_DIR/config/model-settings.json" "$INSTALL_DIR/model-settings.json"
fi
if [ ! -f "$INSTALL_DIR/fast-compact.json" ] && [ -f "$INSTALL_DIR/config/fast-compact.json" ]; then
  install -m 644 "$INSTALL_DIR/config/fast-compact.json" "$INSTALL_DIR/fast-compact.json"
fi
ok "script installati"

# ------------------------------------------------------- ambiente del modello
step "Preparo l'ambiente Python del server MLX"
if [ ! -x "$VENV/bin/mlx_lm.server" ]; then
  PY="${PY:-$(command -v python3)}"
  "$PY" -m venv "$VENV" || die "creazione venv fallita"
  "$VENV/bin/pip" install --upgrade pip >/dev/null 2>&1 || true
  # Il supporto LFM3 vive nella PR mlx-lm #2016 (non ancora merged).
  "$VENV/bin/pip" install "git+${AGENTS_URL}@refs/pull/2016/head" \
    || die "installazione di mlx-lm fallita"
fi
ok "ambiente pronto: $VENV"

# Patch del server: alias dei modelli, parametri di campionamento e hint di sistema.
if ! grep -q "MLX_LM_MODEL_ALIASES" "$VENV/lib/python3.12/site-packages/mlx_lm/server.py" 2>/dev/null \
   && ! grep -rq "MLX_LM_MODEL_ALIASES" "$VENV/lib"/python*/site-packages/mlx_lm/server.py 2>/dev/null; then
  warn "Patch mlx-lm non applicata (il modello potrebbe non essere raggiungibile con gli alias)."
fi

# ------------------------------------------------------------------- modello
if [ "$WITH_MODEL" -eq 1 ]; then
  step "Modello MLX: ${MODEL_REPO}"
  if [ -d "$MODEL_PATH" ]; then
    ok "già presente in ${MODEL_PATH}"
  else
    "$VENV/bin/python" - <<PY || warn "Download non riuscito: rilancia con ./install.sh --no-model più tardi."
import sys
try:
    from huggingface_hub import snapshot_download
except ImportError:
    print("huggingface_hub non disponibile")
    sys.exit(1)
snapshot_download(
    repo_id="${MODEL_REPO}",
    local_dir=r"""${MODEL_PATH}""",
    ignore_patterns=["*.gguf", "original/*", "*.pth"],
)
print("modello scaricato")
PY
    ok "modello scaricato"
  fi
  echo "${MODEL_PATH}" > "$INSTALL_DIR/current-model.txt"
else
  step "Modello MLX: download saltato"
fi

# ------------------------------------------------------------------- Rizzo
if [ "$WITH_RIZZO" -eq 1 ] && command -v uv >/dev/null 2>&1; then
  step "Rizzo Flow (compattazione veloce)"
  if [ -x "${CONTROL_DIR}/rizzo-flow/.venv/bin/rizzo" ]; then
    ok "già installato"
  else
    "${INSTALL_DIR}/scripts/rizzo-flow.sh" install || warn "Installazione di Rizzo Flow non riuscita."
  fi
else
  step "Rizzo Flow: installazione saltata"
fi

# ------------------------------------------------------------- LaunchAgents
step "Scrivo i LaunchAgent"
cat > "$AGENTS/com.minimax.lfm3-mlx-server.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.minimax.lfm3-mlx-server</string>
  <key>ProgramArguments</key>
  <array>
    <string>${INSTALL_DIR}/start-mlx-server.sh</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>${INSTALL_DIR}</string>
  <key>StandardOutPath</key>
  <string>${CONTROL_DIR}/logs/server.log</string>
  <key>StandardErrorPath</key>
  <string>${CONTROL_DIR}/logs/server.error.log</string>
  <key>ProcessType</key>
  <string>Interactive</string>
</dict>
</plist>
PLIST

cat > "$AGENTS/com.minimax.lfm3control.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.minimax.lfm3control</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Applications/MLX Control.app/Contents/MacOS/LFM3Control</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${CONTROL_DIR}/logs/panel.log</string>
  <key>StandardErrorPath</key>
  <string>${CONTROL_DIR}/logs/panel.error.log</string>
</dict>
</plist>
PLIST
ok "LaunchAgent scritti"

# ------------------------------------------------------- provider e avvio
step "Registro il provider custom in MiniMax Code"
if [ -f "${HOME_DIR}/.minimax/config.yaml" ]; then
  "$INSTALL_DIR/scripts/register-provider.py" --default-model local || warn "Registrazione provider non riuscita."
else
  warn "MiniMax Code non è ancora configurato: apri l'app almeno una volta, poi rilancia ./install.sh"
fi

step "Avvio dei servizi"
launchctl bootout "gui/$(id -u)/com.minimax.lfm3-mlx-server" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$(id -u)" "$AGENTS/com.minimax.lfm3-mlx-server.plist" 2>/dev/null \
  || launchctl kickstart -k "gui/$(id -u)/com.minimax.lfm3-mlx-server"
ok "server MLX in avvio (la prima volta carica il modello: qualche decina di secondi)"

if [ -x "${CONTROL_DIR}/rizzo-flow/.venv/bin/rizzo" ]; then
  "${INSTALL_DIR}/scripts/rizzo-flow.sh" start >/dev/null 2>&1 || true
  ok "Rizzo Flow avviato su http://127.0.0.1:8017"
fi
"${INSTALL_DIR}/scripts/fast-compact.sh" start >/dev/null 2>&1 || true
ok "proxy di compattazione veloce avviato su http://127.0.0.1:8081"

# ---------------------------------------------------------- Menu Bar Extra
step "Compilo e installo la Menu Bar Extra"
# Migrazione dalla vecchia app "LFM3 Control.app" (spostata nel Trash, reversibile).
if [ -d "/Applications/LFM3 Control.app" ]; then
  mv "/Applications/LFM3 Control.app" "${HOME_DIR}/.Trash/LFM3 Control.app" 2>/dev/null || true
  ok "vecchia app spostata nel Trash"
fi
if [ -x "$SRC_DIR/build.sh" ]; then
  if "$SRC_DIR/build.sh" >/dev/null 2>&1; then
    ok "app installata in /Applications/MLX Control.app"
  else
    warn "Compilazione della Menu Bar Extra non riuscita: rilancia ./build.sh"
  fi
else
  ok "app già installata (i sorgenti non sono in questa cartella)"
fi

printf '\n\033[1mInstallazione completata.\033[0m\n'
cat <<EOF

  Menu Bar Extra:   icona in alto a destra (MLX Control)
  Modelli:          ${CONTROL_DIR}/models
  Log e stato:      ${CONTROL_DIR}/logs
  Configurazione:   ${HOME_DIR}/.minimax/config.yaml (provider 'mlx')

  Comandi utili:
    ${INSTALL_DIR}/lfm3-mlx.sh status
    ${INSTALL_DIR}/scripts/rizzo-flow.sh status
    ${INSTALL_DIR}/scripts/fast-compact.sh status

  Prossimi passi in MiniMax Code:
    1. riavvia l'app per leggere il provider 'mlx'
    2. apri MLX Control → Compressione del contesto → scegli "Rizzo Flow"
EOF
