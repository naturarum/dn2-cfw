// SPDX-License-Identifier: GPL-2.0-or-later
// main.swift - app/test.sh's driver: Core (the app's path into the C core, the same Sources/Core.swift) with the
// packs of a folder laid out as the app's Resources/packs, for every pack: Elektron's .zip, the unzipped folder, the
// file; an existing output (the build: nothing to do; another file: the app would ask, and Core leaves it); wrong
// files. Each output is compared with OUTDIR/ID/ID.syx.
//   coretest STOCK.syx STOCK.zip PACKSDIR OUTDIR WORKDIR [WRONG_FILE ...]
import Foundation

let a = CommandLine.arguments
let fm = FileManager.default
var failed = 0
var who = ""
func check(_ ok: Bool, _ what: String) {
    print((ok ? "PASS  " : "FAIL  ") + who + ": " + what)
    if !ok { failed += 1 }
}

let stock = URL(fileURLWithPath: a[1]), zip = URL(fileURLWithPath: a[2])
let packs = Pack.load(from: URL(fileURLWithPath: a[3]))
let outdir = URL(fileURLWithPath: a[4]), root = URL(fileURLWithPath: a[5])
let index = (try? String(contentsOf: URL(fileURLWithPath: a[3]).appendingPathComponent("index.txt"), encoding: .utf8))?
    .split(whereSeparator: \.isNewline).map(String.init) ?? []
who = "packs"
check(!packs.isEmpty && packs.map(\.id) == index, "\(packs.count) pack(s) loaded in index.txt's order: "
      + packs.map(\.id).joined(separator: ", "))
check(packs.allSatisfy { $0.effects == ["cmp3", "flng"] }, "every pack lists its effects: "
      + packs.map { "\($0.id) \($0.effects.joined(separator: "+"))" }.joined(separator: ", "))
let wantBase = ["CMP4": [String](), "DJ01": ["djvu", "overbridge"]]          // the others: ["overbridge"]
check(packs.allSatisfy { $0.base == (wantBase[$0.id] ?? ["overbridge"]) }, "every pack lists its base: "
      + packs.map { "\($0.id) [\($0.base.joined(separator: "+"))]" }.joined(separator: ", "))
let temps = { (try? fm.contentsOfDirectory(atPath: fm.temporaryDirectory.path))?
    .filter { $0.hasPrefix("digiwerk-patcher-") }.count ?? 0 }

for pack in packs {
    who = pack.id
    let want = try! Data(contentsOf: outdir.appendingPathComponent("\(pack.id)/\(pack.id).syx"))
    let work = root.appendingPathComponent(pack.id), zdir = work.appendingPathComponent("zip with space")
    try? fm.removeItem(at: work)
    try! fm.createDirectory(at: zdir, withIntermediateDirectories: true)
    let input = work.appendingPathComponent(Core.stockName)
    try! fm.copyItem(at: stock, to: input)
    let zcopy = zdir.appendingPathComponent(Core.zipName)
    try! fm.copyItem(at: zip, to: zcopy)
    let temps0 = temps()
    if case .made(let m) = Core.make(from: zcopy, pack: pack), m.existing == .none {      // the .zip, as downloaded
        let w = Core.write(m, titled: packs.count > 1)
        check(w.ok && (try? Data(contentsOf: m.output)) == want
              && m.output == zdir.appendingPathComponent(pack.id + ".syx")
              && w.message.hasPrefix("Checked: this is Elektron's original OS 1.12 file.")
              && (packs.count == 1 || w.message.contains("(\(pack.title))")),
              "the .zip: \(pack.id).syx next to the .zip, byte for byte the build (sha256 \(pack.buildSHA256.prefix(16))...); "
              + "\"\(w.message.split(separator: "\n").last!.prefix(64))...\"")
        check(temps() == temps0, "the .zip: its temporary folder removed")
    } else { check(false, "the .zip: no build") }
    if case .made(let m) = Core.make(from: work, pack: pack), m.existing == .none {      // the folder Finder unzips to
        let w = Core.write(m)
        check(w.ok && (try? Data(contentsOf: m.output)) == want, "the folder: \(pack.id).syx in it, byte for byte")
    } else { check(false, "the folder: no build") }
    if case .made(let m) = Core.make(from: input, pack: pack) {
        check(m.existing == .same, "the file again: the output is already the build")
    } else { check(false, "the file: no build") }
    let out = work.appendingPathComponent(pack.id + ".syx")
    try! Data("not ours".utf8).write(to: out)
    if case .made(let m) = Core.make(from: input, pack: pack) {
        check(m.existing == .other && (try? Data(contentsOf: out)) == Data("not ours".utf8),
              "another file at the output: reported (the app asks before replacing), left as it was")
    } else { check(false, "another file: no build") }
    for wrong in a.dropFirst(6) + packs.filter { $0.id != pack.id }.map { outdir.path + "/\($0.id)/\($0.id).syx" } {
        let name = URL(fileURLWithPath: wrong).lastPathComponent
        if case .refused(let why, let detail) = Core.make(from: URL(fileURLWithPath: wrong), pack: pack) {
            check(!why.isEmpty && !detail.isEmpty && !why.contains("sha256"),
                  "refuses \(name): \"\(why.prefix(56))...\"")
        } else { check(false, "accepted \(wrong)") }
    }
}
print(failed == 0 ? "\(packs.map(\.id).joined(separator: " ")): all passed" : "\(failed) failed")
exit(failed == 0 ? 0 : 1)
