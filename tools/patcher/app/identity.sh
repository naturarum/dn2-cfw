#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# app/identity.sh DIR: the pixel art the app shows (the mark, the wordmark, every effect icon), copied from
# docs/identity (made by its make_icons.py; read only here) into DIR, which becomes the app's Resources/identity.
set -eu
cd "$(dirname "$0")"
SRC=../../../docs/identity
rm -rf "$1"; mkdir -p "$1"
for n in mark wordmark cmp3 flng tape spng; do
    [ -f "$SRC/$n.svg" ] || { echo "no $SRC/$n.svg"; exit 1; }
    cp "$SRC/$n.svg" "$1/"
done
