#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# T5.3: boot SE01's main OS cold in digikit's emulator (the SE1M container: SE01's main OS with GT01's
# DSP section, so the boot is GT01's but for the main OS) and run key scripts on it, the same way on
# GT01 (the baseline: T6.5's cold-booted warm snapshot). Same recipe as scripts/t65-emu.sh.
#
#   scripts/t53-emu.sh ladder SE1M                     cold boot to 400M (emu.checkpoint), then to
#                                                     800M with timers saved (guirun), ~15 min
#   scripts/t53-emu.sh run NAME SCRIPT [ARGS...]      SCRIPT from NAME's 800M snapshot, headless at
#                                                     --speed 1 (t53_live.py: t65_live.py + the delay
#                                                     page's state at every screen); NAME=GT01 uses T6.5's
#   scripts/t53-emu.sh runfrom NAME SNAP SCRIPT SUB [ARGS...]   the same from another snapshot
#
# NAME=SE01 boots the final container out/T5.3/SE01.syx (same main OS, the Space Echo's DSP section).
# Outputs in out/T5.3/SE01-main/emu/NAME/[SCRIPT name]/. Read-only on digikit; never talks to hardware.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dk=$root/vendor/digikit
stock=$root/elektron-firmware/Digitone_II_OS1_12.syx
cmd=${1:?usage}; name=${2:?usage}
out=$root/out/T5.3/SE01-main/emu/$name
mkdir -p "$out"
abspath() { echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; }

secs() { if [ "$name" = GT01 ]; then echo "$root/out/T6.5/emu/GT01/sections"; else echo "$out/sections"; fi; }
warm() { if [ "$name" = GT01 ]; then echo "$root/out/T6.5/emu/GT01/warm800M.snap"; else echo "$out/warm800M.snap"; fi; }
syxof() {
  case $name in
    GT01) echo "$root/out/T6.5/GT01.syx" ;;
    SE01) echo "$root/out/T5.3/SE01.syx" ;;     # the final container (the Space Echo's DSP section)
    *) echo "$root/out/T5.3/SE01-main/$name.syx" ;;
  esac
}

case $cmd in
ladder)
  cand=$(syxof)
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
  mapargs=()
  if [ "$name" != GT01 ]; then mapargs=(--map "$root/out/T5.3/SE01-main/build/SE01-main.syx.map.json"); fi
  DT2_SECTIONS=$(secs) uv run python "$root/scripts/t53_live.py" "$snap" --syx "$(syxof)" \
    --identify-as "$stock" --speed 1 --script "$script" --out "$sub" ${mapargs[@]+"${mapargs[@]}"} \
    --report "$sub/report.json" --save "$sub/end.snap" "$@" 2>&1 | tee "$sub/run.log" | grep -v '^\[script\]'
  ;;
*) sed -n '3,15p' "$0" >&2; exit 2 ;;
esac
