# MR02: MRB, the morphing reverb on a budget (step 1: C, cost, renders)

2026-11-09. The owner, after MR01's cost (87k-158k): "we should aim to build a reverb that is around the stock
reverb in cpu use." Marks: **[verified]** = run here (Mac build, sharcvm), file named; **[inferred]** = reasoned.
Nothing flashed, committed or pushed. Evidence: `out/morph/mrbcheck_v2.json` (39 cases), `out/morph/ab/`
(renders, `compare.json`, `impcmp.json`, the A/B page `index.html`), version 1 kept in `out/morph/mrb_v1.c`.

## 1. Design (`fx/mrb.c`, `fx/mrb.h`)

The same idea and page as MRPH (A PRE, B DEC, C CHAR room-hall-spring, D SIZE, E HPF, F TONE; the three
character end points are MRPH's, lines 0-3 of its eight), redesigned for selcc's cost:

- the tank at 24 kHz (16 samples a block), the spring's half-bands down (one allpass a branch) and up (two);
- mono in (L + R), stereo out; 4 lines, two levels of rotations; 4 diffusers; 3 early taps a side;
- the spring's dispersion on lines 0 and 1, 6 stretched allpasses (z^-4) each;
- TONE's low-pass on the input (a linear tank: the same as on the output, one filter instead of two);
- every delay read once a block, linear, the fraction ramped; delays move at most a sample a block (a knob
  turn bends the pitch at most about a semitone instead of clicking);
- coefficients only when a knob moves, a quarter a block; the absorption pole squared (the corner of
  MRPH's 48 kHz filter at 24 kHz).

## 2. Cost [verified: mrphcheck `--src mrb`, 39 cases x 2 s, every one bit-identical SHARC = Mac, state too]

| | instructions a block | DSP load |
|---|---:|---:|
| stock reverb | 14,563 | 2.9 % |
| **MRB, knobs still (every CHAR)** | **19,016** | 3.8 % |
| MRB, knobs moving (mean) | 20,570-20,880 | 4.1-4.2 % |
| MRB with 4 dispersion stages (`-DMB_STAGES=4`), still | 17,922 | 3.6 % |
| the spring (SPNG) | 22,108 | 4.4 % |
| MRPH, the full port | 87,000-158,000 | 17-32 % |

Load = instructions / 499k (the block at 1,500 blocks/s). Where it goes (spring end, still): early taps +
pre-delay + diffusers 5.3k, tank 4.0k, dispersion 3.3k, up-sampler 1.9k, input 1.9k, control 1.2k, line reads
1.0k. Version 1 (separate loops, everything every block) was 30.3k. Code 22,536 B (selcc -O1, before any
compiler-fix pass), stack 592 B, state 104 KB of the stock reverb's 364 KB.

## 3. Against the original [verified: `out/morph/ab/`]

- Decay per band (impulse, against the float port, which matches the original to rounding): 250 Hz and 1 kHz
  within 2-7 %, 4 kHz 10-15 % shorter (hall 2.55 -> 2.25 s).
- Loudness (RMS, same knobs): within about +-2 dB on clave, pad, chords and drums, except room on pad and chords
  (-5 dB) and small on drums (-5 dB): mostly the mono input (a wide stereo source loses up to 3 dB) [inferred].
- Echo density (normalised, 50-500 ms): the same (1.07-1.18 against 1.12-1.16); room denser (1.39).
- The tail stops at about 11 kHz (the 24 kHz tank).

## 4. Three more selcc miscompiles [verified, each fixed and checked bit-identical]

- **A float passed on the stack, used in a loop**: `mb_diff`'s fifth argument was kept in R1, and R1 was reused
  for `g * u` inside the loop: the coefficient was 0 after the first sample. Kernels now take at most three
  arguments, or copy a stack argument to a local.
- **`p + a + (b & m)`**: the last term was not scaled to floats (`s->mem + s->pseg + (s->t & MASK)` wrote the
  pre-delay 12 samples off). Offsets are summed as integers, then added to a local pointer once.
- **A void function ending in a `switch` gets no return**: `mrb_set` (and MRPH's `morph_set`, and the harness's
  `mrphdrv`) ran on into the next function; on the SHARC every knob set also snapped. This was MR01's open
  moving-knob mismatch: `morph_set` fixed the same way, its moving case is now bit-identical. A scan of every
  selcc `.s` under `out/` found the pattern in no code that ran on the unit (the DC builds' shared epilogues and
  the test drivers only).

## 5. Open

- The owner's ear (the A/B page). Trims if wanted, each measured or from the profile: 4 dispersion stages
  -1.1k (weaker drip), the up-sampler with one allpass a branch about -1k (images at 13-14 kHz -8 to -14 dB),
  2 early taps a side about -0.7k, 3 diffusers about -0.4k [inferred for the last three].
- Code size: 22.5 KB, more than the spring's 13.6 KB in code2; fitting it in the proven code space is the
  build step's work.
- Like the spring, MRB's output reaches exact 0 in silence (the flushes); see SP02 section 8 before a unit build.
