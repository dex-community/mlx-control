#!/bin/bash
# Compila la Menu Bar Extra e la installa in /Applications.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="MLX Control"
APP="/Applications/${APP_NAME}.app"
VERSION="${VERSION:-2.0.2}"

if ! command -v swiftc >/dev/null 2>&1; then
  echo "ERR: swiftc non trovato. Installa gli Xcode Command Line Tools (xcode-select --install)."
  exit 1
fi

echo "Compilo ${APP_NAME}…"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>MLX Control</string>
  <key>CFBundleDisplayName</key><string>MLX Control</string>
  <key>CFBundleIdentifier</key><string>com.minimax.lfm3control</string>
  <key>CFBundleExecutable</key><string>LFM3Control</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
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

# Icona personalizzata: generata al volo se non è già presente in assets/.
if [ ! -f "$APP/Contents/Resources/AppIcon.icns" ]; then
  if [ -f "$SRC_DIR/assets/AppIcon.icns" ]; then
    cp "$SRC_DIR/assets/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
  else
    echo "→ Genero l'icona…"
    swift "$SRC_DIR/tools/make-icon.swift" "$SRC_DIR/build/LFM3Control.iconset" >/dev/null
    iconutil -c icns "$SRC_DIR/build/LFM3Control.iconset" -o "$APP/Contents/Resources/AppIcon.icns"
  fi
fi

# Rimuove la quarantena: l'app non viene firmata con un certificato Developer ID.
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
codesign -s - --force --deep "$APP" >/dev/null 2>&1 || true

echo "Installata: $APP"
echo "Avvio…"
pkill -f "LFM3Control" 2>/dev/null || true
sleep 1
open "$APP"
echo "Fatto. L'icona compare nella barra dei menu."
