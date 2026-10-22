import AppKit
import SwiftUI

/// Bold agenda layout. Blocks keep their exact interval and session identity.
///
/// Visual adjacency: two blocks in the same lane join into one outer shape when
/// the gap between them is at most `max(adjacency_seconds, one device pixel)`
/// at the current zoom; anything larger stays a visible gap. Joining is drawing
/// only: each block keeps its own rectangle, hit target and session UID, and a
/// gap is never counted as work. Overlapping sessions are not moved: they
/// share the column in side-by-side lanes at their true times.
public struct AgendaLayout {
    public struct Block: Identifiable {
        public let source: AgendaBlockDTO
        public let rect: CGRect
        public let lane: Int
        public let lanes: Int
        public var group: Int
        public var id: String { source.id }
    }

    public struct Column {
        public let day: String
        public let rect: CGRect
        public let label: String
        public let blocks: [Block]
        public let groups: [[Block]]
    }

    public let chart: CGRect
    public let columns: [Column]
    public let hourStart: Int
    public let hourEnd: Int
    public let nowY: CGFloat?
    public let toleranceHours: Double

    public static let leftMargin: CGFloat = 56

    public static func requiredWidth(days: Int, fitWidth: Bool) -> CGFloat {
        guard !fitWidth, days > 0 else { return 0 }
        let required = 56 + 14 + CGFloat(days) * 40 + CGFloat(days - 1) * 6
        return max(max(400, 60 + CGFloat(days) * 80), required)
    }

    public init(agenda: AgendaDTO, size: CGSize, displayScale: CGFloat = 2) {
        let startHour = max(0, min(26, agenda.hourStart))
        let endHour = max(startHour + 1, min(27, agenda.hourEnd))
        let chartRect = CGRect(x: Self.leftMargin, y: 30, width: max(80, size.width - Self.leftMargin - 14), height: max(120, size.height - 60))
        let hours = Double(endHour - startHour)
        let pixelsPerHour = Double(chartRect.height) / hours
        let tolerance = max(DesignTokens.shared.roles.agenda.adjacencySeconds / 3600, 1 / (pixelsPerHour * Double(displayScale)))
        let count = max(1, agenda.days.count)
        let gap: CGFloat = (agenda.fitWidth && count > 15) ? 2 : 6
        let raw = (chartRect.width - gap * CGFloat(count - 1)) / CGFloat(count)
        let width = agenda.fitWidth ? max(2, raw) : max(40, raw)
        func y(_ hour: Double) -> CGFloat { chartRect.minY + CGFloat((hour - Double(startHour)) / hours) * chartRect.height }
        hourStart = startHour
        hourEnd = endHour
        chart = chartRect
        toleranceHours = tolerance

        if agenda.days.contains(agenda.nowDay) && Double(startHour) <= agenda.nowHour && agenda.nowHour <= Double(endHour) {
            nowY = y(agenda.nowHour)
        } else {
            nowY = nil
        }

        let byDay = Dictionary(grouping: agenda.sessions, by: \.day)
        var built: [Column] = []
        for (index, day) in agenda.days.enumerated() {
            let x = chartRect.minX + CGFloat(index) * (width + gap)
            let rect = CGRect(x: x, y: chartRect.minY, width: width, height: chartRect.height)
            let visible = (byDay[day] ?? []).compactMap { block -> (AgendaBlockDTO, Double, Double)? in
                let start = max(Double(startHour), min(Double(endHour), block.startH))
                let end = max(Double(startHour), min(Double(endHour), block.endH))
                return end > start ? (block, start, end) : nil
            }.sorted { ($0.1, $0.2) < ($1.1, $1.2) }

            // Lanes: an overlapping session takes the next free lane.
            var laneEnds: [Double] = []
            var clusterEnd = -Double.infinity
            var cluster: [Int] = []
            var assigned: [(AgendaBlockDTO, Double, Double, Int)] = []
            var laneCounts: [Int] = []
            func closeCluster() {
                let lanes = max(1, cluster.map { assigned[$0].3 }.max().map { $0 + 1 } ?? 1)
                for i in cluster { laneCounts[i] = lanes }
                cluster = []
                laneEnds = []
            }
            for item in visible {
                if item.1 >= clusterEnd - 1e-9 { closeCluster() }
                var lane = laneEnds.firstIndex { $0 <= item.1 + 1e-9 } ?? laneEnds.count
                if lane == laneEnds.count { laneEnds.append(item.2) } else { laneEnds[lane] = item.2 }
                assigned.append((item.0, item.1, item.2, lane))
                laneCounts.append(1)
                cluster.append(assigned.count - 1)
                clusterEnd = max(clusterEnd, item.2)
            }
            closeCluster()

            var blocks: [Block] = []
            for (i, item) in assigned.enumerated() {
                let lanes = laneCounts[i]
                let inset = min(1, width / 10)
                let laneWidth = (width - 2 * inset) / CGFloat(lanes)
                let laneGap = lanes > 1 ? min(1, laneWidth / 5) : 0
                let top = y(item.1), bottom = y(item.2)
                let blockRect = CGRect(x: x + inset + CGFloat(item.3) * laneWidth, y: top, width: laneWidth - laneGap,
                                       height: max(2, bottom - top))
                blocks.append(Block(source: item.0, rect: blockRect, lane: item.3, lanes: lanes, group: -1))
            }
            // Adjacency groups per lane geometry.
            var groupId = 0
            var lastInLane: [String: Int] = [:]
            for i in blocks.indices {
                let key = "\(blocks[i].lane)/\(blocks[i].lanes)"
                if let previous = lastInLane[key] {
                    let prevEnd = assigned[previous].2
                    if abs(assigned[i].1 - prevEnd) <= tolerance { blocks[i].group = blocks[previous].group }
                }
                if blocks[i].group < 0 { blocks[i].group = groupId; groupId += 1 }
                lastInLane[key] = i
            }
            let groups = Dictionary(grouping: blocks, by: \.group).sorted { $0.key < $1.key }.map(\.value)
            built.append(Column(day: day, rect: rect, label: LocalTime.format(day, "EEE dd"), blocks: blocks, groups: groups))
        }
        columns = built
    }

