import SwiftUI
import AppKit

// MARK: - Gestione servizi (LaunchAgent)

final class Service: ObservableObject {
    let id: String
    let title: String
    let symbol: String
    let label: String
    let plist: String
    let healthURL: URL?

    @Published var isRunning = false
    @Published var isBusy = false
    @Published var lastError: String?

    init(id: String, title: String, symbol: String, label: String, plist: String, healthURL: String?) {
        self.id = id
        self.title = title
        self.symbol = symbol
        self.label = label
        self.plist = plist
        self.healthURL = healthURL.flatMap(URL.init(string:))
    }

    func refresh() {
        guard let healthURL else { isRunning = false; return }
        var request = URLRequest(url: healthURL)
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { [weak self] data, _, _ in
            let alive = data != nil
            DispatchQueue.main.async { self?.isRunning = alive }
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
                err = self.launchctl(["bootstrap", "gui/\(uid)", self.plist])
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
        // Scrittura manuale: JSONSerialization serializza 0.1 come 0.10000000000000001.
        let json = """
        {
          "temperature": \(String(format: "%.2f", temperature)),
          "top_k": \(topK),
          "min_p": \(String(format: "%.2f", minP)),
          "repetition_penalty": \(String(format: "%.2f", repetitionPenalty)),
          "enable_thinking": \(enableThinking),
          "max_tokens": \(maxTokens)
        }
        """
        try? json.write(to: URL(fileURLWithPath: Self.path), atomically: true, encoding: .utf8)
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

    let model = Service(
        id: "model",
        title: "Modello LFM3 (MLX)",
        symbol: "cpu",
        label: "com.minimax.lfm3-mlx-server",
        plist: "\(home)/Library/LaunchAgents/com.minimax.lfm3-mlx-server.plist",
        healthURL: "http://127.0.0.1:8080/health"
    )
    let caveman = Service(
        id: "caveman",
        title: "Proxy Caveman",
        symbol: "arrow.down.right.and.arrow.up.left",
        label: "com.minimax.caveman-proxy",
        plist: "\(home)/Library/LaunchAgents/com.minimax.caveman-proxy.plist",
        healthURL: "http://127.0.0.1:8787/health/live"
    )
    let settings = ModelSettings()

    @Published var contextWindow: Int = 262144
    @Published var lastApplied: Date?
    @Published var busyMessage: String?

    private var timer: Timer?

    init() {
        settings.load()
        contextWindow = Self.currentContextWindow()
        refreshAll()
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            self?.refreshAll()
        }
        // Se il server non è attivo, lo riavvia da solo.
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
            guard let self = self else { return }
            self.model.refresh()
            if !self.model.isRunning { self.model.start() }
        }
    }

    deinit { timer?.invalidate() }

    func refreshAll() {
        model.refresh()
        caveman.refresh()
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

    func openLogs() {
        let t = Process()
        t.executableURL = URL(fileURLWithPath: "/usr/bin/tail")
        t.arguments = ["-f", "\(Self.installDir)/server.log"]
        try? t.run()
    }

    func openFolder() {
        NSWorkspace.shared.open(URL(fileURLWithPath: Self.installDir))
    }
}

// MARK: - Interfaccia (pannello)

private struct ValuePicker<T: Hashable>: View {
    @Binding var selection: T
    let options: [T]
    let label: (T) -> String

    var body: some View {
        Picker("", selection: $selection) {
            ForEach(options, id: \.self) { Text(label($0)).tag($0) }
        }
        .pickerStyle(.menu)
        .labelsHidden()
        .controlSize(.small)
        .frame(width: 96, alignment: .leading)
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
                Text(service.isRunning ? "in esecuzione" : "spento")
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
            }
            Button {
                service.toggle()
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: service.isRunning ? "stop.fill" : "play.fill")
                        .font(.system(size: 8))
                    Text(service.isRunning ? "Spegni" : "Accendi")
                        .font(.system(size: 11))
                }
                .padding(.horizontal, 9)
                .padding(.vertical, 4)
                .background(
                    Capsule().fill((service.isRunning ? Color.red : Color.green).opacity(0.16))
                )
                .foregroundStyle(service.isRunning ? Color.red : Color.green)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            header

            Divider()

            VStack(alignment: .leading, spacing: 5) {
                SectionLabel(text: "Servizi")
                ServiceRow(service: state.model)
                ServiceRow(service: state.caveman)
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
                Text("LFM3 1.7B · MLX")
                    .font(.system(size: 13, weight: .semibold))
                Text(state.model.isRunning ? "127.0.0.1:8080 · attivo" : "127.0.0.1:8080 · spento")
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
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
