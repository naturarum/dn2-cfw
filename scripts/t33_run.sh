#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# T3.3: run the emulator page sessions, capture the TX frame for every snapshot, diff them.
#   scripts/t33_run.sh PAGE NAV [PAGE NAV ...]     e.g. scripts/t33_run.sh sendfx2 17+5,14
# SESSION_ARGS="--pairs 2 --encoders 3-3" passes extra options to scripts/t33_session.py.
# Outputs: out/T3.3/snaps/PAGE/, out/T3.3/frames/PAGE/, out/T3.3/diffs/PAGE.{txt,json}.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
syx=$root/elektron-firmware/Digitone_II_OS1_12.syx
while [[ $# -ge 2 ]]; do
  page=$1 nav=$2; shift 2
  echo "== $page ($nav)"
  "$root/venv/bin/python" "$root/scripts/t33_session.py" "$page" --nav "$nav" ${SESSION_ARGS:-} \
    --extra=--watch=0x80003af0:0xd8c=mirror --extra=--watch-max=400
  rm -rf "$root/out/T3.3/frames/$page"
  (cd "$root/vendor/digikit" && DT2_SECTIONS=out/sections/dn2-1.12 uv run python ../../scripts/t33_frame.py \
    $(ls "$root/out/T3.3/snaps/$page"/*.snap | sort) --syx "$syx" --passes 60 \
    --out "$root/out/T3.3/frames/$page" | grep -v "'returned': 60" || true)
  python3 "$root/scripts/t33_diff.py" "$page"
done
