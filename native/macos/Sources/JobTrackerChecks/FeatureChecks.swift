import AppKit
import Foundation
@testable import JobTrackerKit

/// Feature-state checks: the real stores and panel models over a live engine
/// on a synthetic store (running timer, foreign timer, goals, weights).
final class LiveApp {
    let harness = EngineIntegrationTests.Harness()
    var app: AppModel!

    init?(foreignTimer: Bool = false, requestTimeout: TimeInterval = 15) {
        let python = ProcessInfo.processInfo.environment["JOBTRACKER_ENGINE_PYTHON"] ?? "/usr/local/bin/python3"
        try? FileManager.default.createDirectory(at: harness.directory, withIntermediateDirectories: true)
        let build = Process()
        build.executableURL = URL(fileURLWithPath: python)
        build.arguments = ["-m", "tests.support.synthetic_store", harness.directory.appendingPathComponent("store.db").path,
                           "--now", LocalTime.isoString(Date(), microseconds: false)] + (foreignTimer ? ["--foreign-timer"] : [])
        build.currentDirectoryURL = repositoryRoot
        build.standardOutput = FileHandle.nullDevice
        do { try build.run() } catch { return nil }
        build.waitUntilExit()
        guard build.terminationStatus == 0 else { return nil }
        let client = EngineClient(launch: harness.launch, journal: harness.journal, requestTimeout: requestTimeout)
        harness.client = client
        app = AppModel(engine: client, configuration: nil)
        client.start()
        guard harness.pump(20, until: { client.isReady && app.settings != nil && app.subjects.list != nil }) else { return nil }
    }

    func run(_ body: @escaping () async -> Void) {
        var done = false
        Task { @MainActor in await body(); done = true }
        harness.pump(30) { done }
    }

    func settle(_ seconds: TimeInterval = 0.4) { harness.pump(seconds) { false } }

    deinit { harness.stop(); harness.cleanup() }
}

