import Foundation
import SwiftUI

/// Goals page state. Goals stay independent of tracked Subjects.
public final class GoalsStore: ObservableObject {
    @Published public private(set) var goals: [GoalDTO] = []
    @Published public private(set) var templates: [TemplateDTO] = []
    @Published public private(set) var showingCompleted = false
    @Published public var pulseUid: String?
    @Published public private(set) var busy: Set<String> = []

    private unowned let app: AppModel
    private var epoch = 0

    init(app: AppModel) { self.app = app }

    /// Entering Goals generates due recurring instances (idempotent), as in Qt.
    @MainActor
    public func enter() async {
        _ = await app.mutate("goals.generate_due", quiet: true)
        await reload(includeTemplates: false)
    }

    @MainActor
    public func reload(includeTemplates: Bool = false) async {
        epoch += 1
        let mine = epoch
        let response = await app.call("goals.list", ["completed": .bool(showingCompleted)], kind: .read)
        guard mine == epoch, let result = try? response.result(GoalsListDTO.self) else { return }
        goals = result.goals
        if includeTemplates || templates.isEmpty { await reloadTemplates() }
    }

    @MainActor
    public func reloadTemplates() async {
        if let result = try? await app.call("templates.list", kind: .read).result(TemplatesListDTO.self) { templates = result.templates }
    }

    public func toggleCompleted() {
        showingCompleted.toggle()
        Task { @MainActor in await reload() }
    }

    public func goal(_ uid: String) -> GoalDTO? { goals.first { $0.uid == uid } }

    // MARK: actions

    @MainActor
    public func toggleComplete(_ goal: GoalDTO) async {
        guard !busy.contains(goal.uid) else { return }
        busy.insert(goal.uid)
        defer { busy.remove(goal.uid) }
        if goal.completed {
            let response = await app.mutate("goal.uncomplete", ["goal_uid": .string(goal.uid)])
            if response.outcome == .applied {
                app.registerUndo("Complete \(goal.name)") { [weak self] in
                    await self?.app.mutate("goal.complete", ["goal_uid": .string(goal.uid)])
                }
            }
            return
        }
        let response = await app.mutate("goal.complete", ["goal_uid": .string(goal.uid)], quiet: true)
        switch response.outcome {
        case .applied, .replayed:
            app.registerUndo("Reopen \(goal.name)") { [weak self] in
                await self?.app.mutate("goal.uncomplete", ["goal_uid": .string(goal.uid)])
            }
        case .refused:
            app.show("Finish all milestones before completing this goal.", error: true)
        default:
            app.report(response)
        }
    }

    @MainActor
    public func toggleFocus(_ goal: GoalDTO) async {
        let response = await app.mutate("goal.toggle_focus", ["goal_uid": .string(goal.uid)])
        if response.outcome == .applied {
            app.registerUndo("Toggle focus") { [weak self] in
                await self?.app.mutate("goal.toggle_focus", ["goal_uid": .string(goal.uid)])
            }
        }
    }

    @MainActor
    public func reorder(_ uids: [String]) async {
        let previous = goals.map(\.uid)
        guard previous != uids else { return }
        let byUid = Dictionary(uniqueKeysWithValues: goals.map { ($0.uid, $0) })
        goals = uids.compactMap { byUid[$0] }
        let completed = showingCompleted
        let response = await app.mutate("goal.reorder", ["goal_uids": .strings(uids), "completed": .bool(completed)])
        if response.outcome == .applied {
            app.registerUndo("Restore goal order") { [weak self] in
                await self?.app.mutate("goal.reorder", ["goal_uids": .strings(previous), "completed": .bool(completed)])
            }
        } else {
            await reload()
        }
    }

    /// Digit N: focus the Nth row in current order, pulse it, then open it.
    public func openByNumber(_ number: Int) {
        guard number >= 1, number <= goals.count else { return }
        let goal = goals[number - 1]
        pulseUid = goal.uid
        let delay = app.reduceMotion ? 0 : 0.14
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, self.app.page == .goals, self.app.panels.isEmpty, self.goal(goal.uid) != nil else { return }
            self.open(goal)
        }
    }

    public func open(_ goal: GoalDTO) {
        app.present(.goal(GoalEditorModel(app: app, goal: goal)))
    }

    public func newGoal() {
        app.present(.goalForm(GoalFormModel(app: app, goal: nil)))
    }

    public func openTemplates() {
        Task { @MainActor in await reloadTemplates() }
        app.present(.templates(TemplatesModel(app: app)))
    }
}

/// The goal detail panel: milestones toggle here; digits 1–9 toggle the Nth
/// milestone once per press while this is the topmost panel.
public final class GoalEditorModel: ObservableObject {
    @Published public private(set) var goal: GoalDTO
    @Published public var message: String?
    @Published public private(set) var toggling: Set<String> = []
    @Published public var burstUid: String?
    private unowned let app: AppModel

