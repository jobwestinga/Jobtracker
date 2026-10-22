import SwiftUI

/// Window-local panels. Each level dims what is below it; only the topmost
/// panel is interactive and reachable by keyboard and VoiceOver.
struct PanelStack: View {
    @ObservedObject var app: AppModel

    var body: some View {
        ZStack {
            ForEach(Array(app.panels.enumerated()), id: \.element.id) { index, entry in
                let top = index == app.panels.count - 1
                ZStack {
                    Color.black.opacity(index == 0 ? 0.45 : 0.3)
                        .ignoresSafeArea()
                        .contentShape(Rectangle())
                        .onTapGesture { if top { app.dismissTop() } }
                        .accessibilityHidden(true)
                    PanelContent(app: app, entry: entry)
                        .allowsHitTesting(top)
                        .accessibilityHidden(!top)
                }
                .transition(.opacity)
            }
        }
        .animation(app.reduceMotion ? nil : .easeOut(duration: 0.12), value: app.panels.count)
    }
}

struct PanelContent: View {
    @ObservedObject var app: AppModel
    let entry: PanelEntry

    var body: some View {
        switch entry.kind {
        case .confirm(let model): ConfirmPanel(app: app, id: entry.id, model: model)
        case .subjectEditor(let model): SubjectEditorPanel(app: app, id: entry.id, model: model)
        case .deleteSubject(let model): DeleteSubjectPanel(app: app, id: entry.id, model: model)
        case .sessions(let model): SessionPanel(app: app, id: entry.id, model: model)
        case .customDuration(let model): CustomDurationPanel(app: app, id: entry.id, model: model)
        case .goal(let model): GoalDetailPanel(app: app, id: entry.id, model: model)
        case .goalForm(let model): GoalFormPanel(app: app, id: entry.id, model: model)
        case .templates(let model): TemplatesPanel(app: app, id: entry.id, model: model, goals: app.goals)
        case .templateEditor(let model): TemplateEditorPanel(app: app, id: entry.id, model: model)
        case .settings(let model): SettingsPanel(app: app, id: entry.id, model: model)
        case .graphSettings(let model): GraphSettingsPanel(app: app, id: entry.id, model: model)
        case .recovery(let model): RecoveryPanel(app: app, id: entry.id, model: model)
        }
    }
}

/// Shared panel chrome: one title, one close affordance, a scrolling body and
/// footer actions that stay outside the scroll area.
struct PanelChrome<Body: View, Footer: View>: View {
    let title: String
    let width: CGFloat
    let onClose: () -> Void
    let content: Body
    let footer: Footer
    @Environment(\.theme) private var theme

    init(_ title: String, width: CGFloat = 440, onClose: @escaping () -> Void,
         @ViewBuilder content: () -> Body, @ViewBuilder footer: () -> Footer) {
        self.title = title
        self.width = width
        self.onClose = onClose
        self.content = content()
        self.footer = footer()
    }

    var body: some View {
        GeometryReader { proxy in
            VStack(spacing: 0) {
                HStack {
                    Text(title).font(TypeRole.section.font).lineLimit(1).truncationMode(.tail)
                        .accessibilityAddTraits(.isHeader)
                    Spacer()
                    Button(action: onClose) {
                        Image(systemName: "xmark").font(.system(size: 11, weight: .bold))
                            .foregroundColor(theme.textSecondary.color)
                            .frame(width: 26, height: 26)
                            .background(Circle().fill(theme.bgTertiary.color))
                    }
                    .buttonStyle(.plain)
                    .help("Close (Esc)")
                    .accessibilityLabel("Close")
                }
                .padding(.horizontal, 20).padding(.top, 16).padding(.bottom, 10)
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) { content }
                        .padding(.horizontal, 20).padding(.bottom, 10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                Divider().background(theme.border.color)
                HStack(spacing: 10) { footer }
                    .padding(.horizontal, 20).padding(.vertical, 12)
            }
            .frame(width: min(width, proxy.size.width - 32))
            .frame(maxHeight: min(proxy.size.height - 40, 760))
            .fixedSize(horizontal: false, vertical: true)
            .background(RoundedRectangle(cornerRadius: Radius.panel, style: .continuous).fill(theme.bgPrimary.color))
            .overlay(RoundedRectangle(cornerRadius: Radius.panel, style: .continuous).strokeBorder(theme.border.color, lineWidth: 1))
            .shadow(color: .black.opacity(0.45), radius: 24, y: 8)
            .position(x: proxy.size.width / 2, y: proxy.size.height / 2)
            .accessibilityElement(children: .contain)
            .accessibilityLabel(title)
            .accessibilityAddTraits(.isModal)
        }
    }
}

