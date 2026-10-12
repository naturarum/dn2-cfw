#!/usr/bin/env bash
# Boot Digitone II OS 1.12 in digikit's ColdFire emulator, headless, and check
# that the real main screen is reached (task T1.1). See notes/T1.1-emulator-1.12.md.
#
#   scripts/dn2-emu-112.sh setup       uv sync + patched Unicorn (once per venv)
#   scripts/dn2-emu-112.sh extract     sections -> vendor/digikit/out/sections/dn2-1.12
#   scripts/dn2-emu-112.sh ladder      boot snapshots 60M..400M (about 7 minutes)
#   scripts/dn2-emu-112.sh check       emucheck from boot400M, 600M instrs (about 5 minutes)
#   scripts/dn2-emu-112.sh all         the four steps above in order
#
# Needs vendor/digikit on branch dn2-1.12 (the 1.12 hash in devices/ and the
# emucheck --png option). Never talks to hardware.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dk=$root/vendor/digikit
syx=${DN2_SYX:-$root/elektron-firmware/Digitone_II_OS1_12.syx}
want=7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35
sections=out/sections/dn2-1.12
snapshots=out/snapshots/dn2-1.12
report=$root/out/T1.1

[[ $(shasum -a 256 "$syx" | awk '{print $1}') == "$want" ]] || {
  echo "error: $syx is not Digitone II OS 1.12 (sha256 $want)" >&2
  exit 2
}
cd "$dk"

setup() {
  uv sync
  # Unicorn's bundled QEMU configure stops if pkg-config is missing, which
  # leaves CONFIG_INT128 unset and the build fails in qemu/include/qemu/int128.h
  # ("typedef redefinition with different types"). Unicorn's m68k build needs
  # no pkg-config query, so a binary that always fails is enough.
  if command -v pkg-config >/dev/null; then
    tools/install-patched-unicorn.sh
  else
    PKG_CONFIG=/usr/bin/false tools/install-patched-unicorn.sh
  fi
  uv run python -m emu.unicorn_compat
}

extract() {
  uv run python -m emu.extract "$syx" -o "$sections"
  DT2_SECTIONS=$sections uv run python -m emu.symbols "$sections/section_3_MAIN_OS.bin"
}

ladder() {
  mkdir -p "$snapshots" "$report"
  DT2_SECTIONS=$sections DT2_SNAPSHOTS=$snapshots uv run python -m emu.checkpoint make \
    60000000,120000000,200000000,280000000,400000000 "$snapshots/boot" "$syx" \
    | tee "$report/checkpoint-make.log"
}

check() {
  mkdir -p "$report"
  uv run python tools/emucheck.py --device dn2 --syx "$syx" --sections "$sections" \
    --snapshot "$snapshots/boot400M.snap" --instrs 600000000 \
    --png "$report/main-screen.png" | tee "$report/emucheck-400M.log"
}

case ${1:-} in
setup | extract | ladder | check) "$1" ;;
all) setup; extract; ladder; check ;;
*)
  sed -n '2,12p' "$0" >&2
  exit 2
  ;;
esac
