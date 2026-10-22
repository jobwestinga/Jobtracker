import Foundation

/// How to start the bundled (or development) engine.
public struct EngineLaunch: Equatable {
    public var executable: URL
    public var arguments: [String]
    public var environment: [String: String]
    public var workingDirectory: URL?

    public init(executable: URL, arguments: [String], environment: [String: String] = [:], workingDirectory: URL? = nil) {
        self.executable = executable
        self.arguments = arguments
        self.environment = environment
        self.workingDirectory = workingDirectory
    }
}

public enum CommandKind { case read, mutation, action }

public enum EngineEvent {
    case changed(topics: Set<String>, revision: Int)
    case syncStatus(SyncStatusDTO)
    /// A new engine instance is ready (first start or restart); refetch everything.
    case connected(Hello)
    /// A request whose reply was missed was settled later (replay after restart).
    case lateResolution(requestId: String, command: String, response: EngineResponse)
}

/// Child-process client for `python -m jobtracker.desktop`.
///
/// All state is touched on the main queue. Pipe reads, framing and header
/// decoding happen on a background queue; validated frames are delivered to the
/// main queue. Writes go through a serial queue so the UI thread never blocks on
/// the pipe. Responses and events from any other engine instance are dropped.
public final class EngineClient: ObservableObject {
    public enum State: Equatable {
        case idle, starting, ready(Hello), restarting(String), failed(String), storeInUse(String), incompatible(String), stopped
    }

    @Published public private(set) var state: State = .idle
    @Published public private(set) var unresolved: Set<String> = []
    public private(set) var diagnostics: [String] = []
    public var onEvent: ((EngineEvent) -> Void)?

    private let launch: EngineLaunch
    private let journal: RequestJournal
    private let requestTimeout: TimeInterval
    private var process: Process?
    private var stdinHandle: FileHandle?
    private let writeQueue = DispatchQueue(label: "jobtracker.engine.write")
    private let readQueue = DispatchQueue(label: "jobtracker.engine.read")
    private var instance: String?
    private var storeId: String?
    private var pending: [String: Pending] = [:]
    private var restartTimes: [Date] = []
    private var stopping = false
    private var generation = 0

    private struct Pending {
        let command: String
        let kind: CommandKind
        let continuation: CheckedContinuation<EngineResponse, Never>?
        let timeout: DispatchWorkItem?
    }

    public init(launch: EngineLaunch, journal: RequestJournal, requestTimeout: TimeInterval = 20) {
        self.launch = launch
        self.journal = journal
        self.requestTimeout = requestTimeout
    }

    public var hello: Hello? { if case .ready(let hello) = state { return hello }; return nil }
    public var isReady: Bool { hello != nil }

    // MARK: lifecycle

    public func start() {
        dispatchPrecondition(condition: .onQueue(.main))
        stopping = false
        state = .starting
        spawn()
    }

