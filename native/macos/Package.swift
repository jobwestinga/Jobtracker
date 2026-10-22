// swift-tools-version:5.9
// Native Mac frontend for JobTracker. Build outside iCloud:
//   swift build --package-path native/macos --scratch-path ~/Library/Caches/JobTracker/native-build
// The bundled Python engine (jobtracker.desktop) owns all data and rules.
import PackageDescription

let package = Package(
    name: "JobTrackerNative",
    platforms: [.macOS(.v12)],
    products: [
        .executable(name: "JobTracker", targets: ["JobTracker"]),
    ],
    targets: [
        .target(name: "JobTrackerKit", path: "Sources/JobTrackerKit"),
        .executableTarget(name: "JobTracker", dependencies: ["JobTrackerKit"], path: "Sources/JobTracker"),
        // Checks run as an executable (`swift run JobTrackerChecks`): the Command
        // Line Tools provide no macro plugins for swift-testing or XCTest UI.
        .executableTarget(name: "JobTrackerChecks", dependencies: ["JobTrackerKit"], path: "Sources/JobTrackerChecks"),
    ]
)
