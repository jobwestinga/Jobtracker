import Foundation

// Shapes produced by jobtracker/desktop/dto.py and commands.py. Timestamps stay
// the engine's exact naive local strings; they are never decoded through a
// UTC-assuming ISO-8601 strategy. Results decode with `resultDecoder`.

/// Snake-case keys become camelCase, except dictionary entries under a
/// `values` object (settings keys) and upper-case token names, which stay raw.
let resultDecoder: JSONDecoder = {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .custom { path in
        let key = path.last!
        let raw = key.stringValue
        if path.count >= 2 && path[path.count - 2].stringValue == "values" { return key }
        guard raw.contains("_"), raw == raw.lowercased() else { return key }
        let parts = raw.split(separator: "_", omittingEmptySubsequences: true)
        guard let first = parts.first else { return key }
        let camel = String(first) + parts.dropFirst().map { $0.prefix(1).uppercased() + $0.dropFirst() }.joined()
        return AnyCodingKey(camel)
    }
    return decoder
}()

struct AnyCodingKey: CodingKey {
    var stringValue: String
    var intValue: Int? { nil }
    init(_ string: String) { stringValue = string }
    init?(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { return nil }
}

public struct SubjectDTO: Decodable, Equatable, Identifiable, Hashable {
    public let uid: String
    public var name: String
    public var color: String
    public var notes: String
    public let sortOrder: Int
    public let archived: Bool
    public let createdAt: String?
    public let totalSeconds: Int?
    public var id: String { uid }
}

public struct SessionDTO: Decodable, Equatable, Identifiable, Hashable {
    public let uid: String?
    public let subjectUid: String?
    public let subjectName: String
    public let color: String
    public let startTime: String
    public let endTime: String?
    public let durationSeconds: Int
    public let open: Bool
    public let owned: Bool?
    public var id: String { uid ?? "live-\(startTime)" }
}

public struct RecoveryDTO: Decodable, Equatable {
    public let subjectName: String
    public let start: String
    public let lastActive: String
    public let now: String
    public let durationIfLastSeconds: Int
    public let durationIfNowSeconds: Int
    public let gapSeconds: Int
}

public struct TimerDTO: Decodable, Equatable {
    public let engineNow: String
    public let active: SessionDTO?
    public let subject: SubjectDTO?
    public let elapsedSeconds: Int
    public let recovery: RecoveryDTO?
    public let foreignOpen: [SessionDTO]

