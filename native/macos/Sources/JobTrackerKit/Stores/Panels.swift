import Foundation
import SwiftUI

/// One window-local panel in the overlay stack. The topmost panel owns
/// Escape and digit keys; panels below it receive nothing.
public struct PanelEntry: Identifiable {
    public let id = UUID()
    public let kind: Kind

    public enum Kind {
        case confirm(ConfirmModel)
        case subjectEditor(SubjectEditorModel)
        case deleteSubject(DeleteSubjectModel)
        case sessions(SessionPanelModel)
        case customDuration(CustomDurationModel)
        case goal(GoalEditorModel)
        case goalForm(GoalFormModel)
        case templates(TemplatesModel)
        case templateEditor(TemplateEditorModel)
        case settings(SettingsModel)
        case graphSettings(GraphSettingsModel)
        case recovery(RecoveryModel)

        var dismissalBlocked: Bool {
            if case .recovery(let model) = self { return model.saving }
            return false
        }

        func willDismiss() {
            switch self {
            case .settings(let model): model.cancelPreview()
            case .recovery(let model): model.dismissed()
            default: break
            }
        }

        public var title: String {
            switch self {
            case .confirm(let m): return m.title
            case .subjectEditor(let m): return m.isNew ? "New Subject" : "Edit Subject"
            case .deleteSubject: return "Delete Subject"
            case .sessions(let m): return m.title
            case .customDuration: return "Custom Duration"
            case .goal(let m): return m.goal.name
            case .goalForm(let m): return m.isNew ? "New Goal" : "Edit Goal"
            case .templates: return "Recurring Templates"
            case .templateEditor(let m): return m.isNew ? "New Template" : "Edit Template"
            case .settings: return "Settings"
            case .graphSettings: return "Graph Settings"
            case .recovery: return "Unfinished Session"
            }
        }
    }
}

public enum ActionRole { case primary, secondary, quiet, destructive }

public final class ConfirmModel: ObservableObject {
    public let title: String
    public let message: String
    public let confirmTitle: String
    public let role: ActionRole
    public let action: () async -> Void
    @Published public var running = false

    public init(title: String, message: String, confirmTitle: String, role: ActionRole, action: @escaping () async -> Void) {
        self.title = title
        self.message = message
        self.confirmTitle = confirmTitle
        self.role = role
        self.action = action
    }
}

/// New or existing subject draft. Colour choice keeps the saved value exactly.
public final class SubjectEditorModel: ObservableObject {
    public let subject: SubjectDTO?
    public let activeColors: [String]
    public let suggestions: [String]
    @Published public var name: String
    @Published public var color: String
    @Published public var notes: String
    @Published public var error: String?
    @Published public var saving = false
    private unowned let app: AppModel
    private var requestId = UUID().uuidString.lowercased()

    public var isNew: Bool { subject == nil }

    init(app: AppModel, subject: SubjectDTO?, activeColors: [String], suggestions: [String]) {
        self.app = app
        self.subject = subject
        self.activeColors = activeColors
        self.suggestions = suggestions
        name = subject?.name ?? ""
        notes = subject?.notes ?? ""
        let presets = DesignTokens.shared.roles.subjectColors
        color = subject?.color ?? (activeColors.isEmpty ? presets[0] : (suggestions.first ?? presets[0]))
    }

    /// The eight preset hues, plus the current colour when it is custom.
    public var swatches: [String] { DesignTokens.shared.roles.subjectColors }
    public var currentIsCustom: Bool { !swatches.contains { $0.caseInsensitiveCompare(color) == .orderedSame } }

    @MainActor
    public func save() async -> Bool {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { error = "Subject name cannot be empty."; return false }
        saving = true
        defer { saving = false }
        var params: [String: JSONValue] = ["name": .string(trimmed), "color": .string(color),
                                           "notes": .string(notes.trimmingCharacters(in: .whitespacesAndNewlines))]
        let command: String
        if let subject {
            params["subject_uid"] = .string(subject.uid)
            command = "subject.update"
        } else {
            command = "subject.add"
        }
        let response = await app.mutate(command, params, requestId: requestId, quiet: true)
        switch response.outcome {
        case .applied, .replayed:
            return true
        case .refused:
            error = "A subject named \u{201C}\(trimmed)\u{201D} already exists."
        case .conflict:
            requestId = UUID().uuidString.lowercased()
            error = response.error
        case .unavailable:
            error = response.error  // keep identity: a retry resolves the same request
        default:
            requestId = UUID().uuidString.lowercased()
            error = response.error ?? "The subject was not saved."
        }
        return false
    }

    /// Any edit after a settled failure is a new request.
    public func draftChanged() {
        if error != nil && !(app.engine.unresolved.contains(requestId)) {
            requestId = UUID().uuidString.lowercased()
            error = nil
        }
    }
}

public final class DeleteSubjectModel: ObservableObject {
    public let subject: SubjectDTO
    public let summary: DeletionSummaryDTO
    init(subject: SubjectDTO, summary: DeletionSummaryDTO) {
        self.subject = subject
        self.summary = summary
    }
}

/// Recovery decision for a resumed session with a large heartbeat gap.
/// End times come from the engine's snapshot; the client never computes them.
public final class RecoveryModel: ObservableObject {
    public enum Choice: String, CaseIterable { case last, now, customEnd = "custom_end", customLength = "custom_length" }
    public let snapshot: TimerDTO
    @Published public var choice: Choice = .last
    @Published public var customEnd: String
    @Published public var customHours = 0
    @Published public var customMinutes = 0
    @Published public var error: String?
    @Published public var saving = false
    private unowned let app: AppModel
    private let requestId = UUID().uuidString.lowercased()

    init(app: AppModel, timer: TimerDTO) {
        self.app = app
        self.snapshot = timer
        customEnd = LocalTime.format(timer.recovery?.lastActive, "yyyy-MM-dd HH:mm")
        let seconds = timer.recovery?.durationIfLastSeconds ?? 0
        customHours = seconds / 3600
        customMinutes = (seconds % 3600) / 60
    }

    /// The engine applies `end_time_for_choice` against the prompt's moment,
    /// clamping custom values exactly like the Qt dialog.
    @MainActor
    public func resolve() async -> Bool {
        guard let recovery = snapshot.recovery, let uid = snapshot.active?.uid else { return true }
        var params: [String: JSONValue] = ["session_uid": .string(uid), "choice": .string(choice.rawValue), "prompt_now": .string(recovery.now)]
        switch choice {
        case .customEnd:
            guard let parsed = LocalTime.parse(customEnd) else { error = "Enter the end as YYYY-MM-DD HH:MM."; return false }
            params["custom_end"] = .string(LocalTime.isoString(parsed))
        case .customLength:
            params["custom_length_seconds"] = .int(max(0, customHours) * 3600 + max(0, customMinutes) * 60)
        default:
            break
        }
        saving = true
        defer { saving = false }
        let response = await app.mutate("recovery.resolve", params, requestId: requestId, quiet: true)
        if response.outcome.succeeded {
            if let timer = try? response.result(TimerDTO.self) { app.adopt(timer: timer) }
            return true
        }
        error = response.error ?? "The session could not be closed at that time."
        return false
    }

    /// Dismissing keeps the session running (non-destructive), like Qt.
    func dismissed() {
        Task { @MainActor in _ = await app.call("recovery.dismiss", kind: .action) }
    }
}
