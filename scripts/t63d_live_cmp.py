#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP3: a key script's run on C3EM as built (comp-link 0.2's storage sites hooked) against the same run with
the three sites given their stock bytes back (scripts/t63b_live.py --stock-sites), both with --state and the same
--set-k preset of k 70..89 (the prototype's out/T6.4/store-frame/scripts/live_cmp.py, for the raw encoding).

    python3 scripts/t63d_live_cmp.py OUT.json NAME:HOOK_DIR:BASE_DIR [...]

For each pair: the live FX setup (k 0..100), the frame and the raw mirror equal; every working-image kit record
(FX record, 0xa0 bytes, kits 0..128) equal, or differing only at +0x60..+0x97 (our 27 words and the check word);
each record that carries our words decodes to values in range, and the current kit's (if saved) to its live k 70..96.
"""
import json
import os
import struct
import sys

VMAX = [127, 8, 127, 127, 127, 127, 127, 8, 127, 127, 127, 127, 127, 8, 127, 127, 127, 127, 127, 127] + [127] * 7


def cmp_pair(hd, bd):
    h, b = (json.load(open(os.path.join(d, 'state.json'))) for d in (hd, bd))
    r = {'kit': [h['kit'], b['kit']], 'live_fx_equal': h['fx_live'] == b['fx_live'],
         'live_k70_89': [v >> 8 for v in h['fx_live'][70:90]], 'frame_equal': h['frame'] == b['frame'],
         'raw_mirror_equal': h['raw_mirror'] == b['raw_mirror']}
    same, ours, other, recs = 0, 0, [], {}
    for i, (x, y) in enumerate(zip(h['kit_fx_records'], b['kit_fx_records'])):
        if x == y:
            same += 1
            continue
        xb, yb = bytes.fromhex(x), bytes.fromhex(y)
        offs = [o for o in range(0, 0xa0, 2) if xb[o:o + 2] != yb[o:o + 2]]
        if all(0x60 <= o < 0x98 for o in offs):
            ours += 1
            w = struct.unpack('>28H', xb[0x60:0x98])
            recs[i] = {'check_word': '%04x' % w[27], 'k70_89': [v >> 8 for v in w[:20]],
                       'in_range': w[27] == 0xC3B1 and all(v <= m << 8 for v, m in zip(w[:27], VMAX)),
                       'equals_live_k70_96': list(w[:27]) == h['fx_live'][70:97]}
        else:
            other.append((i, ['+%#x' % o for o in offs[:8]]))
    r.update({'records_identical': same, 'records_differing_only_at_our_words': ours,
              'records_differing_elsewhere': other, 'our_records': recs})
    r['ok'] = (r['live_fx_equal'] and r['frame_equal'] and r['raw_mirror_equal'] and not other
               and all(v['in_range'] for v in recs.values()) and ours >= 1)
    return r


def main():
    out = sys.argv[1]
    res = {}
    for arg in sys.argv[2:]:
        name, hd, bd = arg.split(':')
        res[name] = cmp_pair(hd, bd)
        print(name, {k: v for k, v in res[name].items() if k != 'our_records'})
        for i, v in res[name]['our_records'].items():
            print('   kit record %d: %s' % (i, v))
    res['VERDICT'] = 'PASS' if all(v['ok'] for v in res.values() if isinstance(v, dict)) else 'FAIL'
    json.dump(res, open(out, 'w'), indent=1)
    print('VERDICT', res['VERDICT'])


if __name__ == '__main__':
    main()
