import Foundation
import SwiftUI

/// W1: the one session panel used from subject history, graph days and agenda
/// blocks. It keeps an explicit selected UID, a draft bound to that UID and its
/// base values, and three distinct commands: Save (fields of the selected
/// session), Move (whole selected session) and Quick add (new session on the
/// displayed subject). Nothing is ever selected implicitly.
public final class SessionPanelModel: ObservableObject {
    public enum Scope: Equatable {
        case subject(uid: String, name: String, color: String)
        case day(String)
    }

    public struct Draft: Equatable {
        public let id = UUID()
        public let targetUid: String?
        public let baseSubjectUid: String
        public let baseStart: String
        public let baseEnd: String
        public var subjectUid: String
        public var start: String
        public var end: String

        static let displayPattern = "yyyy-MM-dd HH:mm:ss"

        init(session: SessionDTO) {
            targetUid = session.uid
            baseSubjectUid = session.subjectUid ?? ""
            baseStart = session.startTime
            baseEnd = session.endTime ?? session.startTime
            subjectUid = baseSubjectUid
            start = LocalTime.format(baseStart, Self.displayPattern)
            end = LocalTime.format(baseEnd, Self.displayPattern)
        }

        init(newFor subjectUid: String, start: Date, end: Date) {
            targetUid = nil
            baseSubjectUid = subjectUid
            baseStart = LocalTime.isoString(start, microseconds: false)
            baseEnd = LocalTime.isoString(end, microseconds: false)
            self.subjectUid = subjectUid
            self.start = LocalTime.format(baseStart, Self.displayPattern)
            self.end = LocalTime.format(baseEnd, Self.displayPattern)
        }

        public var isNew: Bool { targetUid == nil }

        public var isDirty: Bool {
            isNew || subjectUid != baseSubjectUid
                || start != LocalTime.format(baseStart, Self.displayPattern)
                || end != LocalTime.format(baseEnd, Self.displayPattern)
        }

        /// The exact stored string when a field is unchanged, else the edit.
        func wireValue(field text: String, base: String) -> String? {
            if text == LocalTime.format(base, Self.displayPattern) && !isNew { return base }
            return LocalTime.canonicalTimestamp(text)
        }
    }

    public let scope: Scope
    @Published public private(set) var rows: [SessionDTO] = []
    @Published public private(set) var total = 0
    @Published public private(set) var nextOffset: Int?
    @Published public private(set) var selectedUid: String?
    @Published public var draft: Draft?
    @Published public var message: String?
    @Published public var conflict: String?
    @Published public var pendingSelection: SessionDTO?
    @Published public private(set) var inFlight: Set<String> = []
    @Published public var quickAddSubjectUid: String?
    @Published public private(set) var subjects: [SubjectDTO] = []
    @Published public private(set) var loaded = false

    private unowned let app: AppModel
    private var requestIds: [String: String] = [:]
    private var submittedDrafts: [String: Draft] = [:]
    private var epoch = 0
    private static let pageSize = 200

    public init(app: AppModel, scope: Scope, select uid: String? = nil) {
        self.app = app
        self.scope = scope
        selectedUid = uid
        if case .subject(let subjectUid, _, _) = scope { quickAddSubjectUid = subjectUid }
        Task { @MainActor in await refresh() }
    }

    public var title: String {
        switch scope {
        case .subject(_, let name, _): return "Sessions \u{2014} \(name)"
        case .day(let day): return "Sessions \u{00B7} \(LocalTime.format(day, "EEE d MMM yyyy"))"
        }
    }

    public var selected: SessionDTO? { rows.first { $0.uid != nil && $0.uid == selectedUid } }
    public var isDayScope: Bool { if case .day = scope { return true }; return false }

    public var daySummary: [(name: String, color: String, seconds: Int)] {
        var totals: [String: (String, String, Int)] = [:]
        var order: [String] = []
        for row in rows {
            let key = row.subjectUid ?? row.subjectName
            if totals[key] == nil { order.append(key); totals[key] = (row.subjectName, row.color, 0) }
            totals[key]!.2 += row.durationSeconds
        }
        return order.map { totals[$0]! }.sorted { $0.2 > $1.2 }.map { (name: $0.0, color: $0.1, seconds: $0.2) }
    }

    // MARK: loading and reconciliation