    public let isNew = false

    init(app: AppModel, goal: GoalDTO) {
        self.app = app
        self.goal = goal
    }

    func adoptCanonical(from store: GoalsStore) {
        if let fresh = store.goal(goal.uid) { goal = fresh; return }
        Task { @MainActor in
            for completed in [false, true] {
                if let list = try? await app.call("goals.list", ["completed": .bool(completed)], kind: .read).result(GoalsListDTO.self),
                   let fresh = list.goals.first(where: { $0.uid == goal.uid }) {
                    goal = fresh
                    return
                }
            }
            message = "This goal was deleted."
        }
    }

    @MainActor
    public func toggle(_ milestone: MilestoneDTO) async {
        guard !toggling.contains(milestone.uid) else { return }
        toggling.insert(milestone.uid)
        defer { toggling.remove(milestone.uid) }
        let target = !milestone.done
        let response = await app.mutate("milestone.set_done", ["milestone_uid": .string(milestone.uid), "done": .bool(target)])
        guard response.outcome.succeeded, let result = try? response.result(GoalResult.self) else { return }
        goal = result.goal
        if target { burstUid = milestone.uid }
        app.registerUndo(target ? "Uncheck \(milestone.title)" : "Check \(milestone.title)") { [weak self] in
            await self?.app.mutate("milestone.set_done", ["milestone_uid": .string(milestone.uid), "done": .bool(!target)])
        }
    }

    public func toggleNumber(_ number: Int) {
        guard number >= 1, number <= goal.milestones.count else { return }
        let milestone = goal.milestones[number - 1]
        Task { @MainActor in await toggle(milestone) }
    }

    @MainActor
    public func reorder(_ uids: [String]) async {
        let previous = goal.milestones.map(\.uid)
        guard previous != uids else { return }
        let response = await app.mutate("milestone.reorder", ["goal_uid": .string(goal.uid), "milestone_uids": .strings(uids)])
        if let result = try? response.result(GoalResult.self) { goal = result.goal }
    }

    @MainActor
    public func toggleComplete() async -> Bool {
        if goal.completed {
            let response = await app.mutate("goal.uncomplete", ["goal_uid": .string(goal.uid)])
            if let result = try? response.result(GoalResult.self) { goal = result.goal }
            return false
        }
        let response = await app.mutate("goal.complete", ["goal_uid": .string(goal.uid)], quiet: true)
        if response.outcome.succeeded { return true }
        message = response.outcome == .refused ? "Finish all milestones before completing this goal." : (response.error ?? "Not completed.")
        return false
    }

    public func edit() {
        app.present(.goalForm(GoalFormModel(app: app, goal: goal)))
    }
}

/// Create/edit form for a goal and its complete milestone list.
public final class GoalFormModel: ObservableObject {
    public struct MilestoneDraft: Identifiable, Equatable {
        public let id = UUID()
        public var uid: String?
        public var title: String
        public var note: String
    }

    public let goal: GoalDTO?
    @Published public var name: String
    @Published public var notes: String
    @Published public var hasDeadline: Bool
    @Published public var deadline: String
    @Published public var milestones: [MilestoneDraft]
    @Published public var error: String?
    @Published public var saving = false
    private unowned let app: AppModel
    private var requestId = UUID().uuidString.lowercased()

    public var isNew: Bool { goal == nil }

    init(app: AppModel, goal: GoalDTO?) {
        self.app = app
        self.goal = goal
        name = goal?.name ?? ""
        notes = goal?.notes ?? ""
        hasDeadline = goal?.deadline != nil
        let monthAhead = Calendar.current.date(byAdding: .month, value: 1, to: Date()) ?? Date()
        deadline = goal?.deadline.map { String($0.prefix(10)) } ?? LocalTime.dayString(monthAhead)
        milestones = goal?.milestones.map { MilestoneDraft(uid: $0.uid, title: $0.title, note: $0.note) } ?? []
    }

    public func addMilestone() { milestones.append(MilestoneDraft(uid: nil, title: "", note: "")) }
    public func removeMilestone(_ id: UUID) { milestones.removeAll { $0.id == id } }

