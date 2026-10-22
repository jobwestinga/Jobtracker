import Foundation
@testable import JobTrackerKit

/// End-to-end checks against the real Python engine on disposable stores.
struct EngineIntegrationTests: CheckSuite {
    final class Harness {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("jt-native-check-\(UUID().uuidString)")
        var client: EngineClient!
        var events: [EngineEvent] = []
        var journal: RequestJournal { RequestJournal(directory: directory.appendingPathComponent("journal")) }

        var launch: EngineLaunch {
            let python = ProcessInfo.processInfo.environment["JOBTRACKER_ENGINE_PYTHON"] ?? "/usr/local/bin/python3"
            return EngineLaunch(executable: URL(fileURLWithPath: python),
                                arguments: ["-m", "jobtracker.desktop", "--mode", "isolated", "--db", directory.appendingPathComponent("store.db").path],
                                environment: ["JOBTRACKER_LOCK_DIR": directory.appendingPathComponent("locks").path],
                                workingDirectory: repositoryRoot)
        }

        func start() -> Bool {
            try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            client = EngineClient(launch: launch, journal: journal, requestTimeout: 15)
            client.onEvent = { [weak self] in self?.events.append($0) }
            client.start()
            return pump(20) { self.client.isReady }
        }

        func stop() {
            client.stop()
            _ = client.waitUntilExit(timeout: 15)
        }

        func cleanup() { try? FileManager.default.removeItem(at: directory) }

        /// Spin the main run loop (main actor work included) until done.
        @discardableResult
        func pump(_ seconds: TimeInterval, until condition: () -> Bool) -> Bool {
            let deadline = Date().addingTimeInterval(seconds)
            while !condition() && Date() < deadline { RunLoop.main.run(mode: .default, before: Date().addingTimeInterval(0.02)) }
            return condition()
        }

        func send(_ command: String, _ params: [String: JSONValue] = [:], kind: CommandKind, id: String = UUID().uuidString.lowercased()) -> EngineResponse? {
            var result: EngineResponse?
            Task { @MainActor in result = await self.client.send(command, params, kind: kind, requestId: id) }
            pump(20) { result != nil }
            return result
        }
    }

    func replayedRequestNeverAppliesTwice() {
        let h = Harness()
        defer { h.stop(); h.cleanup() }
        expect(h.start(), "engine ready")
        let id = UUID().uuidString.lowercased()
        let first = h.send("subject.add", ["name": "Physics", "color": "#3B82F6", "notes": ""], kind: .mutation, id: id)
        let again = h.send("subject.add", ["name": "Physics", "color": "#3B82F6", "notes": ""], kind: .mutation, id: id)
        expect(first?.outcome == .applied, "applied \(String(describing: first?.error))")
        expect(again?.outcome == .replayed, "replayed")
        expect((try? first?.result(SubjectResult.self).subject.uid) == (try? again?.result(SubjectResult.self).subject.uid))
        let list = try? h.send("subjects.list", kind: .read)?.result(SubjectsListDTO.self)
        expect(list?.subjects.count == 1, "one subject")
        expect(h.journal.pending(storeId: h.client.hello?.storeId ?? "").isEmpty, "journal settled")
    }

    /// A UI crash after journaling but before the reply: the next launch
    /// re-sends the same identity and the receipt makes it exactly-once.
    func journaledRequestIsReplayedAfterRestart() {
        let h = Harness()
        defer { h.stop(); h.cleanup() }
        expect(h.start(), "engine ready")
        let store = h.client.hello!.storeId
        let id = UUID().uuidString.lowercased()
        // Committed but "reply lost": apply directly, then also leave the envelope.
        _ = h.send("subject.add", ["name": "Maths", "color": "#22C55E", "notes": ""], kind: .mutation, id: id)
        try? h.journal.record(.init(requestId: id, command: "subject.add", params: ["name": "Maths", "color": "#22C55E", "notes": ""], storeId: store, createdAt: Date()))
        // Never sent at all before the crash.
        let unsent = UUID().uuidString.lowercased()
        try? h.journal.record(.init(requestId: unsent, command: "subject.add", params: ["name": "Reading", "color": "#F59E0B", "notes": ""], storeId: store, createdAt: Date()))
        h.stop()
        h.events.removeAll()
        expect(h.start(), "engine restarted")
        h.pump(10) { h.journal.pending(storeId: store).isEmpty }
        expect(h.journal.pending(storeId: store).isEmpty, "journal replayed")
        let late = h.events.compactMap { event -> Outcome? in if case .lateResolution(_, _, let r) = event { return r.outcome }; return nil }
        expect(late.sorted { $0.rawValue < $1.rawValue } == [.applied, .replayed], "late outcomes \(late)")
        let names = (try? h.send("subjects.list", kind: .read)?.result(SubjectsListDTO.self))?.subjects.map(\.name).sorted()
        expect(names == ["Maths", "Reading"], "exactly once each: \(String(describing: names))")
    }

    /// Engine crash: the client reconnects to a new instance and keeps working.
    func crashedEngineRestartsWithNewInstance() {
        let h = Harness()
        defer { h.stop(); h.cleanup() }
        expect(h.start(), "engine ready")
        let firstInstance = h.client.hello!.engineInstance
        let pid = h.client.hello!.pid
        kill(pid_t(pid), SIGKILL)
        h.pump(15) { (h.client.hello?.engineInstance ?? firstInstance) != firstInstance }
        expect(h.client.hello != nil && h.client.hello!.engineInstance != firstInstance, "new instance after crash")
        expect(h.send("engine.info", kind: .read)?.outcome == .ok, "usable after restart")
    }

    /// A second owner on the same store is refused before recovery.
    func secondOwnerIsRefused() {
        let h = Harness()
        defer { h.stop(); h.cleanup() }
        expect(h.start(), "engine ready")
        let other = EngineClient(launch: h.launch, journal: h.journal)
        other.start()
        h.pump(15) { if case .storeInUse = other.state { return true }; return false }
        if case .storeInUse = other.state {} else { expect(false, "expected store in use, got \(other.state)") }
    }
}