    @MainActor
    public func refresh() async {
        epoch += 1
        let mine = epoch
        var fresh: [SessionDTO] = []
        var count = 0
        var next: Int? = nil
        switch scope {
        case .subject(let uid, _, _):
            let limit = max(Self.pageSize, rows.count)
            let response = await app.call("sessions.for_subject", ["subject_uid": .string(uid), "limit": .int(min(500, limit))], kind: .read)
            if response.outcome == .missing { handleScopeGone(); return }
            guard let page = try? response.result(SessionPageDTO.self) else { return }
            fresh = page.sessions.filter { !$0.open }
            count = page.total
            next = page.nextOffset
            // A graph can open any historical session, including one beyond
            // the first page. Keep that explicit target without loading all
            // history or silently changing selection to a newer row.
            if let uid = selectedUid, !fresh.contains(where: { $0.uid == uid }),
               let row = try? await app.call("session.get", ["session_uid": .string(uid)], kind: .read).result(SessionResult.self).session,
               row.subjectUid == uidForScope {
                fresh.append(row)
            }
        case .day(let day):
            let response = await app.call("sessions.for_day", ["day": .string(day)], kind: .read)
            guard let result = try? response.result(DaySessionsDTO.self) else { return }
            fresh = result.sessions.sorted { $0.startTime < $1.startTime }
            count = fresh.count
        }
        let subjectList = await loadSubjects(for: fresh)
        guard mine == epoch else { return }
        subjects = subjectList
        rows = fresh
        total = count
        nextOffset = next
        loaded = true
        reconcile()
        if isDayScope && quickAddSubjectUid == nil { quickAddSubjectUid = selected?.subjectUid }
    }

    @MainActor
    public func loadMore() async {
        guard case .subject(let uid, _, _) = scope, let offset = nextOffset else { return }
        let mine = epoch
        let response = await app.call("sessions.for_subject", ["subject_uid": .string(uid), "offset": .int(offset), "limit": .int(Self.pageSize)], kind: .read)
        guard mine == epoch, nextOffset == offset, let page = try? response.result(SessionPageDTO.self) else { return }
        rows += page.sessions.filter { !$0.open && !rows.map(\.uid).contains($0.uid) }
        nextOffset = page.nextOffset
    }

    @MainActor
    private func loadSubjects(for fresh: [SessionDTO]) async -> [SubjectDTO] {
        var all = (try? await app.call("subjects.list", kind: .read).result(SubjectsListDTO.self))?.subjects ?? []
        // The selected session's own subject may be archived; keep it selectable.
        let archived = (try? await app.call("subjects.list", ["archived": .bool(true)], kind: .read).result(SubjectsListDTO.self))?.subjects ?? []
        let needed = Set(fresh.compactMap(\.subjectUid) + [draft?.subjectUid, uidForScope].compactMap { $0 })
        all += archived.filter { needed.contains($0.uid) }
        return all
    }

    private func handleScopeGone() {
        rows = []
        selectedUid = nil
        if draft?.isDirty != true { draft = nil }
        message = "This subject no longer exists. Any unsaved input is kept until you discard it."
    }

    private var uidForScope: String? {
        if case .subject(let uid, _, _) = scope { return uid }
        return nil
    }

    /// Keep selection and drafts by identity after any refresh.
    private func reconcile() {
        guard let uid = selectedUid else { return }
        guard let row = rows.first(where: { $0.uid == uid }) else {
            let hadEdits = draft?.isDirty == true && draft?.targetUid == uid
            selectedUid = nil
            if draft?.targetUid == uid && !hadEdits { draft = nil }
            conflict = nil
            message = hadEdits
                ? "That session was removed or moved elsewhere; your unsaved input is kept."
                : (inFlight.contains("delete") ? nil : "That session is no longer in this list.")
            return
        }
        guard var current = draft, current.targetUid == uid else {
            if !row.open { draft = Draft(session: row) }
            return
        }
        let changed = row.startTime != current.baseStart || (row.endTime ?? row.startTime) != current.baseEnd
            || (row.subjectUid ?? "") != current.baseSubjectUid
        guard changed else { return }
        if current.isDirty {
            conflict = "This session changed elsewhere while you were editing. Keep your edits or use the latest values."
        } else {
            current = Draft(session: row)
            draft = current
        }
    }

    // MARK: selection

    public func select(_ row: SessionDTO) {
        guard row.uid != selectedUid || row.uid == nil else { return }
        if let draft, draft.isDirty, draft.targetUid != row.uid {
            pendingSelection = row
            return
        }
        apply(selection: row)
    }

    public func confirmPendingSelection(discard: Bool) {
        guard let row = pendingSelection else { return }
        pendingSelection = nil
        if discard { apply(selection: row) }
    }

    private func apply(selection row: SessionDTO) {
        message = nil
        conflict = nil
        selectedUid = row.uid
        draft = row.open || row.uid == nil ? nil : Draft(session: row)
        if isDayScope { quickAddSubjectUid = row.subjectUid ?? quickAddSubjectUid }
    }

