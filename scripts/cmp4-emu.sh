#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# CMP4: boot CMP4's main OS cold in digikit's emulator and run key scripts on it, as scripts/sc01-emu.sh (SC01) and
# scripts/t63b-emu.sh (CMP1..CMP3) do. NAME = C4EM (out/CMP4/main/C4EM.syx: CMP4's main OS with GT01's DSP section, the
# emulator container) or CMP4 (out/CMP4/CMP4.syx, the final container: section 7 is the CMP4 DSP; the emulator runs no
# DSP, so it matters only to the ColdFire's DSP loader).
#
#   scripts/cmp4-emu.sh ladder NAME                       cold boot to 400M (emu.checkpoint), then to 800M with
#                                                         timers saved (guirun)
#   scripts/cmp4-emu.sh run NAME DRIVER SCRIPT [ARGS...]  SCRIPT from NAME's 800M snapshot, headless at --speed 1;
#                                                         DRIVER t19_live (screens only, as t50b-emu.sh's tour) or
#                                                         t63b_live (the MIXER view, the drawn texts, the band meters)
#
# Outputs in out/CMP4/emu/NAME/[SCRIPT name]/. Read-only on digikit; never talks to hardware.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dk=$root/vendor/digikit
stock=$root/elektron-firmware/Digitone_II_OS1_12.syx
cmd=${1:?usage}; name=${2:?usage}
out=$root/out/CMP4/emu/$name
mkdir -p "$out"
abspath() { echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; }
case $name in
  C4EM) syx=$root/out/CMP4/main/C4EM.syx ;;
  CMP4) syx=$root/out/CMP4/CMP4.syx ;;
  *) echo "NAME: C4EM or CMP4" >&2; exit 2 ;;
esac
map=$root/out/CMP4/main/C4MO-main.syx.map.json

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
  driver=${3:?usage}; script=$(abspath "${4:?usage}"); shift 4
  sub=$out/$(basename "$script" .script)
  mkdir -p "$sub"
  extra=()
  [ "$driver" = t19_live ] || extra+=(--map "$map")
  cd "$dk"
  DT2_SECTIONS=$out/sections uv run python "$root/scripts/$driver.py" "$out/warm800M.snap" --syx "$syx" \
    --identify-as "$stock" --speed 1 --script "$script" --out "$sub" \
    --report "$sub/report.json" --save "$sub/end.snap" ${extra[@]+"${extra[@]}"} "$@" 2>&1 | tee "$sub/run.log" | grep -v '^\[script\]'
  ;;
*) sed -n '3,15p' "$0" >&2; exit 2 ;;
esac
