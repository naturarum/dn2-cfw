// SPDX-License-Identifier: GPL-2.0-or-later
// build.mjs - the web patcher as a static site: node tools/patcher/web/build.mjs [ID ...] -> out/patcher/web/
// (default: the 0.3.0 release's eight, in the release's order: the page opens on the first). It stages what the
// app's build.sh stages, for the browser:
//   index.html, style.css and the scripts (app.js, model.js, art.js, dn2patch.js, unzip.js);
//   identity/*.svg (docs/identity: the mark, the wordmark, the icons);
//   packs/ID.dn2p (out/patcher/ID/patch.bin, as `make` makes it), catalog.json, dsp-budget.json, and index.json: each
//   pack's fields, read from the pack by dn2patch.js, with the pack's own sha256 (the page refuses a pack that isn't).
// Nothing is written unless every pack parses, rebuilds its tested build from the stock file here (the same core the
// page runs), and the catalog covers the packs (model.js's problems: the app's Catalog.swift rules). No Elektron
// bytes go into the site: a pack holds only ours (stockcheck.py).
import { readFileSync, writeFileSync, mkdirSync, rmSync, copyFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import * as core from './dn2patch.js';
import { Catalog } from './model.js';

const HERE = dirname(fileURLToPath(import.meta.url)), ROOT = join(HERE, '..', '..', '..');
const OUT = join(ROOT, 'out', 'patcher', 'web');
const REL = 'CFEE CFTE CETE EFTE CEEE EFEE EETE EEEE'.split(' ');
const ids = process.argv.length > 2 ? process.argv.slice(2) : REL;
const FILES = ['index.html', 'style.css', 'app.js', 'model.js', 'art.js', 'dn2patch.js', 'unzip.js'];
const ART = ['mark', 'wordmark', 'cmp3', 'flng', 'tape', 'spng', 'djvu'];
const sha = b => createHash('sha256').update(b).digest('hex');
const die = msg => { console.error(`build.mjs: ${msg}; nothing written`); process.exit(1); };

const stockPath = join(ROOT, 'elektron-firmware', 'Digitone_II_OS1_12.syx');
const stock = existsSync(stockPath) ? new Uint8Array(readFileSync(stockPath)) : null;
const packs = [], blobs = [];
for (const id of ids) {
    const f = join(ROOT, 'out', 'patcher', id, 'patch.bin');
    if (!existsSync(f))
        die(`no ${f} (run make in tools/patcher)`);
    const b = new Uint8Array(readFileSync(f)), i = core.info(b);
    if (!i || i.id !== id)
        die(`${id}: the pack doesn't parse`);
    if (stock) {
        const r = core.run(b, stock);
        if (r.status !== core.OK)
            die(`${id}: ${r.msg.split('\n')[0]}`);
    }
    packs.push({ id, title: i.title, description: i.description, record: i.record, tested_on_unit: i.tested_on_unit,
                 effects: i.effects ? i.effects.split(' ') : [], base: i.base ? i.base.split(' ') : [],
                 build_sha256: i.build_sha256, stock_sha256: i.stock_sha256, pack_sha256: sha(b), pack_bytes: b.length,
                 dsp_code_bytes: i.dsp_code_bytes });
    blobs.push(b);
}
const catalog = JSON.parse(readFileSync(join(HERE, '..', 'catalog.json'), 'utf8'));
const budget = JSON.parse(readFileSync(join(HERE, '..', 'dsp-budget.json'), 'utf8'));
const problems = new Catalog(catalog, budget).problems(packs);
if (problems.length)
    die(`the catalog doesn't cover the packs: ${problems.join('; ')}`);

rmSync(OUT, { recursive: true, force: true });
mkdirSync(join(OUT, 'packs'), { recursive: true });
mkdirSync(join(OUT, 'identity'), { recursive: true });
for (const f of FILES)
    copyFileSync(join(HERE, f), join(OUT, f));
for (const a of ART)
    copyFileSync(join(ROOT, 'docs', 'identity', `${a}.svg`), join(OUT, 'identity', `${a}.svg`));
packs.forEach((p, k) => writeFileSync(join(OUT, 'packs', `${p.id}.dn2p`), blobs[k]));
copyFileSync(join(HERE, '..', 'catalog.json'), join(OUT, 'packs', 'catalog.json'));
copyFileSync(join(HERE, '..', 'dsp-budget.json'), join(OUT, 'packs', 'dsp-budget.json'));
writeFileSync(join(OUT, 'packs', 'index.json'), JSON.stringify({ packs }, null, 1) + '\n');
writeFileSync(join(OUT, '.nojekyll'), '');          // GitHub Pages: serve the files as they are
const total = blobs.reduce((n, b) => n + b.length, 0);
console.log(`out/patcher/web: ${packs.length} packs (${(total / 1e6).toFixed(1)} MB${stock ? ', each rebuilt here' : ''}), ` +
            `${FILES.length} files, ${ART.length} pictures; opens on ${packs[0].id}`);
