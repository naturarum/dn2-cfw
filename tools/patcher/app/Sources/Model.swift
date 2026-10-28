// SPDX-License-Identifier: GPL-2.0-or-later
// Model.swift - the window's state: the packs, the effects picked in each slot and the pack that is that combination
// (if one is built), the page shown (BUILD or MANUAL) and the manual's entry, which steps are done, the result line
// and its detail, where the output is. The work runs off the main thread (Core); the main thread only asks before
// replacing.
import AppKit

final class Model: ObservableObject {
    static let shared: Model = {
        let dir = Bundle.main.resourceURL?.appendingPathComponent("packs")
        return Model(packs: Pack.load(from: dir), catalog: Catalog.load(from: dir))
    }()
    let packs: [Pack]
    let catalog: Catalog
    /// each slot's pick: one of our effects, or "" for Elektron's. Another combination starts step 2 again; a file
    /// that came in while the combination wasn't built (a drop on the icon, say) is made as soon as one is.
    @Published var pick: [String: String] {
        didSet {
            guard combo(pick) != combo(oldValue) else { return }
            made = nil
            line = ""
            detail = ""
            failed = false
            if pack != nil, let p = pending {
                pending = nil
                process(p)
            }
        }
    }
    enum Page { case build, manual }
    @Published var page = Page.build
    @Published var entry = ""                                // the manual's entry shown
    private var pending: URL?
    @Published var line = ""                                 // the result, in plain English
    @Published var detail = ""                               // the precise reason, small (for a bug report)
    @Published var failed = false
    @Published var busy = false
    @Published var checked = false                           // step 1: a real OS 1.12 file came in
    @Published var made: URL?                                // step 2: the output, for "Show in Finder"

    /// the pack that is the picked combination (the first, if a set has two)
    var selected: Int? { packs.firstIndex { Set($0.effects) == combo(pick) } }
    var pack: Pack? { selected.map { packs[$0] } }
    var several: Bool { packs.count > 1 }
    var outName: String { pack.map { $0.id + ".syx" } ?? "the file" }
    var effects: Set<String> { combo(pick) }

    func combo(_ p: [String: String]) -> Set<String> { Set(p.values.filter { !$0.isEmpty }) }

    /// how a slot's option stands against the other slots' picks
    enum Tile { case picked, built, notBuilt, wontFit }

    func tile(_ slot: Catalog.Slot, _ option: String) -> Tile {
        if pick[slot.id, default: ""] == option { return .picked }
        var p = pick
        p[slot.id] = option
        let c = combo(p)
        if packs.contains(where: { Set($0.effects) == c }) { return .built }
        return catalog.budget(for: c)?.fits == false ? .wontFit : .notBuilt
    }

    /// picks these effects (each in its slot; the other slots Elektron's)
    func choose(_ effects: [String]) {
        pick = Dictionary(uniqueKeysWithValues: catalog.slots.map { s in
            (s.id, s.options.first { effects.contains($0) } ?? "")
        })
    }

    /// the manual at an entry
    func open(_ id: String) {
        entry = id
        page = .manual
    }

    /// Elektron Transfer, if this Mac has it
    let transfer: URL? = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "se.elektron.OverbridgeTransfer")
        ?? ["/Applications/Transfer.app", "/Applications/Elektron/Transfer.app"].map { URL(fileURLWithPath: $0) }
            .first { Bundle(url: $0)?.bundleIdentifier?.hasPrefix("se.elektron.") == true }

    init(packs: [Pack], catalog: Catalog) {
        self.packs = packs
        self.catalog = catalog
        pick = [:]
        choose(packs.first?.effects ?? [])
        entry = catalog.entries.first?.id ?? ""
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
        if packs.isEmpty || catalog.slots.isEmpty {
            return finish("This copy of the patcher is damaged. Download it again.",
                          packs.isEmpty ? "no packs in the app's Resources" : "no catalog.json in the app's Resources",
                          failed: true)
        }
        guard let pack else {                                // a combination that isn't built: hold the file
            pending = url
            return finish("Got \(url.lastPathComponent). This combination isn't built yet: pick one that is.", "")
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
                + "already next to it, exactly the build we \(Core.checked(m.pack)).", Core.shortDetail(m), file: m.output)
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
