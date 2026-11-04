// SPDX-License-Identifier: GPL-2.0-or-later
// app.js - the web patcher's page, as the app's window (ContentView.swift, ManualView.swift): a header bar (the
// Digiwerk cog and wordmark; BUILD and MANUAL; Help and "Back to Elektron's OS"), then on BUILD a headline, the base,
// the four effect slots, the picked combination's status, the three steps (01 get Elektron's file, 02 drop it, 03
// send the result); on MANUAL the entries; a status bar and a footer. Everything runs here: Elektron's file is read,
// unzipped (unzip.js) and patched (dn2patch.js) on the user's computer, and only our pack is fetched.
import * as core from './dn2patch.js';
import { extract, isZip } from './unzip.js';
import { Catalog, Model, kb } from './model.js';
import * as art from './art.js';

const ZIP = 'Digitone_II_OS1.12_dist.zip', STOCK = core.STOCK_NAME, MAX_IN = 64 << 20;
const ELEKTRON_PAGE = 'https://elektron.se/support-downloads/digitone-ii';
const TRANSFER_PAGE = 'https://www.elektron.se/support-downloads/transfer';
const GUIDE = 'https://github.com/naturarum/dn2-cfw/blob/main/tools/patcher/README.md';
const SOURCE = 'https://github.com/naturarum/dn2-cfw';
const WHITE = '#f2f2f2', GREY = '#8e8e8e';

let model;
const st = {
    page: 'build', entry: '', back: false,
    line: '', detail: '', failed: false, busy: false, checked: false,
    made: null,                     // { url, name, pack, bytes } of the build, once made: ready to download
    saved: false,                   // the user has downloaded it
    pending: null,                  // a file that came in while the combination wasn't built
};
const packCache = new Map();

// ---- small DOM helpers ----

function h(tag, attrs = {}, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false)
            continue;
        if (k === 'class')
            e.className = v;
        else if (k.startsWith('on'))
            e.addEventListener(k.slice(2), v);
        else if (k === 'text')
            e.textContent = v;
        else
            e.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat())
        if (kid !== null && kid !== undefined && kid !== false)
            e.append(kid);
    return e;
}

const caps = (text, cls = '') => h('span', { class: `caps ${cls}`, text });
const marker = (pt, fg) => art.canvas(art.Glyph.marker, pt, fg);
const tick = pt => art.canvas(art.Glyph.tick, pt, WHITE, 'done');

// ---- start ----

async function boot() {
    const json = async p => (await fetch(p)).json();
    let index, catalog, budget;
    try {
        [index, catalog, budget] = await Promise.all([json('packs/index.json'), json('packs/catalog.json'),
                                                      json('packs/dsp-budget.json')]);
    } catch (e) {
        document.getElementById('page').append(h('p', { class: 'noscript',
            text: `This page couldn't load its builds. Reload it. (${e.message})` }));
        return;
    }
    model = new Model(index.packs, new Catalog(catalog, budget));
    st.entry = catalog.entries[0] ? catalog.entries[0].id : '';
    const input = document.getElementById('file');
    input.addEventListener('change', () => {
        if (input.files[0])
            processFile(input.files[0]);
        input.value = '';
    });
    wireDrop();
    render();
}

function wireDrop() {
    let depth = 0;
    const zone = () => document.querySelector('.drop');
    const files = e => e.dataTransfer && [...e.dataTransfer.types].includes('Files');
    window.addEventListener('dragenter', e => {
        if (!files(e))
            return;
        e.preventDefault();
        depth++;
        if (zone() && model.pack)
            zone().classList.add('hover');
    });
    window.addEventListener('dragleave', () => {
        if (--depth <= 0) {
            depth = 0;
            if (zone())
                zone().classList.remove('hover');
        }
    });
    window.addEventListener('dragover', e => { if (files(e)) e.preventDefault(); });
    window.addEventListener('drop', e => {
        if (!files(e))
            return;
        e.preventDefault();                         // a file dropped anywhere on the page counts as dropped in 02
        depth = 0;
        if (zone())
            zone().classList.remove('hover');
        const f = e.dataTransfer.files[0];
        if (f && !st.busy) {
            st.page = 'build';
            processFile(f);
        }
    });
}

// ---- what the user does ----

function pick(slotId, option) {
    const before = [...model.effects].sort().join();
    model.pick = { ...model.pick, [slotId]: option };
    if ([...model.effects].sort().join() !== before) {   // another combination starts step 2 again
        clearMade();
        st.line = st.detail = '';
        st.failed = false;
        if (model.pack && st.pending) {
            const f = st.pending;
            st.pending = null;
            processFile(f);
            return;
        }
    }
    render();
}

