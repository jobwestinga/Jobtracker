import AppKit
import Foundation
import SwiftUI
import UniformTypeIdentifiers

/// Settings panel draft. Choosing a theme previews every surface at once;
/// Save persists the pair, Cancel/Escape/close restores the saved appearance.
public final class SettingsModel: ObservableObject {
    public struct ThemeOption: Hashable, Identifiable {
        public let name: String
        public let fx: String
        public let palette: String
        public let description: String
        public var id: String { "\(fx)/\(palette)" }
    }

    @Published public private(set) var options: [ThemeOption] = []
    @Published public var selected: ThemeOption
    @Published public var reduceMotion: Bool
    @Published public var dayStart: String
    @Published public var dayStartOptions: [String]
    @Published public var syncEnabled: Bool
    @Published public var serverUrl: String
    @Published public var token = ""
    @Published public var message: String?
    @Published public var saving = false
    @Published public var dataBusy = false

    private unowned let app: AppModel
    private var finished = false

    init(app: AppModel, settings: SettingsDTO, sync: SyncStatusDTO) {
        self.app = app
        let named = DesignTokens.shared.themes.map { ThemeOption(name: $0.name, fx: $0.fx, palette: $0.palette, description: $0.description) }
        let current = app.theme
        var list = named
        if !named.contains(where: { $0.fx == current.fxName && $0.palette == current.paletteName }) {
            // A legacy pair stays exactly as saved rather than being mapped.
            list.insert(ThemeOption(name: "Current appearance (kept)", fx: current.fxName, palette: current.paletteName,
                                    description: "Your saved appearance stays in place until you choose another theme."), at: 0)
        }
        options = list
        selected = list.first { $0.fx == current.fxName && $0.palette == current.paletteName } ?? list[0]
        reduceMotion = settings.value("ui_reduce_motion", "0") == "1"
        dayStart = settings.dayStartTime
        var hours = (0..<24).map { String(format: "%02d:00", $0) }
        if !hours.contains(settings.dayStartTime) {
            let hour = Int(settings.dayStartTime.prefix(2)) ?? 3
            hours.insert(settings.dayStartTime, at: min(hours.count, hour + 1))
        }
        dayStartOptions = hours
        syncEnabled = sync.enabled
        serverUrl = sync.serverUrl
    }

    public func preview(_ option: ThemeOption) {
        selected = option
        app.previewTheme = Theme(fx: option.fx, palette: option.palette)
    }

    func cancelPreview() {
        guard !finished else { return }
        finished = true
        app.previewTheme = nil
    }

    @MainActor
    public func save() async -> Bool {
        saving = true
        defer { saving = false }
        let response = await app.saveAppearance(fx: selected.fx, palette: selected.palette, reduceMotion: reduceMotion, dayStart: dayStart)
        guard response.outcome.succeeded else {
            message = response.error ?? "Settings were not saved."
            app.previewTheme = Theme(fx: selected.fx, palette: selected.palette)
            return false
        }
        let syncResponse = await saveSync()
        finished = true
        if let syncResponse, !syncResponse.outcome.succeeded {
            app.show(syncResponse.error ?? "Sync settings were not saved.", error: true)
        }
        app.invalidate(["subjects", "graphs", "goals", "sessions"])
        return true
    }

    @MainActor
    private func saveSync() async -> EngineResponse? {
        let typed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard syncEnabled != app.sync.enabled || serverUrl.trimmingCharacters(in: .whitespaces) != app.sync.serverUrl || !typed.isEmpty else { return nil }
        // Credentials are a recoverable file replacement in the engine; the
        // token is never echoed back or journaled.
        let response = await app.call("sync.configure", [
            "enabled": .bool(syncEnabled), "server_url": .string(serverUrl),
            "token": typed.isEmpty ? .null : .string(typed),
        ], kind: .action)
        if let status = try? response.result(SyncStatusDTO.self) { app.adoptSync(status) }
        token = ""
        return response
    }

    @MainActor
    public func syncNow() async {
        _ = await saveSync()
        let response = await app.call("sync.now", kind: .action)
        struct Result: Decodable { let started: Bool; let status: SyncStatusDTO }
        if let result = try? response.result(Result.self) {
            app.adoptSync(result.status)
            message = result.started ? "Syncing…" : (result.status.connected ? "A sync is already running" : "Restart JobTracker to start syncing")
        }
    }

    // MARK: data

    @MainActor
    public func exportBackup(window: NSWindow?) async {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "jobtracker_backup.zip"
        panel.allowedContentTypes = [.zip]
        panel.canCreateDirectories = true
        guard await run(panel, window: window) == .OK, let url = panel.url else { return }
        dataBusy = true
        defer { dataBusy = false }
        let response = await app.call("backup.export", ["path": .string(url.path)], kind: .action)
        message = response.outcome.succeeded
            ? "Backup bundle saved: jobtracker_backup.json (restore file), sessions.csv, subjects.csv, daily_summary.csv."
            : "Export failed: \(response.error ?? "unknown error")"
    }

    @MainActor
    public func importBackup(window: NSWindow?) async {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.zip, .json]
        panel.allowsMultipleSelection = false
        guard await run(panel, window: window) == .OK, let url = panel.url else { return }
        app.present(.confirm(ConfirmModel(title: "Import Data", message: "This will merge the backup into your existing data.\nProceed?",
                                          confirmTitle: "Import", role: .primary, action: { [weak self] in
            guard let self else { return }
            self.dataBusy = true
            defer { self.dataBusy = false }
            let response = await self.app.mutate("backup.import", ["path": .string(url.path)], quiet: true)
            if response.outcome.succeeded {
                self.message = "Data restored successfully."
                await self.app.loadSettings()
                if let restored = self.options.first(where: { $0.fx == self.app.theme.fxName && $0.palette == self.app.theme.paletteName }) {
                    self.selected = restored
                }
                self.app.previewTheme = nil
            } else {
                self.message = "Import failed: \(response.error ?? "unknown error")"
            }
        })))
    }

    @MainActor
    private func run(_ panel: NSSavePanel, window: NSWindow?) async -> NSApplication.ModalResponse {
        await withCheckedContinuation { continuation in
            if let window {
                panel.beginSheetModal(for: window) { continuation.resume(returning: $0) }
            } else {
                continuation.resume(returning: panel.runModal())
            }
        }
    }
}

extension AppModel {
    func adoptSync(_ status: SyncStatusDTO) { setSync(status) }

    public func openSettings() {
        guard let settings else { return }
        present(.settings(SettingsModel(app: self, settings: settings, sync: sync)))
    }
}
