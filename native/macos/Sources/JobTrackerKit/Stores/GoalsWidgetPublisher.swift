import Foundation
import WidgetKit
import JobTrackerWidgetSupport

/// Only the containing app writes the display cache. Reads go through its
/// existing engine, independently of the Goals page's Completed filter.
final class GoalsWidgetPublisher {
    private let output: URL
    private let kind: String
    private var epoch = 0

    init(output: URL, kind: String) { self.output = output; self.kind = kind }

    @MainActor
    func refresh(using app: AppModel) async {
        epoch += 1
        let mine = epoch
        let response = await app.call("widget.goals", kind: .read)
        guard mine == epoch, response.outcome.succeeded,
              let snapshot = try? response.result(GoalsWidgetSnapshot.self) else { return }
        do {
            if try GoalsSnapshotFile.write(snapshot, to: output) {
                WidgetCenter.shared.reloadTimelines(ofKind: kind)
            }
        } catch {
            NSLog("JobTracker could not update the goals widget cache: %@", error.localizedDescription)
        }
    }
}
