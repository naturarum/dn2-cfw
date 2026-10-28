// SPDX-License-Identifier: GPL-2.0-or-later
// ContentView.swift - the one window, landscape and always dark: a header bar (the Digiwerk cog and wordmark; the
// BUILD and MANUAL pages; Help and "Back to Elektron's OS"), then on BUILD a headline, the base (the features in every
// build, each opening its manual entry), the four effect slots (Elektron's effect or ours in each, as tiles), the
// picked combination's status (its build and checks, or not built yet; the DSP code it takes), the three steps as
// panels side by side (01 get Elektron's file, 02 drop it, 03 send the result); on MANUAL the entries (ManualView);
// then a status bar (the result in plain English, the precise reason small under it) and a footer.
import AppKit
import SwiftUI

/// The window's colours: near-black, panels a little lighter, 1 px lines; white text; an accent for refusals and
/// what won't fit, a yellow for a build not yet played on a unit.
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
    static let caution = Color(red: 0.96, green: 0.78, blue: 0.26)   // not yet tested on a unit
}

struct ContentView: View {
    @ObservedObject var model: Model
    /// the height the view asks for: the app asks for less by the hidden title bar, which the view then fills too
    var ideal: CGFloat = ContentView.size.height
    @State private var hover = false                         // a file over the drop zone
    @State private var back = false                          // the "Back to Elektron's OS" popover

    static let size = CGSize(width: 1040, height: 760)

    var body: some View {
        VStack(spacing: 0) {
            header
            Palette.line.frame(height: 1)
            Group {
                if model.page == .build {
                    VStack(alignment: .leading, spacing: 14) {
                        hero
                        BaseStrip(model: model)
                        slots
                        PickStatus(model: model)
                        steps
                    }
                    .padding(.horizontal, 28)
                    .padding(.top, 16)
                    .padding(.bottom, 14)
                } else {
                    ManualView(model: model)
                }
            }
            .frame(maxHeight: .infinity)
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
            HStack(spacing: 4) {
                PageTab(title: "Build", on: model.page == .build) { model.page = .build }
                PageTab(title: "Manual", on: model.page == .manual) { model.page = .manual }
            }
            .padding(.trailing, 18)
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
            Text("Custom firmware for the Elektron Digitone II, made on your Mac from Elektron's own OS 1.12 file. "
                 + "Pick an effect for each slot, then go through 01 to 03.")
                .font(.system(size: 13))
                .foregroundStyle(Palette.grey)
                .frame(width: 400, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.bottom, 6)
        }
    }

    // MARK: the slots

