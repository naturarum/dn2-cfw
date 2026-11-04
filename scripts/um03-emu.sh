#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# UM03: boot UM03.syx (the final container) cold in digikit's emulator and run key scripts on it, as
# scripts/ba05-emu.sh does for BA05 (the emulator runs no DSP, so section 7 matters only to the ColdFire's DSP loader).
#
#   scripts/um03-emu.sh ladder                      cold boot to 400M (emu.checkpoint), then to 800M with timers saved
#   scripts/um03-emu.sh run DRIVER SCRIPT [ARGS...]  SCRIPT from the 800M snapshot (or START=SNAP), headless at
#                                                   --speed 1; DRIVER t19_live (screens only), t63b_live (the drawn
#                                                   texts as well) or ts01_live (the parameter mirror, a block a line)
#
# Outputs in out/UM03/emu/[SCRIPT name]/. Read-only on digikit; never talks to hardware.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dk=$root/vendor/digikit
stock=$root/elektron-firmware/Digitone_II_OS1_12.syx
cmd=${1:?usage}
out=$root/out/UM03/emu
mkdir -p "$out"
abspath() { echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; }
syx=$root/out/UM03/UM03.syx
map=$root/out/UM03/build/UM03-main.syx.map.json

case $cmd in
ladder)
  cd "$dk"
  uv run python -m emu.extract "$syx" -o "$out/sections" > "$out/extract.log" 2>&1
  mkdir -p "$out/snapshots"
  DT2_SECTIONS=$out/sections DT2_SNAPSHOTS=$out/snapshots uv run python -m emu.checkpoint make \
    400000000 "$out/snapshots/boot" "$syx" > "$out/checkpoint.log" 2>&1
  DT2_SECTIONS=$out/sections uv run python tools/guirun.py "$out/snapshots/boot400M.snap" \
    --syx "$syx" --limit 800000000 --save-at "800M:$out/warm800M.snap" > "$out/guirun-warm.log" 2>&1
  ls -la "$out/warm800M.snap"
  ;;
run)
  driver=${2:?usage}; script=$(abspath "${3:?usage}"); shift 3
  sub=$out/$(basename "$script" .script)
  mkdir -p "$sub"
  extra=()
  [ "$driver" = t19_live ] || extra+=(--map "$map")
  cd "$dk"
  DT2_SECTIONS=$out/sections uv run python "$root/scripts/$driver.py" "${START:-$out/warm800M.snap}" --syx "$syx" \
    --identify-as "$stock" --speed 1 --script "$script" --out "$sub" \
    --report "$sub/report.json" --save "$sub/end.snap" ${extra[@]+"${extra[@]}"} "$@" 2>&1 | tee "$sub/run.log" | grep -v '^\[script\]'
  ;;
*) sed -n '3,9p' "$0" >&2; exit 2 ;;
esac
