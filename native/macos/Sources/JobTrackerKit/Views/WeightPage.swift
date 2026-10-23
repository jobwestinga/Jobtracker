import SwiftUI

struct WeightPage: View {
    @ObservedObject var app: AppModel
    @ObservedObject var store: WeightStore
    @Environment(\.theme) private var theme

    var body: some View {
        let compact = store.density == "compact"
        Group {
            if app.weightFullscreen {
                chartCard(fullscreen: true).frame(maxHeight: .infinity)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: compact ? 6 : 8) {
                        PageHeader("Weight") {
                            Menu {
                                Picker("Tile size", selection: Binding(get: { store.density }, set: { store.setAppearance(density: $0) })) {
                                    Text("Comfortable").tag("comfortable")
                                    Text("Compact").tag("compact")
                                }
                                Toggle("High contrast", isOn: Binding(get: { store.highContrast }, set: { store.setAppearance(contrast: $0) }))
                            } label: {
                                Image(systemName: "slider.horizontal.3").font(.system(size: 15))
                            }
                            .menuStyle(.borderlessButton).fixedSize()
                            .help("Weight display settings")
                        }
                        entryCard(compact: compact)
                        chartCard(fullscreen: false).frame(height: 360)
                        historyCard(compact: compact)
                    }
                    .padding(.bottom, 8)
                }
            }
        }
        .task { await store.reload() }
    }

    private var cardBorder: RGBA { store.highContrast ? theme.textPrimary : theme.border }

    private func weightCard<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) { content() }
            .padding(10)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(theme.bgSecondary.color))
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(cardBorder.color, lineWidth: 1))
    }

    private func entryCard(compact: Bool) -> some View {
        weightCard {
            HStack(spacing: 8) {
                Text("Date")
                DatePicker("Measurement date", selection: Binding(
                    get: { LocalTime.parse(store.day) ?? Date() },
                    set: { store.chooseDay(LocalTime.dayString($0)) }), displayedComponents: .date)
                    .labelsHidden()
                    .datePickerStyle(.field)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button("Today") { store.today() }.actionStyle(.secondary)
            }
            HStack {
                Text(store.value.map { "\(WeightStore.text($0)) kg" } ?? "Choose kg and decimal")
                    .font(.system(size: 20, weight: .bold, design: .monospaced))
                Spacer()
                if let badge = store.view?.badge {
                    let color = badge.delta < 0 ? RGBA(hex: "#38BDF8") : badge.delta > 0 ? RGBA(hex: "#F87171") : theme.textSecondary
                    Text(String(format: "30d %+.1f kg", badge.delta))
                        .font(.system(size: 11, weight: .bold, design: .monospaced))
                        .foregroundColor(color.color)
                        .padding(.horizontal, 7).padding(.vertical, 2)
                        .background(RoundedRectangle(cornerRadius: 6).fill(theme.bgPrimary.color))
                        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(color.color))
                        .help(String(format: "Change between %@ and %@ within the latest reading's 30-day window: %+.2f kg", badge.from, badge.to, badge.exact))
                }
            }
            HStack {
                Text("Exact kg").font(TypeRole.caption.font).foregroundColor(theme.textSecondary.color)
                PreciseField(value: store.value) { store.setPrecise($0) }
                Spacer()
            }
            HStack(spacing: 4) {
                Button("\u{2039}") { store.shiftRange(-7) }.buttonStyle(TileStyle(selected: false, compact: compact, narrow: true)).help("Lower kilogram range")
                ForEach(0..<7, id: \.self) { offset in
                    let value = store.firstWhole + offset
                    Button("\(value)") { store.selectWhole(value) }
                        .buttonStyle(TileStyle(selected: store.whole == value, compact: compact))
                        .accessibilityLabel("\(value) kilograms")
                }
                Button("\u{203A}") { store.shiftRange(7) }.buttonStyle(TileStyle(selected: false, compact: compact, narrow: true)).help("Higher kilogram range")
            }
            HStack(spacing: 4) {
                ForEach(0..<10, id: \.self) { decimal in
                    Button(".\(decimal)") { store.selectDecimal(decimal) }
                        .buttonStyle(TileStyle(selected: store.decimal == decimal, compact: compact))
                        .accessibilityLabel("Decimal \(decimal)")
                }
            }
            Button(store.editingUid != nil ? "Update weight" : "Save weight") { Task { @MainActor in await store.save() } }
                .actionStyle(.primary, fill: true)
                .disabled(!store.canSave)
            Text(store.status).font(.system(size: 12)).foregroundColor((store.highContrast ? theme.textPrimary : theme.textSecondary).color)
        }
    }

    private func chartCard(fullscreen: Bool) -> some View {
        weightCard {
            HStack(spacing: 8) {
                CompactSelector("Weight range", options: WeightStore.ranges, selection: store.range) { store.setChartOption(range: $0) }
                CompactSelector("Smoothing", options: WeightStore.smoothings, selection: store.smoothing) { store.setChartOption(smoothing: $0) }
                Spacer()
                Button(fullscreen ? "\u{2715} Exit Fullscreen" : "\u{26F6} Fullscreen") { app.weightFullscreen.toggle() }
                    .actionStyle(.secondary)
                    .help("Expand weight plot to fill the window (Esc to exit)")
                    .accessibilityLabel("Toggle weight plot fullscreen")
            }
            HStack(spacing: 12) {
                Toggle("Show dots", isOn: Binding(get: { store.showDots }, set: { store.setChartOption(dots: $0) }))
                Toggle("Historic readings", isOn: Binding(get: { store.historic }, set: { store.setChartOption(historic: $0) }))
                Spacer()
                Text("Curve span")
                Stepper(String(format: "%.2f", store.span), value: Binding(get: { store.span }, set: { store.setChartOption(span: $0) }), in: 0.01...1, step: 0.01)
                    .disabled(store.smoothing != "loess")
            }
            if let view = store.view {
                WeightChartView(data: view, showDots: store.showDots, highContrast: store.highContrast) { store.edit($0) }
                    .frame(maxHeight: .infinity)
                Text(summary(view)).font(.system(size: 12)).foregroundColor(theme.textSecondary.color)
            } else {
                Spacer()
            }
        }
    }

    private func summary(_ view: WeightViewDTO) -> String {
        guard let summary = view.summary else { return "Save a reading to start your chart. Click a dot or a history row to edit." }
        return "\(summary.count) readings \u{00B7} latest \(WeightStore.text(summary.latest)) kg \u{00B7} change \(String(format: "%+.2f", summary.change)) kg"
    }

    private func historyCard(compact: Bool) -> some View {
        weightCard {
            HStack(spacing: 8) {
                Text("History")
                TextField("Search date or source", text: $store.search).textFieldStyle(FieldStyle())
            }
            HStack {
                Text("Date").frame(maxWidth: .infinity, alignment: .leading)
                Text("kg").frame(width: 70, alignment: .leading)
                Text("Change").frame(width: 84, alignment: .leading)
                Text("Source").frame(width: 70, alignment: .leading)
            }
            .font(.system(size: 11, weight: .semibold))
            .foregroundColor((store.highContrast ? theme.textPrimary : theme.textSecondary).color)
            .padding(.horizontal, 6).padding(.vertical, 4)
            .background(theme.bgTertiary.color)
            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(store.history) { row in
                        HStack {
                            Text(row.day).frame(maxWidth: .infinity, alignment: .leading)
                            Text(WeightStore.text(row.weightKg)).frame(width: 70, alignment: .leading)
                            Text(row.change.map { String(format: "%+.2f kg", $0) } ?? "\u{2014}")
                                .foregroundColor(changeColor(row.change).color)
                                .frame(width: 84, alignment: .leading)
                            Text(row.source).frame(width: 70, alignment: .leading)
                        }
                        .font(.system(size: 12).monospacedDigit())
                        .padding(.horizontal, 6)
                        .frame(height: compact ? 24 : 28)
                        .background(store.selectedHistoryUid == row.uid ? theme.accent.alpha(0.25).color : Color.clear)
                        .contentShape(Rectangle())
                        .onTapGesture(count: 2) { store.edit(row.uid) }
                        .onTapGesture { store.selectedHistoryUid = row.uid }
                        .accessibilityElement(children: .combine)
                        .accessibilityAddTraits(store.selectedHistoryUid == row.uid ? .isSelected : [])
                    }
                }
            }
            .frame(minHeight: 160, maxHeight: 220)
            HStack {
                Button("Edit selected") { if let uid = store.selectedHistoryUid { store.edit(uid) } }
                    .actionStyle(.secondary).disabled(store.selectedHistoryUid == nil)
                Button("Delete selected") { store.requestDelete() }
                    .actionStyle(.secondary).disabled(store.selectedHistoryUid == nil)
                Spacer()
            }
        }
    }

    private func changeColor(_ change: Double?) -> RGBA {
        guard let change else { return theme.textSecondary }
        if change < -0.005 { return RGBA(hex: "#38BDF8") }
        if change > 0.005 { return RGBA(hex: "#F87171") }
        return theme.textPrimary
    }
}

