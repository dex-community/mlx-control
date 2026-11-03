import SwiftUI
import AppKit

enum AppConfig {
    /// Lo screenshot del pannello non può disegnare un TextField: lo sostituisce con un'etichetta.
    static var showSearchField = true
}

// MARK: - Gestione servizi (LaunchAgent)

final class Service: ObservableObject {
    let id: String
    let title: String
    let symbol: String
    let label: String
    let plist: String
    let healthURL: URL?
    var statsURL: URL?

    @Published var isRunning = false
    @Published var isBusy = false
    @Published var lastError: String?
    @Published var detail: String?

    init(id: String, title: String, symbol: String, label: String, plist: String, healthURL: String?, statsURL: String? = nil) {
        self.id = id
        self.title = title
        self.symbol = symbol
        self.label = label
        self.plist = plist
        self.healthURL = healthURL.flatMap(URL.init(string:))
        self.statsURL = statsURL.flatMap(URL.init(string:))
    }

    func refresh() {
        guard let healthURL else { isRunning = false; return }
        var request = URLRequest(url: healthURL)
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { [weak self] data, _, _ in
            let alive = data != nil
            DispatchQueue.main.async { self?.isRunning = alive }
        }.resume()
        refreshDetail()
    }

    /// Dettaglio opzionale (per il server MLX: memoria occupata).
    private func refreshDetail() {
        guard let statsURL else { return }
        var request = URLRequest(url: statsURL)
        request.timeoutInterval = 3
        URLSession.shared.dataTask(with: request) { [weak self] data, _, _ in
            guard let data,
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let bytes = obj["mlx_process_bytes"] as? Int else {
                DispatchQueue.main.async { self?.detail = nil }
                return
            }
            let text = String(format: "%.1f GB", Double(bytes) / 1_073_741_824.0)
            DispatchQueue.main.async { self?.detail = text }
        }.resume()
    }

    private func launchctl(_ args: [String]) -> String? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = args
        let err = Pipe()
        p.standardError = err
        try? p.run()
        p.waitUntilExit()
        guard p.terminationStatus != 0 else { return nil }
        let data = err.fileHandleForReading.readDataToEndOfFile()
        return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func start() { perform("start") }
    func stop() { perform("stop") }
    func restart() { perform("restart") }
    func toggle() { isRunning ? stop() : start() }

    /// Verifica se il servizio risponde (usato dopo l'avvio).
    private func alive() -> Bool? {
        guard let healthURL else { return nil }
        var request = URLRequest(url: healthURL)
        request.timeoutInterval = 2
        var result: Bool? = nil
        let semaphore = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: request) { data, _, _ in
            result = data != nil
            semaphore.signal()
        }.resume()
        _ = semaphore.wait(timeout: .now() + 3)
        return result
    }

    private func perform(_ action: String) {
        isBusy = true
        lastError = nil
        DispatchQueue.global().async {
            let uid = getuid()
            var err: String?
            switch action {
            case "start":
                // bootout può fallire se il servizio non è caricato: va ignorato.
                _ = self.launchctl(["bootout", "gui/\(uid)/\(self.label)"])
                // Il job deve essere rimosso del tutto prima del bootstrap.
                Thread.sleep(forTimeInterval: 1.0)
                err = self.launchctl(["bootstrap", "gui/\(uid)", self.plist])
                if err != nil {
                    Thread.sleep(forTimeInterval: 1.5)
                    err = self.launchctl(["bootstrap", "gui/\(uid)", self.plist])
                }
                // I servizi "on demand" (Rizzo Flow) non partono con RunAtLoad:
                // in quel caso va avviato esplicitamente.
                Thread.sleep(forTimeInterval: 1.5)
                if self.alive() == false {
                    _ = self.launchctl(["kickstart", "-k", "gui/\(uid)/\(self.label)"])
                }
            case "stop":
                err = self.launchctl(["bootout", "gui/\(uid)/\(self.label)"])
                if let e = err, e.contains("Could not find service") { err = nil }
            case "restart":
                err = self.launchctl(["kickstart", "-k", "gui/\(uid)/\(self.label)"])
                if err != nil {
                    _ = self.launchctl(["bootout", "gui/\(uid)/\(self.label)"])
                    err = self.launchctl(["bootstrap", "gui/\(uid)", self.plist])
                }
            default:
                break
            }
            DispatchQueue.main.async {
                self.isBusy = false
                if let e = err, !e.isEmpty { self.lastError = e }
                DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { self.refresh() }
            }
        }
    }
}