function openEntry(id) {
    st.entry = id;
    st.page = 'manual';
    render();
    window.scrollTo(0, 0);
}

function clearMade() {
    if (st.made)
        URL.revokeObjectURL(st.made.url);
    st.made = null;
    st.saved = false;
}

function finish(line, detail, failed = false) {
    Object.assign(st, { line, detail, failed, busy: false });
    render();
}

const nextFrame = () => new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));

/** our pack for this build, fetched once and checked against the index (its own sha256 and the build it makes) */
async function loadPack(p) {
    if (packCache.has(p.id))
        return packCache.get(p.id);
    const r = await fetch(`packs/${p.id}.dn2p`);
    if (!r.ok)
        throw new Error(`packs/${p.id}.dn2p: HTTP ${r.status}`);
    const b = new Uint8Array(await r.arrayBuffer()), i = core.info(b);
    if (core.hex(core.sha256(b)) !== p.pack_sha256 || !i || i.build_sha256 !== p.build_sha256)
        throw new Error(`packs/${p.id}.dn2p is not the pack this page lists`);
    packCache.set(p.id, b);
    return b;
}

async function processFile(file) {
    if (st.busy)
        return;
    const p = model.pack;
    if (!p) {                                       // a combination that isn't built: hold the file
        st.pending = file;
        return finish(`Got ${file.name}. This combination isn't built yet: pick one that is.`, '');
    }
    clearMade();
    Object.assign(st, { busy: true, failed: false, line: `Checking the file and making ${p.id}.syx...`, detail: '' });
    render();
    await nextFrame();
    try {
        if (file.size > MAX_IN)
            return finish(`That's not Elektron's Digitone II OS 1.12 file. Use the ${STOCK} file from Elektron's ` +
                `download (${ZIP}).`, `${file.name}: ${file.size} bytes`, true);
        let bytes = new Uint8Array(await file.arrayBuffer());
        if (isZip(bytes)) {
            if (typeof DecompressionStream === 'undefined')
                return finish(`This browser can't unzip files. Unzip ${ZIP} first, then drop the ${STOCK} inside it.`,
                    'no DecompressionStream in this browser', true);
            const syx = await extract(bytes, STOCK);
            if (!syx)
                return finish(`That .zip doesn't have ${STOCK} in it. Use ${ZIP} from Elektron's download page.`,
                    `no ${STOCK} in ${file.name}`, true);
            bytes = syx;
        }
        const r = core.run(await loadPack(p), bytes);
        const [text, ...rest] = r.msg.split('\n');
        if (r.status !== core.OK)
            return finish(text, rest.join(' '), true);
        const name = `${p.id}.syx`;
        st.made = { url: URL.createObjectURL(new Blob([r.out], { type: 'application/octet-stream' })), name, pack: p,
                    bytes: r.out.length };
        st.checked = true;
        finish(`Checked: this is Elektron's original OS 1.12 file.\n${name} (${p.title}) is ready to download, ` +
            `exactly the build we ${Model.checked(p)}.`,
            `${name}: ${r.out.length} bytes, sha256 ${p.build_sha256.slice(0, 16)}...`);
    } catch (e) {
        finish(`Something went wrong, so nothing was saved. Please report this.`, String(e && e.message || e), true);
    }
}

/** the download, only when the user asks for it */
function save() {
    if (!st.made)
        return;
    const a = h('a', { href: st.made.url, download: st.made.name });
    document.body.append(a);
    a.click();
    a.remove();
    st.saved = true;
    st.line = `Downloaded ${st.made.name}. Now send it to your Digitone: 03.`;
    render();
}

// ---- drawing ----

function render() {
    renderBar();
    const page = document.getElementById('page');
    page.replaceChildren(st.page === 'build' ? buildPage() : manualPage());
    renderStatus();
    renderFoot();
}

function renderBar() {
    const bar = document.getElementById('bar');
    const tab = (id, title) => h('button', { class: 'tab caps', 'aria-current': st.page === id ? 'page' : null,
        onclick: () => { st.page = id; st.back = false; render(); } }, title, h('span'));
    bar.replaceChildren(...[
        art.named('mark', 2.5, WHITE, 'Digiwerk'),
        art.named('wordmark', 3, WHITE, 'DIGIWERK'),
        h('div', { class: 'spacer' }),
        h('nav', { class: 'tabs', 'aria-label': 'Pages' }, tab('build', 'Build'), tab('manual', 'Manual')),
        h('a', { class: 'navlink caps hide-narrow', href: GUIDE, target: '_blank', rel: 'noopener' },
            art.canvas(art.Glyph.help, 1.5, GREY), 'Help'),
        h('button', { class: 'navlink caps back', 'aria-expanded': String(st.back),
            onclick: () => { st.back = !st.back; renderBar(); } },
            art.canvas(art.Glyph.back, 1.5, GREY), "Back to Elektron's OS"),
        st.back ? backPop() : null].filter(Boolean));
}

