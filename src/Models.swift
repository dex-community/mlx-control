import Foundation
import SwiftUI

// MARK: - Gestione modelli (Hugging Face)

struct InstalledModel: Identifiable, Decodable {
    var path: String
    var name: String
    var size_gb: Double
    var id: String { path }
}

struct RemoteModel: Identifiable, Decodable {
    var id: String
    var downloads: Int
    var likes: Int
    var tags: [String]
}

final class ModelStore: ObservableObject {
    @Published var installed: [InstalledModel] = []
    @Published var remote: [RemoteModel] = []
    @Published var search: String = ""
    @Published var downloading: String?
    @Published var downloadPercent: Double = 0
    @Published var statusMessage: String?
    @Published var showDownloader: Bool = false
    @Published var activePath: String = ""

    private let scriptsDir = "\(AppState.installDir)/scripts"

    func refreshInstalled() {
        runJSON([scriptsDir + "/models.py", "list-installed"]) { result in
            if let models = try? JSONDecoder().decode([InstalledModel].self, from: result) {
                DispatchQueue.main.async { self.installed = models }
            }
        }
        runJSON([scriptsDir + "/models.py", "active"]) { result in
            let path = String(data: result, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            DispatchQueue.main.async { self.activePath = path }
        }
    }

    func fetchRemote() {
        statusMessage = "Cerco modelli su Hugging Face…"
        var args = [scriptsDir + "/models.py", "list-remote"]
        if !search.isEmpty { args.append(search) }
        runJSON(args) { result in
            if let models = try? JSONDecoder().decode([RemoteModel].self, from: result) {
                DispatchQueue.main.async {
                    self.remote = models
                    self.statusMessage = models.isEmpty ? "Nessun modello trovato" : "\(models.count) modelli MLX trovati"
                }
            } else {
                DispatchQueue.main.async { self.statusMessage = "Impossibile contattare Hugging Face" }
            }
        }
    }

    func download(_ model: RemoteModel) {
        downloading = model.id
        downloadPercent = 0
        statusMessage = "Download di \(model.id)…"

        let p = Process()
        p.executableURL = URL(fileURLWithPath: scriptsDir + "/models.py")
        p.arguments = ["download", model.id]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = Pipe()
        do { try p.run() } catch {
            DispatchQueue.main.async { self.statusMessage = "Download non riuscito" }
            return
        }
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            for line in text.split(separator: "\n") {
                guard let json = line.data(using: .utf8),
                      let obj = try? JSONSerialization.jsonObject(with: json) as? [String: Any] else { continue }
                let status = obj["status"] as? String ?? ""
                DispatchQueue.main.async {
                    if status == "progress", let percent = obj["percent"] as? Double {
                        self.downloadPercent = percent
                    } else if status == "done" {
                        self.downloadPercent = 100
                        self.downloading = nil
                        self.statusMessage = "Modello scaricato"
                        self.refreshInstalled()
                    } else if status == "error" {
                        self.downloading = nil
                        self.statusMessage = "Download fallito"
                    }
                }
            }
        }
        p.waitUntilExit()
        DispatchQueue.main.async {
            if self.downloading == model.id {
                self.downloading = nil
                if self.downloadPercent < 100 { self.statusMessage = "Download interrotto" }
            }
        }
    }

    func setActive(_ model: InstalledModel) {
        statusMessage = "Attivo \(model.name)…"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: scriptsDir + "/models.py")
        p.arguments = ["set-active", model.path]
        let pipe = Pipe()
        p.standardOutput = pipe
        try? p.run()
        p.waitUntilExit()
        DispatchQueue.main.async {
            self.activePath = model.path
            self.statusMessage = "Modello attivo: \(model.name)"
        }
    }

    private func runJSON(_ args: [String], completion: @escaping (Data) -> Void) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: args[0])
        p.arguments = Array(args.dropFirst())
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = Pipe()
        do { try p.run() } catch { return }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        completion(data)
    }
}