    public func clearSelection() {
        selectedUid = nil
        draft = nil
        conflict = nil
    }

    public func useLatest() {
        guard let row = selected, !row.open else { return }
        draft = Draft(session: row)
        conflict = nil
    }

    public func discardDraft() {
        if let row = selected, !row.open { draft = Draft(session: row) } else { draft = nil }
        conflict = nil
        message = nil
    }

    /// Start a new explicit session on the displayed (or chosen) subject.
    public func beginNew() {
        guard let subject = quickAddSubjectUid else { message = "Choose a subject first."; return }
        if let draft, draft.isDirty, !draft.isNew { message = "Save or discard your edits first."; return }
        let now = Date()
        var start = now.addingTimeInterval(-3600)
        if Calendar.current.startOfDay(for: start) != Calendar.current.startOfDay(for: now) { start = Calendar.current.startOfDay(for: now) }
        selectedUid = nil
        draft = Draft(newFor: subject, start: start, end: now)
        message = nil
    }

    // MARK: commands

    private func requestId(for key: String) -> String {
        if let existing = requestIds[key] { return existing }
        let fresh = UUID().uuidString.lowercased()
        requestIds[key] = fresh
        return fresh
    }

    /// A settled outcome frees the key; an unknown outcome keeps its identity
    /// so the retry is the same request, never a second session or move.
    private func settle(_ key: String, _ response: EngineResponse) {
        if response.outcome.isSettled { requestIds[key] = nil; submittedDrafts[key] = nil }
    }

    /// Bind a late confirmation to its original draft, retaining newer fields.
    func resolvePending(_ requestId: String, response: EngineResponse) {
        guard let key = requestIds.first(where: { $0.value == requestId })?.key else { return }
        let submitted = submittedDrafts[key]
        settle(key, response)
        if response.outcome.succeeded, let submitted, let current = draft, current.id == submitted.id,
           let saved = try? response.result(SessionResult.self).session {
            var rebased = Draft(session: saved)
            if current.subjectUid != submitted.subjectUid { rebased.subjectUid = current.subjectUid }
            if current.start != submitted.start { rebased.start = current.start }
            if current.end != submitted.end { rebased.end = current.end }
            selectedUid = saved.uid
            draft = rebased
            conflict = nil
            message = "The earlier save was confirmed. Any newer edits are kept."
        } else if !response.outcome.succeeded {
            message = response.error ?? "The earlier change was not applied. Your input is kept."
        }
    }

    @MainActor
    private func run(_ key: String, _ command: String, _ params: [String: JSONValue]) async -> EngineResponse? {
        guard !inFlight.contains(key) else { return nil }
        inFlight.insert(key)
        defer { inFlight.remove(key) }
        if submittedDrafts[key] == nil && (key == "add" || key.hasPrefix("save:")) { submittedDrafts[key] = draft }
        let response = await app.mutate(command, params, requestId: requestId(for: key), quiet: true)
        settle(key, response)
        if !response.outcome.succeeded {
            message = response.outcome == .unavailable
                ? (response.error ?? "Waiting for the engine to confirm this change.")
                : (response.error ?? "The change was not saved.")
        }
        return response
    }

    @MainActor
    public func save() async {
        guard conflict == nil else { message = "Resolve the changed session before saving your edits."; return }
        guard var current = draft else { return }
        guard current.isNew || current.targetUid == selectedUid else {
            message = "The original session is no longer selected. Your unsaved input is kept."
            return
        }
        guard let start = current.wireValue(field: current.start, base: current.baseStart),
              let end = current.wireValue(field: current.end, base: current.baseEnd) else {
            message = "Enter times as YYYY-MM-DD HH:MM:SS."
            return
        }
        // The engine validates wall-clock duration. Date.timeIntervalSince
        // would reinterpret DST gaps/folds and can reject a valid naive edit.
        let submitted = current
        let key = current.isNew ? "add" : "save:\(current.targetUid!)"
        var params: [String: JSONValue] = ["subject_uid": .string(current.subjectUid), "start_time": .string(start), "end_time": .string(end)]
        let command: String
        if let uid = current.targetUid { params["session_uid"] = .string(uid); command = "session.update" } else { command = "session.add" }
        guard let response = await run(key, command, params), response.outcome.succeeded,
              let saved = try? response.result(SessionResult.self).session else { return }
        message = current.isNew ? "Session added." : "Saved."
        conflict = nil
        // Only clear the draft that was submitted; a newer edit made while
        // waiting stays in place.
        current = draft ?? submitted
        if current == submitted {
            selectedUid = saved.uid
            draft = Draft(session: saved)
        }
        await refresh()
    }

