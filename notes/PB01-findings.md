# PB01: findings before the build stopped (2026-10-23)

The PB01 build agent's output was halted by a safety classifier during its exploration phase. It wrote no files and
built nothing. These are the facts it had settled, kept here so a later attempt starts from them. Marks as in the
other notes: none of this was run on the unit.

A second attempt (2026-10-23, the owner asked for it) was halted the same way, in its reading phase, before writing
anything. It confirmed against FL01's build.json that this layout holds: `0x2e2800-0x2e2900` and code2 at
`0x310000-0x315f8c` are free in FL01.

## Candidate ranges, corrected

| range | room | verdict |
|---|---:|---|
| L1 block 1 above `0x2e4f46`, below `0x2e8000` (build.py keeps the edge for a 32 KB DM cache), minus FL01's data and save area | about 12 KB (not the 45 KB of the static map) | candidate |
| L1 block 3's tail | 16 KB | **out**: brickcheck's cache list and the start-up's cache setup put the 16 KB I-cache there |
| block 2 `0x315f8c-0x318000` | 8 KB | open: depends on the block-2 PM cache size (16 KB or 32 KB), not settled |
| L2 range A (SC01's echo knob code) | | **out**: in FL01 it holds the stock delay, which FL01 runs |
| L2 B, C, D (SC01) | | dead in FL01 (SC01's deadcode.json: B only from C, C only from the hooked chorus call, D only from the hooked compressor call) |
| L2 `0x2008823c-0x200918c0` | 38,532 B | candidate |
| L2 `0x200918c0-0x200c2000` | | **out**: build.py's forbidden list ("keep data out") |
| L2 `0x200c2000-0x200fa000` | 229,376 B | candidate |

Section 7 can grow by about 150 KB before its 1 MiB limit, so watermarks over L2 must be spread pages, not a fill.

## The planned design (not written)

- Probe driver in the proven `0x310000-0x315f8c`, called at the end of the compressor's process step in code4.
- Its state at `0x2e2800`, where CP2F's scanner state was.
- Probes as small selcc leaf functions in each candidate range; watermark payload pages; a crumb before each call.
- Readout: CP2F's encoding on the four bars (LOW, MID, HIGH, the right bar through the stock getter).
