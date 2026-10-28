// SPDX-License-Identifier: GPL-2.0-or-later
// snap/main.swift - app/screens.sh's driver: the window's own view (ContentView) and Model, rendered offscreen to PNG
// (AppKit's cacheDisplay; no screen capture), after real runs, for one set of packs (with catalog.json and
// dsp-budget.json, as build.sh stages Resources/packs): the first pack's combination before; another built one (not
// yet tested on a unit, an effect that won't fit next to it) made from Elektron's .zip; a combination not built yet,
// with a file dropped (held), then made once a built one is picked; a wrong file; the MANUAL page at a base feature
// and at an effect.
//   snap OUTDIR STOCK.zip WRONG.syx WORKDIR PACKSDIR IDENTITY
import AppKit
import SwiftUI

let a = CommandLine.arguments
let out = URL(fileURLWithPath: a[1]), work = URL(fileURLWithPath: a[4])
let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
try? FileManager.default.removeItem(at: work)
try! FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
try! FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)

func wait(_ m: Model) {
    while m.busy { RunLoop.main.run(until: Date().addingTimeInterval(0.05)) }
    RunLoop.main.run(until: Date().addingTimeInterval(0.3))
}

func snap(_ m: Model, _ name: String) {
    let host = NSHostingView(rootView: ContentView(model: m))
    let win = NSWindow(contentRect: NSRect(origin: .zero, size: ContentView.size), styleMask: [.borderless],
                       backing: .buffered, defer: false)
    win.appearance = NSAppearance(named: .darkAqua)
    win.contentView = host
    RunLoop.main.run(until: Date().addingTimeInterval(0.4))
    let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds)!
    host.cacheDisplay(in: host.bounds, to: rep)
    try! rep.representation(using: .png, properties: [:])!.write(to: out.appendingPathComponent(name + ".png"))
    print("wrote \(name).png \(Int(host.bounds.width))x\(Int(host.bounds.height)): \(m.pack?.id ?? "no pack"), "
          + "\(m.effects.sorted().joined(separator: "+")); \(m.line.replacingOccurrences(of: "\n", with: " "))")
}

func zipCopy(_ dir: String) -> URL {
    let d = work.appendingPathComponent(dir)
    try! FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
    let z = d.appendingPathComponent(Core.zipName)
    try! FileManager.default.copyItem(at: URL(fileURLWithPath: a[2]), to: z)
    return z
}

let dir = URL(fileURLWithPath: a[5])
let packs = Pack.load(from: dir), catalog = Catalog.load(from: dir)
Art.dir = URL(fileURLWithPath: a[6])                         // the art, as build.sh stages it for Resources/identity
for p in catalog.problems(packs) { print("catalog: \(p)") }
// the built combination with the most effects but the first pack's, if there is one
let other = packs.dropFirst().max { $0.effects.count < $1.effects.count }
// a combination that fits but isn't built
let fits = catalog.budget?.combos.first { c in c.fits && !packs.contains { Set($0.effects) == Set(c.effects) } }

let m = Model(packs: packs, catalog: catalog)
snap(m, "1-first-pack")
if let other {
    m.choose(other.effects)
    snap(m, "2-another-pack")
    m.process(zipCopy("other"))
    wait(m)
    snap(m, "3-another-pack-made")
}
let h = Model(packs: packs, catalog: catalog)
if let fits {
    h.choose(fits.effects)
    snap(h, "4-not-built")
    h.process(zipCopy("held"))                               // held until a built combination is picked
    wait(h)
    snap(h, "5-not-built-file-held")
    h.choose(packs[0].effects)
    wait(h)
    snap(h, "6-held-file-made")
}
let r = Model(packs: packs, catalog: catalog)
r.process(URL(fileURLWithPath: a[3]))
wait(r)
snap(r, "7-refusal")
let man = Model(packs: packs, catalog: catalog)
let base = catalog.baseShown(packs.flatMap(\.base)).map(\.id)
man.open(base.contains("arp") ? "arp" : base.last ?? catalog.entries[0].id)    // the arp's page has the most in it
snap(man, "8-manual-base")
man.open(other?.effects.last ?? packs[0].effects.first ?? catalog.entries[0].id)
snap(man, "9-manual-effect")