    @MainActor
    public func save() async -> Bool {
        let title = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { error = "Give the goal a title."; return false }
        if hasDeadline && LocalTime.parse(deadline) == nil { error = "Use YYYY-MM-DD for the target date."; return false }
        let entries = milestones.filter { !$0.title.trimmingCharacters(in: .whitespaces).isEmpty || $0.uid != nil }
        if entries.contains(where: { $0.title.trimmingCharacters(in: .whitespaces).isEmpty }) {
            error = "Every milestone needs a title."
            return false
        }
        saving = true
        defer { saving = false }
        var params: [String: JSONValue] = [
            "name": .string(title), "notes": .string(notes.trimmingCharacters(in: .whitespacesAndNewlines)),
            "deadline": hasDeadline ? .string(String(deadline.prefix(10))) : .null,
            "milestones": .array(entries.map { entry in
                var item: [String: JSONValue] = ["title": .string(entry.title.trimmingCharacters(in: .whitespaces)), "note": .string(entry.note)]
                if let uid = entry.uid { item["uid"] = .string(uid) }
                return .object(item)
            }),
        ]
        if let goal { params["goal_uid"] = .string(goal.uid) }
        let response = await app.mutate("goal.save", params, requestId: requestId, quiet: true)
        if response.outcome.succeeded { return true }
        if response.outcome.isSettled { requestId = UUID().uuidString.lowercased() }
        error = response.error ?? "The goal was not saved."
        return false
    }

    public func requestDelete(onDeleted: @escaping () -> Void) {
        guard let goal else { return }
        app.present(.confirm(ConfirmModel(title: "Delete Goal", message: "Delete \u{201C}\(goal.name)\u{201D} and its milestones?",
                                          confirmTitle: "Delete Goal", role: .destructive,
                                          action: { [weak self] in
            guard let self else { return }
            let response = await self.app.mutate("goal.delete", ["goal_uid": .string(goal.uid)])
            if response.outcome.succeeded { onDeleted() }
        })))
    }
}

public final class TemplatesModel: ObservableObject {
    private unowned let app: AppModel
    init(app: AppModel) { self.app = app }

    public var templates: [TemplateDTO] { app.goals.templates }

    public func edit(_ template: TemplateDTO?) {
        app.present(.templateEditor(TemplateEditorModel(app: app, template: template)))
    }

    @MainActor
    public func setActive(_ template: TemplateDTO, _ active: Bool) async {
        await app.mutate("template.set_active", ["template_uid": .string(template.uid), "active": .bool(active)])
        await app.goals.reloadTemplates()
    }

    public func requestDelete(_ template: TemplateDTO) {
        app.present(.confirm(ConfirmModel(title: "Delete Template", message: "Delete the recurring template \u{201C}\(template.title)\u{201D}? Goals it already created stay.",
                                          confirmTitle: "Delete", role: .destructive,
                                          action: { [weak self] in
            await self?.app.mutate("template.delete", ["template_uid": .string(template.uid)])
            await self?.app.goals.reloadTemplates()
        })))
    }
}

public final class TemplateEditorModel: ObservableObject {
    public let template: TemplateDTO?
    @Published public var title: String
    @Published public var notes: String
    @Published public var recurrence: String
    @Published public var weekday: Int
    @Published public var monthDay: Int
    @Published public var milestones: [GoalFormModel.MilestoneDraft]
    @Published public var error: String?
    @Published public var saving = false
    private unowned let app: AppModel
    private var requestId = UUID().uuidString.lowercased()

    public var isNew: Bool { template == nil }

    init(app: AppModel, template: TemplateDTO?) {
        self.app = app
        self.template = template
        title = template?.title ?? ""
        notes = template?.notes ?? ""
        recurrence = template?.recurrence ?? "daily"
        weekday = template?.recurrence == "weekly" ? (template?.recurrenceDay ?? 1) : 1
        monthDay = template?.recurrence == "monthly" ? (template?.recurrenceDay ?? 1) : 1
        milestones = template?.milestones.map { .init(uid: nil, title: $0.title, note: $0.note) } ?? []
    }

    @MainActor
    public func save() async -> Bool {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { error = "Give the template a title."; return false }
        saving = true
        defer { saving = false }
        let day: JSONValue = recurrence == "weekly" ? .int(weekday) : recurrence == "monthly" ? .int(monthDay) : .null
        var params: [String: JSONValue] = [
            "title": .string(trimmed), "notes": .string(notes), "recurrence": .string(recurrence), "recurrence_day": day,
            "milestones": .array(milestones.filter { !$0.title.trimmingCharacters(in: .whitespaces).isEmpty }
                .map { .object(["title": .string($0.title.trimmingCharacters(in: .whitespaces)), "note": .string($0.note)]) }),
        ]
        let command: String
        if let template { params["template_uid"] = .string(template.uid); command = "template.update" } else { command = "template.add" }
        let response = await app.mutate(command, params, requestId: requestId, quiet: true)
        if response.outcome.succeeded { await app.goals.reloadTemplates(); return true }
        if response.outcome.isSettled { requestId = UUID().uuidString.lowercased() }
        error = response.error ?? "The template was not saved."
        return false
    }
}
