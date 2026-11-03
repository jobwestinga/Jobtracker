import Foundation
import Darwin

/// A small display cache. It contains no notes, sync settings or write actions.
public struct WidgetGoal: Codable, Equatable, Identifiable {
    public let uid: String
    public let title: String
    public let focused: Bool
    public let deadlineLabel: String?
    public let doneMilestones: Int
    public let totalMilestones: Int
    public var id: String { uid }
}

public struct GoalsWidgetSnapshot: Codable, Equatable {
    public let version: Int
    public let capturedAt: String
    public let updatedLabel: String
    public let totalCount: Int
    public let goals: [WidgetGoal]

    public func validate() throws {
        guard version == 1, goals.count <= 4, totalCount >= goals.count,
              totalCount <= 1_000_000, capturedAt.count <= 64, !capturedAt.isEmpty,
              updatedLabel.count <= 64, Set(goals.map(\.uid)).count == goals.count,
              goals.allSatisfy({ WidgetChannel.validUID($0.uid) && !$0.title.isEmpty && $0.title.count <= 500
                  && ($0.deadlineLabel?.count ?? 0) <= 64 && $0.doneMilestones >= 0
                  && $0.totalMilestones >= $0.doneMilestones }) else { throw SnapshotError.invalid }
    }
}

public enum SnapshotError: Error { case invalid, tooLarge }

public enum WidgetChannel: String {
    case production, pilot
    public var kind: String { "JobTrackerGoals.\(rawValue)" }
    public var scheme: String { self == .production ? "jobtracker" : "jobtracker-pilot" }
    public var homeRelativePath: String { "/Library/Application Support/JobTracker Widget/\(rawValue)/goals.json" }

    public var snapshotURL: URL {
        // NSHomeDirectory is the extension's sandbox container. POSIX gives
        // the user's home; the sandbox still enforces the explicit file grant.
        let home = getpwuid(getuid()).map { String(cString: $0.pointee.pw_dir) } ?? NSHomeDirectory()
        return URL(fileURLWithPath: home + homeRelativePath)
    }

    public func goalsURL(_ uid: String? = nil) -> URL {
        var parts = URLComponents()
        parts.scheme = scheme
        parts.host = uid == nil ? "goals" : "goal"
        // Imported stable UIDs need not be UUIDs. Encode the entire opaque
        // identifier as one component, including slashes, percent signs and dots.
        parts.percentEncodedPath = uid.map { "/" + $0.utf8.map { String(format: "%%%02X", $0) }.joined() } ?? ""
        return parts.url!
    }

    public func route(_ url: URL) -> WidgetRoute? {
        guard url.scheme == scheme, url.user == nil, url.password == nil, url.port == nil,
              url.query == nil, url.fragment == nil else { return nil }
        if url.host == "goals", url.path.isEmpty || url.path == "/" { return .goals }
        guard url.host == "goal", let path = URLComponents(url: url, resolvingAgainstBaseURL: false)?.percentEncodedPath,
              path.hasPrefix("/"), !path.dropFirst().contains("/"),
              let uid = String(path.dropFirst()).removingPercentEncoding, Self.validUID(uid) else { return nil }
        return .goal(uid)
    }

    public static func validUID(_ uid: String) -> Bool {
        !uid.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && uid.utf8.count <= 1024
            && !uid.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) })
    }
}

public enum WidgetRoute: Equatable { case goals, goal(String) }

public enum GoalsSnapshotFile {
    public static let maximumBytes = 32_768

    public static func read(_ url: URL) throws -> GoalsWidgetSnapshot {
        let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        guard size <= maximumBytes else { throw SnapshotError.tooLarge }
        let data = try Data(contentsOf: url, options: .mappedIfSafe)
        guard data.count <= maximumBytes else { throw SnapshotError.tooLarge }
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let snapshot = try decoder.decode(GoalsWidgetSnapshot.self, from: data)
        try snapshot.validate()
        return snapshot
    }

    /// Validate before replacing the last good cache; readers see one complete
    /// generation. The cache directory and file are private to this Mac user.
    public static func write(_ snapshot: GoalsWidgetSnapshot, to url: URL) throws -> Bool {
        try snapshot.validate()
        if (try? read(url)) == snapshot { return false }
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        let data = try encoder.encode(snapshot)
        guard data.count <= maximumBytes else { throw SnapshotError.tooLarge }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        try data.write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        return true
    }
}
