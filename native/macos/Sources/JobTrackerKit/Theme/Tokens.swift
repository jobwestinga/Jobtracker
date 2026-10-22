import AppKit
import SwiftUI

/// Decoded generated tokens (see scripts/generate_ui_tokens.py).
public struct DesignTokens: Decodable {
    public struct Reference: Decodable, Equatable { public let token: String; public let alpha: Int; public let width: Double? }
    public struct Fx: Decodable, Equatable {
        public let cardBackground: Reference
        public let cardHoverBackground: Reference
        public let cardBorder: Reference
        public let cardHoverBorder: Reference
        public let timerBackground: Reference
        public let timerBorder: Reference
        public let radius: Double
        public let subjectRadius: Double
        public let taskRadius: Double
    }
    public struct ThemeChoice: Decodable, Equatable, Hashable { public let name: String; public let fx: String; public let palette: String; public let description: String }
    public struct TypeRole: Decodable, Equatable { public let size: Double; public let weight: String; public let monospacedDigits: Bool?; public let strongWeight: String?; public let minimum: Double? }
    public struct HourTier: Decodable, Equatable { public let maxHours: Double?; public let color: String; public let strong: Bool }
    public struct HourTiers: Decodable, Equatable {
        public struct Scale: Decodable, Equatable { public let base: Double; public let capHours: Double; public let divisor: Double }
        public struct Bloom: Decodable, Equatable { public let strongRadius: Double; public let strongAlpha: Double; public let peakRadius: Double; public let peakAlpha: Double; public let peakAboveHours: Double }
        public let scale: Scale
        public let tiers: [HourTier]
        public let bloom: Bloom
    }
    public struct SessionPresets: Decodable, Equatable { public let durationsMinutes: [Int]; public let movesSeconds: [Int]; public let minimumSeconds: Int }
    public struct Agenda: Decodable, Equatable { public let adjacencySeconds: Double; public let boldFillMix: Double; public let edgeWidth: Double; public let minimumTextContrast: Double }
    public struct Roles: Decodable {
        public let typography: [String: TypeRole]
        public let spacing: [String: Double]
        public let radius: [String: Double]
        public let border: [String: Double]
        public let density: [String: Double]
        public let subjectColors: [String]
        public let hourTiers: HourTiers
        public let sessionPresets: SessionPresets
        public let agenda: Agenda
    }

    public let palettes: [String: [String: String]]
    public let paletteOrder: [String]
    public let fx: [String: Fx]
    public let fxOrder: [String]
    public let themes: [ThemeChoice]
    public let roles: Roles

    /// Role dictionaries keep their JSON keys; accept camelCase names too.
    public static func lookup<T>(_ values: [String: T], _ key: String) -> T? {
        if let value = values[key] { return value }
        let snake = key.reduce(into: "") { out, character in
            if character.isUppercase { out += "_" + character.lowercased() } else { out.append(character) }
        }
        return values[snake]
    }

    public static let shared: DesignTokens = {
        try! resultDecoder.decode(DesignTokens.self, from: Data(TokensData.json.utf8))
    }()
}

public struct RGBA: Equatable, Hashable {
    public var r: Double, g: Double, b: Double, a: Double

    public init(r: Double, g: Double, b: Double, a: Double = 1) { self.r = r; self.g = g; self.b = b; self.a = a }

    public init(hex: String, alpha: Double = 1) {
        var value = hex.trimmingCharacters(in: .whitespaces)
        if value.hasPrefix("#") { value.removeFirst() }
        let number = UInt32(value.prefix(6), radix: 16) ?? 0
        self.init(r: Double((number >> 16) & 0xFF) / 255, g: Double((number >> 8) & 0xFF) / 255, b: Double(number & 0xFF) / 255, a: alpha)
    }

    public var color: Color { Color(.sRGB, red: r, green: g, blue: b, opacity: a) }
    public var nsColor: NSColor { NSColor(srgbRed: r, green: g, blue: b, alpha: a) }
    public var cgColor: CGColor { CGColor(srgbRed: r, green: g, blue: b, alpha: a) }
    public func alpha(_ value: Double) -> RGBA { RGBA(r: r, g: g, b: b, a: value) }

    public func mixed(with other: RGBA, _ amount: Double) -> RGBA {
        RGBA(r: r + (other.r - r) * amount, g: g + (other.g - g) * amount, b: b + (other.b - b) * amount, a: a + (other.a - a) * amount)
    }

    /// Qt `QColor.lighter(factor)`/`darker(factor)` approximations in HSV value.
    public func scaledValue(_ factor: Double) -> RGBA {
        RGBA(r: min(1, r * factor), g: min(1, g * factor), b: min(1, b * factor), a: a)
    }

    public var hexString: String {
        String(format: "#%02X%02X%02X", Int((r * 255).rounded()), Int((g * 255).rounded()), Int((b * 255).rounded()))
    }

    /// WCAG relative luminance.
    public var luminance: Double {
        func channel(_ c: Double) -> Double { c <= 0.03928 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4) }
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    }

    public func contrast(with other: RGBA) -> Double {
        let (a, b) = (luminance, other.luminance)
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)
    }

    /// Same rule as `styles._contrast_text`.
    public var contrastText: RGBA {
        let lum = (0.299 * r + 0.587 * g + 0.114 * b)
        return lum > 0.62 ? RGBA(hex: "#0B1120") : RGBA(hex: "#FFFFFF")
    }
}

