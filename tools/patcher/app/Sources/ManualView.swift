// SPDX-License-Identifier: GPL-2.0-or-later
// ManualView.swift - the MANUAL page: catalog.json's entries in a column on the left (the base the builds carry, the
// effects of the slots, then the rest), the chosen one on the right: its icon, kind and title, what it does, where it
// is on the unit, its knobs, what's worth knowing, whose design it follows, and (an effect) the builds that have it.
import AppKit
import SwiftUI

struct ManualView: View {
    @ObservedObject var model: Model

    var body: some View {
        let c = model.catalog
        let groups: [(String, [Catalog.Entry])] = [
            ("Base \u{00B7} in every build", c.baseShown(Array(Set(model.packs.flatMap(\.base))))),
            ("Effects", c.slots.flatMap(\.options).compactMap { c.entry($0) }),
            ("About the builds", c.entries.filter { $0.kind == "about" }),
        ]
        HStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    ForEach(groups.filter { !$0.1.isEmpty }, id: \.0) { title, entries in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(title).font(.system(size: 10, weight: .bold)).tracking(1.1).textCase(.uppercase)
                                .foregroundStyle(Palette.dim).padding(.bottom, 6)
                            ForEach(entries, id: \.id) { e in
                                ListRow(entry: e, on: e.id == model.entry) { model.entry = e.id }
                            }
                        }
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 22)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(width: 260)
            .background(Palette.panel)
            Palette.line.frame(width: 1)
            ScrollView {
                if let e = c.entry(model.entry) {
                    EntryPage(entry: e, kind: kind(e), builds: builds(e))
                        .id(e.id)
                        .padding(.horizontal, 36)
                        .padding(.vertical, 28)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }

    private func kind(_ e: Catalog.Entry) -> String {
        switch e.kind {
        case "base": return "Base \u{00B7} in every build"
        case "effect": return "Effect \u{00B7} " + (model.catalog.slot(of: e.id)?.title ?? "")
        default: return "About the builds"
        }
    }

    /// an effect's builds in this patcher, by id
    private func builds(_ e: Catalog.Entry) -> String? {
        guard e.kind == "effect" else { return nil }
        let ids = model.packs.filter { $0.effects.contains(e.id) }.map(\.id)
        return ids.isEmpty ? "None yet" : ids.joined(separator: ", ")
    }

    /// one entry in the column: its name, white with a block in front when chosen
    struct ListRow: View {
        let entry: Catalog.Entry, on: Bool
        let action: () -> Void
        @State private var hover = false

        var body: some View {
            Button(action: action) {
                HStack(spacing: 8) {
                    Rectangle().fill(on ? Palette.text : Color.clear).frame(width: 5, height: 5)
                    Text(entry.title).font(.system(size: 12.5, weight: on ? .semibold : .regular))
                        .foregroundStyle(on || hover ? Palette.text : Palette.grey).lineLimit(1)
                    Spacer(minLength: 0)
                }
                .padding(.vertical, 5)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .onHover { hover = $0 }
            .accessibilityAddTraits(on ? .isSelected : [])
        }
    }
}

/// One entry, as a page.
struct EntryPage: View {
    let entry: Catalog.Entry, kind: String
    let builds: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .center, spacing: 20) {
                if let icon = entry.icon {
                    PixelArt(name: icon, pt: 3)
                        .frame(width: 112, height: 84)
                        .background(Palette.well)
                        .overlay(Rectangle().strokeBorder(Palette.line, lineWidth: 1))
                }
                VStack(alignment: .leading, spacing: 6) {
                    Text(kind).font(.system(size: 10.5, weight: .bold)).tracking(1.1).textCase(.uppercase)
                        .foregroundStyle(Palette.grey)
                    Text(entry.title).font(.system(size: 30, weight: .black)).tracking(-0.9)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            VStack(alignment: .leading, spacing: 10) {
                ForEach(entry.text, id: \.self) { Text($0).font(.system(size: 13.5)) }
            }
            .fixedSize(horizontal: false, vertical: true)
            if let w = entry.where {
                section("Where") { Text(w).font(.system(size: 13)) }
            }
            if let knobs = entry.knobs, !knobs.isEmpty {
                section("Knobs") {
                    VStack(alignment: .leading, spacing: 7) {
                        ForEach(Array(knobs.enumerated()), id: \.offset) { _, k in
                            HStack(alignment: .firstTextBaseline, spacing: 10) {
                                Text(k.first ?? "").font(.system(size: 11, weight: .heavy))
                                    .frame(width: 20, height: 20)
                                    .overlay(Rectangle().strokeBorder(k.first?.isEmpty == false ? Palette.dim
                                                                      : Color.clear, lineWidth: 1))
                                    .alignmentGuide(.firstTextBaseline) { $0[.firstTextBaseline] }
                                Text(k.count > 1 ? k[1] : "").font(.system(size: 11, weight: .bold)).tracking(0.8)
                                    .textCase(.uppercase).frame(width: 118, alignment: .leading)
                                Text(k.count > 2 ? k[2] : "").font(.system(size: 13)).foregroundStyle(Palette.grey)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }
            }
            if let notes = entry.notes, !notes.isEmpty {
                section("Worth knowing") {
                    VStack(alignment: .leading, spacing: 7) {
                        ForEach(notes, id: \.self) { n in
                            HStack(alignment: .firstTextBaseline, spacing: 10) {
                                Rectangle().fill(Palette.dim).frame(width: 4, height: 4).offset(y: -3)
                                Text(n).font(.system(size: 13)).foregroundStyle(Palette.grey)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }
            }
            if let credit = entry.credit {
                section("Credit") { Text(credit).font(.system(size: 12.5)).foregroundStyle(Palette.grey) }
            }
            if let builds {
                section("In these builds") { Text(builds).font(.system(size: 13, weight: .semibold)) }
            }
        }
        .frame(maxWidth: 680, alignment: .leading)
    }

    private func section<C: View>(_ title: String, @ViewBuilder _ content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                PixelArt(Glyph.marker, pt: 1.5, fg: NSColor(white: 0.5, alpha: 1))
                Text(title).font(.system(size: 10.5, weight: .bold)).tracking(1.1).textCase(.uppercase)
                    .foregroundStyle(Palette.grey)
            }
            content().fixedSize(horizontal: false, vertical: true)
        }
    }
}