    /// Bold labels that fit the block: the subject title when it fits (elided
    /// to at least three characters), otherwise the duration, plus a duration
    /// line when the block is tall enough. Nothing is drawn in tiny blocks.
    public static func labelLines(for block: Block) -> [(text: String, font: NSFont)] {
        let rect = block.rect
        guard rect.height > 13, rect.width > 20 else { return [] }
        let width = rect.width - 8
        let titleFont = NSFont.systemFont(ofSize: 11, weight: .bold)
        let durationFont = NSFont.systemFont(ofSize: 10, weight: .medium)
        let seconds = block.source.durationSeconds
        let duration = seconds >= 3600 ? String(format: "%.1fh", Double(seconds) / 3600) : "\(max(1, Int((Double(seconds) / 60).rounded())))m"
        let title = elided(block.source.subjectName, font: titleFont, width: width)
        // The duration falls back to smaller sizes rather than being clipped.
        let fittedDuration = [11.0, 10.0, 9.0].lazy
            .map { NSFont.systemFont(ofSize: $0, weight: .bold) }
            .first { ChartText.metrics(duration, font: $0).width <= width }
            .map { (duration, $0) }
        if rect.height >= 28 {
            guard let title else { return fittedDuration.map { [$0] } ?? [] }
            return ChartText.metrics(duration, font: durationFont).width <= width ? [(title, titleFont), (duration, durationFont)] : [(title, titleFont)]
        }
        let combined = "\(block.source.subjectName) \u{00B7} \(duration)"
        if ChartText.metrics(combined, font: titleFont).width <= width { return [(combined, titleFont)] }
        if let title { return [(title, titleFont)] }
        return fittedDuration.map { [$0] } ?? []
    }

    static func elided(_ text: String, font: NSFont, width: CGFloat) -> String? {
        if ChartText.metrics(text, font: font).width <= width { return text }
        var characters = Array(text)
        while characters.count > 3 {
            characters.removeLast()
            let candidate = String(characters).trimmingCharacters(in: .whitespaces) + "\u{2026}"
            if ChartText.metrics(candidate, font: font).width <= width { return candidate }
        }
        return nil
    }

    public static func hourLabel(_ hour: Int) -> String {
        hour >= 24 ? String(format: "%02d:00 (+1)", hour - 24) : String(format: "%02d:00", hour)
    }

    public static func timeLabel(_ hours: Double) -> String {
        let total = Int((hours * 60).rounded())
        let dayOffset = total / (24 * 60), minutes = total % (24 * 60)
        return String(format: "%02d:%02d%@", minutes / 60, minutes % 60, dayOffset > 0 ? " (+1)" : "")
    }

