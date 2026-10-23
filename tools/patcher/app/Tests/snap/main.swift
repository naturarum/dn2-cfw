// SPDX-License-Identifier: GPL-2.0-or-later
// snap/main.swift - app/screens.sh's driver: the window's own view (ContentView) and Model, rendered offscreen to PNG
// (AppKit's cacheDisplay; no screen capture), after real runs, for a one-pack set (before, after Elektron's .zip,
// after a wrong file; its one pack is chosen, so no file is ever held) and a three-pack set (before; Elektron's .zip
// held before a choice; made once the last pack is chosen; a wrong file).
//   snap OUTDIR STOCK.zip WRONG.syx WORKDIR PACKS1 PACKS3 IDENTITY
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
    print("wrote \(name).png \(Int(host.bounds.width))x\(Int(host.bounds.height))")
}

func zipCopy(_ dir: String) -> URL {
    let d = work.appendingPathComponent(dir)
    try! FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
    let z = d.appendingPathComponent(Core.zipName)
    try! FileManager.default.copyItem(at: URL(fileURLWithPath: a[2]), to: z)
    return z
}

let one = Pack.load(from: URL(fileURLWithPath: a[5])), three = Pack.load(from: URL(fileURLWithPath: a[6]))
Art.dir = URL(fileURLWithPath: a[7])                         // the art, as build.sh stages it for Resources/identity
let m = Model(packs: one)
snap(m, "1-one-pack-before")
m.process(zipCopy("one"))
wait(m)
snap(m, "2-one-pack-success")
let r = Model(packs: one)
r.process(URL(fileURLWithPath: a[3]))
wait(r)
snap(r, "3-one-pack-refusal")
let t = Model(packs: three)
snap(t, "4-three-packs-before")
t.process(zipCopy("three"))                                  // a file before any choice: held
wait(t)
snap(t, "5-three-packs-file-held")
t.selected = three.count - 1                                 // the choice makes it
wait(t)
snap(t, "6-three-packs-success")
let u = Model(packs: three)
u.selected = 0
u.process(URL(fileURLWithPath: a[3]))
wait(u)
snap(u, "7-three-packs-refusal")
