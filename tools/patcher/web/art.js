// SPDX-License-Identifier: GPL-2.0-or-later
// art.js - Digiwerk's pixel identity on the page, as the app's Art.swift draws it: the mark, the wordmark and one
// icon per effect from identity/NAME.svg (docs/identity, made by its make_icons.py: one 1x1 rect per lit pixel inside
// a one-pixel border), and the page's own small glyphs, as rows of text. Each is drawn on a canvas with a whole
// number of device pixels per art pixel, so the pixels stay crisp at any zoom.

const cache = new Map();

/** a grid from rows of text, "#" lit */
export function grid(rows) {
    const on = [];
    rows.forEach((r, y) => [...r].forEach((c, x) => { if (c === '#') on.push([x, y]); }));
    return { w: Math.max(...rows.map(r => r.length)), h: rows.length, on };
}

/** the page's own glyphs (the app's Glyph) */
export const Glyph = {
    marker: grid(['##.##.##', '##.##.##']),
    tick: grid(['.......#', '......#.', '.....#..', '#...#...', '.#.#....', '..#.....']),
    bang: grid(['##', '##', '##', '##', '..', '##']),
    up: grid(['...#...', '..###..', '.#.#.#.', '...#...', '...#...', '...#...']),
    drop: grid(['...#...', '...#...', '...#...', '.#.#.#.', '..###..', '...#...', '.......', '#######']),
    help: grid(['.###.', '#...#', '....#', '..##.', '..#..', '.....', '..#..']),
    back: grid(['..#....', '.#.....', '#######', '.#.....', '..#....']),
};

/** identity/NAME.svg -> its grid (the lit pixels, + the border), or null */
export async function load(name) {
    if (cache.has(name))
        return cache.get(name);
    let g = null;
    try {
        const svg = await (await fetch(`identity/${name}.svg`)).text();
        const vb = /viewBox="-1 -1 (\d+) (\d+)"/.exec(svg), lit = svg.indexOf('<g fill="#f2f2f2">');
        if (vb && lit >= 0) {
            const on = [...svg.slice(lit).matchAll(/<rect x="(-?\d+)" y="(-?\d+)"/g)].map(m => [+m[1] + 1, +m[2] + 1]);
            g = { w: +vb[1], h: +vb[2], on };
        }
    } catch {
        g = null;
    }
    cache.set(name, g);
    return g;
}

/** a canvas showing g, about pt CSS pixels per art pixel (rounded to whole device pixels), in colour fg */
export function canvas(g, pt, fg = '#f2f2f2', label = '') {
    const c = document.createElement('canvas');
    c.className = 'px';
    if (label) {
        c.setAttribute('role', 'img');
        c.setAttribute('aria-label', label);
    } else
        c.setAttribute('aria-hidden', 'true');
    if (!g)
        return c;
    const dpr = window.devicePixelRatio || 1, cell = Math.max(1, Math.round(pt * dpr));
    c.width = g.w * cell;
    c.height = g.h * cell;
    c.style.width = `${c.width / dpr}px`;
    c.style.height = `${c.height / dpr}px`;
    const x = c.getContext('2d');
    x.fillStyle = fg;
    for (const [px, py] of g.on)
        x.fillRect(px * cell, py * cell, cell, cell);
    return c;
}

/** the same for identity/NAME.svg, filled in when it has loaded */
export function named(name, pt, fg, label) {
    const holder = document.createElement('span');
    holder.className = 'px-holder';
    load(name).then(g => holder.replaceChildren(canvas(g, pt, fg, label)));
    return holder;
}
