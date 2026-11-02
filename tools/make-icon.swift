#!/usr/bin/env swift
// Genera l'icona dell'app (iconset) e la converte in .icns.
// Uso: swift tools/make-icon.swift <output.iconset>

import AppKit
import Foundation

let outputPath = CommandLine.arguments.count > 1
    ? CommandLine.arguments[1]
    : "build/LFM3Control.iconset"

let fileManager = FileManager.default
try? fileManager.createDirectory(atPath: outputPath, withIntermediateDirectories: true)

/// Disegna l'icona master a 1024x1024.
func drawIcon(size: CGFloat) -> NSImage {
    let image = NSImage(size: NSSize(width: size, height: size))
    image.lockFocus()
    defer { image.unlockFocus() }

    let rect = NSRect(x: 0, y: 0, width: size, height: size)
    let inset = size * 0.06
    let body = rect.insetBy(dx: inset, dy: inset)
    let radius = size * 0.22

    // Sfondo: gradiente viola -> ciano.
    let gradient = NSGradient(colors: [
        NSColor(srgbRed: 0.42, green: 0.36, blue: 0.91, alpha: 1.0),
        NSColor(srgbRed: 0.10, green: 0.55, blue: 0.85, alpha: 1.0),
    ])!
    let path = NSBezierPath(roundedRect: body, xRadius: radius, yRadius: radius)
    gradient.draw(in: path, angle: -60)

    // Bordo chiaro.
    NSColor.white.withAlphaComponent(0.18).setStroke()
    path.lineWidth = size * 0.012
    path.stroke()

    // Simbolo: fulmine (SB Symbol) al centro, colorato di bianco.
    if let symbol = NSImage(systemSymbolName: "bolt.fill", accessibilityDescription: nil) {
        let palette = NSImage.SymbolConfiguration(paletteColors: [NSColor.white])
        let configuration = NSImage.SymbolConfiguration(pointSize: size * 0.46, weight: .bold)
            .applying(palette)
        let configured = symbol.withSymbolConfiguration(configuration) ?? symbol
        configured.isTemplate = false
        let symbolSize = configured.size
        let target = NSSize(width: size * 0.34, height: size * 0.34)
        let scale = min(target.width / symbolSize.width, target.height / symbolSize.height)
        let drawSize = NSSize(width: symbolSize.width * scale, height: symbolSize.height * scale)
        let origin = NSPoint(
            x: body.midX - drawSize.width / 2,
            y: body.midY - drawSize.height / 2 + size * 0.03
        )
        configured.draw(
            in: NSRect(origin: origin, size: drawSize),
            from: .zero,
            operation: .sourceOver,
            fraction: 1.0
        )
    }

    // Etichetta "MLX" in basso.
    let label = "MLX" as NSString
    let attributes: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: size * 0.13, weight: .heavy),
        .foregroundColor: NSColor.white.withAlphaComponent(0.92),
        .kern: size * 0.012,
    ]
    let textSize = label.size(withAttributes: attributes)
    label.draw(
        at: NSPoint(x: body.midX - textSize.width / 2, y: body.minY + size * 0.15),
        withAttributes: attributes
    )

    return image
}

func png(from image: NSImage, size: CGFloat) -> Data? {
    guard let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: Int(size),
        pixelsHigh: Int(size),
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bytesPerRow: 0,
        bitsPerPixel: 0
    ) else { return nil }
    rep.size = NSSize(width: size, height: size)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    image.draw(in: NSRect(x: 0, y: 0, width: size, height: size))
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])
}

let sizes: [(Int, String)] = [
    (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
]

for (size, name) in sizes {
    let image = drawIcon(size: CGFloat(size))
    guard let data = png(from: image, size: CGFloat(size)) else { continue }
    try? data.write(to: URL(fileURLWithPath: outputPath).appendingPathComponent(name))
}

print("iconset generato in \(outputPath)")
