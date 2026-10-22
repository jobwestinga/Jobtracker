import Foundation
import SwiftUI

/// Graphs workspace. Bucket bounds, grouping, totals and agenda hours come
/// from the engine (`graph_window` + analytics); the views only lay out pixels.
public final class GraphsStore: ObservableObject {
    @Published public private(set) var mode = "bar"
    @Published public private(set) var breakdown: BreakdownDTO?
    @Published public private(set) var agenda: AgendaDTO?
    @Published public private(set) var rangePreset = "weeks"
    @Published public var pulseSettings = false
    @Published public private(set) var loading = false

    private unowned let app: AppModel
    private var stale = true
    private var epoch = 0

    init(app: AppModel) { self.app = app }

    func adopt(settings: SettingsDTO) {
        let stored = settings.value("graph_view_mode", "bar")
        mode = stored == "agenda" ? "agenda" : "bar"
        rangePreset = settings.value("graph_range", "weeks")
    }

    public func markStale() { stale = true }

    @MainActor
    public func reloadIfStale() async { if stale { await reload() } }

    @MainActor
    public func reload() async {
        epoch += 1
        let mine = epoch
        stale = false
        loading = true
        defer { if mine == epoch { loading = false } }
        if mode == "agenda" {
            let response = await app.call("graphs.agenda", kind: .read)
            guard mine == epoch else { return }
            if let result = try? response.result(AgendaDTO.self) { agenda = result } else { app.report(response) }
        } else {
            let response = await app.call("graphs.breakdown", kind: .read)
            guard mine == epoch else { return }
            if let result = try? response.result(BreakdownDTO.self) { breakdown = result } else { app.report(response) }
        }
    }

    /// Up selects Stacked Bar, Down selects Agenda (Graphs page only).
    public func setMode(_ newMode: String) {
        guard newMode == "bar" || newMode == "agenda", newMode != mode else { return }
        mode = newMode
        Task { @MainActor in
            await reload()
            await app.setPreference("graph_view_mode", newMode)
        }
    }

    /// W/M/Y/A range shortcuts replace a custom range with the preset.
    public func setRange(_ preset: String) {
        guard ["weeks", "months", "year", "all"].contains(preset) else { return }
        rangePreset = preset
        pulseSettings = true
        Task { @MainActor in
            await app.setPreference("graph_range", preset)
            await reload()
        }
    }

    /// Re-read only the live parts while tracking (current-time line, running
    /// segment) without touching selection or panels.
    @MainActor
    public func liveTick() async {
        guard app.page == .graphs, app.timer.active != nil || mode == "agenda" else { return }
        await reload()
    }

    public func openDay(_ day: String) {
        app.present(.sessions(SessionPanelModel(app: app, scope: .day(day))))
    }

    /// Either mouse button on a persisted block opens exactly that session.
    public func openBlock(_ block: AgendaBlockDTO) {
        guard let uid = block.uid else { openDay(block.day); return }
        app.present(.sessions(SessionPanelModel(app: app, scope: .day(block.day), select: uid)))
    }

    public func openSettings() {
        guard let settings = app.settings else { return }
        app.present(.graphSettings(GraphSettingsModel(app: app, settings: settings)))
    }
}

/// Graph Settings panel draft; Save writes every preference in one commit.
public final class GraphSettingsModel: ObservableObject {
    @Published public var range: String
    @Published public var useCustom: Bool
    @Published public var customStart: String
    @Published public var customEnd: String
    @Published public var grouping: String
    @Published public var mode: String
    @Published public var fitWidth: Bool
    @Published public var autofitHours: Bool
    @Published public var hourStart: Int
    @Published public var hourEnd: Int
    @Published public var error: String?
    @Published public var saving = false
    private unowned let app: AppModel

    init(app: AppModel, settings: SettingsDTO) {
        self.app = app
        let stored = settings.value("graph_range", "weeks")
        let legacy = ["7": "weeks", "14": "weeks", "30": "months"]
        let resolved = legacy[stored] ?? stored
        useCustom = resolved == "custom"
        range = ["weeks", "months", "year", "all"].contains(resolved) ? resolved : "weeks"
        let today = LocalTime.dayString(Date())
        customStart = settings.value("graph_custom_start", today)
        customEnd = settings.value("graph_custom_end", today)
        let storedGrouping = settings.value("graph_grouping", "auto")
        grouping = ["auto", "daily", "weekly", "monthly"].contains(storedGrouping) ? storedGrouping : "auto"
        mode = settings.value("graph_view_mode", "bar") == "agenda" ? "agenda" : "bar"
        fitWidth = settings.value("graph_fit_horizontal", "1") == "1"
        autofitHours = settings.value("graph_autofit_hours", "0") == "1"
        var start = Int(settings.value("graph_hour_start", "6")) ?? 6
        var end = Int(settings.value("graph_hour_end", "23")) ?? 23
        start = min(27, max(0, start)); end = min(27, max(0, end))
        if end <= start { start = 6; end = 23 }
        hourStart = start
        hourEnd = end
    }

    @MainActor
    public func save() async -> Bool {
        if useCustom {
            guard let start = LocalTime.parse(customStart), let end = LocalTime.parse(customEnd) else {
                error = "Use YYYY-MM-DD for both custom dates."
                return false
            }
            guard start <= end else { error = "The custom range must end on or after its start."; return false }
        }
        guard hourEnd > hourStart else { error = "The visible hours must end after they start."; return false }
        var values: [String: JSONValue] = [
            "graph_range": .string(useCustom ? "custom" : range), "graph_view_mode": .string(mode),
            "graph_grouping": .string(grouping), "graph_hour_start": .string(String(hourStart)),
            "graph_hour_end": .string(String(hourEnd)), "graph_fit_horizontal": .string(fitWidth ? "1" : "0"),
            "graph_autofit_hours": .string(autofitHours ? "1" : "0"),
        ]
        if useCustom {
            values["graph_custom_start"] = .string(String(customStart.prefix(10)))
            values["graph_custom_end"] = .string(String(customEnd.prefix(10)))
        }
        saving = true
        defer { saving = false }
        let response = await app.mutate("settings.set", ["values": .object(values)], quiet: true)
        guard response.outcome.succeeded else { error = response.error ?? "Graph settings were not saved."; return false }
        await app.loadSettings()
        await app.graphs.reload()
        return true
    }
}
