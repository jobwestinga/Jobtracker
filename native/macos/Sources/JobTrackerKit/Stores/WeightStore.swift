import Foundation
import SwiftUI

public struct WeightRowDTO: Decodable, Equatable, Identifiable, Hashable {
    public let uid: String
    public let measuredAt: String
    public let weightKg: Double
    public let source: String
    public let change: Double?
    public var id: String { uid }
    public var day: String { String(measuredAt.prefix(10)) }
}

public struct WeightPointDTO: Decodable, Equatable, Hashable {
    public let x: Double
    public let y: Double
    public let uid: String?
    public let measuredAt: String
    public let source: String?
}

public struct WeightAxisDTO: Decodable, Equatable {
    public let first: Double
    public let last: Double
    public let startLabel: String
    public let endLabel: String
    public let years: [[Double]]
    public let fallbackYear: String
}

public struct WeightBadgeDTO: Decodable, Equatable { public let delta: Double; public let exact: Double; public let from: String; public let to: String }
public struct WeightSummaryDTO: Decodable, Equatable { public let count: Int; public let latest: Double; public let change: Double }

public struct WeightViewDTO: Decodable, Equatable {
    public let rows: [WeightRowDTO]
    public let points: [WeightPointDTO]
    public let trend: [[Double]]
    public let range: String
    public let smoothing: String
    public let span: Double
    public let historic: Bool
    public let summary: WeightSummaryDTO?
    public let badge: WeightBadgeDTO?
    public let axis: WeightAxisDTO?
}

/// Weight entry and chart state; mirrors the Qt view's tile/draft rules.
/// Calendar dates only; tracking's logical day does not apply here.
public final class WeightStore: ObservableObject {
    public static let ranges = [("30", "30 days"), ("90", "90 days"), ("365", "1 year"), ("all", "All history")]
    public static let smoothings = [("loess", "Original curve"), ("mean7", "7-day average"), ("none", "Measurements")]

    @Published public private(set) var view: WeightViewDTO?
    @Published public var day: String = LocalTime.dayString(Date())
    @Published public private(set) var center = 80
    @Published public private(set) var whole: Int?
    @Published public private(set) var decimal: Int?
    @Published public private(set) var exactValue: Double?
    @Published public private(set) var editingUid: String?
    @Published public private(set) var editingTimestamp: String?
    @Published public private(set) var editingSource: String?
    @Published public var status = "Select both rows, then save. Choosing a date opens that day's entry."
    @Published public private(set) var saving = false
    @Published public var selectedHistoryUid: String?
    @Published public var search = ""
    @Published public var showDots = true
    @Published public var density = "comfortable"
    @Published public var highContrast = false
    @Published public var range = "90"
    @Published public var smoothing = "loess"
    @Published public var historic = false
    @Published public var span = 0.04

    private unowned let app: AppModel
    private var followToday = true
    private var draftDirty = false
    private var draftRevision = 0
    private var loadedOnce = false
    private var calendarDay = LocalTime.dayString(Date())
    private var epoch = 0
    private var requestId: String?
    private var pendingSaves: [String: Int] = [:]

    init(app: AppModel) { self.app = app }

    func adopt(settings: SettingsDTO) {
        func int(_ key: String, _ low: Int, _ high: Int) -> Int? {
            guard let value = Int(settings.value(key)), (low...high).contains(value) else { return nil }
            return value
        }
        if !loadedOnce {
            whole = int("weight_picker_whole", 0, 1000)
            decimal = int("weight_picker_decimal", 0, 9)
            if let whole { center = whole }
        }
        showDots = settings.value("weight_chart_points", "1") != "0"
        density = settings.value("weight_ui_density", "comfortable")
        highContrast = settings.value("weight_ui_contrast", "0") == "1"
        range = settings.value("weight_chart_range", "90")
        smoothing = settings.value("weight_chart_smoothing", "loess")
        historic = settings.value("weight_chart_historic", "0") == "1"
        span = Double(settings.value("weight_chart_span", "0.04")).flatMap { $0.isFinite ? $0 : nil } ?? 0.04
    }

    public var value: Double? {
        if let exactValue { return exactValue }
        guard let whole, let decimal else { return nil }
        return Double(whole) + Double(decimal) / 10
    }

    public var firstWhole: Int { min(994, max(0, center - 3)) }
    public var canSave: Bool { !saving && (value.map { $0 > 0 && $0 <= 1000 } ?? false) }
    public var history: [WeightRowDTO] {
        let rows = view?.rows ?? []
        let query = search.trimmingCharacters(in: .whitespaces).lowercased()
        guard !query.isEmpty else { return rows }
        return rows.filter { "\($0.day) \(Self.text($0.weightKg)) \($0.source)".lowercased().contains(query) }
    }