    /// Bold fill: a strong subject tint on the near-opaque panel, darkened
    /// only as far as needed for readable white titles.
    public static func boldFill(for color: String, theme: Theme) -> RGBA {
        let roles = DesignTokens.shared.roles.agenda
        let subject = RGBA(hex: color)
        var amount = roles.boldFillMix
        var fill = theme.bgPrimary.mixed(with: subject, amount)
        while fill.contrast(with: theme.textPrimary) < roles.minimumTextContrast && amount > 0.3 {
            amount -= 0.04
            fill = theme.bgPrimary.mixed(with: subject, amount)
        }
        return fill
    }
}

struct AgendaChartView: View {
    let data: AgendaDTO
    let onDay: (String) -> Void
    let onBlock: (AgendaBlockDTO) -> Void
    @Environment(\.theme) private var theme
    @Environment(\.displayScale) private var displayScale

    var body: some View {
        GeometryReader { proxy in
            let width = max(proxy.size.width, AgendaLayout.requiredWidth(days: data.days.count, fitWidth: data.fitWidth))
            let size = CGSize(width: width, height: max(360, proxy.size.height))
            let layout = AgendaLayout(agenda: data, size: size, displayScale: displayScale)
            ScrollView(.horizontal, showsIndicators: !data.fitWidth) {
                ZStack(alignment: .topLeading) {
                    canvas(layout)
                    ChartHitArea(regions: regions(layout))
                    accessibilityLayer(layout)
                }
                .frame(width: size.width, height: size.height)
            }
        }
        .frame(minHeight: 360)
    }

    private func regions(_ layout: AgendaLayout) -> [ChartHitArea.Region] {
        var regions: [ChartHitArea.Region] = layout.columns.map { column in
            .init(rect: column.rect, tooltip: "", clickable: true, action: { onDay(column.day) })
        }
        for column in layout.columns {
            for block in column.blocks {
                let source = block.source
                let tip = "\(source.subjectName)\n\(LocalTime.format(source.day, "EEEE, dd MMMM yyyy"))\n" +
                    "\(AgendaLayout.timeLabel(source.startH))\u{2013}\(AgendaLayout.timeLabel(source.endH)) \u{00B7} \(String(format: "%.1fh", Double(source.durationSeconds) / 3600))" +
                    (source.uid != nil ? "\nClick to edit \u{00B7} click empty space for the whole day" : "")
                // A very short block keeps a usable target without growing its drawing.
                let target = block.rect.height < 6 ? block.rect.insetBy(dx: 0, dy: -(6 - block.rect.height) / 2) : block.rect
                regions.append(.init(rect: target, tooltip: tip, clickable: true, action: { onBlock(source) }))
            }
        }
        return regions
    }

