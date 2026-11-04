// SPDX-License-Identifier: GPL-2.0-or-later
// unzip.js - the one file the web patcher needs from Elektron's download: Digitone_II_OS1.12.syx inside
// Digitone_II_OS1.12_dist.zip, found through the zip's central directory and inflated with the browser's own
// DecompressionStream (deflate-raw), on the user's computer. Stored (method 0) and deflated (8) entries; no ZIP64
// (the file is about 2.4 MB). macOS's __MACOSX/ and ._ entries are skipped.

const u16 = (p, i) => p[i] | p[i + 1] << 8;
const u32 = (p, i) => (p[i] | p[i + 1] << 8 | p[i + 2] << 16 | p[i + 3] << 24) >>> 0;

export const isZip = b => b.length >= 4 && b[0] === 0x50 && b[1] === 0x4b && b[2] === 3 && b[3] === 4;

// the zip's entries: [{ name, method, csize, size, at (local header offset) }], or null when it is not a zip
export function entries(b) {
    let e = -1;                                     // the end of central directory record, at most 64 KB from the end
    for (let i = b.length - 22; i >= Math.max(0, b.length - 22 - 65535); i--)
        if (u32(b, i) === 0x06054b50) {
            e = i;
            break;
        }
    if (e < 0)
        return null;
    const count = u16(b, e + 10), out = [];
    let at = u32(b, e + 16);
    for (let k = 0; k < count; k++) {
        if (at + 46 > b.length || u32(b, at) !== 0x02014b50)
            return null;
        const nlen = u16(b, at + 28), xlen = u16(b, at + 30), clen = u16(b, at + 32);
        out.push({ name: new TextDecoder().decode(b.subarray(at + 46, at + 46 + nlen)), method: u16(b, at + 10),
                   csize: u32(b, at + 20), size: u32(b, at + 24), at: u32(b, at + 42) });
        at += 46 + nlen + xlen + clen;
    }
    return out;
}

async function inflate(data) {
    const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
}

// the bytes of the entry whose file name is name (in any folder), or null
export async function extract(b, name) {
    const all = entries(b);
    const e = all && all.find(x => (x.name === name || x.name.endsWith('/' + name)) &&
                                   !x.name.startsWith('__MACOSX/') && !x.name.split('/').pop().startsWith('._'));
    if (!e || e.at + 30 > b.length || u32(b, e.at) !== 0x04034b50)
        return null;
    const start = e.at + 30 + u16(b, e.at + 26) + u16(b, e.at + 28), data = b.subarray(start, start + e.csize);
    if (data.length !== e.csize)
        return null;
    const out = e.method === 0 ? data.slice() : e.method === 8 ? await inflate(data) : null;
    return out && out.length === e.size ? out : null;
}
