// SPDX-License-Identifier: GPL-2.0-or-later
// Core.swift - the app's path into the C core (../dn2patch.h): the packs (one per tested build, from the bundle's
// Resources/packs: index.txt names them in order, ID.dn2p each), finding the stock file (Elektron's .zip, the folder
// it unzips to, or the .syx), making a build, seeing what is at the output's place, writing it. No UI here, so
// app/test.sh runs this same code on every pack.
import Foundation

/// One tested build the patcher can make.
struct Pack {
    let id, title, description, record, buildSHA256: String
    let effects: [String]                                    // effect ids, e.g. ["cmp3", "flng"] (Art's icons)
    let data: Data

    /// the packs in dir, in index.txt's order; a file the core can't read is left out
    static func load(from dir: URL?) -> [Pack] {
        guard let dir, let index = try? String(contentsOf: dir.appendingPathComponent("index.txt"), encoding: .utf8)
        else { return [] }
        return index.split(whereSeparator: \.isNewline).compactMap { id in
            guard let data = try? Data(contentsOf: dir.appendingPathComponent(id + ".dn2p")) else { return nil }
            var info = dn2p_info()
            let ok = data.withUnsafeBytes { dn2patch_info($0.bindMemory(to: UInt8.self).baseAddress, $0.count, &info) }
            guard ok == 0 else { return nil }
            return Pack(id: str(info.id), title: str(info.title), description: str(info.description),
                        record: str(info.record), buildSHA256: str(info.build_sha256),
                        effects: str(info.effects).split(separator: " ").map(String.init), data: data)
        }
    }
}

/// a C string field of a struct (a tuple of CChar in Swift)
private func str<T>(_ field: T) -> String {
    withUnsafeBytes(of: field) { String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self) }
}

enum Core {
    static let stockName = "Digitone_II_OS1.12.syx"
    static let zipName = "Digitone_II_OS1.12_dist.zip"
    static let maxIn = 64 << 20                              // DN2P_MAX_IN: no OS file is this big
    static let elektronPage = URL(string: "https://elektron.se/support-downloads/digitone-ii")!
    // MAINTAINER: the guide's public URL (tools/patcher/README.md as published)
    static let guide = URL(string: "https://github.com/naturarum/dn2-cfw/blob/main/tools/patcher/README.md")!
    static let guideStartup = URL(string: guide.absoluteString + "#if-your-digitone-doesnt-start")!

    enum Existing { case none, same, other }                 // at the output's place: nothing, the build, a file

    struct Made {                                            // a build, ready to write
        let pack: Pack
        let data: Data
        let input: URL                                       // what the user gave: the .zip, the folder or the .syx
        let output: URL                                      // ID.syx, next to it (in the folder)
        let existing: Existing
        let message: String, detail: String
    }

    enum Result { case made(Made), refused(String, String) } // refused: the sentence, the precise reason

