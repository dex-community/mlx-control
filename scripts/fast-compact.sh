#!/bin/bash
# Gestione del proxy di compattazione veloce (Fast Compact).
#
#   fast-compact.sh start|stop|restart|status|logs
#
# Il proxy ascolta su 127.0.0.1:8081 e inoltra al server MLX su 8080;
# intercetta la richiesta di checkpoint e la accorcia usando Rizzo Flow.
set -uo pipefail

HOME_DIR="${HOME:-$(echo ~)}"
CONTROL_DIR="${MLX_CONTROL_DIR:-${HOME_DIR}/.mlxControl}"
INSTALL_DIR="${LFM3_INSTALL_DIR:-${HOME_DIR}/.minimax/local-models}"
LOG_DIR="${CONTROL_DIR}/logs"
STATE_DIR="${CONTROL_DIR}/state"
LABEL="com.minimax.fast-compact"
PLIST="${HOME_DIR}/Library/LaunchAgents/${LABEL}.plist"
URL="http://127.0.0.1:8081/health"
SCRIPT="${INSTALL_DIR}/fast_compact.py"
PYTHON_BIN="${MINIMAX_PYTHON:-/usr/bin/python3}"

mkdir -p "$LOG_DIR" "$STATE_DIR" "$HOME_DIR/Library/LaunchAgents"

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
    <string>${PYTHON_BIN}</string>
    <string>${SCRIPT}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>${INSTALL_DIR}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>FASTCOMPACT_UPSTREAM</key>
    <string>http://127.0.0.1:8080</string>
    <key>FASTCOMPACT_LISTEN</key>
    <string>8081</string>
    <key>FASTCOMPACT_RIZZO</key>
    <string>http://127.0.0.1:8017</string>
    <key>FASTCOMPACT_CONFIG</key>
    <string>${INSTALL_DIR}/fast-compact.json</string>
    <key>FASTCOMPACT_STATE</key>
    <string>${STATE_DIR}/last-compaction.json</string>
  </dict>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/fast-compact.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/fast-compact.error.log</string>
  <key>ProcessType</key>
  <string>Interactive</string>
</dict>
</plist>
PLIST
}

start() {
  [ -f "$SCRIPT" ] || { echo "ERR: manca ${SCRIPT}"; exit 1; }
  write_plist
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null
  sleep 1
  launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null \
    || launchctl kickstart -k "gui/$(id -u)/${LABEL}"
  echo "Fast Compact avviato su ${URL}"
}

stop() {
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null && echo "Fast Compact fermato"
}

restart() { stop; sleep 1; start; }

status() {
  if curl -sf "$URL" >/dev/null 2>&1; then
    echo "Attivo: $(curl -s "$URL")"
  else
    echo "Spento"
  fi
  launchctl list | grep "$LABEL" || true
}

logs() { tail -n 50 "${LOG_DIR}/fast-compact.log" 2>/dev/null; }

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  restart) restart ;;
  status) status ;;
  logs) logs ;;
  *) echo "uso: $0 {start|stop|restart|status|logs}"; exit 2 ;;
esac