    public static func text(_ value: Double) -> String {
        var text = String(value)
        if text.hasSuffix(".0") { text.removeLast(2) }
        return text
    }

    // MARK: loading

    @MainActor
    public func reload() async {
        epoch += 1
        let mine = epoch
        let response = await app.call("weight.view", [
            "range": .string(range), "smoothing": .string(smoothing), "historic": .bool(historic), "span": .double(span),
        ], kind: .read)
        guard mine == epoch, let result = try? response.result(WeightViewDTO.self) else { return }
        let today = LocalTime.dayString(Date())
        let rowsChanged = result.rows != view?.rows
        view = result
        if followToday && !draftDirty && day != today { day = today; centerLatest() }
        calendarDay = today
        if let uid = editingUid, editingSource == "manual", !result.rows.contains(where: { $0.uid == uid }) {
            // Two devices' same-day saves can merge into one canonical row.
            if let canonical = manualRow(on: day) { editingUid = canonical.uid; editingTimestamp = canonical.measuredAt }
        }
        if !loadedOnce {
            loadedOnce = true
            loadDailyEntry()
            centerLatest()
        } else if rowsChanged, let selected = selectedHistoryUid, !result.rows.contains(where: { $0.uid == selected }) {
            selectedHistoryUid = nil
        }
    }

    private func manualRow(on day: String) -> WeightRowDTO? {
        view?.rows.first { $0.source == "manual" && $0.day == day }
    }

    // MARK: tiles and dates

    private func centerLatest() {
        guard let latest = view?.rows.first else { return }
        center = Int((latest.weightKg + 0.5).rounded(.down))
        clearOutsideSelection()
    }

    private func clearOutsideSelection() {
        let first = firstWhole
        if let whole, !(first...(first + 6)).contains(whole) {
            self.whole = nil
            exactValue = nil
            persist("weight_picker_whole", "")
        }
    }

    public func shiftRange(_ delta: Int) {
        center = min(997, max(3, center + delta))
        clearOutsideSelection()
    }

    public func selectWhole(_ value: Int) {
        whole = value
        exactValue = nil
        draftChanged()
        persist("weight_picker_whole", String(value))
    }

    public func selectDecimal(_ value: Int) {
        decimal = value
        exactValue = nil
        draftChanged()
        persist("weight_picker_decimal", String(value))
    }

    public func setPrecise(_ value: Double) {
        guard value.isFinite, value >= 0, value <= 1000 else { return }
        exactValue = value
        let tenths = Int((value * 10 + 0.5).rounded(.down))
        whole = tenths / 10
        decimal = tenths % 10
        center = Int((value + 0.5).rounded(.down))
        draftChanged()
        persist("weight_picker_whole", String(tenths / 10))
        persist("weight_picker_decimal", String(tenths % 10))
    }

    /// Clears both tile selections; never deletes a saved reading.
    public func resetDraft() {
        whole = nil
        decimal = nil
        exactValue = nil
        draftChanged()
        persist("weight_picker_whole", "")
        persist("weight_picker_decimal", "")
    }

    private func draftChanged() {
        draftDirty = true
        draftRevision += 1
        requestId = nil
    }

    public func chooseDay(_ newDay: String) {
        guard LocalTime.parse(newDay) != nil else { return }
        draftRevision += 1
        requestId = nil
        day = String(newDay.prefix(10))
        followToday = day == LocalTime.dayString(Date())
        draftDirty = false
        loadDailyEntry()
        status = "Choosing a date opens that day's entry. Select both rows, then save."
    }

    public func today() {
        chooseDay(LocalTime.dayString(Date()))
        centerLatest()
    }

    private func loadDailyEntry() {
        editingUid = nil
        editingTimestamp = nil
        editingSource = nil
        if let row = manualRow(on: day) { load(entry: row) }
    }

    private func load(entry row: WeightRowDTO) {
        editingUid = row.uid
        editingTimestamp = row.measuredAt
        editingSource = row.source
        exactValue = row.weightKg
        let tenths = Int((row.weightKg * 10 + 0.5).rounded(.down))
        whole = tenths / 10
        decimal = tenths % 10
        center = Int((row.weightKg + 0.5).rounded(.down))
    }

    /// Editing keeps imported precision until a tile changes.
    public func edit(_ uid: String) {
        guard let row = view?.rows.first(where: { $0.uid == uid }) else { return }
        draftRevision += 1
        requestId = nil
        app.weightFullscreen = false
        followToday = false
        draftDirty = false
        day = row.day
        load(entry: row)
        status = "Editing this reading. Imported precision is kept until you change a tile."
    }

