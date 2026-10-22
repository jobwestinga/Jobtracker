import AppKit

/// Window-level keys with the Qt window's ownership rules.
///
/// * Text input keeps its keys (digits, arrows, letters).
/// * Escape closes the innermost panel, then weight fullscreen, then the
///   Completed/Archived views.
/// * With a panel open, only the topmost goal panel receives digits (toggling
///   its numbered milestones once per press); nothing below a panel reacts.
/// * Without a panel: Left/Right change pages, Up/Down choose the graph mode
///   and W/M/Y/A the graph range on Graphs, digits open goals on Goals.
/// * Held keys never repeat these actions.
final class KeyRouter {
    private weak var model: AppModel?
    private weak var window: NSWindow?
    private var monitor: Any?

    init(model: AppModel, window: NSWindow) {
        self.model = model
        self.window = window
        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self else { return event }
            return self.handle(event) ? nil : event
        }
    }

    deinit { if let monitor { NSEvent.removeMonitor(monitor) } }

    private var textInputFocused: Bool {
        guard let responder = window?.firstResponder else { return false }
        return responder is NSTextView || responder is NSTextField
    }

    /// Returns true when the event was consumed.
    func handle(_ event: NSEvent) -> Bool {
        guard let model, event.window === window else { return false }
        let modifiers = event.modifierFlags.intersection([.command, .control, .option])
        if !modifiers.isEmpty { return false }
        let key = event.charactersIgnoringModifiers?.lowercased() ?? ""

        if event.keyCode == 53 {  // Escape
            if event.isARepeat { return true }
            if textInputFocused && model.panels.isEmpty {
                window?.makeFirstResponder(nil)
                return true
            }
            model.escape()
            return true
        }
        if textInputFocused { return false }

        let digit = Int(key).flatMap { (1...9).contains($0) ? $0 : nil }
        if let top = model.topPanel {
            guard let digit else { return false }
            if !event.isARepeat, case .goal(let goalModel) = top.kind { goalModel.toggleNumber(digit) }
            return true
        }
        if case .ready = model.connection {} else { return false }

        switch event.keyCode {
        case 123, 124:  // Left, Right
            if !event.isARepeat { model.navigate(event.keyCode == 123 ? -1 : 1) }
            return true
        case 126, 125:  // Up, Down
            guard model.page == .graphs else { return false }
            if !event.isARepeat { model.graphs.setMode(event.keyCode == 126 ? "bar" : "agenda") }
            return true
        default:
            break
        }
        if let digit {
            if !event.isARepeat && model.page == .goals { model.goals.openByNumber(digit) }
            return true
        }
        if model.page == .graphs, let preset = ["w": "weeks", "m": "months", "y": "year", "a": "all"][key] {
            if !event.isARepeat { model.graphs.setRange(preset) }
            return true
        }
        return false
    }
}
