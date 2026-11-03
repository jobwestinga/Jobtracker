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
        var drag: Drag? = nil
    }

    struct Drag {
        let changed: (CGPoint, CGPoint) -> Void
        let ended: (CGPoint) -> Void
        let cancelled: () -> Void
    }

    let regions: [Region]

    func makeNSView(context: Context) -> HitView { HitView() }

    func updateNSView(_ view: HitView, context: Context) {
        view.regions = regions
    }

    final class HitView: NSView {
        private var pressed: Region?
        private var start = CGPoint.zero
        private var dragging = false
        private var cancelled = false
        private var cancelObserver: NSObjectProtocol?

        override init(frame: NSRect) {
            super.init(frame: frame)
            cancelObserver = NotificationCenter.default.addObserver(forName: .cancelCardDrag, object: nil, queue: .main) { [weak self] _ in
                guard let self, self.pressed != nil else { return }
                self.pressed?.drag?.cancelled()
                self.cancelled = true
            }
        }
        required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
        deinit { if let cancelObserver { NotificationCenter.default.removeObserver(cancelObserver) } }
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

        override func mouseDown(with event: NSEvent) {
            pressed = region(at: event)
            start = convert(event.locationInWindow, from: nil)
            dragging = false; cancelled = false
        }
        override func mouseDragged(with event: NSEvent) {
            guard !cancelled, let drag = pressed?.drag else { return }
            let point = convert(event.locationInWindow, from: nil)
            guard dragging || hypot(point.x - start.x, point.y - start.y) >= LiftPlacement.threshold else { return }
            dragging = true
            drag.changed(start, point)
            autoscroll(with: event)
        }
        override func mouseUp(with event: NSEvent) {
            defer { pressed = nil; dragging = false; cancelled = false }
            guard !cancelled, let pressed else { return }
            let point = convert(event.locationInWindow, from: nil)
            if dragging { pressed.drag?.ended(point) }
            else if pressed.rect.contains(point) { pressed.action() }
        }
        override func rightMouseDown(with event: NSEvent) { region(at: event)?.action() }
    }
}