function backPop() {
    return h('div', { class: 'pop', role: 'dialog', 'aria-label': "Back to Elektron's OS" },
        h('div', { class: 'head' }, marker(2), caps("Back to Elektron's OS")),
        h('p', { text: `Send Elektron's ${STOCK} to the Digitone the same way, with Transfer. It's inside ${ZIP}: ` +
            'unzip the .zip to get it.' }),
        h('p', { text: "If the Digitone doesn't start: hold FUNC while you switch it on, press TRIG 4 (OS UPGRADE), " +
            "then send Elektron's .syx with Transfer over a DIN MIDI cable. The startup menu has no USB." }),
        h('a', { class: 'btn', href: `${GUIDE}#if-your-digitone-doesnt-start`, target: '_blank', rel: 'noopener' },
            'Open the guide'));
}

function buildPage() {
    return h('div', { class: 'build' }, hero(), baseStrip(), slots(), pickStatus(), steps());
}

function hero() {
    return h('div', { class: 'hero' },
        h('div', { class: 'left' }, marker(3), h('h1', { text: 'Make your Digiwerk.' })),
        h('p', { text: "Custom firmware for the Elektron Digitone II, made in your browser from Elektron's own OS " +
            '1.12 file. Nothing is uploaded. Pick an effect for each slot, then go through 01 to 03.' }));
}

function baseShown() {
    return model.catalog.baseShown([...new Set(model.packs.flatMap(p => p.base))]);
}

function baseStrip() {
    const shown = baseShown(), kids = [caps('Base', 'label'), caps('In every build', 'label2')];
    shown.forEach((e, i) => {
        if (i)
            kids.push(h('span', { class: 'dot', text: '·' }));
        kids.push(h('button', { class: 'chip caps', title: `${e.title}: open its page in the manual`,
            onclick: () => openEntry(e.id) }, e.icon ? art.named(e.icon, 1, WHITE) : null, e.label || e.title));
    });
    if (!shown.length)
        kids.push(caps('Nothing extra', 'label2'));
    return h('div', { class: 'base', role: 'group', 'aria-label': 'Base, in every build' }, kids);
}

const HELP = { picked: 'Picked', built: 'Built and checked with the other slots as picked',
               notBuilt: "Fits with the other slots as picked, but that combination isn't built yet",
               wontFit: 'With the other slots as picked, this needs more DSP memory than there is' };

function slots() {
    return h('div', { class: 'slots' }, model.catalog.slots.map(s =>
        h('div', { class: 'slot' }, h('h2', { class: 'caps', text: s.title }),
            h('div', { class: 'tiles' }, ['', ...s.options].map(o => tile(s, o))))));
}

function tile(slot, option) {
    const state = model.tile(slot, option), entry = option ? model.catalog.entry(option) : null;
    const faint = state === 'notBuilt' || state === 'wontFit';
    const label = entry ? entry.label || entry.title : slot.stock;
    const b = h('button', {
        class: `tile${state === 'picked' ? ' picked' : ''}${faint ? ' faint' : ''}`,
        'aria-pressed': String(state === 'picked'), disabled: state === 'wontFit' || st.busy,
        title: HELP[state], 'aria-label': `${slot.title}: ${entry ? entry.title : `Elektron's ${slot.stock.toLowerCase()}`}`,
        onclick: () => pick(slot.id, option) },
        h('div', { class: 'art' }, entry ? art.named(entry.icon || option, 3, faint ? '#5c5c5c' : WHITE)
                                         : h('span', { class: 'plate caps', text: 'Elektron' })),
        caps(label, 'name'),
        h('div', { class: 'tagrow' }, state === 'wontFit' ? h('span', { class: 'tag wontfit', text: "Won't fit" })
            : state === 'notBuilt' ? h('span', { class: 'tag notbuilt', text: 'Not built yet' }) : null),
        state === 'picked' ? h('span', { class: 'tick' }, tick(1.5)) : null);
    if (!entry)
        return b;
    const wrap = h('div', { style: 'position: relative' }, b,
        h('button', { class: 'info', title: `${entry.title}: open its page in the manual`,
            'aria-label': `${entry.title}: open in the manual`, onclick: () => openEntry(entry.id) },
            art.canvas(art.Glyph.help, 1.5, '#737373')));
    return wrap;
}