struct InlineMessage: View {
    let text: String?
    var isError = true
    @Environment(\.theme) private var theme
    var body: some View {
        if let text, !text.isEmpty {
            Text(text).font(TypeRole.secondary.font)
                .foregroundColor((isError ? theme.accentRed : theme.textSecondary).color)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isStaticText)
        }
    }
}

struct ConfirmPanel: View {
    @ObservedObject var app: AppModel
    let id: UUID
    @ObservedObject var model: ConfirmModel

    var body: some View {
        PanelChrome(model.title, width: 400, onClose: { app.dismiss(id) }) {
            Text(model.message).fixedSize(horizontal: false, vertical: true)
        } footer: {
            Spacer()
            Button("Cancel") { app.dismiss(id) }.actionStyle(.secondary).keyboardShortcut(.cancelAction)
            Button(model.confirmTitle) {
                Task { @MainActor in
                    model.running = true
                    await model.action()
                    model.running = false
                    app.dismiss(id)
                }
            }
            .actionStyle(model.role)
            .keyboardShortcut(.defaultAction)
            .disabled(model.running)
        }
    }
}

/// Compact colour selection: the eight preset hues, the current/custom colour
/// and one Custom action. Saved colours are kept exactly; Cancel changes nothing.
struct SubjectEditorPanel: View {
    @ObservedObject var app: AppModel
    let id: UUID
    @ObservedObject var model: SubjectEditorModel
    @Environment(\.theme) private var theme

    var body: some View {
        PanelChrome(model.isNew ? "New Subject" : "Edit Subject", width: 400, onClose: { app.dismiss(id) }) {
            FieldLabel("Subject Name")
            TextField("e.g. Physics, Marketing, Music Theory \u{2026}", text: $model.name)
                .textFieldStyle(FieldStyle())
                .onChange(of: model.name) { _ in model.draftChanged() }
                .accessibilityLabel("Subject name")
            FieldLabel("Colour")
            HStack(spacing: 6) {
                ForEach(model.swatches, id: \.self) { hex in
                    Swatch(hex: hex, selected: hex.caseInsensitiveCompare(model.color) == .orderedSame,
                           suggested: model.suggestions.contains(hex)) { model.color = hex }
                }
                if model.currentIsCustom {
                    Swatch(hex: model.color, selected: true, suggested: false) {}
                        .accessibilityLabel("Current custom colour \(model.color)")
                }
                ColorPicker("Custom", selection: Binding(
                    get: { RGBA(hex: model.color).color },
                    set: { newColor in
                        if let rgb = NSColor(newColor).usingColorSpace(.sRGB) {
                            model.color = RGBA(r: Double(rgb.redComponent), g: Double(rgb.greenComponent), b: Double(rgb.blueComponent)).hexString
                        }
                    }), supportsOpacity: false)
                    .labelsHidden()
                    .frame(width: 34, height: 24)
                    .help("Choose a custom colour")
                    .accessibilityLabel("Choose a custom subject colour")
                Text(model.color.uppercased()).font(TypeRole.caption.font.monospacedDigit()).foregroundColor(theme.textSecondary.color)
                    .fixedSize()
            }
            if !model.suggestions.isEmpty && !model.activeColors.isEmpty {
                Text("Dotted hues stand apart from your active subjects.").font(TypeRole.caption.font).foregroundColor(theme.textDimmed.color)
            }
            FieldLabel("Notes")
            TextField("Optional \u{2014} add context or details \u{2026}", text: $model.notes)
                .textFieldStyle(FieldStyle())
                .accessibilityLabel("Subject notes")
            InlineMessage(text: model.error)
        } footer: {
            Spacer()
            Button("Cancel") { app.dismiss(id) }.actionStyle(.secondary).keyboardShortcut(.cancelAction)
            Button("Save Subject") {
                Task { @MainActor in if await model.save() { app.dismiss(id) } }
            }
            .actionStyle(.primary)
            .keyboardShortcut(.defaultAction)
            .disabled(model.saving)
        }
    }
}

