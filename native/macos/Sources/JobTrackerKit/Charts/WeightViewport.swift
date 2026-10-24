import Foundation

/// Normalised display bounds, independent of measurement values and drafts.
/// Both axes clamp to the loaded history; zooming never fetches or saves data.
struct WeightViewport: Equatable {
    var x: ClosedRange<Double> = 0...1
    var y: ClosedRange<Double> = 0...1
    var isZoomed: Bool { x != 0...1 || y != 0...1 }

    mutating func zoom(_ factor: Double, anchor: CGPoint, horizontal: Bool = true, vertical: Bool = true) {
        guard factor.isFinite && factor > 0 else { return }
        if horizontal { x = Self.zoomed(x, factor, anchor: anchor.x, minimum: 1 / 4096) }
        if vertical { y = Self.zoomed(y, factor, anchor: anchor.y, minimum: 1 / 128) }
    }

    mutating func pan(horizontal: Double, vertical: Double) {
        if horizontal.isFinite { x = Self.clamp(x.lowerBound + horizontal * (x.upperBound - x.lowerBound), span: x.upperBound - x.lowerBound) }
        if vertical.isFinite { y = Self.clamp(y.lowerBound + vertical * (y.upperBound - y.lowerBound), span: y.upperBound - y.lowerBound) }
    }

    private static func zoomed(_ range: ClosedRange<Double>, _ factor: Double, anchor: Double, minimum: Double) -> ClosedRange<Double> {
        let anchor = max(0, min(1, anchor.isFinite ? anchor : 0.5))
        let old = range.upperBound - range.lowerBound
        let span = max(minimum, min(1, old / factor))
        return clamp(range.lowerBound + old * anchor - span * anchor, span: span)
    }

    private static func clamp(_ start: Double, span: Double) -> ClosedRange<Double> {
        let lower = max(0, min(1 - span, start))
        return lower...(lower + span)
    }
}
