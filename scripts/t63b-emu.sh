#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# T6.3 CMP1: boot CMP1's main OS cold in digikit's emulator (the C1EM container: CMP1's main OS with
# GT01's DSP section, so that the boot is GT01's but for the main OS) and run key scripts on it, the
# same way on GT01 (the baseline: T6.5's cold-booted warm snapshot). Same recipe as scripts/t53-emu.sh.
#
#   scripts/t63b-emu.sh ladder C1EM                    cold boot to 400M (emu.checkpoint), then to
#                                                     800M with timers saved (guirun), ~15 min
#   scripts/t63b-emu.sh run NAME SCRIPT [ARGS...]      SCRIPT from NAME's 800M snapshot, headless at
#                                                     --speed 1 (t63b_live.py: t65_live.py + the
#                                                     MIXER view's state and the drawn texts at every
#                                                     screen); NAME=GT01 uses T6.5's
#   scripts/t63b-emu.sh runfrom NAME SNAP SCRIPT SUB [ARGS...]   the same from another snapshot
#
# Outputs in out/T6.3/CMP1/emu/NAME/[SCRIPT name]/. Read-only on digikit; never talks to hardware.
# CMP2 (2026-10-17): T63_STAGE=CMP2 reads out/T6.3/CMP2/NAME.syx (C2EM) and the C2MO map, and writes under
# out/T6.3/CMP2/emu/; without it the script is CMP1's. CMP3 (2026-10-18): T63_STAGE=CMP3 the same for out/T6.3/CMP3/ (C3EM, C3MO).
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dk=$root/vendor/digikit
stock=$root/elektron-firmware/Digitone_II_OS1_12.syx
cmd=${1:?usage}; name=${2:?usage}
stage=${T63_STAGE:-CMP1}
out=$root/out/T6.3/$stage/emu/$name
mkdir -p "$out"
abspath() { echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; }

secs() { if [ "$name" = GT01 ]; then echo "$root/out/T6.5/emu/GT01/sections"; else echo "$out/sections"; fi; }
warm() { if [ "$name" = GT01 ]; then echo "$root/out/T6.5/emu/GT01/warm800M.snap"; else echo "$out/warm800M.snap"; fi; }
syxof() {
  case $name in
    GT01) echo "$root/out/T6.5/GT01.syx" ;;
    *) echo "$root/out/T6.3/$stage/$name.syx" ;;
  esac
}
mapof() {
  case $name in
    GT01) echo "$root/out/T6.5/build/GT01-main.syx.map.json" ;;
    *) if [ "$stage" = CMP2 ]; then echo "$root/out/T6.3/CMP2/C2MO-main.syx.map.json"
       elif [ "$stage" = CMP3 ]; then echo "$root/out/T6.3/CMP3/C3MO-main.syx.map.json"
       else echo "$root/out/T6.3/CMP1/C1MO-main.syx.map.json"; fi ;;  # the main-only link (C1MO; CMP1 review), the same map as C1EM's
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
  DT2_SECTIONS=$(secs) uv run python "$root/scripts/t63b_live.py" "$snap" --syx "$(syxof)" \
    --identify-as "$stock" --speed 1 --script "$script" --out "$sub" --map "$(mapof)" \
    --report "$sub/report.json" --save "$sub/end.snap" "$@" 2>&1 | tee "$sub/run.log" | grep -v '^\[script\]'
  ;;
*) sed -n '3,15p' "$0" >&2; exit 2 ;;
esac
