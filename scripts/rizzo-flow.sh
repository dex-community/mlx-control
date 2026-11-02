#!/bin/bash
# Gestione di Rizzo Flow (motore di decisioni locali, API Jev-compatibile).
#
#   rizzo-flow.sh install     clona/installa Rizzo Flow e scarica il modello
#   rizzo-flow.sh start|stop|restart|status|logs
#
# Tutti i percorsi sono dinamici: l'installazione vive in ~/.mlxControl.
set -uo pipefail

HOME_DIR="${HOME:-$(echo ~)}"
CONTROL_DIR="${MLX_CONTROL_DIR:-${HOME_DIR}/.mlxControl}"
RIZZO_DIR="${CONTROL_DIR}/rizzo-flow"
LOG_DIR="${CONTROL_DIR}/logs"
LABEL="com.minimax.rizzo-flow"
PLIST="${HOME_DIR}/Library/LaunchAgents/${LABEL}.plist"
URL="http://127.0.0.1:8017"
REPO="https://github.com/Rizzo-AI-Academy/rizzo-flow"
SIZE="${RIZZO_SIZE:-1.7b}"
CTX="${RIZZO_CTX:-32768}"

mkdir -p "$LOG_DIR" "$HOME_DIR/Library/LaunchAgents"

log() { printf '%s\n' "$*"; }

have_uv() { command -v uv >/dev/null 2>&1; }

write_plist() {
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${RIZZO_DIR}/run-rizzo.sh</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>${RIZZO_DIR}</string>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/rizzo.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/rizzo.error.log</string>
  <key>ProcessType</key>
  <string>Interactive</string>
</dict>
</plist>
PLIST
}

write_runner() {
  cat > "${RIZZO_DIR}/run-rizzo.sh" <<'RUNNER'
#!/bin/bash
# Avvia il server Rizzo Flow dalla venv locale (nessuna dipendenza da uv a runtime).
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "${DIR}/.venv/bin/rizzo" serve --size "${RIZZO_SIZE:-1.7b}" --host 127.0.0.1 --port 8017 --ctx "${RIZZO_CTX:-32768}"
RUNNER
  chmod +x "${RIZZO_DIR}/run-rizzo.sh"
}

install_rizzo() {
  if [ ! -d "$RIZZO_DIR/.git" ]; then
    log "→ Clonazione Rizzo Flow in ${RIZZO_DIR}"
    mkdir -p "$RIZZO_DIR"
    git clone --depth 1 "$REPO" "$RIZZO_DIR" || { log "ERR: clone fallito"; return 1; }
  fi
  if ! have_uv; then
    log "ERR: 'uv' non trovato. Installalo con: brew install uv"
    return 1
  fi
  log "→ uv sync (ambiente Python)"
  (cd "$RIZZO_DIR" && uv sync --locked) || { log "ERR: uv sync fallito"; return 1; }

  if [ ! -d "${RIZZO_DIR}/models" ] || [ -z "$(ls -A "${RIZZO_DIR}/models" 2>/dev/null)" ]; then
    log "→ Download del modello Spark-X2.5 (${SIZE}, ~1.8 GB)"
    (cd "$RIZZO_DIR" && uv run rizzo download --size "$SIZE") || { log "ERR: download fallito"; return 1; }
  else
    log "→ Modello già presente"
  fi

  write_runner
  write_plist
  log "→ LaunchAgent scritto: ${PLIST}"
  start
}

start() {
  write_runner
  write_plist
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null
  sleep 1
  launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null \
    || launchctl kickstart -k "gui/$(id -u)/${LABEL}"
  log "Rizzo Flow avviato su ${URL}"
}

stop() {
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null && log "Rizzo Flow fermato"
}

restart() { stop; sleep 1; start; }

status() {
  if curl -sf "${URL}/health" >/dev/null 2>&1; then
    log "Attivo: $(curl -s "${URL}/health" | head -c 160)"
  else
    log "Spento"
  fi
  launchctl list | grep "$LABEL" || true
}

logs() { tail -n 50 "${LOG_DIR}/rizzo.log" 2>/dev/null; }

case "${1:-status}" in
  install) install_rizzo ;;
  start) start ;;
  stop) stop ;;
  restart) restart ;;
  status) status ;;
  logs) logs ;;
  *) echo "uso: $0 {install|start|stop|restart|status|logs}"; exit 2 ;;
esac
