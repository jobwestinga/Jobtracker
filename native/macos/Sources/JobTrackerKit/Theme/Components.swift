import AppKit
import SwiftUI

// F1 shared components. Every state (normal, hover, focus, pressed, disabled,
// selected) keeps the same geometry: borders are drawn as overlays and the
// focus ring sits outside the control, so nothing shifts when state changes.

public struct ActionButtonStyle: ButtonStyle {
    public let role: ActionRole
    public var compact = false
    public var selected = false
    public var fill = false
    @Environment(\.theme) private var theme
    @Environment(\.isEnabled) private var enabled

    public init(_ role: ActionRole, compact: Bool = false, selected: Bool = false, fill: Bool = false) {
        self.role = role
        self.compact = compact
        self.selected = selected
        self.fill = fill
    }

    public func makeBody(configuration: Configuration) -> some View {
        ActionBody(configuration: configuration, role: selected ? .primary : role, compact: compact, fill: fill, theme: theme, enabled: enabled)
    }

    private struct ActionBody: View {
        let configuration: Configuration
        let role: ActionRole
        let compact: Bool
        let fill: Bool
        let theme: Theme
        let enabled: Bool
        @StateObject private var hovering = ViewState(false)

        var colors: (fill: RGBA, text: RGBA, border: RGBA) {
            switch role {
            case .primary: return (theme.accent, theme.accent.contrastText, theme.accent)
            case .secondary: return (theme.bgTertiary, theme.textPrimary, theme.border)
            case .quiet: return (theme.bgSecondary.alpha(0), theme.textSecondary, theme.border.alpha(0.85))
            case .destructive: return (theme.accentRed, RGBA(hex: "#FFFFFF"), theme.accentRed)
            }
        }

        var body: some View {
            let c = colors
            var fill = c.fill
            var border = c.border
            var text = c.text
            if hovering.value && enabled {
                switch role {
                case .primary, .destructive: fill = fill.scaledValue(0.9)
                case .secondary: border = theme.accent
                case .quiet: fill = theme.bgTertiary.alpha(0.8); text = theme.textPrimary
                }
            }
            if configuration.isPressed { fill = role == .quiet ? theme.bgTertiary : fill.scaledValue(0.82) }
            if !enabled { fill = role == .quiet ? fill : theme.bgTertiary; text = theme.textDimmed; border = theme.border.alpha(0.6) }
            return configuration.label
                .font(compact ? TypeRole.caption.font : TypeRole.control.font)
                .lineLimit(1)
                .foregroundColor(text.color)
                .padding(.horizontal, compact ? 9 : 14)
                .frame(maxWidth: self.fill ? .infinity : nil, minHeight: compact ? 26 : Density.controlMin)
                .background(RoundedRectangle(cornerRadius: Radius.control, style: .continuous).fill(fill.color))
                .overlay(RoundedRectangle(cornerRadius: Radius.control, style: .continuous).strokeBorder(border.color, lineWidth: 1))
                .contentShape(RoundedRectangle(cornerRadius: Radius.control, style: .continuous))
                .onHover { hovering.value = $0 }
                .animation(nil, value: hovering.value)
        }
    }
}

/// `@State` is a macro needing Xcode's SwiftUIMacros plugin; this holder keeps
/// small view-local state buildable with the Command Line Tools.
final class ViewState<Value>: ObservableObject {
    @Published var value: Value
    init(_ value: Value) { self.value = value }
}

public extension View {
    func actionStyle(_ role: ActionRole, compact: Bool = false, selected: Bool = false, fill: Bool = false) -> some View {
        buttonStyle(ActionButtonStyle(role, compact: compact, selected: selected, fill: fill))
    }

    /// A content card with one quiet border (S1) and an optional strong state.
    func card(radius: CGFloat? = nil, highlighted: Bool = false, hovering: Bool = false, dimmed: Bool = false) -> some View {
        modifier(CardModifier(radius: radius, highlighted: highlighted, hovering: hovering, dimmed: dimmed))
    }

    /// Panel/frame surface used by charts and inline panels.
    func surface(radius: CGFloat = Radius.chart) -> some View { modifier(SurfaceModifier(radius: radius)) }
}

struct CardModifier: ViewModifier {
    let radius: CGFloat?
    let highlighted: Bool
    let hovering: Bool
    let dimmed: Bool
    @Environment(\.theme) private var theme

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: radius ?? theme.subjectRadius, style: .continuous)
        let border = highlighted ? theme.accent : (hovering ? theme.cardHoverBorder : theme.cardBorder)
        return content
            .background(shape.fill((hovering ? theme.cardHoverBackground : theme.cardBackground).color))
            .overlay(shape.strokeBorder(border.color, lineWidth: highlighted ? 1.5 : 1))
            .opacity(dimmed ? 0.62 : 1)
    }
}