// MARK: - Parametri del modello

final class ModelSettings: ObservableObject {
    static let path: String = {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(home)/.minimax/local-models/model-settings.json"
    }()

    @Published var temperature: Double = 0.1
    @Published var topK: Int = 50
    @Published var minP: Double = 0.0
    @Published var repetitionPenalty: Double = 1.1
    @Published var enableThinking: Bool = false
    @Published var maxTokens: Int = 8192

    func load() {
        guard let data = FileManager.default.contents(atPath: Self.path),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        if let v = obj["temperature"] as? Double { temperature = v }
        if let v = obj["top_k"] as? Int { topK = v }
        if let v = obj["min_p"] as? Double { minP = v }
        if let v = obj["repetition_penalty"] as? Double { repetitionPenalty = v }
        if let v = obj["enable_thinking"] as? Bool { enableThinking = v }
        if let v = obj["max_tokens"] as? Int { maxTokens = v }
    }

    func save() {
        // Si parte dal file esistente: le chiavi non gestite dalla UI (es. il
        // budget di memoria: prompt_cache_bytes, kv_bits, ...) devono sopravvivere.
        var obj: [String: Any] = [:]
        if let data = FileManager.default.contents(atPath: Self.path),
           let existing = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            obj = existing
        }
        obj["temperature"] = Double(String(format: "%.2f", temperature))
        obj["top_k"] = topK
        obj["min_p"] = Double(String(format: "%.2f", minP))
        obj["repetition_penalty"] = Double(String(format: "%.2f", repetitionPenalty))
        obj["enable_thinking"] = enableThinking
        obj["max_tokens"] = maxTokens
        guard let data = try? JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted, .sortedKeys]) else { return }
        try? data.write(to: URL(fileURLWithPath: Self.path), options: .atomic)
    }

    /// Valori consigliati da Liquid per LFM3 1.7B.
    func applyRecommended() {
        temperature = 0.1
        topK = 50
        repetitionPenalty = 1.1
        enableThinking = false
        maxTokens = 8192
    }
}

// MARK: - Stato applicazione

final class AppState: ObservableObject {
    static let home = FileManager.default.homeDirectoryForCurrentUser.path
    static let installDir = "\(home)/.minimax/local-models"
    static let controlDir: String = {
        let env = ProcessInfo.processInfo.environment["MLX_CONTROL_DIR"]
        return env ?? "\(home)/.mlxControl"
    }()

    let model = Service(
        id: "model",
        title: "Modello LFM3 (MLX)",
        symbol: "cpu",
        label: "com.minimax.lfm3-mlx-server",
        plist: "\(home)/Library/LaunchAgents/com.minimax.lfm3-mlx-server.plist",
        healthURL: "http://127.0.0.1:8080/health",
        statsURL: "http://127.0.0.1:8081/memory"
    )
    let caveman = Service(
        id: "caveman",
        title: "Proxy Caveman",
        symbol: "arrow.down.right.and.arrow.up.left",
        label: "com.minimax.caveman-proxy",
        plist: "\(home)/Library/LaunchAgents/com.minimax.caveman-proxy.plist",
        healthURL: "http://127.0.0.1:8787/health/live"
    )
    let rizzo = Service(
        id: "rizzo",
        title: "Rizzo Flow",
        symbol: "wand.and.stars",
        label: "com.minimax.rizzo-flow",
        plist: "\(home)/Library/LaunchAgents/com.minimax.rizzo-flow.plist",
        healthURL: "http://127.0.0.1:8017/health"
    )
    let fastCompact = Service(
        id: "fastcompact",
        title: "Compattazione veloce",
        symbol: "bolt.horizontal.circle",
        label: "com.minimax.fast-compact",
        plist: "\(home)/Library/LaunchAgents/com.minimax.fast-compact.plist",
        healthURL: "http://127.0.0.1:8081/health"
    )
    let settings = ModelSettings()
    var modelStore = ModelStore()

    @Published var contextWindow: Int = 262144
    @Published var lastApplied: Date?
    @Published var busyMessage: String?
    /// "native" | "rizzo" | "rizzo-only"
    @Published var compactionMode: String = "native"
    @Published var rizzoInstalled: Bool = false
    @Published var lastCompaction: [String: Any]?
    @Published var compressionMessage: String?
    /// Esito dell'ultima sincronizzazione dei modelli con MiniMax Code.
    @Published var registrationNotice: String?

