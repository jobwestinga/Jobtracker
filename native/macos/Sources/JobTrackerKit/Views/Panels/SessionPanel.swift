import SwiftUI

/// W1: one panel for subject history, day history and agenda blocks. A stable
/// selection header, the exact editable fields, visible quick-add and move
/// presets, and distinct Save / Move / Quick add / Duplicate / Delete commands.
struct SessionPanel: View {
    @ObservedObject var app: AppModel
    let id: UUID
    @ObservedObject var model: SessionPanelModel
    @Environment(\.theme) private var theme

    private var presets: DesignTokens.SessionPresets { DesignTokens.shared.roles.sessionPresets }

    var body: some View {
        PanelChrome(model.title, width: 560, onClose: { app.dismiss(id) }) {
            header
            historyList
            if model.isDayScope { daySummary }
            Divider()
            selectionSection
            Divider()
            quickAddSection
            InlineMessage(text: model.message, isError: false)
        } footer: {
            Button("Delete") { model.requestDelete() }
                .actionStyle(.destructive)
                .disabled(!canAct || model.inFlight.contains("delete"))
            Spacer()
            Button("Close") { app.dismiss(id) }.actionStyle(.secondary).keyboardShortcut(.cancelAction)
            Button(model.draft?.isNew == true ? "Add Session" : "Save Session") { Task { @MainActor in await model.save() } }
                .actionStyle(.primary)
                .keyboardShortcut(.defaultAction)
                .disabled(model.draft == nil || model.draft?.isDirty != true || model.inFlight.contains { $0.hasPrefix("save") || $0 == "add" })
        }
        .overlay(pendingSelectionPrompt)
    }

    private var canAct: Bool { model.selected.map { !$0.open && $0.uid != nil } ?? false }

    // MARK: selection header

    private var header: some View {
        HStack(alignment: .center, spacing: 10) {
            if let row = model.selected {
                ColorMarker(row.color, width: 4, height: 30)
                VStack(alignment: .leading, spacing: 2) {
                    Text(row.subjectName).font(TypeRole.cardTitle.font).lineLimit(1)
                    Text("\(LocalTime.format(row.startTime, "EEE d MMM yyyy  HH:mm"))\u{2013}\(row.endTime.map { LocalTime.format($0, "HH:mm") } ?? "running")  \u{00B7}  \(DurationText.session(row.durationSeconds))")
                        .font(TypeRole.secondary.font.monospacedDigit()).foregroundColor(theme.textSecondary.color)
                }
            } else if model.draft?.isNew == true {
                Image(systemName: "plus.circle").foregroundColor(theme.accent.color)
                Text("New session").font(TypeRole.cardTitle.font)
            } else {
                Text("No session selected").font(TypeRole.cardTitle.font).foregroundColor(theme.textSecondary.color)
                Text("Choose a row to edit or move it.").font(TypeRole.secondary.font).foregroundColor(theme.textDimmed.color)
            }
            Spacer()
        }
        .frame(minHeight: 40)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(model.selected.map { "Selected session: \($0.subjectName), \(DurationText.session($0.durationSeconds))" } ?? "No session selected")
    }

    // MARK: history

    private var historyList: some View {
        VStack(spacing: 0) {
            if model.loaded && model.rows.isEmpty {
                Text(model.isDayScope ? "No sessions on this day." : "No sessions recorded yet. Use Quick add or New session.")
                    .italic().foregroundColor(theme.textDimmed.color)
                    .frame(maxWidth: .infinity).padding(.vertical, 22)
            } else {
                ScrollView {
                    LazyVStack(spacing: 2) {
                        ForEach(model.rows) { row in
                            SessionRow(row: row, showDate: !model.isDayScope, showSubject: model.isDayScope,
                                       selected: row.uid != nil && row.uid == model.selectedUid) { model.select(row) }
                        }
                        if model.nextOffset != nil {
                            Button("Load older sessions") { Task { @MainActor in await model.loadMore() } }
                                .actionStyle(.quiet, compact: true).padding(.top, 4)
                        }
                    }
                }
                .frame(height: 200)
            }
        }
        .background(RoundedRectangle(cornerRadius: Radius.control).fill(theme.bgSecondary.color))
        .overlay(RoundedRectangle(cornerRadius: Radius.control).strokeBorder(theme.border.color))
    }

