import SwiftUI
import UniformTypeIdentifiers

/// Goal detail: description, numbered milestones (digits 1–9 toggle them while
/// this panel is on top), Edit Goal and Mark Complete / Reopen.
struct GoalDetailPanel: View {
    @ObservedObject var app: AppModel
    let id: UUID
    @ObservedObject var model: GoalEditorModel
    @Environment(\.theme) private var theme
    @StateObject private var dragging = ViewState<String?>(nil)

    var body: some View {
        let goal = model.goal
        PanelChrome(goal.name, width: 480, onClose: { app.dismiss(id) }) {
            Text(goal.notes.isEmpty ? "No description yet." : goal.notes)
                .foregroundColor((goal.notes.isEmpty ? theme.textDimmed : theme.textSecondary).color)
                .fixedSize(horizontal: false, vertical: true)
            if let deadline = goal.deadline {
                Label("Target: \(String(deadline.prefix(10)))", systemImage: "scope").font(TypeRole.secondary.font)
            }
            HStack {
                Text("Milestones").font(TypeRole.section.font)
                Spacer()
                Text(goal.totalCount > 0 ? "\(goal.doneCount) / \(goal.totalCount) complete" : "No milestones")
                    .font(TypeRole.secondary.font).foregroundColor(theme.textSecondary.color)
            }
            if goal.milestones.isEmpty {
                Text("No milestones yet. Use Edit Goal to add them.").italic().foregroundColor(theme.textDimmed.color)
            }
            ForEach(Array(goal.milestones.enumerated()), id: \.element.uid) { index, milestone in
                MilestoneRow(number: index < 9 ? index + 1 : nil, milestone: milestone,
                             busy: model.toggling.contains(milestone.uid), burst: model.burstUid == milestone.uid) {
                    Task { @MainActor in await model.toggle(milestone) }
                }
                .onChange(of: model.burstUid) { _ in
                    if model.burstUid != nil { DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { model.burstUid = nil } }
                }
                .onDrag { dragging.value = milestone.uid; return NSItemProvider(object: milestone.uid as NSString) }
                .onDrop(of: [UTType.text], delegate: ReorderDropDelegate(
                    target: milestone.uid, ordered: goal.milestones.map(\.uid), dragging: $dragging.value,
                    commit: { order in Task { @MainActor in await model.reorder(order) } }))
            }
            InlineMessage(text: model.message)
        } footer: {
            Button("Edit Goal") { model.edit() }.actionStyle(.secondary)
            Spacer()
            Button("Close") { app.dismiss(id) }.actionStyle(.secondary).keyboardShortcut(.cancelAction)
            Button(goal.completed ? "Reopen Goal" : "Mark Complete") {
                Task { @MainActor in if await model.toggleComplete() { app.dismiss(id) } }
            }
            .actionStyle(.primary)
            .disabled(!goal.completed && !goal.canComplete)
            .help(!goal.completed && !goal.canComplete ? "Finish all milestones before completing this goal." : "")
        }
    }
}

struct MilestoneRow: View {
    let number: Int?
    let milestone: MilestoneDTO
    let busy: Bool
    let burst: Bool
    let toggle: () -> Void
    @Environment(\.theme) private var theme

    var body: some View {
        Button(action: toggle) {
            HStack(alignment: .top, spacing: 10) {
                ZStack {
                    Circle().strokeBorder((milestone.done ? theme.accentGreen : theme.textDimmed).color, lineWidth: 1.4)
                        .background(Circle().fill(milestone.done ? theme.accentGreen.color : .clear))
                    if milestone.done { Image(systemName: "checkmark").font(.system(size: 9, weight: .bold)).foregroundColor(theme.bgPrimary.color) }
                }
                .frame(width: 18, height: 18)
                .scaleEffect(burst ? 1.25 : 1)
                .animation(.spring(response: 0.25, dampingFraction: 0.5), value: burst)
                VStack(alignment: .leading, spacing: 2) {
                    Text(milestone.title).strikethrough(milestone.done).foregroundColor((milestone.done ? theme.textSecondary : theme.textPrimary).color)
                    if !milestone.note.isEmpty { Text(milestone.note).font(TypeRole.caption.font).foregroundColor(theme.textSecondary.color) }
                }
                Spacer()
                if let number {
                    Text("\(number)").font(.system(size: 10, weight: .bold).monospacedDigit()).foregroundColor(theme.textSecondary.color)
                        .frame(width: 18, height: 18).background(RoundedRectangle(cornerRadius: 4).fill(theme.bgTertiary.color))
                }
            }
            .padding(10)
            .frame(minHeight: 52)
            .card(radius: Radius.control)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(busy)
        .accessibilityLabel("\(number.map { "Milestone \($0): " } ?? "")\(milestone.title)")
        .accessibilityValue(milestone.done ? "done" : "not done")
    }
}

struct GoalFormPanel: View {
    @ObservedObject var app: AppModel
    let id: UUID
    @ObservedObject var model: GoalFormModel
    @Environment(\.theme) private var theme

