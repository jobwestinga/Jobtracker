import AppKit
import Combine
import SwiftUI

/// Window-level presentation state and the single route to the engine.
///
/// Canonical entities come from engine responses; this model and the feature
/// stores hold only presentation state (page, panels, drafts, selections).
/// Committed-topic events are batched and delivered once per run-loop turn to
/// the stores that depend on them.
public final class AppModel: ObservableObject {
    public enum Page: Int, CaseIterable, Identifiable {
        case goals, subjects, graphs, weight
        public var id: Int { rawValue }
        public var title: String { ["Goals", "Subjects", "Graphs", "Weight"][rawValue] }
    }

    public struct Notice: Equatable, Identifiable {
        public let id = UUID()
        public let text: String
        public let isError: Bool
    }

    @Published public var page: Page = .subjects
    @Published public private(set) var theme: Theme = .fallback
    @Published public var previewTheme: Theme?
    @Published public private(set) var reduceMotion = false
    @Published public private(set) var settings: SettingsDTO?
    @Published public private(set) var timer: TimerDTO = .idle
    @Published public private(set) var timerReceived = Date()
    @Published public private(set) var sync: SyncStatusDTO = .unknown
    @Published public var panels: [PanelEntry] = []
    @Published public var notice: Notice?
    @Published public var foreground = true
    @Published public var weightFullscreen = false
    @Published public private(set) var connection: EngineClient.State = .idle
    @Published public private(set) var undoLabel: String?

    public let engine: EngineClient
    public let configuration: EngineConfiguration?
    public private(set) lazy var subjects = SubjectsStore(app: self)
    public private(set) lazy var goals = GoalsStore(app: self)
    public private(set) lazy var graphs = GraphsStore(app: self)
    public private(set) lazy var weight = WeightStore(app: self)

    private var pendingTopics: Set<String> = []
    private var flushScheduled = false
    private var undoAction: (() async -> Void)?
    private var noticeWork: DispatchWorkItem?
    private var connectionObserver: Any?

    public var effectiveTheme: Theme { previewTheme ?? theme }

    public init(engine: EngineClient, configuration: EngineConfiguration?) {
        self.engine = engine
        self.configuration = configuration
        engine.onEvent = { [weak self] event in self?.handle(event) }
        connectionObserver = engine.$state.sink { [weak self] state in self?.connection = state }
    }

    // MARK: engine access

    @MainActor
    public func call(_ command: String, _ params: [String: JSONValue] = [:], kind: CommandKind,
                     requestId: String = UUID().uuidString.lowercased()) async -> EngineResponse {
        await engine.send(command, params, kind: kind, requestId: requestId)
    }

    /// Run a mutation and surface a failure as a notice. Returns the response
    /// so callers can keep drafts on anything but success.
    @MainActor @discardableResult
    public func mutate(_ command: String, _ params: [String: JSONValue] = [:], requestId: String = UUID().uuidString.lowercased(),
                       quiet: Bool = false) async -> EngineResponse {
        let response = await call(command, params, kind: .mutation, requestId: requestId)
        if !quiet { report(response) }
        return response
    }

    @MainActor
    public func report(_ response: EngineResponse) {
        switch response.outcome {
        case .applied, .replayed, .ok:
            break
        case .unavailable:
            show(response.error ?? "The engine did not answer yet. Your change will be confirmed when it does.", error: true)
        case .missing:
            show(response.error ?? "That item no longer exists.", error: true)
        default:
            show(response.error ?? "The change was not saved.", error: true)
        }
    }

