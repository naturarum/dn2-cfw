#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# T5.0: boot a candidate .syx cold in digikit's emulator and take the FX-page screens
# (notes/T5.0-flanger-page-ui.md). Same recipe as scripts/t19-emu.sh (T1.9).
#
#   scripts/t50-emu.sh ladder NAME CAND.syx   cold boot to 400M (emu.checkpoint), then to 800M
#                                             with timers saved (guirun --save-at), ~10 min
#   scripts/t50-emu.sh pages NAME [CAND.syx]  scripts/t50-pages.script from that 800M snapshot
#                                             (track FX page, SEND FX 1-3, knob turns);
#                                             NAME=stock uses stock's warm800M.snap
#   scripts/t50-emu.sh page2 NAME [CAND.syx]  scripts/t50-page2.script (SEND FX page stepping,
#                                             encoder turns on the last page)
# Both run at --speed 1: script key holds are wall-clock, and at --speed 0 a 0.12 s tap lasts
# long enough in emulated time to auto-repeat (UP/DOWN then skip pages).
#
# Outputs in out/T5.0/emu/NAME/. Read-only on digikit; never talks to hardware.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dk=$root/vendor/digikit
stock=$root/elektron-firmware/Digitone_II_OS1_12.syx
cmd=${1:?usage}; name=${2:?usage}; cand=${3:-}
out=$root/out/T5.0/emu/$name
mkdir -p "$out"
[ -n "$cand" ] && cand=$(cd "$(dirname "$cand")" && pwd)/$(basename "$cand")

secs() { if [ "$name" = stock ]; then echo out/sections/dn2-1.12; else echo "$out/sections"; fi; }
warm() { if [ "$name" = stock ]; then echo "$root/out/T2.4a/warm/warm800M.snap"; else echo "$out/warm800M.snap"; fi; }
syx() { if [ "$name" = stock ]; then echo "$stock"; else echo "$cand"; fi; }

case $cmd in
ladder)
  cd "$dk"
  uv run python -m emu.extract "$cand" -o "$out/sections" > "$out/extract.log" 2>&1
  mkdir -p "$out/snapshots"
  DT2_SECTIONS=$out/sections DT2_SNAPSHOTS=$out/snapshots uv run python -m emu.checkpoint make \
    400000000 "$out/snapshots/boot" "$cand" > "$out/checkpoint.log" 2>&1
  DT2_SECTIONS=$out/sections uv run python tools/guirun.py "$out/snapshots/boot400M.snap" \
    --syx "$cand" --limit 800000000 --save-at "800M:$out/warm800M.snap" > "$out/guirun-warm.log" 2>&1
  ls -la "$out/warm800M.snap"
  ;;
pages|page2)
  sub=$out/$cmd; mkdir -p "$sub"
  script=$root/scripts/t50-pages.script
  [ "$cmd" = page2 ] && script=$root/scripts/t50-page2.script
  extra=()
  if [ "$name" != stock ]; then
    m=$(syx).map.json
    [ -f "$m" ] && extra=(--read-syms "$m:${T50_SYMS:-flui_state,flui_ticks}")
    extra+=(--identify-as "$stock")
  fi
  cd "$dk"
  DT2_SECTIONS=$(secs) uv run python "$root/scripts/t19_live.py" "$(warm)" --syx "$(syx)" \
    --speed 1 --script "$script" --out "$sub" \
    --report "$sub/report.json" --save "$sub/end.snap" ${extra[@]+"${extra[@]}"} 2>&1 | tee "$sub/run.log" | grep -v '^\[script\]'
  ;;
*) sed -n '2,12p' "$0" >&2; exit 2 ;;
esac
