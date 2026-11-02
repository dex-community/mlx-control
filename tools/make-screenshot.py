#!/usr/bin/env python3
"""Genera gli screenshot del pannello (senza screencapture: niente permessi TCC).

  docs/screenshot-menu.png      pannello principale
  docs/screenshot-download.png  pannello con il catalogo Hugging Face aperto

Il rendering usa ImageRenderer di SwiftUI sulla stessa vista usata dall'app.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "LFM3Control.swift")
MODELS_SRC = os.path.join(ROOT, "src", "Models.swift")
DOCS = os.path.join(ROOT, "docs")
TMP = "/tmp/lfm3-shot"

os.makedirs(DOCS, exist_ok=True)
os.makedirs(TMP, exist_ok=True)

source = open(SRC, encoding="utf-8").read()
MARKER = "@main\nstruct LFM3ControlApp: App {"
if MARKER not in source:
    print("ERR: blocco @main non trovato in LFM3Control.swift")
    sys.exit(1)

shot = source[: source.index(MARKER)] + '''@main
struct ScreenshotMain {
    @MainActor
    static func main() {
        let which = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "menu"
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

        var output = "''' + DOCS + '''/screenshot-menu.png"

        if which == "download" {
            AppConfig.showSearchField = false
            state.modelStore.showDownloader = true
            state.modelStore.statusMessage = "12 modelli MLX trovati"
            state.modelStore.remote = [
                RemoteModel(id: "LiquidAI/LFM3-1.7B-MLX-4bit", downloads: 41200, likes: 310, tags: ["mlx", "4bit"]),
                RemoteModel(id: "mlx-community/Qwen3-4B-Instruct-4bit", downloads: 88700, likes: 420, tags: ["mlx", "4bit"]),
                RemoteModel(id: "mlx-community/Llama-3.2-3B-Instruct-4bit", downloads: 121000, likes: 610, tags: ["mlx", "4bit"]),
                RemoteModel(id: "mistralai/Mistral-7B-Instruct-v0.4-MLX", downloads: 5400, likes: 95, tags: ["mlx"]),
            ]
            output = "''' + DOCS + '''/screenshot-download.png"
        }

        let view = MenuContent(state: state)
            .frame(width: 340)
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
        try? png.write(to: URL(fileURLWithPath: output))
        print("Screenshot scritto in " + output)
    }
}
'''

path = os.path.join(TMP, "shot.swift")
open(path, "w", encoding="utf-8").write(shot)
models_copy = os.path.join(TMP, "Models.swift")
open(models_copy, "w", encoding="utf-8").write(open(MODELS_SRC, encoding="utf-8").read())

subprocess.run(
    [
        "swiftc", "-O", "-parse-as-library",
        "-target", "arm64-apple-macos13.0",
        "-framework", "SwiftUI", "-framework", "AppKit",
        "-o", os.path.join(TMP, "shot"), path, models_copy,
    ],
    check=True,
)
subprocess.run([os.path.join(TMP, "shot"), "menu"], check=True)
subprocess.run([os.path.join(TMP, "shot"), "download"], check=True)
