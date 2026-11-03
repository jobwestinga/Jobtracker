import AppKit
import Combine
@testable import JobTrackerKit

struct InteractionTests: CheckSuite {
    func agendaBottomSnapStaysInsideTheChosenLogicalDay() {
        for dayStart in [3.0, 6.5, 23.75] {
            for minute in 0..<60 {
                let start = dayStart + 6 + Double(minute) / 60
                let source = AgendaBlockDTO(uid: "s", day: "2026-11-02", subjectUid: "a", subjectName: "Work", color: "#3B82F6", startH: start, endH: start + 1, durationSeconds: 3600)
                let data = AgendaDTO(days: [source.day], sessions: [source], hourStart: Int(dayStart), hourEnd: Int(ceil(dayStart + 24)), nowDay: "", nowHour: 0, dayStartHour: dayStart, rangeLabel: "", fitWidth: true)
                let layout = AgendaLayout(agenda: data, size: CGSize(width: 600, height: 780))
                let block = layout.columns[0].blocks[0]
                var lift = LiftPlacement()
                lift.begin("s", frame: block.rect, start: CGPoint(x: block.rect.midX, y: block.rect.minY))
                lift.location = CGPoint(x: block.rect.midX, y: layout.chart.maxY - 0.1)
                let drop = AgendaDrop.project(block: block, lift: lift, layout: layout, dayStart: dayStart)!
                expect(start + Double(drop.seconds) / 3600 < dayStart + 24, "bottom drop stays in its logical column (boundary=\(dayStart), minute=\(minute))")
            }
        }
    }

    func agendaProjectionKeepsGrabOffsetAndSnapsAcrossDays() {
        let source = AgendaBlockDTO(uid: "s", day: "2026-11-02", subjectUid: "a", subjectName: "Work", color: "#3B82F6", startH: 25, endH: 26, durationSeconds: 3600)
        let data = AgendaDTO(days: ["2026-11-02", "2026-11-03"], sessions: [source], hourStart: 3, hourEnd: 27, nowDay: "", nowHour: 0, dayStartHour: 3, rangeLabel: "", fitWidth: true)
        let layout = AgendaLayout(agenda: data, size: CGSize(width: 600, height: 780))
        let block = layout.columns[0].blocks[0]
        var lift = LiftPlacement()
        lift.begin("s", frame: block.rect, start: CGPoint(x: block.rect.midX, y: block.rect.minY + 8))
        let unchanged = AgendaDrop.project(block: block, lift: lift, layout: layout, dayStart: 3)!
        expect(unchanged.day == source.day && unchanged.seconds == 0)
        lift.location = CGPoint(x: layout.columns[1].rect.midX, y: lift.start.y - 7)
        let drop = AgendaDrop.project(block: block, lift: lift, layout: layout, dayStart: 3)!
        expect(drop.day == "2026-11-03" && drop.seconds == -900, "seven pixels is fourteen minutes, snapped to fifteen")
        expect(lift.liftedFrame.minY == lift.location.y - 8, "pointer stays where the block was grabbed")
        lift.location = CGPoint(x: layout.chart.maxX + 1, y: layout.chart.midY)
        expect(AgendaDrop.project(block: block, lift: lift, layout: layout, dayStart: 3) == nil, "outside drops cancel")
        lift.location = CGPoint(x: layout.columns[1].rect.midX, y: layout.chart.maxY - 1)
        let bottom = AgendaDrop.project(block: block, lift: lift, layout: layout, dayStart: 3)!
        expect(source.startH + Double(bottom.seconds) / 3600 < 27, "the start remains inside the target logical day")
    }

