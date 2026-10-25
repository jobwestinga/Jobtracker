import Foundation
@testable import JobTrackerKit

struct HourStyleTests: CheckSuite {
    func tiersAndScaleMatchTheRestoredHierarchy() {
        func style(_ hours: Double) -> HourStyle { HourStyle.forIntensity(seconds: hours * 3600) }
        expect(style(1.0).color == RGBA(hex: "#94A3B8") && !style(1.0).strong)
        expect(style(1.01).color == RGBA(hex: "#86EFAC"))
        expect(style(2.5).color == RGBA(hex: "#86EFAC"))
        expect(style(4.5).color == RGBA(hex: "#60A5FA") && !style(4.5).strong)
        expect(style(5.0).color == RGBA(hex: "#A855F7") && style(5.0).strong)
        expect(style(7.9).color == RGBA(hex: "#FB923C") && style(7.9).strong && !style(7.9).peak)
        expect(style(10.0).color == RGBA(hex: "#FB923C") && !style(10.0).peak)
        expect(style(10.5).color == RGBA(hex: "#EF4444") && style(10.5).peak)
        expect(abs(style(5).scale - (0.85 + 5.0 / 15)) < 1e-9)
        expect(abs(style(20).scale - (0.85 + 14.0 / 15)) < 1e-9)  // capped at 14 h
        expect(style(7.9).scale < style(10.5).scale)
    }

    func bloomMasksAreCachedByTextFontColourAndScale() {
        let cache = BloomCache()
        let key = BloomCache.Key(text: "11.0h", fontSize: 16, strong: true, color: RGBA(hex: "#EF4444"), radius: 5.5, scale: 2)
        let first = cache.mask(for: key)
        let second = cache.mask(for: key)
        expect(first != nil && cache.renders == 1)
        expect(first?.image === second?.image)
        _ = cache.mask(for: BloomCache.Key(text: "11.0h", fontSize: 16, strong: true, color: RGBA(hex: "#EF4444"), radius: 5.5, scale: 1))
        expect(cache.renders == 2)
        expect((first?.size.width ?? 0) > 30)
    }
}

struct BarLayoutTests: CheckSuite {
    func narrowChartsKeepEveryTotalWithoutCollisions() {
        for count in [7, 14, 31, 120] {
            for equal in [false, true] {
                let buckets = (0..<count).map { day in
                    bucket(String(format: "2026-10-%02d", day % 28 + 1), [("a", "#3B82F6", equal ? 39600 : 900 + (day % 15) * 2700)])
                }
                let width = max(410, BarChartLayout.requiredWidth(buckets: buckets, fitWidth: true))
                let layout = BarChartLayout(buckets: buckets, grouping: "daily", size: CGSize(width: width, height: 470), fitWidth: true)
                expect(layout.labels.count == count, "all \(count) totals survive at \(width)pt, equal=\(equal): got \(layout.labels.count)")
                for (index, label) in layout.labels.enumerated() {
                    let bar = layout.bars[index]
                    expect(abs(label.origin.y + label.height + 9 - bar.rect.minY) < 0.001, "a total stays just above its own bar")
                    expect(label.font.pointSize <= 11 * label.style.scale + 0.001, "quieter size retains relative magnitude")
                    let rect = CGRect(origin: label.origin, size: CGSize(width: label.width, height: label.height))
                    expect(rect.minX >= 0 && rect.minY >= 0 && rect.maxX <= width)
                    for other in layout.labels.dropFirst(index + 1) {
                        expect(!rect.intersects(CGRect(origin: other.origin, size: CGSize(width: other.width, height: other.height))))
                    }
                }
            }
        }
    }
    func bucket(_ date: String, _ segments: [(String, String, Int)], intensity: Double? = nil) -> BucketDTO {
        let total = segments.reduce(0) { $0 + $1.2 }
        return BucketDTO(date: date, totalSeconds: total, intensitySeconds: intensity ?? Double(total), periodDays: 1, thresholdDays: 1,
                         thresholdFactor: 1, segments: segments.map { SegmentDTO(subjectUid: $0.0, subjectName: $0.0, color: $0.1, seconds: $0.2, startTime: nil, endTime: nil) })
    }

