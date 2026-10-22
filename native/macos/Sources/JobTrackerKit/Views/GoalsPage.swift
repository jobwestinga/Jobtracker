import SwiftUI
import UniformTypeIdentifiers

struct GoalsPage: View {
    @ObservedObject var app: AppModel
    @ObservedObject var store: GoalsStore
    @Environment(\.theme) private var theme
    @StateObject private var dragging = ViewState<String?>(nil)

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            PageHeader(store.showingCompleted ? "Completed Goals" : "Goals") {
                Button("Templates") { store.openTemplates() }.actionStyle(.secondary)
                Button(store.showingCompleted ? "Back to Active" : "Completed") { store.toggleCompleted() }
                    .actionStyle(store.showingCompleted ? .primary : .secondary)
                if !store.showingCompleted {
                    Button("+ Goal") { store.newGoal() }.actionStyle(.primary)
                }
            }
            if store.goals.isEmpty {
                Text(store.showingCompleted ? "No completed goals yet." : "No goals yet - click + Goal to define an outcome.")
                    .italic().foregroundColor(theme.textDimmed.color)
                    .frame(maxWidth: .infinity).padding(.vertical, 26)
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(spacing: 8) {
                        ForEach(Array(store.goals.enumerated()), id: \.element.uid) { index, goal in
                            GoalCard(app: app, store: store, goal: goal, number: index < 9 ? index + 1 : nil)
                                .onDrag { dragging.value = goal.uid; return NSItemProvider(object: goal.uid as NSString) }
                                .onDrop(of: [UTType.text], delegate: ReorderDropDelegate(
                                    target: goal.uid, ordered: store.goals.map(\.uid), dragging: $dragging.value,
                                    commit: { order in Task { @MainActor in await store.reorder(order) } }))
                        }
                    }
                    .padding(.bottom, 8)
                }
            }
        }
    }
}

struct GoalCard: View {
    @ObservedObject var app: AppModel
    @ObservedObject var store: GoalsStore
    let goal: GoalDTO
    let number: Int?
    @Environment(\.theme) private var theme
    @StateObject private var hovering = ViewState(false)

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            if let number {
                Text("\(number)")
                    .font(.system(size: 10, weight: .bold).monospacedDigit())
                    .foregroundColor(theme.textSecondary.color)
                    .frame(width: 20, height: 20)
                    .background(RoundedRectangle(cornerRadius: 5, style: .continuous).fill(theme.bgTertiary.color))
                    .accessibilityHidden(true)
            }
            CompletionButton(goal: goal) { Task { @MainActor in await store.toggleComplete(goal) } }
            VStack(alignment: .leading, spacing: 3) {
                Text(goal.name).font(TypeRole.cardTitle.font).lineLimit(1).truncationMode(.tail)
                if goal.totalCount > 0 || goal.completed {
                    HStack(spacing: 8) {
                        if goal.totalCount > 0 {
                            ProgressBar(fraction: Double(goal.doneCount) / Double(max(1, goal.totalCount)))
                                .frame(width: 130, height: 4)
                        }
                        Text(goal.completed
                             ? (goal.totalCount > 0 ? "Completed \u{00B7} \(goal.doneCount)/\(goal.totalCount) milestones" : "Completed")
                             : "\(goal.doneCount)/\(goal.totalCount) milestones")
                            .font(TypeRole.caption.font).foregroundColor(theme.textSecondary.color)
                    }
                }
                if let deadline = goal.deadline {
                    Label("Target: \(String(deadline.prefix(10)))", systemImage: "scope")
                        .font(TypeRole.caption.font).foregroundColor(theme.textSecondary.color)
                }
                if !goal.notes.isEmpty {
                    Text(goal.notes).font(TypeRole.caption.font).foregroundColor(theme.textSecondary.color)
                        .lineLimit(1).truncationMode(.tail)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Button { Task { @MainActor in await store.toggleFocus(goal) } } label: {
                Image(systemName: goal.focused ? "star.fill" : "star")
                    .foregroundColor((goal.focused ? theme.accent : theme.textSecondary).color)
                    .frame(width: 26, height: 26)
            }
            .buttonStyle(.plain)
            .help(goal.focused ? "Weekly focus (click to clear)" : "Mark as this week's focus")
            .accessibilityLabel(goal.focused ? "Clear weekly focus" : "Mark weekly focus")
            Button { app.present(.goalForm(GoalFormModel(app: app, goal: goal))) } label: {
                Image(systemName: "pencil").foregroundColor(theme.textSecondary.color).frame(width: 26, height: 26)
            }
            .buttonStyle(.plain)
            .help("Edit goal")
            .accessibilityLabel("Edit \(goal.name)")
        }
        .padding(.horizontal, 14).padding(.vertical, 9)
        .frame(minHeight: Density.goalRowMin)
        .card(radius: theme.taskRadius, highlighted: goal.focused, hovering: hovering.value)
        .opacity(goal.completed && !store.showingCompleted ? 0.7 : 1)
        .contentShape(Rectangle())
        .onHover { hovering.value = $0 }
        .onTapGesture { store.open(goal) }
        .modifier(PulseModifier(active: store.pulseUid == goal.uid, reduceMotion: app.reduceMotion) { store.pulseUid = nil })
        .contextMenu {
            Button("Open Goal") { store.open(goal) }
            Button("Edit Goal") { app.present(.goalForm(GoalFormModel(app: app, goal: goal))) }
            Button(goal.completed ? "Reopen Goal" : "Mark Complete") { Task { @MainActor in await store.toggleComplete(goal) } }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(number.map { "Goal \($0): " } ?? "")\(goal.name)")
    }
}

struct CompletionButton: View {
    let goal: GoalDTO
    let action: () -> Void
    @Environment(\.theme) private var theme

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle().strokeBorder((goal.completed ? theme.accentGreen : theme.textDimmed).color, lineWidth: 1.4)
                    .background(Circle().fill(goal.completed ? theme.accentGreen.color : .clear))
                Image(systemName: "checkmark").font(.system(size: 10, weight: .bold))
                    .foregroundColor((goal.completed ? theme.bgPrimary : (goal.canComplete ? theme.textSecondary : theme.textDimmed)).color)
            }
            .frame(width: 22, height: 22)
        }
        .buttonStyle(.plain)
        .help(goal.completed ? "Reopen goal" : (goal.canComplete ? "Mark complete" : "Finish all milestones before completing."))
        .accessibilityLabel(goal.completed ? "Reopen \(goal.name)" : "Complete \(goal.name)")
    }
}

struct ProgressBar: View {
    let fraction: Double
    @Environment(\.theme) private var theme
    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Capsule().fill(theme.bgTertiary.color)
                Capsule().fill(theme.textSecondary.color).frame(width: proxy.size.width * max(0, min(1, fraction)))
            }
        }
        .accessibilityValue("\(Int((fraction * 100).rounded())) percent")
    }
}
