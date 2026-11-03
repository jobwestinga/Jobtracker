import AppKit
import SwiftUI
@testable import JobTrackerWidgetSupport
@testable import JobTrackerKit

struct WidgetTests: CheckSuite {
    private func snapshot(_ title: String = "A focused goal") -> GoalsWidgetSnapshot {
        GoalsWidgetSnapshot(version: 1, capturedAt: "2026-11-03T17:00:00", updatedLabel: "03 Nov 2026 17:00", totalCount: 7,
            goals: (0..<4).map { index in
                WidgetGoal(uid: "00000000-0000-4000-8000-00000000000\(index)", title: index == 0 ? title : "Another goal \(index)",
                           focused: index < 2, deadlineLabel: index == 2 ? "05 Nov 2026" : nil, doneMilestones: 1, totalMilestones: 3)
            })
    }

    func snapshotReplacementRejectsInvalidDataAndKeepsLastGoodCache() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let path = directory.appendingPathComponent("Widget/goals.json")
        let first = snapshot()
        expect(try GoalsSnapshotFile.write(first, to: path))
        expect(try GoalsSnapshotFile.read(path) == first)
        expect(try !GoalsSnapshotFile.write(first, to: path), "identical cache is not replaced")
        let invalid = GoalsWidgetSnapshot(version: 2, capturedAt: first.capturedAt, updatedLabel: first.updatedLabel, totalCount: 7, goals: first.goals)
        do { _ = try GoalsSnapshotFile.write(invalid, to: path); expect(false, "unknown schema refused") } catch {}
        expect(try GoalsSnapshotFile.read(path) == first, "failed update retains the prior generation")
        let permissions = try FileManager.default.attributesOfItem(atPath: path.path)[.posixPermissions] as? Int
        expect(permissions == 0o600)
        try Data(repeating: 65, count: GoalsSnapshotFile.maximumBytes + 1).write(to: path)
        do { _ = try GoalsSnapshotFile.read(path); expect(false, "oversized cache refused") } catch {}
    }

    func routesKeepIdentityAndRejectOtherChannelsOrWriteActions() {
        let channel = WidgetChannel.production
        let uid = snapshot().goals[0].uid
        expect(channel.route(channel.goalsURL(uid)) == .goal(uid))
        expect(channel.route(channel.goalsURL()) == .goals)
        expect(WidgetChannel.pilot.route(channel.goalsURL(uid)) == nil)
        for legacy in ["legacy-goal", "imported / goal?name=1#value%", ".", ".."] {
            expect(channel.route(channel.goalsURL(legacy)) == .goal(legacy), "opaque imported identities survive URL encoding")
        }
        for text in ["jobtracker://complete/\(uid)", "jobtracker://goal/", "jobtracker://goal/%00", "jobtracker://goal/\(uid)?delete=1", "jobtracker://goal/\(uid)/extra", "https://goal/\(uid)"] {
            expect(channel.route(URL(string: text)!) == nil)
        }
        expect(channel.snapshotURL != WidgetChannel.pilot.snapshotURL)
    }

    func publisherUsesActiveGoalsEvenWhenCompletedHistoryIsVisible() throws {
        guard let live = LiveApp(publishWidget: true) else { expect(false, "live app"); return }
        let app = live.app!
        let path = live.harness.directory.appendingPathComponent("Widget/goals.json")
        live.harness.pump(10) { (try? GoalsSnapshotFile.read(path)) != nil }
        let first = try GoalsSnapshotFile.read(path)
        expect(first.goals.count == 4 && first.goals[0].focused)
        let target = app.goals.goals.first { !$0.focused }!
        app.goals.toggleCompleted()
        live.run { _ = await app.mutate("goal.toggle_focus", ["goal_uid": .string(target.uid)]) }
        live.harness.pump(10) { (try? GoalsSnapshotFile.read(path))?.goals.first(where: { $0.uid == target.uid })?.focused == !target.focused }
        let changed = try GoalsSnapshotFile.read(path)
        expect(changed.totalCount == first.totalCount, "Completed UI filter never changes exported active count")
        expect(changed.goals.contains { $0.uid == target.uid && $0.focused != target.focused })
    }

    func widgetGoalLinksOpenThatGoalAndKeepExistingDrafts() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        live.harness.pump(10) { !app.goals.goals.isEmpty }
        guard let target = app.goals.goals.last else { expect(false, "loaded goals"); return }
        app.goals.newGoal()
        guard case .goalForm(let draft) = app.topPanel?.kind else { expect(false, "goal draft"); return }
        draft.name = "Unfinished input"
        expect(app.openWidgetURL(WidgetChannel.pilot.goalsURL(target.uid), channel: .pilot))
        live.harness.pump(10) { if case .goal(let model) = app.topPanel?.kind { return model.goal.uid == target.uid }; return false }
        expect(app.panels.count == 2 && draft.name == "Unfinished input", "widget navigation never discards a draft")
        app.dismissTop()
        expect(app.topPanel.map { if case .goalForm = $0.kind { return true }; return false } == true)
    }

    func coldLaunchAndDeletedGoalLinksKeepTheOriginalIdentity() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        live.harness.pump(10) { !app.goals.goals.isEmpty }
        guard let target = app.goals.goals.first else { expect(false, "loaded goals"); return }
        live.harness.stop()
        expect(app.openWidgetURL(WidgetChannel.pilot.goalsURL(target.uid), channel: .pilot))
        app.engine.start()
        let opened = live.harness.pump(20) {
            if case .goal(let model) = app.topPanel?.kind { return model.goal.uid == target.uid }
            return false
        }
        expect(opened, "cold link waits for the engine and opens its exact goal")
        app.dismissTop()
        live.run { _ = await app.mutate("goal.delete", ["goal_uid": .string(target.uid)]) }
        _ = app.openWidgetURL(WidgetChannel.pilot.goalsURL(target.uid), channel: .pilot)
        live.harness.pump(10) { app.notice?.text.contains("no longer available") == true }
        expect(app.notice?.text.contains("no longer available") == true)
        expect(!app.panels.contains { if case .goal = $0.kind { return true }; return false }, "removed goal never targets a different row")
    }

    func mediumWidgetRendersFourRowsAndEmptyStates() throws {
        let capture = ProcessInfo.processInfo.environment["JOBTRACKER_WIDGET_CAPTURE_DIR"].map { URL(fileURLWithPath: $0) }
        if let capture { try FileManager.default.createDirectory(at: capture, withIntermediateDirectories: true) }
        for (name, data) in [("four-goals", Optional(snapshot())), ("long-title", Optional(snapshot(String(repeating: "Long goal title ", count: 20)))),
                             ("first-launch", nil)] {
            let view = NSHostingView(rootView: GoalsWidgetView(snapshot: data, channel: .pilot)
                .background(Color(red: 0.10, green: 0.11, blue: 0.13)).environment(\.colorScheme, .dark))
            view.frame = NSRect(x: 0, y: 0, width: 360, height: 170)
            view.layoutSubtreeIfNeeded()
            guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { expect(false, "widget bitmap"); continue }
            view.cacheDisplay(in: view.bounds, to: bitmap)
            expect(bitmap.pixelsWide >= 360 && bitmap.pixelsHigh >= 170)
            if let capture, let png = bitmap.representation(using: .png, properties: [:]) { try png.write(to: capture.appendingPathComponent(name + ".png")) }
        }
    }
}
