import SwiftUI

/// Shared session history and editor. Existing fields commit on selection or
/// editing completion; moves and duration changes preserve explicit identity.
struct SessionPanel: View {
    @ObservedObject var app: AppModel
    let id: UUID
    @ObservedObject var model: SessionPanelModel
    @Environment(\.theme) private var theme

    private var presets: DesignTokens.SessionPresets { DesignTokens.shared.roles.sessionPresets }

    var body: some View {
        PanelChrome(model.title, width: 560, onClose: close) {
            historyList
            Divider()
            selectionSection
            InlineMessage(text: model.message, isError: false)
        } footer: {
            Button("Delete") { model.requestDelete() }
                .actionStyle(.destructive)
                .disabled(!canAct || model.inFlight.contains("delete"))
            if !model.isDayScope {
                Button("New Session") { model.beginNew() }.actionStyle(.secondary).disabled(!model.canMutate)
            }
            Spacer()
            Button("Close", action: close).actionStyle(.secondary).keyboardShortcut(.cancelAction)
            Button(model.draft?.isNew == true ? "Add Session" : "Save Session") { Task { @MainActor in await model.save() } }
                .actionStyle(.primary)
                .keyboardShortcut(.defaultAction)
                .disabled(!model.canMutate || model.draft == nil || model.draft?.isDirty != true || model.inFlight.contains { $0.hasPrefix("save") || $0 == "add" })
        }
        .overlay(pendingSelectionPrompt)
    }

    private var canAct: Bool { (model.selected.map { !$0.open && $0.uid != nil } ?? false) && model.canMutate }

    // MARK: history

    private var historyList: some View {
        VStack(spacing: 0) {
            if model.loaded && model.rows.isEmpty {
                Text(model.isDayScope ? "No sessions on this day." : "No sessions recorded yet.")
                    .italic().foregroundColor(theme.textDimmed.color)
                    .frame(maxWidth: .infinity).padding(.vertical, 22)
            } else {
                ScrollViewReader { reader in
                    ScrollView {
                        LazyVStack(spacing: 2) {
                            ForEach(model.rows) { row in
                                SessionRow(row: row, showDate: !model.isDayScope, showSubject: model.isDayScope,
                                           selected: row.uid != nil && row.uid == model.selectedUid) { model.select(row) }
                                    .id(row.id)
                            }
                            if model.nextOffset != nil {
                                Button("Load older sessions") { Task { @MainActor in await model.loadMore() } }
                                    .actionStyle(.quiet, compact: true).padding(.top, 4)
                            }
                        }
                    }
                    .onChange(of: model.selectedUid) { uid in
                        if let uid { reader.scrollTo(uid) }
                    }
                }
                .frame(height: 200)
            }
        }
        .background(RoundedRectangle(cornerRadius: Radius.control).fill(theme.bgSecondary.color))
        .overlay(RoundedRectangle(cornerRadius: Radius.control).strokeBorder(theme.border.color))
    }

    // MARK: fields and move

    @ViewBuilder
    private var selectionSection: some View {
        if let row = model.selected, row.open {
            Text("This session is still running. Stop it first, then you can edit or move it here.")
                .font(TypeRole.secondary.font).foregroundColor(theme.textSecondary.color)
        } else if model.draft != nil {
            fields
        }
        if let conflict = model.conflict {
            HStack(spacing: 8) {
                InlineMessage(text: conflict)
                Spacer()
                Button("Keep my edits") { model.keepEdits() }.actionStyle(.secondary, compact: true)
                Button("Use latest") { model.useLatest() }.actionStyle(.secondary, compact: true)
            }
        }
        VStack(alignment: .leading, spacing: 6) {
            FieldLabel("Move selected session")
            HStack(spacing: 4) {
                ForEach(presets.movesSeconds, id: \.self) { seconds in
                    Button(moveLabel(seconds)) { Task { @MainActor in await model.shift(seconds) } }
                        .actionStyle(.secondary, compact: true, fill: true)
                        .disabled(!canAct)
                        .help("Move this session \(seconds < 0 ? "back" : "forward") \(abs(seconds) == 86400 ? "1 day" : "\(abs(seconds) / 60) minutes")")
                }
            }
            FieldLabel("Change duration of selected session")
            HStack(spacing: 4) {
                ForEach([-3600, -900, -300, 300, 900, 3600], id: \.self) { seconds in
                    Button(moveLabel(seconds)) { Task { @MainActor in await model.resize(seconds) } }
                        .actionStyle(.secondary, compact: true, fill: true)
                        .disabled(!canAct)
                        .help("Keep the start time and change the end by \(seconds / 60) minutes")
                }
            }
            HStack(spacing: 6) {
                Button("\u{29C9} Duplicate to Today") { Task { @MainActor in await model.duplicate(to: "today") } }
                    .actionStyle(.secondary, compact: true).disabled(!canAct)
                Button("\u{29C9} +1 Day") { Task { @MainActor in await model.duplicate(to: "next_day") } }
                    .actionStyle(.secondary, compact: true).disabled(!canAct)
                    .help("Copy this session to the next day at the same clock time")
                Spacer()
            }
        }
    }