    func chartClickDragAndEscapeDispatchOnlyOneAction() {
        let view = ChartHitArea.HitView(frame: CGRect(x: 0, y: 0, width: 200, height: 200))
        var clicks = 0, moves = 0, drops = 0, cancels = 0
        view.regions = [.init(rect: view.bounds, tooltip: "", clickable: true, action: { clicks += 1 },
            drag: .init(changed: { _, _ in moves += 1 }, ended: { _ in drops += 1 }, cancelled: { cancels += 1 }))]
        func event(_ type: NSEvent.EventType, _ x: CGFloat) -> NSEvent {
            NSEvent.mouseEvent(with: type, location: CGPoint(x: x, y: 10), modifierFlags: [], timestamp: 0, windowNumber: 0, context: nil, eventNumber: 0, clickCount: 1, pressure: 1)!
        }
        view.mouseDown(with: event(.leftMouseDown, 10))
        expect(clicks == 0, "press does not open the panel before a drag")
        view.mouseUp(with: event(.leftMouseUp, 10))
        expect(clicks == 1)
        view.mouseDown(with: event(.leftMouseDown, 10))
        view.mouseDragged(with: event(.leftMouseDragged, 30))
        view.mouseUp(with: event(.leftMouseUp, 30))
        expect(clicks == 1 && moves == 1 && drops == 1, "drag never also clicks")
        view.mouseDown(with: event(.leftMouseDown, 10))
        view.mouseDragged(with: event(.leftMouseDragged, 30))
        NotificationCenter.default.post(name: .cancelCardDrag, object: nil)
        view.mouseUp(with: event(.leftMouseUp, 30))
        expect(clicks == 1 && drops == 1 && cancels == 1, "Escape suppresses mouse-up")
    }

