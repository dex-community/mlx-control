#!/bin/bash
# Installa lo stack LFM3 locale per MiniMax Code.
#   ./install.sh            -> installa script, LaunchAgent e Menu Bar Extra
#   ./install.sh --no-caveman -> salta il proxy Caveman
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${LFM3_INSTALL_DIR:-$HOME/.minimax/local-models}"
AGENTS="$HOME/Library/LaunchAgents"
MODEL="${LFM3_MODEL_PATH:-$HOME/.lmstudio/models/LiquidAI/LFM3-1.7B-MLX-4bit}"

echo "→ Directory di installazione: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR" "$AGENTS"

echo "→ Copia degli script"
install -m 755 "$SRC_DIR/scripts/lfm3-mlx.sh" "$INSTALL_DIR/lfm3-mlx.sh"
install -m 755 "$SRC_DIR/scripts/start-mlx-server.sh" "$INSTALL_DIR/start-mlx-server.sh"
install -m 755 "$SRC_DIR/scripts/set-context.py" "$INSTALL_DIR/set-context.py"
mkdir -p "$INSTALL_DIR/scripts"
install -m 755 "$SRC_DIR/scripts/models.py" "$INSTALL_DIR/scripts/models.py"
install -m 755 "$SRC_DIR/scripts/set-default-model.py" "$INSTALL_DIR/scripts/set-default-model.py"
if [ ! -f "$INSTALL_DIR/model-settings.json" ]; then
  install -m 644 "$SRC_DIR/config/model-settings.json" "$INSTALL_DIR/model-settings.json"
fi

echo "→ LaunchAgent del server"
cat > "$AGENTS/com.minimax.lfm3-mlx-server.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.minimax.lfm3-mlx-server</string>
  <key>ProgramArguments</key>
  <array>
    <string>$INSTALL_DIR/start-mlx-server.sh</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>$INSTALL_DIR</string>
  <key>StandardOutPath</key>
  <string>$INSTALL_DIR/server.log</string>
  <key>StandardErrorPath</key>
  <string>$INSTALL_DIR/server.error.log</string>
  <key>ProcessType</key>
  <string>Interactive</string>
</dict>
</plist>
PLIST

echo "→ LaunchAgent della Menu Bar Extra"
cat > "$AGENTS/com.minimax.lfm3control.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.minimax.lfm3control</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/open</string>
    <string>-g</string>
    <string>/Applications/LFM3 Control.app</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$INSTALL_DIR/lfm3control.log</string>
  <key>StandardErrorPath</key>
  <string>$INSTALL_DIR/lfm3control.error.log</string>
</dict>
</plist>
PLIST

if [ "${1:-}" != "--no-caveman" ]; then
  echo "→ LaunchAgent del proxy Caveman (se ~/.caveman/caveman.yaml esiste)"
  if [ -f "$HOME/.caveman/caveman.yaml" ]; then
    cat > "$AGENTS/com.minimax.caveman-proxy.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.minimax.caveman-proxy</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HOME/.caveman/bin/caveman-proxy</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$HOME/.caveman/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>HOME</key>
    <string>$HOME</string>
    <key>CAVE_SSRF_ALLOWLIST</key>
    <string>127.0.0.1:8080,localhost:8080</string>
    <key>MLX_API_KEY</key>
    <string>sk-mlx-local</string>
    <key>CAVEMAN_MODE</key>
    <string>compress</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$HOME/.caveman/proxy-stdout.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/.caveman/proxy-stderr.log</string>
</dict>
</plist>
PLIST
  fi
fi

echo "→ Compilo la Menu Bar Extra"
"$SRC_DIR/build.sh"

echo "→ Avvio i servizi"
restart_agent() {
  local label="$1" plist="$2"
  launchctl bootout gui/$(id -u)/"$label" 2>/dev/null || true
  # launchd ha bisogno di un momento per scaricare il servizio prima del bootstrap.
  sleep 2
  launchctl bootstrap gui/$(id -u) "$plist" 2>/dev/null \
    || launchctl kickstart -k gui/$(id -u)/"$label" 2>/dev/null \
    || echo "  ATTENZIONE: avvio di $label non riuscito (riprova con: lfm3-mlx.sh start)"
}

restart_agent com.minimax.lfm3-mlx-server "$AGENTS/com.minimax.lfm3-mlx-server.plist"
restart_agent com.minimax.lfm3control "$AGENTS/com.minimax.lfm3control.plist"
if [ -f "$AGENTS/com.minimax.caveman-proxy.plist" ]; then
  restart_agent com.minimax.caveman-proxy "$AGENTS/com.minimax.caveman-proxy.plist"
fi

sleep 3
if curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1; then
  echo "→ Server MLX attivo su http://127.0.0.1:8080/v1"
else
  echo "→ ATTENZIONE: il server non risponde ancora, controlla $INSTALL_DIR/server.log"
fi

cat <<EOF

Installazione completata.

  Server      : http://127.0.0.1:8080/v1  (modello $MODEL)
  Menu bar    : icona CPU in alto a destra
  Config      : ~/.minimax/config.yaml  (provider "mlx")
  Parametri   : ~/.minimax/local-models/model-settings.json

Comandi utili:
  $INSTALL_DIR/lfm3-mlx.sh status
  $INSTALL_DIR/lfm3-mlx.sh set-context 131072
  $INSTALL_DIR/lfm3-mlx.sh logs
EOF