    var body: some View {
        PanelChrome(model.isNew ? "New Goal" : "Edit Goal", width: 500, busy: model.saving, onClose: { app.dismiss(id) }) {
            Text(model.isNew ? "Define a new outcome" : "Edit the outcome").font(TypeRole.cardTitle.font)
            FieldLabel("Title")
            TextField("e.g. Become a FIDE master", text: $model.name).textFieldStyle(FieldStyle()).accessibilityLabel("Goal title")
            FieldLabel("Description")
            TextField("Why this matters and what success looks like \u{2026}", text: $model.notes).textFieldStyle(FieldStyle())
                .accessibilityLabel("Goal description")
            HStack(spacing: 8) {
                Toggle("Target date", isOn: $model.hasDeadline)
                TextField("YYYY-MM-DD", text: $model.deadline).textFieldStyle(FieldStyle()).frame(width: 140).disabled(!model.hasDeadline)
                    .accessibilityLabel("Goal target date")
            }
            HStack {
                FieldLabel("Milestones")
                Spacer()
                Button("+ Add milestone") { model.addMilestone() }.actionStyle(.secondary, compact: true)
            }
            ForEach($model.milestones) { $entry in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        TextField("Milestone title \u{2026}", text: $entry.title).textFieldStyle(FieldStyle())
                        Button("Remove") { model.removeMilestone(entry.id) }.actionStyle(.quiet, compact: true)
                    }
                    TextField("Milestone description (optional) \u{2026}", text: $entry.note).textFieldStyle(FieldStyle())
                }
                .padding(8)
                .background(RoundedRectangle(cornerRadius: Radius.control).fill(theme.bgSecondary.color))
            }
            InlineMessage(text: model.error)
        } footer: {
            if !model.isNew {
                Button("Delete Goal") {
                    model.requestDelete { app.dismiss(id) ; if let top = app.topPanel, case .goal = top.kind { app.dismissTop() } }
                }
                .actionStyle(.destructive)
            }
            Spacer()
            Button("Cancel") { app.dismiss(id) }.actionStyle(.secondary).keyboardShortcut(.cancelAction)
            Button("Save Goal") { Task { @MainActor in if await model.save() { app.dismiss(id) } } }
                .actionStyle(.primary).keyboardShortcut(.defaultAction).disabled(model.saving)
        }
    }
}

struct TemplatesPanel: View {
    @ObservedObject var app: AppModel
    let id: UUID
    @ObservedObject var model: TemplatesModel
    @ObservedObject var goals: GoalsStore
    @Environment(\.theme) private var theme

    var body: some View {
        PanelChrome("Recurring Templates", width: 520, onClose: { app.dismiss(id) }) {
            Text("Templates add a normal goal to the top of the list once per day, week or month. Earlier unfinished goals stay visible.")
                .font(TypeRole.secondary.font).foregroundColor(theme.textSecondary.color).fixedSize(horizontal: false, vertical: true)
            if goals.templates.isEmpty {
                Text("No templates yet.").italic().foregroundColor(theme.textDimmed.color)
            }
            ForEach(goals.templates) { template in
                HStack(spacing: 8) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(template.title).font(TypeRole.cardTitle.font).lineLimit(1)
                        Text(schedule(template) + (template.active ? "" : " \u{00B7} Disabled") + (template.milestones.isEmpty ? "" : " \u{00B7} \(template.milestones.count) milestones"))
                            .font(TypeRole.caption.font).foregroundColor(theme.textSecondary.color)
                    }
                    Spacer()
                    Button("Edit") { model.edit(template) }.actionStyle(.quiet, compact: true)
                    Button(template.active ? "Disable" : "Enable") { Task { @MainActor in await model.setActive(template, !template.active) } }
                        .actionStyle(.quiet, compact: true)
                    Button("Delete") { model.requestDelete(template) }.actionStyle(.quiet, compact: true)
                }
                .padding(10)
                .card(radius: Radius.control, dimmed: !template.active)
            }
        } footer: {
            Button("New") { model.edit(nil) }.actionStyle(.primary)
            Spacer()
            Button("Close") { app.dismiss(id) }.actionStyle(.secondary).keyboardShortcut(.cancelAction)
        }
    }

    private func schedule(_ template: TemplateDTO) -> String {
        switch template.recurrence {
        case "weekly":
            let names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            return "Weekly on \(names[max(1, min(7, template.recurrenceDay ?? 1)) - 1])"
        case "monthly": return "Monthly on day \(template.recurrenceDay ?? 1)"
        default: return "Daily"
        }
    }
}