    private var timer: Timer?
    /// Ultimo controllo della lista modelli (sincronizzazione automatica).
    private var lastModelSync: Date = .distantPast

    init() {
        settings.load()
        contextWindow = Self.currentContextWindow()
        modelStore.refreshInstalled()
        // Nuovi modelli servibili => il server MLX deve ricaricare gli alias.
        modelStore.onServerRestartNeeded = { [weak self] in
            self?.model.restart()
        }
        refreshAll()
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            self?.refreshAll()
        }
        // Sincronizzazione automatica: la lista dei modelli in MiniMax Code
        // segue da sola i modelli installati su disco.
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
            self?.syncModelsIfNeeded()
        }
        // Se il server non è attivo, lo riavvia da solo.
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
            guard let self = self else { return }
            self.model.refresh()
            if !self.model.isRunning { self.model.start() }
        }
    }

    deinit { timer?.invalidate() }

    /// Sincronizza i modelli solo se qualcosa e' cambiato (o ogni 5 minuti).
    func syncModelsIfNeeded(force: Bool = false) {
        let now = Date()
        if !force, now.timeIntervalSince(lastModelSync) < 300 { return }
        lastModelSync = now
        modelStore.syncRegistration { message, needsRestart in
            if message.contains("aggiornato") { self.registrationNotice = message }
            // Nuovi alias = nuovi modelli servibili: il server va riavviato.
            if needsRestart { self.model.restart() }
        }
    }

    func refreshAll() {
        model.refresh()
        // La lista dei modelli puo' cambiare da fuori (download, attivazione):
        // ogni tanto controlliamo e allineiamo MiniMax Code.
        syncModelsIfNeeded()
        caveman.refresh()
        rizzo.refresh()
        fastCompact.refresh()
        rizzoInstalled = Self.isRizzoInstalled()
        compactionMode = Self.currentCompactionMode()
        lastCompaction = Self.lastCompactionStats()
    }

    /// Rilegge i parametri da disco (per recepire modifiche esterne) all'apertura del menu.
    func reloadFromDisk() {
        settings.load()
        contextWindow = Self.currentContextWindow()
    }

    static func currentContextWindow() -> Int {
        let config = "\(home)/.minimax/config.yaml"
        guard let text = try? String(contentsOfFile: config, encoding: .utf8) else { return 262144 }
        // Parsing minimale: primo "context:" dentro il blocco del provider mlx.
        var inBlock = false
        for rawLine in text.split(separator: "\n") {
            let line = String(rawLine)
            if line.hasPrefix("  mlx:") { inBlock = true; continue }
            if inBlock && line.hasPrefix("  ") && !line.hasPrefix("   ") && line.contains(":") {
                break
            }
            if inBlock, line.contains("context:"),
               let value = line.split(separator: ":").last,
               let number = Int(value.trimmingCharacters(in: .whitespaces)) {
                return number
            }
        }
        return 262144
    }

    /// Applica i parametri di campionamento: salva il file e riavvia il server.
    func applySampling() {
        busyMessage = "Applicazione parametri…"
        settings.save()
        model.restart()
        lastApplied = Date()
        busyMessage = nil
    }

    /// Cambia la finestra di contesto: aggiorna config.yaml e riallinea le sessioni esistenti.
    func applyContext(_ value: Int) {
        busyMessage = "Aggiornamento contesto…"
        contextWindow = value
        runScript("\(Self.installDir)/lfm3-mlx.sh", arguments: ["set-context", String(value)]) { [weak self] status, output in
            guard let self = self else { return }
            self.busyMessage = nil
            if status != 0 {
                self.model.lastError = "Contesto non aggiornato: \(output.trimmingCharacters(in: .whitespacesAndNewlines))"
            } else {
                self.lastApplied = Date()
                self.model.restart()
            }
        }
    }

    func restartStack() {
        busyMessage = "Riavvio…"
        model.restart()
        caveman.restart()
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in self?.busyMessage = nil }
    }

    private func runScript(_ path: String, arguments: [String], completion: @escaping (Int32, String) -> Void) {
        DispatchQueue.global().async {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: path)
            p.arguments = arguments
            let out = Pipe()
            p.standardOutput = out
            p.standardError = out
            do { try p.run() } catch {
                DispatchQueue.main.async { completion(1, "comando non eseguibile: \(path)") }
                return
            }
            let data = out.fileHandleForReading.readDataToEndOfFile()
            p.waitUntilExit()
            let text = String(data: data, encoding: .utf8) ?? ""
            DispatchQueue.main.async { completion(p.terminationStatus, text) }
        }
    }

    /// Esce dall'app: scarica il LaunchAgent per non essere riavviata subito.
    func quit(stopServer: Bool) {
        if stopServer { model.stop() }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = ["bootout", "gui/\(getuid())/com.minimax.lfm3control"]
        try? p.run()
        p.waitUntilExit()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            NSApplication.shared.terminate(nil)
        }
    }

    func openLogs() {
        let t = Process()
        t.executableURL = URL(fileURLWithPath: "/usr/bin/tail")
        t.arguments = ["-f", "\(Self.installDir)/server.log"]
        try? t.run()
    }

    func openFolder() {
        NSWorkspace.shared.open(URL(fileURLWithPath: Self.installDir))
    }

    // MARK: - Compressione del contesto

    /// Rizzo Flow è installato se esiste l'eseguibile nella venv locale.
    static func isRizzoInstalled() -> Bool {
        FileManager.default.fileExists(atPath: "\(Self.controlDir)/rizzo-flow/.venv/bin/rizzo")
    }

    /// La modalità corrente è salvata nel file di configurazione del proxy.
    static func currentCompactionMode() -> String {
        let path = "\(Self.installDir)/fast-compact.json"
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let mode = obj["mode"] as? String else { return "native" }
        return mode
    }

    static func lastCompactionStats() -> [String: Any]? {
        let path = "\(Self.controlDir)/state/last-compaction.json"
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return obj
    }

    /// Applica la modalità di compattazione scelta e avvia/ferma i servizi necessari.
    func setCompactionMode(_ mode: String) {
        guard mode != compactionMode else { return }
        compressionMessage = "Applicazione modalità…"
        runScript("\(Self.installDir)/scripts/set-compression.py", arguments: [mode]) { [weak self] status, output in
            guard let self = self else { return }
            if status != 0 {
                self.compressionMessage = "Errore: \(output.trimmingCharacters(in: .whitespacesAndNewlines))"
                self.compactionMode = Self.currentCompactionMode()
                return
            }
            if mode == "native" {
                self.fastCompact.stop()
            } else {
                if !self.rizzoInstalled {
                    self.compressionMessage = "Rizzo Flow non è installato: usa «Installa Rizzo Flow»."
                    self.compactionMode = Self.currentCompactionMode()
                    return
                }
                // Rizzo Flow non viene avviato qui: in regime "on demand" lo
                // avvia il proxy di compattazione quando serve e lo spegne
                // dopo un periodo di inattivita' (risparmia ~2 GB di RAM).
                self.fastCompact.start()
            }
            self.compactionMode = mode
            self.compressionMessage = "Fatto: riavvia MiniMax Code per applicarla."
        }
    }

    /// Installa Rizzo Flow (clone + ambiente Python + modello) con un solo clic.
    func installRizzo() {
        compressionMessage = "Installazione di Rizzo Flow in corso… (~1,8 GB, qualche minuto)"
        runScript("\(Self.installDir)/scripts/rizzo-flow.sh", arguments: ["install"]) { [weak self] status, output in
            guard let self = self else { return }
            self.rizzoInstalled = Self.isRizzoInstalled()
            if status == 0 {
                self.compressionMessage = "Rizzo Flow installato e avviato."
                self.rizzo.refresh()
            } else {
                self.compressionMessage = "Installazione non riuscita: \(output.suffix(160))"
            }
        }
    }

    func openRizzoPlayground() {
        NSWorkspace.shared.open(URL(string: "http://127.0.0.1:8017/playground")!)
    }

    /// Riepilogo dello stato dello stack, con un solo clic.
    @Published var stackReport: String?

    func checkStack() {
        var lines: [String] = []
        lines.append("Modello: \(model.isRunning ? "attivo" : "spento")")
        lines.append("Rizzo Flow: \(rizzo.isRunning ? "attivo" : "spento")")
        lines.append("Compattazione veloce: \(fastCompact.isRunning ? "attiva" : "spenta")")
        lines.append("Caveman: \(caveman.isRunning ? "attivo" : "spento")")
        lines.append("Modalità compressione: \(compactionMode)")
        if let stats = lastCompaction {
            let elapsed = (stats["elapsed"] as? Double).map { String(format: "%.1f s", $0) } ?? "?"
            lines.append("Ultima compattazione: \(elapsed)")
        }
        stackReport = lines.joined(separator: " · ")
    }

    /// Rilancia l'installer: allinea dipendenze, modello, servizi e provider.
    func updateEnvironment() {
        let installer = "\(Self.installDir)/install.sh"
        guard FileManager.default.isExecutableFile(atPath: installer) else {
            stackReport = "Installer non trovato in \(installer): scarica mlx-control e rilancia ./install.sh"
            return
        }
        stackReport = "Aggiornamento dell'ambiente in corso…"
        runScript(installer, arguments: []) { [weak self] status, output in
            guard let self = self else { return }
            if status == 0 {
                self.stackReport = "Ambiente aggiornato. Riavvia MiniMax Code per applicare il provider."
                self.refreshAll()
            } else {
                let tail = output.split(separator: "\n").suffix(3).joined(separator: " · ")
                self.stackReport = "Aggiornamento non riuscito: \(tail)"
            }
        }
    }
}

