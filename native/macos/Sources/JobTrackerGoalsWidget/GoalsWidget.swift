import Foundation
import SwiftUI
import WidgetKit
import JobTrackerWidgetSupport

private struct GoalsEntry: TimelineEntry {
    let date: Date
    let snapshot: GoalsWidgetSnapshot?
}

private struct GoalsProvider: TimelineProvider {
    let channel: WidgetChannel
    func placeholder(in context: Context) -> GoalsEntry { GoalsEntry(date: Date(), snapshot: nil) }
    func getSnapshot(in context: Context, completion: @escaping (GoalsEntry) -> Void) {
        completion(GoalsEntry(date: Date(), snapshot: try? GoalsSnapshotFile.read(channel.snapshotURL)))
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<GoalsEntry>) -> Void) {
        let now = Date()
        let entry = GoalsEntry(date: now, snapshot: try? GoalsSnapshotFile.read(channel.snapshotURL))
        // This schedules a display-cache read, not a database or network job.
        completion(Timeline(entries: [entry], policy: .after(now.addingTimeInterval(15 * 60))))
    }
}

@available(macOS 14.0, *)
@main struct JobTrackerGoalsWidget: Widget {
    private var channel: WidgetChannel { WidgetChannel(rawValue: Bundle.main.object(forInfoDictionaryKey: "JTWidgetChannel") as? String ?? "") ?? .pilot }
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: channel.kind, provider: GoalsProvider(channel: channel)) { entry in
            GoalsWidgetView(snapshot: entry.snapshot, channel: channel)
                .containerBackground(.background, for: .widget)
                .widgetURL(channel.goalsURL())
        }
        .configurationDisplayName("Goals")
        .description("Four goals: focused first, then deadlines and recently added goals.")
        .supportedFamilies([.systemMedium])
        .contentMarginsDisabled()
    }
}
