#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# T1.6: run the unit's own code on elekloader's Digitone II 1.12 builds
# (made by scripts/t1.6_builds.py into out/T1.6/), under Unicorn, and boot the
# no-mod build in digikit's emulator. See notes/T1.6-elekloader-dn2.md.
#
#   scripts/t1.6-oracle.sh oracle   tools/acceptoracle.py on the builds (bootstrap boot path,
#                                   section 4's in-place loader, validators)
#   scripts/t1.6-oracle.sh boot     cold-boot ladder + emucheck of nomod-DN01.syx (~12 min)
#   scripts/t1.6-oracle.sh all
#
# Read-only on digikit (vendor/digikit, branch dn2-1.12). Never talks to hardware.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dk=$root/vendor/digikit
syx=$root/elektron-firmware/Digitone_II_OS1_12.syx
main=$root/ele3_out/02_type3.unpacked.bin
out=$root/out/T1.6

oracle() {
  cd "$dk"
  uv run python -u tools/acceptoracle.py --stock "$syx" --want-main "$main" --no-controls \
    --json "$out/oracle-nomod.json" "$out/nomod-DN01.syx" | tee "$out/oracle-nomod.log"
  uv run python -u tools/acceptoracle.py --stock "$syx" --want-main "$out/zeros120k.main.bin" \
    --no-controls --json "$out/oracle-zeros.json" \
    "$out/zeros120k-DN02.syx" "$out/zeros120k-nofloor.syx" | tee "$out/oracle-zeros.log"
}

boot() {
  cd "$dk"
  local sec=out/sections/dn2-1.12-T1.6 snap=out/snapshots/dn2-1.12-T1.6
  uv run python -m emu.extract "$out/nomod-DN01.syx" -o "$sec"
  mkdir -p "$snap"
  DT2_SECTIONS=$sec DT2_SNAPSHOTS=$snap uv run python -m emu.checkpoint make \
    60000000,120000000,200000000,280000000,400000000 "$snap/boot" "$out/nomod-DN01.syx" \
    | tee "$out/nomod-checkpoint-make.log"
  uv run python tools/emucheck.py --device dn2 --syx "$out/nomod-DN01.syx" --sections "$sec" \
    --snapshot "$snap/boot400M.snap" --instrs 600000000 \
    --png "$out/nomod-main-screen.png" | tee "$out/nomod-emucheck.log"
}

case ${1:-} in
oracle | boot) "$1" ;;
all) oracle; boot ;;
*)
  sed -n '2,11p' "$0" >&2
  exit 2
  ;;
esac