// MARK: - Interfaccia (pannello)

private struct ValuePicker<T: Hashable>: View {
    @Binding var selection: T
    let options: [T]
    let label: (T) -> String
    var width: CGFloat = 96

    var body: some View {
        Picker("", selection: $selection) {
            ForEach(options, id: \.self) { Text(label($0)).tag($0) }
        }
        .pickerStyle(.menu)
        .labelsHidden()
        .controlSize(.small)
        .frame(width: width, alignment: .leading)
    }
}



private struct ServiceRow: View {
    @ObservedObject var service: Service

    var body: some View {
        HStack(alignment: .center, spacing: 8) {
            Image(systemName: service.symbol)
                .font(.system(size: 12))
                .frame(width: 16, alignment: .center)
                .foregroundStyle(service.isRunning ? Color.green : Color.secondary)
            VStack(alignment: .leading, spacing: 0) {
                Text(service.title).font(.system(size: 12))
                Text(service.isRunning ? "in esecuzione" : (service.id == "rizzo" ? "pronto · si avvia da solo" : "spento"))
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 6)
            Button {
                service.isRunning ? service.stop() : service.start()
            } label: {
                HStack(spacing: 4) {
                    Circle()
                        .fill(service.isRunning ? Color.green : Color.secondary)
                        .frame(width: 6, height: 6)
                    Text(service.isRunning ? "ON" : "OFF")
                        .font(.system(size: 10, weight: .semibold))
                }
                .padding(.horizontal, 9)
                .padding(.vertical, 4)
                .background(
                    Capsule().fill((service.isRunning ? Color.green : Color.secondary).opacity(0.16))
                )
                .foregroundStyle(service.isRunning ? Color.green : Color.secondary)
            }
            .buttonStyle(.plain)
            .disabled(service.isBusy)
        }
        .padding(.vertical, 1)
    }
}

