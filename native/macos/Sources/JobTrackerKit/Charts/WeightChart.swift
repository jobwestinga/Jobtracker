import AppKit
import SwiftUI

/// Draws engine-fitted weight data (calendar-ordinal x, kg y). Port of the Qt
/// WeightChart: neutral dots, accent trend with area fill, year lines with a
/// separate label row, hover crosshair and click-to-edit. In tangent mode the
/// pointer instead places a tangent to the trend with its kcal/day.
struct WeightChartView: View {
    let data: WeightViewDTO
    let showDots: Bool
    let highContrast: Bool
    @Binding var viewport: WeightViewport
    var tangentMode = false
    let onEdit: (String) -> Void
    @Environment(\.theme) private var theme
    @StateObject private var hovered = ViewState<Int?>(nil)
    @StateObject private var tangentX = ViewState<Double?>(nil)
    static let tangentColor = RGBA(hex: "#FBBF24")

    struct Geometry {
        let rect: CGRect
        let first: Double, last: Double, low: Double, high: Double
        func point(_ x: Double, _ y: Double) -> CGPoint {
            CGPoint(x: rect.minX + CGFloat((x - first) / (last - first)) * rect.width,
                    y: rect.maxY - CGFloat((y - low) / (high - low)) * rect.height)
        }
    }

    static func geometry(_ data: WeightViewDTO, size: CGSize, viewport: WeightViewport = WeightViewport()) -> Geometry? {
        guard let axis = data.axis, !data.points.isEmpty else { return nil }
        let rect = CGRect(x: 48, y: 16, width: max(1, size.width - 64), height: max(1, size.height - 62))
        let ys = data.points.map(\.y) + data.trend.compactMap { $0.count > 1 ? $0[1] : nil }
        var low = ys.min() ?? 0, high = ys.max() ?? 1
        let pad = max(0.3, (high - low) * 0.12)
        low -= pad; high += pad
        let spanX = max(1, axis.last - axis.first), spanY = high - low
        return Geometry(rect: rect, first: axis.first + spanX * viewport.x.lowerBound, last: axis.first + spanX * viewport.x.upperBound,
                        low: low + spanY * viewport.y.lowerBound, high: low + spanY * viewport.y.upperBound)
    }

    var body: some View {
        GeometryReader { proxy in
            let geometry = Self.geometry(data, size: proxy.size, viewport: viewport)
            ZStack(alignment: .topLeading) {
                canvas(geometry, size: proxy.size)
                if let geometry {
                    MouseTracker(onMove: { location in
                                     guard tangentMode else { hovered.value = location.flatMap { nearest($0, geometry) }; return }
                                     hovered.value = nil
                                     if let location, geometry.rect.contains(location) { tangentX.value = Self.ordinal(location, geometry) }
                                 },
                                 onClick: { location in
                                     if tangentMode {
                                         if geometry.rect.contains(location) { tangentX.value = Self.ordinal(location, geometry) }
                                     } else if let index = nearest(location, geometry), let uid = data.points[index].uid { onEdit(uid) }
                                 }, onMagnify: { location, amount, modifiers in
                                     guard geometry.rect.contains(location) else { return false }
                                     hovered.value = nil
                                     let anchor = CGPoint(x: (location.x - geometry.rect.minX) / geometry.rect.width,
                                                          y: (geometry.rect.maxY - location.y) / geometry.rect.height)
                                     viewport.zoom(exp(Double(amount) * 2), anchor: anchor,
                                                   horizontal: !modifiers.contains(.option), vertical: !modifiers.contains(.shift))
                                     return true
                                 }, onScroll: { location, delta, modifiers in
                                     guard geometry.rect.contains(location) else { return false }
                                     let horizontal = modifiers.contains(.shift), vertical = modifiers.contains(.option)
                                     if horizontal || vertical {
                                         let anchor = CGPoint(x: (location.x - geometry.rect.minX) / geometry.rect.width,
                                                              y: (geometry.rect.maxY - location.y) / geometry.rect.height)
                                         viewport.zoom(exp(Double(delta.y) * 0.015), anchor: anchor, horizontal: horizontal, vertical: vertical)
                                     } else {
                                         guard viewport.isZoomed else { return false }
                                         viewport.pan(horizontal: -Double(delta.x / geometry.rect.width), vertical: Double(delta.y / geometry.rect.height))
                                     }
                                     hovered.value = nil
                                     return true
                                 })
                }
            }
        }
        .frame(minHeight: 195)
        .onChange(of: tangentMode) { _ in tangentX.value = nil }
        .accessibilityElement()
        .accessibilityLabel(accessibilitySummary)
    }

    private var accessibilitySummary: String {
        guard let summary = data.summary else { return "No measurements in this range" }
        return "Weight chart, \(summary.count) readings, latest \(WeightStore.text(summary.latest)) kilograms, change \(String(format: "%+.2f", summary.change)) kilograms"
    }

    static func ordinal(_ location: CGPoint, _ geometry: Geometry) -> Double {
        geometry.first + Double((location.x - geometry.rect.minX) / geometry.rect.width) * (geometry.last - geometry.first)
    }

    /// The tangent at x, its slope measured over a few days (less when zoomed in).
    static func tangent(at x: Double, data: WeightViewDTO, geometry: Geometry) -> WeightTangent? {
        guard x >= geometry.first && x <= geometry.last else { return nil }
        let samples = data.trend.filter { $0.count > 1 }
        guard let first = samples.first?[0], let last = samples.last?[0] else { return nil }
        return WeightTangent.at(max(first, min(last, x)), trend: samples, reach: max(0.5, min(3, (geometry.last - geometry.first) * 0.02)))
    }

