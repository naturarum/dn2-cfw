#!/usr/bin/env bash
# Run the unit's own unpack and acceptance code on rebuilt Digitone II 1.12
# files, under Unicorn (task T1.2). See notes/T1.2-device-oracle.md.
#
#   scripts/dn2-oracle-112.sh build     the test files in out/T1.2/ (recompressed, -r, -l 0)
#   scripts/dn2-oracle-112.sh oracle    vendor/digikit/tools/acceptoracle.py on them (~25 min)
#   scripts/dn2-oracle-112.sh boot      cold-boot ladder + emucheck of the recompressed file (~12 min)
#   scripts/dn2-oracle-112.sh all       the three steps above in order
#   scripts/dn2-oracle-112.sh check F.syx   the oracle on one candidate file
#
# Needs vendor/digikit on branch dn2-1.12 with its venv set up
# (scripts/dn2-emu-112.sh setup). Never talks to hardware.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dk=$root/vendor/digikit
tool=$root/vendor/elektron-firmware-tool/elektron-firmware-tool
syx=$root/elektron-firmware/Digitone_II_OS1_12.syx
main=$root/ele3_out/02_type3.unpacked.bin
want=7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35
out=$root/out/T1.2

[[ $(shasum -a 256 "$syx" | awk '{print $1}') == "$want" ]] || {
  echo "error: $syx is not Digitone II OS 1.12 (sha256 $want)" >&2
  exit 2
}
mkdir -p "$out"

build() {
  "$tool" -i "$syx" -c 3 "$main" -o "$out/recomp.syx"
  "$tool" -i "$syx" -r -o "$out/repack.syx"
  "$tool" -i "$syx" -c 3 "$main" -l 0 -o "$out/recomp-l0.syx"
  shasum -a 256 "$out"/*.syx
}

oracle() {
  cd "$dk"
  uv run python -u tools/acceptoracle.py --stock "$syx" --want-main "$main" \
    --json "$out/oracle.json" "$out/recomp.syx" "$out/repack.syx" "$syx" "$out/recomp-l0.syx" \
    | tee "$out/oracle.log"
}

check() {
  cd "$dk"
  uv run python -u tools/acceptoracle.py --stock "$syx" --want-main "$main" --no-controls "$1"
}

boot() {
  cd "$dk"
  local sec=out/sections/dn2-1.12-recomp snap=out/snapshots/dn2-1.12-recomp
  uv run python -m emu.extract "$out/recomp.syx" -o "$sec"
  mkdir -p "$snap"
  DT2_SECTIONS=$sec DT2_SNAPSHOTS=$snap uv run python -m emu.checkpoint make \
    60000000,120000000,200000000,280000000,400000000 "$snap/boot" "$out/recomp.syx" \
    | tee "$out/recomp-checkpoint-make.log"
  uv run python tools/emucheck.py --device dn2 --syx "$out/recomp.syx" --sections "$sec" \
    --snapshot "$snap/boot400M.snap" --instrs 600000000 \
    --png "$out/recomp-main-screen.png" | tee "$out/recomp-emucheck.log"
}

case ${1:-} in
build | oracle | boot) "$1" ;;
check) check "${2:?usage: $0 check FILE.syx}" ;;
all) build; oracle; boot ;;
*)
  sed -n '2,13p' "$0" >&2
  exit 2
  ;;
esac