struct Swatch: View {
    let hex: String
    let selected: Bool
    let suggested: Bool
    let action: () -> Void
    @Environment(\.theme) private var theme

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle().fill(RGBA(hex: hex).color).frame(width: 22, height: 22)
                Circle().strokeBorder(selected ? theme.textPrimary.color : Color.clear, lineWidth: 2).frame(width: 28, height: 28)
                if suggested && !selected {
                    Circle().fill(theme.textPrimary.color).frame(width: 5, height: 5).offset(y: 15)
                }
            }
            .frame(width: 28, height: 34)
        }
        .buttonStyle(.plain)
        .help(suggested ? "\(hex) \u{00B7} suggested" : hex)
        .accessibilityLabel("Subject colour \(hex)\(suggested ? ", suggested" : "")")
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

struct DeleteSubjectPanel: View {
    @ObservedObject var app: AppModel
    let id: UUID
    @ObservedObject var model: DeleteSubjectModel
    @StateObject private var running = ViewState(false)

    var body: some View {
        PanelChrome("Delete \u{201C}\(model.subject.name)\u{201D}?", width: 440, onClose: { app.dismiss(id) }) {
            Text("This subject has \(model.summary.sessionCount) tracked session\(model.summary.sessionCount == 1 ? "" : "s") totalling \(DurationText.tracked(model.summary.totalSeconds))\(range).")
                .fixedSize(horizontal: false, vertical: true)
            Text("Deleting removes the subject and all of its sessions permanently. Archiving hides it but keeps its history and graphs.")
                .fixedSize(horizontal: false, vertical: true)
        } footer: {
            Button("Delete Permanently") { run("subject.delete") }.actionStyle(.destructive).disabled(running.value)
            Spacer()
            Button("Cancel") { app.dismiss(id) }.actionStyle(.secondary).keyboardShortcut(.cancelAction)
            Button("Archive Instead") { run("subject.archive") }.actionStyle(.primary).keyboardShortcut(.defaultAction).disabled(running.value)
        }
    }

    private var range: String {
        guard let earliest = model.summary.earliest, let latest = model.summary.latest else { return "" }
        return " between \(String(earliest.prefix(10))) and \(String(latest.prefix(10)))"
    }

    private func run(_ command: String) {
        running.value = true
        Task { @MainActor in
            let response = await app.mutate(command, ["subject_uid": .string(model.subject.uid)])
            running.value = false
            if response.outcome.succeeded { app.dismiss(id) }
        }
    }
}

struct RecoveryPanel: View {
    @ObservedObject var app: AppModel
    let id: UUID
    @ObservedObject var model: RecoveryModel
    @Environment(\.theme) private var theme

