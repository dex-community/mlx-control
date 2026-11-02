#!/usr/bin/env python3
"""Genera docs/screenshot-menu.png renderizzando la stessa vista del menu.

Non usa screencapture (servirebbe il permesso TCC di registrazione schermo):
renderizza la vista SwiftUI con ImageRenderer dentro l'app e salva il PNG.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "LFM3Control.swift")
OUT = os.path.join(ROOT, "docs", "screenshot-menu.png")
TMP = "/tmp/lfm3-shot"

os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
os.makedirs(TMP, exist_ok=True)

source = open(SRC, encoding="utf-8").read()

MARKER = "@main\nstruct LFM3ControlApp: App {"
if MARKER not in source:
    print("ERR: blocco @main non trovato in LFM3Control.swift")
    sys.exit(1)

# Sostituisce la Scene MenuBarExtra con un entry point che disegna il menu su PNG.
shot = source[: source.index(MARKER)] + '''@main
struct ScreenshotMain {
    @MainActor
    static func main() {
        let state = AppState()
        // Attende il caricamento della lista dei modelli locali.
        let deadline = Date().addingTimeInterval(5)
        while state.modelStore.installed.isEmpty && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.2))
        }
        state.modelStore.activePath = state.modelStore.installed.first?.path ?? ""
        // Simula i valori mostrati in condizioni tipiche.
        state.model.isRunning = true
        state.caveman.isRunning = true
        state.settings.temperature = 0.1
        state.settings.topK = 50
        state.settings.repetitionPenalty = 1.1
        state.settings.maxTokens = 8192
        state.contextWindow = 262144

        let view = MenuContent(state: state)
            .frame(width: 290)
            .padding(12)
            .background(Color(nsColor: .windowBackgroundColor))

        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        guard let image = renderer.nsImage,
              let tiff = image.tiffRepresentation,
              let rep = NSBitmapImageRep(data: tiff),
              let png = rep.representation(using: .png, properties: [:]) else {
            FileHandle.standardError.write("ERR: rendering fallito\\n".data(using: .utf8)!)
            exit(1)
        }
        try? png.write(to: URL(fileURLWithPath: "''' + OUT + '''"))
        print("Screenshot scritto in " + "''' + OUT + '''")
    }
}
'''

path = os.path.join(TMP, "shot.swift")
open(path, "w", encoding="utf-8").write(shot)
models_src = os.path.join(ROOT, "src", "Models.swift")
models_copy = os.path.join(TMP, "Models.swift")
open(models_copy, "w", encoding="utf-8").write(open(models_src, encoding="utf-8").read())

subprocess.run(
    [
        "swiftc", "-O", "-parse-as-library",
        "-target", "arm64-apple-macos13.0",
        "-framework", "SwiftUI", "-framework", "AppKit",
        "-o", os.path.join(TMP, "shot"), path, models_copy,
    ],
    check=True,
)
subprocess.run([os.path.join(TMP, "shot")], check=True)
