import AppKit
import SwiftUI
@testable import JobTrackerKit

struct SessionCreationTests: CheckSuite {
    func subjectHistoryCreatesEveryPresetCustomDurationAndTimeSlot() throws {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        var subject: SubjectDTO?
        live.run {
            subject = try? await app.mutate("subject.add", ["name": "Manual sessions", "color": "#3B82F6", "notes": ""]).result(SubjectResult.self).subject
        }
        guard let subject else { expect(false, "new subject"); return }
        let panel = SessionPanelModel(app: app, scope: .subject(uid: subject.uid, name: subject.name, color: subject.color))
        app.present(.sessions(panel))
        live.harness.pump(5) { panel.loaded }
        expect(panel.rows.isEmpty && panel.canCreateSessions, "creation is available without an existing session")

        func capture(_ name: String) throws {
            guard let output = ProcessInfo.processInfo.environment["JOBTRACKER_SESSION_CAPTURE_DIR"] else { return }
            let directory = URL(fileURLWithPath: output)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let view = NSHostingView(rootView: SessionPanel(app: app, id: app.topPanel!.id, model: panel)
                .environment(\.theme, app.effectiveTheme).environment(\.colorScheme, .dark))
            view.frame = NSRect(x: 0, y: 0, width: 640, height: 820)
            view.layoutSubtreeIfNeeded()
            guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { expect(false, "session panel bitmap"); return }
            view.cacheDisplay(in: view.bounds, to: bitmap)
            if let png = bitmap.representation(using: .png, properties: [:]) { try png.write(to: directory.appendingPathComponent(name + ".png")) }
        }
        try capture("empty-subject-history")

        let durations = DesignTokens.shared.roles.sessionPresets.durationsMinutes
        expect(durations == [15, 30, 60, 75, 90, 120])
        let at = LocalTime.parse("2026-11-04T00:05:12")!
        var identities: Set<String> = []
        for (index, minutes) in durations.enumerated() {
            live.run { await panel.quickAdd(minutes: minutes, at: at) }
            live.harness.pump(5) { panel.total == index + 1 }
            guard let added = panel.selected, let uid = added.uid else { expect(false, "created session selected"); return }
            expect(added.subjectUid == subject.uid && added.durationSeconds == minutes * 60)
            expect(added.endTime == LocalTime.isoString(at), "each duration ends at its captured click, including across midnight")
            expect(identities.insert(uid).inserted && panel.total == index + 1)
            expect(panel.canCreateSessions, "each completed add frees the controls")
        }
        panel.openCustomDuration()
        guard let entry = app.topPanel, case .customDuration(let custom) = entry.kind else { expect(false, "custom duration opened"); return }
        custom.toggle(60)
        custom.toggle(15)
        expect(custom.totalMinutes == 75)
        app.dismiss(entry.id)
        live.run { await custom.onConfirm(custom.totalMinutes) }
        live.harness.pump(5) { panel.total == 7 }
        expect(panel.total == 7 && panel.selected?.durationSeconds == 75 * 60)
        live.run { await panel.addSlot(startHour: 9, endHour: 11, at: at) }
        live.harness.pump(5) { panel.total == 8 }
        expect(panel.total == 8 && panel.selected?.subjectUid == subject.uid)
        expect(panel.selected?.startTime == "2026-11-04T09:00:00" && panel.selected?.endTime == "2026-11-04T11:00:00")
        try capture("subject-history-with-selection")
    }

    func subjectCreationPreservesEditsAndStaysOutOfDayHistory() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        let subject = app.subjects.subjects.first { $0.name == "Physics" }!
        let panel = SessionPanelModel(app: app, scope: .subject(uid: subject.uid, name: subject.name, color: subject.color))
        live.harness.pump(5) { panel.loaded }
        panel.select(panel.rows.first!)
        panel.draft?.end = "unfinished time edit"
        let draft = panel.draft, selected = panel.selectedUid, before = panel.total
        live.run { await panel.quickAdd(minutes: 30) }
        live.harness.pump(5) { panel.total == before + 1 }
        expect(panel.total == before + 1 && panel.draft == draft && panel.selectedUid == selected, "adding never erases the existing editor")
        live.run { await panel.quickAdd(minutes: 0) }
        expect(panel.total == before + 1 && panel.draft == draft && panel.canCreateSessions, "a refused add preserves input and frees controls")

        let day = SessionPanelModel(app: app, scope: .day(LocalTime.dayString(Date())))
        live.harness.pump(5) { day.loaded }
        let dayCount = day.total
        expect(!day.canCreateSessions)
        live.run { await day.quickAdd(minutes: 15); await day.addSlot(startHour: 9, endHour: 11); await day.refresh() }
        expect(day.total == dayCount, "day history stays an editor for its selected sessions")
    }

    func subjectCreationBlocksExtraTapsUntilLateConfirmation() {
        guard let live = LiveApp(requestTimeout: 0.2) else { expect(false, "live app"); return }
        let app = live.app!
        let subject = app.subjects.subjects.first { $0.name == "Physics" }!
        let panel = SessionPanelModel(app: app, scope: .subject(uid: subject.uid, name: subject.name, color: subject.color))
        app.present(.sessions(panel))
        live.harness.pump(5) { panel.loaded }
        let before = panel.total
        let at = LocalTime.parse("2026-11-03T10:30:00")!
        let pid = pid_t(app.engine.hello!.pid)
        kill(pid, SIGSTOP)
        defer { kill(pid, SIGCONT) }
        var finished = false
        Task { @MainActor in await panel.quickAdd(minutes: 15, at: at); finished = true }
        live.harness.pump(2) { !panel.inFlight.isEmpty }
        live.run { await panel.quickAdd(minutes: 30) }
        live.harness.pump(3) { finished }
        expect(finished && !panel.canCreateSessions && app.engine.unresolved.count == 1)
        live.run { await panel.quickAdd(minutes: 120) }
        kill(pid, SIGCONT)
        live.harness.pump(10) { panel.total == before + 1 && panel.canCreateSessions && panel.selected?.endTime == LocalTime.isoString(at) }
        expect(panel.total == before + 1 && panel.selected?.durationSeconds == 900, "late confirmation creates and selects exactly one captured session")
        expect(panel.selected?.endTime == LocalTime.isoString(at) && app.engine.unresolved.isEmpty)
        let first = panel.selectedUid
        live.run { await panel.quickAdd(minutes: 15, at: at.addingTimeInterval(60)) }
        live.harness.pump(5) { panel.total == before + 2 }
        expect(panel.total == before + 2 && panel.selectedUid != first && panel.canCreateSessions, "a subsequent deliberate click adds one new session")
    }
}
