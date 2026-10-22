import Foundation

/// Where the engine runs and which store it may own.
///
/// Packaged builds read `JTEngineMode` (`production` or `isolated`) and
/// `JTStoreFolder` from Info.plist and run the bundled helper in
/// `Contents/Resources/engine/`. A development run (no bundle helper)
/// uses `JOBTRACKER_ENGINE_PYTHON` + `JOBTRACKER_ENGINE_ROOT` to start
/// `python -m jobtracker.desktop` from a checkout. Without explicit settings
/// the app never selects a real store: it uses an isolated pilot store.
public struct EngineConfiguration: Equatable {
    public var launch: EngineLaunch
    public var mode: String
    public var storePath: URL?
    public var supportDirectory: URL

    public static let pilotFolder = "JobTracker Native Pilot"

    public static func resolve(bundle: Bundle = .main, environment: [String: String] = ProcessInfo.processInfo.environment) throws -> EngineConfiguration {
        let info = bundle.infoDictionary ?? [:]
        let mode = environment["JOBTRACKER_NATIVE_MODE"] ?? (info["JTEngineMode"] as? String) ?? "isolated"
        let folder = environment["JOBTRACKER_NATIVE_FOLDER"] ?? (info["JTStoreFolder"] as? String) ?? pilotFolder
        let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        let support = environment["JOBTRACKER_NATIVE_SUPPORT_DIR"].map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? appSupport.appendingPathComponent(mode == "production" ? "JobTracker" : folder, isDirectory: true)
        var arguments = ["--mode", mode]
        var store: URL? = nil
        if mode == "isolated" {
            store = environment["JOBTRACKER_NATIVE_DB"].map { URL(fileURLWithPath: $0) } ?? support.appendingPathComponent("jobtracker.db")
            arguments += ["--db", store!.path]
        } else if mode != "production" && mode != "development" {
            throw NSError(domain: "JobTracker", code: 2, userInfo: [NSLocalizedDescriptionKey: "Unknown engine mode \(mode)"])
        }

        let helper = bundle.bundleURL.appendingPathComponent("Contents/Resources/engine/jobtracker-engine")
        let launch: EngineLaunch
        if let python = environment["JOBTRACKER_ENGINE_PYTHON"], let root = environment["JOBTRACKER_ENGINE_ROOT"] {
            launch = EngineLaunch(executable: URL(fileURLWithPath: python), arguments: ["-m", "jobtracker.desktop"] + arguments,
                                  workingDirectory: URL(fileURLWithPath: root, isDirectory: true))
        } else if FileManager.default.isExecutableFile(atPath: helper.path) {
            launch = EngineLaunch(executable: helper, arguments: arguments)
        } else {
            throw NSError(domain: "JobTracker", code: 1, userInfo: [NSLocalizedDescriptionKey:
                "The bundled engine is missing. Reinstall JobTracker, or set JOBTRACKER_ENGINE_PYTHON and JOBTRACKER_ENGINE_ROOT for a development run."])
        }
        return EngineConfiguration(launch: launch, mode: mode, storePath: store, supportDirectory: support)
    }

    /// Private folder for unresolved request envelopes (per support folder).
    public var journalDirectory: URL { supportDirectory.appendingPathComponent("native-pending-requests", isDirectory: true) }
}
