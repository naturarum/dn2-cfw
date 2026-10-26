#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# build.sh OUT: fxrender with SPNG v2, the settings of the A/B page of 2026-10-26 (out/T3.11/spng-v2.html).
# fx/'s sources with this folder's spring.c, spring.h and spmix.h over them, in one folder (render.c includes
# spring.h, so the two must not come from different folders). Render as `make renders` does, e.g.
#   OUT -fx spring -tail 5 -vol 64 -gain -12 -i fx/../out/T3.11/dry/pluck.wav -o sp_def.wav
set -e
here=$(cd "$(dirname "$0")" && pwd); fx="$here/../.."
out=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
cp "$fx"/*.c "$fx"/*.h "$t"/
cp "$here"/spring.c "$here"/spring.h "$here"/spmix.h "$t"/
cd "$t"
clang -O2 -fno-strict-aliasing -ffp-contract=off -w -I. \
    -DSPV2_COUPLE=2 -DSPV2_MD=3.0f -DSPV2_INTERP=1 -DSPV2_R2=1.21f -DSPV2_R3=1.32f -DSP_OUT=2.4f \
    -o "$out" render.c wav.c tapedelay.c mbcomp.c flanger.c spaceecho.c spring.c -lm
