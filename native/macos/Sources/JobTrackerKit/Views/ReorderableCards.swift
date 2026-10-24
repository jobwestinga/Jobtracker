import AppKit
import Combine
import SwiftUI

/// A drag is presentation state until mouse-up. Cancelling never writes an
/// intermediate order, and the original UID remains the editor/undo target.
struct CardPlacement: Equatable {
    var order: [String] = []
    var source: String?
    var location = CGPoint.zero
    var grabY: CGFloat = 0
    var height: CGFloat = 0

    mutating func begin(_ uid: String, order: [String], frame: CGRect, start: CGPoint) {
        self.order = order
        source = uid
        grabY = start.y - frame.minY
        height = frame.height
    }

    mutating func move(to location: CGPoint, frames: [String: CGRect], canMove: (String, String) -> Bool) {
        self.location = location
        guard let source, let from = order.firstIndex(of: source) else { return }
        let center = location.y - grabY + height / 2
        // Crossing a neighbour's centre opens the insertion gap. Retaining
        // offscreen identities is essential for long, lazy lists.
        var destination = from
        for (index, uid) in order.enumerated() where uid != source && canMove(source, uid) {
            guard let rect = frames[uid] else { continue }
            if index > from && center > rect.midY { destination = max(destination, index) }
            if index < from && center < rect.midY { destination = min(destination, index) }
        }
        if destination != from {
            order.remove(at: from)
            order.insert(source, at: destination)
        }
    }
}

private struct CardFrames: PreferenceKey {
    static var defaultValue: [String: CGRect] { [:] }
    static func reduce(value: inout [String: CGRect], nextValue: () -> [String: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { _, new in new })
    }
}

extension Notification.Name {
    static let cancelCardDrag = Notification.Name("JobTracker.cancelCardDrag")
}

/// Mouse-down and drag lifts the actual card over an animated insertion gap.
/// No system copy badge, stationary duplicate or save on every hover.
struct ReorderableCards<Item: Identifiable, Row: View>: View where Item.ID == String {
    let items: [Item]
    let reduceMotion: Bool
    var canMove: (String, String) -> Bool = { _, _ in true }
    let commit: ([String]) async -> Void
    @ViewBuilder let row: (Item, Int) -> Row
    @StateObject private var placement = ViewState(CardPlacement())
    @StateObject private var frames = ViewState<[String: CGRect]>([:])
    @StateObject private var saving = ViewState(false)
    @StateObject private var cancelled = ViewState(false)
    @StateObject private var coordinate = ViewState(UUID())
    private var space: UUID { coordinate.value }

    private var ids: [String] { items.map(\.id) }
    private var ordered: [Item] {
        let byID = Dictionary(uniqueKeysWithValues: items.map { ($0.id, $0) })
        return placement.value.order.isEmpty ? items : placement.value.order.compactMap { byID[$0] }
    }
    private var animation: Animation? { reduceMotion ? nil : .easeInOut(duration: 0.16) }
    private var dragTicks: AnyPublisher<Date, Never> {
        placement.value.source == nil || saving.value ? Empty().eraseToAnyPublisher()
            : Timer.publish(every: 0.1, on: .main, in: .common).autoconnect().eraseToAnyPublisher()
    }