struct TemplateEditorPanel: View {
    @ObservedObject var app: AppModel
    let id: UUID
    @ObservedObject var model: TemplateEditorModel
    @Environment(\.theme) private var theme

    var body: some View {
        PanelChrome(model.isNew ? "New Template" : "Edit Template", width: 500, busy: model.saving, onClose: { app.dismiss(id) }) {
            FieldLabel("Title")
            TextField("e.g. Weekly review {date}", text: $model.title).textFieldStyle(FieldStyle())
            Text("Placeholders such as {date} are filled in when each goal is created.").font(TypeRole.caption.font).foregroundColor(theme.textDimmed.color)
            FieldLabel("Description")
            TextField("Optional", text: $model.notes).textFieldStyle(FieldStyle())
            FieldLabel("Repeats")
            ChoiceRow([("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly")], selection: model.recurrence) { model.recurrence = $0 }
            if model.recurrence == "weekly" {
                ChoiceRow(Array(zip(1...7, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])).map { ($0.0, $0.1) }, selection: model.weekday, compact: true) { model.weekday = $0 }
            } else if model.recurrence == "monthly" {
                Stepper("Day \(model.monthDay) of the month", value: $model.monthDay, in: 1...31)
            }
            HStack {
                FieldLabel("Milestones")
                Spacer()
                Button("+ Add milestone") { model.milestones.append(.init(uid: nil, title: "", note: "")) }.actionStyle(.secondary, compact: true)
            }
            ForEach($model.milestones) { $entry in
                HStack {
                    TextField("Milestone title \u{2026}", text: $entry.title).textFieldStyle(FieldStyle())
                    Button("Remove") { model.milestones.removeAll { $0.id == entry.id } }.actionStyle(.quiet, compact: true)
                }
            }
            InlineMessage(text: model.error)
        } footer: {
            Spacer()
            Button("Cancel") { app.dismiss(id) }.actionStyle(.secondary).keyboardShortcut(.cancelAction)
            Button("Save Template") { Task { @MainActor in if await model.save() { app.dismiss(id) } } }
                .actionStyle(.primary).keyboardShortcut(.defaultAction).disabled(model.saving)
        }
    }
}

struct SettingsPanel: View {
    @ObservedObject var app: AppModel
    let id: UUID
    @ObservedObject var model: SettingsModel
    @Environment(\.theme) private var theme