    public func show(_ text: String, error: Bool = false) {
        noticeWork?.cancel()
        notice = Notice(text: text, isError: error)
        let work = DispatchWorkItem { [weak self] in self?.notice = nil }
        noticeWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + (error ? 6 : 3.5), execute: work)
    }

    // MARK: events

    private func handle(_ event: EngineEvent) {
        switch event {
        case .connected:
            JobTrackerMain.mark("engine-hello")
            Task { @MainActor in await self.reloadEverything() }
        case .changed(let topics, _):
            invalidate(topics)
        case .syncStatus(let status):
            sync = status
        case .lateResolution(_, _, let response):
            if response.outcome == .applied || response.outcome == .replayed {
                show("A pending change was confirmed.")
            }
            invalidate(["subjects", "timer", "sessions", "graphs", "goals", "templates", "weight", "settings"])
        }
    }

    /// Batch committed topics; each dependent view refreshes once.
    public func invalidate(_ topics: Set<String>) {
        pendingTopics.formUnion(topics)
        guard !flushScheduled else { return }
        flushScheduled = true
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.flushScheduled = false
            let topics = self.pendingTopics
            self.pendingTopics.removeAll()
            Task { @MainActor in await self.apply(topics) }
        }
    }

    @MainActor
    private func apply(_ topics: Set<String>) async {
        if !topics.isDisjoint(with: ["settings", "appearance"]) { await loadSettings() }
        if !topics.isDisjoint(with: ["subjects", "timer"]) { await subjects.reload() }
        else if topics.contains("timer") { await loadTimer() }
        if !topics.isDisjoint(with: ["goals", "templates"]) { await goals.reload(includeTemplates: topics.contains("templates")) }
        if !topics.isDisjoint(with: ["graphs", "sessions", "subjects", "settings"]) { graphs.markStale(); if page == .graphs { await graphs.reload() } }
        if topics.contains("weight") || topics.contains("settings") { await weight.reload() }
        if !topics.isDisjoint(with: ["sessions", "subjects", "timer"]) {
            for entry in panels { if case .sessions(let model) = entry.kind { await model.refresh() } }
        }
        if !topics.isDisjoint(with: ["goals"]) {
            for entry in panels { if case .goal(let model) = entry.kind { model.adoptCanonical(from: goals) } }
        }
    }

    @MainActor
    public func reloadEverything() async {
        await loadSettings()
        JobTrackerMain.mark("settings-loaded")
        await subjects.reload()
        JobTrackerMain.mark("subjects-loaded")
        await goals.reload(includeTemplates: true)
        graphs.markStale()
        if page == .graphs { await graphs.reload() }
        await weight.reload()
        if let status = try? await call("sync.status", kind: .read).result(SyncStatusDTO.self) { sync = status }
        if timer.recovery != nil && !panels.contains(where: { if case .recovery = $0.kind { return true }; return false }) {
            present(.recovery(RecoveryModel(app: self, timer: timer)))
        }
    }

    @MainActor
    public func loadSettings() async {
        guard let settings = try? await call("settings.get", kind: .read).result(SettingsDTO.self) else { return }
        self.settings = settings
        theme = Theme(fx: settings.value("theme_fx", "Glow"), palette: settings.value("theme_palette", "Ocean"))
        reduceMotion = settings.value("ui_reduce_motion", "0") == "1"
        graphs.adopt(settings: settings)
        weight.adopt(settings: settings)
        if subjects.filter != settings.value("subject_stats_filter", "Total") && !subjects.filterTouched {
            subjects.filter = settings.value("subject_stats_filter", "Total")
        }
    }

    @MainActor
    public func loadTimer() async {
        if let snapshot = try? await call("timer.get", kind: .read).result(TimerDTO.self) { adopt(timer: snapshot) }
    }

    func setSync(_ status: SyncStatusDTO) { sync = status }

    public func adopt(timer snapshot: TimerDTO) {
        timer = snapshot
        timerReceived = Date()
    }

    /// Elapsed seconds of the running timer, animated from the engine snapshot.
    public func elapsed(at date: Date = Date()) -> Int {
        guard timer.active != nil else { return 0 }
        return timer.elapsedSeconds + max(0, Int(date.timeIntervalSince(timerReceived)))
    }

    // MARK: navigation and panels

    public func select(_ newPage: Page) {
        guard newPage != page else { return }
        if page == .weight { weightFullscreen = false }
        page = newPage
        Task { @MainActor in
            switch newPage {
            case .goals: await goals.enter()
            case .graphs: await graphs.reloadIfStale()
            case .weight: await weight.reload()
            case .subjects: break
            }
        }
    }

    public func navigate(_ delta: Int) {
        let target = max(0, min(Page.allCases.count - 1, page.rawValue + delta))
        if let next = Page(rawValue: target) { select(next) }
    }

    public func present(_ kind: PanelEntry.Kind) {
        panels.append(PanelEntry(kind: kind))
    }

    public func dismissTop() {
        guard let top = panels.last else { return }
        if top.kind.dismissalBlocked { return }
        top.kind.willDismiss()
        panels.removeLast()
    }

    public func dismiss(_ id: UUID) {
        guard let index = panels.firstIndex(where: { $0.id == id }) else { return }
        panels[index].kind.willDismiss()
        panels.removeSubrange(index...)
    }

    public var topPanel: PanelEntry? { panels.last }

    /// Escape: innermost panel, then weight fullscreen, then secondary views.
    public func escape() {
        if !panels.isEmpty { dismissTop(); return }
        if page == .weight && weightFullscreen { weightFullscreen = false; return }
        if page == .goals && goals.showingCompleted { goals.toggleCompleted(); return }
        if page == .subjects && subjects.showingArchived { subjects.toggleArchived() }
    }

    // MARK: undo (one reversible step, like the Qt window)

    public func registerUndo(_ label: String, _ action: @escaping () async -> Void) {
        undoLabel = label
        undoAction = action
    }

    @MainActor
    public func performUndo() async {
        guard let action = undoAction else { return }
        undoAction = nil
        undoLabel = nil
        await action()
    }

    // MARK: appearance

    @MainActor
    public func saveAppearance(fx: String, palette: String, reduceMotion: Bool, dayStart: String) async -> EngineResponse {
        var values: [String: JSONValue] = ["theme_fx": .string(fx), "theme_palette": .string(palette),
                                           "ui_reduce_motion": .string(reduceMotion ? "1" : "0")]
        if let current = settings?.dayStartTime, current != dayStart { values["day_start_time"] = .string(dayStart) }
        let response = await mutate("settings.set", ["values": .object(values)])
        if response.outcome.succeeded, let saved = try? response.result(SettingsDTO.self) {
            settings = saved
            theme = Theme(fx: fx, palette: palette)
            self.reduceMotion = reduceMotion
        }
        previewTheme = nil
        return response
    }

    @MainActor
    public func setPreference(_ key: String, _ value: String) async {
        await mutate("settings.set", ["values": .object([key: .string(value)])], quiet: true)
    }

    // MARK: lifecycle

    @MainActor
    public func foregroundChanged(_ active: Bool) async {
        foreground = active
        _ = await call("app.foreground", ["active": .bool(active)], kind: .action)
        if active { await weight.reload() }
    }

    /// Normal quit: the engine refuses while a session is genuinely running.
    @MainActor
    public func requestQuit() async -> QuitResultDTO? {
        guard engine.isReady else { return QuitResultDTO(quit: true, reason: nil, title: nil, message: nil, backupPath: nil, backupError: nil) }
        let response = await call("app.quit", kind: .action)
        return try? response.result(QuitResultDTO.self)
    }
}
