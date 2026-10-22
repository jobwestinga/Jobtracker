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
        let layout = BarChartLayout(buckets: buckets, grouping: "daily", size: CGSize(width: 2400, height: 400), fitWidth: true)
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

    func overlapsStayAtTheirTrueTimesInSeparateLanes() {
        let layout = AgendaLayout(agenda: agenda([block("a", 9, 12), block("b", 10, 11)]), size: CGSize(width: 400, height: 700))
        let blocks = layout.columns[0].blocks
        expect(blocks.map(\.lanes) == [2, 2])
        expect(blocks[0].lane == 0 && blocks[1].lane == 1)
        let hourHeight = layout.chart.height / 17
        expect(abs(blocks[1].rect.minY - (layout.chart.minY + 4 * hourHeight)) < 0.001)
        expect(blocks[0].rect.maxX <= blocks[1].rect.minX + 0.001)
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