    /// It unzips, reads, hashes and rebuilds: not on the main thread.
    static func make(from input: URL, pack: Pack) -> Result {
        let fm = FileManager.default
        var url = input, isDir: ObjCBool = false
        var tmp: URL?
        defer { if let tmp { try? fm.removeItem(at: tmp) } }
        guard fm.fileExists(atPath: input.path, isDirectory: &isDir) else {
            return .refused("I can't find that file.", input.path)
        }
        if isDir.boolValue {                                 // the folder Finder unzips to
            url = input.appendingPathComponent(stockName)
            guard fm.fileExists(atPath: url.path) else {
                return .refused("That folder has no \(stockName) in it.", input.path)
            }
        } else if isZip(input) {                             // Elektron's .zip: unzipped to a temporary folder
            let t = fm.temporaryDirectory.appendingPathComponent("digiwerk-patcher-" + UUID().uuidString)
            tmp = t
            let size = (try? fm.attributesOfItem(atPath: input.path)[.size] as? Int) ?? 0
            let unzip = Process()
            unzip.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
            unzip.arguments = ["-x", "-k", input.path, t.path]
            unzip.standardOutput = FileHandle.nullDevice
            unzip.standardError = FileHandle.nullDevice
            do {
                guard size <= maxIn else { throw CocoaError(.fileReadTooLarge) }
                try unzip.run()
                unzip.waitUntilExit()
            } catch {
                return .refused("I couldn't unzip that file. Double-click it in Finder, then drop the \(stockName) "
                    + "inside.", "ditto: \(error.localizedDescription)")
            }
            let found = (fm.enumerator(at: t, includingPropertiesForKeys: nil)?.allObjects as? [URL] ?? [])
                .first { $0.lastPathComponent == stockName && !$0.path.contains("__MACOSX") }
            guard unzip.terminationStatus == 0, let found else {
                return .refused("That .zip doesn't have \(stockName) in it. Use \(zipName) from Elektron's "
                    + "download page.", "ditto exit \(unzip.terminationStatus); no \(stockName) in \(input.lastPathComponent)")
            }
            url = found
        }
        let size = (try? fm.attributesOfItem(atPath: url.path)[.size] as? Int) ?? nil
        // a file too big to be an OS file goes to the core empty, which says it is not the file
        guard let size, let data = size <= maxIn ? (try? Data(contentsOf: url)) : Data() else {
            return .refused("I can't open that file.", url.path)
        }
        var out: UnsafeMutablePointer<UInt8>? = nil
        var outn = 0
        var msg = [CChar](repeating: 0, count: Int(DN2P_MSG))
        let cap = msg.count
        let status = pack.data.withUnsafeBytes { pk in
            data.withUnsafeBytes { buf in
                dn2patch_run(pk.bindMemory(to: UInt8.self).baseAddress, pk.count,
                             buf.bindMemory(to: UInt8.self).baseAddress, buf.count, nil, nil, &out, &outn, &msg, cap)
            }
        }
        let parts = String(cString: msg).split(separator: "\n", maxSplits: 1).map(String.init)
        let (text, detail) = (parts.first ?? "", parts.count > 1 ? parts[1] : "")
        guard status == DN2P_OK, let out else { return .refused(text, detail) }
        let built = Data(bytes: out, count: outn)
        dn2patch_free(out)
        let dir = isDir.boolValue ? input : input.deletingLastPathComponent()
        let dest = dir.appendingPathComponent(pack.id + ".syx")
        return .made(Made(pack: pack, data: built, input: input, output: dest,
                          existing: existing(at: dest, pack: pack), message: text, detail: detail))
    }

    static func isZip(_ url: URL) -> Bool {
        guard let h = try? FileHandle(forReadingFrom: url) else { return false }
        defer { try? h.close() }
        return (try? h.read(upToCount: 4)) == Data([0x50, 0x4B, 0x03, 0x04])
    }

    static func existing(at url: URL, pack: Pack) -> Existing {
        guard FileManager.default.fileExists(atPath: url.path) else { return .none }
        guard let d = try? Data(contentsOf: url) else { return .other }
        let same = pack.data.withUnsafeBytes { pk in
            d.withUnsafeBytes {
                dn2patch_is_build(pk.bindMemory(to: UInt8.self).baseAddress, pk.count,
                                  $0.bindMemory(to: UInt8.self).baseAddress, $0.count)
            }
        }
        return same == 1 ? .same : .other
    }

    /// "OB01.syx", or with several packs "FL01.syx (Flanger with a TIME knob)"
    static func name(_ pack: Pack, titled: Bool) -> String {
        titled ? "\(pack.id).syx (\(pack.title))" : "\(pack.id).syx"
    }

    /// Writes the build to its output (replacing what is there: the caller has asked the user) and reads it back.
    static func write(_ m: Made, titled: Bool = false) -> (ok: Bool, message: String, detail: String) {
        do {
            try m.data.write(to: m.output, options: .atomic)
        } catch {
            return (false, "I couldn't save \(m.pack.id).syx in that folder. Copy \(zipName) to your Desktop and "
                + "drop that copy instead.", error.localizedDescription)
        }
        guard existing(at: m.output, pack: m.pack) == .same else {
            return (false, "\(m.pack.id).syx didn't read back as it was written. Please try again.", m.output.path)
        }
        return (true, "Checked: this is Elektron's original OS 1.12 file.\nSaved \(name(m.pack, titled: titled)) "
            + "next to \(m.input.lastPathComponent), exactly the build we tested.", shortDetail(m))
    }

    /// a success's detail, one line: the build's size and the start of its sha256
    static func shortDetail(_ m: Made) -> String {
        "\(m.pack.id).syx: \(m.data.count) bytes, sha256 \(m.pack.buildSHA256.prefix(16))..."
    }
}
