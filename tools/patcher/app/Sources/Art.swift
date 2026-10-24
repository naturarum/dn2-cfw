// SPDX-License-Identifier: GPL-2.0-or-later
// Art.swift - Digiwerk's pixel identity in the window: the mark, the wordmark and one icon per effect, from
// docs/identity/NAME.svg (made by its make_icons.py; build.sh copies it into Resources/identity: one 1x1 rect per lit
// pixel inside a one-pixel border), and the window's own small glyphs (the section marker, the tick, the arrows),
// drawn here as text rows. Each is drawn as a bitmap with a whole number of device pixels per art pixel and shown
// without interpolation, so the pixels stay crisp at any display scale.
import AppKit
import SwiftUI

enum Art {
    /// where the SVGs are (the app's Resources/identity; app/screens.sh points it at the staged copy)
    static var dir = Bundle.main.resourceURL?.appendingPathComponent("identity")
    static let effectNames = ["cmp3": "3-band compressor", "flng": "Flanger", "tape": "Tape echo",
                              "spng": "Spring reverb"]                 // mkpatch.py's EFFECTS
    static let effectLabels = ["cmp3": "COMPRESSOR", "flng": "FLANGER", "tape": "TAPE ECHO", "spng": "SPRING"]
    static let baseNames = ["djvu": "D\u{E9}j\u{E0} vu LFO", "overbridge": "Overbridge works"]   // mkpatch.py's BASE
    static let baseIcons: Set<String> = ["djvu"]             // the base features with an icon in docs/identity
    static let white = NSColor(white: 0.95, alpha: 1)
    private static var cache: [String: Grid] = [:]

    /// pixels: w x h cells, the lit ones
    struct Grid {
        let w, h: Int
        let on: [(Int, Int)]

        init(w: Int, h: Int, on: [(Int, Int)]) {
            self.w = w
            self.h = h
            self.on = on
        }

        /// from rows of text, "#" lit
        init(_ rows: [String]) {
            h = rows.count
            w = rows.map(\.count).max() ?? 0
            on = rows.enumerated().flatMap { y, r in r.enumerated().compactMap { x, c in c == "#" ? (x, y) : nil } }
        }
    }

    static func grid(_ name: String) -> Grid? {
        if let g = cache[name] { return g }
        guard let dir, let svg = try? String(contentsOf: dir.appendingPathComponent(name + ".svg"), encoding: .utf8),
              let size = number(svg[...], "viewBox=\"-1 -1 ", count: 2), size.count == 2,
              let lit = svg.range(of: "<g fill=\"#f2f2f2\">") else { return nil }
        let on = svg[lit.upperBound...].components(separatedBy: "<rect ").dropFirst().compactMap { r -> (Int, Int)? in
            guard let x = number(r[...], "x=\"")?.first, let y = number(r[...], "y=\"")?.first else { return nil }
            return (x + 1, y + 1)                            // + the border
        }
        let g = Grid(w: size[0], h: size[1], on: on)
        cache[name] = g
        return g
    }

    /// the integers after `key` in s, up to the next quote: "3" -> [3], "26 18" -> [26, 18]
    private static func number(_ s: Substring, _ key: String, count: Int = 1) -> [Int]? {
        guard let r = s.range(of: key) else { return nil }
        let v = s[r.upperBound...].prefix { $0 != "\"" }.split(separator: " ").compactMap { Int($0) }
        return v.count == count ? v : nil
    }

    /// the grid as a bitmap, cell device pixels per art pixel; bg nil: transparent
    static func image(_ g: Grid, cell: Int, fg: NSColor, bg: NSColor?) -> NSImage {
        let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: max(1, g.w * cell), pixelsHigh: max(1, g.h * cell),
                                   bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                   colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        NSGraphicsContext.current?.shouldAntialias = false
        if let bg {
            bg.setFill()
            NSRect(x: 0, y: 0, width: g.w * cell, height: g.h * cell).fill()
        }
        fg.setFill()
        for (x, y) in g.on {                                 // the art's y runs down; the bitmap's up
            NSRect(x: x * cell, y: (g.h - 1 - y) * cell, width: cell, height: cell).fill()
        }
        NSGraphicsContext.restoreGraphicsState()
        let img = NSImage(size: NSSize(width: g.w * cell, height: g.h * cell))
        img.addRepresentation(rep)
        return img
    }
}

/// The window's own glyphs (ours, drawn for this window).
enum Glyph {
    static let marker = Art.Grid(["##.##.##", "##.##.##"])                   // the section marker: three blocks
    static let tick = Art.Grid([".......#", "......#.", ".....#..", "#...#...", ".#.#....", "..#....."])
    static let bang = Art.Grid(["##", "##", "##", "##", "..", "##"])
    static let up = Art.Grid(["...#...", "..###..", ".#.#.#.", "...#...", "...#...", "...#..."])
    static let drop = Art.Grid(["...#...", "...#...", "...#...", ".#.#.#.", "..###..", "...#...", ".......",
                                "#######"])
    static let help = Art.Grid([".###.", "#...#", "....#", "..##.", "..#..", ".....", "..#.."])
    static let back = Art.Grid(["..#....", ".#.....", "#######", ".#.....", "..#...."])
    static let link = Art.Grid(["###...###", "#.#####.#", "###...###"])        // Overbridge: two boxes, linked
}

/// One piece of pixel art, about `pt` points per art pixel (rounded to whole device pixels), white on bg.
struct PixelArt: View {
    let grid: Art.Grid?
    var pt: CGFloat = 1.5
    var fg: NSColor = Art.white
    var bg: NSColor? = nil
    var label: String? = nil
    @Environment(\.displayScale) private var scale

    init(name: String, pt: CGFloat = 1.5, fg: NSColor = Art.white, bg: NSColor? = nil) {
        self.grid = Art.grid(name)
        self.pt = pt
        self.fg = fg
        self.bg = bg
        self.label = Art.effectNames[name]
    }

    init(_ grid: Art.Grid, pt: CGFloat = 1.5, fg: NSColor = Art.white, bg: NSColor? = nil) {
        self.grid = grid
        self.pt = pt
        self.fg = fg
        self.bg = bg
    }

    var body: some View {
        if let g = grid {
            let cell = max(1, Int((pt * scale).rounded()))
            Image(nsImage: Art.image(g, cell: cell, fg: fg, bg: bg))
                .interpolation(.none)
                .resizable()
                .frame(width: CGFloat(g.w * cell) / scale, height: CGFloat(g.h * cell) / scale)
                .help(label ?? "")
                .accessibilityLabel(label ?? "")
        }
    }
}
