import Foundation
import SwiftUI

/// Subjects page presentation state; canonical rows come from `subjects.list`.
public final class SubjectsStore: ObservableObject {
    public static let filters = ["Total", "Today", "Last 7 days", "Last 30 days"]

    @Published public private(set) var list: SubjectsListDTO?
    @Published public private(set) var showingArchived = false
    @Published public var filter = "Total"
    @Published public private(set) var busy: Set<String> = []
    @Published public private(set) var lastStoppedUid: String?
    @Published public var pulseUid: String?
    public var filterTouched = false

    private unowned let app: AppModel
    private var epoch = 0

    init(app: AppModel) { self.app = app }

    public var subjects: [SubjectDTO] { list?.subjects ?? [] }

    /// Resume follows actual timer history (`get_last_tracked_subject`), kept
    /// for a subject just stopped even when its short session was discarded.
    public var resumeSubject: SubjectDTO? {
        guard let list, !list.archived else { return nil }
        if let stopped = lastStoppedUid, let subject = list.subjects.first(where: { $0.uid == stopped }) { return subject }
        guard let uid = list.resumeSubjectUid else { return nil }
        return list.subjects.first { $0.uid == uid }
    }

    @MainActor
    public func reload() async {
        epoch += 1
        let mine = epoch
        let response = await app.call("subjects.list", ["archived": .bool(showingArchived), "filter": .string(filter)], kind: .read)
        guard mine == epoch, let result = try? response.result(SubjectsListDTO.self) else { return }
        list = result
        app.adopt(timer: result.timer)
    }

    public func toggleArchived() {
        showingArchived.toggle()
        Task { @MainActor in await reload() }
    }

    public func setFilter(_ value: String) {
        guard Self.filters.contains(value), value != filter else { return }
        filter = value
        filterTouched = true
        Task { @MainActor in
            await reload()
            await app.setPreference("subject_stats_filter", value)
        }
    }

    // MARK: tracking

    /// Click/Enter on a card: start, or confirm a switch while another runs.
    @MainActor
    public func activate(_ subject: SubjectDTO) async {
        guard !subject.archived, !busy.contains(subject.uid) else { return }
        let timer = app.timer
        if let active = timer.active, let current = timer.subject {
            guard current.uid != subject.uid else { return }
            let sourceUid = active.uid
            app.present(.confirm(ConfirmModel(
                title: "Switch Subject",
                message: "Stop \u{201C}\(current.name)\u{201D} and start tracking \u{201C}\(subject.name)\u{201D}?",
                confirmTitle: "Switch", role: .primary,
                action: { [weak self] in await self?.switchTo(subject, from: sourceUid) })))
            return
        }
        await start(subject)
    }

    @MainActor
    func start(_ subject: SubjectDTO) async {
        let at = LocalTime.isoString(Date())
        busy.insert(subject.uid)
        defer { busy.remove(subject.uid) }
        let response = await app.mutate("timer.start", ["subject_uid": .string(subject.uid), "at": .string(at)])
        if response.outcome.succeeded, let timer = try? response.result(TimerDTO.self) {
            app.adopt(timer: timer)
            lastStoppedUid = nil
            pulseUid = subject.uid
        }
    }

    @MainActor
    func switchTo(_ subject: SubjectDTO, from sourceUid: String?) async {
        let at = LocalTime.isoString(Date())
        busy.insert(subject.uid)
        defer { busy.remove(subject.uid) }
        let response = await app.mutate("timer.switch", [
            "subject_uid": .string(subject.uid), "source_session_uid": .optional(sourceUid), "at": .string(at),
        ])
        if response.outcome.succeeded, let timer = try? response.result(TimerDTO.self) {
            app.adopt(timer: timer)
            pulseUid = subject.uid
        }
    }

    @MainActor
    public func stop() async {
        guard let active = app.timer.active, let uid = active.uid else { return }
        let stoppedSubject = app.timer.subject?.uid
        let at = LocalTime.isoString(Date())
        let response = await app.mutate("timer.stop", ["session_uid": .string(uid), "at": .string(at)])
        guard response.outcome.succeeded, let result = try? response.result(StopResult.self) else { return }
        lastStoppedUid = stoppedSubject
        app.adopt(timer: result.timer)
        if !result.kept { app.show("Session under 30s discarded") }
    }

    @MainActor
    public func resume() async {
        guard let subject = resumeSubject else { return }
        await activate(subject)
    }

    // MARK: management

    @MainActor
    public func archive(_ subject: SubjectDTO) async {
        let response = await app.mutate("subject.archive", ["subject_uid": .string(subject.uid)])
        if response.outcome == .applied {
            app.registerUndo("Unarchive \(subject.name)") { [weak self] in
                await self?.app.mutate("subject.unarchive", ["subject_uid": .string(subject.uid)])
            }
        }
    }

    @MainActor
    public func unarchive(_ subject: SubjectDTO) async {
        let response = await app.mutate("subject.unarchive", ["subject_uid": .string(subject.uid)])
        if response.outcome == .applied {
            app.registerUndo("Archive \(subject.name)") { [weak self] in
                await self?.app.mutate("subject.archive", ["subject_uid": .string(subject.uid)])
            }
        }
    }

    @MainActor
    public func requestDelete(_ subject: SubjectDTO) async {
        let response = await app.call("subject.deletion_summary", ["subject_uid": .string(subject.uid)], kind: .read)
        guard let summary = try? response.result(DeletionSummaryDTO.self) else { app.report(response); return }
        if summary.sessionCount == 0 {
            app.present(.confirm(ConfirmModel(
                title: "Delete Subject", message: "Delete \u{201C}\(subject.name)\u{201D}? It has no tracked sessions.",
                confirmTitle: "Delete", role: .destructive,
                action: { [weak self] in await self?.app.mutate("subject.delete", ["subject_uid": .string(subject.uid)]) })))
        } else {
            app.present(.deleteSubject(DeleteSubjectModel(subject: subject, summary: summary)))
        }
    }

    /// Persist a drag reorder; the previous order becomes the single undo step.
    @MainActor
    public func reorder(_ uids: [String]) async {
        let previous = subjects.map(\.uid)
        guard previous != uids, let current = list else { return }
        epoch += 1  // Ignore list reads captured before this final drop.
        let byUid = Dictionary(uniqueKeysWithValues: current.subjects.map { ($0.uid, $0) })
        list = SubjectsListDTO(archived: current.archived, filter: current.filter, subjects: uids.compactMap { byUid[$0] },
                               resumeSubjectUid: current.resumeSubjectUid, todaySeconds: current.todaySeconds,
                               suggestedColors: current.suggestedColors, timer: current.timer)
        let archived = showingArchived
        let response = await app.mutate("subject.reorder", ["subject_uids": .strings(uids), "archived": .bool(archived)])
        if response.outcome == .applied {
            app.registerUndo("Restore subject order") { [weak self] in
                await self?.app.mutate("subject.reorder", ["subject_uids": .strings(previous), "archived": .bool(archived)])
            }
        } else {
            await reload()
        }
    }

    public func openEditor(for subject: SubjectDTO? = nil) {
        let active = subjects.filter { !$0.archived && $0.uid != subject?.uid }.map(\.color)
        app.present(.subjectEditor(SubjectEditorModel(app: app, subject: subject, activeColors: active,
                                                      suggestions: list?.suggestedColors ?? [])))
    }

    public func openSessions(for subject: SubjectDTO) {
        app.present(.sessions(SessionPanelModel(app: app, scope: .subject(uid: subject.uid, name: subject.name, color: subject.color))))
    }
}
