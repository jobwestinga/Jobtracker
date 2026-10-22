import AppKit

/// Evidence capture for visual review (JOBTRACKER_NATIVE_CAPTURE=<dir>).
///
/// Renders the real window offscreen-safe via `cacheDisplay`, for every named
/// theme and page (both graph modes), then quits normally. Used only with
/// synthetic isolated stores; never writes outside the given folder.
final class CaptureRunner {
    private let model: AppModel
    private let window: NSWindow
    private let directory: URL
    private var steps: [() -> Void] = []
    private let prefix: String

    init(model: AppModel, window: NSWindow, directory: URL) {
        self.model = model
        self.window = window
        self.directory = directory
        prefix = ProcessInfo.processInfo.environment["JOBTRACKER_NATIVE_CAPTURE_TAG"] ?? "native"
    }

    func start() {
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        if let size = ProcessInfo.processInfo.environment["JOBTRACKER_NATIVE_CAPTURE_SIZE"]?.split(separator: "x").compactMap({ Double($0) }), size.count == 2 {
            window.setContentSize(NSSize(width: size[0], height: size[1]))
        }
        waitUntilReady()
    }

    private func waitUntilReady(attempt: Int = 0) {
        if case .ready = model.connection, model.settings != nil, model.subjects.list != nil {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { self.plan() }
            return
        }
        guard attempt < 200 else { NSLog("Capture: engine never became ready"); NSApp.terminate(nil); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { self.waitUntilReady(attempt: attempt + 1) }
    }

    private func plan() {
        let only = ProcessInfo.processInfo.environment["JOBTRACKER_NATIVE_CAPTURE_THEMES"].map { Set($0.split(separator: ",").map(String.init)) }
        for choice in DesignTokens.shared.themes where only == nil || only!.contains(choice.name) {
            let name = choice.name.replacingOccurrences(of: " ", with: "_")
            steps.append { self.model.previewTheme = Theme(fx: choice.fx, palette: choice.palette) }
            for page in AppModel.Page.allCases {
                if page == .graphs {
                    for mode in ["bar", "agenda"] {
                        steps.append { self.model.select(.graphs); self.model.graphs.setMode(mode) }
                        steps.append { self.snapshot("\(self.prefix)-\(name)-graphs-\(mode)") }
                    }
                } else {
                    steps.append { self.model.select(page) }
                    steps.append { self.snapshot("\(self.prefix)-\(name)-\(page.title.lowercased())") }
                }
            }
        }
        if ProcessInfo.processInfo.environment["JOBTRACKER_NATIVE_CAPTURE_PANELS"] == "1" {
            steps.append { self.model.previewTheme = nil; self.model.select(.subjects) }
            steps.append {
                if let subject = self.model.subjects.subjects.first { self.model.subjects.openSessions(for: subject) }
            }
            steps.append { self.snapshot("\(self.prefix)-panel-sessions") }
            steps.append { self.model.panels.removeAll(); self.model.select(.goals) }
            steps.append { if let goal = self.model.goals.goals.first { self.model.goals.open(goal) } }
            steps.append { self.snapshot("\(self.prefix)-panel-goal") }
            steps.append { self.model.panels.removeAll(); self.model.subjects.openEditor(for: self.model.subjects.subjects.first) }
            steps.append { self.snapshot("\(self.prefix)-panel-subject-editor") }
            steps.append { self.model.panels.removeAll(); self.model.openSettings() }
            steps.append { self.snapshot("\(self.prefix)-panel-settings") }
        }
        steps.append {
            // Evidence runs end without the quit prompt: settle the engine, exit.
            self.model.panels.removeAll()
            self.model.engine.stop()
            _ = self.model.engine.waitUntilExit(timeout: 10)
            exit(0)
        }
        next()
    }

    private func next() {
        guard !steps.isEmpty else { return }
        let step = steps.removeFirst()
        step()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.9) { self.next() }
    }

    private func snapshot(_ name: String) {
        guard let view = window.contentView, let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: rep)
        guard let data = rep.representation(using: .png, properties: [:]) else { return }
        try? data.write(to: directory.appendingPathComponent("\(name).png"))
    }
}