private struct SectionLabel: View {
    let text: String
    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 9, weight: .semibold))
            .foregroundStyle(.tertiary)
    }
}

struct MenuContent: View {
    @ObservedObject var state: AppState
    @ObservedObject var store: ModelStore

    init(state: AppState) {
        self.state = state
        self.store = state.modelStore
    }

    /// Riga etichetta + controllo, tutto allineato a sinistra.
    @ViewBuilder
    private func paramRow<T: View>(_ label: String, @ViewBuilder control: () -> T) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(label)
                .font(.system(size: 12))
                .frame(width: 148, alignment: .leading)
            control()
            Spacer(minLength: 0)
        }
        .padding(.vertical, 1)
    }

    /// Binding che applica subito la modifica (nessun pulsante "Applica" da ricordare).
    private func bind<T>(_ get: @escaping () -> T, _ set: @escaping (T) -> Void) -> Binding<T> {
        Binding(get: get, set: { newValue in
            set(newValue)
            state.applySampling()
        })
    }

    /// Spiegazione della modalità di compattazione selezionata.
    private func compressionHint(_ mode: String) -> String {
        switch mode {
        case "rizzo":
            return "Rizzo Flow decide in locale quali risultati scartare, poi il modello scrive un checkpoint breve. Circa 3-6× più veloce della modalità nativa."
        case "rizzo-only":
            return "Rizzo Flow decide cosa tenere e il checkpoint viene composto senza generare nulla: è la modalità più veloce in assoluto."
        default:
            return "MiniMax Code scrive il checkpoint con il modello di sessione: è il metodo nativo, più lento sui modelli locali."
        }
    }

    /// Riepilogo dell'ultima compattazione eseguita.
    private func lastCompactionText(_ stats: [String: Any]) -> String {
        var parts: [String] = []
        if let elapsed = stats["elapsed"] as? Double {
            parts.append(String(format: "ultima: %.1f s", elapsed))
        }
        if let reduction = stats["reduction"] as? Double {
            parts.append(String(format: "input −%.0f%%", reduction * 100))
        }
        if let generated = stats["generated"] as? Bool {
            parts.append(generated ? "con generazione" : "senza generazione")
        }
        return parts.isEmpty ? "ultima compattazione: nessun dato" : parts.joined(separator: " · ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            header

            Divider()

            VStack(alignment: .leading, spacing: 5) {
                SectionLabel(text: "Servizi")
                ServiceRow(service: state.model)
                ServiceRow(service: state.caveman)
                ServiceRow(service: state.rizzo)
                ServiceRow(service: state.fastCompact)
            }

            Divider()

            VStack(alignment: .leading, spacing: 5) {
                SectionLabel(text: "Modello")
                paramRow("Attivo") {
                    ValuePicker(
                        selection: Binding(
                            get: { store.activePath },
                            set: { path in
                                if let model = store.installed.first(where: { $0.path == path }) {
                                    state.modelStore.setActive(model)
                                    state.model.restart()
                                }
                            }
                        ),
                        options: store.installed.map { $0.path },
                        label: { path in
                            store.installed.first(where: { $0.path == path })?.name
                                ?? (path as NSString).lastPathComponent
                        }
                    )
                    .frame(width: 190, alignment: .leading)
                }
                HStack(spacing: 6) {
                    Button {
                        store.showDownloader.toggle()
                        if store.showDownloader { store.fetchRemote() }
                    } label: {
                        Text(store.showDownloader ? "Chiudi catalogo" : "Scarica da Hugging Face…")
                            .font(.system(size: 11))
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)

                    Button {
                        store.syncRegistration { message, needsRestart in
                            state.registrationNotice = message
                            // Nuovi alias = nuovi modelli servibili: il server va riavviato.
                            if needsRestart { state.model.restart() }
                        }
                    } label: {
                        Text("Sincronizza modelli")
                            .font(.system(size: 11))
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    if let status = store.statusMessage, !store.showDownloader {
                        Text(status).font(.system(size: 10)).foregroundStyle(.tertiary)
                    }
                    Spacer(minLength: 0)
                }

                if let notice = state.registrationNotice {
                    Text(notice)
                        .font(.system(size: 10))
                        .foregroundStyle(.tertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if store.showDownloader {
                    downloaderSection
                }
            }

            Divider()

            Divider()

            VStack(alignment: .leading, spacing: 5) {
                SectionLabel(text: "Compressione del contesto")
                paramRow("Modalità") {
                    ValuePicker(
                        selection: Binding(
                            get: { state.compactionMode },
                            set: { state.setCompactionMode($0) }
                        ),
                        options: ["native", "rizzo", "rizzo-only"],
                        label: { mode in
                            switch mode {
                            case "rizzo": return "Rizzo + LLM"
                            case "rizzo-only": return "Solo Rizzo"
                            default: return "nativa"
                            }
                        },
                        width: 150
                    )
                }
                Text(compressionHint(state.compactionMode))
                    .font(.system(size: 10))
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)

                HStack(spacing: 6) {
                    Button {
                        state.installRizzo()
                    } label: {
                        Text(state.rizzoInstalled ? "Reinstalla Rizzo Flow" : "Installa Rizzo Flow")
                            .font(.system(size: 11))
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)

                    Button {
                        state.openRizzoPlayground()
                    } label: {
                        Text("Playground").font(.system(size: 11))
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(!state.rizzo.isRunning)

                    Spacer(minLength: 0)
                }

                if let message = state.compressionMessage {
                    Text(message).font(.system(size: 10)).foregroundStyle(.tertiary)
                }
                if let stats = state.lastCompaction {
                    Text(lastCompactionText(stats)).font(.system(size: 10)).foregroundStyle(.tertiary)
                }
            }

            Divider()

            VStack(alignment: .leading, spacing: 5) {
                SectionLabel(text: "Parametri modello")
                paramRow("Temperatura") {
                    ValuePicker(
                        selection: bind({ state.settings.temperature }, { state.settings.temperature = $0 }),
                        options: [0.0, 0.1, 0.3, 0.5, 0.7, 1.0],
                        label: { String(format: "%.1f", $0) }
                    )
                }
                paramRow("Top-k") {
                    ValuePicker(
                        selection: bind({ state.settings.topK }, { state.settings.topK = $0 }),
                        options: [0, 20, 50, 100],
                        label: { $0 == 0 ? "off" : String($0) }
                    )
                }
                paramRow("Min-p") {
                    ValuePicker(
                        selection: bind({ state.settings.minP }, { state.settings.minP = $0 }),
                        options: [0.0, 0.05, 0.1],
                        label: { String(format: "%.2f", $0) }
                    )
                }
                paramRow("Repetition penalty") {
                    ValuePicker(
                        selection: bind({ state.settings.repetitionPenalty }, { state.settings.repetitionPenalty = $0 }),
                        options: [1.0, 1.05, 1.1, 1.2],
                        label: { String(format: "%.2f", $0) }
                    )
                }
                paramRow("Max output tokens") {
                    ValuePicker(
                        selection: bind({ state.settings.maxTokens }, { state.settings.maxTokens = $0 }),
                        options: [2048, 4096, 8192, 16384],
                        label: { String($0) }
                    )
                }
                HStack(spacing: 8) {
                    Toggle(isOn: bind({ state.settings.enableThinking }, { state.settings.enableThinking = $0 })) {
                        Text("Thinking (ragionamento)")
                            .font(.system(size: 12))
                    }
                    .toggleStyle(.checkbox)
                    .controlSize(.small)
                }
                .padding(.vertical, 1)
            }

            Divider()

            VStack(alignment: .leading, spacing: 5) {
                SectionLabel(text: "Finestra di contesto")
                paramRow("Context window") {
                    ValuePicker(
                        selection: Binding(get: { state.contextWindow }, set: { state.applyContext($0) }),
                        options: [32768, 65536, 131072, 262144],
                        label: { "\($0 / 1024)K" }
                    )
                }
                Text("input+output ≤ \(state.contextWindow / 1024)K")
                    .font(.system(size: 10))
                    .foregroundStyle(.tertiary)
                    .padding(.leading, 156)
            }

            Divider()

            footer
        }
        .padding(14)
        .frame(width: 340, alignment: .leading)
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear { state.reloadFromDisk() }
    }

    /// Catalogo MLX su Hugging Face, mostrato inline (niente sheet: nei pannelli
    /// MenuBar la presentazione modale viene rimandata e si aggiorna solo riaprendo).
    private var downloaderSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                if AppConfig.showSearchField {
                    TextField("Cerca (es. qwen, llama, mlx)", text: $store.search)
                        .textFieldStyle(.roundedBorder)
                        .controlSize(.small)
                        .frame(width: 190)
                } else {
                    Text(store.search.isEmpty ? "Cerca (es. qwen, llama, mlx)" : store.search)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .frame(width: 190, alignment: .leading)
                }
                Button("Cerca") { store.fetchRemote() }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                Spacer(minLength: 0)
            }

            // Filtri rapidi: la casella di testo nei pannelli MenuBar non prende
            // sempre il focus, quindi ci sono anche scorciatoie.
            HStack(spacing: 4) {
                ForEach(["qwen", "llama", "mistral", "gemma", "lfm"], id: \.self) { tag in
                    Button {
                        store.search = tag
                        store.fetchRemote()
                    } label: {
                        Text(tag).font(.system(size: 10))
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.mini)
                }
                Spacer(minLength: 0)
            }

            if let downloading = store.downloading {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Scaricando \(downloading)").font(.system(size: 10))
                    ProgressView(value: store.downloadPercent, total: 100)
                        .controlSize(.small)
                }
            } else if store.remote.isEmpty {
                Text(store.statusMessage ?? "Premi Cerca per elencare i modelli MLX.")
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(store.remote.prefix(6)) { model in
                        HStack(spacing: 8) {
                            VStack(alignment: .leading, spacing: 0) {
                                Text(model.id).font(.system(size: 10))
                                Text("\(model.downloads) download · \(model.likes) like")
                                    .font(.system(size: 9))
                                    .foregroundStyle(.secondary)
                            }
                            Spacer(minLength: 8)
                            Button("Scarica") { store.download(model) }
                                .buttonStyle(.bordered)
                                .controlSize(.small)
                        }
                        .padding(.vertical, 2)
                        Divider()
                    }
                    if store.remote.count > 6 {
                        Text("+ \(store.remote.count - 6) altri modelli: usa la ricerca")
                            .font(.system(size: 9))
                            .foregroundStyle(.tertiary)
                            .padding(.top, 2)
                    }
                }
            }

            if let status = store.statusMessage, store.downloading == nil {
                Text(status).font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }
        .padding(.leading, 8)
        .padding(.top, 2)
    }

    /// Nome del modello attivo (dal catalogo installato).
    private var activeModelName: String {
        if let model = store.installed.first(where: { $0.path == store.activePath }) {
            return model.name
        }
        let path = store.activePath as NSString
        return path.lastPathComponent.isEmpty ? "MLX Control" : path.lastPathComponent
    }

    private var header: some View {
        HStack(spacing: 9) {
            ZStack {
                RoundedRectangle(cornerRadius: 7, style: .continuous)
                    .fill(state.model.isRunning ? Color.green.opacity(0.18) : Color.secondary.opacity(0.14))
                    .frame(width: 28, height: 28)
                Image(systemName: "cpu")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(state.model.isRunning ? Color.green : Color.secondary)
            }
            VStack(alignment: .leading, spacing: 1) {
                Text(activeModelName)
                    .font(.system(size: 13, weight: .semibold))
                Text(state.model.isRunning ? "127.0.0.1:8080 · attivo" : "127.0.0.1:8080 · spento")
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
                if let detail = state.model.detail {
                    Text("memoria \(detail)")
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 0)
            if let busy = state.busyMessage {
                HStack(spacing: 5) {
                    ProgressView().controlSize(.small).scaleEffect(0.6)
                    Text(busy).font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Button {
                    state.settings.applyRecommended()
                    state.applySampling()
                } label: {
                    Text("Consigliati").font(.system(size: 11))
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                Button {
                    state.restartStack()
                } label: {
                    Text("Riavvia").font(.system(size: 11))
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                if let applied = state.lastApplied {
                    Text("applicato \(applied.formatted(date: .omitted, time: .shortened))")
                        .font(.system(size: 10))
                        .foregroundStyle(.tertiary)
                } else {
                    Text("le modifiche si applicano subito")
                        .font(.system(size: 10))
                        .foregroundStyle(.tertiary)
                }
                Spacer(minLength: 0)
            }

            HStack(spacing: 6) {
                Button {
                    state.openLogs()
                } label: {
                    Text("Apri log").font(.system(size: 11))
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                Button {
                    state.openFolder()
                } label: {
                    Text("Apri cartella").font(.system(size: 11))
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                Button {
                    state.checkStack()
                } label: {
                    Text("Verifica stack").font(.system(size: 11))
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                Button {
                    state.updateEnvironment()
                } label: {
                    Text("Aggiorna ambiente").font(.system(size: 11))
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                Spacer(minLength: 0)
            }

            if let report = state.stackReport {
                Text(report)
                    .font(.system(size: 10))
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack(spacing: 6) {
                Button {
                    state.quit(stopServer: false)
                } label: {
                    Text("Esci").font(.system(size: 11))
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                Button {
                    state.quit(stopServer: true)
                } label: {
                    Text("Esci e spegni il modello").font(.system(size: 11))
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                Spacer(minLength: 0)
            }
        }
    }
}

@main
struct LFM3ControlApp: App {
    @StateObject private var state = AppState()

    var body: some Scene {
        MenuBarExtra {
            MenuContent(state: state)
        } label: {
            Image(systemName: "cpu")
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(state.model.isRunning ? Color.green : Color.secondary)
        }
        // Pannello libero: il menu nativo imporrebbe sottomenu e righe a tutta larghezza.
        .menuBarExtraStyle(.window)
    }
}
