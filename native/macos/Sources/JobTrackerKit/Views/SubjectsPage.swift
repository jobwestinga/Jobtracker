import SwiftUI
import UniformTypeIdentifiers

struct SubjectsPage: View {
    @ObservedObject var app: AppModel
    @ObservedObject var store: SubjectsStore
    @Environment(\.theme) private var theme

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            PageHeader("Subjects") {
                if !store.showingArchived {
                    Button("+ Subject") { store.openEditor() }.actionStyle(.primary)
                }
                Button(store.showingArchived ? "Back to Active" : "Archived") { store.toggleArchived() }
                    .actionStyle(store.showingArchived ? .primary : .secondary)
            }
            if !store.showingArchived {
                TimerArea(app: app, store: store)
            }
            HStack {
                Text(store.showingArchived ? "Archived Subjects" : "Tracked Time")
                    .font(TypeRole.section.font)
                Spacer()
                CompactSelector("Tracked time range", options: SubjectsStore.filters.map { ($0, $0) }, selection: store.filter) { store.setFilter($0) }
            }
            if store.list != nil && store.subjects.isEmpty {
                Text(store.showingArchived ? "No archived subjects." : "No subjects yet - click + Subject to begin.")
                    .italic()
                    .foregroundColor(theme.textDimmed.color)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 26)
                Spacer()
            } else {
                SubjectList(app: app, store: store)
            }
        }
    }
}

/// Slim idle bar with Resume and today's total; active hero with clock and Stop.
struct TimerArea: View {
    @ObservedObject var app: AppModel
    @ObservedObject var store: SubjectsStore
    @Environment(\.theme) private var theme

    var body: some View {
        Group {
            if let active = app.timer.active, let subject = app.timer.subject {
                activeView(active: active, subject: subject)
            } else {
                idleView
            }
        }
        .animation(app.reduceMotion ? nil : .easeOut(duration: 0.16), value: app.timer.active?.uid)
    }

    private var idleView: some View {
        HStack(spacing: 10) {
            Circle().fill(theme.textDimmed.color).frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 1) {
                Text("Ready to track").font(.system(size: 13, weight: .semibold))
                if (store.list?.todaySeconds ?? 0) > 0 {
                    Text("Today: \(DurationText.short(store.list?.todaySeconds ?? 0))").font(TypeRole.caption.font).foregroundColor(theme.textSecondary.color)
                } else {
                    Text("Click a subject to start").font(TypeRole.caption.font).foregroundColor(theme.textSecondary.color)
                }
            }
            Spacer()
            if let resume = store.resumeSubject {
                Button { Task { @MainActor in await store.resume() } } label: {
                    Text("\u{21BA}  Resume \(resume.name.count <= 18 ? resume.name : String(resume.name.prefix(17)) + "\u{2026}")")
                }
                .actionStyle(.secondary)
                .help("Resume tracking \(resume.name)")
                .accessibilityLabel("Resume tracking \(resume.name)")
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
        .frame(minHeight: 64)
        .background(RoundedRectangle(cornerRadius: theme.radius, style: .continuous).fill(theme.cardBackground.color))
        .overlay(RoundedRectangle(cornerRadius: theme.radius, style: .continuous).strokeBorder(theme.border.color, lineWidth: 1))
    }

    private func activeView(active: SessionDTO, subject: SubjectDTO) -> some View {
        HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 3) {
                Text("CURRENTLY TRACKING").font(.system(size: 11, weight: .semibold)).kerning(0.8).foregroundColor(theme.textSecondary.color)
                Text(subject.name).font(.system(size: 17, weight: .bold)).lineLimit(1).truncationMode(.tail)
            }
            Spacer(minLength: 8)
            TimelineView(.periodic(from: Date(), by: 1)) { context in
                Text(DurationText.clock(app.elapsed(at: context.date)))
                    .font(TypeRole.duration.font)
                    .foregroundColor(theme.accent.color)
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
                    .accessibilityLabel("Elapsed \(DurationText.clock(app.elapsed(at: context.date)))")
            }
            Button { Task { @MainActor in await store.stop() } } label: {
                Text("\u{25A0}  Stop").font(.system(size: 14, weight: .semibold))
            }
            .buttonStyle(StopButtonStyle())
            .accessibilityLabel("Stop tracking \(subject.name)")
        }
        .padding(.horizontal, 20).padding(.vertical, 14)
        .frame(minHeight: 96)
        .background(RoundedRectangle(cornerRadius: theme.radius, style: .continuous).fill(theme.timerBackground.color))
        .overlay(RoundedRectangle(cornerRadius: theme.radius, style: .continuous).strokeBorder(theme.timerBorder.color, lineWidth: theme.timerBorderWidth))
    }
}

struct StopButtonStyle: ButtonStyle {
    @Environment(\.theme) private var theme
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundColor(.white)
            .padding(.horizontal, 22).frame(height: 40)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(theme.accentRed.scaledValue(configuration.isPressed ? 0.8 : 1).color))
    }
}