    func heightsAreProportionalAndSegmentsContiguous() {
        let buckets = [bucket("2026-10-20", [("a", "#3B82F6", 3600), ("a", "#3B82F6", 1800), ("b", "#22C55E", 5400)]),
                       bucket("2026-10-21", [("b", "#22C55E", 2 * 3600)])]
        let layout = BarChartLayout(buckets: buckets, grouping: "daily", size: CGSize(width: 600, height: 400), fitWidth: true)
        expect(layout.maxTotal == 10800)
        let first = layout.bars[0], second = layout.bars[1]
        expect(abs(first.rect.height - layout.chart.height) < 0.001)
        expect(abs(second.rect.height - layout.chart.height * 2 / 3) < 0.001)
        // Adjacent same-subject segments merge; stacks stay contiguous.
        expect(first.segments.count == 2)
        expect(abs(first.segments[0].rect.minY - 0.5 - (first.segments[1].rect.maxY - 1)) < 0.6)
        expect(layout.labels.count == 2)
        expect(layout.labels.first { $0.text == "3.0h" }!.style.strong == false)
    }

    func weeklyAndMonthlyLabelsUseTheirGrouping() {
        expect(BarChartLayout.bucketLabel("2026-10-19", grouping: "weekly") == "W43")
        expect(BarChartLayout.bucketLabel("2026-10-01", grouping: "monthly") == "Oct 26")
        expect(BarChartLayout.bucketLabel("2026-10-21", grouping: "daily") == "Wed 21")
    }

    func denseHistoryKeepsLabelsFromOverlapping() {
        let buckets = (1...60).map { day in bucket(String(format: "2026-08-%02d", (day - 1) % 28 + 1), [("a", "#3B82F6", 3600 * (day % 11 + 1))]) }
        let width = max(2400, BarChartLayout.requiredWidth(buckets: buckets, fitWidth: true))
        let layout = BarChartLayout(buckets: buckets, grouping: "daily", size: CGSize(width: width, height: 400), fitWidth: true)
        for (i, a) in layout.labels.enumerated() {
            for b in layout.labels[(i + 1)...] {
                let ra = CGRect(x: a.origin.x, y: a.origin.y, width: a.width, height: a.height)
                let rb = CGRect(x: b.origin.x, y: b.origin.y, width: b.width, height: b.height)
                expect(!ra.intersects(rb))
            }
        }
    }
}

struct AgendaLayoutTests: CheckSuite {
    func customLogicalDaysFollowSharedHourWindows() throws {
        expect(AgendaLayout.hourLabel(48) == "00:00 (+2)")
        expect(AgendaLayout.timeLabel(48) == "00:00 (+2)")
        struct Boundary: Decodable {
            let id: String, boundary: String, day: String
            let startH: Double, endH: Double
            let hourStart: Int, hourEnd: Int
        }
        struct Fixture: Decodable { let agendaWindows: [Boundary] }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let fixture = try decoder.decode(Fixture.self, from: Data(contentsOf: repositoryRoot.appendingPathComponent("tests/contracts/time_boundaries.json")))
        for entry in fixture.agendaWindows {
            let clock = entry.boundary.split(separator: ":").compactMap { Double($0) }
            let source = block(entry.id, entry.startH, entry.endH, day: entry.day)
            let data = AgendaDTO(days: [entry.day], sessions: [source], hourStart: entry.hourStart,
                                 hourEnd: entry.hourEnd, nowDay: entry.day, nowHour: entry.startH,
                                 dayStartHour: clock[0] + clock[1] / 60, rangeLabel: "Custom", fitWidth: true)
            let layout = AgendaLayout(agenda: data, size: CGSize(width: 470, height: 700))
            expect(layout.hourStart == entry.hourStart && layout.hourEnd == entry.hourEnd, entry.id)
            expect(layout.columns.flatMap(\.blocks).count == 1, entry.id)
            expect(layout.columns.first?.blocks.first?.source.uid == entry.id, entry.id)
            expect(layout.nowY != nil, entry.id)
        }
    }

