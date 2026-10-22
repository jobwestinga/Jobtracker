import Foundation

protocol CheckSuite {}

private var failures: [String] = []
private var current = ""

func expect(_ condition: @autoclosure () throws -> Bool, _ message: @autoclosure () -> String = "", file: StaticString = #fileID, line: UInt = #line) {
    let passed = (try? condition()) ?? false
    if !passed { failures.append("\(current): \(file):\(line) \(message())") }
}

func runChecks(_ checks: [(String, () throws -> Void)]) -> Int32 {
    let filter = CommandLine.arguments.dropFirst().first
    var ran = 0
    for (name, body) in checks where filter == nil || name.contains(filter!) {
        current = name
        ran += 1
        do { try body() } catch { failures.append("\(name): threw \(error)") }
    }
    for failure in failures { print("FAIL \(failure)") }
    print("\(ran) checks, \(failures.count) failures")
    return failures.isEmpty ? 0 : 1
}
