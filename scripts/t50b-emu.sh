#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# T5.0b: boot a candidate .syx cold in digikit's emulator and run a screen script on it, the same
# way on stock (notes/T5.0b-rename.md). Same recipe as scripts/t50-emu.sh (T5.0) and t19-emu.sh (T1.9).
#
#   scripts/t50b-emu.sh ladder NAME CAND.syx          cold boot to 400M (emu.checkpoint), then to 800M
#                                                     with timers saved (guirun --save-at), ~10 min
#   scripts/t50b-emu.sh run NAME SCRIPT [CAND.syx]    SCRIPT from that 800M snapshot, headless, at
#                                                     --speed 1; the end state is saved as end.snap.
#                                                     NAME=stock uses stock's warm800M.snap
# --speed 1 because script key holds are wall-clock: at --speed 0 a 0.12 s tap lasts long enough in
# emulated time to auto-repeat.
#
# Outputs in out/T5.0b/emu/NAME/[SCRIPT name]/. Read-only on digikit; never talks to hardware.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dk=$root/vendor/digikit
stock=$root/elektron-firmware/Digitone_II_OS1_12.syx
cmd=${1:?usage}; name=${2:?usage}
out=$root/out/T5.0b/emu/$name
mkdir -p "$out"
abspath() { echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; }

secs() { if [ "$name" = stock ]; then echo out/sections/dn2-1.12; else echo "$out/sections"; fi; }
warm() { if [ "$name" = stock ]; then echo "$root/out/T2.4a/warm/warm800M.snap"; else echo "$out/warm800M.snap"; fi; }

case $cmd in
ladder)
  cand=$(abspath "${3:?usage}")
  cd "$dk"
  uv run python -m emu.extract "$cand" -o "$out/sections" > "$out/extract.log" 2>&1
  mkdir -p "$out/snapshots"
  DT2_SECTIONS=$out/sections DT2_SNAPSHOTS=$out/snapshots uv run python -m emu.checkpoint make \
    400000000 "$out/snapshots/boot" "$cand" > "$out/checkpoint.log" 2>&1
  DT2_SECTIONS=$out/sections uv run python tools/guirun.py "$out/snapshots/boot400M.snap" \
    --syx "$cand" --limit 800000000 --save-at "800M:$out/warm800M.snap" > "$out/guirun-warm.log" 2>&1
  ls -la "$out/warm800M.snap"
  ;;
run)
  script=$(abspath "${3:?usage}")
  sub=$out/$(basename "$script" .script); mkdir -p "$sub"
  extra=()
  if [ "$name" = stock ]; then
    syx=$stock
  else
    syx=$(abspath "${4:?usage: run NAME SCRIPT CAND.syx}")
    extra+=(--identify-as "$stock")
  fi
  cd "$dk"
  DT2_SECTIONS=$(secs) uv run python "$root/scripts/t19_live.py" "$(warm)" --syx "$syx" \
    --speed 1 --script "$script" --out "$sub" \
    --report "$sub/report.json" --save "$sub/end.snap" ${extra[@]+"${extra[@]}"} 2>&1 | tee "$sub/run.log" | grep -v '^\[script\]'
  ;;
*) sed -n '3,13p' "$0" >&2; exit 2 ;;
esac