    func tallBlocksKeepCompactDurationsInNarrowFortnightColumns() {
        let days = (1...14).map { String(format: "2026-10-%02d", $0) }
        let sessions = days.enumerated().map { i, day in block("s-\(i)", 9, i.isMultiple(of: 2) ? 11 : 10.5, day: day) }
        let data = AgendaDTO(days: days, sessions: sessions, hourStart: 6, hourEnd: 23, nowDay: "2026-10-23", nowHour: 12,
                             dayStartHour: 3, rangeLabel: "Weeks", fitWidth: true)
        let layout = AgendaLayout(agenda: data, size: CGSize(width: 470, height: 700))
        for block in layout.columns.flatMap(\.blocks) {
            let lines = AgendaLayout.labelLines(for: block)
            expect(!lines.isEmpty, "tall fortnight blocks retain duration even when a subject title cannot fit")
            expect(lines.allSatisfy { ChartText.metrics($0.text, font: $0.font).width <= block.rect.width - 4 })
        }
    }
    func tallNarrowArrangedBlocksAlwaysKeepTheirDuration() {
        let layout = AgendaLayout(agenda: agenda([block("a", 9, 10), block("b", 9, 10)]), size: CGSize(width: 132, height: 760))
        let shifted = layout.columns[0].blocks[1]
        let lines = AgendaLayout.labelLines(for: shifted)
        expect(lines.count == 2 && lines.last?.text.contains("1.0h") == true,
               "recorded start time yields to duration in a narrow block with room for two lines")
        expect(lines.allSatisfy { ChartText.metrics($0.text, font: $0.font).width <= shifted.rect.width - 8 })
    }
    func denseOverlapsKeepEveryTargetDistinct() {
        let sessions = (0..<12).map { block("session-\($0)", 9, 10) }
        for width: CGFloat in [400, 84] {
            let layout = AgendaLayout(agenda: agenda(sessions), size: CGSize(width: width, height: 700))
            let blocks = layout.columns[0].blocks
            expect(Set(blocks.compactMap(\.source.uid)).count == 12)
            expect(blocks.allSatisfy { $0.lanes == 1 && $0.rect.width > 0 && $0.source.startH == 9 && $0.source.endH == 10 })
            for pair in zip(blocks, blocks.dropFirst()) { expect(pair.0.rect.maxY <= pair.1.rect.minY + 0.001) }
        }
    }

    func block(_ uid: String?, _ start: Double, _ end: Double, day: String = "2026-10-20", color: String = "#3B82F6") -> AgendaBlockDTO {
        AgendaBlockDTO(uid: uid, day: day, subjectUid: "s", subjectName: "Physics", color: color, startH: start, endH: end, durationSeconds: Int((end - start) * 3600))
    }

    func agenda(_ blocks: [AgendaBlockDTO]) -> AgendaDTO {
        AgendaDTO(days: ["2026-10-20"], sessions: blocks, hourStart: 6, hourEnd: 23, nowDay: "2026-10-21", nowHour: 12,
                  dayStartHour: 3, rangeLabel: "Weeks", fitWidth: true)
    }

    func adjacencyJoinsWithinToleranceOnly() {
        let at = block("a", 9, 10), within = block("b", 10 + 20.0 / 3600, 11), beyond = block("c", 11 + 31.0 / 60, 12)
        let layout = AgendaLayout(agenda: agenda([at, within, beyond]), size: CGSize(width: 400, height: 700))
        let column = layout.columns[0]
        expect(column.groups.count == 2)
        expect(column.groups[0].map(\.source.uid) == ["a", "b"])
        // Joined blocks keep their own rectangles and identities.
        expect(column.blocks.count == 3)
        expect(column.blocks[1].rect.minY > column.blocks[0].rect.minY)
    }

