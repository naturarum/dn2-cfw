// SPDX-License-Identifier: GPL-2.0-or-later
// Catalog.swift - what the window knows besides the packs: the DSP's four effect slots and our effects for each, and
// the manual's entries (catalog.json, tools/patcher/catalog.json), and how much DSP code each combination of our
// effects takes and whether it fits (dsp-budget.json, made by scripts/patcher_budget.py). Both sit next to the packs
// (Resources/packs). No UI here, so app/test.sh checks a set of packs against it.
import Foundation

struct Catalog: Decodable {
    struct Slot: Decodable {
        let id, title, stock: String                         // stock: the name of Elektron's effect in the slot
        let options: [String]                                // our effects for the slot (effect ids)
    }

    struct Entry: Decodable {
        let id, kind, title: String                          // kind: base, effect or about
        let label: String?                                   // the base strip's or the tile's words
        let name: String?                                    // an effect in a sentence: "flanger"
        let icon: String?                                    // its pixel icon (Resources/identity)
        let text: [String]
        let `where`: String?
        let knobs: [[String]]?                               // [knob, name, what it does]
        let notes: [String]?
        let credit: String?
    }

    struct Budget: Decodable {
        struct Combo: Decodable {
            let effects: [String]
            let code: Int                                    // bytes of our DSP code
            let fits: Bool                                   // every function placed in the room
        }
        let room: Int                                        // bytes of DSP memory our code may use
        let combos: [Combo]
    }

    let slots: [Slot]
    let entries: [Entry]
    var budget: Budget?

    private enum CodingKeys: String, CodingKey { case slots, entries }

    static let empty = Catalog(slots: [], entries: [], budget: nil)

    /// catalog.json and dsp-budget.json in dir; an unreadable catalog gives no slots (the window then says it's damaged)
    static func load(from dir: URL?) -> Catalog {
        guard let dir, let d = try? Data(contentsOf: dir.appendingPathComponent("catalog.json")),
              var c = try? JSONDecoder().decode(Catalog.self, from: d) else { return .empty }
        if let b = try? Data(contentsOf: dir.appendingPathComponent("dsp-budget.json")) {
            c.budget = try? JSONDecoder().decode(Budget.self, from: b)
        }
        return c
    }

    func entry(_ id: String) -> Entry? { entries.first { $0.id == id } }

    func slot(of effect: String) -> Slot? { slots.first { $0.options.contains(effect) } }

    /// the DSP code a combination takes and whether it fits; Elektron's effects alone take none of ours
    func budget(for combo: Set<String>) -> (code: Int, fits: Bool)? {
        if combo.isEmpty { return (0, true) }
        return budget?.combos.first { Set($0.effects) == combo }.map { ($0.code, $0.fits) }
    }

    /// "Compressor, flanger and spring"; none: "Elektron's effects"
    func name(of combo: Set<String>) -> String {
        let names = slots.flatMap { $0.options }.filter { combo.contains($0) }.map { entry($0)?.name ?? $0 }
        guard let first = names.first else { return "Elektron's effects" }
        let all = names.count == 1 ? first : names.dropLast().joined(separator: ", ") + " and " + names.last!
        return all.prefix(1).uppercased() + all.dropFirst()
    }

    /// the base ids the window shows, in the manual's order (those with no entry, like Overbridge, aren't features)
    func baseShown(_ ids: [String]) -> [Entry] {
        entries.filter { $0.kind == "base" && ids.contains($0.id) }
    }

    /// What doesn't match in a set of packs: an effect in no slot, two in one slot, two packs with the same
    /// effects, a base id with no entry but Overbridge, a combination with no budget.
    func problems(_ packs: [Pack]) -> [String] {
        var out: [String] = [], seen: [Set<String>: String] = [:]
        for p in packs {
            for e in p.effects where slot(of: e) == nil { out.append("\(p.id): \(e) is in no slot") }
            for s in slots where s.options.filter(p.effects.contains).count > 1 {
                out.append("\(p.id): two effects in the \(s.id) slot")
            }
            let c = Set(p.effects)
            if let o = seen[c] { out.append("\(p.id): the same effects as \(o)") } else { seen[c] = p.id }
            for b in p.base where b != "overbridge" && entry(b) == nil { out.append("\(p.id): base \(b) has no entry") }
            if budget(for: c) == nil { out.append("\(p.id): no DSP budget for \(p.effects.joined(separator: "+"))") }
        }
        return out
    }
}