struct FeatureStateTests: CheckSuite {
    func sessionSaveKeepsNewerInputBoundToTheConfirmedSession() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        let subject = app.subjects.subjects.first { $0.name == "Reading" }!
        let panel = SessionPanelModel(app: app, scope: .subject(uid: subject.uid, name: subject.name, color: subject.color))
        live.harness.pump(5) { panel.loaded }
        let before = panel.total
        panel.beginNew()
        let pid = pid_t(app.engine.hello!.pid)
        kill(pid, SIGSTOP)
        var finished = false
        Task { @MainActor in await panel.save(); finished = true }
        live.harness.pump(2) { panel.inFlight.contains("add") }
        let newerEnd = LocalTime.isoString(Date().addingTimeInterval(600), microseconds: false).replacingOccurrences(of: "T", with: " ")
        panel.draft?.end = newerEnd
        kill(pid, SIGCONT)
        live.harness.pump(10) { finished }
        expect(finished && panel.draft?.isNew == false, "a normal delayed reply adopts the created UID")
        expect(panel.draft?.end == newerEnd, "newer input survives the confirmation")
        live.run { await panel.save() }
        expect(panel.total == before + 1, "saving the newer input edits the same session")
        expect(panel.draft?.isDirty == false, "the second save confirms the edited fields")
    }

    func closeAndEscapeKeepAPanelWhoseSaveIsInFlight() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        app.openSettings()
        guard let entry = app.topPanel, case .settings(let model) = entry.kind else { expect(false, "settings"); return }
        model.saving = true
        app.dismiss(entry.id)
        app.escape()
        expect(app.topPanel?.id == entry.id, "neither close route can abandon a save")
        model.saving = false
        app.dismiss(entry.id)
        expect(app.panels.isEmpty)
    }

    func weightSaveCannotRetargetADateChosenWhileWaiting() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        live.run { await app.weight.reload() }
        guard let imported = app.weight.view?.rows.first(where: { $0.source != "manual" }) else {
            expect(false, "imported fixture"); return
        }
        app.weight.edit(imported.uid)
        app.weight.setPrecise(imported.weightKg + 0.1)
        let pid = pid_t(app.engine.hello!.pid)
        kill(pid, SIGSTOP)
        var finished = false
        Task { @MainActor in await app.weight.save(); finished = true }
        live.harness.pump(2) { app.weight.saving }
        let newDay = LocalTime.dayString(Date().addingTimeInterval(-30 * 86400))
        app.weight.chooseDay(newDay)
        let newTarget = app.weight.editingUid
        kill(pid, SIGCONT)
        live.harness.pump(10) { finished }
        expect(finished && app.weight.day == newDay)
        expect(app.weight.editingUid == newTarget, "an older completion cannot attach the previous imported UID to another day")
    }

    func failedSyncSetupKeepsTheSettingsPanelAndTokenDraft() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        let tokenPath = live.harness.directory.appendingPathComponent("sync_token", isDirectory: true)
        do { try FileManager.default.createDirectory(at: tokenPath, withIntermediateDirectories: true) }
        catch { expect(false, "token failure fixture"); return }
        app.openSettings()
        guard case .settings(let model) = app.topPanel?.kind else { expect(false, "settings panel"); return }
        model.syncEnabled = true
        model.serverUrl = "https://example.invalid"
        model.token = "check-token"
        live.run { let saved = await model.save(); expect(!saved, "a failed sync save must not close settings") }
        expect(model.token == "check-token", "failed private file write keeps the typed token")
        expect(model.message?.contains("sync settings were not saved") == true)
        let status = try? live.harness.send("sync.status", kind: .read)?.result(SyncStatusDTO.self)
        expect(status?.enabled == false && status?.serverUrl != "https://example.invalid")
    }

    func lateSaveBindsTheOriginalEntityWithoutErasingNewerDrafts() {
        guard let live = LiveApp(requestTimeout: 0.2) else { expect(false, "live app"); return }
        let app = live.app!
        app.goals.newGoal()
        guard case .goalForm(let form) = app.topPanel?.kind else { expect(false, "goal form"); return }
        form.name = "Captured goal"
        form.addMilestone()
        form.milestones[0].title = "Captured milestone"
        let pid = pid_t(app.engine.hello!.pid)
        kill(pid, SIGSTOP)
        live.run { let saved = await form.save(); expect(!saved, "unknown save keeps the form") }
        form.name = "Newer goal draft"
        form.milestones[0].title = "Newer milestone draft"
        kill(pid, SIGCONT)
        live.harness.pump(10) { form.goal != nil && app.engine.unresolved.isEmpty }
        expect(form.goal != nil && !form.isNew, "late create becomes an edit of that exact goal")
        expect(form.name == "Newer goal draft" && form.milestones[0].title == "Newer milestone draft", "new input survives")
        expect(form.milestones[0].uid != nil, "late-created milestone identity is retained")
        let uid = form.goal?.uid
        live.run { let saved = await form.save(); expect(saved, "save newer edits") }
        let goals = (try? live.harness.send("goals.list", kind: .read)?.result(GoalsListDTO.self))?.goals ?? []
        expect(goals.filter { $0.name == "Newer goal draft" || $0.name == "Captured goal" }.count == 1)
        expect(goals.first { $0.uid == uid }?.milestones.first?.title == "Newer milestone draft")
        app.dismissTop()

        let subject = app.subjects.subjects.first { $0.name == "Reading" }!
        let panel = SessionPanelModel(app: app, scope: .subject(uid: subject.uid, name: subject.name, color: subject.color))
        app.present(.sessions(panel))
        live.harness.pump(10) { panel.loaded }
        let before = panel.total
        panel.beginNew()
        kill(pid, SIGSTOP)
        live.run { await panel.save() }
        let newerEnd = LocalTime.format(LocalTime.isoString(Date().addingTimeInterval(900)), "yyyy-MM-dd HH:mm:ss")
        panel.draft?.end = newerEnd
        kill(pid, SIGCONT)
        live.harness.pump(10) { panel.draft?.targetUid != nil && app.engine.unresolved.isEmpty }
        expect(panel.draft?.end == newerEnd && panel.draft?.isNew == false, "late session keeps newer input bound to its new UID")
        live.run { await panel.refresh() }
        expect(panel.total == before + 1, "one created session")
    }

    func historyRefreshKeepsAnOlderExplicitTargetAndArchivedSubject() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        let subject = app.subjects.subjects.first { $0.name == "Reading" }!
        var oldest: String?
        // Exceed the first history page; opening an exact old session must not
        // clear its selection just because it is outside that page.
        for index in 0..<205 {
            let day = String(format: "2025-01-%02d", index % 28 + 1)
            let result = live.harness.send("session.add", ["subject_uid": .string(subject.uid),
                "start_time": .string("\(day)T09:00:00"), "end_time": .string("\(day)T10:00:00")], kind: .mutation)
            if index == 0 { oldest = try? result?.result(SessionResult.self).session.uid }
        }
        let all = try? live.harness.send("sessions.for_subject", ["subject_uid": .string(subject.uid), "limit": 500], kind: .read)?.result(SessionPageDTO.self)
        oldest = all?.sessions.last?.uid
        _ = live.harness.send("subject.archive", ["subject_uid": .string(subject.uid)], kind: .mutation)
        let panel = SessionPanelModel(app: app, scope: .subject(uid: subject.uid, name: subject.name, color: subject.color), select: oldest)
        live.harness.pump(10) { panel.loaded }
        expect(panel.selectedUid == oldest && panel.selected != nil, "old exact session remains selected")
        expect(panel.subjects.contains { $0.uid == subject.uid }, "archived owner remains available in the editor")
        if let row = panel.selected {
            panel.draft?.end = "2025-02-01 11:00:00"
            let pendingDraft = panel.draft
            _ = live.harness.send("session.delete", ["session_uid": .string(row.uid!)], kind: .mutation)
            live.run { await panel.refresh() }
            expect(panel.selectedUid == nil && panel.draft == pendingDraft, "a deleted target clears selection but retains unsaved input")
        }
    }

    func trackingStopResumeAndSwitchFollowTheEngine() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        let physics = app.subjects.subjects.first { $0.name == "Physics" }!
        expect(app.timer.active != nil && app.timer.subject?.uid == physics.uid, "synthetic timer resumed")
        live.run { await app.subjects.stop() }
        expect(app.timer.active == nil, "stopped")
        expect(app.subjects.resumeSubject?.uid == physics.uid, "Resume follows history")
        let reading = app.subjects.subjects.first { $0.name == "Reading" }!
        live.run { await app.subjects.activate(reading) }
        expect(app.timer.subject?.uid == reading.uid, "started")
        let spanish = app.subjects.subjects.first { $0.name == "Spanish" }!
        live.run { await app.subjects.activate(spanish) }
        guard case .confirm(let confirm) = app.topPanel?.kind else { expect(false, "switch needs confirmation"); return }
        expect(app.timer.subject?.uid == reading.uid, "running session kept until confirmed")
        live.run { await confirm.action() }
        expect(app.timer.subject?.uid == spanish.uid, "switched")
    }

    /// Another device's running timer is shown, never recovered, and (as in
    /// the Qt app) a new local start waits until it stops.
    func foreignTimerIsVisibleAndRespected() {
        guard let live = LiveApp(foreignTimer: true) else { expect(false, "live app"); return }
        let app = live.app!
        expect(app.timer.foreignOpen.count == 1 && app.timer.foreignOpen[0].owned == false, "foreign timer visible, not owned")
        live.run { await app.subjects.stop() }
        let reading = app.subjects.subjects.first { $0.name == "Reading" }!
        live.run { await app.subjects.activate(reading) }
        expect(app.timer.active == nil && app.notice?.isError == true, "start refused while another device tracks")
        live.run { await app.subjects.reload() }
        expect(app.timer.foreignOpen.count == 1, "foreign timer untouched")
    }

    func archiveRegistersOneUndoStep() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        let reading = app.subjects.subjects.first { $0.name == "Reading" }!
        live.run { await app.subjects.archive(reading) }
        live.settle()
        expect(!app.subjects.subjects.contains { $0.uid == reading.uid }, "archived")
        expect(app.undoLabel != nil)
        live.run { await app.performUndo() }
        live.settle()
        expect(app.subjects.subjects.contains { $0.uid == reading.uid }, "undo restored")
        expect(app.undoLabel == nil, "single step consumed")
    }

    func sessionPanelKeepsCommandsAndSelectionDistinct() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        let physics = app.subjects.subjects.first { $0.name == "Physics" }!
        let panel = SessionPanelModel(app: app, scope: .subject(uid: physics.uid, name: physics.name, color: physics.color))
        live.harness.pump(10) { panel.loaded }
        expect(panel.selectedUid == nil, "no implicit selection")
        let before = panel.rows.count
        live.run { await panel.quickAdd(minutes: 75) }
        expect(panel.rows.count == before + 1, "quick add created one session")
        guard let created = panel.selected else { expect(false, "new session selected"); return }
        expect(created.durationSeconds == 75 * 60)
        let originalStart = LocalTime.parse(created.startTime)!
        live.run { await panel.shift(900) }
        live.run { await panel.shift(900) }
        let moved = panel.selected!
        expect(moved.uid == created.uid, "same identity after moves")
        expect(abs(LocalTime.parse(moved.startTime)!.timeIntervalSince(originalStart) - 1800) < 1, "two separate +15m moves")
        expect(moved.durationSeconds == created.durationSeconds, "duration preserved")
        // A pending field draft blocks Move and selection changes.
        panel.draft?.end = LocalTime.format(moved.startTime, "yyyy-MM-dd HH:mm:ss")
        live.run { await panel.shift(900) }
        expect(panel.message?.contains("Save or discard") == true, "move refused with dirty draft")
        let other = panel.rows.first { $0.uid != moved.uid }!
        panel.select(other)
        expect(panel.pendingSelection?.uid == other.uid && panel.selectedUid == moved.uid, "keep/discard prompt")
        panel.confirmPendingSelection(discard: false)
        // Saving a too-short edit keeps the original row.
        live.run { await panel.save() }
        expect(panel.message?.contains("30 seconds") == true, "short edit refused before mutation")
        live.run { await panel.refresh() }
        expect(panel.rows.first { $0.uid == moved.uid }?.durationSeconds == created.durationSeconds, "original intact")
        panel.discardDraft()
        // Valid edit through Save only touches the selected session.
        let newEnd = LocalTime.parse(moved.endTime)!.addingTimeInterval(600)
        panel.draft?.end = LocalTime.format(LocalTime.isoString(newEnd, microseconds: false), "yyyy-MM-dd HH:mm:ss")
        live.run { await panel.save() }
        // The edited end has whole-second precision; the untouched start keeps its fraction.
        expect(abs((panel.selected?.durationSeconds ?? 0) - (created.durationSeconds + 600)) <= 1, "saved edit: \(panel.message ?? "")")
        expect(panel.selected?.startTime == moved.startTime, "unchanged start kept exactly")
        // Delete clears the explicit selection rather than selecting row zero.
        live.run { await panel.refresh() }
        let uid = panel.selectedUid!
        panel.requestDelete()
        guard case .confirm(let confirm) = app.topPanel?.kind else { expect(false, "delete confirm"); return }
        live.run { await confirm.action() }
        expect(panel.selectedUid == nil && !panel.rows.contains { $0.uid == uid }, "deleted, nothing auto-selected")
    }

    func agendaBlockOpensThatExactSession() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        app.select(.graphs)
        app.graphs.setMode("agenda")
        live.harness.pump(10) { app.graphs.agenda != nil }
        guard let block = app.graphs.agenda?.sessions.first(where: { $0.uid != nil }) else { expect(false, "agenda blocks"); return }
        app.graphs.openBlock(block)
        guard case .sessions(let panel) = app.topPanel?.kind else { expect(false, "session panel"); return }
        live.harness.pump(10) { panel.loaded }
        expect(panel.isDayScope && panel.selectedUid == block.uid && panel.selected?.uid == block.uid, "exact block selected")
    }

    func goalDigitsOpenRowsAndToggleMilestones() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        app.select(.goals)
        live.settle(1)
        let index = app.goals.goals.firstIndex { $0.milestones.count == 3 }!
        let second = app.goals.goals[index]
        app.goals.openByNumber(index + 1)
        live.settle(0.5)
        guard case .goal(let detail) = app.topPanel?.kind else { expect(false, "goal opened"); return }
        expect(detail.goal.uid == second.uid, "the digit opens the displayed row, including starred order")
        expect(detail.goal.milestones.count == 3, "three milestones")
        let firstDone = detail.goal.milestones[1].done
        detail.toggleNumber(2)
        live.settle(0.8)
        expect(detail.goal.milestones[1].done == !firstDone, "digit 2 toggled milestone 2")
        detail.toggleNumber(9)
        live.settle(0.3)
        live.run { _ = await detail.toggleComplete() }
        expect(detail.message?.contains("milestones") == true, "completion gated")
    }

    func themePreviewRollsBackOnCancel() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        let saved = app.theme
        app.openSettings()
        guard case .settings(let settings) = app.topPanel?.kind else { expect(false, "settings"); return }
        let violet = settings.options.first { $0.name == "Violet Space" }!
        settings.preview(violet)
        expect(app.effectiveTheme.paletteName == "Violet", "previewed everywhere")
        app.escape()
        expect(app.effectiveTheme == saved && app.panels.isEmpty, "Escape restored the saved appearance")
        app.openSettings()
        guard case .settings(let again) = app.topPanel?.kind else { expect(false, "settings"); return }
        again.preview(violet)
        live.run { _ = await again.save() }
        live.settle()
        expect(app.theme.paletteName == "Violet" && app.settings?.values["theme_fx"] == "Space", "saved pair persisted")
    }

    func keyRoutingFollowsPageAndPanelOwnership() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 600, height: 600), styleMask: [.titled], backing: .buffered, defer: false)
        let router = KeyRouter(model: app, window: window)
        func key(_ characters: String, code: UInt16, repeated: Bool = false) -> Bool {
            let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: window.windowNumber,
                                         context: nil, characters: characters, charactersIgnoringModifiers: characters, isARepeat: repeated, keyCode: code)!
            return router.handle(event)
        }
        app.select(.subjects)
        expect(key("\u{F702}", code: 123) && app.page == .goals, "Left to Goals")
        expect(key("\u{F703}", code: 124, repeated: true) && app.page == .goals, "held arrow does not repeat")
        expect(key("\u{F703}", code: 124) && key("\u{F703}", code: 124) && app.page == .graphs, "Right twice to Graphs")
        expect(key("\u{F701}", code: 125) && app.graphs.mode == "agenda", "Down selects Agenda")
        expect(key("m", code: 46) && app.graphs.rangePreset == "months", "M selects Months")
        expect(key("3", code: 20) && app.panels.isEmpty, "digits ignored outside Goals")
        app.select(.goals)
        live.settle(0.5)
        expect(key("1", code: 18), "digit consumed on Goals")
        live.settle(0.5)
        guard case .goal(let detail) = app.topPanel?.kind else { expect(false, "digit opened goal"); return }
        expect(detail.goal.uid == app.goals.goals[0].uid)
        expect(key("\u{F703}", code: 124) == false && app.page == .goals, "arrows belong to the panel")
        expect(key("\u{1B}", code: 53) && app.panels.isEmpty, "Escape closes the panel")
    }

    func weightEditsKeepImportedPrecisionAndDailyIdentity() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        let weight = app.weight
        live.run { await weight.reload() }
        guard let view = weight.view, let imported = view.rows.first(where: { $0.source == "scale" && ($0.weightKg * 10).rounded() != $0.weightKg * 10 }) else {
            expect(false, "imported reading with extra precision"); return
        }
        weight.edit(imported.uid)
        expect(weight.value == imported.weightKg, "editing shows the exact imported value")
        live.run { await weight.save() }
        expect(weight.view?.rows.first { $0.uid == imported.uid }?.weightKg == imported.weightKg, "unchanged save keeps precision")
        // Today's manual entry opens for editing; a tile change updates that same row.
        weight.today()
        let manual = weight.view!.rows.first { $0.source == "manual" && $0.day == LocalTime.dayString(Date()) }!
        expect(weight.editingUid == manual.uid, "today's entry opened")
        weight.selectWhole(weight.firstWhole + 1)
        weight.selectDecimal(4)
        let expected = weight.value!
        let sentTo = weight.editingUid
        live.run { await weight.save() }
        expect(sentTo == manual.uid, "save targets today's row")
        // A newer refresh (from the tile preference writes) may supersede the
        // save's own refresh by epoch; the view settles on the committed value.
        live.harness.pump(5) { weight.view?.rows.first { $0.uid == manual.uid }?.weightKg == expected }
        let rows = weight.view!.rows.filter { $0.source == "manual" && $0.day == LocalTime.dayString(Date()) }
        expect(rows.count == 1 && rows[0].uid == manual.uid && abs(rows[0].weightKg - expected) < 1e-9, "same daily identity updated")
        expect(weight.status == "Saved.")
    }

    func movingTheSelectionOutOfTheDayClearsItWithoutReselecting() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        app.select(.graphs)
        app.graphs.setMode("agenda")
        live.harness.pump(10) { app.graphs.agenda != nil }
        guard let block = app.graphs.agenda?.sessions.first(where: { $0.uid != nil }) else { expect(false, "agenda blocks"); return }
        let panel = SessionPanelModel(app: app, scope: .day(block.day), select: block.uid)
        live.harness.pump(10) { panel.loaded }
        let others = panel.rows.filter { $0.uid != block.uid }.map(\.uid)
        live.run { await panel.shift(86400) }
        expect(panel.selectedUid == nil, "selection cleared after leaving the day")
        expect(panel.message?.contains("outside this list") == true, "explained")
        expect(panel.rows.map(\.uid) == others, "remaining rows untouched, none selected")
        live.run { await panel.shift(86400) }
        expect(panel.selectedUid == nil && panel.rows.map(\.uid) == others, "another tap acts on nothing")
    }

    func unreachableServerShowsOfflineWhileLocalSavesSucceed() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        let configure = live.harness.send("sync.configure", ["enabled": true, "server_url": "https://127.0.0.1:9", "token": "check-token"], kind: .action)
        expect(configure?.outcome == .ok, "configured")
        // Sync starts with the next owner, like the Qt window after a restart.
        live.harness.stop()
        let client = EngineClient(launch: live.harness.launch, journal: live.harness.journal, requestTimeout: 15)
        live.harness.client = client
        let restarted = AppModel(engine: client, configuration: nil)
        client.start()
        expect(live.harness.pump(20) { client.isReady && restarted.subjects.list != nil }, "restarted")
        live.harness.pump(20) { restarted.sync.title.hasPrefix("Offline") }
        expect(restarted.sync.title.hasPrefix("Offline") && restarted.sync.enabled, "offline status: \(restarted.sync.title)")
        expect(!restarted.sync.detail.contains("check-token"), "token never shown")
        let reading = restarted.subjects.subjects.first { $0.name == "Reading" }!
        let response = live.harness.send("subject.update", ["subject_uid": .string(reading.uid), "name": "Reading list", "color": .string(reading.color), "notes": ""], kind: .mutation)
        expect(response?.outcome == .applied, "local save succeeds offline")
        live.harness.pump(10) { restarted.sync.pending > 0 }
        expect(restarted.sync.pending > 0, "queued for the server")
    }
}