/// One complete appearance: a palette resolved with an FX style.
public struct Theme: Equatable {
    public let fxName: String
    public let paletteName: String
    public let bgPrimary, bgSecondary, bgTertiary, border, borderFocus: RGBA
    public let textPrimary, textSecondary, textDimmed, accent, accentGreen, accentRed: RGBA
    public let cardBackground, cardHoverBackground, timerBackground: RGBA
    public let cardBorder, cardHoverBorder, timerBorder: RGBA
    public let cardBorderWidth, cardHoverBorderWidth, timerBorderWidth: Double
    public let radius, subjectRadius, taskRadius: Double

    public init(fx: String, palette: String, tokens: DesignTokens = .shared) {
        let paletteName = tokens.palettes[palette] != nil ? palette : "Ocean"
        let fxName = tokens.fx[fx] != nil ? fx : "Glow"
        let values = tokens.palettes[paletteName]!
        let style = tokens.fx[fxName]!
        func color(_ key: String) -> RGBA { RGBA(hex: values[key] ?? "#000000") }
        func resolve(_ ref: DesignTokens.Reference) -> RGBA { color(ref.token).alpha(Double(ref.alpha) / 255) }
        self.fxName = fxName
        self.paletteName = paletteName
        bgPrimary = color("BG_PRIMARY"); bgSecondary = color("BG_SECONDARY"); bgTertiary = color("BG_TERTIARY")
        border = color("BORDER_COLOR"); borderFocus = color("BORDER_FOCUS")
        textPrimary = color("TEXT_PRIMARY"); textSecondary = color("TEXT_SECONDARY"); textDimmed = color("TEXT_DIMMED")
        accent = color("ACCENT"); accentGreen = color("ACCENT_GREEN"); accentRed = color("ACCENT_RED")
        cardBackground = resolve(style.cardBackground); cardHoverBackground = resolve(style.cardHoverBackground)
        timerBackground = resolve(style.timerBackground)
        cardBorder = resolve(style.cardBorder); cardHoverBorder = resolve(style.cardHoverBorder); timerBorder = resolve(style.timerBorder)
        cardBorderWidth = style.cardBorder.width ?? 1; cardHoverBorderWidth = style.cardHoverBorder.width ?? 1
        timerBorderWidth = style.timerBorder.width ?? 1
        radius = style.radius; subjectRadius = style.subjectRadius; taskRadius = style.taskRadius
    }

    public static let fallback = Theme(fx: "Clean", palette: "Ocean")

    /// The named theme matching this pair, or nil for a kept legacy appearance.
    public var namedChoice: DesignTokens.ThemeChoice? {
        DesignTokens.shared.themes.first { $0.fx == fxName && $0.palette == paletteName }
    }

    /// Near-opaque content surface used for panels and chart frames.
    public var panelSurface: RGBA { bgSecondary.alpha(238 / 255) }
}

/// Typography roles from design/ui_roles.json, using the system font.
public enum TypeRole: String {
    case pageTitle, section, cardTitle, body, secondary, caption, control
    case duration, durationCompact, chartAxis, chartTotal

    var role: DesignTokens.TypeRole { DesignTokens.lookup(DesignTokens.shared.roles.typography, rawValue)! }

    public var size: CGFloat { CGFloat(role.size) }

    public var font: Font {
        let font = Font.system(size: size, weight: Self.weight(role.weight))
        return role.monospacedDigits == true ? font.monospacedDigit() : font
    }

    public var nsFont: NSFont {
        let font = NSFont.systemFont(ofSize: size, weight: Self.nsWeight(role.weight))
        return role.monospacedDigits == true ? NSFont.monospacedDigitSystemFont(ofSize: size, weight: Self.nsWeight(role.weight)) : font
    }

    static func weight(_ name: String) -> Font.Weight {
        switch name {
        case "bold": return .bold
        case "semibold": return .semibold
        case "medium": return .medium
        default: return .regular
        }
    }

    static func nsWeight(_ name: String) -> NSFont.Weight {
        switch name {
        case "bold": return .bold
        case "semibold": return .semibold
        case "medium": return .medium
        default: return .regular
        }
    }
}

public enum Space {
    static func value(_ key: String) -> CGFloat { CGFloat(DesignTokens.shared.roles.spacing[key] ?? 8) }
    public static var xxs: CGFloat { value("xxs") }
    public static var xs: CGFloat { value("xs") }
    public static var sm: CGFloat { value("sm") }
    public static var md: CGFloat { value("md") }
    public static var lg: CGFloat { value("lg") }
    public static var xl: CGFloat { value("xl") }
}

public enum Radius {
    static func value(_ key: String) -> CGFloat { CGFloat(DesignTokens.lookup(DesignTokens.shared.roles.radius, key) ?? 8) }
    public static var chip: CGFloat { value("chip") }
    public static var control: CGFloat { value("control") }
    public static var card: CGFloat { value("card") }
    public static var panel: CGFloat { value("panel") }
    public static var chart: CGFloat { value("chart") }
}

public enum Density {
    static func value(_ key: String) -> CGFloat { CGFloat(DesignTokens.lookup(DesignTokens.shared.roles.density, key) ?? 0) }
    public static var subjectRowMin: CGFloat { value("subjectRowMin") }
    public static var goalRowMin: CGFloat { value("goalRowMin") }
    public static var controlMin: CGFloat { value("controlMin") }
    public static var sidebarWidth: CGFloat { value("sidebarWidth") }
}

private struct ThemeKey: EnvironmentKey { static let defaultValue = Theme.fallback }

public extension EnvironmentValues {
    var theme: Theme {
        get { self[ThemeKey.self] }
        set { self[ThemeKey.self] = newValue }
    }
}
