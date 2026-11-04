// SPDX-License-Identifier: GPL-2.0-or-later
// test-model.mjs - the web patcher's rules (model.js) on the built site's index (out/patcher/web/packs, build.mjs),
// as app/test.sh checks the app's: every pack lists the effects its name says and base 0.3; the catalog covers the
// packs, one pack per combination; every combination's tiles (0.3.0: the eight without the spring built, the six
// with it not built, CETS and CFTS won't fit);
// the page opens on the first pack; the names, the DSP notes and the record lines the status box shows.
//   node tools/patcher/web/test-model.mjs
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { Catalog, Model } from './model.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..'), SITE = join(ROOT, 'out', 'patcher', 'web');
const json = p => JSON.parse(readFileSync(join(SITE, 'packs', p), 'utf8'));
const { packs } = json('index.json'), catalog = new Catalog(json('catalog.json'), json('dsp-budget.json'));
let failed = 0;
const check = (ok, what) => {
    console.log((ok ? 'PASS  ' : 'FAIL  ') + what);
    if (!ok)
        failed++;
};
const BASE03 = ['djvu', 'key', 'onehalf', 'arp', 'midiarp', 'intro', 'usb', 'overbridge'];
const fromName = id => [...id].map((c, i) => c === 'CFTS'[i] ? ['cmp3', 'flng', 'tape', 'spng'][i] : null).filter(Boolean);

check(packs.every(p => p.effects.join() === fromName(p.id).join()), 'every pack lists the effects its name says: ' +
      packs.map(p => `${p.id} ${p.effects.join('+') || '-'}`).join(', '));
check(packs.every(p => p.base.join() === BASE03.join()), 'every pack carries base 0.3');
check(catalog.problems(packs).length === 0 && catalog.slots.length === 4 && catalog.budgetData.combos.length === 15,
      `the catalog covers the packs, one per combination (${catalog.problems(packs).join('; ') || 'no problems'})`);
const m = new Model(packs, catalog);
check(m.pack && m.pack.id === packs[0].id, `the page opens on ${packs[0].id}`);

// every one of the 16 combinations, by picking it: built ones have their pack, the others none
const all = [];
for (let k = 0; k < 16; k++)
    all.push(['cmp3', 'flng', 'tape', 'spng'].filter((_, i) => k >> i & 1));
const built = [], wont = [], notBuilt = [];
for (const fx of all) {
    m.choose(fx);
    const b = catalog.budget(m.effects);
    if (m.pack)
        built.push(m.pack.id);
    else if (b && !b.fits)
        wont.push(fx.join('+'));
    else
        notBuilt.push(fx.join('+'));
}
check(built.length === 8 && built.every(id => id[3] === 'E') && notBuilt.length === 6 &&
      notBuilt.every(fx => fx.endsWith('spng')) && wont.join() === 'cmp3+tape+spng,cmp3+flng+tape+spng',
      `16 combinations: 8 built (${built.join(' ')}), not built: ${notBuilt.join(', ')}; won't fit: ${wont.join(', ')}`);

m.choose(['cmp3', 'flng', 'tape']);
const spring = catalog.slots.find(s => s.id === 'reverb');
check(m.pack.id === 'CFTE' && m.tile(spring, 'spng') === 'wontFit' && m.tile(spring, '') === 'picked',
      'CFTE: the spring tile won\'t fit, Elektron\'s reverb is picked');
check(m.note() === "With the spring too: 99.3 KB, won't fit", `CFTE's note: "${m.note()}"`);
check(catalog.name(m.effects) === 'Compressor, flanger and tape echo', `CFTE's name: "${catalog.name(m.effects)}"`);
m.choose([]);
check(m.pack.id === 'EEEE' && catalog.name(m.effects) === "Elektron's effects" && m.note() === '83.8 KB left',
      `EEEE: "${catalog.name(m.effects)}", "${m.note()}"`);
m.choose(['cmp3', 'flng']);
check(m.pack.id === 'CFEE' && m.tile(spring, 'spng') === 'notBuilt', 'CFEE: the spring tile is not built (CFES)');
const rec = /^tested on a Digitone II, \d{1,2} [A-Z][a-z]{2} 20\d\d$|^not yet tested on a unit$/;
check(packs.every(p => Model.record(p)[0] === 'Brick check 12/12' && rec.test(Model.record(p)[1]) &&
                       Model.checked(p) === (Model.record(p)[1].startsWith('tested') ? 'tested' : 'checked')),
      'every pack\'s record: brick check 12/12, then tested on a unit with its date or not yet (' +
      packs.filter(p => Model.checked(p) === 'tested').length + ' of ' + packs.length + ' tested)');
m.choose(['spng']);
check(!m.pack && m.tile(spring, 'spng') === 'picked', 'the spring alone: picked, no pack (not built)');
console.log(failed ? `${failed} failed` : 'model: all passed');
process.exit(failed ? 1 : 0);
