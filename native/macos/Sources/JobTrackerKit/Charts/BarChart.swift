import AppKit
import SwiftUI

/// Pixel layout for the stacked bars; meaning (buckets, totals, intensity)
/// comes from the engine. Ported from `graphs_view._GraphCanvas`.
public struct BarChartLayout {
    public struct Bar {
        public let index: Int
        public let bucket: BucketDTO
        public let rect: CGRect
        public let radius: CGFloat
        public let segments: [(color: String, rect: CGRect)]
    }

    public struct Label {
        public let text: String
        public let origin: CGPoint
        public let font: NSFont
        public let style: HourStyle
        public let width: CGFloat
        public let height: CGFloat
    }

    public let size: CGSize
    public let chart: CGRect
    public let maxTotal: Int
    public let bars: [Bar]
    public let labels: [Label]
    public let dayLabels: [(text: String, rect: CGRect)]
    public let barWidth: CGFloat
    public let gap: CGFloat

    /// Minimum canvas width when the chart scrolls instead of fitting.
    public static func requiredWidth(count: Int, fitWidth: Bool) -> CGFloat {
        guard !fitWidth, count > 0 else { return 0 }
        let required = 44 + 22 + CGFloat(count) * 12 + CGFloat(count - 1) * 10
        return max(max(400, 80 + CGFloat(count) * 50), required)
    }

    public init(buckets: [BucketDTO], grouping: String, size: CGSize, fitWidth: Bool) {
        self.size = size
        chart = CGRect(x: 44, y: 46, width: max(80, size.width - 66), height: max(120, size.height - 90))
        let peak = buckets.map(\.totalSeconds).max() ?? 0
        maxTotal = Int((Double(max(peak, 3600)) / 1800).rounded(.up)) * 1800
        let count = max(1, buckets.count)
        gap = (fitWidth && count > 15) ? 4 : 10
        let raw = (chart.width - gap * CGFloat(count - 1)) / CGFloat(count)
        barWidth = fitWidth ? max(2, raw) : max(12, raw)

        // One shared base size keeps relative differences across dense views.
        var base: CGFloat = 14
        for bucket in buckets where bucket.totalSeconds > 0 {
            let style = HourStyle.forIntensity(seconds: bucket.intensitySeconds)
            let font = ChartText.font(size: 14 * style.scale, strong: style.strong)
            let width = ChartText.metrics(HourStyle.label(totalSeconds: bucket.totalSeconds), font: font).width
            base = min(base, 14 * max(1, barWidth + gap - 10) / max(1, width))
        }
        base = max(CGFloat(DesignTokens.lookup(DesignTokens.shared.roles.typography, "chartTotal")?.minimum ?? 9), base)

        var bars: [Bar] = []
        var labels: [Label] = []
        var days: [(String, CGRect)] = []
        var lastDayRight = -CGFloat.infinity
        let axisFont = NSFont.systemFont(ofSize: 9)
        for (index, bucket) in buckets.enumerated() {
            let x = chart.minX + CGFloat(index) * (barWidth + gap)
            let total = max(0, bucket.totalSeconds)
            let filled = chart.height * CGFloat(total) / CGFloat(maxTotal)
            let rect = CGRect(x: x, y: chart.maxY - filled, width: barWidth, height: filled)
            let radius = min(6, barWidth / 3, filled / 2)
            var segments: [(String, CGRect)] = []
            var cursor = chart.maxY
            for segment in Self.merged(bucket.segments) {
                let height = chart.height * CGFloat(segment.seconds) / CGFloat(maxTotal)
                cursor -= height
                // A half-pixel overlap removes hairlines between stacked colours.
                segments.append((segment.color, CGRect(x: x, y: cursor - 0.5, width: barWidth, height: max(1, height) + 1)))
            }
            bars.append(Bar(index: index, bucket: bucket, rect: rect, radius: max(0, radius), segments: segments))

            if barWidth > 20 && total > 0 {
                let style = HourStyle.forIntensity(seconds: bucket.intensitySeconds)
                let font = ChartText.font(size: base * style.scale, strong: style.strong)
                let text = HourStyle.label(totalSeconds: total)
                let metrics = ChartText.metrics(text, font: font)
                let height = metrics.ascent + metrics.descent
                labels.append(Label(text: text, origin: CGPoint(x: x + barWidth / 2 - metrics.width / 2, y: rect.minY - height - 9),
                                    font: font, style: style, width: metrics.width, height: height))
            }
            let dayText = Self.bucketLabel(bucket.date, grouping: grouping)
            let dayWidth = ChartText.metrics(dayText, font: axisFont).width
            let dayLeft = x + barWidth / 2 - dayWidth / 2
            if dayLeft >= lastDayRight + 6 {
                days.append((dayText, CGRect(x: dayLeft, y: chart.maxY + 8, width: dayWidth, height: 14)))
                lastDayRight = dayLeft + dayWidth
            }
        }
        self.bars = bars
        self.dayLabels = days

        // Largest days win label collisions; the size hierarchy survives.
        var occupied: [CGRect] = []
        var placed: [Label] = []
        for label in labels.sorted(by: { $0.style.scale > $1.style.scale }) {
            let bounds = CGRect(x: label.origin.x - 5, y: label.origin.y - 4, width: label.width + 10, height: label.height + 8)
            if occupied.contains(where: { $0.intersects(bounds) }) { continue }
            occupied.append(bounds)
            placed.append(label)
        }
        self.labels = placed
    }