    private var fields: some View {
        let captured = model.draft!
        let binding = Binding<SessionPanelModel.Draft>(get: {
            guard let current = model.draft, current.targetUid == captured.targetUid else { return captured }
            return current
        }, set: { value in
            guard model.draft?.targetUid == captured.targetUid else { return }
            model.draft = value
        })
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    FieldLabel("Subject")
                    CompactSelector("Session subject", options: model.subjects.map { ($0.uid, $0.name) }, selection: binding.wrappedValue.subjectUid) {
                        let uid = $0
                        Task { @MainActor in await model.changeSubject(uid) }
                    }.disabled(!model.canMutate)
                }
                Spacer()
            }
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    FieldLabel("Start Time")
                    TextField("YYYY-MM-DD HH:MM:SS", text: binding.start, onEditingChanged: { if !$0 { commitFields() } }, onCommit: commitFields).textFieldStyle(FieldStyle())
                        .accessibilityLabel("Session start date and time")
                }
                VStack(alignment: .leading, spacing: 4) {
                    FieldLabel("End Time")
                    TextField("YYYY-MM-DD HH:MM:SS", text: binding.end, onEditingChanged: { if !$0 { commitFields() } }, onCommit: commitFields).textFieldStyle(FieldStyle())
                        .accessibilityLabel("Session end date and time")
                }
            }
            if binding.wrappedValue.isDirty && !binding.wrappedValue.isNew {
                HStack {
                    Text("Unsaved edits").font(TypeRole.caption.font).foregroundColor(theme.accent.color)
                    Button("Discard") { model.discardDraft() }.actionStyle(.quiet, compact: true)
                        .disabled(!model.canMutate)
                }
            }
        }
    }

    private func commitFields() {
        Task { @MainActor in await model.commitFields() }
    }

    private func close() {
        Task { @MainActor in
            await model.commitFields()
            app.dismiss(id)
        }
    }

    private func moveLabel(_ seconds: Int) -> String {
        let sign = seconds < 0 ? "\u{2212}" : "+"
        return sign + DurationText.preset(minutes: abs(seconds) / 60)
    }

    // MARK: keep/discard prompt

    @ViewBuilder
    private var pendingSelectionPrompt: some View {
        if model.pendingSelection != nil {
            ZStack {
                Color.black.opacity(0.35).ignoresSafeArea()
                VStack(alignment: .leading, spacing: 12) {
                    Text("Unsaved edits").font(TypeRole.section.font)
                    Text("Your edits to the selected session are not saved. Keep editing it, or discard the edits and select the other session.")
                        .fixedSize(horizontal: false, vertical: true)
                    HStack {
                        Spacer()
                        Button("Keep Editing") { model.confirmPendingSelection(discard: false) }.actionStyle(.secondary).keyboardShortcut(.cancelAction)
                        Button("Discard Edits") { model.confirmPendingSelection(discard: true) }.actionStyle(.destructive)
                    }
                }
                .padding(20)
                .frame(width: 380)
                .background(RoundedRectangle(cornerRadius: Radius.panel).fill(theme.bgPrimary.color))
                .overlay(RoundedRectangle(cornerRadius: Radius.panel).strokeBorder(theme.border.color))
            }
        }
    }
}

struct SessionRow: View {
    let row: SessionDTO
    let showDate: Bool
    let showSubject: Bool
    let selected: Bool
    let action: () -> Void
    @Environment(\.theme) private var theme

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Dot(row.color)
                if showDate { Text(LocalTime.format(row.startTime, "yyyy-MM-dd")).frame(width: 84, alignment: .leading) }
                Text("\(LocalTime.clock(row.startTime))\u{2013}\(row.endTime.map { LocalTime.clock($0) } ?? "  \u{2026}  ")")
                    .frame(width: 96, alignment: .leading)
                Text(DurationText.session(row.durationSeconds)).frame(width: 64, alignment: .trailing)
                if showSubject { Text(row.subjectName).lineLimit(1).truncationMode(.tail) }
                if row.open { Text("(running)").foregroundColor(theme.textSecondary.color) }
                Spacer()
            }
            .font(.system(size: 12).monospacedDigit())
            .padding(.horizontal, 8).frame(height: 26)
            .background(RoundedRectangle(cornerRadius: 6).fill(selected ? theme.accent.alpha(68 / 255).color : Color.clear))
            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(selected ? theme.accent.color : Color.clear, lineWidth: 1.6))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}
