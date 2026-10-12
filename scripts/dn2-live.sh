#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Play the emulated Digitone II 1.12 from the Mac keyboard (T2.4a; key map: ? in the window, or vendor/digikit/docs/LIVE.md). Extra arguments go to emu.live; DN2_SNAP picks another snapshot.
# emu.live runs inside vendor/digikit, so relative paths given here (DN2_SNAP, --script, --out, --save, --report, --keymap, --midi-log, --midi-file) are made absolute against the caller's directory first.
# DN2_RTMIDI=1 adds python-rtmidi for --midi-port / --midi-in (virtual CoreMIDI ports), e.g.: DN2_RTMIDI=1 scripts/dn2-live.sh --ssi0-hz --ips 150e6 --midi-port
r=$(cd "$(dirname "$0")/.." && pwd); here=$PWD
abs() { case "$1" in /*) printf '%s' "$1";; *) printf '%s/%s' "$here" "$1";; esac; }
args=(); want=0
for a in "$@"; do
  if [ "$want" = 1 ]; then args+=("$(abs "$a")"); want=0; continue; fi
  case "$a" in
    --script|--out|--save|--report|--keymap|--midi-log|--midi-file) args+=("$a"); want=1;;
    --script=*|--out=*|--save=*|--report=*|--keymap=*|--midi-log=*|--midi-file=*) args+=("${a%%=*}=$(abs "${a#*=}")");;
    *) args+=("$a");;
  esac
done
snap=$(abs "${DN2_SNAP:-$r/out/T2.4a/warm/warm800M.snap}")
cd "$r/vendor/digikit" && DT2_SECTIONS=out/sections/dn2-1.12 exec uv run ${DN2_RTMIDI:+--with python-rtmidi} python -m emu.live "$snap" --syx "$r/elektron-firmware/Digitone_II_OS1_12.syx" ${args[@]+"${args[@]}"}