    var body: some View {
        let recovery = model.snapshot.recovery
        PanelChrome("Unfinished Session", width: 460, onClose: { app.dismiss(id) }) {
            if let recovery {
                Text("\u{201C}\(recovery.subjectName)\u{201D} was still running.value when JobTracker closed.")
                    .font(TypeRole.cardTitle.font).fixedSize(horizontal: false, vertical: true)
                Text("Started \(LocalTime.format(recovery.start, "EEE d MMM HH:mm")); last known active \(LocalTime.format(recovery.lastActive, "EEE d MMM HH:mm")) (\(DurationText.session(recovery.gapSeconds)) ago).")
                    .foregroundColor(theme.textSecondary.color).fixedSize(horizontal: false, vertical: true)
                choice(.last, "End at last known activity", detail: DurationText.session(recovery.durationIfLastSeconds))
                choice(.now, "End now", detail: DurationText.session(recovery.durationIfNowSeconds))
                choice(.customEnd, "End at a time", detail: nil)
                if model.choice == .customEnd {
                    TextField("YYYY-MM-DD HH:MM", text: $model.customEnd).textFieldStyle(FieldStyle())
                }
                choice(.customLength, "Set the length", detail: nil)
                if model.choice == .customLength {
                    HStack {
                        Stepper("\(model.customHours) h", value: $model.customHours, in: 0...400)
                        Stepper("\(model.customMinutes) min", value: $model.customMinutes, in: 0...59)
                    }
                }
                Text("Closing this keeps the session running.value.").font(TypeRole.caption.font).foregroundColor(theme.textDimmed.color)
                InlineMessage(text: model.error)
            }
        } footer: {
            Spacer()
            Button("Keep Running") { app.dismiss(id) }.actionStyle(.secondary).keyboardShortcut(.cancelAction)
            Button("End Session") { Task { @MainActor in if await model.resolve() { app.dismiss(id) } } }
                .actionStyle(.primary).keyboardShortcut(.defaultAction).disabled(model.saving)
        }
    }

    private func choice(_ value: RecoveryModel.Choice, _ title: String, detail: String?) -> some View {
        Button { model.choice = value } label: {
            HStack {
                Image(systemName: model.choice == value ? "largecircle.fill.circle" : "circle")
                Text(title)
                Spacer()
                if let detail { Text(detail).foregroundColor(theme.textSecondary.color) }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(model.choice == value ? .isSelected : [])
    }
}

struct CustomDurationPanel: View {
    @ObservedObject var app: AppModel
    let id: UUID
    @ObservedObject var model: CustomDurationModel
    @Environment(\.theme) private var theme

    var body: some View {
        PanelChrome("Custom Duration", width: 400, onClose: { app.dismiss(id) }) {
            Text(model.exact ? "Exact duration entered. Selecting a chunk starts a new total." : "Toggle chunks on or off. The session ends now.")
                .font(TypeRole.secondary.font).foregroundColor(theme.textSecondary.color)
            ForEach([Array(CustomDurationModel.chunks.prefix(3)), Array(CustomDurationModel.chunks.dropFirst(3))], id: \.first!.1) { row in
                HStack(spacing: 6) {
                    ForEach(row, id: \.1) { chunk in
                        Button(chunk.0) { model.toggle(chunk.1) }
                            .actionStyle(.secondary, selected: model.selectedChunks.contains(chunk.1))
                    }
                }
            }
            HStack(spacing: 12) {
                Stepper("\(model.hours) h", value: Binding(get: { model.hours }, set: { model.hours = max(0, min(99, $0)); model.exactChanged() }), in: 0...99)
                Stepper("\(model.minutes) min", value: Binding(get: { model.minutes }, set: { model.minutes = max(0, min(59, $0)); model.exactChanged() }), in: 0...59)
            }
            Text("Total: \(DurationText.preset(minutes: model.totalMinutes))").font(TypeRole.cardTitle.font)
        } footer: {
            Spacer()
            Button("Cancel") { app.dismiss(id) }.actionStyle(.secondary).keyboardShortcut(.cancelAction)
            Button("Add Session") {
                let minutes = model.totalMinutes
                app.dismiss(id)
                Task { @MainActor in await model.onConfirm(minutes) }
            }
            .actionStyle(.primary).keyboardShortcut(.defaultAction)
            .disabled(model.totalMinutes < 1)
        }
    }
}