/// S1 calm cards: separate blocks, colour marker, neutral text, quiet actions.
struct SubjectList: View {
    @ObservedObject var app: AppModel
    @ObservedObject var store: SubjectsStore
    @StateObject private var dragging = ViewState<String?>(nil)

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 8) {
                ForEach(store.subjects) { subject in
                    SubjectCard(app: app, store: store, subject: subject)
                        .onDrag {
                            dragging.value = subject.uid
                            return NSItemProvider(object: subject.uid as NSString)
                        }
                        .onDrop(of: [UTType.text], delegate: ReorderDropDelegate(
                            target: subject.uid, ordered: store.subjects.map(\.uid), dragging: $dragging.value,
                            commit: { order in Task { @MainActor in await store.reorder(order) } }))
                }
            }
            .padding(.bottom, 8)
        }
    }
}

struct ReorderDropDelegate: DropDelegate {
    let target: String
    let ordered: [String]
    @Binding var dragging: String?
    let commit: ([String]) -> Void

    func performDrop(info: DropInfo) -> Bool {
        defer { dragging = nil }
        guard let source = dragging, source != target,
              let from = ordered.firstIndex(of: source), let to = ordered.firstIndex(of: target) else { return false }
        var order = ordered
        order.remove(at: from)
        order.insert(source, at: to)
        commit(order)
        return true
    }

    func dropUpdated(info: DropInfo) -> DropProposal? { DropProposal(operation: .move) }
}

struct SubjectCard: View {
    @ObservedObject var app: AppModel
    @ObservedObject var store: SubjectsStore
    let subject: SubjectDTO
    @Environment(\.theme) private var theme
    @StateObject private var hovering = ViewState(false)

    var body: some View {
        let activeUid = app.timer.subject?.uid
        let isActive = activeUid == subject.uid && app.timer.active != nil
        let dimmed = app.timer.active != nil && !isActive && !subject.archived
        HStack(spacing: 10) {
            ColorMarker(subject.color)
            VStack(alignment: .leading, spacing: 2) {
                Text(subject.name).font(TypeRole.cardTitle.font).lineLimit(1).truncationMode(.tail)
                    .foregroundColor(theme.textPrimary.color)
                Text((subject.totalSeconds ?? 0) > 0 ? "Tracked: \(DurationText.tracked(subject.totalSeconds ?? 0))" : "No time tracked yet")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(((subject.totalSeconds ?? 0) > 0 ? theme.textSecondary : theme.textDimmed).color)
                if !subject.notes.isEmpty {
                    Text(subject.notes).font(TypeRole.caption.font).foregroundColor(theme.textSecondary.color)
                        .lineLimit(1).truncationMode(.tail).help(subject.notes)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .layoutPriority(1)
            HStack(spacing: 6) {
                if subject.archived {
                    Button("Unarchive") { Task { @MainActor in await store.unarchive(subject) } }.actionStyle(.quiet, compact: true)
                    Button("Sessions") { store.openSessions(for: subject) }.actionStyle(.quiet, compact: true)
                    Button("Delete") { Task { @MainActor in await store.requestDelete(subject) } }.actionStyle(.quiet, compact: true)
                } else {
                    Button("Edit") { store.openEditor(for: subject) }.actionStyle(.quiet, compact: true)
                    Button("Sessions") { store.openSessions(for: subject) }.actionStyle(.quiet, compact: true)
                    Button("Archive") { Task { @MainActor in await store.archive(subject) } }.actionStyle(.quiet, compact: true)
                }
            }
            .fixedSize()
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .frame(minHeight: Density.subjectRowMin)
        .card(highlighted: isActive, hovering: hovering.value && !subject.archived, dimmed: dimmed)
        .contentShape(Rectangle())
        .onHover { hovering.value = $0 }
        .onTapGesture { if !subject.archived { Task { @MainActor in await store.activate(subject) } } }
        .modifier(PulseModifier(active: store.pulseUid == subject.uid, reduceMotion: app.reduceMotion) { store.pulseUid = nil })
        .contextMenu {
            if subject.archived {
                Button("Unarchive Subject") { Task { @MainActor in await store.unarchive(subject) } }
                Button("Manage Sessions") { store.openSessions(for: subject) }
                Divider()
                Button("Delete Subject") { Task { @MainActor in await store.requestDelete(subject) } }
            } else {
                Button("Edit Subject") { store.openEditor(for: subject) }
                Button("Manage Sessions") { store.openSessions(for: subject) }
                Divider()
                Button("Archive Subject") { Task { @MainActor in await store.archive(subject) } }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(subject.name), \((subject.totalSeconds ?? 0) > 0 ? "tracked \(DurationText.tracked(subject.totalSeconds ?? 0))" : "no time tracked")\(isActive ? ", tracking now" : "")")
        .accessibilityAction(named: isActive ? "Tracking" : "Start or switch tracking") {
            if !subject.archived { Task { @MainActor in await store.activate(subject) } }
        }
    }
}
