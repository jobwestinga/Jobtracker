import SwiftUI

/// The window: animated background, sidebar, current page, panels, notices.
public struct RootView: View {
    @ObservedObject var app: AppModel

    public init(app: AppModel) { self.app = app }

    public var body: some View {
        let theme = app.effectiveTheme
        ZStack {
            FxBackground(theme: theme, animating: app.foreground && !app.reduceMotion)
            HStack(spacing: 12) {
                if !(app.page == .weight && app.weightFullscreen) {
                    Sidebar(app: app, subjects: app.subjects)
                }
                PageHost(app: app)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            .padding(EdgeInsets(top: 12, leading: 12, bottom: 12, trailing: 14))
            .accessibilityHidden(!app.panels.isEmpty)
            PanelStack(app: app)
            NoticeView(app: app)
            ConnectionOverlay(app: app)
        }
        .environment(\.theme, theme)
        .foregroundColor(theme.textPrimary.color)
        .font(TypeRole.body.font)
    }
}

struct PageHost: View {
    @ObservedObject var app: AppModel

    var body: some View {
        Group {
            switch app.page {
            case .goals: GoalsPage(app: app, store: app.goals)
            case .subjects: SubjectsPage(app: app, store: app.subjects)
            case .graphs: GraphsPage(app: app, store: app.graphs)
            case .weight: WeightPage(app: app, store: app.weight)
            }
        }
        .transition(.opacity)
        .animation(app.reduceMotion ? nil : .easeOut(duration: 0.13), value: app.page)
    }
}

/// Port of FxBackgroundWidget: Base/Clean/Checkerboard are static; Glow and
/// Space drift slowly and pause when inactive or with reduced motion.
struct FxBackground: View {
    let theme: Theme
    let animating: Bool

    var body: some View {
        if animating && (theme.fxName == "Glow" || theme.fxName == "Space") {
            TimelineView(.animation(minimumInterval: 1.0 / 25.0, paused: false)) { timeline in
                canvas(phase: timeline.date.timeIntervalSinceReferenceDate * 0.25)
            }
        } else {
            canvas(phase: 0)
        }
    }

    private func canvas(phase: Double) -> some View {
        Canvas { context, size in
            let rect = CGRect(origin: .zero, size: size)
            let w = size.width, h = size.height
            func linear(_ stops: [(RGBA, Double)], from: CGPoint, to: CGPoint) -> GraphicsContext.Shading {
                .linearGradient(Gradient(stops: stops.map { .init(color: $0.0.color, location: $0.1) }), startPoint: from, endPoint: to)
            }
            switch theme.fxName {
            case "Base":
                context.fill(Path(rect), with: .color(theme.bgPrimary.color))
            case "Clean":
                context.fill(Path(rect), with: linear([(theme.bgPrimary, 0), (theme.bgSecondary, 1)], from: .zero, to: CGPoint(x: 0, y: h)))
                context.fill(Path(rect), with: linear([(theme.textPrimary.alpha(6 / 255), 0), (RGBA(r: 0, g: 0, b: 0, a: 0), 0.5),
                                                       (theme.textPrimary.alpha(4 / 255), 1)], from: .zero, to: CGPoint(x: w, y: 0)))
            case "Space":
                context.fill(Path(rect), with: linear([(theme.bgPrimary.scaledValue(1 / 1.35), 0), (theme.bgPrimary, 1)], from: .zero, to: CGPoint(x: 0, y: h)))
                let center = CGPoint(x: w * (0.5 + 0.05 * sin(phase * 0.4)), y: h * (0.18 + 0.03 * cos(phase * 0.35)))
                context.fill(Path(rect), with: .radialGradient(Gradient(stops: [
                    .init(color: theme.accent.alpha(22 / 255).color, location: 0),
                    .init(color: theme.accent.alpha(6 / 255).color, location: 0.55),
                    .init(color: .clear, location: 1),
                ]), center: center, startRadius: 0, endRadius: max(w, h) * 0.62))
            case "Checkerboard":
                context.fill(Path(rect), with: .color(theme.bgPrimary.color))
                let sheen = min(140, h)
                context.fill(Path(CGRect(x: 0, y: 0, width: w, height: sheen)),
                             with: linear([(theme.textPrimary.alpha(6 / 255), 0), (RGBA(r: 0, g: 0, b: 0, a: 0), 1)], from: .zero, to: CGPoint(x: 0, y: sheen)))
            default:
                context.fill(Path(rect), with: linear([(theme.bgPrimary, 0), (theme.bgSecondary.scaledValue(1 / 1.15), 1)], from: .zero, to: CGPoint(x: 0, y: h)))
                let center = CGPoint(x: w * (0.52 + 0.04 * sin(phase * 0.6)), y: h * (0.08 + 0.02 * cos(phase * 0.5)))
                context.fill(Path(rect), with: .radialGradient(Gradient(stops: [
                    .init(color: theme.accent.alpha(26 / 255).color, location: 0),
                    .init(color: theme.accent.alpha(9 / 255).color, location: 0.5),
                    .init(color: .clear, location: 1),
                ]), center: center, startRadius: 0, endRadius: max(w, h) * 0.55))
            }
        }
        .ignoresSafeArea()
        .accessibilityHidden(true)
    }
}

struct NoticeView: View {
    @ObservedObject var app: AppModel
    @Environment(\.theme) private var theme

