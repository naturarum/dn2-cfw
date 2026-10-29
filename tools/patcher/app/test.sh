#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# app/test.sh: the app's own path into the core, without its window: Sources/Core.swift (what the window, "Choose
# File..." and a drop on the icon call) and Sources/Catalog.swift compiled with the C core and Tests/main.swift, run
# with a packs folder laid out as the app's Resources/packs (with catalog.json and dsp-budget.json), for a six-pack
# set (CMP4, OB01, FL01, FL02, DT01, DJ01), a one-pack set (DJ01) and a set as the patcher ships them, one pack per
# combination (the 0.2.0 release's fourteen packs): every pack from copies of Elektron's .zip and of the stock file, and wrong files (the
# 1.11 .syx, a truncated 1.12, a .zip without the stock file, the other packs' builds).
set -eu
cd "$(dirname "$0")"
ROOT=$(cd ../../.. && pwd)
T="$ROOT/out/patcher/test/app"
STOCK="$ROOT/elektron-firmware/Digitone_II_OS1_12.syx"
ZIP="$ROOT/elektron-firmware/Digitone_II_OS1.12_dist.zip"
mkdir -p "$T"
head -c 1000000 "$STOCK" >"$T/truncated.syx"
printf 'not the OS\n' >"$T/readme.txt"
rm -f "$T/other.zip"; ditto -c -k "$T/readme.txt" "$T/other.zip"
WRONG="$T/truncated.syx $T/other.zip"
[ -f "$ROOT/out/firmware/Digitone_II_OS1.11_dist/Digitone_II_OS1.11.syx" ] && \
    WRONG="$WRONG $ROOT/out/firmware/Digitone_II_OS1.11_dist/Digitone_II_OS1.11.syx"
REL="CFEE CFTE CFES EFTS CETE CEES EFTE EFES EETS CEEE EFEE EETE EEES EEEE"
make -s -C .. PACKS="CMP4 OB01 FL01 FL02 DT01 DJ01 SP02 $REL" >/dev/null     # the packs (up to date: nothing to do)
clang -std=c99 -O2 -Wall -Wextra -c -I.. ../dn2patch.c -o "$T/dn2patch.o"
swiftc -O -swift-version 5 -import-objc-header Sources/Bridging.h -Xcc -I.. Sources/Core.swift Sources/Catalog.swift \
    Tests/main.swift \
    "$T/dn2patch.o" -o "$T/coretest"
for SET in "CMP4 OB01 FL01 FL02 DT01 DJ01" "DJ01" "$REL"; do
    P="$T/packs-$(echo $SET | tr ' ' '+')"
    rm -rf "$P"; mkdir -p "$P"
    for b in $SET; do cp "$ROOT/out/patcher/$b/patch.bin" "$P/$b.dn2p"; echo "$b" >>"$P/index.txt"; done
    cp ../catalog.json ../dsp-budget.json "$P/"
    R=0; [ "$SET" = "$REL" ] && R=1
    # shellcheck disable=SC2086
    DIGIWERK_RELEASE_SET=$R "$T/coretest" "$STOCK" "$ZIP" "$P" "$ROOT/out" "$T/work" $WRONG
done
echo "app core: the six-pack, one-pack and 0.2.0 release sets passed"
