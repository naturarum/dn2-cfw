// SPDX-License-Identifier: GPL-2.0-or-later
// model.js - the web patcher's state and rules, without the page (app.js draws it): the packs (packs/index.json,
// made from the packs themselves by build.mjs), the catalog (catalog.json: the DSP's four effect slots and our effects
// for each, the manual's entries; dsp-budget.json: the DSP code each combination takes), the effects picked in each
// slot and the pack that is that combination. The same rules as the app's Catalog.swift and Model.swift, so
// test-model.mjs checks a set of packs against them.

export class Catalog {
    constructor(catalog, budget) {
        this.slots = catalog ? catalog.slots : [];
        this.entries = catalog ? catalog.entries : [];
        this.about = catalog ? catalog.about : '';
        this.budgetData = budget || null;
    }

    entry(id) { return this.entries.find(e => e.id === id) || null; }

    slotOf(effect) { return this.slots.find(s => s.options.includes(effect)) || null; }

    get room() { return this.budgetData ? this.budgetData.room : 0; }

    /** the DSP code a combination takes and whether it fits; Elektron's effects alone take none of ours */
    budget(combo) {
        if (!combo.size)
            return { code: 0, fits: true };
        const c = this.budgetData && this.budgetData.combos.find(x => sameSet(x.effects, combo));
        return c ? { code: c.code, fits: c.fits } : null;
    }

    /** "Compressor, flanger and spring reverb"; none: "Elektron's effects" */
    name(combo) {
        const names = this.slots.flatMap(s => s.options).filter(o => combo.has(o))
            .map(o => (this.entry(o) || {}).name || o);
        if (!names.length)
            return "Elektron's effects";
        const all = names.length === 1 ? names[0] : names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1];
        return all[0].toUpperCase() + all.slice(1);
    }

    /** the base entries the page shows, in the manual's order (those with no entry, like Overbridge, aren't features) */
    baseShown(ids) { return this.entries.filter(e => e.kind === 'base' && ids.includes(e.id)); }

    /** what doesn't match in a set of packs (as Catalog.swift's problems) */
    problems(packs) {
        const out = [], seen = new Map();
        for (const p of packs) {
            for (const e of p.effects)
                if (!this.slotOf(e))
                    out.push(`${p.id}: ${e} is in no slot`);
            for (const s of this.slots)
                if (s.options.filter(o => p.effects.includes(o)).length > 1)
                    out.push(`${p.id}: two effects in the ${s.id} slot`);
            const key = [...p.effects].sort().join('+');
            if (seen.has(key))
                out.push(`${p.id}: the same effects as ${seen.get(key)}`);
            else
                seen.set(key, p.id);
            for (const b of p.base)
                if (b !== 'overbridge' && !this.entry(b))
                    out.push(`${p.id}: base ${b} has no entry`);
            if (!this.budget(new Set(p.effects)))
                out.push(`${p.id}: no DSP budget for ${p.effects.join('+')}`);
        }
        return out;
    }
}

const sameSet = (list, set) => list.length === set.size && list.every(x => set.has(x));

export const kb = b => (b / 1000).toFixed(1);

export class Model {
    constructor(packs, catalog) {
        this.packs = packs;
        this.catalog = catalog;
        this.pick = {};
        this.choose(packs.length ? packs[0].effects : []);
    }

    combo(p = this.pick) { return new Set(Object.values(p).filter(v => v)); }

    get effects() { return this.combo(); }

    /** the pack that is the picked combination (the first, if a set has two), or null */
    get pack() {
        const c = this.effects;
        return this.packs.find(p => sameSet(p.effects, c)) || null;
    }

    /** picks these effects (each in its slot; the other slots Elektron's) */
    choose(effects) {
        const p = {};
        for (const s of this.catalog.slots)
            p[s.id] = s.options.find(o => effects.includes(o)) || '';
        this.pick = p;
    }

    /** how a slot's option stands against the other slots' picks: picked, built, notBuilt or wontFit */
    tile(slot, option) {
        if ((this.pick[slot.id] || '') === option)
            return 'picked';
        const c = this.combo({ ...this.pick, [slot.id]: option });
        if (this.packs.some(p => sameSet(p.effects, c)))
            return 'built';
        const b = this.catalog.budget(c);
        return b && b.fits === false ? 'wontFit' : 'notBuilt';
    }

    /** under the DSP meter: what wouldn't fit with this pick, or the room left */
    note() {
        for (const s of this.catalog.slots)
            for (const o of s.options)
                if (this.tile(s, o) === 'wontFit') {
                    const b = this.catalog.budget(this.combo({ ...this.pick, [s.id]: o }));
                    const name = (this.catalog.entry(o) || {}).name || o;
                    if (b)
                        return `With the ${name} ${this.pick[s.id] ? 'instead' : 'too'}: ${kb(b.code)} KB, won't fit`;
                }
        const b = this.catalog.budget(this.effects);
        return b && b.fits ? `${kb(this.catalog.room - b.code)} KB left` : '';
    }

    /** the record line, split for the status box: ["BRICK CHECK 12/12", "TESTED ON A DIGITONE II, 29 OCT 2026"] */
    static record(pack) {
        const parts = pack.record.split(' · ');
        return parts.length === 2 ? [parts[0].replace(' of ', '/'), parts[1]] : [pack.record, ''];
    }

    /** "tested" for a build played on a unit, "checked" for one only the brick checker has run */
    static checked(pack) { return pack.record.includes('tested on a Digitone') ? 'tested' : 'checked'; }
}
