import AppKit
import SwiftUI

/// Small AppKit adapter for exact chart hit testing: either mouse button
/// activates the region under the pointer, tooltips and pointing-hand cursors
/// follow the same rectangles. Later regions win (drawn on top).
struct ChartHitArea: NSViewRepresentable {
    struct Region {
        let rect: CGRect
        let tooltip: String
        let clickable: Bool
        let action: () -> Void
    }

    let regions: [Region]

    func makeNSView(context: Context) -> HitView { HitView() }

    func updateNSView(_ view: HitView, context: Context) {
        view.regions = regions
    }

    final class HitView: NSView {
        var regions: [Region] = [] {
            didSet { refreshTracking() }
        }

        override var isFlipped: Bool { true }
        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

        private func refreshTracking() {
            removeAllToolTips()
            for (index, region) in regions.enumerated() where !region.tooltip.isEmpty {
                addToolTip(region.rect, owner: self, userData: UnsafeMutableRawPointer(bitPattern: index + 1))
            }
            window?.invalidateCursorRects(for: self)
        }

        @objc func view(_ view: NSView, stringForToolTip tag: NSView.ToolTipTag, point: NSPoint, userData data: UnsafeMutableRawPointer?) -> String {
            let index = Int(bitPattern: data) - 1
            return regions.indices.contains(index) ? regions[index].tooltip : ""
        }

        override func resetCursorRects() {
            for region in regions where region.clickable { addCursorRect(region.rect, cursor: .pointingHand) }
        }

        private func region(at event: NSEvent) -> Region? {
            let point = convert(event.locationInWindow, from: nil)
            return regions.last { $0.clickable && $0.rect.contains(point) }
        }

        override func hitTest(_ point: NSPoint) -> NSView? {
            let local = convert(point, from: superview)
            return regions.contains { $0.rect.contains(local) } ? self : nil
        }

        override func mouseDown(with event: NSEvent) { region(at: event)?.action() }
        override func rightMouseDown(with event: NSEvent) { region(at: event)?.action() }
    }
}
