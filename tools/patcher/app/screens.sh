#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# app/screens.sh: PNGs of the window (Tests/snap/main.swift renders ContentView offscreen after real runs of the
# Model) for a one-pack set (OB01) and a three-pack set (CMP4, OB01, FL01) -> out/patcher/screens/. No screen
# capture permission needed.
set -eu
cd "$(dirname "$0")"
ROOT=$(cd ../../.. && pwd)
T="$ROOT/out/patcher/test/screens"
mkdir -p "$T"
make -s -C .. PACKS="CMP4 OB01 FL01" >/dev/null
for SET in "OB01" "CMP4 OB01 FL01"; do
    P="$T/packs-$(echo $SET | tr ' ' '+')"
    rm -rf "$P"; mkdir -p "$P"
    for b in $SET; do cp "$ROOT/out/patcher/$b/patch.bin" "$P/$b.dn2p"; echo "$b" >>"$P/index.txt"; done
done
sh identity.sh "$T/identity"
clang -std=c99 -O2 -c -I.. ../dn2patch.c -o "$T/dn2patch.o"
swiftc -O -swift-version 5 -import-objc-header Sources/Bridging.h -Xcc -I.. Sources/Core.swift Sources/Model.swift \
    Sources/Art.swift Sources/ContentView.swift Tests/snap/main.swift "$T/dn2patch.o" -o "$T/snap"
rm -rf "$ROOT/out/patcher/screens"
"$T/snap" "$ROOT/out/patcher/screens" "$ROOT/elektron-firmware/Digitone_II_OS1.12_dist.zip" \
    "$ROOT/out/firmware/Digitone_II_OS1.11_dist/Digitone_II_OS1.11.syx" "$T/work" "$T/packs-OB01" "$T/packs-CMP4+OB01+FL01" \
    "$T/identity"