    private func spawn() {
        generation += 1
        let myGeneration = generation
        let process = Process()
        process.executableURL = launch.executable
        process.arguments = launch.arguments
        var environment = ProcessInfo.processInfo.environment
        environment.removeValue(forKey: "JOBTRACKER_DB_PATH")
        environment["PYTHONUNBUFFERED"] = "1"
        for (key, value) in launch.environment { environment[key] = value }
        process.environment = environment
        if let directory = launch.workingDirectory { process.currentDirectoryURL = directory }
        let input = Pipe(), output = Pipe(), errors = Pipe()
        process.standardInput = input
        process.standardOutput = output
        process.standardError = errors

        var splitter = LineSplitter()
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            guard let self else { return }
            if chunk.isEmpty {
                handle.readabilityHandler = nil
                return
            }
            self.readQueue.sync {
                for item in splitter.feed(chunk) {
                    switch item {
                    case .oversized:
                        DispatchQueue.main.async { self.note("Dropped an oversized engine frame") }
                    case .line(let data):
                        guard let header = try? JSONDecoder().decode(FrameHeader.self, from: data) else {
                            DispatchQueue.main.async { self.note("Unreadable engine frame") }
                            continue
                        }
                        DispatchQueue.main.async { self.route(header: header, data: data, generation: myGeneration) }
                    }
                }
            }
        }
        errors.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            if chunk.isEmpty { handle.readabilityHandler = nil; return }
            let text = String(decoding: chunk, as: UTF8.self)
            DispatchQueue.main.async { self?.note(text) }
        }
        process.terminationHandler = { [weak self] finished in
            DispatchQueue.main.async { self?.terminated(code: finished.terminationStatus, generation: myGeneration) }
        }
        do {
            try process.run()
        } catch {
            state = .failed("The JobTracker engine could not start: \(error.localizedDescription)")
            return
        }
        self.process = process
        self.stdinHandle = input.fileHandleForWriting
    }

    /// Report a launch problem found before any process could start.
    public func fail(_ message: String) {
        state = .failed(message)
    }

    /// Ask the engine to exit after settling its work (stdin EOF).
    public func stop() {
        stopping = true
        let handle = stdinHandle
        stdinHandle = nil
        writeQueue.async { try? handle?.close() }
    }

    public func waitUntilExit(timeout: TimeInterval) -> Bool {
        guard let process else { return true }
        let deadline = Date().addingTimeInterval(timeout)
        while process.isRunning && Date() < deadline {
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
        }
        return !process.isRunning
    }

    private func terminated(code: Int32, generation finished: Int) {
        guard finished == generation else { return }
        let wasReady = isReady
        failAllPending(reason: "The engine stopped before replying")
        instance = nil
        process = nil
        stdinHandle = nil
        if stopping { state = .stopped; return }
        switch state {
        case .storeInUse, .incompatible, .failed:
            return
        default:
            break
        }
        let now = Date()
        restartTimes = restartTimes.filter { now.timeIntervalSince($0) < 60 } + [now]
        if restartTimes.count > 3 || (!wasReady && restartTimes.count > 1) {
            state = .failed("The JobTracker engine stopped unexpectedly (exit \(code)). Your saved data is unchanged.")
            return
        }
        state = .restarting("Reconnecting to the JobTracker engine…")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            guard let self, !self.stopping else { return }
            self.spawn()
        }
    }

    private func note(_ text: String) {
        diagnostics.append(contentsOf: text.split(separator: "\n").map(String.init))
        if diagnostics.count > 200 { diagnostics.removeFirst(diagnostics.count - 200) }
    }

    // MARK: routing

    private func route(header: FrameHeader, data: Data, generation frameGeneration: Int) {
        guard frameGeneration == generation else { return }
        switch header.kind {
        case "hello":
            handleHello(data)
        case "fatal":
            if header.reason == "store_in_use" {
                state = .storeInUse(header.message ?? "JobTracker is already open with this data.")
            } else {
                state = .failed(header.message ?? "The JobTracker engine could not start.")
            }
        case "response":
            guard header.engineInstance == instance else { return }
            handleResponse(EngineResponse(header: header, data: data))
        case "event":
            guard header.engineInstance == instance else { return }
            handleEvent(header: header, data: data)
        default:
            note("Ignored engine frame kind \(header.kind)")
        }
    }

    private func handleHello(_ data: Data) {
        guard let hello = try? JSONDecoder().decode(Hello.self, from: data) else {
            state = .incompatible("The JobTracker engine sent an unreadable greeting.")
            stop()
            return
        }
        guard hello.protocolVersion == EngineProtocol.version else {
            state = .incompatible("This engine speaks protocol \(hello.protocolVersion); the app needs \(EngineProtocol.version).")
            stop()
            return
        }
        instance = hello.engineInstance
        storeId = hello.storeId
        state = .ready(hello)
        onEvent?(.connected(hello))
        replayJournal()
    }

    private func handleResponse(_ response: EngineResponse) {
        guard let requestId = response.header.requestId else { return }
        if response.outcome.isSettled { journal.resolve(requestId) }
        guard let entry = pending.removeValue(forKey: requestId) else {
            if unresolved.remove(requestId) != nil || response.outcome.isSettled {
                onEvent?(.lateResolution(requestId: requestId, command: "", response: response))
            }
            return
        }
        entry.timeout?.cancel()
        unresolved.remove(requestId)
        if let continuation = entry.continuation {
            continuation.resume(returning: response)
        } else {
            onEvent?(.lateResolution(requestId: requestId, command: entry.command, response: response))
        }
    }

    private func handleEvent(header: FrameHeader, data: Data) {
        switch header.event {
        case "changed":
            onEvent?(.changed(topics: Set(header.topics ?? []), revision: header.revision ?? 0))
        case "sync_status":
            struct Wrapper: Decodable { let status: SyncStatusDTO }
            if let status = try? resultDecoder.decode(Wrapper.self, from: data).status {
                onEvent?(.syncStatus(status))
            }
        default:
            break
        }
    }

    private func failAllPending(reason: String) {
        let entries = pending
        pending.removeAll()
        for (requestId, entry) in entries {
            entry.timeout?.cancel()
            if entry.kind == .mutation { unresolved.insert(requestId) }
            entry.continuation?.resume(returning: .local(.unavailable, requestId: requestId, error: reason))
        }
    }

    private func replayJournal() {
        guard let storeId else { return }
        for envelope in journal.pending(storeId: storeId) where pending[envelope.requestId] == nil {
            unresolved.insert(envelope.requestId)
            pending[envelope.requestId] = Pending(command: envelope.command, kind: .mutation, continuation: nil, timeout: nil)
            write(requestId: envelope.requestId, command: envelope.command, params: envelope.params)
        }
    }

    // MARK: sending

    /// Send one command. Mutations are journaled first; on a lost reply the
    /// caller receives `.unavailable` and the same identity is retried later.
    @MainActor
    public func send(_ command: String, _ params: [String: JSONValue] = [:], kind: CommandKind,
                     requestId: String = UUID().uuidString.lowercased()) async -> EngineResponse {
        guard isReady, let storeId else {
            return .local(.unavailable, requestId: requestId, error: "The JobTracker engine is not connected")
        }
        if pending.count >= EngineProtocol.maxOutstanding {
            return .local(.unavailable, requestId: requestId, error: "Too many requests are waiting; try again")
        }
        if kind == .mutation {
            do {
                try journal.record(.init(requestId: requestId, command: command, params: params, storeId: storeId, createdAt: Date()))
            } catch {
                return .local(.storageError, requestId: requestId, error: "The change could not be prepared: \(error.localizedDescription)")
            }
        }
        return await withCheckedContinuation { continuation in
            let timeout = DispatchWorkItem { [weak self] in
                guard let self, let entry = self.pending.removeValue(forKey: requestId) else { return }
                if entry.kind == .mutation {
                    self.unresolved.insert(requestId)
                    // Keep listening: a late reply resolves the journal entry.
                    self.pending[requestId] = Pending(command: command, kind: .mutation, continuation: nil, timeout: nil)
                }
                entry.continuation?.resume(returning: .local(.unavailable, requestId: requestId,
                                                             error: "No reply yet. The change will be confirmed when the engine answers."))
            }
            pending[requestId] = Pending(command: command, kind: kind, continuation: continuation, timeout: timeout)
            DispatchQueue.main.asyncAfter(deadline: .now() + requestTimeout, execute: timeout)
            write(requestId: requestId, command: command, params: params)
        }
    }

    private func write(requestId: String, command: String, params: [String: JSONValue]) {
        guard let handle = stdinHandle, let frame = try? encodeCommand(requestId: requestId, command: command, params: params) else { return }
        writeQueue.async { [weak self] in
            do {
                try handle.write(contentsOf: frame)
            } catch {
                DispatchQueue.main.async { self?.note("Write to engine failed: \(error.localizedDescription)") }
            }
        }
    }
}