function pickStatus() {
    const c = model.effects, budget = model.catalog.budget(c), room = model.catalog.room, p = model.pack;
    let rec;
    if (p) {
        const [a, b] = Model.record(p);
        rec = [h('span', { class: 'grey', text: a }),
               b ? h('span', { class: p.record.includes('not yet') ? 'yellow' : '', text: b }) : null];
    } else if (budget && budget.fits === false)
        rec = [h('span', { class: 'orange', text: 'Won’t fit: more DSP code than there is room for' })];
    else
        rec = [h('span', { class: 'grey', text: 'It fits; we build and check each combination before the patcher offers it' })];
    const f = room > 0 ? Math.min(1, (budget ? budget.code : 0) / room) : 0;
    return h('div', { class: 'status-box', title: p ? p.description : '' },
        h('div', { class: 'who' },
            h('div', { class: 'row' },
                h('span', { class: `id${p ? '' : ' none'}`, text: p ? p.id : 'Not built yet' }),
                h('span', { class: 'title', text: model.catalog.name(c) })),
            h('div', { class: 'rec caps' }, rec)),
        h('div', { class: 'dsp' },
            h('div', { class: 'top' }, h('span', { class: 'caps', text: 'DSP code' }),
                h('span', { class: 'kb', text: budget ? `${kb(budget.code)} of ${kb(room)} KB` : 'not measured' })),
            h('div', { class: 'bar', role: 'meter', 'aria-label': 'DSP code', 'aria-valuemin': '0',
                       'aria-valuemax': String(room), 'aria-valuenow': String(budget ? budget.code : 0) },
                h('div', { class: `fill${budget && budget.fits === false ? ' over' : ''}`, style: `width: ${f * 100}%` })),
            h('div', { class: 'note', text: model.note() })));
}

function steps() {
    const p = model.pack, name = p ? `${p.id}.syx` : 'the file';
    const step = (num, title, done, dim, ...content) =>
        h('div', { class: `step${dim ? ' dim' : ''}` },
            h('div', { class: 'head' }, h('span', { class: 'num', text: num }), caps(title),
                done ? h('span', { class: 'done' }, tick(2)) : null),
            content);
    const choose = () => document.getElementById('file').click();
    const drop = st.made
        ? h('div', { class: 'drop ready' },                 // made: ready to download, when the user wants it
            tick(3),
            h('div', { class: 'big', text: st.saved ? `${st.made.name} downloaded` : `${st.made.name} is ready` }),
            h('div', { class: 'small', text: st.saved ? 'Download it again any time.' : 'Exactly the build we ' +
                `${Model.checked(st.made.pack)}. Nothing is saved until you download it.` }),
            h('button', { class: `btn${st.saved ? '' : ' primary'}`, onclick: save }, `Download ${st.made.name}`),
            h('button', { class: 'link', disabled: st.busy, onclick: choose }, 'or use another file'))
        : h('div', { class: 'drop' },
            art.canvas(art.Glyph.drop, 3, '#999999'),
            h('div', { class: 'big', text: p ? `Drop ${ZIP} here` : "This combination isn't built yet" }),
            h('div', { class: 'small', text: p ? `or the ${STOCK} inside it` : 'Pick one with no tag on its tiles' }),
            h('button', { class: 'btn', disabled: st.busy, onclick: choose }, 'Choose file'));
    const send = ['Back up your projects with Transfer first.', 'Connect over USB and open Elektron Transfer.',
                  `Drag ${name} (in your downloads) onto “Drop files here”.`,
                  'Answer YES, wait for SUCCESS! REBOOTING.'];
    return h('div', { class: 'steps' },
        step('01', "Get Elektron's OS 1.12", st.checked, false,
            h('p', { text: `On Elektron's Digitone II page, download “Digitone II OS 1.12”. It arrives as ` +
                `${ZIP}. There's no need to unzip it.` }),
            h('div', { class: 'grow' }),
            h('div', { class: 'buttons' }, h('a', { class: 'btn', href: ELEKTRON_PAGE, target: '_blank', rel: 'noopener' },
                'Open download page'))),
        step('02', st.made ? 'Download it' : 'Drop it here', st.saved, !p, drop),
        step('03', 'Send it to your Digitone', false, !st.saved,
            h('ol', {}, send.map((s, i) => h('li', {}, h('b', { text: String(i + 1) }), h('span', { text: s })))),
            h('div', { class: 'grow' }),
            h('div', { class: 'buttons' },
                h('a', { class: 'btn', href: TRANSFER_PAGE, target: '_blank', rel: 'noopener' }, 'Get Transfer'))));
}