struct SurfaceModifier: ViewModifier {
    let radius: CGFloat
    @Environment(\.theme) private var theme
    func body(content: Content) -> some View {
        content
            .background(RoundedRectangle(cornerRadius: radius, style: .continuous).fill(theme.panelSurface.color))
            .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous).strokeBorder(theme.border.alpha(160 / 255).color, lineWidth: 1))
    }
}

/// Text field with the shared field role.
public struct FieldStyle: TextFieldStyle {
    @Environment(\.theme) private var theme
    public init() {}
    public func _body(configuration: TextField<Self._Label>) -> some View {
        configuration
            .textFieldStyle(.plain)
            .font(TypeRole.body.font)
            .foregroundColor(theme.textPrimary.color)
            .padding(.horizontal, 10)
            .frame(minHeight: Density.controlMin)
            .background(RoundedRectangle(cornerRadius: Radius.control, style: .continuous).fill(theme.bgPrimary.color))
            .overlay(RoundedRectangle(cornerRadius: Radius.control, style: .continuous).strokeBorder(theme.border.color, lineWidth: 1))
    }
}

public struct FieldLabel: View {
    let text: String
    @Environment(\.theme) private var theme
    public init(_ text: String) { self.text = text }
    public var body: some View {
        Text(text).font(TypeRole.secondary.font.weight(.semibold)).foregroundColor(theme.textSecondary.color)
    }
}

/// Segmented choice with real buttons and an explicit selected state.
public struct ChoiceRow<Value: Hashable>: View {
    let options: [(Value, String)]
    let selection: Value
    let compact: Bool
    let onSelect: (Value) -> Void

    public init(_ options: [(Value, String)], selection: Value, compact: Bool = false, onSelect: @escaping (Value) -> Void) {
        self.options = options
        self.selection = selection
        self.compact = compact
        self.onSelect = onSelect
    }

    public var body: some View {
        HStack(spacing: 6) {
            ForEach(options, id: \.0) { option in
                Button(option.1) { onSelect(option.0) }
                    .actionStyle(.secondary, compact: compact, selected: option.0 == selection)
                    .accessibilityAddTraits(option.0 == selection ? .isSelected : [])
            }
        }
    }
}

/// Small pop-up selector using the native menu.
public struct CompactSelector<Value: Hashable>: View {
    let label: String
    let options: [(Value, String)]
    let selection: Value
    let onSelect: (Value) -> Void
    @Environment(\.theme) private var theme

    public init(_ label: String, options: [(Value, String)], selection: Value, onSelect: @escaping (Value) -> Void) {
        self.label = label
        self.options = options
        self.selection = selection
        self.onSelect = onSelect
    }

    public var body: some View {
        Menu {
            ForEach(options, id: \.0) { option in
                Button { onSelect(option.0) } label: {
                    if option.0 == selection { Label(option.1, systemImage: "checkmark") } else { Text(option.1) }
                }
            }
        } label: {
            HStack(spacing: 6) {
                Text(options.first { $0.0 == selection }?.1 ?? "").font(TypeRole.control.font).foregroundColor(theme.textPrimary.color)
                Image(systemName: "chevron.down").font(.system(size: 9, weight: .semibold)).foregroundColor(theme.textSecondary.color)
            }
            .padding(.horizontal, 10)
            .frame(minHeight: Density.controlMin)
            .background(RoundedRectangle(cornerRadius: Radius.control, style: .continuous).fill(theme.bgTertiary.color))
            .overlay(RoundedRectangle(cornerRadius: Radius.control, style: .continuous).strokeBorder(theme.border.color, lineWidth: 1))
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .accessibilityLabel(label)
    }
}

public struct ColorMarker: View {
    let color: String
    var width: CGFloat = 4
    var height: CGFloat = 34
    public init(_ color: String, width: CGFloat = 4, height: CGFloat = 34) { self.color = color; self.width = width; self.height = height }
    public var body: some View {
        RoundedRectangle(cornerRadius: width / 2, style: .continuous).fill(RGBA(hex: color).color).frame(width: width, height: height)
    }
}

public struct Dot: View {
    let color: String
    var size: CGFloat = 9
    public init(_ color: String, size: CGFloat = 9) { self.color = color; self.size = size }
    public var body: some View { Circle().fill(RGBA(hex: color).color).frame(width: size, height: size) }
}

/// Vector navigation glyphs drawn to match the Qt sidebar icons.
public struct NavGlyph: Shape {
    public enum Kind { case goals, subjects, graphs, weight }
    let kind: Kind
    public init(_ kind: Kind) { self.kind = kind }