    @MainActor
    public func shift(_ seconds: Int) async {
        guard let row = selected, let uid = row.uid, !row.open else { message = "Select a closed session to move."; return }
        if let draft, draft.isDirty { message = "Save or discard your edits before moving the session."; return }
        guard let response = await run("shift:\(uid):\(seconds)", "session.shift", ["session_uid": .string(uid), "seconds": .int(seconds)]),
              response.outcome.succeeded else { return }
        message = nil
        await refresh()
        if selectedUid == nil { message = "The session moved outside this list." }
    }

    @MainActor
    public func duplicate(to target: String) async {
        guard let row = selected, let uid = row.uid, !row.open else { message = "Select a closed session to duplicate."; return }
        guard let response = await run("duplicate:\(uid):\(target)", "session.duplicate", ["session_uid": .string(uid), "to": .string(target)]),
              response.outcome.succeeded, let copy = try? response.result(SessionResult.self).session else { return }
        await refresh()
        if rows.contains(where: { $0.uid == copy.uid }) {
            if draft?.isDirty != true { selectedUid = copy.uid; draft = Draft(session: copy) }
            message = "Duplicated."
        } else {
            message = target == "today" ? "Duplicated to today." : "Duplicated to the next day."
        }
    }

    public func requestDelete() {
        guard let row = selected, let uid = row.uid, !row.open else { message = "Select a closed session to delete."; return }
        app.present(.confirm(ConfirmModel(title: "Delete Session", message: "Delete this session permanently?",
                                          confirmTitle: "Delete", role: .destructive,
                                          action: { [weak self] in await self?.delete(uid) })))
    }

    @MainActor
    private func delete(_ uid: String) async {
        guard let response = await run("delete", "session.delete", ["session_uid": .string(uid)]), response.outcome.succeeded else { return }
        if selectedUid == uid { clearSelection() }
        message = "Session deleted."
        await refresh()
    }

    @MainActor
    public func quickAdd(minutes: Int) async {
        guard let subject = quickAddSubjectUid else { message = "Choose a subject for the new session."; return }
        let at = LocalTime.isoString(Date())
        guard let response = await run("quick:\(minutes)", "session.quick_add", ["subject_uid": .string(subject), "minutes": .int(minutes), "at": .string(at)]),
              response.outcome.succeeded, let created = try? response.result(SessionResult.self).session else { return }
        await adoptCreated(created)
    }

    @MainActor
    public func addSlot(startHour: Int, endHour: Int) async {
        guard let subject = quickAddSubjectUid else { message = "Choose a subject for the new session."; return }
        let at = LocalTime.isoString(Date())
        guard let response = await run("slot:\(startHour)", "session.add_slot", [
            "subject_uid": .string(subject), "start_hour": .int(startHour), "end_hour": .int(endHour), "at": .string(at),
        ]), response.outcome.succeeded, let created = try? response.result(SessionResult.self).session else { return }
        await adoptCreated(created)
    }

    @MainActor
    private func adoptCreated(_ created: SessionDTO) async {
        await refresh()
        message = "Added \(DurationText.session(created.durationSeconds))."
        if rows.contains(where: { $0.uid == created.uid }), draft?.isDirty != true {
            selectedUid = created.uid
            draft = Draft(session: created)
        }
    }

    public func openCustomDuration() {
        guard quickAddSubjectUid != nil else { message = "Choose a subject for the new session."; return }
        app.present(.customDuration(CustomDurationModel(onConfirm: { [weak self] minutes in await self?.quickAdd(minutes: minutes) })))
    }
}

/// Duration chunks (4h…5m) or an exact hours/minutes value, as in Qt.
public final class CustomDurationModel: ObservableObject {
    public static let chunks: [(String, Int)] = [("4h", 240), ("2h", 120), ("1h", 60), ("30m", 30), ("15m", 15), ("10m", 10), ("5m", 5)]
    @Published public var selectedChunks: Set<Int> = []
    @Published public var hours = 0
    @Published public var minutes = 0
    @Published public var exact = false
    public let onConfirm: (Int) async -> Void

    init(onConfirm: @escaping (Int) async -> Void) { self.onConfirm = onConfirm }

    public var totalMinutes: Int { exact ? hours * 60 + minutes : selectedChunks.reduce(0, +) }

    public func toggle(_ chunk: Int) {
        if exact { exact = false; selectedChunks = [] }
        if selectedChunks.contains(chunk) { selectedChunks.remove(chunk) } else { selectedChunks.insert(chunk) }
        hours = totalMinutes / 60
        minutes = totalMinutes % 60
    }

    public func exactChanged() {
        exact = true
        selectedChunks = []
    }
}
