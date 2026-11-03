import SwiftUI

struct GraphsPage: View {
    @ObservedObject var app: AppModel
    @ObservedObject var store: GraphsStore
    @Environment(\.theme) private var theme

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            PageHeader("Graphs") { EmptyView() }
            HStack(spacing: 7) {
                Button("Stacked Bar") { store.setMode("bar") }
                    .actionStyle(.secondary, selected: store.mode == "bar", fill: true)
                    .help("Press Up to switch to Stacked Bar")
                Button("Agenda") { store.setMode("agenda") }
                    .actionStyle(.secondary, selected: store.mode == "agenda", fill: true)
                    .help("Press Down to switch to Agenda")
                Button("Graph Settings") { store.openSettings() }
                    .actionStyle(.secondary)
                    .modifier(PulseModifier(active: store.pulseSettings, reduceMotion: app.reduceMotion) { store.pulseSettings = false })
            }
            Text(subtitle).font(.system(size: 12, weight: .medium)).foregroundColor(theme.textSecondary.color)
            Group {
                if store.mode == "agenda", let agenda = store.agenda {
                    AgendaChartView(data: agenda, onDay: store.openDay, onBlock: store.openBlock, onMove: store.moveBlock, reduceMotion: app.reduceMotion)
                } else if store.mode == "bar", let breakdown = store.breakdown {
                    BarChartView(data: breakdown, onDay: store.openDay)
                } else {
                    Color.clear.surface()
                }
            }
            .frame(maxHeight: .infinity)
        }
        .task { await store.reloadIfStale() }
        .onReceive(Timer.publish(every: 60, on: .main, in: .common).autoconnect()) { _ in
            // Minute ticks move the running segment and the now line only.
            Task { @MainActor in await store.liveTick() }
        }
    }

    private var subtitle: String {
        if store.mode == "agenda", let agenda = store.agenda {
            let total = agenda.sessions.reduce(0) { $0 + $1.durationSeconds }
            return "Agenda \u{00B7} \(agenda.rangeLabel) \u{00B7} \(String(format: "%.1fh", Double(total) / 3600)) \u{00B7} \(AgendaLayout.hourLabel(agenda.hourStart))\u{2013}\(AgendaLayout.hourLabel(agenda.hourEnd))"
        }
        if let breakdown = store.breakdown {
            let total = breakdown.buckets.reduce(0) { $0 + $1.totalSeconds }
            let group = ["daily": "Daily", "weekly": "Weekly", "monthly": "Monthly"][breakdown.grouping] ?? "Daily"
            return "\(group) \u{00B7} \(breakdown.rangeLabel) \u{00B7} \(String(format: "%.1fh", Double(total) / 3600))"
        }
        return " "
    }

}
