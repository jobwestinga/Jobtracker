import AppKit
import Combine
@testable import JobTrackerKit

struct InteractionTests: CheckSuite {
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
