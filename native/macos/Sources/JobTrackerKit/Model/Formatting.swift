import Foundation

/// Naive local timestamps, exactly as the engine stores them.
///
/// The engine owns time rules. The client only needs to (a) capture the local
/// wall-clock moment of a click and (b) display stored strings. Neither step
/// involves a timezone conversion: a naive string is interpreted in the Mac's
/// current calendar, like `datetime.fromisoformat` does in Python.
public enum LocalTime {
    private static let calendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .current
        return calendar
    }()

    /// `datetime.now().isoformat()`-compatible string for a captured moment.
    public static func isoString(_ date: Date, microseconds: Bool = true) -> String {
        let parts = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second, .nanosecond], from: date)
        var text = String(format: "%04d-%02d-%02dT%02d:%02d:%02d", parts.year!, parts.month!, parts.day!, parts.hour!, parts.minute!, parts.second!)
        let micros = (parts.nanosecond ?? 0) / 1000
        if microseconds && micros > 0 { text += String(format: ".%06d", micros) }
        return text
    }

    /// Parse `YYYY-MM-DD[THH:MM[:SS[.ffffff]]]` (also accepting a space) as local time.
    public static func parse(_ text: String?) -> Date? {
        guard let text, text.count >= 10 else { return nil }
        let normalized = text.replacingOccurrences(of: " ", with: "T")
        let parts = normalized.split(separator: "T", maxSplits: 1)
        let day = parts[0].split(separator: "-").compactMap { Int($0) }
        guard day.count == 3 else { return nil }
        var components = DateComponents(year: day[0], month: day[1], day: day[2], hour: 0, minute: 0, second: 0)
        if parts.count == 2 {
            var clock = String(parts[1])
            if let zone = clock.firstIndex(where: { $0 == "+" || $0 == "Z" }) { clock = String(clock[..<zone]) }
            let pieces = clock.split(separator: ":")
            components.hour = pieces.count > 0 ? Int(pieces[0]) : 0
            components.minute = pieces.count > 1 ? Int(pieces[1]) : 0
            if pieces.count > 2 {
                let seconds = pieces[2].split(separator: ".")
                components.second = Int(seconds[0])
                if seconds.count > 1 {
                    let fraction = String(seconds[1].prefix(6)).padding(toLength: 6, withPad: "0", startingAt: 0)
                    components.nanosecond = (Int(fraction) ?? 0) * 1000
                }
            }
        }
        return calendar.date(from: components)
    }

    public static func dayString(_ date: Date) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year!, parts.month!, parts.day!)
    }

    public static func format(_ text: String?, _ pattern: String) -> String {
        guard let date = parse(text) else { return text ?? "" }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_GB")
        formatter.calendar = calendar
        formatter.timeZone = calendar.timeZone
        formatter.dateFormat = pattern
        return formatter.string(from: date)
    }

    public static func clock(_ text: String?) -> String { format(text, "HH:mm") }
}

public enum DurationText {
    /// Subject card total, matching `subject_item.format_duration`.
    public static func tracked(_ seconds: Int) -> String {
        let seconds = max(0, seconds)
        let h = seconds / 3600, m = (seconds % 3600) / 60, s = seconds % 60
        if h > 0 { return "\(h)h \(m)m" }
        if m > 0 { return "\(m)m \(s)s" }
        return "\(s)s"
    }

    /// Running clock `HH:MM:SS`.
    public static func clock(_ seconds: Int) -> String {
        let seconds = max(0, seconds)
        return String(format: "%02d:%02d:%02d", seconds / 3600, (seconds % 3600) / 60, seconds % 60)
    }

    /// Compact `1h 05m` / `45m`, matching the timer's idle summary.
    public static func short(_ seconds: Int) -> String {
        let seconds = max(0, seconds)
        let h = seconds / 3600, m = (seconds % 3600) / 60
        return h > 0 ? String(format: "%dh %02dm", h, m) : "\(m)m"
    }

    /// Session duration label used by history rows and the W1 panel.
    public static func session(_ seconds: Int) -> String {
        let seconds = max(0, seconds)
        let h = seconds / 3600, m = (seconds % 3600) / 60
        if h > 0 { return m > 0 ? "\(h)h \(m)m" : "\(h)h" }
        if m > 0 { return "\(m)m" }
        return "\(seconds)s"
    }

    /// Preset/move button label: 15m, 1h, 1h 15m, 1 day.
    public static func preset(minutes: Int) -> String {
        if minutes % 1440 == 0 { return minutes == 1440 ? "1 day" : "\(minutes / 1440) days" }
        let h = minutes / 60, m = minutes % 60
        if h == 0 { return "\(m)m" }
        return m == 0 ? "\(h)h" : "\(h)h \(m)m"
    }

    public static func hours(_ seconds: Int) -> String { String(format: "%.1fh", Double(seconds) / 3600) }
}
