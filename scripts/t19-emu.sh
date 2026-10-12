#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# T1.9: boot a candidate .syx cold in digikit's emulator and play the MIDI proof on it,
# the same way as stock (notes/T1.9-dn2-core.md, "Test in the emulator").
#
#   scripts/t19-emu.sh ladder NAME CAND.syx   cold boot to 400M (emu.checkpoint), then to 800M
#                                             with timers saved (guirun --save-at), like
#                                             out/T2.4a/warm/warm800M.snap for stock (~10 min)
#   scripts/t19-emu.sh proof NAME [CAND.syx]  the T2.4c MIDI proof script (keys only: MIDI sync
#                                             on, track 1 = MIDI ch 1, trigs 1 5 9 13, PLAY 4 s,
#                                             STOP) from that 800M snapshot, with the MIDI log.
#                                             NAME=stock uses stock's warm800M.snap
#   scripts/t19-emu.sh ui NAME CAND.syx       screens: main, SETTINGS, PERSONALIZE (ui.script)
#
# Outputs in out/T1.9/emu/NAME/. Read-only on digikit; never talks to hardware.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dk=$root/vendor/digikit
stock=$root/elektron-firmware/Digitone_II_OS1_12.syx
cmd=${1:?usage}; name=${2:?usage}; cand=${3:-}
out=$root/out/T1.9/emu/$name
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
proof|ui)
  script=$root/out/T2.4c/proof/midi-proof.script
  [ "$cmd" = ui ] && script=$root/scripts/t19-ui.script
  sub=$out/$cmd; mkdir -p "$sub"
  extra=()
  if [ "$name" != stock ]; then
    m=$(syx).map.json
    [ -f "$m" ] && extra=(--read-syms "$m:${T19_SYMS:-fast_done}")
    extra+=(--identify-as "$stock")
  fi
  cd "$dk"
  DT2_SECTIONS=$(secs) uv run python "$root/scripts/t19_live.py" "$(warm)" --syx "$(syx)" \
    --ssi0-hz --ips 150e6 --speed 0 --script "$script" --out "$sub" \
    --midi-log "$sub/midi.txt" --midi-file "$sub/out.mid" --midi-bpm 81 \
    --report "$sub/report.json" ${extra[@]+"${extra[@]}"} 2>&1 | tee "$sub/run.log" | grep -v '^\[script\]'
  ;;
*) sed -n '2,15p' "$0" >&2; exit 2 ;;
esac