    var body: some View {
        GeometryReader { viewport in
            ScrollViewReader { reader in
                ZStack(alignment: .topLeading) {
                    ScrollView {
                        LazyVStack(spacing: 8) {
                            ForEach(Array(ordered.enumerated()), id: \.element.id) { index, item in
                                row(item, index)
                                    .opacity(placement.value.source == item.id ? 0 : 1)
                                    .background(GeometryReader { proxy in
                                        Color.clear.preference(key: CardFrames.self, value: [item.id: proxy.frame(in: .named(space))])
                                    })
                                    .contentShape(Rectangle())
                                    // Once the threshold is crossed the drag
                                    // cancels child clicks (notably Complete).
                                    .highPriorityGesture(drag(item.id, bounds: viewport.size))
                                    .id(item.id)
                            }
                        }
                        .padding(.bottom, 8)
                    }
                    .allowsHitTesting(!saving.value)
                    if let source = placement.value.source,
                       let index = ordered.firstIndex(where: { $0.id == source }) {
                        row(ordered[index], index)
                            .frame(width: max(1, viewport.size.width - 8), height: placement.value.height)
                            .shadow(color: .black.opacity(0.4), radius: 12, y: 5)
                            .offset(x: 4, y: placement.value.location.y - placement.value.grabY)
                            .allowsHitTesting(false)
                            .accessibilityHidden(true)
                            .transition(.identity)
                    }
                }
                .coordinateSpace(name: space)
                .clipped()
                .onPreferenceChange(CardFrames.self) { frames.value = $0 }
                .onReceive(dragTicks) { _ in
                    guard placement.value.source != nil && !saving.value else { return }
                    let location = placement.value.location.y
                    let visible = ordered.enumerated().filter { _, item in
                        guard let rect = frames.value[item.id] else { return false }
                        return rect.maxY > 0 && rect.minY < viewport.size.height
                    }
                    var next: Int?
                    if location < 32, let first = visible.first { next = max(0, first.offset - 1) }
                    if location > viewport.size.height - 32, let last = visible.last { next = min(ordered.count - 1, last.offset + 1) }
                    if let next {
                        reader.scrollTo(ordered[next].id, anchor: location < 32 ? .top : .bottom)
                        withAnimation(animation) { placement.value.move(to: placement.value.location, frames: frames.value, canMove: canMove) }
                    }
                }
            }
        }
        .onChange(of: ids) { _ in
            if !saving.value {
                if placement.value.source != nil { cancelled.value = true }
                placement.value = CardPlacement()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .cancelCardDrag)) { _ in
            if placement.value.source != nil { cancelled.value = true; placement.value = CardPlacement() }
        }
        .onDisappear { placement.value = CardPlacement() }
    }

    private func drag(_ uid: String, bounds: CGSize) -> some Gesture {
        DragGesture(minimumDistance: 5, coordinateSpace: .named(space))
            .onChanged { value in
                guard !saving.value && !cancelled.value else { return }
                if placement.value.source == nil {
                    guard let frame = frames.value[uid] else { return }
                    placement.value.begin(uid, order: ids, frame: frame, start: value.startLocation)
                }
                var next = placement.value
                next.move(to: value.location, frames: frames.value, canMove: canMove)
                placement.value.location = value.location
                withAnimation(animation) { placement.value.order = next.order }
            }
            .onEnded { value in
                defer { cancelled.value = false }
                guard placement.value.source != nil else { return }
                guard CGRect(origin: .zero, size: bounds).contains(value.location) else {
                    placement.value = CardPlacement()
                    return
                }
                let order = placement.value.order
                let changed = order != ids
                saving.value = true
                // Keep the lifted copy alive while it travels to the gap.
                // Fading that copy out while fading the row in caused a blink
                // and a morph on release, even though the order was correct.
                let duration = reduceMotion ? 0.0 : 0.14
                if let source = placement.value.source, let slot = frames.value[source] {
                    withAnimation(reduceMotion ? nil : .easeOut(duration: duration)) {
                        placement.value.location.y = slot.minY + placement.value.grabY
                    }
                }
                Task { @MainActor in
                    let started = Date()
                    if changed { await commit(order) }
                    let remaining = max(0, duration - Date().timeIntervalSince(started))
                    if remaining > 0 { try? await Task.sleep(nanoseconds: UInt64(remaining * 1_000_000_000)) }
                    var transaction = Transaction(animation: nil)
                    transaction.disablesAnimations = true
                    withTransaction(transaction) {
                        placement.value = CardPlacement()
                        saving.value = false
                    }
                }
            }
    }
}
