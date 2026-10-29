#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# app/screens.sh [ID ...]: PNGs of the window (Tests/snap/main.swift renders ContentView offscreen after real runs of
# the Model) for a set of packs (default: the 0.2.0 release's fourteen, in the release's order: CFEE first, the
# combination played on a unit, which the app opens on), staged as build.sh stages Resources/packs (the packs, index.txt,
# catalog.json, dsp-budget.json) -> out/patcher/screens/. No screen capture permission needed.
set -eu
cd "$(dirname "$0")"
[ $# -gt 0 ] || set -- CFEE CFTE CFES EFTS CETE CEES EFTE EFES EETS CEEE EFEE EETE EEES EEEE
ROOT=$(cd ../../.. && pwd)
T="$ROOT/out/patcher/test/screens"
mkdir -p "$T"
make -s -C .. PACKS="$*" >/dev/null
P="$T/packs"
rm -rf "$P"; mkdir -p "$P"
for b in "$@"; do cp "$ROOT/out/patcher/$b/patch.bin" "$P/$b.dn2p"; echo "$b" >>"$P/index.txt"; done
cp ../catalog.json ../dsp-budget.json "$P/"
sh identity.sh "$T/identity"
clang -std=c99 -O2 -c -I.. ../dn2patch.c -o "$T/dn2patch.o"
swiftc -O -swift-version 5 -import-objc-header Sources/Bridging.h -Xcc -I.. Sources/Core.swift Sources/Catalog.swift \
    Sources/Model.swift Sources/Art.swift Sources/ContentView.swift Sources/ManualView.swift Tests/snap/main.swift \
    "$T/dn2patch.o" -o "$T/snap"
rm -rf "$ROOT/out/patcher/screens"
"$T/snap" "$ROOT/out/patcher/screens" "$ROOT/elektron-firmware/Digitone_II_OS1.12_dist.zip" \
    "$ROOT/out/firmware/Digitone_II_OS1.11_dist/Digitone_II_OS1.11.syx" "$T/work" "$P" "$T/identity"
