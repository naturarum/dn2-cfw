# SPNG v2: the Mac prototype (2026-10-26)

The spring reverb after the owner's first listen on the unit (SP01: "stable ... not there yet, but promising"; the
tail cycles and it is quiet). Mac only: no recipe builds these files, and `fx/spring.c` is still v1, the source SP01
compiles. The findings and numbers: notes/T5.5-spring-reverb.md section 9.

| file | what |
|---|---|
| `spring.c`, `spring.h` | v1 with the changes behind macros; with every macro at its default the output is v1's bit for bit |
| `spmix.h`, `mkmix.py` | the cross-feed matrix: `python3 mkmix.py 0.5236 > spmix.h` (30 degrees) |
| `build.sh OUT` | fxrender with v2's settings (the A/B page's v2 column, checked bit for bit) |
| `analysis/` | stdlib-only measurements: `splevel.py` (BS.1770 loudness), `cycle.py` (how much the tail repeats itself, lags 16-600 ms), `tone.py` (RT60 and level per band), `ratios.py` (where three echo trains pile up), `flutter.py` (the first, short-lag metric) |

The macros: `SPV2_R2`, `SPV2_R3` the second and third spring's length ratios (v2 1.21, 1.32; v1 1.173, 1.387);
`SPV2_COUPLE` the cross-feed (0 none, 1 Householder, 2 `spmix.h`); `SPV2_MD` the random wobble's depth in round-trip
samples at 12 kHz (v2 3.0) and `SPV2_MK` its smoother; `SPV2_INTERP` the loop read (0 linear, 1 Catmull-Rom); `SP_OUT`
the wet gain (v2 2.4, v1 1.4). `SPV2_DWEL` DWEL's law: 0 v1's, 1 the level-held first try (0..+23.6 dB, `SPV2_DPA`
of it made up), 2 `SPV2_DLO`..`SPV2_DHI` dB with `SPV2_DAGC` 1 for the measured make-up (`SPV2_DATC` its meters,
`SPV2_DAMX` its cap); the A/B page's "more marked" rows are 2 with 8 and 32.

Before this goes on the DSP: freeze SP01's spring sources (as DT01 did in `tools/dspsplice/src/dt01/`) so SP01 still
rebuilds byte for byte, and keep to selcc's limits (`spmix.h` has negative constants in a static table).
