// SPDX-License-Identifier: GPL-2.0-or-later
// ContentView.swift - the one window, landscape and always dark: a header bar (the Digiwerk cog and wordmark; Help
// and "Back to Elektron's OS"), a headline, the packs as cards in a row (their effects' pixel icons on top, a
// caption bar under), the three steps as panels side by side (01 get Elektron's file, 02 drop it, 03 send the
// result), a status bar (the result in plain English, the precise reason small under it) and a footer.
import AppKit
import SwiftUI

/// The window's colours: near-black, panels a little lighter, 1 px lines; white text; one accent, for refusals only.
enum Palette {
    static let page = Color(white: 0x14 / 255.0)
    static let panel = Color(white: 0x1b / 255.0)
    static let well = Color(white: 0x10 / 255.0)
    static let line = Color(white: 0x2a / 255.0)
    static let dim = Color(white: 0x55 / 255.0)
    static let grey = Color(white: 0x8e / 255.0)
    static let text = Color(white: 0xf2 / 255.0)
    static let accent = Color(red: 1, green: 0.47, blue: 0.12)       // warm orange
    static let accentNS = NSColor(red: 1, green: 0.47, blue: 0.12, alpha: 1)
}

struct ContentView: View {
    @ObservedObject var model: Model
    /// the height the view asks for: the app asks for less by the hidden title bar, which the view then fills too
    var ideal: CGFloat = ContentView.size.height
    @State private var hover = false                         // a file over the drop zone
    @State private var back = false                          // the "Back to Elektron's OS" popover

    static let size = CGSize(width: 1040, height: 640)

    var body: some View {
        VStack(spacing: 0) {
            header
            Palette.line.frame(height: 1)
            VStack(alignment: .leading, spacing: 16) {
                hero
                cards
                steps
            }
            .padding(.horizontal, 28)
            .padding(.top, 18)
            .padding(.bottom, 14)
            Palette.line.frame(height: 1)
            status
            Palette.line.frame(height: 1)
            footer
        }
        .frame(width: Self.size.width)
        .frame(minHeight: ideal, idealHeight: ideal, maxHeight: .infinity)
        .background(Palette.page)
        .foregroundStyle(Palette.text)
        .environment(\.colorScheme, .dark)
    }

    // MARK: the header bar

    private var header: some View {
        HStack(spacing: 14) {
            PixelArt(name: "mark", pt: 2.5)
            PixelArt(name: "wordmark", pt: 3)
            Spacer()
            NavLink(glyph: Glyph.help, title: "Help") { NSWorkspace.shared.open(Core.guide) }
            NavLink(glyph: Glyph.back, title: "Back to Elektron's OS") { back.toggle() }
                .popover(isPresented: $back, arrowEdge: .bottom) { BackToStock() }
        }
        .padding(.leading, 84)                               // the window's buttons sit at the left of this bar
        .padding(.trailing, 28)
        .frame(height: 52)
    }

    // MARK: the headline

