import Foundation

/// A tangent touching the fitted weight trend (calendar-ordinal x, kg y), with
/// the daily energy surplus or deficit its slope implies. Mirrors the phone's
/// `trendTangent` in web/features/weight/math.js.
struct WeightTangent: Equatable {
    /// Losses during hypertrophy training are treated as adipose tissue
    /// (~7,700 kcal/kg). Gains are assumed to be about half lean tissue
    /// (~3,500 kcal/kg including synthesis) and half fat, ~5,600 kcal/kg.
    static let kcalPerKgLoss = 7700.0
    static let kcalPerKgGain = 5600.0

    let x: Double, y: Double
    /// Trend slope in kilograms per day.
    let perDay: Double
    var kcal: Double { Self.energy(perDay) }

    static func energy(_ perDay: Double) -> Double {
        guard perDay.isFinite else { return 0 }
        return perDay * (perDay < 0 ? kcalPerKgLoss : kcalPerKgGain)
    }

    /// The trend's height at x, linearly between samples; nil outside it.
    static func value(_ trend: [[Double]], at x: Double) -> Double? {
        let samples = trend.filter { $0.count > 1 }
        guard let first = samples.first, let last = samples.last, x.isFinite, x >= first[0], x <= last[0] else { return nil }
        var low = 0, high = samples.count - 1
        while high - low > 1 {
            let middle = (low + high) / 2
            if samples[middle][0] <= x { low = middle } else { high = middle }
        }
        let a = samples[low], b = samples[high]
        return b[0] == a[0] ? a[1] : a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0])
    }

    /// Central difference across `reach` days on each side, one-sided at the ends.
    static func at(_ x: Double, trend: [[Double]], reach: Double = 3) -> WeightTangent? {
        let samples = trend.filter { $0.count > 1 }
        guard samples.count >= 2, let y = value(samples, at: x) else { return nil }
        let a = max(samples[0][0], x - reach), b = min(samples[samples.count - 1][0], x + reach)
        guard b > a, let ya = value(samples, at: a), let yb = value(samples, at: b) else { return nil }
        return WeightTangent(x: x, y: y, perDay: (yb - ya) / (b - a))
    }

    /// "12 Oct 2026" for a calendar ordinal (Python `date.toordinal()`, 1970-01-01 = 719163).
    static func dayLabel(_ ordinal: Double) -> String {
        guard ordinal.isFinite else { return "" }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_GB")
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.dateFormat = "d MMM yyyy"
        return formatter.string(from: Date(timeIntervalSince1970: (ordinal.rounded(.down) - 719_163) * 86_400))
    }

    /// "−462 kcal/day · −0.42 kg/wk".
    var label: String {
        func signed(_ value: Double, _ digits: Int) -> String {
            let text = String(format: "%.\(digits)f", abs(value))
            return Double(text) == 0 ? text : (value < 0 ? "\u{2212}" : "+") + text
        }
        return "\(signed(kcal, 0)) kcal/day \u{00B7} \(signed(perDay * 7, 2)) kg/wk"
    }
}