    /// Consecutive same-subject segments become one seamless segment.
    static func merged(_ segments: [SegmentDTO]) -> [SegmentDTO] {
        var out: [SegmentDTO] = []
        for segment in segments where segment.seconds > 0 {
            if let last = out.last, last.subjectUid == segment.subjectUid, last.subjectName == segment.subjectName, last.color == segment.color {
                out[out.count - 1] = SegmentDTO(subjectUid: last.subjectUid, subjectName: last.subjectName, color: last.color,
                                                seconds: last.seconds + segment.seconds, startTime: last.startTime, endTime: segment.endTime)
            } else {
                out.append(segment)
            }
        }
        return out
    }

    public static func bucketLabel(_ iso: String, grouping: String) -> String {
        switch grouping {
        case "weekly":
            guard let date = LocalTime.parse(iso) else { return iso }
            let week = Calendar(identifier: .iso8601).component(.weekOfYear, from: date)
            return String(format: "W%02d", week)
        case "monthly": return LocalTime.format(iso, "MMM yy")
        default: return LocalTime.format(iso, "EEE dd")
        }
    }

    public static func bucketTitle(_ iso: String, grouping: String) -> String {
        switch grouping {
        case "weekly":
            guard let date = LocalTime.parse(iso) else { return iso }
            let week = Calendar(identifier: .iso8601).component(.weekOfYear, from: date)
            return String(format: "Week %02d \u{00B7} from %@", week, LocalTime.format(iso, "dd MMM yyyy"))
        case "monthly": return LocalTime.format(iso, "MMMM yyyy")
        default: return LocalTime.format(iso, "EEEE, dd MMMM yyyy")
        }
    }

    public static func summary(_ bucket: BucketDTO, grouping: String) -> String {
        var totals: [String: (String, Int)] = [:]
        var order: [String] = []
        for segment in bucket.segments {
            let key = segment.subjectName + segment.color
            if totals[key] == nil { order.append(key); totals[key] = (segment.subjectName, 0) }
            totals[key]!.1 += max(0, segment.seconds)
        }
        let entries = order.map { totals[$0]! }.sorted { $0.1 > $1.1 }
        var lines = [bucketTitle(bucket.date, grouping: grouping), String(format: "%.1f tracked hours", Double(bucket.totalSeconds) / 3600)]
        lines += entries.prefix(8).map { String(format: "%@ \u{00B7} %.1fh", $0.0, Double($0.1) / 3600) }
        if entries.count > 8 { lines.append("\u{2026} \(entries.count - 8) more") }
        return lines.joined(separator: "\n")
    }
}

struct BarChartView: View {
    let data: BreakdownDTO
    let onDay: (String) -> Void
    @Environment(\.theme) private var theme
    @Environment(\.displayScale) private var displayScale

    var body: some View {
        GeometryReader { proxy in
            let width = max(proxy.size.width, BarChartLayout.requiredWidth(count: data.buckets.count, fitWidth: data.fitWidth))
            let size = CGSize(width: width, height: max(360, proxy.size.height))
            let layout = BarChartLayout(buckets: data.buckets, grouping: data.grouping, size: size, fitWidth: data.fitWidth)
            ScrollView(.horizontal, showsIndicators: !data.fitWidth) {
                ZStack(alignment: .topLeading) {
                    canvas(layout)
                    ChartHitArea(regions: layout.bars.map { bar in
                        ChartHitArea.Region(rect: CGRect(x: bar.rect.minX, y: layout.chart.minY, width: bar.rect.width, height: layout.chart.height),
                                            tooltip: BarChartLayout.summary(bar.bucket, grouping: data.grouping),
                                            clickable: data.grouping == "daily",
                                            action: { onDay(bar.bucket.date) })
                    })
                    accessibilityLayer(layout)
                }
                .frame(width: size.width, height: size.height)
            }
            .disabled(false)
        }
        .frame(minHeight: 360)
    }