    var body: some View {
        PanelChrome("Settings", width: 460, busy: model.saving || model.dataBusy, onClose: { app.dismiss(id) }) {
            Text("Theme").font(TypeRole.section.font)
            CompactSelector("Appearance theme", options: model.options.map { ($0, $0.name) }, selection: model.selected) { model.preview($0) }
            Text(model.selected.description).font(TypeRole.secondary.font).foregroundColor(theme.textSecondary.color)
            Toggle("Reduce motion", isOn: $model.reduceMotion)
                .help("Pause animated backgrounds and decorative feedback. Timer clocks keep updating.")
            Divider()
            Text("Day starts at").font(TypeRole.section.font)
            HStack {
                CompactSelector("Tracking day start", options: model.dayStartOptions.map { ($0, $0) }, selection: model.dayStart) { model.dayStart = $0 }
                Text("late-night work counts on the day it started").font(TypeRole.caption.font).foregroundColor(theme.textSecondary.color)
            }
            Divider()
            Text("Sync").font(TypeRole.section.font)
            Toggle("Sync with my server", isOn: $model.syncEnabled)
            TextField("https://your-server:8443", text: $model.serverUrl).textFieldStyle(FieldStyle()).accessibilityLabel("Server address")
            SecureField(app.sync.hasToken ? "Access token saved \u{2014} leave empty to keep it" : "Access token", text: $model.token)
                .textFieldStyle(FieldStyle()).accessibilityLabel("Access token")
            HStack {
                Button("Sync Now") { Task { @MainActor in await model.syncNow() } }.actionStyle(.secondary)
                Text(app.sync.title).font(TypeRole.caption.font).foregroundColor(theme.textSecondary.color).lineLimit(2)
            }
            Divider()
            Text("Data").font(TypeRole.section.font)
            HStack {
                Button("Export Backup") { Task { @MainActor in await model.exportBackup(window: NSApp.keyWindow) } }.actionStyle(.secondary).disabled(model.dataBusy)
                Button("Import Backup") { Task { @MainActor in await model.importBackup(window: NSApp.keyWindow) } }.actionStyle(.secondary).disabled(model.dataBusy)
            }
            InlineMessage(text: model.message, isError: false)
        } footer: {
            Spacer()
            Button("Cancel") { app.dismiss(id) }.actionStyle(.secondary).keyboardShortcut(.cancelAction)
            Button("Save") { Task { @MainActor in if await model.save() { app.dismiss(id) } } }
                .actionStyle(.primary).keyboardShortcut(.defaultAction).disabled(model.saving)
        }
    }
}

struct GraphSettingsPanel: View {
    @ObservedObject var app: AppModel
    let id: UUID
    @ObservedObject var model: GraphSettingsModel
    @Environment(\.theme) private var theme

    var body: some View {
        PanelChrome("Graph Settings", width: 460, busy: model.saving, onClose: { app.dismiss(id) }) {
            Text("Time Range").font(TypeRole.section.font)
            ChoiceRow([("weeks", "Weeks"), ("months", "Months"), ("year", "Year"), ("all", "All")], selection: model.range) {
                model.range = $0
                model.useCustom = false
            }
            Text("Shortcuts: W, M, Y and A on the Graphs page.").font(TypeRole.caption.font).foregroundColor(theme.textDimmed.color)
            Toggle("Use a custom date range", isOn: $model.useCustom)
            if model.useCustom {
                HStack {
                    Text("From:"); TextField("YYYY-MM-DD", text: $model.customStart).textFieldStyle(FieldStyle()).frame(width: 130)
                    Text("To:"); TextField("YYYY-MM-DD", text: $model.customEnd).textFieldStyle(FieldStyle()).frame(width: 130)
                }
            }
            Text("Bar size").font(TypeRole.section.font)
            ChoiceRow([("auto", "Automatic"), ("daily", "Days"), ("weekly", "Weeks"), ("monthly", "Months")], selection: model.grouping, compact: true) { model.grouping = $0 }
            Text("View Mode").font(TypeRole.section.font)
            ChoiceRow([("bar", "Stacked Bar"), ("agenda", "Agenda")], selection: model.mode) { model.mode = $0 }
            Toggle("Fit Width to Screen (No horizontal scrolling)", isOn: $model.fitWidth)
            Toggle("Auto-fit time bounds to actual work", isOn: $model.autofitHours)
            Text("Visible Hour Range (Agenda)").font(TypeRole.section.font)
            HStack {
                CompactSelector("From hour", options: (0...26).map { ($0, AgendaLayout.hourLabel($0)) }, selection: model.hourStart) { model.hourStart = $0 }
                Text("to")
                CompactSelector("To hour", options: (1...27).map { ($0, AgendaLayout.hourLabel($0)) }, selection: model.hourEnd) { model.hourEnd = $0 }
            }
            .disabled(model.autofitHours)
            InlineMessage(text: model.error)
        } footer: {
            Spacer()
            Button("Cancel") { app.dismiss(id) }.actionStyle(.secondary).keyboardShortcut(.cancelAction)
            Button("Apply") { Task { @MainActor in if await model.save() { app.dismiss(id) } } }
                .actionStyle(.primary).keyboardShortcut(.defaultAction).disabled(model.saving)
        }
    }
}
