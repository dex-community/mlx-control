#!/bin/bash
# Gestione dello stack MLX locale per MiniMax Code.
#   start | stop | restart | status | logs | test | set-context N | fix-sessions
LABEL="com.minimax.lfm3-mlx-server"
HOME_DIR="${HOME:-$(echo ~)}"
PLIST="${HOME_DIR}/Library/LaunchAgents/${LABEL}.plist"
CONTROL_DIR="${MLX_CONTROL_DIR:-${HOME_DIR}/.mlxControl}"
MODEL="${LFM3_MODEL_PATH:-${CONTROL_DIR}/models/LiquidAI/LFM3-1.7B-MLX-4bit}"
BASE="${LFM3_INSTALL_DIR:-${HOME_DIR}/.minimax/local-models}"
URL="http://127.0.0.1:8080"

case "$1" in
  start)
    launchctl bootstrap gui/$(id -u) "$PLIST" 2>/dev/null || launchctl kickstart -k gui/$(id -u)/$LABEL
    echo "Server MLX avviato su $URL (modello: $MODEL)"
    ;;
  stop)
    launchctl bootout gui/$(id -u)/$LABEL 2>/dev/null && echo "Server MLX fermato"
    ;;
  restart)
    launchctl kickstart -k gui/$(id -u)/$LABEL && echo "Server MLX riavviato"
    ;;
  status)
    if curl -sf "$URL/health" >/dev/null 2>&1; then
      echo "Attivo: $(curl -s $URL/health)"
    else
      echo "Spento"
    fi
    launchctl list | grep "$LABEL" || true
    ;;
  logs)
    tail -n 50 "$BASE/server.log"
    ;;
  test)
    curl -s "$URL/v1/chat/completions" -H "Content-Type: application/json" \
      -d '{"model":"lfm3-1.7b-mlx","messages":[{"role":"user","content":"Ciao, rispondi con una parola."}],"max_tokens":400,"temperature":0.1}'
    echo
    ;;
  set-context)
    /usr/bin/python3 "$BASE/set-context.py" "$2"
    ;;
  fix-sessions)
    /usr/bin/python3 "$BASE/set-context.py" --sessions-only
    ;;
  *)
    echo "Uso: $0 {start|stop|restart|status|logs|test|set-context N|fix-sessions}"
    exit 1
    ;;
esac