function manualPage() {
    const c = model.catalog;
    const groups = [['Base · in every build', baseShown()],
                    ['Effects', c.slots.flatMap(s => s.options).map(o => c.entry(o)).filter(Boolean)],
                    ['About the builds', c.entries.filter(e => e.kind === 'about')]].filter(g => g[1].length);
    const nav = h('nav', { 'aria-label': 'Manual' }, groups.map(([title, entries]) =>
        h('div', {}, h('h3', { class: 'caps', text: title }),
            h('ul', {}, entries.map(e => h('li', {}, h('button', { 'aria-current': e.id === st.entry ? 'true' : null,
                onclick: () => { st.entry = e.id; render(); } }, h('i'), e.title)))))));
    const e = c.entry(st.entry);
    return h('div', { class: 'manual' }, nav, e ? entryPage(e) : h('div', { class: 'entry' }));
}

function entryPage(e) {
    const c = model.catalog;
    const kind = e.kind === 'base' ? 'Base · in every build'
        : e.kind === 'effect' ? `Effect · ${(c.slotOf(e.id) || {}).title || ''}` : 'About the builds';
    const section = (title, ...content) => h('section', {}, h('div', { class: 'head' }, marker(1.5, '#808080'),
        caps(title)), content);
    let builds = null;
    if (e.kind === 'effect') {
        const ids = model.packs.filter(p => p.effects.includes(e.id)).map(p => p.id);
        builds = ids.length ? ids.join(', ') : 'None yet';
    }
    return h('article', { class: 'entry' },
        h('div', { class: 'top' },
            e.icon ? h('div', { class: 'icon' }, art.named(e.icon, 3, WHITE)) : null,
            h('div', {}, caps(kind, 'kind'), h('h2', { text: e.title }))),
        h('div', { class: 'text' }, (e.text || []).map(t => h('p', { text: t }))),
        e.where ? section('Where', h('p', { text: e.where })) : null,
        e.knobs && e.knobs.length ? section('Knobs', h('div', { class: 'knobs' }, e.knobs.map(k =>
            h('div', { class: 'knob' }, h('span', { class: `k${k[0] ? '' : ' empty'}`, text: k[0] || '' }),
              h('span', { class: 'n', text: k[1] || '' }), h('span', { class: 'd', text: k[2] || '' }))))) : null,
        e.notes && e.notes.length ? section('Worth knowing', h('ul', { class: 'notes' },
            e.notes.map(n => h('li', { text: n })))) : null,
        e.credit ? section('Credit', h('p', { class: 'credit', text: e.credit })) : null,
        builds ? section('In these builds', h('p', { class: 'builds', text: builds })) : null);
}

function renderStatus() {
    const p = model.pack;
    const idle = p ? `Drop Elektron's file in 02 to make ${p.id}.syx.`
                   : "Pick a combination that's built, then drop Elektron's file in 02.";
    let icon;
    if (st.busy)
        icon = h('div', { class: 'spin', role: 'img', 'aria-label': 'working' });
    else if (st.failed)
        icon = art.canvas(art.Glyph.bang, 2, '#ff781f', 'refused');
    else if (st.made)
        icon = st.saved ? tick(2) : art.canvas(art.Glyph.up, 2, '#f2f2f2');
    else if (st.line)
        icon = art.canvas(art.Glyph.up, 2, '#999999');
    else
        icon = marker(2, '#595959');
    document.getElementById('status').replaceChildren(
        h('div', { class: 'icon' }, icon),
        h('div', {},
            h('div', { class: `line${st.line ? '' : ' idle'}${st.failed ? ' bad' : ''}`,
                       text: st.line ? st.line.replace('\n', ' ') : idle }),
            st.detail ? h('div', { class: 'detail', text: st.detail }) : null));
}

function renderFoot() {
    document.getElementById('foot').replaceChildren(
        h('span', { text: 'Not made or supported by Elektron.' }),
        h('span', {}, 'Digiwerk · custom firmware for the Digitone II · GPL-2.0-or-later · ',
            h('a', { href: SOURCE, target: '_blank', rel: 'noopener' }, 'Source')));
}

boot();
