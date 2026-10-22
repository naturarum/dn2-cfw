#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# SC01: boot SC01's main OS cold in digikit's emulator and run key scripts on it, as scripts/t53-emu.sh (SE01) and
# scripts/t63b-emu.sh (CMP1..CMP3) do. NAME = SCEM (out/SC01/main/SCEM.syx: SC01's main OS with GT01's DSP section, the
# emulator container) or SC01 (out/SC01/SC01.syx, the final container: section 7 is the SC01 DSP; the emulator runs no
# DSP, so it matters only to the ColdFire's DSP loader).
#
#   scripts/sc01-emu.sh ladder NAME                       cold boot to 400M (emu.checkpoint), then to 800M with
#                                                         timers saved (guirun)
#   scripts/sc01-emu.sh run NAME DRIVER SCRIPT [ARGS...]  SCRIPT from NAME's 800M snapshot, headless at --speed 1;
#                                                         DRIVER t53_live (the Echo page's state records) or t63b_live
#                                                         (the MIXER view, the drawn texts, the band meters)
#
# Outputs in out/SC01/emu/NAME/[SCRIPT name]/. Read-only on digikit; never talks to hardware.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dk=$root/vendor/digikit
stock=$root/elektron-firmware/Digitone_II_OS1_12.syx
cmd=${1:?usage}; name=${2:?usage}
out=$root/out/SC01/emu/$name
mkdir -p "$out"
abspath() { echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; }
case $name in
  SCEM) syx=$root/out/SC01/main/SCEM.syx ;;
  SC01) syx=$root/out/SC01/SC01.syx ;;
  *) echo "NAME: SCEM or SC01" >&2; exit 2 ;;
esac
map=$root/out/SC01/main/SCMO-main.syx.map.json

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
  cd "$dk"
  DT2_SECTIONS=$out/sections uv run python "$root/scripts/$driver.py" "$out/warm800M.snap" --syx "$syx" \
    --identify-as "$stock" --speed 1 --script "$script" --out "$sub" --map "$map" \
    --report "$sub/report.json" --save "$sub/end.snap" "$@" 2>&1 | tee "$sub/run.log" | grep -v '^\[script\]'
  ;;
*) sed -n '3,15p' "$0" >&2; exit 2 ;;
esac