    private var slots: some View {
        HStack(alignment: .top, spacing: 16) {
            ForEach(model.catalog.slots, id: \.id) { slot in
                VStack(alignment: .leading, spacing: 7) {
                    Text(slot.title).font(.system(size: 10.5, weight: .bold)).tracking(1.1).textCase(.uppercase)
                        .foregroundStyle(Palette.grey)
                    HStack(spacing: 8) {
                        ForEach([""] + slot.options, id: \.self) { option in
                            SlotTile(model: model, slot: slot, option: option)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
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
            StepPanel(number: "02", title: "Drop it here", done: model.made != nil, dim: model.pack == nil) {
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
            Text(model.pack == nil ? "This combination isn't built yet" : "Drop \(Core.zipName) here")
                .font(.system(size: 12, weight: .semibold))
            Text(model.pack == nil ? "Pick one with no tag on its tiles" : "or the \(Core.stockName) inside it")
                .font(.system(size: 10.5)).foregroundStyle(Palette.grey)
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
        model.pack == nil ? "Pick a combination that's built, then drop Elektron's file in 02."
            : "Drop Elektron's file in 02 to make \(model.outName)."
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

/// The base: the features every build carries, in one outlined bar; each opens its manual entry.
struct BaseStrip: View {
    @ObservedObject var model: Model

    var body: some View {
        let shown = model.catalog.baseShown(Array(Set(model.packs.flatMap(\.base))))
        HStack(spacing: 10) {
            Text("Base").font(.system(size: 10.5, weight: .bold)).tracking(1.1).textCase(.uppercase)
                .foregroundStyle(Palette.grey)
            Text("In every build").font(.system(size: 10.5, weight: .bold)).tracking(1.1).textCase(.uppercase)
                .foregroundStyle(Palette.dim)
                .padding(.trailing, 6)
            ForEach(Array(shown.enumerated()), id: \.element.id) { i, e in
                if i > 0 { Text("\u{00B7}").font(.system(size: 12, weight: .bold)).foregroundStyle(Palette.dim) }
                Chip(entry: e) { model.open(e.id) }
            }
            Spacer(minLength: 0)
            if shown.isEmpty {
                Text("Nothing extra").font(.system(size: 10.5, weight: .bold)).tracking(1.1).textCase(.uppercase)
                    .foregroundStyle(Palette.dim)
            }
        }
        .padding(.horizontal, 16)
        .frame(height: 36)
        .background(Palette.panel)
        .overlay(Rectangle().strokeBorder(Palette.line, lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Base, in every build")
    }

    /// one feature: its icon where it has one, its name; white, underlined on hover
    struct Chip: View {
        let entry: Catalog.Entry
        let action: () -> Void
        @State private var hover = false

        var body: some View {
            Button(action: action) {
                HStack(spacing: 6) {
                    if let icon = entry.icon { PixelArt(name: icon, pt: 1) }
                    Text(entry.label ?? entry.title).font(.system(size: 10.5, weight: .bold)).tracking(1.1)
                        .textCase(.uppercase).underline(hover)
                        .foregroundStyle(hover ? Palette.text : Palette.text.opacity(0.92))
                        .lineLimit(1).fixedSize()
                }
                .padding(.vertical, 6)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .onHover { hover = $0 }
            .help("\(entry.title): open its page in the manual")
            .accessibilityLabel("\(entry.title), open in the manual")
        }
    }
}

/// One option of a slot: Elektron's effect (an outlined ELEKTRON plate) or ours (its pixel icon), its name, and how
/// it stands: picked (white outline and a tick), built, not built yet (greyed), won't fit (dim, a tag, not pickable).
/// Ours have a ? that opens the manual.
struct SlotTile: View {
    @ObservedObject var model: Model
    let slot: Catalog.Slot, option: String
    @State private var hover = false
    static let height: CGFloat = 132

    var body: some View {
        let state = model.tile(slot, option)
        let entry = option.isEmpty ? nil : model.catalog.entry(option)
        let faint = state == .notBuilt || state == .wontFit
        Button { model.pick[slot.id] = option } label: {
            VStack(spacing: 0) {
                ZStack {
                    if let entry {
                        PixelArt(name: entry.icon ?? option, pt: 3, fg: faint ? NSColor(white: 0.36, alpha: 1) : Art.white)
                    } else {
                        Text("Elektron").font(.system(size: 9.5, weight: .bold)).tracking(1.2).textCase(.uppercase)
                            .foregroundStyle(faint ? Palette.dim : Palette.grey)
                            .padding(.horizontal, 10).padding(.vertical, 8)
                            .overlay(Rectangle().strokeBorder(faint ? Palette.line : Palette.dim, lineWidth: 1))
                    }
                }
                .frame(maxWidth: .infinity)
                .frame(height: 74)
                Text(entry?.label ?? slot.stock).font(.system(size: 10.5, weight: .bold)).tracking(1.1)
                    .textCase(.uppercase).lineLimit(1).minimumScaleFactor(0.8)
                    .foregroundStyle(state == .picked ? Palette.text : faint ? Palette.dim : Palette.grey)
                Group {
                    switch state {
                    case .wontFit:
                        Text("Won't fit").font(.system(size: 9, weight: .heavy)).tracking(1).textCase(.uppercase)
                            .foregroundStyle(Palette.accent)
                            .padding(.horizontal, 6).padding(.vertical, 3)
                            .background(Palette.accent.opacity(0.14))
                    case .notBuilt:
                        Text("Not built yet").font(.system(size: 9, weight: .heavy)).tracking(1).textCase(.uppercase)
                            .foregroundStyle(Palette.dim)
                            .padding(.horizontal, 6).padding(.vertical, 3)
                            .overlay(Rectangle().strokeBorder(Palette.line, lineWidth: 1))
                    default:
                        Color.clear.frame(height: 1)
                    }
                }
                .frame(height: 22)
                Spacer(minLength: 0)
            }
            .padding(.top, 8)
            .frame(maxWidth: .infinity)
            .frame(height: Self.height)
            .background(state == .picked ? Palette.panel : Palette.page)
            .overlay(alignment: .bottomLeading) {
                if state == .picked { PixelArt(Glyph.tick, pt: 1.5).padding(9) }
            }
            .overlay(Rectangle().strokeBorder(state == .picked ? Palette.text : hover && state != .wontFit
                                              ? Palette.dim : Palette.line, lineWidth: state == .picked ? 1.5 : 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(state == .wontFit || model.busy)
        .overlay(alignment: .topTrailing) {
            if let entry {
                InfoButton { model.open(entry.id) }
                    .help("\(entry.title): open its page in the manual")
                    .padding(4)
            }
        }
        .onHover { hover = $0 }
        .help(help(state, entry))
        .accessibilityLabel("\(slot.title): \(entry?.title ?? "Elektron's \(slot.stock.lowercased())")")
        .accessibilityValue(help(state, entry))
        .accessibilityAddTraits(state == .picked ? .isSelected : [])
    }

    private func help(_ state: Model.Tile, _ entry: Catalog.Entry?) -> String {
        switch state {
        case .picked: return "Picked"
        case .built: return "Built and checked with the other slots as picked"
        case .notBuilt: return "Fits with the other slots as picked, but that combination isn't built yet"
        case .wontFit: return "With the other slots as picked, this needs more DSP memory than there is"
        }
    }
}

/// A small ? in a tile's corner: grey, white on hover.
struct InfoButton: View {
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            PixelArt(Glyph.help, pt: 1.5, fg: hover ? Art.white : NSColor(white: 0.45, alpha: 1))
                .padding(5)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .accessibilityLabel("Open in the manual")
    }
}

/// The picked combination: its build's id, its effects, its checks (or not built yet), and the DSP code it takes
/// against the room, with what wouldn't fit next to it.
struct PickStatus: View {
    @ObservedObject var model: Model

    var body: some View {
        let c = model.effects
        let budget = model.catalog.budget(for: c)
        let room = model.catalog.budget?.room ?? 0
        HStack(alignment: .center, spacing: 24) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(model.pack?.id ?? "Not built yet").font(.system(size: model.pack == nil ? 13 : 17,
                                                                          weight: .heavy))
                        .tracking(model.pack == nil ? 0.6 : -0.3).textCase(model.pack == nil ? .uppercase : nil)
                        .foregroundStyle(model.pack == nil ? Palette.grey : Palette.text)
                    Text(model.catalog.name(of: c)).font(.system(size: 15, weight: .semibold)).lineLimit(1)
                }
                HStack(spacing: 14) {
                    if let p = model.pack {
                        let parts = p.record.components(separatedBy: " \u{00B7} ")
                        Text(parts.count == 2 ? parts[0].replacingOccurrences(of: " of ", with: "/") : p.record)
                            .foregroundStyle(Palette.grey)
                        if parts.count == 2 {
                            Text(parts[1]).foregroundStyle(p.record.contains("not yet") ? Palette.caution : Palette.text)
                        }
                    } else if budget?.fits == false {
                        Text("Won't fit: more DSP code than there is room for").foregroundStyle(Palette.accent)
                    } else {
                        Text("It fits; we build and check each combination before the patcher offers it")
                            .foregroundStyle(Palette.grey)
                    }
                }
                .font(.system(size: 9.5, weight: .bold)).tracking(1).textCase(.uppercase).lineLimit(1)
            }
            Spacer(minLength: 0)
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline) {
                    Text("DSP code").font(.system(size: 9.5, weight: .bold)).tracking(1).textCase(.uppercase)
                        .foregroundStyle(Palette.grey)
                    Spacer()
                    Text(budget.map { "\(kb($0.code)) of \(kb(room)) KB" } ?? "not measured")
                        .font(.system(size: 13, weight: .semibold)).monospacedDigit()
                }
                GeometryReader { g in
                    let f = room > 0 ? min(1, Double(budget?.code ?? 0) / Double(room)) : 0
                    ZStack(alignment: .leading) {
                        Palette.well
                        (budget?.fits == false ? Palette.accent : Palette.text).frame(width: g.size.width * f)
                    }
                    .overlay(Rectangle().strokeBorder(Palette.dim, lineWidth: 1))
                }
                .frame(height: 9)
                Text(note(room)).font(.system(size: 9, weight: .bold)).tracking(0.9).textCase(.uppercase)
                    .foregroundStyle(Palette.dim).lineLimit(1)
            }
            .frame(width: 330)
        }
        .padding(.horizontal, 18)
        .frame(height: 64)
        .background(Palette.panel)
        .overlay(Rectangle().strokeBorder(Palette.line, lineWidth: 1))
        .help(model.pack?.description ?? "")
    }

    private func kb(_ b: Int) -> String { String(format: "%.1f", Double(b) / 1000) }

    /// what wouldn't fit with this pick, or the room left
    private func note(_ room: Int) -> String {
        for s in model.catalog.slots {
            for o in s.options where model.tile(s, o) == .wontFit {
                var p = model.pick
                p[s.id] = o
                let name = model.catalog.entry(o)?.name ?? o
                if let b = model.catalog.budget(for: model.combo(p)) {
                    return "With the \(name) \(model.pick[s.id, default: ""].isEmpty ? "too" : "instead"): "
                        + "\(kb(b.code)) KB, won't fit"
                }
            }
        }
        guard let b = model.catalog.budget(for: model.effects), b.fits else { return "" }
        return "\(kb(room - b.code)) KB left"
    }
}

/// BUILD or MANUAL in the header bar: capitals, white with a line under it when on.
struct PageTab: View {
    let title: String, on: Bool
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            VStack(spacing: 4) {
                Text(title).font(.system(size: 10.5, weight: .bold)).tracking(1.1).textCase(.uppercase)
                    .foregroundStyle(on || hover ? Palette.text : Palette.grey)
                (on ? Palette.text : Color.clear).frame(height: 1.5)
            }
            .fixedSize()
            .padding(.horizontal, 8)
            .padding(.top, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .accessibilityAddTraits(on ? .isSelected : [])
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
