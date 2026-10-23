#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# app/build.sh [ID ...]: Digiwerk Patcher with these packs (default OB01), in this order, universal, UNSIGNED
# (CODE_SIGNING_ALLOWED=NO): the packs (make), Resources/packs (index.txt, ID.dn2p), Resources/identity (the pixel
# art, identity.sh), the project (xcodegen), the app (xcodebuild) -> out/patcher/app/SET/Digiwerk Patcher.app (SET:
# the ids joined with +).
#
# The release build is the owner's (signing needs their Developer ID; this script never signs or notarizes): fill
# the packs as below, then
#   xcodegen generate
#   xcodebuild -project "Digiwerk Patcher.xcodeproj" -scheme "Digiwerk Patcher" -configuration Release \
#       DEVELOPMENT_TEAM=<TEAM ID> archive -archivePath out/patcher/Patcher.xcarchive
#   then export it as Developer ID (Xcode's Organizer, or xcodebuild -exportArchive), zip it with
#   ditto -c -k --keepParent, submit with xcrun notarytool submit ... --wait, and xcrun stapler staple the app.
set -eu
cd "$(dirname "$0")"
[ $# -gt 0 ] || set -- OB01
ROOT=$(cd ../../.. && pwd)
SET=$(echo "$*" | tr ' ' '+')
make -s -C .. PACKS="$*" >/dev/null
P="$ROOT/out/patcher/app-packs/packs"
rm -rf "$P"; mkdir -p "$P"
for b in "$@"; do cp "$ROOT/out/patcher/$b/patch.bin" "$P/$b.dn2p"; echo "$b" >>"$P/index.txt"; done
sh identity.sh "$ROOT/out/patcher/app-packs/identity"
xcodegen generate --spec project.yml --quiet
xcodebuild -project "Digiwerk Patcher.xcodeproj" -scheme "Digiwerk Patcher" -configuration Release \
    -derivedDataPath "$ROOT/out/patcher/app-build/$SET" ARCHS="arm64 x86_64" ONLY_ACTIVE_ARCH=NO \
    -destination "generic/platform=macOS" CODE_SIGNING_ALLOWED=NO build -quiet
OUT="$ROOT/out/patcher/app/$SET"
rm -rf "$OUT/Digiwerk Patcher.app"; mkdir -p "$OUT"
cp -R "$ROOT/out/patcher/app-build/$SET/Build/Products/Release/Digiwerk Patcher.app" "$OUT/"
APP="$OUT/Digiwerk Patcher.app"
cmp -s "$P/index.txt" "$APP/Contents/Resources/packs/index.txt" || { echo "the app's packs are not the ones staged"; exit 1; }
for b in "$@"; do cmp -s "$P/$b.dn2p" "$APP/Contents/Resources/packs/$b.dn2p" || { echo "pack $b differs in the app"; exit 1; }; done
diff -rq "$ROOT/out/patcher/app-packs/identity" "$APP/Contents/Resources/identity" >/dev/null || { echo "the art differs in the app"; exit 1; }
echo "built (unsigned): $APP ($*)"
lipo -info "$APP/Contents/MacOS/Digiwerk Patcher"
du -sk "$APP" | awk '{print $1 " KB"}'
