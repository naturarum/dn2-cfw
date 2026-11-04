// SPDX-License-Identifier: GPL-2.0-or-later
// test.mjs - the web patcher's core (dn2patch.js, unzip.js) against the C core's results, in Node:
//   node tools/patcher/web/test.mjs [ID ...]          (default: all fourteen 0.3 packs; the release ships eight)
// For every pack (out/patcher/ID/patch.bin, as `make` makes it): the build from Elektron's .syx and from Elektron's
// .zip, byte for byte out/ID/ID.syx (the build the brick checker passed and the C core rebuilds); the pack's fields;
// the build itself recognised; wrong files refused with the C core's message and no output.
import { readFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import * as core from './dn2patch.js';
import { extract } from './unzip.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const REL = 'CFEE CFTE CFES EFTS CETE CEES EFTE EFES EETS CEEE EFEE EETE EEES EEEE'.split(' ');
const ids = process.argv.length > 2 ? process.argv.slice(2) : REL;
const read = p => new Uint8Array(readFileSync(join(ROOT, p)));
const sha = b => createHash('sha256').update(b).digest('hex');
let failed = 0;
const check = (ok, what) => {
    console.log((ok ? 'PASS  ' : 'FAIL  ') + what);
    if (!ok)
        failed++;
};

const stock = read('elektron-firmware/Digitone_II_OS1_12.syx');
const zip = read('elektron-firmware/Digitone_II_OS1.12_dist.zip');
const fromZip = await extract(zip, core.STOCK_NAME);
check(fromZip && sha(fromZip) === sha(stock), `unzip: ${core.STOCK_NAME} from Elektron's .zip is the stock file ` +
      `(sha256 ${sha(stock).slice(0, 16)}...)`);
check(core.hex(core.sha256(stock)) === sha(stock), 'sha256: the core\'s own matches Node\'s on the stock file');
const old = 'out/firmware/Digitone_II_OS1.11_dist/Digitone_II_OS1.11.syx';
const wrong = [['a truncated 1.12', stock.subarray(0, 1000000), /damaged or incomplete|not Elektron's/],
               ['a text file', new TextEncoder().encode('not the OS\n'), /not Elektron's Digitone II OS 1\.12/],
               ['a zip archive', zip, /\.zip file/]];
if (existsSync(join(ROOT, old)))
    wrong.push(['OS 1.11', read(old), /That's Digitone II OS 1\.11/]);

for (const id of ids) {
    const pack = read(`out/patcher/${id}/patch.bin`), want = read(`out/${id}/${id}.syx`);
    const info = core.info(pack);
    check(info && info.id === id && info.build_sha256 === sha(want) && info.stock_sha256 === sha(stock) &&
          /^Brick check 12 of 12 · /.test(info.record), `${id}: the pack's fields (${info && info.record})`);
    for (const [how, input] of [['the .syx', stock], ['the .zip', fromZip]]) {
        const t0 = performance.now(), r = core.run(pack, input), ms = performance.now() - t0;
        check(r.status === core.OK && r.out.length === want.length && sha(r.out) === sha(want) &&
              r.msg.startsWith("Checked: this is Elektron's original OS 1.12 file. Made " + id + '.syx'),
              `${id}: from ${how}, byte for byte ${id}.syx (${want.length} bytes, sha256 ${sha(want).slice(0, 16)}...) ` +
              `in ${ms.toFixed(0)} ms`);
    }
    check(core.isBuild(pack, want) && !core.isBuild(pack, stock), `${id}: the build itself is recognised as ${id}.syx`);
    const self = core.run(pack, want);
    check(self.status === core.NOT_STOCK && !self.out && self.msg.startsWith(`That's ${id}.syx already`),
          `${id}: its own build refused: "${self.msg.split('\n')[0]}"`);
    for (const [what, input, re] of wrong) {
        const r = core.run(pack, input);
        check(r.status === core.NOT_STOCK && !r.out && re.test(r.msg), `${id}: ${what} refused: "${r.msg.split('\n')[0]}"`);
    }
    const other = REL.find(x => x !== id);
    const r = core.run(pack, read(`out/${other}/${other}.syx`));
    check(r.status === core.NOT_STOCK && /a custom OS/.test(r.msg), `${id}: another build (${other}) refused`);
}
const bad = read(`out/patcher/${ids[0]}/patch.bin`).slice();
bad[bad.length - 1] ^= 0x55;                        // a literal byte flipped: the result's sha256 no longer matches
const r = core.run(bad, stock);
check(r.status === core.FAILED && !r.out, `a corrupted pack: nothing handed over ("${r.msg.split('\n')[0]}")`);
check(core.info(new Uint8Array(10)) === null, 'not a pack: info() says null');
console.log(failed ? `${failed} failed` : `${ids.join(' ')}: all passed`);
process.exit(failed ? 1 : 0);
