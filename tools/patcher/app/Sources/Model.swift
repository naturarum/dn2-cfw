// SPDX-License-Identifier: GPL-2.0-or-later
// Model.swift - the window's state: the packs and the chosen one, which steps are done, the result line and its
// detail, where the output is. The work runs off the main thread (Core); the main thread only asks before replacing.
import AppKit

final class Model: ObservableObject {
    static let shared = Model(packs: Pack.load(from: Bundle.main.resourceURL?.appendingPathComponent("packs")))
    let packs: [Pack]
    /// the chosen pack: with one pack, that one; with several, none until the user picks one. Another choice starts
    /// step 2 again; a file that came in before any choice (a drop on the icon, say) is made as soon as there is one.
    @Published var selected: Int? {
        didSet {
            guard selected != oldValue, !busy else { return }
            made = nil
            line = ""
            detail = ""
            if let p = pending {
                pending = nil
                process(p)
            }
        }
    }
    private var pending: URL?
    @Published var line = ""                                 // the result, in plain English
    @Published var detail = ""                               // the precise reason, small (for a bug report)
    @Published var failed = false
    @Published var busy = false
    @Published var checked = false                           // step 1: a real OS 1.12 file came in
    @Published var made: URL?                                // step 2: the output, for "Show in Finder"

    var pack: Pack? { selected.flatMap { packs.indices.contains($0) ? packs[$0] : nil } }
    var several: Bool { packs.count > 1 }
    var outName: String { pack.map { $0.id + ".syx" } ?? "the file" }

    /// Elektron Transfer, if this Mac has it
    let transfer: URL? = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "se.elektron.OverbridgeTransfer")
        ?? ["/Applications/Transfer.app", "/Applications/Elektron/Transfer.app"].map { URL(fileURLWithPath: $0) }
            .first { Bundle(url: $0)?.bundleIdentifier?.hasPrefix("se.elektron.") == true }

    init(packs: [Pack]) {
        self.packs = packs
        selected = packs.count == 1 ? 0 : nil
    }

    func choose() {
        let panel = NSOpenPanel()
        panel.message = "Choose \(Core.zipName) or \(Core.stockName)"
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { process(url) }
    }

    func process(_ url: URL) {
        guard !busy else { return }
        if packs.isEmpty {
            return finish("This copy of the patcher is damaged. Download it again.", "no packs in the app's Resources",
                          failed: true)
        }
        guard let pack else {                                // several packs, none chosen yet: hold the file
            pending = url
            return finish("Got \(url.lastPathComponent). Now choose the build you want above.", "")
        }
        busy = true
        made = nil
        failed = false
        line = "Checking the file and making \(pack.id).syx..."
        detail = ""
        DispatchQueue.global(qos: .userInitiated).async {
            let r = Core.make(from: url, pack: pack)
            DispatchQueue.main.async { self.decide(r) }
        }
    }

    private func decide(_ r: Core.Result) {
        switch r {
        case .refused(let why, let detail):
            finish(why, detail, failed: true)
        case .made(let m) where m.existing == .same:
            checked = true
            finish("Checked: this is Elektron's original OS 1.12 file.\n\(Core.name(m.pack, titled: several)) is "
                + "already next to it, exactly the build we tested.", Core.shortDetail(m), file: m.output)
        case .made(let m) where m.existing == .other:
            checked = true
            let a = NSAlert()
            a.messageText = "There is already a file called \(m.pack.id).syx in that folder."
            a.informativeText = "Replace it with the new one?"
            a.addButton(withTitle: "Replace")
            a.addButton(withTitle: "Cancel")
            if a.runModal() == .alertFirstButtonReturn { write(m) } else {
                finish("I left the existing \(m.pack.id).syx alone. Move or rename it, then drop the file again.",
                       m.output.path, failed: true)
            }
        case .made(let m):
            checked = true
            write(m)
        }
    }

    private func write(_ m: Core.Made) {
        let titled = several
        DispatchQueue.global(qos: .userInitiated).async {
            let w = Core.write(m, titled: titled)
            DispatchQueue.main.async { self.finish(w.message, w.detail, file: w.ok ? m.output : nil, failed: !w.ok) }
        }
    }

    private func finish(_ text: String, _ why: String, file: URL? = nil, failed: Bool = false) {
        line = text
        detail = why
        made = file
        self.failed = failed
        busy = false
    }

    func openTransfer() {
        if let t = transfer { NSWorkspace.shared.openApplication(at: t, configuration: .init()) }
    }
}
