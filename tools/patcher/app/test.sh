#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# app/test.sh: the app's own path into the core, without its window: Sources/Core.swift (what the window, "Choose
# File..." and a drop on the icon call) compiled with the C core and Tests/main.swift, run with a packs folder laid
# out as the app's Resources/packs, for a three-pack set (CMP4, OB01, FL01) and a one-pack set (OB01): every pack
# from copies of Elektron's .zip and of the stock file, and wrong files (the 1.11 .syx, a truncated 1.12, a .zip
# without the stock file, the other packs' builds).
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
make -s -C .. PACKS="CMP4 OB01 FL01" >/dev/null      # the packs (up to date: nothing to do)
clang -std=c99 -O2 -Wall -Wextra -c -I.. ../dn2patch.c -o "$T/dn2patch.o"
swiftc -O -swift-version 5 -import-objc-header Sources/Bridging.h -Xcc -I.. Sources/Core.swift Tests/main.swift \
    "$T/dn2patch.o" -o "$T/coretest"
for SET in "CMP4 OB01 FL01" "OB01"; do
    P="$T/packs-$(echo $SET | tr ' ' '+')"
    rm -rf "$P"; mkdir -p "$P"
    for b in $SET; do cp "$ROOT/out/patcher/$b/patch.bin" "$P/$b.dn2p"; echo "$b" >>"$P/index.txt"; done
    # shellcheck disable=SC2086
    "$T/coretest" "$STOCK" "$ZIP" "$P" "$ROOT/out" "$T/work" $WRONG
done
echo "app core: the three-pack and one-pack sets passed"