    private func canvas(_ layout: AgendaLayout) -> some View {
        Canvas { context, size in
            let outer = CGRect(origin: .zero, size: size).insetBy(dx: 0.5, dy: 0.5)
            context.fill(Path(roundedRect: outer, cornerRadius: Radius.chart), with: .color(theme.bgSecondary.alpha(238 / 255).color))
            context.stroke(Path(roundedRect: outer, cornerRadius: Radius.chart), with: .color(theme.border.alpha(160 / 255).color), lineWidth: 1)
            guard !data.days.isEmpty else {
                context.draw(Text("No tracked data yet").foregroundColor(theme.textDimmed.color), at: CGPoint(x: size.width / 2, y: size.height / 2))
                return
            }
            let chart = layout.chart
            let hours = CGFloat(layout.hourEnd - layout.hourStart)
            for hour in layout.hourStart...layout.hourEnd {
                let y = (chart.minY + CGFloat(hour - layout.hourStart) / hours * chart.height).rounded() + 0.25
                var line = Path()
                line.move(to: CGPoint(x: chart.minX, y: y))
                line.addLine(to: CGPoint(x: chart.maxX, y: y))
                context.stroke(line, with: .color(theme.border.alpha(70 / 255).color), lineWidth: 0.5)
                context.draw(Text(AgendaLayout.hourLabel(hour)).font(.system(size: 9)).foregroundColor(theme.textDimmed.color),
                             at: CGPoint(x: AgendaLayout.leftMargin - 6, y: y), anchor: .trailing)
            }
            if let nowY = layout.nowY {
                var line = Path()
                line.move(to: CGPoint(x: chart.minX, y: nowY))
                line.addLine(to: CGPoint(x: chart.maxX, y: nowY))
                context.stroke(line, with: .color(theme.textDimmed.alpha(115 / 255).color), lineWidth: 1.5)
            }
            let edge = CGFloat(DesignTokens.shared.roles.agenda.edgeWidth)
            let radius = CGFloat(DesignTokens.lookup(DesignTokens.shared.roles.radius, "agendaGroup") ?? 6)
            var lastDayRight = -CGFloat.infinity
            for column in layout.columns {
                // Every column, today included, is styled identically.
                context.fill(Path(roundedRect: column.rect, cornerRadius: 5), with: .color(theme.bgSecondary.alpha(60 / 255).color))
                context.stroke(Path(roundedRect: column.rect, cornerRadius: 5), with: .color(theme.border.alpha(60 / 255).color), lineWidth: 0.5)
                let labelWidth = ChartText.metrics(column.label, font: .systemFont(ofSize: 9)).width
                if column.rect.midX - labelWidth / 2 >= lastDayRight + 6 {
                    context.draw(Text(column.label).font(.system(size: 9)).foregroundColor(theme.textDimmed.color),
                                 at: CGPoint(x: column.rect.midX, y: chart.maxY + 12))
                    lastDayRight = column.rect.midX + labelWidth / 2
                }
                for group in column.groups {
                    let union = group.dropFirst().reduce(group[0].rect) { $0.union($1.rect) }
                    let shape = Path(roundedRect: union, cornerRadius: min(radius, union.width / 3, union.height / 2), style: .continuous)
                    context.drawLayer { layer in
                        layer.clip(to: shape)
                        for block in group {
                            // Half-pixel overlap: no seam or black divider between joined blocks.
                            let rect = block.rect.insetBy(dx: 0, dy: -0.5)
                            layer.fill(Path(rect), with: .color(AgendaLayout.boldFill(for: block.source.color, theme: theme).color))
                            layer.fill(Path(CGRect(x: rect.minX, y: rect.minY, width: min(edge, rect.width / 3), height: rect.height)),
                                       with: .color(RGBA(hex: block.source.color).color))
                        }
                    }
                    for block in group { drawLabel(block, in: &context) }
                }
            }
        }
    }

    private func drawLabel(_ block: AgendaLayout.Block, in context: inout GraphicsContext) {
        let lines = AgendaLayout.labelLines(for: block)
        guard !lines.isEmpty else { return }
        let rect = block.rect
        let textRect = CGRect(x: rect.minX + 6, y: rect.minY + 2, width: max(0, rect.width - 8), height: max(0, rect.height - 4))
        let primary = theme.textPrimary.cgColor
        let secondary = theme.textPrimary.alpha(0.82).cgColor
        context.withCGContext { cg in
            cg.saveGState()
            cg.clip(to: textRect)
            var y = textRect.minY
            for (index, line) in lines.enumerated() {
                ChartText.draw(line.text, font: line.font, color: index == 0 ? primary : secondary, at: CGPoint(x: textRect.minX, y: y), in: cg)
                y += 14
            }
            cg.restoreGState()
        }
    }

    private func accessibilityLayer(_ layout: AgendaLayout) -> some View {
        ZStack(alignment: .topLeading) {
            ForEach(layout.columns, id: \.day) { column in
                Color.clear
                    .frame(width: column.rect.width, height: column.rect.height)
                    .offset(x: column.rect.minX, y: column.rect.minY)
                    .accessibilityElement()
                    .accessibilityLabel("\(LocalTime.format(column.day, "EEEE d MMMM")), \(column.blocks.count) sessions")
                    .accessibilityAddTraits(.isButton)
                    .accessibilityAction { onDay(column.day) }
                    .allowsHitTesting(false)
                ForEach(column.blocks) { block in
                    Color.clear
                        .frame(width: block.rect.width, height: block.rect.height)
                        .offset(x: block.rect.minX, y: block.rect.minY)
                        .accessibilityElement()
                        .accessibilityLabel("\(block.source.subjectName), \(AgendaLayout.timeLabel(block.source.startH)) to \(AgendaLayout.timeLabel(block.source.endH))")
                        .accessibilityAddTraits(.isButton)
                        .accessibilityAction { onBlock(block.source) }
                        .allowsHitTesting(false)
                }
            }
        }
    }
}