    // MARK: persistence

    @MainActor
    public func save() async {
        if LocalTime.dayString(Date()) != calendarDay { await reload() }
        guard canSave, let value else { return }
        let revision = draftRevision
        saving = true
        defer { saving = false }
        let timestamp = (editingUid != nil && editingTimestamp?.prefix(10) == Substring(day)) ? editingTimestamp! : day
        var params: [String: JSONValue] = ["measured_at": .string(timestamp), "weight_kg": .double(value)]
        if let editingUid { params["weight_uid"] = .string(editingUid) }
        let id = requestId ?? UUID().uuidString.lowercased()
        requestId = id
        if pendingSaves[id] == nil { pendingSaves[id] = revision }
        let response = await app.mutate("weight.upsert", params, requestId: id, quiet: true)
        if response.outcome.isSettled {
            if requestId == id { requestId = nil }
            pendingSaves[id] = nil
        }
        switch response.outcome {
        case .applied, .replayed:
            guard let saved = try? response.result(WeightUpsertResult.self).measurement else { break }
            // A newer draft entered while saving survives this completion.
            if revision == draftRevision {
                editingUid = saved.uid
                editingTimestamp = saved.measuredAt
                editingSource = saved.source
                draftDirty = false
                status = "Saved."
            } else {
                status = "Saved the earlier value; your newer selection is kept."
            }
            await reload()
            if revision == draftRevision { centerLatest() }
        case .missing:
            status = "This entry was removed. Choose its date again to add a new reading."
            editingUid = nil
            await reload()
        case .unavailable:
            status = response.error ?? "Waiting for the engine to confirm this save."
        default:
            status = "Could not save: \(response.error ?? "unknown error"). Your selection is kept."
        }
    }

    func resolvePending(_ id: String, response: EngineResponse) {
        guard let submittedRevision = pendingSaves.removeValue(forKey: id) else { return }
        if requestId == id { requestId = nil }
        if response.outcome.succeeded, let saved = try? response.result(WeightUpsertResult.self).measurement {
            if submittedRevision == draftRevision {
                editingUid = saved.uid
                editingTimestamp = saved.measuredAt
                editingSource = saved.source
                draftDirty = false
                status = "The earlier save was confirmed."
            } else { status = "The earlier save was confirmed; your newer selection is kept." }
        } else { status = response.error ?? "The earlier save was not applied. Your selection is kept." }
    }

    public func requestDelete() {
        guard let uid = selectedHistoryUid else { return }
        app.present(.confirm(ConfirmModel(title: "Delete weight reading?", message: "Delete the selected reading from your history?",
                                          confirmTitle: "Delete", role: .destructive,
                                          action: { [weak self] in await self?.delete(uid) })))
    }

    @MainActor
    private func delete(_ uid: String) async {
        let response = await app.mutate("weight.delete", ["weight_uid": .string(uid)], quiet: true)
        if uid == editingUid && (response.outcome.succeeded || response.outcome == .missing) {
            editingUid = nil; editingTimestamp = nil; editingSource = nil
        }
        status = response.outcome.succeeded ? "Deleted." : (response.outcome == .missing ? "This reading was already removed." : "Could not delete: \(response.error ?? "")")
        await reload()
    }

    public func setChartOption(range newRange: String? = nil, smoothing newSmoothing: String? = nil, historic newHistoric: Bool? = nil,
                               dots: Bool? = nil, span newSpan: Double? = nil) {
        var values: [String: JSONValue] = [:]
        if let newRange { range = newRange; values["weight_chart_range"] = .string(newRange) }
        if let newSmoothing { smoothing = newSmoothing; values["weight_chart_smoothing"] = .string(newSmoothing) }
        if let newHistoric { historic = newHistoric; values["weight_chart_historic"] = .string(newHistoric ? "1" : "0") }
        if let dots { showDots = dots; values["weight_chart_points"] = .string(dots ? "1" : "0") }
        if let newSpan, newSpan.isFinite { span = min(1, max(0.01, newSpan)); values["weight_chart_span"] = .string(String(span)) }
        Task { @MainActor in
            await app.mutate("settings.set", ["values": .object(values)], quiet: true)
            await reload()
        }
    }

    public func setAppearance(density newDensity: String? = nil, contrast: Bool? = nil) {
        if let newDensity { density = newDensity; persist("weight_ui_density", newDensity) }
        if let contrast { highContrast = contrast; persist("weight_ui_contrast", contrast ? "1" : "0") }
    }

    private func persist(_ key: String, _ value: String) {
        Task { @MainActor in await app.setPreference(key, value) }
    }
}
