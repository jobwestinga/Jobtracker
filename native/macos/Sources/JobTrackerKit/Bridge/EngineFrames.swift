import Foundation

/// Protocol constants shared with `jobtracker/desktop/protocol.py`.
public enum EngineProtocol {
    public static let version = 1
    public static let maxFrameBytes = 1 << 20
    public static let maxOutstanding = 64
}

/// Command outcomes; see docs/architecture/native-engine.md.
public enum Outcome: String, Codable, Equatable {
    case applied, replayed, ok, missing, refused, invalid, conflict
    case storageError = "storage_error"
    case unavailable

    /// The engine reached a durable decision; the request identity is settled.
    public var isSettled: Bool { self != .unavailable }
    public var succeeded: Bool { self == .applied || self == .replayed || self == .ok }
}

/// Minimal header used to route a frame before decoding its typed payload.
public struct FrameHeader: Decodable {
    public let kind: String
    public let protocolVersion: Int?
    public let engineInstance: String?
    public let requestId: String?
    public let outcome: Outcome?
    public let error: String?
    public let topics: [String]?
    public let revision: Int?
    public let event: String?
    public let reason: String?
    public let message: String?

    enum CodingKeys: String, CodingKey {
        case kind, outcome, error, topics, revision, event, reason, message
        case protocolVersion = "protocol_version"
        case engineInstance = "engine_instance"
        case requestId = "request_id"
    }
}

public struct Hello: Decodable, Equatable {
    public struct CommandInfo: Decodable, Equatable { public let name: String; public let kind: String; public let topics: [String] }
    public let protocolVersion: Int
    public let engineInstance: String
    public let mode: String
    public let storeId: String
    public let dbPath: String
    public let pid: Int
    public let revision: Int
    public let syncEnabled: Bool
    public let commands: [CommandInfo]
    public let qtLoaded: Bool?

    enum CodingKeys: String, CodingKey {
        case mode, pid, revision, commands
        case protocolVersion = "protocol_version"
        case engineInstance = "engine_instance"
        case storeId = "store_id"
        case dbPath = "db_path"
        case syncEnabled = "sync_enabled"
        case qtLoaded = "qt_loaded"
    }
}

/// One response, keeping the raw frame so a caller decodes its own result type.
public struct EngineResponse {
    public let header: FrameHeader
    public let data: Data

    public var outcome: Outcome { header.outcome ?? .invalid }
    public var error: String? { header.error }
    public var topics: [String] { header.topics ?? [] }

    public func result<T: Decodable>(_ type: T.Type) throws -> T {
        try resultDecoder.decode(ResultEnvelope<T>.self, from: data).result
    }

    /// Local failure that never reached the engine or lost its connection.
    public static func local(_ outcome: Outcome, requestId: String, error: String) -> EngineResponse {
        let frame: [String: JSONValue] = [
            "kind": "response", "request_id": .string(requestId), "outcome": .string(outcome.rawValue),
            "error": .string(error), "result": .object([:]), "topics": .array([]),
        ]
        let data = (try? JSONEncoder().encode(frame)) ?? Data()
        let header = try! JSONDecoder().decode(FrameHeader.self, from: data)
        return EngineResponse(header: header, data: data)
    }
}

struct ResultEnvelope<T: Decodable>: Decodable { let result: T }

public struct EmptyResult: Decodable, Equatable { public init() {} }

/// Splits a byte stream into bounded lines; mirrors `protocol.LineReader`.
public struct LineSplitter {
    public enum Item: Equatable { case line(Data), oversized }
    private var buffer = Data()
    private var discarding = false
    private let limit: Int

    public init(limit: Int = EngineProtocol.maxFrameBytes) { self.limit = limit }

    public mutating func feed(_ chunk: Data) -> [Item] {
        var out: [Item] = []
        buffer.append(chunk)
        while true {
            guard let index = buffer.firstIndex(of: 0x0A) else {
                if buffer.count > limit {
                    if !discarding { out.append(.oversized) }
                    discarding = true
                    buffer.removeAll(keepingCapacity: true)
                }
                return out
            }
            let line = buffer[buffer.startIndex..<index]
            buffer.removeSubrange(buffer.startIndex...index)
            if discarding { discarding = false; continue }
            if line.count > limit { out.append(.oversized); continue }
            if !line.allSatisfy({ $0 == 0x20 || $0 == 0x09 || $0 == 0x0D }) { out.append(.line(Data(line))) }
        }
    }
}

/// Encodes one command frame; JSON escaping guarantees a single line.
public func encodeCommand(requestId: String, command: String, params: [String: JSONValue]) throws -> Data {
    let frame: [String: JSONValue] = [
        "protocol_version": .int(EngineProtocol.version), "kind": "command",
        "request_id": .string(requestId), "command": .string(command), "params": .object(params),
    ]
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    var data = try encoder.encode(frame)
    data.append(0x0A)
    return data
}
