#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# T6.5: run the behaviour scripts on one build, one after the other (each from the build's warm
# 800M snapshot, headless; scripts/t65-emu.sh run). NAME is FTN1 (the baseline), GT00, GT01 or GT1T.
#
#   scripts/t65-tests.sh NAME [tour tour2 tour3 flood plock lfo midiout midiout2 storage random copy]
#   (the Overbridge trace: scripts/t65-emu.sh runfrom NAME WARM scripts/t65-storage.script t65-ob --ob-trace --state)
#
#   tour     scripts/t65-tour.script      --stock-reads
#   tour2    scripts/t65-tour2.script     --stock-reads (the same tour, slower: see t65_mktour.py)
#   tour3    scripts/t65-tour3.script     --stock-reads (the same tour, captures away from the value/label change)
#   random   scripts/t65-random.script    --stock-reads --state (page randomiser)
#   copy     scripts/t65-copypaste.script --stock-reads --state (page, track sound, pattern copy/paste)
#   flood    scripts/t65-midi-flood.script --cache-watch --flush-trace --state
#   plock    scripts/t65-plock.script     (lock records: scripts/t61_locks.py on end.snap)
#   lfo      scripts/t65-lfo.script
#   midiout  scripts/t65-midi-out.script  --midi-log (DIN out)
#   midiout2 scripts/t65-midi-out2.script --midi-log (the same, 0.6 s per detent)
#   storage  scripts/t65-storage.script   --state (then scripts/t65_roundtrip.py on its end.snap)
set -uo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
name=${1:?usage}; shift
what=${*:-tour flood plock lfo midiout storage}
out=$root/out/T6.5/emu/$name
mkdir -p "$out" "$out/t65-midi-out" "$out/t65-midi-out2"
for w in $what; do
  case $w in
    tour)    "$root/scripts/t65-emu.sh" run "$name" "$root/scripts/t65-tour.script" --stock-reads ;;
    tour2)   "$root/scripts/t65-emu.sh" run "$name" "$root/scripts/t65-tour2.script" --stock-reads ;;
    tour3)   "$root/scripts/t65-emu.sh" run "$name" "$root/scripts/t65-tour3.script" --stock-reads ;;
    random)  "$root/scripts/t65-emu.sh" run "$name" "$root/scripts/t65-random.script" --stock-reads --state ;;
    copy)    "$root/scripts/t65-emu.sh" run "$name" "$root/scripts/t65-copypaste.script" --stock-reads --state ;;
    flood)   "$root/scripts/t65-emu.sh" run "$name" "$root/scripts/t65-midi-flood.script" --cache-watch --flush-trace --state ;;
    plock)   "$root/scripts/t65-emu.sh" run "$name" "$root/scripts/t65-plock.script" --stock-reads ;;
    lfo)     "$root/scripts/t65-emu.sh" run "$name" "$root/scripts/t65-lfo.script" --stock-reads ;;
    midiout) "$root/scripts/t65-emu.sh" run "$name" "$root/scripts/t65-midi-out.script" --stock-reads \
               --midi-log "$out/t65-midi-out/midi.txt" ;;
    midiout2) "$root/scripts/t65-emu.sh" run "$name" "$root/scripts/t65-midi-out2.script" --stock-reads \
               --midi-log "$out/t65-midi-out2/midi.txt" ;;
    storage) "$root/scripts/t65-emu.sh" run "$name" "$root/scripts/t65-storage.script" --stock-reads --state ;;
    *) echo "unknown: $w" >&2; exit 2 ;;
  esac > "$out/$w.log" 2>&1
  echo "$name $w: exit $? $(grep -c '' "$out/$w.log") lines"
done
