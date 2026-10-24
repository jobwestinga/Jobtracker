import AppKit
@testable import JobTrackerKit

struct WeightViewportTests: CheckSuite {
    func weightZoomAnchorsBoundsAxesAndReset() {
        var viewport = WeightViewport()
        viewport.zoom(2, anchor: CGPoint(x: 0.25, y: 0.75))
        expect(viewport.x == 0.125...0.625 && viewport.y == 0.375...0.875, "pinch retains the data under the pointer")
        let y = viewport.y
        viewport.zoom(2, anchor: CGPoint(x: 0.5, y: 0.5), vertical: false)
        expect(viewport.y == y && viewport.x.upperBound - viewport.x.lowerBound == 0.25)
        let x = viewport.x
        viewport.zoom(2, anchor: CGPoint(x: 0.5, y: 0.5), horizontal: false)
        expect(viewport.x == x && viewport.y.upperBound - viewport.y.lowerBound == 0.25)
        viewport.pan(horizontal: 1000, vertical: -1000)
        expect(viewport.x.upperBound == 1 && viewport.y.lowerBound == 0)
        viewport.zoom(.infinity, anchor: .zero)
        expect(viewport.x.upperBound == 1, "invalid input cannot corrupt the viewport")
        viewport.zoom(1e10, anchor: .zero)
        expect(viewport.x.upperBound > viewport.x.lowerBound && viewport.y.upperBound > viewport.y.lowerBound)
        viewport = WeightViewport()
        expect(!viewport.isZoomed && viewport.x == 0...1 && viewport.y == 0...1)
    }
}