    func sessionAutosaveAndDurationKeepExactIdentity() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        let physics = app.subjects.subjects.first { $0.name == "Physics" }!
        let reading = app.subjects.subjects.first { $0.name == "Reading" }!
        let panel = SessionPanelModel(app: app, scope: .subject(uid: physics.uid, name: physics.name, color: physics.color))
        live.harness.pump(5) { panel.loaded }
        let row = panel.rows.first!
        panel.select(row)
        panel.draft?.end = LocalTime.format(LocalTime.isoString(LocalTime.parse(row.endTime)!.addingTimeInterval(300), microseconds: false), "yyyy-MM-dd HH:mm:ss")
        live.run { await panel.commitFields() }
        expect(panel.selected?.durationSeconds == row.durationSeconds + 300, "time commits without Save")
        live.run { await panel.resize(300) }
        expect(panel.selectedUid == row.uid && panel.selected?.durationSeconds == row.durationSeconds + 600)
        expect(panel.selected?.startTime == row.startTime, "resize keeps exact start")
        live.run { await panel.changeSubject(reading.uid) }
        var result: SessionDTO?
        live.run { result = try? await app.call("session.get", ["session_uid": .string(row.uid!)], kind: .read).result(SessionResult.self).session }
        expect(result?.subjectUid == reading.uid, "subject commits without Save")
        expect(panel.selectedUid == nil && panel.draft == nil, "moving outside subject history clears selection")
    }

    func closingInvalidSessionInputKeepsItUntilExplicitDiscard() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        let subject = app.subjects.subjects.first!
        let panel = SessionPanelModel(app: app, scope: .subject(uid: subject.uid, name: subject.name, color: subject.color))
        live.harness.pump(5) { panel.loaded }
        app.present(.sessions(panel))
        let id = app.topPanel!.id
        panel.select(panel.rows.first!)
        panel.draft?.end = "incomplete time"
        live.run { await panel.commitFields() }
        app.dismiss(id)
        expect(app.panels.count == 2 && panel.draft?.end == "incomplete time", "closing offers discard and preserves invalid input")
        app.dismissTop()
        expect(app.topPanel?.id == id && panel.draft?.isDirty == true, "cancel returns to the original draft")
        app.dismiss(id)
        guard case .confirm(let confirm) = app.topPanel?.kind else { expect(false, "discard prompt"); return }
        live.run { confirm.running = true; await confirm.action(); confirm.running = false; app.dismissTop() }
        live.settle(0.1)
        expect(app.panels.isEmpty, "explicit discard closes the original editor")
    }

    func anOlderReadCannotRestoreThePreDropOrder() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        live.run { await app.goals.reload() }
        live.settle()
        let pid = pid_t(app.engine.hello!.pid)
        for goals in [false, true] {
            let previous = goals ? app.goals.goals.map(\.uid) : app.subjects.subjects.map(\.uid)
            let desired = goals ? Array(previous.prefix(1)) + previous.dropFirst().reversed() : previous.reversed().map { $0 }
            kill(pid, SIGSTOP)
            var readDone = false, dropDone = false
            Task { @MainActor in
                if goals { await app.goals.reload() } else { await app.subjects.reload() }
                readDone = true
            }
            live.settle(0.05)
            Task { @MainActor in
                if goals { await app.goals.reorder(desired) } else { await app.subjects.reorder(desired) }
                dropDone = true
            }
            live.harness.pump(2) { (goals ? app.goals.goals.map(\.uid) : app.subjects.subjects.map(\.uid)) == desired }
            var orders: [[String]] = []
            let observation: AnyCancellable
            if goals { observation = app.goals.$goals.sink { orders.append($0.map(\.uid)) } }
            else { observation = app.subjects.$list.sink { orders.append($0?.subjects.map(\.uid) ?? []) } }
            kill(pid, SIGCONT)
            live.harness.pump(10) { readDone && dropDone }
            live.settle()
            expect(readDone && dropDone && orders.allSatisfy { $0 == desired }, "\(goals ? "goals" : "subjects") never flashes the pre-drop order")
            observation.cancel()
        }
    }

    func liftPreviewRetainsEveryIdentityAndRestrictsFocusGroups() {
        var drag = CardPlacement()
        let order = ["star", "a", "b", "c"]
        let frames = Dictionary(uniqueKeysWithValues: order.enumerated().map { ($0.element, CGRect(x: 0, y: $0.offset * 80, width: 420, height: 72)) })
        drag.begin("a", order: order, frame: frames["a"]!, start: CGPoint(x: 30, y: 100))
        drag.move(to: CGPoint(x: 30, y: 280), frames: frames, canMove: { $0 != "star" && $1 != "star" })
        expect(drag.order == ["star", "b", "c", "a"] && drag.source == "a")
        expect(Set(drag.order) == Set(order), "a preview moves identities rather than duplicating rows")
        drag.move(to: CGPoint(x: 30, y: 0), frames: frames, canMove: { $0 != "star" && $1 != "star" })
        expect(drag.order.first == "star", "focused goals stay in the top group")
        drag = CardPlacement()
        expect(drag.source == nil && drag.order.isEmpty, "cancellation clears only presentation state")
    }

    func starredGoalsFloatAndHistoryArrowsPreserveDrafts() {
        guard let live = LiveApp() else { expect(false, "live app"); return }
        let app = live.app!
        live.run { await app.goals.reload() }
        let original = app.goals.goals
        guard let last = original.last(where: { !$0.focused }) else { expect(false, "unfocused fixture goal"); return }
        live.run { await app.goals.toggleFocus(last); await app.goals.reload() }
        live.harness.pump(5) { app.goals.goal(last.uid)?.focused == true }
        expect(app.goals.goals.firstIndex { $0.uid == last.uid }! < app.goals.goals.firstIndex { !$0.focused }!, "a starred goal floats above every unstarred goal")
        live.run { await app.goals.toggleFocus(app.goals.goal(last.uid)!); await app.goals.reload() }
        live.harness.pump(5) { app.goals.goal(last.uid)?.focused == false }
        expect(app.goals.goals.map(\.uid) == original.filter(\.focused).map(\.uid) + original.filter { !$0.focused }.map(\.uid), "unstar preserves the underlying order")

        let subject = app.subjects.subjects.first { $0.name == "Physics" }!
        let panel = SessionPanelModel(app: app, scope: .subject(uid: subject.uid, name: subject.name, color: subject.color))
        app.present(.sessions(panel))
        live.harness.pump(5) { panel.loaded }
        let window = NSWindow(contentRect: CGRect(x: 0, y: 0, width: 640, height: 800), styleMask: [.titled], backing: .buffered, defer: false)
        let router = KeyRouter(model: app, window: window)
        func down() -> Bool {
            router.handle(NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: window.windowNumber,
                                           context: nil, characters: "\u{F701}", charactersIgnoringModifiers: "\u{F701}", isARepeat: false, keyCode: 125)!)
        }
        expect(down() && panel.selectedUid == panel.rows.first?.uid)
        expect(down() && panel.selectedUid == panel.rows[1].uid)
        let selected = panel.selectedUid
        panel.draft?.end = "2026-10-23 23:59:00"
        expect(down() && panel.selectedUid == selected && panel.pendingSelection?.uid == panel.rows[2].uid, "arrows ask before abandoning edits")
        panel.confirmPendingSelection(discard: false)
        let field = NSTextField(string: "123")
        window.contentView?.addSubview(field)
        window.makeFirstResponder(field)
        expect(!down() && panel.pendingSelection == nil, "text fields keep their arrow keys")
    }
}
