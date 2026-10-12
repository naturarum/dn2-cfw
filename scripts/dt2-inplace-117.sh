#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Digitakt II OS 1.17: section 4's in-place main-OS loader on stock and on
# elekloader builds (task T1.11). See notes/T1.11-dt2-inplace.md.
#
#   scripts/dt2-inplace-117.sh fetch      Elektron's public 1.17 zip -> out/T1.11/, sha256 checked
#   scripts/dt2-inplace-117.sh build      core-dt2, the two DT2 examples, and four builds (elekloader's commands)
#   scripts/dt2-inplace-117.sh oracle     the unit's loaders under Unicorn on stock and the builds (~6 min)
#   scripts/dt2-inplace-117.sh synthetic  a test mod with 240 KB of zeros in .run: built, then the oracle
#   scripts/dt2-inplace-117.sh all        the four steps in order
#
# Needs: Homebrew m68k-elf-binutils and m68k-elf-gcc (elekloader's DT2 prefix is
# m68k-linux-gnu-; ELEKLOADER_CROSS=m68k-elf- selects these), vendor/digikit with
# its uv environment (scripts/dn2-emu-112.sh setup). Never talks to hardware.
# Every output stays under out/T1.11/ (git-ignored). Never flash any of it.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
out=$root/out/T1.11
el=$root/vendor/elekloader
dk=$root/vendor/digikit
syx=$out/Digitakt_II_OS1.17.syx
url=https://www.elektron.se/wp-content/uploads/2026/10/Digitakt_II_OS1.17_dist.zip
want=26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5   # elekloader's pin
export ELEKLOADER_CROSS=${ELEKLOADER_CROSS:-m68k-elf-}

check() {
  [[ -f $syx && $(shasum -a 256 "$syx" | awk '{print $1}') == "$want" ]] || {
    echo "error: $syx missing or not Digitakt II OS 1.17 (sha256 $want); run: $0 fetch" >&2
    exit 2
  }
}

fetch() {
  mkdir -p "$out"
  curl -fsSL -A 'Mozilla/5.0' -o "$out/Digitakt_II_OS1.17_dist.zip" "$url"
  (cd "$out" && unzip -o -q Digitakt_II_OS1.17_dist.zip Digitakt_II_OS1.17.syx)
  check
  echo "OK $syx sha256 $want"
}

build() {
  check
  mkdir -p "$out/mods" "$out/builds"
  cd "$el"
  python3 -m elekloader.sdk.build mods/core-dt2 --stock "$syx" --out "$out/mods"
  python3 -m elekloader.sdk.build examples/hello-marker-dt2 --stock "$syx" --out "$out/mods"
  python3 -m elekloader.sdk.build examples/perform-direct --stock "$syx" --out "$out/mods"
  # the no-mod rebuild: elekloader.patch refuses zero mods, so a format-1 mod with no sites
  python3 - "$out/mods/nomod.elemod" <<'EOF'
import json, sys
from elekloader import devices
dev = [d for d in devices.devices() if d.key == 'digitakt-mk2'][0]
doc = {'elemod': 1, 'id': 'nomod', 'version': '1', 'title': 'no change',
       'target': devices.target_of(dev, dev.releases['1.17']), 'sites': []}
json.dump(doc, open(sys.argv[1], 'w'), indent=1)
EOF
  python3 -m elekloader.lint "$out/mods/hello-marker-1.0.elemod" --stock "$syx" \
    --with "$out/mods/core-1.0.elemod" --json | grep -E '"ok"|"problems"'
  python3 -m elekloader.patch --stock "$syx" --mod "$out/mods/nomod.elemod" \
    --out "$out/builds/nomod.syx" --version 1.17
  python3 -m elekloader.patch --stock "$syx" --mod "$out/mods/core-1.0.elemod" \
    --out "$out/builds/core.syx" --version 1.17
  python3 -m elekloader.patch --stock "$syx" --mod "$out/mods/core-1.0.elemod" \
    --mod "$out/mods/hello-marker-1.0.elemod" --out "$out/builds/core-hello.syx" --version 1.17
  python3 -m elekloader.patch --stock "$syx" --mod "$out/mods/core-1.0.elemod" \
    --mod "$out/mods/perform-direct-1.0.elemod" --out "$out/builds/core-perform.syx" --version 1.17
  shasum -a 256 "$out"/mods/*.elemod "$out"/builds/*.syx
}

oracle() {
  check
  cd "$dk"
  uv run python -u tools/acceptoracle_dt2.py --stock "$syx" --ddr both --dump "$out/dumps" \
    --json "$out/oracle.json" "$syx" "$out/builds/nomod.syx" "$out/builds/core.syx" \
    "$out/builds/core-hello.syx" "$out/builds/core-perform.syx" | tee "$out/oracle.log"
}

synthetic() {
  check
  local d=$out/synthetic/bigdata
  mkdir -p "$d"
  cat > "$d/big.s" <<'EOF'
| Synthetic test mod (T1.11): a 240 KB initialized table of zeros in .run,
| to measure how compressible mod data moves the in-place margin.
        .section .run, "ax"
        .globl  bigdata_table
bigdata_table:
        .fill   0x3C000, 1, 0
        .globl  bigdata_draw
bigdata_draw:
        rts
EOF
  cat > "$d/mod.json" <<'EOF'
{
 "id": "bigdata-test", "version": "0.1", "title": "in-place margin test",
 "category": "Test", "license": "GPL-2.0-or-later",
 "description": "Synthetic: a 240 KB zero table in .run (T1.11 margin test). Never flash.",
 "device": "digitakt-mk2", "os": "1.17",
 "sources": ["big.s"],
 "subscribe": [{"event": "ev_draw", "fn": "bigdata_draw", "order": 90}],
 "requires": ["core"]
}
EOF
  cd "$el"
  python3 -m elekloader.sdk.build "$d" --stock "$syx" --out "$out/synthetic"
  python3 -m elekloader.patch --stock "$syx" --mod "$out/mods/core-1.0.elemod" \
    --mod "$out/synthetic/bigdata-test-0.1.elemod" --out "$out/synthetic/core-bigdata.syx" --version 1.17
  cd "$dk"
  uv run python -u tools/acceptoracle_dt2.py --stock "$syx" --ddr both --dump "$out/dumps-synthetic" \
    --json "$out/oracle-synthetic.json" "$out/synthetic/core-bigdata.syx" | tee "$out/oracle-synthetic.log"
}

case ${1:-} in
  fetch) fetch ;;
  build) build ;;
  oracle) oracle ;;
  synthetic) synthetic ;;
  all) fetch; build; oracle; synthetic ;;
  *) sed -n '2,15p' "$0"; exit 1 ;;
esac
