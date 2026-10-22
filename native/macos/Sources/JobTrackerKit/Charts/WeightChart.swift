import AppKit
import SwiftUI

/// Draws engine-fitted weight data (calendar-ordinal x, kg y). Port of the Qt
/// WeightChart: neutral dots, accent trend with area fill, year lines with a
/// separate label row, hover crosshair and click-to-edit.
struct WeightChartView: View {
    let data: WeightViewDTO
    let showDots: Bool
    let highContrast: Bool
    let onEdit: (String) -> Void
    @Environment(\.theme) private var theme
    @StateObject private var hovered = ViewState<Int?>(nil)

    struct Geometry {
        let rect: CGRect
        let first: Double, last: Double, low: Double, high: Double
        func point(_ x: Double, _ y: Double) -> CGPoint {
            CGPoint(x: rect.minX + CGFloat((x - first) / (last - first)) * rect.width,
                    y: rect.maxY - CGFloat((y - low) / (high - low)) * rect.height)
        }
    }

    static func geometry(_ data: WeightViewDTO, size: CGSize) -> Geometry? {
        guard let axis = data.axis, !data.points.isEmpty else { return nil }
        let rect = CGRect(x: 48, y: 16, width: max(1, size.width - 64), height: max(1, size.height - 62))
        let ys = data.points.map(\.y) + data.trend.compactMap { $0.count > 1 ? $0[1] : nil }
        var low = ys.min() ?? 0, high = ys.max() ?? 1
        let pad = max(0.3, (high - low) * 0.12)
        low -= pad; high += pad
        return Geometry(rect: rect, first: axis.first, last: axis.last, low: low, high: high)
    }

    var body: some View {
        GeometryReader { proxy in
            let geometry = Self.geometry(data, size: proxy.size)
            ZStack(alignment: .topLeading) {
                canvas(geometry, size: proxy.size)
                if let geometry {
                    MouseTracker(onMove: { location in hovered.value = location.flatMap { nearest($0, geometry) } },
                                 onClick: { location in
                                     if let index = nearest(location, geometry), let uid = data.points[index].uid { onEdit(uid) }
                                 })
                }
            }
        }
        .frame(minHeight: 195)
        .accessibilityElement()
        .accessibilityLabel(accessibilitySummary)
    }

    private var accessibilitySummary: String {
        guard let summary = data.summary else { return "No measurements in this range" }
        return "Weight chart, \(summary.count) readings, latest \(WeightStore.text(summary.latest)) kilograms, change \(String(format: "%+.2f", summary.change)) kilograms"
    }

    private func nearest(_ location: CGPoint, _ geometry: Geometry) -> Int? {
        var best: (Int, CGFloat)?
        for (index, point) in data.points.enumerated() {
            let p = geometry.point(point.x, point.y)
            let distance = pow(p.x - location.x, 2) + pow(p.y - location.y, 2)
            if distance <= 169 && distance < (best?.1 ?? .infinity) { best = (index, distance) }
        }
        return best?.0
    }