    public static let idle = TimerDTO(engineNow: "", active: nil, subject: nil, elapsedSeconds: 0, recovery: nil, foreignOpen: [])
}

public struct SubjectsListDTO: Decodable, Equatable {
    public let archived: Bool
    public let filter: String
    public let subjects: [SubjectDTO]
    public let resumeSubjectUid: String?
    public let todaySeconds: Int
    public let suggestedColors: [String]
    public let timer: TimerDTO
}

public struct SubjectResult: Decodable { public let subject: SubjectDTO }
public struct SessionResult: Decodable { public let session: SessionDTO }
public struct StopResult: Decodable { public let kept: Bool; public let sessionUid: String; public let timer: TimerDTO }

public struct DeletionSummaryDTO: Decodable, Equatable {
    public let sessionCount: Int
    public let totalSeconds: Int
    public let earliest: String?
    public let latest: String?
}

public struct SessionPageDTO: Decodable, Equatable {
    public let sessions: [SessionDTO]
    public let total: Int
    public let offset: Int
    public let nextOffset: Int?
}

public struct DaySessionsDTO: Decodable, Equatable {
    public let day: String
    public let sessions: [SessionDTO]
}

public struct MilestoneDTO: Decodable, Equatable, Identifiable, Hashable {
    public let uid: String
    public let goalUid: String
    public var title: String
    public var note: String
    public var done: Bool
    public let sortOrder: Int
    public var id: String { uid }
}

public struct GoalDTO: Decodable, Equatable, Identifiable, Hashable {
    public let uid: String
    public var name: String
    public var notes: String
    public var deadline: String?
    public var completed: Bool
    public var focused: Bool
    public let sortOrder: Int
    public let createdAt: String?
    public let templateUid: String?
    public var milestones: [MilestoneDTO]
    public let progress: [Int]
    public let canComplete: Bool
    public var id: String { uid }
    public var doneCount: Int { progress.first ?? 0 }
    public var totalCount: Int { progress.count > 1 ? progress[1] : 0 }
}

public struct GoalsListDTO: Decodable, Equatable { public let completed: Bool; public let goals: [GoalDTO] }
public struct GoalResult: Decodable { public let goal: GoalDTO }

public struct MilestoneSpec: Codable, Equatable, Hashable { public var title: String; public var note: String
    public init(title: String, note: String = "") { self.title = title; self.note = note }
}

public struct TemplateDTO: Decodable, Equatable, Identifiable, Hashable {
    public let uid: String
    public let title: String
    public let notes: String
    public let recurrence: String
    public let recurrenceDay: Int?
    public let milestones: [MilestoneSpec]
    public let lastGenerated: String?
    public let active: Bool
    public let sortOrder: Int
    public var id: String { uid }
}

public struct TemplatesListDTO: Decodable, Equatable { public let templates: [TemplateDTO] }

public struct SegmentDTO: Decodable, Equatable, Hashable {
    public let subjectUid: String?
    public let subjectName: String
    public let color: String
    public let seconds: Int
    public let startTime: String?
    public let endTime: String?
}

public struct BucketDTO: Decodable, Equatable, Hashable {
    public let date: String
    public let totalSeconds: Int
    public let intensitySeconds: Double
    public let periodDays: Int?
    public let thresholdDays: Int?
    public let thresholdFactor: Double?
    public let segments: [SegmentDTO]
}

public struct BreakdownDTO: Decodable, Equatable {
    public let grouping: String
    public let rangeLabel: String
    public let startDay: String?
    public let endDay: String?
    public let fitWidth: Bool
    public let buckets: [BucketDTO]
}

public struct AgendaBlockDTO: Decodable, Equatable, Hashable, Identifiable {
    public let uid: String?
    public let day: String
    public let subjectUid: String?
    public let subjectName: String
    public let color: String
    public let startH: Double
    public let endH: Double
    public let durationSeconds: Int
    public var id: String { uid ?? "live-\(day)-\(startH)" }
}

public struct AgendaDTO: Decodable, Equatable {
    public let days: [String]
    public let sessions: [AgendaBlockDTO]
    public let hourStart: Int
    public let hourEnd: Int
    public let nowDay: String
    public let nowHour: Double
    public let dayStartHour: Double
    public let rangeLabel: String
    public let fitWidth: Bool
}

public struct WeightMeasurementDTO: Decodable, Equatable, Hashable, Identifiable {
    public let uid: String
    public let measuredAt: String
    public let weightKg: Double
    public let source: String
    public let createdAt: String?
    public var id: String { uid }
}

public struct WeightListDTO: Decodable, Equatable { public let measurements: [WeightMeasurementDTO] }
public struct WeightUpsertResult: Decodable { public let measurement: WeightMeasurementDTO }

public struct ThemeChoiceDTO: Decodable, Equatable, Hashable { public let name: String; public let fx: String; public let palette: String }

public struct SettingsDTO: Decodable, Equatable {
    public let values: [String: String]
    public let dayStartTime: String
    public let themes: [ThemeChoiceDTO]
    public let fxNames: [String]
    public let paletteNames: [String]
    public func value(_ key: String, _ fallback: String = "") -> String {
        let value = values[key] ?? ""
        return value.isEmpty ? fallback : value
    }
}

public struct SyncStatusDTO: Decodable, Equatable {
    public let enabled: Bool
    public let serverUrl: String
    public let hasToken: Bool
    public let connected: Bool
    public let busy: Bool
    public let pending: Int
    public let lastSyncAt: String
    public let title: String
    public let detail: String
    public let action: String
    public let error: String

    public static let unknown = SyncStatusDTO(enabled: false, serverUrl: "", hasToken: false, connected: false, busy: false,
                                              pending: 0, lastSyncAt: "", title: "Connecting…", detail: "", action: "Settings", error: "")
}

public struct QuitResultDTO: Decodable, Equatable {
    public let quit: Bool
    public let reason: String?
    public let title: String?
    public let message: String?
    public let backupPath: String?
    public let backupError: String?
}

public struct ReasonResult: Decodable { public let reason: String? }
