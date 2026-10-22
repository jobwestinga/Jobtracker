import AppKit
import CoreImage
import CoreText
import SwiftUI

/// The restored v2 hour hierarchy: continuous size scale, intensity tiers and
/// strong tiers with a coloured halo (red above 10 h). Values come from
/// design/ui_roles.json, ported exactly from `graphs_view._intensity_style`.
public struct HourStyle: Equatable {
    public let color: RGBA
    public let scale: Double
    public let strong: Bool
    public let peak: Bool

    public static func forIntensity(seconds: Double) -> HourStyle {
        let tiers = DesignTokens.shared.roles.hourTiers
        let hours = max(0, seconds) / 3600
        let scale = tiers.scale.base + min(hours, tiers.scale.capHours) / tiers.scale.divisor
        let tier = tiers.tiers.first { $0.maxHours.map { hours <= $0 } ?? true } ?? tiers.tiers.last!
        return HourStyle(color: RGBA(hex: tier.color), scale: scale, strong: tier.strong, peak: hours > tiers.bloom.peakAboveHours)
    }

    public static func label(totalSeconds: Int) -> String { String(format: "%.1fh", Double(totalSeconds) / 3600) }
}

/// Measures and draws chart text with the system font so the cached bloom mask
/// and the crisp glyphs share exactly the same layout.
public enum ChartText {
    public static func font(size: CGFloat, strong: Bool) -> NSFont {
        NSFont.systemFont(ofSize: size, weight: strong ? .bold : .semibold)
    }

    public static func line(_ text: String, font: NSFont, color: CGColor) -> CTLine {
        let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: color]
        return CTLineCreateWithAttributedString(NSAttributedString(string: text, attributes: attributes))
    }

    /// Typographic width and ascent/descent of one line.
    public static func metrics(_ text: String, font: NSFont) -> (width: CGFloat, ascent: CGFloat, descent: CGFloat) {
        let line = line(text, font: font, color: CGColor(gray: 1, alpha: 1))
        var ascent: CGFloat = 0, descent: CGFloat = 0, leading: CGFloat = 0
        let width = CGFloat(CTLineGetTypographicBounds(line, &ascent, &descent, &leading))
        return (width, ascent, descent)
    }

    /// Draw a line with its top-left at `origin` in a top-left (flipped) context.
    public static func draw(_ text: String, font: NSFont, color: CGColor, at origin: CGPoint, in context: CGContext) {
        let line = line(text, font: font, color: color)
        var ascent: CGFloat = 0, descent: CGFloat = 0, leading: CGFloat = 0
        _ = CTLineGetTypographicBounds(line, &ascent, &descent, &leading)
        context.saveGState()
        context.textMatrix = .identity
        context.translateBy(x: origin.x, y: origin.y + ascent)
        context.scaleBy(x: 1, y: -1)
        context.textPosition = .zero
        CTLineDraw(line, context)
        context.restoreGState()
    }
}

/// R1 soft bloom: a blurred colour mask of the label, cached by text, font,
/// colour, radius and display scale, drawn beneath crisp foreground glyphs.
public final class BloomCache {
    public static let shared = BloomCache()

    public struct Key: Hashable {
        let text: String
        let fontSize: CGFloat
        let strong: Bool
        let color: RGBA
        let radius: CGFloat
        let scale: CGFloat
    }

    public struct Mask {
        public let image: CGImage
        /// Offset of the mask's top-left from the text's top-left, in points.
        public let offset: CGPoint
        public let size: CGSize
    }

    private var cache: [Key: Mask] = [:]
    private let context = CIContext(options: [.useSoftwareRenderer: false])
    public private(set) var renders = 0

    public func mask(for key: Key) -> Mask? {
        if let cached = cache[key] { return cached }
        let font = ChartText.font(size: key.fontSize, strong: key.strong)
        let metrics = ChartText.metrics(key.text, font: font)
        let pad = ceil(key.radius * 3)
        let size = CGSize(width: ceil(metrics.width) + pad * 2, height: ceil(metrics.ascent + metrics.descent) + pad * 2)
        let pixelsWide = Int(ceil(size.width * key.scale)), pixelsHigh = Int(ceil(size.height * key.scale))
        guard pixelsWide > 0, pixelsHigh > 0,
              let bitmap = CGContext(data: nil, width: pixelsWide, height: pixelsHigh, bitsPerComponent: 8, bytesPerRow: 0,
                                     space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
        else { return nil }
        bitmap.scaleBy(x: key.scale, y: key.scale)
        // Draw into a top-left coordinate space so the mask matches ChartText.draw.
        bitmap.translateBy(x: 0, y: size.height)
        bitmap.scaleBy(x: 1, y: -1)
        ChartText.draw(key.text, font: font, color: key.color.cgColor, at: CGPoint(x: pad, y: pad), in: bitmap)
        guard let sharp = bitmap.makeImage() else { return nil }
        let input = CIImage(cgImage: sharp)
        let blurred = input.clampedToExtent().applyingGaussianBlur(sigma: Double(key.radius * key.scale)).cropped(to: input.extent)
        guard let image = context.createCGImage(blurred, from: input.extent) else { return nil }
        renders += 1
        let mask = Mask(image: image, offset: CGPoint(x: -pad, y: -pad), size: size)
        if cache.count > 512 { cache.removeAll() }
        cache[key] = mask
        return mask
    }
}