struct TileStyle: ButtonStyle {
    let selected: Bool
    let compact: Bool
    var narrow = false
    @Environment(\.theme) private var theme

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: compact ? 13 : 15, weight: selected ? .bold : .medium).monospacedDigit())
            .foregroundColor((selected ? theme.accent.contrastText : theme.textPrimary).color)
            .frame(maxWidth: narrow ? 22 : .infinity, minHeight: compact ? 30 : 36)
            .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill((selected ? theme.accent : theme.bgPrimary).color))
            .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous)
                .strokeBorder((selected ? theme.textPrimary : theme.border).color, lineWidth: selected ? 1.8 : 1))
            .opacity(configuration.isPressed ? 0.8 : 1)
            .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

struct PreciseField: View {
    let value: Double?
    let onCommit: (Double) -> Void
    @StateObject private var text = ViewState("")

    var body: some View {
        TextField("kg", text: $text.value, onCommit: {
            if let parsed = Double(text.value.replacingOccurrences(of: ",", with: ".")) { onCommit(parsed) }
        })
        .textFieldStyle(FieldStyle())
        .frame(maxWidth: 140)
        .onAppear { text.value = value.map { WeightStore.text($0) } ?? "" }
        .onChange(of: value) { newValue in text.value = newValue.map { WeightStore.text($0) } ?? "" }
    }
}
