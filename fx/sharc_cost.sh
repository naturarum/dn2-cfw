#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Compile the effects with selache's selcc for the ADSP-21569 and count instructions (T3.11).
# Usage: ./sharc_cost.sh OUTDIR   (run from fx/)
set -e
OUT=${1:-../out/T3.11/sharc}
SELCC=../vendor/selache/target/release/selcc
mkdir -p "$OUT"
for f in tapedelay mbcomp flanger spaceecho; do
    "$SELCC" -proc ADSP-21569 -O1 -S -o "$OUT/$f.s" "$f.c"
done
python3 sharc_cost.py "$OUT/tapedelay.s" "$OUT/mbcomp.s" "$OUT/flanger.s" "$OUT/spaceecho.s" | tee "$OUT/cost.txt"
# the block-loop experiment: instructions in the loop body of one SVF
"$SELCC" -proc ADSP-21569 -O1 -S -o "$OUT/svf_block.s" sharc_svf_block.c
n=$(awk '/DO .L_doloop_end/{p=1;next} /^.L_doloop_end/{print c+1; exit} p&&/^    /{c++}' "$OUT/svf_block.s")
echo "svf as a block loop (sharc_svf_block.c): $n instructions per sample in the loop body" | tee -a "$OUT/cost.txt"
# exact dynamic counts (2026-10-06): the selcc build run in the T3.6 SHARC interpreter, 2 s of the
# test signal, default settings; also checks the SHARC output against the Mac build. More configurations:
# notes/T3.11 section 12.7. Stock, same interpreter: delay 5,426, chorus 14,457, compressor 16,560-16,640.
# selcc emits register-count shifts only; this counts any immediate-count shift (selas mis-encodes the
# negative ones, which park the core on silicon): it must stay 0
echo "immediate-count shifts in selcc's output: $(grep -h -i -c -E 'shift [^;]* by -?(0x)?[0-9]' "$OUT"/tapedelay.s "$OUT"/mbcomp.s "$OUT"/flanger.s "$OUT"/spaceecho.s | paste -sd+ - | bc)" | tee -a "$OUT/cost.txt"
echo "dynamic (tools/sharcvm/tests/fxcheck.py, instructions per 32-sample block):" | tee -a "$OUT/cost.txt"
for f in tapedelay mbcomp flanger spaceecho; do
    python3 ../tools/sharcvm/tests/fxcheck.py --fx $f 2>&1 | sed 's/^/  /' | tee -a "$OUT/cost.txt"
done
# the Space Echo (T5.2) by configuration, notes/T5.2 section 6 (the default above: three heads, AGE 64,
# a resonant band, ALT); knob indices: 0 TIME 1 MODE 2 INT 3 AGE 4 CTR 5 BW 6 WID 7 PAN
F=../tools/sharcvm/tests/fxcheck.py
echo "  spaceecho by configuration:" | tee -a "$OUT/cost.txt"
python3 $F --nobuild --fx spaceecho --set 0=23 1=0 2=50 3=0 4=64 5=127 6=127 7=0 2>&1 | sed 's/^/    one head, clean, no filter, MONO: /' | tee -a "$OUT/cost.txt"
python3 $F --nobuild --fx spaceecho --set 0=23 1=0 2=50 3=64 4=64 5=40 6=100 7=1 2>&1 | sed 's/^/    one head, AGE 64, band, ALT: /' | tee -a "$OUT/cost.txt"
python3 $F --nobuild --fx spaceecho --set 0=23 1=3 2=50 3=64 4=64 5=40 6=100 7=1 2>&1 | sed 's/^/    two heads, AGE 64, band, ALT: /' | tee -a "$OUT/cost.txt"
python3 $F --nobuild --fx spaceecho --set 0=23 1=6 2=60 3=127 4=64 5=20 6=127 7=2 --at 10:0=11 20:1=4 30:5=90 40:4=100 50:7=1 60:1=6 2>&1 | sed 's/^/    worst: three heads, AGE 127, RND, knob moves: /' | tee -a "$OUT/cost.txt"
