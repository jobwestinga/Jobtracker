import SwiftUI

/// The widget's four rows are links, with no completion/editing controls.
public struct GoalsWidgetView: View {
    public let snapshot: GoalsWidgetSnapshot?
    public let channel: WidgetChannel

    public init(snapshot: GoalsWidgetSnapshot?, channel: WidgetChannel) {
        self.snapshot = snapshot; self.channel = channel
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 5) {
                Image(systemName: "scope")
                Text("JobTracker").fontWeight(.semibold)
                Spacer(minLength: 4)
                Text("Focus").font(.system(size: 10, weight: .medium)).foregroundStyle(.secondary)
            }.font(.system(size: 13))
            if let snapshot, !snapshot.goals.isEmpty {
                VStack(spacing: 0) {
                    ForEach(snapshot.goals) { goal in
                        Link(destination: channel.goalsURL(goal.uid)) {
                            HStack(spacing: 6) {
                                Image(systemName: goal.focused ? "star.fill" : "circle.fill")
                                    .font(.system(size: goal.focused ? 10 : 4))
                                    .foregroundStyle(goal.focused ? Color.yellow : Color.secondary)
                                    .frame(width: 12)
                                Text(goal.title).font(.system(size: 12, weight: .medium))
                                    .lineLimit(1).truncationMode(.tail)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                if let deadline = goal.deadlineLabel {
                                    Text(deadline).font(.system(size: 10)).foregroundStyle(.secondary)
                                        .lineLimit(1).fixedSize(horizontal: true, vertical: false)
                                } else if goal.totalMilestones > 0 {
                                    Text("\(goal.doneMilestones)/\(goal.totalMilestones)")
                                        .font(.system(size: 10).monospacedDigit()).foregroundStyle(.secondary)
                                }
                            }
                            .foregroundStyle(.primary)
                            .frame(height: 24).contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("\(goal.focused ? "Focused goal: " : "Goal: ")\(goal.title)")
                        .accessibilityHint("Open this goal in JobTracker")
                    }
                }
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    Text(snapshot == nil ? "Open JobTracker" : "No active goals")
                        .font(.system(size: 13, weight: .medium))
                    Text(snapshot == nil ? "Your focused goals will appear here." : "New goals will appear here when you add them.")
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                }.frame(maxHeight: .infinity, alignment: .center)
            }
            Spacer(minLength: 0)
            HStack(spacing: 4) {
                Text(snapshot.map { "Updated \($0.updatedLabel)" } ?? "Open to update")
                Spacer(minLength: 4)
                if let snapshot, snapshot.totalCount > snapshot.goals.count {
                    Text("+\(snapshot.totalCount - snapshot.goals.count) more")
                }
            }.font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(1)
        }
        .padding(12)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}