    private var daySummary: some View {
        let entries = model.daySummary
        let total = entries.reduce(0) { $0 + $1.seconds }
        return VStack(alignment: .leading, spacing: 4) {
            Text("\(DurationText.session(total)) across \(model.rows.count) session\(model.rows.count == 1 ? "" : "s")")
                .font(TypeRole.secondary.font.weight(.semibold))
            entries.enumerated().reduce(Text("")) { text, item in
                text + Text(item.offset == 0 ? "" : "    ") + Text("\u{25A0} ").foregroundColor(RGBA(hex: item.element.color).color)
                    + Text("\(item.element.name) \(DurationText.session(item.element.seconds))").foregroundColor(theme.textSecondary.color)
            }
            .font(TypeRole.caption.font)
        }
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
                Button("Keep my edits") { model.conflict = nil }.actionStyle(.secondary, compact: true)
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
        let binding = Binding<SessionPanelModel.Draft>(get: { model.draft! }, set: { model.draft = $0 })
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    FieldLabel("Subject")
                    CompactSelector("Session subject", options: model.subjects.map { ($0.uid, $0.name) }, selection: binding.wrappedValue.subjectUid) {
                        model.draft?.subjectUid = $0
                    }
                }
                Spacer()
            }
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    FieldLabel("Start Time")
                    TextField("YYYY-MM-DD HH:MM:SS", text: binding.start).textFieldStyle(FieldStyle())
                        .accessibilityLabel("Session start date and time")
                }
                VStack(alignment: .leading, spacing: 4) {
                    FieldLabel("End Time")
                    TextField("YYYY-MM-DD HH:MM:SS", text: binding.end).textFieldStyle(FieldStyle())
                        .accessibilityLabel("Session end date and time")
                }
            }
            if binding.wrappedValue.isDirty && !binding.wrappedValue.isNew {
                HStack {
                    Text("Unsaved edits").font(TypeRole.caption.font).foregroundColor(theme.accent.color)
                    Button("Discard") { model.discardDraft() }.actionStyle(.quiet, compact: true)
                }
            }
        }
    }

    // MARK: quick add

    private var quickAddSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                FieldLabel("Quick add \u{00B7} ending now")
                if model.isDayScope {
                    CompactSelector("Subject for new sessions", options: model.subjects.filter { !$0.archived }.map { ($0.uid, $0.name) },
                                    selection: model.quickAddSubjectUid ?? "") { model.quickAddSubjectUid = $0 }
                }
                Spacer()
                Button("New session\u{2026}") { model.beginNew() }.actionStyle(.quiet, compact: true)
            }
            HStack(spacing: 4) {
                ForEach(presets.durationsMinutes, id: \.self) { minutes in
                    Button(DurationText.preset(minutes: minutes)) { Task { @MainActor in await model.quickAdd(minutes: minutes) } }
                        .actionStyle(.secondary, compact: true, fill: true)
                        .disabled(model.quickAddSubjectUid == nil || model.inFlight.contains("quick:\(minutes)"))
                        .accessibilityLabel("Add \(DurationText.preset(minutes: minutes)) ending now")
                }
                Button("Custom\u{2026}") { model.openCustomDuration() }
                    .actionStyle(.secondary, compact: true)
                    .disabled(model.quickAddSubjectUid == nil)
                    .help("Enter any duration (hours and minutes)")
            }
            if !model.isDayScope {
                HStack(spacing: 4) {
                    Text("Time slot, today:").font(TypeRole.caption.font).foregroundColor(theme.textSecondary.color)
                    ForEach([(9, 11), (11, 13), (13, 15), (15, 17), (17, 19)], id: \.0) { slot in
                        Button("\(slot.0)\u{2013}\(slot.1)") { Task { @MainActor in await model.addSlot(startHour: slot.0, endHour: slot.1) } }
                            .actionStyle(.secondary, compact: true)
                    }
                }
            }
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
