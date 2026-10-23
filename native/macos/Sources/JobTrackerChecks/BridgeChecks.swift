import Foundation
@testable import JobTrackerKit

/// Repository root, derived from this file's location.
let repositoryRoot = URL(fileURLWithPath: #filePath)
    .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    .deletingLastPathComponent().deletingLastPathComponent()
let bridgeFixtures = repositoryRoot.appendingPathComponent("tests/contracts/desktop_bridge")

struct FramingTests: CheckSuite {
    func splitsPartialAndMultipleFrames() {
        var splitter = LineSplitter()
        expect(splitter.feed(Data(#"{"a":"#.utf8)) == [])
        expect(splitter.feed(Data("1}\n\n{\"b\":2}\n{\"c\"".utf8)) == [.line(Data(#"{"a":1}"#.utf8)), .line(Data(#"{"b":2}"#.utf8))])
        expect(splitter.feed(Data(":3}\n".utf8)) == [.line(Data(#"{"c":3}"#.utf8))])
    }

    func oversizedFrameIsReportedOnceAndResynchronizes() {
        var splitter = LineSplitter(limit: 16)
        expect(splitter.feed(Data(String(repeating: "x", count: 40).utf8)) == [.oversized])
        expect(splitter.feed(Data("yyyy".utf8)) == [])
        expect(splitter.feed(Data("tail\n{\"ok\":1}\n".utf8)) == [.line(Data(#"{"ok":1}"#.utf8))])
    }

    func commandFrameIsOneLineWithIntegerSeconds() throws {
        let data = try encodeCommand(requestId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", command: "session.shift",
                                     params: ["session_uid": "x", "seconds": .int(900), "note": "a\nb"])
        let text = String(decoding: data, as: UTF8.self)
        expect(text.filter { $0 == "\n" }.count == 1)
        expect(text.contains("\"seconds\":900"))
        expect(text.contains("\"protocol_version\":1"))
    }

    func localResponsesCarryOutcomeAndError() {
        let response = EngineResponse.local(.unavailable, requestId: "r", error: "No reply yet")
        expect(response.outcome == .unavailable)
        expect(response.error == "No reply yet")
        expect(!response.outcome.isSettled)
        expect(Outcome.refused.isSettled && !Outcome.refused.succeeded)
    }

    func replayKeepsTheOriginalRefusalOrMissingOutcome() throws {
        for outcome in [Outcome.refused, .missing] {
            let raw: [String: Any] = ["kind": "response", "request_id": "r", "outcome": "replayed",
                                      "result": ["original_outcome": outcome.rawValue, "reason": "Not applied"]]
            let data = try JSONSerialization.data(withJSONObject: raw)
            let response = EngineResponse(header: try JSONDecoder().decode(FrameHeader.self, from: data), data: data)
            expect(response.outcome == outcome, "a replay is not necessarily a successful mutation")
            expect(!response.outcome.succeeded)
            expect(response.error == "Not applied")
        }
    }
}

struct SharedFixtureTests: CheckSuite {
    struct CommandFixture: Decodable {
        struct Spec: Decodable { let kind: String; let topics: [String] }
        let protocolVersion: Int
        let outcomes: [String]
        let commands: [String: Spec]
        enum CodingKeys: String, CodingKey { case protocolVersion = "protocol_version", outcomes, commands }
    }

    func fixture() throws -> CommandFixture {
        try JSONDecoder().decode(CommandFixture.self, from: Data(contentsOf: bridgeFixtures.appendingPathComponent("commands.json")))
    }

    func protocolVersionAndOutcomesMatch() throws {
        let fixture = try fixture()
        expect(fixture.protocolVersion == EngineProtocol.version)
        for outcome in fixture.outcomes { expect(Outcome(rawValue: outcome) != nil, "unknown outcome \(outcome)") }
    }

    /// Every command string the app sends exists in the engine allowlist.
    func everyCommandUsedBySwiftIsAllowlisted() throws {
        let fixture = try fixture()
        let sources = repositoryRoot.appendingPathComponent("native/macos/Sources/JobTrackerKit")
        let files = FileManager.default.enumerator(at: sources, includingPropertiesForKeys: nil)!.compactMap { $0 as? URL }.filter { $0.pathExtension == "swift" }
        let pattern = try NSRegularExpression(pattern: #"(?:call|mutate|send)\("([a-z_]+\.[a-z_]+)""#)
        var used = Set<String>()
        for file in files {
            let text = try String(contentsOf: file)
            for match in pattern.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
                used.insert(String(text[Range(match.range(at: 1), in: text)!]))
            }
        }
        expect(used.count > 30)
        let unknown = used.subtracting(fixture.commands.keys)
        expect(unknown.isEmpty, "not allowlisted: \(unknown.sorted())")
    }

    /// Real engine responses (synthetic data) decode into the Swift DTOs.
    func liveSamplesDecode() throws {
        let data = try Data(contentsOf: bridgeFixtures.appendingPathComponent("samples.json"))
        let samples = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        func frame(_ name: String) throws -> EngineResponse {
            let raw = try JSONSerialization.data(withJSONObject: samples[name]!)
            return EngineResponse(header: try JSONDecoder().decode(FrameHeader.self, from: raw), data: raw)
        }
        let hello = try JSONDecoder().decode(Hello.self, from: JSONSerialization.data(withJSONObject: samples["hello"]!))
        expect(hello.protocolVersion == 1 && !hello.commands.isEmpty)
        let subjects = try frame("subjects.list").result(SubjectsListDTO.self)
        expect(subjects.subjects.count == 5 && subjects.timer.active != nil && subjects.timer.foreignOpen.count == 1)
        expect(subjects.resumeSubjectUid != nil)
        let settings = try frame("settings.get").result(SettingsDTO.self)
        expect(settings.values["theme_palette"] != nil, "settings keys must stay raw")
        expect(settings.themes.map(\.name) == ["OLED Midnight", "Carbon Dark", "Ocean", "Slate", "Ocean Aura", "Violet Space"])
        let goals = try frame("goals.list").result(GoalsListDTO.self)
        expect(goals.goals.contains { $0.milestones.count == 3 })
        _ = try frame("templates.list").result(TemplatesListDTO.self)
        let breakdown = try frame("graphs.breakdown").result(BreakdownDTO.self)
        expect(!breakdown.buckets.isEmpty)
        let agenda = try frame("graphs.agenda").result(AgendaDTO.self)
        expect(!agenda.sessions.isEmpty && agenda.hourEnd > agenda.hourStart)
        let weight = try frame("weight.view").result(WeightViewDTO.self)
        expect(weight.rows.count > 30 && weight.axis != nil)
        _ = try frame("sync.status").result(SyncStatusDTO.self)
        _ = try frame("timer.get").result(TimerDTO.self)
        let page = try frame("sessions.for_subject").result(SessionPageDTO.self)
        expect(page.sessions.count <= 3)
        _ = try frame("sessions.for_day").result(DaySessionsDTO.self)
        expect(try frame("session.missing").outcome == .missing)
    }
}

struct JournalTests: CheckSuite {
    func clonedDeviceIdentityDoesNotReplayAgainstAnotherStore() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let journal = RequestJournal(directory: directory)
        let original = RequestJournal.Envelope(requestId: "r", command: "session.shift", params: ["seconds": 900],
            storeId: "copied-device-id", createdAt: Date(), storePath: directory.appendingPathComponent("original.db").path)
        try journal.record(original)
        expect(journal.pending(storeId: original.storeId, storePath: original.storePath) == [original])
        expect(journal.pending(storeId: original.storeId, storePath: directory.appendingPathComponent("copy.db").path).isEmpty)
        expect(journal.pending(storeId: original.storeId) == [original], "the original pending record is preserved")
    }

    func retryCannotReplaceTheCapturedRequest() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let journal = RequestJournal(directory: directory)
        let original = RequestJournal.Envelope(requestId: "r", command: "session.quick_add",
            params: ["at": "2026-10-21T10:00:00", "minutes": 15], storeId: "store", createdAt: Date())
        try journal.record(original)
        try journal.record(.init(requestId: "r", command: original.command, params: original.params,
                                storeId: "store", createdAt: Date().addingTimeInterval(1)))
        expect(journal.pending(storeId: "store") == [original], "an identical retry keeps original capture/order")
        var rejected = false
        do {
            try journal.record(.init(requestId: "r", command: original.command,
                params: ["at": "2026-10-21T11:00:00", "minutes": 15], storeId: "store", createdAt: Date()))
        } catch { rejected = true }
        expect(rejected, "changed parameters must not overwrite an unresolved request")
        expect(journal.pending(storeId: "store") == [original])
    }

    func recordsResolvesAndFiltersByStore() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let journal = RequestJournal(directory: directory)
        let first = RequestJournal.Envelope(requestId: "a", command: "session.shift", params: ["seconds": 900], storeId: "store-1", createdAt: Date(timeIntervalSince1970: 1))
        let other = RequestJournal.Envelope(requestId: "b", command: "subject.add", params: [:], storeId: "store-2", createdAt: Date(timeIntervalSince1970: 2))
        try journal.record(first)
        try journal.record(other)
        expect(journal.pending(storeId: "store-1") == [first])
        let attributes = try FileManager.default.attributesOfItem(atPath: directory.appendingPathComponent("a.json").path)
        expect((attributes[.posixPermissions] as? NSNumber)?.intValue == 0o600)
        journal.resolve("a")
        expect(journal.pending(storeId: "store-1").isEmpty)
        expect(journal.pending(storeId: "store-2") == [other])
    }
}

struct FormattingTests: CheckSuite {
    func invalidDatesAndZonedInputAreNotSilentlyNormalized() {
        for value in ["2026-02-30", "2026-13-01", "2026-10-21T24:00:00", "2026-10-21T10:70:00",
                      "2026-10-21T10:00:00Z", "2026-10-21T10:00:00+02:00", "2026-10-21T10:00:00-02:00"] {
            expect(LocalTime.parse(value) == nil, "reject \(value)")
            expect(LocalTime.canonicalTimestamp(value) == nil)
        }
        expect(LocalTime.canonicalTimestamp("2026-03-29 02:15:00") == "2026-03-29T02:15:00")
        expect(LocalTime.format("2026-03-29T02:15:00", "HH:mm") == "02:15", "display preserves civil fields during a DST gap")
        expect(LocalTime.parse("2028-02-29") != nil)
    }

    func naiveLocalTimestampsRoundTripWithoutTimezoneShift() {
        let parsed = LocalTime.parse("2026-07-14T23:30:15.250000")!
        expect(LocalTime.isoString(parsed) == "2026-07-14T23:30:15.250000")
        let plain = LocalTime.parse("2026-10-21T16:20:00")!
        expect(LocalTime.isoString(plain) == "2026-10-21T16:20:00")
        expect(LocalTime.format("2026-10-21 16:20:00", "HH:mm") == "16:20")
    }

    func durationsMatchTheQtWording() {
        expect(DurationText.tracked(34 * 3600 + 40 * 60) == "34h 40m")
        expect(DurationText.tracked(125) == "2m 5s")
        expect(DurationText.clock(1503) == "00:25:03")
        expect(DurationText.short(3900) == "1h 05m")
        expect(DurationText.preset(minutes: 75) == "1h 15m")
        expect(DurationText.preset(minutes: 1440) == "1 day")
    }
}