    public func path(in rect: CGRect) -> Path {
        let c = CGPoint(x: rect.midX, y: rect.midY)
        var p = Path()
        switch kind {
        case .goals:
            p.addEllipse(in: CGRect(x: c.x - 6.2, y: c.y - 6.2, width: 12.4, height: 12.4))
            p.move(to: CGPoint(x: c.x - 2.6, y: c.y + 0.2))
            p.addLine(to: CGPoint(x: c.x - 0.6, y: c.y + 2.2))
            p.addLine(to: CGPoint(x: c.x + 3.0, y: c.y - 1.8))
        case .subjects:
            p.addEllipse(in: CGRect(x: c.x - 6.2, y: c.y - 6.2, width: 12.4, height: 12.4))
        case .graphs, .weight:
            break
        }
        return p
    }
}

public struct NavIcon: View {
    let kind: NavGlyph.Kind
    let color: Color
    public init(_ kind: NavGlyph.Kind, color: Color) { self.kind = kind; self.color = color }

    public var body: some View {
        Canvas { context, size in
            let c = CGPoint(x: size.width / 2, y: size.height / 2)
            let stroke = StrokeStyle(lineWidth: 1.55, lineCap: .round, lineJoin: .round)
            switch kind {
            case .goals:
                context.stroke(NavGlyph(.goals).path(in: CGRect(origin: .zero, size: size)), with: .color(color), style: stroke)
            case .subjects:
                context.stroke(NavGlyph(.subjects).path(in: CGRect(origin: .zero, size: size)), with: .color(color), style: stroke)
                var tri = Path()
                tri.move(to: CGPoint(x: c.x - 1.6, y: c.y - 2.6))
                tri.addLine(to: CGPoint(x: c.x - 1.6, y: c.y + 2.6))
                tri.addLine(to: CGPoint(x: c.x + 2.8, y: c.y))
                tri.closeSubpath()
                context.fill(tri, with: .color(color))
            case .graphs:
                for (x, top, height) in [(-6.0, -0.5, 6.5), (-1.4, -5.2, 11.2), (3.2, -2.8, 8.8)] {
                    context.fill(Path(roundedRect: CGRect(x: c.x + x, y: c.y + top, width: 2.8, height: height), cornerRadius: 1), with: .color(color))
                }
            case .weight:
                var trend = Path()
                trend.move(to: CGPoint(x: c.x - 6, y: c.y + 3.2))
                trend.addLine(to: CGPoint(x: c.x - 2, y: c.y - 1))
                trend.addLine(to: CGPoint(x: c.x + 1.6, y: c.y + 1.4))
                trend.addLine(to: CGPoint(x: c.x + 5.5, y: c.y - 3.4))
                context.stroke(trend, with: .color(color), style: stroke)
                context.fill(Path(ellipseIn: CGRect(x: c.x + 3.5, y: c.y - 5.4, width: 4, height: 4)), with: .color(color))
            }
        }
        .frame(width: 18, height: 18)
        .accessibilityHidden(true)
    }
}

public struct PageHeader<Trailing: View>: View {
    let title: String
    let trailing: Trailing
    @Environment(\.theme) private var theme

    public init(_ title: String, @ViewBuilder trailing: () -> Trailing) {
        self.title = title
        self.trailing = trailing()
    }

    public var body: some View {
        HStack(spacing: 8) {
            Text(title).font(TypeRole.pageTitle.font).foregroundColor(theme.textPrimary.color)
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 8)
            trailing
        }
    }
}

/// Short-lived emphasis for keyboard-triggered actions; off with reduced motion.
public struct PulseModifier: ViewModifier {
    let active: Bool
    let reduceMotion: Bool
    let onDone: () -> Void
    @StateObject private var dim = ViewState(false)

    public func body(content: Content) -> some View {
        content
            .opacity(dim.value ? 0.62 : 1)
            .onChange(of: active) { value in
                guard value else { return }
                guard !reduceMotion else { onDone(); return }
                withAnimation(.easeInOut(duration: 0.09)) { dim.value = true }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.09) {
                    withAnimation(.easeInOut(duration: 0.09)) { dim.value = false }
                    onDone()
                }
            }
    }
}
