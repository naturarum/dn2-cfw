#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Compile the effects with selache's selcc for the ADSP-21569 and count instructions (T3.11).
# Usage: ./sharc_cost.sh OUTDIR   (run from fx/)
set -e
OUT=${1:-../out/T3.11/sharc}
SELCC=../vendor/selache/target/release/selcc
mkdir -p "$OUT"
for f in tapedelay mbcomp flanger; do
    "$SELCC" -proc ADSP-21569 -O1 -S -o "$OUT/$f.s" "$f.c"
done
python3 sharc_cost.py "$OUT/tapedelay.s" "$OUT/mbcomp.s" "$OUT/flanger.s" | tee "$OUT/cost.txt"
# the block-loop experiment: instructions in the loop body of one SVF
"$SELCC" -proc ADSP-21569 -O1 -S -o "$OUT/svf_block.s" sharc_svf_block.c
n=$(awk '/DO .L_doloop_end/{p=1;next} /^.L_doloop_end/{print c+1; exit} p&&/^    /{c++}' "$OUT/svf_block.s")
echo "svf as a block loop (sharc_svf_block.c): $n instructions per sample in the loop body" | tee -a "$OUT/cost.txt"
# exact dynamic counts (2026-10-06): the selcc build run in the T3.6 SHARC interpreter, 2 s of the
# test signal, default settings; also checks the SHARC output against the Mac build. More configurations:
# notes/T3.11 section 12.7. Stock, same interpreter: delay 5,426, chorus 14,457, compressor 16,560-16,640.
# selcc emits register-count shifts only; this counts any immediate-count shift (selas mis-encodes the
# negative ones, which park the core on silicon): it must stay 0
echo "immediate-count shifts in selcc's output: $(grep -h -i -c -E 'shift [^;]* by -?(0x)?[0-9]' "$OUT"/tapedelay.s "$OUT"/mbcomp.s "$OUT"/flanger.s | paste -sd+ - | bc)" | tee -a "$OUT/cost.txt"
echo "dynamic (tools/sharcvm/tests/fxcheck.py, instructions per 32-sample block):" | tee -a "$OUT/cost.txt"
for f in tapedelay mbcomp flanger; do
    python3 ../tools/sharcvm/tests/fxcheck.py --fx $f 2>&1 | sed 's/^/  /' | tee -a "$OUT/cost.txt"
done
