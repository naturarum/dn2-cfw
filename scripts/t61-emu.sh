#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# T6.1: run scripts/t61-pages.script on stock's warm 800M snapshot with one variant of
# scripts/t61_grow_emu.py (stock, inert, grown, pathc, grown-cc), headless at --speed 1.
#
#   scripts/t61-emu.sh VARIANT [SCRIPT [NAME]]   (NAME: output directory, default VARIANT)
#
# Outputs in out/T6.1/emu/VARIANT/ (PNG + .txt per screen, t61.json, midi.txt, report.json,
# end.snap). Emulator RAM only: nothing is built, flashed or written outside out/T6.1.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
v=${1:?usage: t61-emu.sh VARIANT [SCRIPT]}
script=${2:-$root/scripts/t61-pages.script}
script=$(cd "$(dirname "$script")" && pwd)/$(basename "$script")
out=$root/out/T6.1/emu/${3:-$v}
mkdir -p "$out"
cd "$root/vendor/digikit"
DT2_SECTIONS=out/sections/dn2-1.12 uv run python "$root/scripts/t61_grow_emu.py" \
  "$root/out/T2.4a/warm/warm800M.snap" --syx "$root/elektron-firmware/Digitone_II_OS1_12.syx" \
  --variant "$v" --speed 1 --script "$script" --out "$out" --report "$out/report.json" \
  --save "$out/end.snap" --midi-log "$out/midi.txt" 2>&1 | tee "$out/run.log" | grep -v '^\[script\]'