    func overlapsArrangeVerticallyWithoutChangingRecordedTimes() {
        let layout = AgendaLayout(agenda: agenda([block("a", 9, 12), block("b", 10, 11)]), size: CGSize(width: 400, height: 700))
        let blocks = layout.columns[0].blocks
        expect(blocks.map(\.lanes) == [1, 1])
        let hourHeight = layout.chart.height / 17
        expect(abs(blocks[1].rect.minY - (layout.chart.minY + 6 * hourHeight)) < 0.001)
        expect(blocks[0].rect.maxY <= blocks[1].rect.minY + 0.001)
        expect(blocks[1].source.startH == 10 && blocks[1].source.endH == 11 && blocks[1].arranged)
        expect(AgendaLayout.labelLines(for: blocks[1]).contains { $0.text.contains("10:00") })
    }

    func arrangedOverflowStaysInsideTheWindowWithEveryExactTarget() {
        for count in [2, 50] {
            let sessions = (0..<count).map { block("s-\($0)", 22, 23) }
            let layout = AgendaLayout(agenda: agenda(sessions), size: CGSize(width: 400, height: 700))
            let blocks = layout.columns[0].blocks
            expect(blocks.count == count && Set(blocks.compactMap(\.source.uid)).count == count)
            expect(blocks.first!.rect.minY >= layout.chart.minY - 0.001 && blocks.last!.rect.maxY <= layout.chart.maxY + 0.001)
            expect(blocks.allSatisfy { $0.rect.height > 0 && $0.source.startH == 22 && $0.source.endH == 23 })
            for pair in zip(blocks, blocks.dropFirst()) { expect(pair.0.rect.maxY <= pair.1.rect.minY + 0.001) }
        }
    }

    func liveBlocksAreKeptWithoutIdentity() {
        let layout = AgendaLayout(agenda: agenda([block(nil, 15, 16)]), size: CGSize(width: 400, height: 700))
        expect(layout.columns[0].blocks.first?.source.uid == nil)
    }

    func boldFillKeepsTitlesReadable() {
        for theme in DesignTokens.shared.themes.map({ Theme(fx: $0.fx, palette: $0.palette) }) {
            for hex in DesignTokens.shared.roles.subjectColors {
                let fill = AgendaLayout.boldFill(for: hex, theme: theme)
                expect(fill.contrast(with: theme.textPrimary) >= 3.0, "\(hex) on \(theme.paletteName)")
            }
        }
    }

    func labelsFitOrFallBackToDuration() {
        let narrow = AgendaLayout(agenda: agenda([block("a", 9, 12)]), size: CGSize(width: 110, height: 700)).columns[0].blocks[0]
        let lines = AgendaLayout.labelLines(for: narrow)
        expect(!lines.isEmpty)
        expect(lines.allSatisfy { ChartText.metrics($0.text, font: $0.font).width <= narrow.rect.width - 10 })
        expect(AgendaLayout.hourLabel(25) == "01:00 (+1)")
        expect(AgendaLayout.timeLabel(24.5) == "00:30 (+1)")
    }
}

struct SessionDraftTests: CheckSuite {
    func unchangedEndpointsKeepTheirExactStoredStrings() {
        let row = SessionDTO(uid: "u", subjectUid: "s", subjectName: "Physics", color: "#fff", startTime: "2026-03-01T09:00:00.123456",
                             endTime: "2026-03-01T10:00:00.654321", durationSeconds: 3600, open: false, owned: true)
        var draft = SessionPanelModel.Draft(session: row)
        expect(!draft.isDirty)
        expect(draft.wireValue(field: draft.start, base: draft.baseStart) == "2026-03-01T09:00:00.123456")
        draft.end = "2026-03-01 11:00:00"
        expect(draft.isDirty)
        expect(draft.wireValue(field: draft.end, base: draft.baseEnd) == "2026-03-01T11:00:00")
        expect(draft.wireValue(field: "not a time", base: draft.baseEnd) == nil)
    }
}
