import Foundation

/// Private record of mutation requests whose outcome is not yet known.
///
/// An envelope is written (0600, in a 0700 folder) before its frame is sent and
/// removed once the engine settles it. After a UI crash or an engine restart the
/// same envelopes are re-sent with their original request IDs, so the engine's
/// receipts answer instead of applying them twice. This is not a sync queue:
/// the Python engine's outbox remains the only record of work for the server,
/// and configuration/credential commands are never journaled.
public final class RequestJournal {
    public struct Envelope: Codable, Equatable {
        public let requestId: String
        public let command: String
        public let params: [String: JSONValue]
        public let storeId: String
        public let createdAt: Date
    }

    public let directory: URL
    private let fileManager = FileManager.default

    public init(directory: URL) {
        self.directory = directory
    }

    private func url(for requestId: String) -> URL {
        directory.appendingPathComponent("\(requestId).json")
    }

    public func record(_ envelope: Envelope) throws {
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: true,
                                        attributes: [.posixPermissions: 0o700])
        let data = try JSONEncoder().encode(envelope)
        let target = url(for: envelope.requestId)
        let temporary = directory.appendingPathComponent(".\(envelope.requestId).tmp")
        guard fileManager.createFile(atPath: temporary.path, contents: data, attributes: [.posixPermissions: 0o600]) else {
            throw CocoaError(.fileWriteUnknown)
        }
        if fileManager.fileExists(atPath: target.path) { try fileManager.removeItem(at: target) }
        try fileManager.moveItem(at: temporary, to: target)
    }

    public func resolve(_ requestId: String) {
        try? fileManager.removeItem(at: url(for: requestId))
    }

    public func pending(storeId: String) -> [Envelope] {
        guard let files = try? fileManager.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil) else { return [] }
        return files
            .filter { $0.pathExtension == "json" }
            .compactMap { try? JSONDecoder().decode(Envelope.self, from: Data(contentsOf: $0)) }
            .filter { $0.storeId == storeId }
            .sorted { $0.createdAt < $1.createdAt }
    }
}