    var body: some View {
        VStack {
            Spacer()
            if let notice = app.notice {
                HStack(spacing: 8) {
                    Image(systemName: notice.isError ? "exclamationmark.triangle.fill" : "checkmark.circle.fill")
                        .foregroundColor((notice.isError ? theme.accentRed : theme.accentGreen).color)
                    Text(notice.text).font(TypeRole.secondary.font).foregroundColor(theme.textPrimary.color)
                        .fixedSize(horizontal: false, vertical: true)
                    if let undo = app.undoLabel, !notice.isError {
                        Button("Undo") { Task { @MainActor in await app.performUndo() } }
                            .actionStyle(.quiet, compact: true)
                            .help(undo)
                    }
                }
                .padding(.horizontal, 14).padding(.vertical, 10)
                .background(RoundedRectangle(cornerRadius: Radius.card, style: .continuous).fill(theme.bgSecondary.color))
                .overlay(RoundedRectangle(cornerRadius: Radius.card, style: .continuous).strokeBorder((notice.isError ? theme.accentRed : theme.border).color))
                .shadow(color: .black.opacity(0.35), radius: 12, y: 4)
                .padding(.bottom, 18)
                .transition(.move(edge: .bottom).combined(with: .opacity))
                .accessibilityElement(children: .combine)
                .accessibilityAddTraits(.isStaticText)
            }
        }
        .frame(maxWidth: 520)
        .animation(app.reduceMotion ? nil : .easeOut(duration: 0.18), value: app.notice)
        .allowsHitTesting(app.notice != nil)
    }
}

struct ConnectionOverlay: View {
    @ObservedObject var app: AppModel
    @Environment(\.theme) private var theme

    var body: some View {
        switch app.connection {
        case .ready:
            EmptyView()
        case .idle, .starting:
            overlay(title: "Opening JobTracker…", detail: nil, spinner: true)
        case .restarting(let message):
            overlay(title: message, detail: "Your saved data is unchanged.", spinner: true)
        case .failed(let message), .incompatible(let message):
            overlay(title: "JobTracker could not open its data", detail: message, spinner: false)
        case .storeInUse(let message):
            overlay(title: message, detail: "Switch to the open JobTracker window to continue. Only one app may own this data at a time.", spinner: false)
        case .stopped:
            overlay(title: "JobTracker is closing…", detail: nil, spinner: true)
        }
    }

    private func overlay(title: String, detail: String?, spinner: Bool) -> some View {
        ZStack {
            theme.bgPrimary.alpha(0.86).color.ignoresSafeArea()
            VStack(spacing: 12) {
                if spinner { ProgressView().controlSize(.small) }
                Text(title).font(TypeRole.section.font).multilineTextAlignment(.center)
                if let detail {
                    Text(detail).font(TypeRole.secondary.font).foregroundColor(theme.textSecondary.color)
                        .multilineTextAlignment(.center).frame(maxWidth: 420)
                }
            }
            .padding(28)
            .surface(radius: Radius.panel)
        }
    }
}