    private func nearest(_ location: CGPoint, _ geometry: Geometry) -> Int? {
        var best: (Int, CGFloat)?
        for (index, point) in data.points.enumerated() {
            let p = geometry.point(point.x, point.y)
            guard geometry.rect.contains(p), geometry.rect.insetBy(dx: -13, dy: -13).contains(location) else { continue }
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
            let visible = data.points.filter { $0.x >= g.first && $0.x <= g.last }
            let start = viewport.x == 0...1 ? axis.startLabel : visible.first.map { LocalTime.format($0.measuredAt, "dd MMM yyyy") } ?? ""
            let end = viewport.x == 0...1 ? axis.endLabel : visible.last.map { LocalTime.format($0.measuredAt, "dd MMM yyyy") } ?? ""
            context.draw(Text(start).font(.system(size: 11)).foregroundColor(label.color), at: CGPoint(x: g.rect.minX, y: g.rect.maxY + 6), anchor: .topLeading)
            context.draw(Text(end).font(.system(size: 11)).foregroundColor(label.color), at: CGPoint(x: g.rect.maxX, y: g.rect.maxY + 6), anchor: .topTrailing)
            var labelRight = -CGFloat.infinity
            for tick in axis.years where tick.count == 2 {
                let x = g.point(tick[0], g.low).x
                guard x >= g.rect.minX && x <= g.rect.maxX else { continue }
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
            let perPoint = g.rect.width / CGFloat(max(1, visible.count))
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
            if tangentMode, let x = tangentX.value, let tangent = Self.tangent(at: x, data: data, geometry: g) {
                let color = Self.tangentColor
                func at(_ x: Double) -> CGPoint { g.point(x, tangent.y + tangent.perDay * (x - tangent.x)) }
                var line = Path(); line.move(to: at(g.first)); line.addLine(to: at(g.last))
                clipped.stroke(line, with: .color(color.color), style: StrokeStyle(lineWidth: 1.6, lineCap: .round, dash: [6, 4]))
                let p = g.point(tangent.x, tangent.y)
                let dot = Path(ellipseIn: CGRect(x: p.x - 4.5, y: p.y - 4.5, width: 9, height: 9))
                clipped.fill(dot, with: .color(color.color))
                clipped.stroke(dot, with: .color(theme.bgSecondary.color), lineWidth: 1.4)
                let text = "\(WeightTangent.dayLabel(tangent.x)) \u{00B7} \(tangent.label)"
                let width = ChartText.metrics(text, font: .systemFont(ofSize: 11, weight: .semibold)).width + 18
                let top = p.y - 36 >= g.rect.minY ? p.y - 36 : min(g.rect.maxY - 24, p.y + 12)
                let pill = CGRect(x: max(g.rect.minX, min(g.rect.maxX - width, p.x - width / 2)), y: top, width: width, height: 24)
                context.fill(Path(roundedRect: pill, cornerRadius: 6), with: .color(theme.bgPrimary.color))
                context.stroke(Path(roundedRect: pill, cornerRadius: 6), with: .color(color.color), lineWidth: 1.2)
                context.draw(Text(text).font(.system(size: 11, weight: .semibold)).foregroundColor(theme.textPrimary.color), at: CGPoint(x: pill.midX, y: pill.midY))
            }
        }
    }
}

/// AppKit mouse tracking for hover/click inside a SwiftUI chart (macOS 12).
struct MouseTracker: NSViewRepresentable {
    let onMove: (CGPoint?) -> Void
    let onClick: (CGPoint) -> Void
    let onMagnify: (CGPoint, CGFloat, NSEvent.ModifierFlags) -> Bool
    let onScroll: (CGPoint, CGPoint, NSEvent.ModifierFlags) -> Bool

    func makeNSView(context: Context) -> TrackerView {
        let view = TrackerView()
        view.onMove = onMove
        view.onClick = onClick
        view.onMagnify = onMagnify
        view.onScroll = onScroll
        return view
    }

    func updateNSView(_ view: TrackerView, context: Context) {
        view.onMove = onMove
        view.onClick = onClick
        view.onMagnify = onMagnify
        view.onScroll = onScroll
    }

    final class TrackerView: NSView {
        var onMove: ((CGPoint?) -> Void)?
        var onClick: ((CGPoint) -> Void)?
        var onMagnify: ((CGPoint, CGFloat, NSEvent.ModifierFlags) -> Bool)?
        var onScroll: ((CGPoint, CGPoint, NSEvent.ModifierFlags) -> Bool)?
        override var isFlipped: Bool { true }

        override func updateTrackingAreas() {
            super.updateTrackingAreas()
            trackingAreas.forEach(removeTrackingArea)
            addTrackingArea(NSTrackingArea(rect: bounds, options: [.mouseMoved, .mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect], owner: self))
        }

        override func mouseMoved(with event: NSEvent) { onMove?(convert(event.locationInWindow, from: nil)) }
        override func mouseExited(with event: NSEvent) { onMove?(nil) }
        override func mouseDown(with event: NSEvent) { onClick?(convert(event.locationInWindow, from: nil)) }
        override func magnify(with event: NSEvent) {
            if onMagnify?(convert(event.locationInWindow, from: nil), event.magnification, event.modifierFlags) != true { super.magnify(with: event) }
        }
        override func scrollWheel(with event: NSEvent) {
            let delta = CGPoint(x: event.scrollingDeltaX, y: event.scrollingDeltaY)
            if onScroll?(convert(event.locationInWindow, from: nil), delta, event.modifierFlags) != true { super.scrollWheel(with: event) }
        }
    }
}
