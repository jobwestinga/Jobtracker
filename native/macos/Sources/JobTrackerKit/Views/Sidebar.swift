import SwiftUI

/// The familiar 142 px sidebar: brand, settings, sync status, the four pages
/// in their existing order and the active-timer return button. No numbers.
struct Sidebar: View {
    @ObservedObject var app: AppModel
    @ObservedObject var subjects: SubjectsStore
    @Environment(\.theme) private var theme

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 4) {
                Text("JobTracker").font(.system(size: 15, weight: .bold)).foregroundColor(theme.textPrimary.color)
                Spacer(minLength: 2)
                GearButton(action: app.openSettings)
            }
            .padding(.leading, 4)
            .padding(.bottom, 2)

            SyncStatusBox(app: app)
                .padding(.bottom, 4)

            ForEach(AppModel.Page.allCases) { page in
                NavButton(page: page, selected: app.page == page) { app.select(page) }
            }

            Spacer(minLength: 8)

            if app.timer.active != nil, let subject = app.timer.subject, app.page != .subjects {
                ActiveTimerReturn(app: app, subject: subject)
            }
        }
        .padding(EdgeInsets(top: 12, leading: 9, bottom: 10, trailing: 9))
        .frame(width: Density.sidebarWidth)
        .frame(maxHeight: .infinity)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(theme.bgSecondary.alpha(0.92).color))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(theme.border.color, lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Navigation")
    }
}

private struct GearButton: View {
    let action: () -> Void
    @Environment(\.theme) private var theme
    @StateObject private var hovering = ViewState(false)

    var body: some View {
        Button(action: action) {
            Image(systemName: "gearshape")
                .font(.system(size: 13, weight: .medium))
                .foregroundColor((hovering.value ? theme.textPrimary : theme.textSecondary).color)
                .frame(width: 28, height: 28)
                .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(hovering.value ? theme.bgTertiary.color : .clear))
                .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).strokeBorder(hovering.value ? theme.border.color : .clear))
        }
        .buttonStyle(.plain)
        .onHover { hovering.value = $0 }
        .help("Settings")
        .accessibilityLabel("Settings")
    }
}

private struct NavButton: View {
    let page: AppModel.Page
    let selected: Bool
    let action: () -> Void
    @Environment(\.theme) private var theme
    @StateObject private var hovering = ViewState(false)

    var body: some View {
        let fg = selected ? theme.accent.contrastText : (hovering.value ? theme.textPrimary : theme.textSecondary)
        Button(action: action) {
            HStack(spacing: 10) {
                NavIcon(kind, color: fg.color)
                Text(page.title).font(.system(size: 13, weight: selected ? .semibold : .medium)).foregroundColor(fg.color)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 10)
            .frame(height: 38)
            .background(RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(selected ? theme.accent.color : (hovering.value ? theme.bgTertiary.color : .clear)))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering.value = $0 }
        .help("\(page.title) (Left/Right arrows)")
        .accessibilityLabel("\(page.title) tab")
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private var kind: NavGlyph.Kind {
        switch page {
        case .goals: return .goals
        case .subjects: return .subjects
        case .graphs: return .graphs
        case .weight: return .weight
        }
    }
}

/// Persistent sync status with its action (Settings, Sync or Retry).
struct SyncStatusBox: View {
    @ObservedObject var app: AppModel
    @Environment(\.theme) private var theme

    var body: some View {
        let status = app.sync
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 5) {
                if status.busy { ProgressView().controlSize(.mini).scaleEffect(0.7).frame(width: 10, height: 10) }
                else if !status.error.isEmpty { Circle().fill(theme.accentRed.color).frame(width: 6, height: 6).padding(.top, 4) }
                else if status.pending > 0 { Circle().fill(theme.accent.color).frame(width: 6, height: 6).padding(.top, 4) }
                Text(status.title)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(theme.textSecondary.color)
                    .fixedSize(horizontal: false, vertical: true)
                    .lineLimit(3)
            }
            Button(status.action) {
                if status.action == "Settings" { app.openSettings() }
                else { Task { @MainActor in _ = await app.call("sync.now", kind: .action) } }
            }
            .actionStyle(.secondary, compact: true, fill: true)
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(theme.bgPrimary.alpha(0.7).color))
        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(theme.border.color, lineWidth: 1))
        .help(status.detail)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Sync status: \(status.title)")
    }
}

/// Returns to Subjects without stopping tracking.
private struct ActiveTimerReturn: View {
    @ObservedObject var app: AppModel
    let subject: SubjectDTO
    @Environment(\.theme) private var theme
    @StateObject private var hovering = ViewState(false)

    var body: some View {
        Button { app.select(.subjects) } label: {
            TimelineView(.periodic(from: Date(), by: 1)) { context in
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 5) {
                        Dot(subject.color, size: 7)
                        Text(subject.name).font(.system(size: 11, weight: .bold)).lineLimit(1).truncationMode(.tail)
                    }
                    Text("\(DurationText.clock(app.elapsed(at: context.date)))  \u{203A}")
                        .font(.system(size: 11, weight: .bold).monospacedDigit())
                }
                .foregroundColor(theme.textPrimary.color)
                .padding(.horizontal, 9).padding(.vertical, 6)
                .frame(maxWidth: .infinity, minHeight: 46, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill((hovering.value ? theme.bgTertiary : theme.bgPrimary).color))
                .overlay(RoundedRectangle(cornerRadius: 9, style: .continuous).strokeBorder(RGBA(hex: subject.color).color, lineWidth: 1.5))
            }
        }
        .buttonStyle(.plain)
        .onHover { hovering.value = $0 }
        .help("Return to Subjects. This does not stop tracking.")
        .accessibilityLabel("Return to the active timer, tracking \(subject.name)")
    }
}
