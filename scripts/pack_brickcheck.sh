#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# The packs of Digiwerk 0.2.0 (scripts/pack_build.py): the brick checker on each, with its intended main OS and DSP
# section (EEEE: Elektron's DSP, no --intended-dsp), J at a time (default 2), out/brickcheck/NAME-DATE/ and
# out/NAME/brickcheck.log. Never talks to hardware.
#
#   scripts/pack_brickcheck.sh [-j J] NAME [NAME ...]
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
j=2
if [ "${1:-}" = -j ]; then j=$2; shift 2; fi
day=$(date +%Y-%m-%d)
one() {
  n=$1
  case $n in
    EEEE) dsp=() ;;
    CFEE) dsp=(--intended-dsp "$root/out/DC02/DC02-dsp/section7.bin") ;;
    CFTE) dsp=(--intended-dsp "$root/out/TE01/TE01-dsp/section7.bin") ;;
    CFES) dsp=(--intended-dsp "$root/out/SP02/SP02-dsp/section7.bin") ;;
    *) dsp=(--intended-dsp "$root/out/$n/$n-dsp/section7.bin") ;;
  esac
  cd "$root"
  tools/brickcheck/brickcheck "out/$n/$n.syx" --intended-main "out/$n/$n.main.bin" ${dsp[@]+"${dsp[@]}"} \
    --out "out/brickcheck/$n-$day" > "out/$n/brickcheck.log" 2>&1 || true
  echo "$n: $(grep -E 'ACCEPT|REJECT' "out/$n/brickcheck.log" | tail -1)"
}
export -f one
export root day
printf '%s\n' "$@" | xargs -P "$j" -I{} bash -c 'one {}'
