#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# T6.5: boot a candidate cold in digikit's emulator and run key scripts on it, the same way on
# FTN1 (the baseline: T5.0b's cold-booted warm snapshot). Same recipe as scripts/t50b-emu.sh.
#
#   scripts/t65-emu.sh ladder NAME CAND.syx           cold boot to 400M (emu.checkpoint), then
#                                                     to 800M with timers saved (guirun), ~15 min
#   scripts/t65-emu.sh run NAME SCRIPT [ARGS...]      SCRIPT from NAME's 800M snapshot, headless at
#                                                     --speed 1 (t65_live.py: t19_live.py + memory
#                                                     watches and reads); NAME=FTN1 uses T5.0b's
#   scripts/t65-emu.sh runfrom NAME SNAP SCRIPT SUB [ARGS...]   the same from another snapshot
#
# Outputs in out/T6.5/emu/NAME/[SCRIPT name]/. Read-only on digikit; never talks to hardware.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dk=$root/vendor/digikit
stock=$root/elektron-firmware/Digitone_II_OS1_12.syx
cmd=${1:?usage}; name=${2:?usage}
out=$root/out/T6.5/emu/$name
mkdir -p "$out"
abspath() { echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; }

secs() { if [ "$name" = FTN1 ]; then echo "$root/out/T5.0b/emu/FTN1/sections"; else echo "$out/sections"; fi; }
warm() { if [ "$name" = FTN1 ]; then echo "$root/out/T5.0b/emu/FTN1/warm800M.snap"; else echo "$out/warm800M.snap"; fi; }
syxof() { if [ "$name" = FTN1 ]; then echo "$root/out/T5.0b/FTN1.syx"; else echo "$root/out/T6.5/$name.syx"; fi; }

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
run|runfrom)
  if [ "$cmd" = run ]; then
    script=$(abspath "${3:?usage}"); snap=$(warm); sub=$out/$(basename "$script" .script); shift 3
  else
    snap=$(abspath "${3:?usage}"); script=$(abspath "${4:?usage}"); sub=$out/${5:?usage}; shift 5
  fi
  mkdir -p "$sub"
  cd "$dk"
  DT2_SECTIONS=$(secs) uv run python "$root/scripts/t65_live.py" "$snap" --syx "$(syxof)" \
    --identify-as "$stock" --speed 1 --script "$script" --out "$sub" \
    --report "$sub/report.json" --save "$sub/end.snap" "$@" 2>&1 | tee "$sub/run.log" | grep -v '^\[script\]'
  ;;
*) sed -n '3,12p' "$0" >&2; exit 2 ;;
esac
