# MR01: MRPH, the owner's Beads morph reverb ported to the DN2 (step 1: port, fidelity, cost)

Owner, 2026-11-09: port the morphing reverb of their Beads firmware as a "budget-friendly" reverb for the reverb
slot, in place of the shelved spring; "we could have fixed diffusion so cpu budget stays roughly the same? ducking
can go. and yeah we could try the cpu budget fixes you suggest, if you think we really need them". Marks:
**[verified]** = run, file named; **[inferred]** = estimated. Nothing built for the unit, nothing committed.

## 1. The port [verified]

- `fx/morph.h`, `fx/morph.c`: C99, float only, no libc, no division (reciprocals and square roots are Newton
  steps, the same operations in both builds). Source: the owner's eurorack fork (naturarum/eurorack), branch
  beads-reverb, `beads/dsp/fx/morph_reverb.h` v2 (uncommitted there on 11-09, sha256 `b272fd5f...`; owner's
  copyright, MIT). Diffusion fixed at the middle (no wash), no ducking, float delay lines in one 65,536-float
  ring, 32-sample blocks; added PRE (0..120 ms) and HPF (20 Hz..1 kHz above the character's own). Page as
  proposed: A PRE, B DEC, C CHAR, D SIZE, E HPF, F TONE. `sizeof(Morph)` 267,540 B (the stock reverb's DDR
  region is 364,820 B).
- The block loops run one line over the whole block before the next; the original packs its lines two slots
  apart, which only holds sample by sample, so each line here has a guard of a block (found as a diffuser
  reading the pre-delay's fresh writes).

## 2. Against the original [verified: `out/morph/ref/fidelity.sh`]

A copy of the original with 32-sample blocks (`out/morph/ref/inc/`, the only change) against the port on the
dry clave and pad, 9 settings (room, hall, spring, two blends, SIZE 0 and 1, TONE 0 and 1): with `MR_Q16`
(the module's 16-bit lines) the difference is -50 to -78 dB re the signal (rounding); the float port the same,
except a loud pad at SIZE 0, where the module's 16-bit lines clip and the float lines don't (-37 dB).

## 3. On the DSP compiler [verified: `tools/sharcvm/tests/mrphcheck.py`, harness `tools/sharcvm/harness/mrphdrv.c`]

| | instructions per 32-sample block |
|---|---:|
| stock reverb (T5.5) | 14,563 |
| SPNG, the spring (T5.5) | 22,108 |
| MRPH as ported (hall) | 98,863 |
| MRPH, loops with constant trip counts and locals (hall / CHAR 96 / spring end) | 87,115 / 157,639 / 136,965 |
| ... worst block, knobs moving | 199,595 |
| ... with `MR_EARLY_FIXED` (spring end) | 79,786 |

Code 23,880 B (the spring 15,550 B), stack 1,288 B. Profile (hall, `out/morph/prof/`): the per-sample matrix and
writes 26.5k, the tank reads 12.8k (50 instructions per interpolated read), the diffusers 12.7k, the early taps
22k, input filters 5.8k, control 8k. selcc keeps every variable in memory: about 4-5 instructions per float
operation (T3.11 section 11.4); the spring stays cheap by running its tank at 12 kHz.

## 4. Two selcc miscompiles [verified]

- **New (the tenth?):** in `mr_blend`, pointers chosen in an `if` and a value computed from a local after it:
  selcc kept CHAR in R0, loaded a table address into R0 inside the `if`, then computed with R0 as CHAR; CHAR
  above 0.5 gave the hall. Rewritten as two loops; all 36 static cases now bit-identical SHARC = Mac, state too.
- **Found 11-09 (MR02):** the three moving-knob cases differed (0.7-4.4 dB re peak) because selcc gave
  `morph_set`, a void function ending in a `switch`, no return: on the SHARC every knob set ran on into
  `morph_snap`. `morph_set` is now an if-chain; the moving case is bit-identical (SHARC = Mac, state too).

## 5. Where this leaves the port [inferred]

A faithful port costs 4-7x the spring on this compiler. Options put to the owner (11-09): the tank at 24 kHz
with the cuts (fixed early taps, 12 spring stages, coefficients only when a knob moves), about 35-45k; at 12 kHz
like the spring, about 20-25k but dark; hand-written assembly for the hot loops (new ground for this project);
or a smaller design.