    private func canvas(_ layout: BarChartLayout) -> some View {
        Canvas { context, size in
            let outer = CGRect(origin: .zero, size: size).insetBy(dx: 0.5, dy: 0.5)
            context.fill(Path(roundedRect: outer, cornerRadius: Radius.chart), with: .color(theme.bgSecondary.alpha(238 / 255).color))
            context.stroke(Path(roundedRect: outer, cornerRadius: Radius.chart), with: .color(theme.border.alpha(160 / 255).color), lineWidth: 1)
            let chart = layout.chart
            guard !data.buckets.isEmpty else {
                context.draw(Text("No tracked data yet").foregroundColor(theme.textDimmed.color), at: CGPoint(x: chart.midX, y: chart.midY))
                return
            }
            // Minimal grid: top and baseline only.
            for y in [chart.minY, chart.maxY] {
                var line = Path()
                line.move(to: CGPoint(x: chart.minX, y: y.rounded() + 0.5))
                line.addLine(to: CGPoint(x: chart.maxX, y: y.rounded() + 0.5))
                context.stroke(line, with: .color(theme.border.alpha(90 / 255).color), lineWidth: 1)
            }
            context.draw(Text(String(format: "%.1fh", Double(layout.maxTotal) / 3600)).font(.system(size: 9)).foregroundColor(theme.textDimmed.color),
                         at: CGPoint(x: 4, y: chart.minY), anchor: .leading)
            context.draw(Text("0.0h").font(.system(size: 9)).foregroundColor(theme.textDimmed.color), at: CGPoint(x: 4, y: chart.maxY), anchor: .leading)

            for bar in layout.bars where bar.rect.height > 0 {
                let clip = Path(roundedRect: bar.rect, cornerRadius: bar.radius, style: .continuous)
                context.drawLayer { layer in
                    layer.clip(to: clip)
                    for segment in bar.segments {
                        let base = RGBA(hex: segment.color)
                        layer.fill(Path(segment.rect), with: .linearGradient(
                            Gradient(stops: [.init(color: base.scaledValue(1.08).color, location: 0), .init(color: base.color, location: 0.45),
                                             .init(color: base.scaledValue(1 / 1.08).color, location: 1)]),
                            startPoint: CGPoint(x: bar.rect.minX, y: 0), endPoint: CGPoint(x: bar.rect.maxX, y: 0)))
                    }
                }
                context.stroke(Path(roundedRect: bar.rect.insetBy(dx: 0.4, dy: 0.4), cornerRadius: bar.radius, style: .continuous),
                               with: .color(theme.textPrimary.alpha(35 / 255).color), lineWidth: 0.8)
            }
            for day in layout.dayLabels {
                context.draw(Text(day.text).font(.system(size: 9)).foregroundColor(theme.textDimmed.color),
                             at: CGPoint(x: day.rect.midX, y: day.rect.midY))
            }
            let scale = displayScale
            let bloom = DesignTokens.shared.roles.hourTiers.bloom
            context.withCGContext { cg in
                for label in layout.labels {
                    if label.style.strong {
                        let radius = CGFloat(label.style.peak ? bloom.peakRadius : bloom.strongRadius)
                        let alpha = label.style.peak ? bloom.peakAlpha : bloom.strongAlpha
                        let key = BloomCache.Key(text: label.text, fontSize: label.font.pointSize, strong: true,
                                                 color: label.style.color.alpha(alpha), radius: radius, scale: scale)
                        if let mask = BloomCache.shared.mask(for: key) {
                            let rect = CGRect(x: label.origin.x + mask.offset.x, y: label.origin.y + mask.offset.y, width: mask.size.width, height: mask.size.height)
                            cg.saveGState()
                            cg.translateBy(x: rect.minX, y: rect.maxY)
                            cg.scaleBy(x: 1, y: -1)
                            cg.draw(mask.image, in: CGRect(origin: .zero, size: rect.size))
                            cg.restoreGState()
                        }
                    }
                    let color = label.style.strong ? theme.textPrimary.cgColor : label.style.color.cgColor
                    ChartText.draw(label.text, font: label.font, color: color, at: label.origin, in: cg)
                }
            }
        }
    }

    private func accessibilityLayer(_ layout: BarChartLayout) -> some View {
        ZStack(alignment: .topLeading) {
            ForEach(layout.bars, id: \.index) { bar in
                Color.clear
                    .frame(width: max(1, bar.rect.width), height: layout.chart.height)
                    .offset(x: bar.rect.minX, y: layout.chart.minY)
                    .accessibilityElement()
                    .accessibilityLabel(BarChartLayout.summary(bar.bucket, grouping: data.grouping).replacingOccurrences(of: "\n", with: ", "))
                    .accessibilityAddTraits(data.grouping == "daily" ? .isButton : [])
                    .accessibilityAction { if data.grouping == "daily" { onDay(bar.bucket.date) } }
                    .allowsHitTesting(false)
            }
        }
    }
}