    private func canvas(_ geometry: Geometry?, size: CGSize) -> some View {
        Canvas { context, size in
            let label = (highContrast ? theme.textPrimary : theme.textSecondary)
            context.fill(Path(CGRect(origin: .zero, size: size)), with: .color(theme.bgSecondary.color))
            guard let g = geometry, let axis = data.axis else {
                context.draw(Text("No measurements in this range").foregroundColor(label.color), at: CGPoint(x: size.width / 2, y: size.height / 2))
                return
            }
            let grid = theme.textSecondary.alpha(highContrast ? 130 / 255 : 40 / 255)
            for fraction in [0.0, 0.5, 1.0] {
                let y = g.rect.maxY - CGFloat(fraction) * g.rect.height
                var line = Path(); line.move(to: CGPoint(x: g.rect.minX, y: y)); line.addLine(to: CGPoint(x: g.rect.maxX, y: y))
                context.stroke(line, with: .color(grid.color), lineWidth: 1)
                context.draw(Text(String(format: "%.1f", g.low + (g.high - g.low) * fraction)).font(.system(size: 11)).foregroundColor(label.color),
                             at: CGPoint(x: 42, y: y), anchor: .trailing)
            }
            context.draw(Text(axis.startLabel).font(.system(size: 11)).foregroundColor(label.color), at: CGPoint(x: g.rect.minX, y: g.rect.maxY + 6), anchor: .topLeading)
            context.draw(Text(axis.endLabel).font(.system(size: 11)).foregroundColor(label.color), at: CGPoint(x: g.rect.maxX, y: g.rect.maxY + 6), anchor: .topTrailing)
            var labelRight = -CGFloat.infinity
            for tick in axis.years where tick.count == 2 {
                let x = g.point(tick[0], g.low).x
                var line = Path(); line.move(to: CGPoint(x: x, y: g.rect.minY)); line.addLine(to: CGPoint(x: x, y: g.rect.maxY))
                context.stroke(line, with: .color(grid.color), lineWidth: 0.7)
                let text = String(Int(tick[1]))
                let width = ChartText.metrics(text, font: .systemFont(ofSize: 11)).width + 4
                let center = max(g.rect.minX + width / 2, min(g.rect.maxX - width / 2, x))
                if center - width / 2 < labelRight + 10 { continue }
                context.draw(Text(text).font(.system(size: 11)).foregroundColor(label.color), at: CGPoint(x: center, y: g.rect.maxY + 24), anchor: .top)
                labelRight = center + width / 2
            }
            if axis.years.isEmpty {
                context.draw(Text(axis.fallbackYear).font(.system(size: 11)).foregroundColor(label.color), at: CGPoint(x: g.rect.minX, y: g.rect.maxY + 24), anchor: .topLeading)
            }

            var clipped = context
            clipped.clip(to: Path(g.rect.insetBy(dx: -4, dy: -4)))
            let accent = theme.accent
            let trend = data.trend.compactMap { $0.count > 1 ? g.point($0[0], $0[1]) : nil }
            if trend.count >= 2 {
                var area = Path()
                area.move(to: CGPoint(x: trend[0].x, y: g.rect.maxY))
                for point in trend { area.addLine(to: point) }
                area.addLine(to: CGPoint(x: trend[trend.count - 1].x, y: g.rect.maxY))
                area.closeSubpath()
                clipped.fill(area, with: .linearGradient(Gradient(colors: [accent.alpha((highContrast ? 90 : 68) / 255).color, accent.alpha(0).color]),
                                                         startPoint: CGPoint(x: 0, y: g.rect.minY), endPoint: CGPoint(x: 0, y: g.rect.maxY)))
            }
            let perPoint = g.rect.width / CGFloat(max(1, data.points.count))
            let dense = perPoint < 5.5
            func dots() {
                guard showDots else { return }
                let fill = highContrast ? theme.textPrimary : RGBA(hex: "#E2E8F0")
                for (index, point) in data.points.enumerated() where index != hovered.value {
                    let p = g.point(point.x, point.y)
                    if dense {
                        clipped.fill(Path(ellipseIn: CGRect(x: p.x - 1.35, y: p.y - 1.35, width: 2.7, height: 2.7)), with: .color(fill.alpha(highContrast ? 180 / 255 : 115 / 255).color))
                    } else {
                        let r: CGFloat = perPoint >= 11 ? 2.6 : 1.9
                        let dot = Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r, width: r * 2, height: r * 2))
                        clipped.fill(dot, with: .color(fill.alpha(highContrast ? 245 / 255 : 225 / 255).color))
                        clipped.stroke(dot, with: .color(theme.bgSecondary.color), lineWidth: perPoint >= 11 ? 1.1 : 0.7)
                    }
                }
            }
            if dense { dots() }
            if trend.count >= 2 {
                var line = Path(); line.move(to: trend[0]); for point in trend.dropFirst() { line.addLine(to: point) }
                clipped.stroke(line, with: .color(accent.color), style: StrokeStyle(lineWidth: highContrast ? 3 : 2.5, lineCap: .round, lineJoin: .round))
            }
            if !dense { dots() }
            if let hoveredIndex = hovered.value, data.points.indices.contains(hoveredIndex) {
                let point = data.points[hoveredIndex]
                let p = g.point(point.x, point.y)
                var cross = Path(); cross.move(to: CGPoint(x: p.x, y: g.rect.minY)); cross.addLine(to: CGPoint(x: p.x, y: g.rect.maxY))
                clipped.stroke(cross, with: .color(accent.color), style: StrokeStyle(lineWidth: 1, dash: [4, 3]))
                let dot = Path(ellipseIn: CGRect(x: p.x - 4.4, y: p.y - 4.4, width: 8.8, height: 8.8))
                clipped.fill(dot, with: .color(accent.color))
                clipped.stroke(dot, with: .color(theme.bgSecondary.color), lineWidth: 1.4)
                let text = "\(point.measuredAt.prefix(10)) \u{00B7} \(WeightStore.text(point.y)) kg"
                let width = ChartText.metrics(text, font: .systemFont(ofSize: 10, weight: .semibold)).width + 16
                let pill = CGRect(x: max(g.rect.minX, min(g.rect.maxX - width, p.x - width / 2)), y: max(4, p.y - 30), width: width, height: 22)
                context.fill(Path(roundedRect: pill, cornerRadius: 6), with: .color(theme.bgPrimary.color))
                context.stroke(Path(roundedRect: pill, cornerRadius: 6), with: .color(accent.color), lineWidth: 1.2)
                context.draw(Text(text).font(.system(size: 10, weight: .semibold)).foregroundColor(theme.textPrimary.color), at: CGPoint(x: pill.midX, y: pill.midY))
            }
        }
    }
}

/// AppKit mouse tracking for hover/click inside a SwiftUI chart (macOS 12).
struct MouseTracker: NSViewRepresentable {
    let onMove: (CGPoint?) -> Void
    let onClick: (CGPoint) -> Void

    func makeNSView(context: Context) -> TrackerView {
        let view = TrackerView()
        view.onMove = onMove
        view.onClick = onClick
        return view
    }

    func updateNSView(_ view: TrackerView, context: Context) {
        view.onMove = onMove
        view.onClick = onClick
    }

    final class TrackerView: NSView {
        var onMove: ((CGPoint?) -> Void)?
        var onClick: ((CGPoint) -> Void)?
        override var isFlipped: Bool { true }

        override func updateTrackingAreas() {
            super.updateTrackingAreas()
            trackingAreas.forEach(removeTrackingArea)
            addTrackingArea(NSTrackingArea(rect: bounds, options: [.mouseMoved, .mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect], owner: self))
        }

        override func mouseMoved(with event: NSEvent) { onMove?(convert(event.locationInWindow, from: nil)) }
        override func mouseExited(with event: NSEvent) { onMove?(nil) }
        override func mouseDown(with event: NSEvent) { onClick?(convert(event.locationInWindow, from: nil)) }
    }
}
