import Foundation
@testable import JobTrackerKit

/// A real loopback API and two independent engine stores, not a mocked refresh.
struct NativeSyncTests: CheckSuite {
    func remoteSessionChangeKeepsTheNativeDraftAndExactIdentity() {
        guard let live = LiveApp(foreignTimer: true) else { expect(false, "live app"); return }
        let mac = live.harness
        let phone = EngineIntegrationTests.Harness()
        let server = Process()
        let folder = mac.directory.appendingPathComponent("api")
        server.executableURL = URL(fileURLWithPath: ProcessInfo.processInfo.environment["JOBTRACKER_ENGINE_PYTHON"] ?? "/usr/local/bin/python3")
        server.arguments = ["-m", "tests.support.native_sync_server", folder.path, mac.directory.appendingPathComponent("store.db").path]
        server.currentDirectoryURL = repositoryRoot
        server.standardOutput = FileHandle.nullDevice
        defer {
            if phone.client != nil { phone.stop() }
            phone.cleanup()
            if server.isRunning { server.terminate(); server.waitUntilExit() }
        }
        do { try server.run() } catch { expect(false, "test API launch: \(error)"); return }
        let manifest = folder.appendingPathComponent("ready.json")
        guard mac.pump(10, until: { FileManager.default.fileExists(atPath: manifest.path) }),
              let bytes = try? Data(contentsOf: manifest),
              let config = try? JSONDecoder().decode([String: String].self, from: bytes) else {
            expect(false, "test API ready"); return
        }
        guard phone.start() else { expect(false, "second device"); return }
        for (harness, token) in [(mac, config["native-check"]!), (phone, config["phone-check"]!)] {
            let response = harness.send("sync.configure", ["enabled": true, "server_url": .string(config["url"]!), "token": .string(token)], kind: .action)
            expect(response?.outcome == .ok, "configure isolated device")
        }
        // Switching local/synced facades takes effect on restart, as in Qt.
        mac.stop()
        let client = EngineClient(launch: mac.launch, journal: mac.journal)
        mac.client = client
        live.app = AppModel(engine: client, configuration: nil)
        client.start()
        guard mac.pump(10, until: { client.isReady && live.app.subjects.list != nil }),
              sync(mac) else { expect(false, "initial native sync"); return }
        phone.stop()
        guard phone.start(), sync(phone) else { expect(false, "initial phone sync"); return }
        let app = live.app!
        let activeUid = app.timer.active?.uid
        let subject = app.subjects.subjects.first { $0.name == "Reading" }!
        let panel = SessionPanelModel(app: app, scope: .subject(uid: subject.uid, name: subject.name, color: subject.color))
        app.present(.sessions(panel))
        guard mac.pump(5, until: { panel.loaded }), let row = panel.rows.first, let uid = row.uid else {
            expect(false, "selected history"); return
        }
        panel.select(row)
        let newerEnd = LocalTime.format(LocalTime.isoString(Date().addingTimeInterval(900)), "yyyy-MM-dd HH:mm:ss")
        panel.draft?.end = newerEnd
        let shifted = phone.send("session.shift", ["session_uid": .string(uid), "seconds": 900], kind: .mutation)
        expect(shifted?.outcome.succeeded == true, "other device moved the exact session")
        expect(sync(phone) && sync(mac), "push other device, pull Mac")
        expect(mac.pump(5) { panel.conflict != nil }, "native refresh detects concurrent edits")
        expect(panel.selectedUid == uid && panel.draft?.targetUid == uid && panel.draft?.end == newerEnd,
               "the exact target and unsaved input survive sync")
        live.run { await panel.save() }
        expect(panel.conflict != nil && panel.draft?.end == newerEnd, "Save cannot bypass conflict resolution")
        panel.useLatest()
        let latest = try? shifted?.result(SessionResult.self).session
        expect(panel.draft?.baseStart == latest?.startTime && panel.draft?.isDirty == false)
        expect(app.timer.active?.uid == activeUid, "sync does not adopt the foreign timer")
        let owners = try? phone.send("timer.get", kind: .read)?.result(TimerDTO.self)
        expect(owners?.active == nil, "the other mirror does not own either pre-existing timer")
        expect(sync(phone) && sync(mac), "both queues settle after conflict resolution")
    }

    private func sync(_ harness: EngineIntegrationTests.Harness) -> Bool {
        let before = (try? harness.send("sync.status", kind: .read)?.result(SyncStatusDTO.self))?.lastSyncAt
        _ = harness.send("sync.now", kind: .action)
        var lastStatus: SyncStatusDTO?
        let done = harness.pump(15) {
            guard let status = try? harness.send("sync.status", kind: .read)?.result(SyncStatusDTO.self) else { return false }
            lastStatus = status
            return !status.busy && status.connected && status.error.isEmpty && status.pending == 0
                && !status.lastSyncAt.isEmpty && status.lastSyncAt != before
        }
        if !done { expect(false, "sync: \(lastStatus?.title ?? "no status"); \(lastStatus?.error ?? "")") }
        return done
    }
}