    private var hero: some View {
        HStack(alignment: .bottom, spacing: 32) {
            VStack(alignment: .leading, spacing: 8) {
                PixelArt(Glyph.marker, pt: 3)
                Text("Make your Digiwerk.")
                    .font(.system(size: 40, weight: .black))
                    .tracking(-1.4)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
            Text(model.several
                 ? "Custom firmware for the Elektron Digitone II, made on your Mac from Elektron's own OS 1.12 file. "
                    + "Pick a build, then go through 01 to 03."
                 : "Custom firmware for the Elektron Digitone II. This makes \(model.outName) on your Mac from "
                    + "Elektron's own OS 1.12 file: go through 01 to 03.")
                .font(.system(size: 13))
                .foregroundStyle(Palette.grey)
                .frame(width: 400, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.bottom, 6)
        }
    }

    // MARK: the packs

    private var cards: some View {
        let w = (Self.size.width - 56 - 24) / 3
        return ScrollView(.horizontal, showsIndicators: model.packs.count > 3) {
            HStack(spacing: 12) {
                ForEach(model.packs.indices, id: \.self) { i in
                    PackCard(pack: model.packs[i], on: i == model.selected, width: w) { model.selected = i }
                        .disabled(model.busy)
                }
                if model.packs.count == 1 {                  // room to say what the card's record means
                    about.frame(width: Self.size.width - 56 - w - 12, height: PackCard.height)
                }
            }
        }
        .frame(height: PackCard.height)
    }

    private var about: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                PixelArt(Glyph.marker, pt: 2, fg: NSColor(white: 0.6, alpha: 1))
                Text("What the card says").font(.system(size: 11.5, weight: .bold)).tracking(1.1).textCase(.uppercase)
            }
            Text("BRICK CHECK 12/12: the build passed all twelve checks of our brick checker. In an emulator of the "
                + "Digitone II it starts up to the main screen and writes nothing to the unit's memory that it shouldn't.")
            Text("TESTED ON A DIGITONE II: we also ran it on a real unit, on that date. NOT YET TESTED ON A UNIT: only "
                + "the brick checker has run it so far.")
            Text("The big icons are its effects: "
                + (model.pack?.effects.map { (Art.effectNames[$0] ?? $0).lowercased() } ?? [])
                    .joined(separator: " and ") + ". BASE is what every build of this release carries in the "
                + "main OS: " + (model.pack.map { $0.base.isEmpty ? "nothing extra" : $0.base
                    .map { Art.baseNames[$0] ?? $0 }.joined(separator: " and ") } ?? "") + ".")
        }
        .font(.system(size: 12))
        .foregroundStyle(Palette.grey)
        .padding(16)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .overlay(Rectangle().strokeBorder(Palette.line, lineWidth: 1))
    }

    // MARK: the three steps

    private var steps: some View {
        HStack(spacing: 1) {
            StepPanel(number: "01", title: "Get Elektron's OS 1.12", done: model.checked) {
                Text("On Elektron's Digitone II page, download \u{201C}Digitone II OS 1.12\u{201D}. It arrives as "
                    + "\(Core.zipName). There's no need to unzip it.")
                    .font(.system(size: 12)).foregroundStyle(Palette.grey).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                Button("Open download page") { NSWorkspace.shared.open(Core.elektronPage) }
                    .buttonStyle(RectButton())
            }
            StepPanel(number: "02", title: model.several ? "Drop it here" : "Drop it here", done: model.made != nil) {
                dropZone
            }
            StepPanel(number: "03", title: "Send it to your Digitone", done: false, dim: model.made == nil) {
                VStack(alignment: .leading, spacing: 5) {
                    ForEach(Array(sendSteps.enumerated()), id: \.offset) { n, s in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text("\(n + 1)").font(.system(size: 11, weight: .heavy)).monospacedDigit()
                            Text(s).font(.system(size: 12)).foregroundStyle(Palette.grey)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                Spacer(minLength: 0)
                HStack(spacing: 10) {
                    Button("Show in Finder") {
                        if let f = model.made { NSWorkspace.shared.activateFileViewerSelecting([f]) }
                    }
                    .buttonStyle(RectButton(primary: true))
                    if model.transfer != nil {
                        Button("Open Transfer") { model.openTransfer() }.buttonStyle(RectButton())
                    }
                }
                .disabled(model.made == nil)
            }
        }
        .background(Palette.line)
        .overlay(Rectangle().strokeBorder(Palette.line, lineWidth: 1))
        .frame(maxHeight: .infinity)
    }

    private var sendSteps: [String] {
        ["Back up your projects with Transfer first.",
         "Connect over USB and open Elektron Transfer.",
         "Drag \(model.outName) onto \u{201C}Drop files here\u{201D}.",
         "Answer YES, wait for SUCCESS! REBOOTING."]
    }

    private var dropZone: some View {
        VStack(spacing: 7) {
            PixelArt(Glyph.drop, pt: 3, fg: hover ? Art.white : NSColor(white: 0.6, alpha: 1))
            Text(model.several && model.selected == nil ? "Pick a build above, then drop the file here"
                 : "Drop \(Core.zipName) here")
                .font(.system(size: 12, weight: .semibold))
            Text("or the \(Core.stockName) inside it").font(.system(size: 10.5)).foregroundStyle(Palette.grey)
            Button("Choose file") { model.choose() }.buttonStyle(RectButton()).disabled(model.busy).padding(.top, 2)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(hover ? Palette.text.opacity(0.06) : Palette.well)
        .overlay(Rectangle().strokeBorder(hover ? Palette.text : Palette.dim,
                                          style: StrokeStyle(lineWidth: 1, dash: hover ? [] : [4, 3])))
        .contentShape(Rectangle())
        .dropDestination(for: URL.self) { urls, _ in
            guard let url = urls.first, !model.busy else { return false }
            model.process(url)
            return true
        } isTargeted: { hover = $0 }
    }

    // MARK: the status bar and the footer

    private var status: some View {
        HStack(alignment: .center, spacing: 14) {
            Group {
                if model.busy {
                    ProgressView().controlSize(.small)
                } else if model.failed {
                    PixelArt(Glyph.bang, pt: 2, fg: Palette.accentNS)
                } else if model.made != nil {
                    PixelArt(Glyph.tick, pt: 2)
                } else if !model.line.isEmpty {
                    PixelArt(Glyph.up, pt: 2, fg: NSColor(white: 0.6, alpha: 1))
                } else {
                    PixelArt(Glyph.marker, pt: 2, fg: NSColor(white: 0.35, alpha: 1))
                }
            }
            .frame(width: 20)
            VStack(alignment: .leading, spacing: 3) {
                Text(model.line.isEmpty ? idle : model.line.replacingOccurrences(of: "\n", with: " "))
                    .font(.system(size: 13, weight: model.line.isEmpty ? .regular : .medium))
                    .foregroundStyle(model.failed ? Palette.accent : model.line.isEmpty ? Palette.grey : Palette.text)
                    .lineLimit(2)
                if !model.detail.isEmpty {
                    Text(model.detail).font(.system(size: 10.5, design: .monospaced)).foregroundStyle(Palette.grey)
                        .lineLimit(2).textSelection(.enabled)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 28)
        .frame(height: 50)
    }

    private var idle: String {
        model.several ? "Pick a build, then drop Elektron's file in 02." : "Drop Elektron's file in 02."
    }

    private var footer: some View {
        HStack {
            Text("Not made or supported by Elektron.")
            Spacer()
            Text("Digiwerk \u{00B7} custom firmware for the Digitone II \u{00B7} GPL-2.0-or-later")
        }
        .font(.system(size: 10))
        .foregroundStyle(Palette.dim)
        .padding(.horizontal, 28)
        .frame(height: 26)
    }
}

/// One pack as a product card: its effects' icons big on a dark well, a caption bar with its id, title, description
/// and record line. The chosen one has a white outline and a filled square.
struct PackCard: View {
    static let height: CGFloat = 182
    let pack: Pack, on: Bool, width: CGFloat
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        let parts = pack.record.components(separatedBy: " \u{00B7} ")
        let badge = parts.count == 2 ? parts[0].replacingOccurrences(of: " of ", with: "/") : ""
        let tested = parts.count == 2 ? parts[1] : pack.record
        let pt: CGFloat = CGFloat(pack.effects.count) * 26 * 3 + CGFloat(max(0, pack.effects.count - 1)) * 10
            <= width - 24 ? 3 : 2
        Button(action: action) {
            VStack(spacing: 0) {
                ZStack(alignment: .topTrailing) {
                    HStack(alignment: .bottom, spacing: 10) {
                        ForEach(pack.effects, id: \.self) { fx in
                            VStack(spacing: 2) {
                                PixelArt(name: fx, pt: pt)
                                Text(Art.effectLabels[fx] ?? fx.uppercased())
                                    .font(.system(size: 8.5, weight: .bold)).tracking(1).foregroundStyle(Palette.grey)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    Rectangle().fill(on ? Palette.text : Color.clear)
                        .overlay(Rectangle().strokeBorder(on ? Palette.text : Palette.dim, lineWidth: 1))
                        .frame(width: 9, height: 9)
                        .padding(10)
                }
                .frame(height: 70)
                .background(Palette.well)
                Palette.line.frame(height: 1)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(pack.id).font(.system(size: 16, weight: .heavy)).tracking(-0.3)
                        Spacer()
                        Text(badge.uppercased()).font(.system(size: 9, weight: .bold)).tracking(0.8)
                            .foregroundStyle(on ? Palette.text : Palette.grey)
                    }
                    Text(pack.title).font(.system(size: 12.5, weight: .semibold)).lineLimit(1)
                    Text(pack.description).font(.system(size: 10.5)).foregroundStyle(Palette.grey)
                        .lineLimit(2).fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                    if !pack.base.isEmpty { BaseLine(base: pack.base) }
                    Text(tested.uppercased()).font(.system(size: 8.5, weight: .bold)).tracking(0.8)
                        .foregroundStyle(Palette.dim).lineLimit(1)
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
            .frame(width: width, height: Self.height)
            .background(Palette.panel)
            .overlay(Rectangle().strokeBorder(on ? Palette.text : hover ? Palette.dim : Palette.line, lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help(pack.description)
        .accessibilityLabel("\(pack.id), \(pack.title). \(pack.description) \(pack.record)")
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

/// A pack's base features, small, in its caption bar: an icon where the feature has one (Déjà vu), a pixel glyph
/// for Overbridge, and their names.
struct BaseLine: View {
    let base: [String]

    var body: some View {
        HStack(spacing: 6) {
            Text("BASE").font(.system(size: 8.5, weight: .bold)).tracking(0.8).foregroundStyle(Palette.dim)
            ForEach(base, id: \.self) { id in
                HStack(spacing: 4) {
                    if Art.baseIcons.contains(id) {
                        PixelArt(name: id, pt: 1)
                    } else if id == "overbridge" {
                        PixelArt(Glyph.link, pt: 1, fg: NSColor(white: 0.75, alpha: 1))
                    }
                    Text((Art.baseNames[id] ?? id).uppercased()).font(.system(size: 8.5, weight: .bold)).tracking(0.8)
                        .foregroundStyle(Palette.grey).lineLimit(1)
                }
            }
        }
        .frame(height: 16)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Base: " + base.map { Art.baseNames[$0] ?? $0 }.joined(separator: ", "))
    }
}

/// One step as a panel: its big number, its title, a pixel tick once done; dimmed until it's the user's turn.
struct StepPanel<Content: View>: View {
    let number: String, title: String, done: Bool
    var dim = false
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(number).font(.system(size: 30, weight: .heavy)).monospacedDigit().tracking(-0.8)
                Text(title).font(.system(size: 11.5, weight: .bold)).tracking(1.1).textCase(.uppercase)
                Spacer(minLength: 0)
                if done { PixelArt(Glyph.tick, pt: 2) }
            }
            content
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Palette.panel)
        .opacity(dim ? 0.45 : 1)
    }
}

/// An outlined rectangle with small bold capitals; the primary one filled white with black text.
struct RectButton: ButtonStyle {
    var primary = false

    func makeBody(configuration: Configuration) -> some View { Face(configuration: configuration, primary: primary) }

    struct Face: View {
        let configuration: ButtonStyleConfiguration
        let primary: Bool
        @Environment(\.isEnabled) private var enabled
        @State private var hover = false

        var body: some View {
            let fill = configuration.isPressed ? 0.75 : hover ? 0.88 : 1.0
            let primary = self.primary && enabled            // a disabled primary is drawn outlined, readable
            configuration.label
                .font(.system(size: 10.5, weight: .bold))
                .tracking(1.2)
                .textCase(.uppercase)
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .foregroundStyle(primary ? Color.black : Palette.text)
                .background(primary ? Palette.text.opacity(fill)
                            : Palette.text.opacity(configuration.isPressed ? 0.16 : hover ? 0.07 : 0))
                .overlay(Rectangle().strokeBorder(Palette.text, lineWidth: 1))
                .contentShape(Rectangle())
                .opacity(enabled ? 1 : 0.35)
                .onHover { hover = $0 }
        }
    }
}

/// A small link in the header bar: a pixel glyph and capitals, grey, white on hover.
struct NavLink: View {
    let glyph: Art.Grid, title: String
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 7) {
                PixelArt(glyph, pt: 1.5, fg: hover ? Art.white : NSColor(white: 0.62, alpha: 1))
                Text(title).font(.system(size: 10.5, weight: .bold)).tracking(1.1).textCase(.uppercase)
                    .foregroundStyle(hover ? Palette.text : Palette.grey)
            }
            .padding(.vertical, 6)
            .padding(.horizontal, 4)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
    }
}

/// How to go back to Elektron's OS (the popover from the header bar).
struct BackToStock: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                PixelArt(Glyph.marker, pt: 2)
                Text("Back to Elektron's OS").font(.system(size: 12, weight: .bold)).tracking(1).textCase(.uppercase)
            }
            Text("Send Elektron's \(Core.stockName) to the Digitone the same way, with Transfer. It's inside "
                + "\(Core.zipName): double-click the .zip to get it.")
            Text("If the Digitone doesn't start: hold FUNC while you switch it on, press TRIG 4 (OS UPGRADE), then send "
                + "Elektron's .syx with Transfer over a DIN MIDI cable. The startup menu has no USB.")
            Button("Open the guide") { NSWorkspace.shared.open(Core.guideStartup) }.buttonStyle(RectButton())
        }
        .font(.system(size: 12))
        .foregroundStyle(Palette.text)
        .fixedSize(horizontal: false, vertical: true)
        .padding(18)
        .frame(width: 380)
        .background(Palette.panel)
        .environment(\.colorScheme, .dark)
    }
}
