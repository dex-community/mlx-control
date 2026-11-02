#!/bin/bash
# Compila la Menu Bar Extra e la installa in /Applications.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="LFM3 Control"
APP="/Applications/${APP_NAME}.app"

if ! command -v swiftc >/dev/null 2>&1; then
  echo "ERR: swiftc non trovato. Installa gli Xcode Command Line Tools (xcode-select --install)."
  exit 1
fi

echo "Compilo ${APP_NAME}…"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>LFM3 Control</string>
  <key>CFBundleDisplayName</key><string>LFM3 Control</string>
  <key>CFBundleIdentifier</key><string>com.minimax.lfm3control</string>
  <key>CFBundleExecutable</key><string>LFM3Control</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

swiftc -O -parse-as-library \
  -target arm64-apple-macos13.0 \
  -framework SwiftUI -framework AppKit \
  -o "$APP/Contents/MacOS/LFM3Control" \
  "$SRC_DIR/src/LFM3Control.swift" \
  "$SRC_DIR/src/Models.swift"

codesign -s - --force --deep "$APP" >/dev/null 2>&1 || true

echo "Installata: $APP"
echo "Avvio…"
pkill -f "LFM3Control" 2>/dev/null || true
sleep 1
open "$APP"
echo "Fatto. L'icona CPU appare nella barra dei menu."
