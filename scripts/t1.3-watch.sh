#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# T1.3: write watches over the Digitone II 1.12 candidate free areas, in
# digikit's emulator. Two runs, both read-only on digikit:
#   session  from the cold-boot rung boot400M, ~800M instructions of guirun
#            with key presses and encoder turns (scripts/t1.3_session_watch.py)
#   boot     a cold boot from the main OS's reset to 400M (tools/bootwatch.py,
#            through scripts/t1.3_bootwatch.py so the run ends with its summary)
# Output: out/T1.3/session.log, session-*.png, session799M.snap,
#         out/T1.3/boot.log, boot.json.
#
#   scripts/t1.3-watch.sh session|boot|all
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DK="$ROOT/vendor/digikit"
SYX="$ROOT/elektron-firmware/Digitone_II_OS1_12.syx"
OUT="$ROOT/out/T1.3"
mkdir -p "$OUT"
export DT2_SECTIONS="$DK/out/sections/dn2-1.12"

# name=lo:len, the candidate areas and their neighbours (notes/T1.3-dn2-memory.md)
WATCHES="
--watch 0x466b84d0:0x7b30=ddr-above-clear
--watch 0x466c0000:0x50000=dsp-bufs-cached-view
--watch 0x46710000:0x18e0000=ddr-candidate
--watch 0x47ff0000:0x10000=stack-top-64K
--watch 0x4e6b84d0:0x7b30=alias-above-clear
--watch 0x4e710000:0x18f0000=alias-above-bufs
--watch 0x8000f980:0x64=sram-table-end
--watch 0x8000f9e4:0x61c=sram-candidate
--watch 0x4664bcdc:4=control-current-tcb
"

session() {
  # times are instructions from the snapshot; the main screen is up by +300M (notes/T1.1)
  IN=""
  for e in \
    330M:press:25 332M:release:25 334M:press:29 336M:release:29 \
    338M:press:33 340M:release:33 342M:press:37 344M:release:37 \
    350M:press:20 352M:release:20 \
    370M:press:2 372M:release:2 380M:press:3 382M:release:3 \
    390M:press:4 392M:release:4 400M:press:5 402M:release:5 \
    410M:press:6 412M:release:6 420M:press:21 422M:release:21 \
    430M:press:8 432M:release:8 440M:press:14 442M:release:14 \
    450M:press:14 452M:release:14 460M:press:10 462M:release:10 \
    470M:press:12 472M:release:12 480M:press:12 482M:release:12 \
    490M:press:12 492M:release:12 \
    500M:press:18 502M:release:18 510M:press:25 512M:release:25 \
    515M:press:27 517M:release:27 520M:press:29 522M:release:29 \
    530M:press:18 532M:release:18 \
    540M:press:16 542M:press:26 544M:release:26 546M:release:16 \
    550M:press:26 552M:release:26 560M:press:20 562M:release:20 \
    640M:press:7 642M:release:7 650M:press:12 652M:release:12 \
    700M:press:21 702M:release:21 710M:press:23 712M:release:23 \
    720M:press:12 722M:release:12; do
    IN="$IN --input $e"
  done
  # encoder turns: [0x30|channel, delta]; A +5, B -4, C +7, D +3, E +2, LEVEL +10, H +10
  FEED="--feed 360M:3005 --feed 365M:31fc --feed 375M:3207 --feed 385M:3303 \
        --feed 405M:3402 --feed 620M:380a --feed 625M:370a"
  cd "$DK"
  # shellcheck disable=SC2086
  uv run python "$ROOT/scripts/t1.3_session_watch.py" out/snapshots/dn2-1.12/boot400M.snap \
    --syx "$SYX" --limit 800000000 --watch-max 8 $WATCHES $IN $FEED \
    --png-at 320M:"$OUT/session-320M.png" --png-at 425M:"$OUT/session-425M.png" \
    --png-at 495M:"$OUT/session-495M.png" --png-at 600M:"$OUT/session-600M.png" \
    --png-at 790M:"$OUT/session-790M.png" \
    --save-at 799000000:"$OUT/session799M.snap" > "$OUT/session.log" 2>&1
}

boot() {
  cd "$DK"
  # shellcheck disable=SC2086
  DT2_SYX="$SYX" uv run python "$ROOT/scripts/t1.3_bootwatch.py" --limit 400000000 --syx "$SYX" \
    --max-hits 4000 $WATCHES --json "$OUT/boot.json" > "$OUT/boot.log" 2>&1
}

case "${1:-all}" in
  session) session ;;
  boot) boot ;;
  all) session; boot ;;
  *) echo "usage: $0 session|boot|all" >&2; exit 2 ;;
esac
