#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# tools/patcher/test.sh (make test): the packs and digiwerk-patch end to end, on the developer's machine.
#   - CMP4, OB01, FL01, FL02, DT01, DJ01: mkpatch.py makes each pack (pinned to the build's sha256; its pack JSON
#     and brickcheck ACCEPT report); stockcheck.py passes them all;
#   - the six-pack tool: every pack (--pack ID) byte for byte from a copy of the stock file, a pack chosen by its
#     number, --list (with each pack's base line), an unknown --pack and no choice refused; universal, and the
#     x86_64 slice runs (Rosetta);
#   - the one-pack tool (OB01): the build byte for byte, the input untouched; the paths Terminal pastes when a file is
#     dropped on it (escaped, quoted, trailing space; the unzipped folder); Elektron's .zip itself;
#   - refusals, with no file written: the 1.11 .syx, a truncated 1.12, a .zip without the stock file, a missing file,
#     a build as input, an existing different output file; a pack corrupted in its literal data and in its skeleton;
#   - mkpatch refuses: a file that is not a build of 1.12, a pack with no brickcheck ACCEPT report, a report for
#     another file, a pack with no pack JSON, an unknown effect id, an unknown base id; stockcheck fails a pack with
#     16 stock bytes;
#   - the check stage (dn2p_check): a failing stage withholds the output, a passing one hands it over;
#   - the app's path into the core (app/test.sh: its Swift Core with the six-pack and one-pack sets).
set -u
cd "$(dirname "$0")"
ROOT=$(cd ../.. && pwd)
STOCK="$ROOT/elektron-firmware/Digitone_II_OS1_12.syx"
OLD="$ROOT/out/firmware/Digitone_II_OS1.11_dist/Digitone_II_OS1.11.syx"
ZIP="$ROOT/elektron-firmware/Digitone_II_OS1.12_dist.zip"
T="$ROOT/out/patcher/test"
PY=${PYTHON:-python3}
CFLAGS="-std=c99 -O2 -Wall -Wextra -arch arm64 -arch x86_64 -mmacosx-version-min=11.0"
BUILDS="CMP4:c1df74aaa36f987c0dbdfb43486fe059952c413fccf95272966064cec42c3b88
OB01:bfb886237377555aea6778ba49d0b48b48e7ce46a04bd9be77365f36cb0b62dd
FL01:a2179c0cc5dc02d7d3816ee13aeb0cdf6a1908aef4e06aaf0322a973c8d75daf
FL02:82bfd32db4294413bcd0b62e7c8faf77f650dc9f3262ea678e2f94c3245c92f1
DT01:5999998c6ca63f70b6c91372891df77472b8c4e32f59e6795a82d708ffce3749
DJ01:7edf7c59e2aeabd61ebca132a9f031f776475794e04ea37f1635ec8e5a17aed8"
ALL="CMP4 OB01 FL01 FL02 DT01 DJ01"
C3="$ROOT/out/patcher/cli/CMP4+OB01+FL01+FL02+DT01+DJ01/digiwerk-patch"
C1="$ROOT/out/patcher/cli/OB01/digiwerk-patch"
pass=0; fail=0
ok()  { pass=$((pass + 1)); echo "PASS  $1"; }
bad() { fail=$((fail + 1)); echo "FAIL  $1"; }
sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
fresh() { rm -rf "$1"; mkdir -p "$1"; cp "$STOCK" "$1/Digitone_II_OS1.12.syx"; }
none() { [ "$(ls "$1" 2>/dev/null | grep -c '\.part$\|^[A-Z0-9]*[0-9]\.syx$')" = 0 ]; }   # no output (or .part) in $1

# expect NAME RC PATTERN DIR [ARGS...]: run the one-pack tool (stdin empty), check its exit status and message, and
# that DIR holds no output or .part file afterwards unless RC is 0
expect() {
    name=$1 rc=$2 pat=$3 dir=$4; shift 4
    out=$("$C1" "$@" </dev/null 2>&1); got=$?
    if [ "$got" = "$rc" ] && echo "$out" | grep -q "$pat" && { [ "$rc" = 0 ] || none "$dir"; }; then ok "$name"
    else bad "$name (exit $got): $out"; fi
}

[ "$(sha "$STOCK")" = 7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35 ] || { echo "no stock 1.12 at $STOCK"; exit 1; }
rm -rf "$T"; mkdir -p "$T"

# the packs, pinned to the builds' hashes; then the three-pack and the one-pack tool
for b in $BUILDS; do
    B=${b%%:*} H=${b#*:}
    if $PY mkpatch.py "$ROOT/out/$B/$B.syx" --name $B --expect $H >"$T/mkpatch-$B.log" 2>&1; then
        ok "$B: pack made ($(sed 's/.*rebuilds [0-9a-f]*; //; s/; stockcheck.*//' "$T/mkpatch-$B.log"))"
    else bad "$B: mkpatch: $(cat "$T/mkpatch-$B.log")"; fi
done
make -s PACKS="$ALL" >"$T/make-3.log" 2>&1 && make -s PACKS=OB01 >"$T/make-1.log" 2>&1 \
    || bad "make: $(cat "$T/make-3.log" "$T/make-1.log")"
# shellcheck disable=SC2046
$PY stockcheck.py $(for b in $ALL; do echo "$ROOT/out/patcher/$b/patch.bin"; done) \
    --brief >"$T/stockcheck.log" 2>&1 && ok "stockcheck: no 16-byte run of any of the six packs in the stock file" \
    || bad "stockcheck: $(cat "$T/stockcheck.log")"

# the six-pack tool
for b in $BUILDS; do
    B=${b%%:*} H=${b#*:}
    fresh "$T/run3-$B"
    "$C3" --pack $B "$T/run3-$B/Digitone_II_OS1.12.syx" </dev/null >"$T/run3-$B.log" 2>&1
    if [ $? = 0 ] && cmp -s "$T/run3-$B/$B.syx" "$ROOT/out/$B/$B.syx" && [ "$(sha "$T/run3-$B/$B.syx")" = "$H" ]; then
        ok "six-pack tool, --pack $B: byte for byte out/$B/$B.syx (sha256 $H)"
    else bad "six-pack $B: $(cat "$T/run3-$B.log")"; fi
done
fresh "$T/run3-num"
printf '3\n' | "$C3" "$T/run3-num/Digitone_II_OS1.12.syx" >"$T/run3-num.log" 2>&1
cmp -s "$T/run3-num/FL01.syx" "$ROOT/out/FL01/FL01.syx" && grep -q "Which one?" "$T/run3-num.log" \
    && ok "six-pack tool: asks which one; number 3 makes FL01.syx, byte for byte" || bad "number: $(cat "$T/run3-num.log")"
out=$("$C3" --list 2>&1)
[ "$(echo "$out" | grep -c '^  [1-6]\. \(CMP4\|OB01\|FL01\|FL02\|DT01\|DJ01\) ')" = 6 ] \
    && echo "$out" | grep -q "Brick check 12 of 12 · tested on a Digitone II, 2[0-9] Oct 2026" \
    && [ "$(echo "$out" | grep -c '     base: ')" = 6 ] \
    && echo "$out" | grep -q "base: D.*vu LFO$" && echo "$out" | grep -q "base: none" \
    && ok "six-pack tool: --list shows the six packs with their base and record lines" || bad "--list: $out"
fresh "$T/run3-bad"
out=$("$C3" --pack ZZ99 "$T/run3-bad/Digitone_II_OS1.12.syx" </dev/null 2>&1); got=$?
[ $got = 1 ] && none "$T/run3-bad" && echo "$out" | grep -q "no build called ZZ99" \
    && ok "six-pack tool: an unknown --pack is refused, no file" || bad "unknown pack: $out"
out=$("$C3" "$T/run3-bad/Digitone_II_OS1.12.syx" </dev/null 2>&1); got=$?
[ $got = 1 ] && none "$T/run3-bad" && ok "six-pack tool: no choice (stdin empty): refused, no file" || bad "no choice: $out"
lipo -info "$C3" | grep -q 'x86_64 arm64\|arm64 x86_64' && ok "six-pack tool: universal binary ($(stat -f %z "$C3") bytes)" \
    || bad "six-pack: not universal"
if arch -x86_64 /usr/bin/true 2>/dev/null; then
    fresh "$T/run3-x86"
    arch -x86_64 "$C3" --pack DJ01 "$T/run3-x86/Digitone_II_OS1.12.syx" </dev/null >/dev/null 2>&1
    cmp -s "$T/run3-x86/DJ01.syx" "$ROOT/out/DJ01/DJ01.syx" && ok "six-pack tool: the x86_64 slice too (Rosetta, DJ01)" \
        || bad "six-pack x86_64"
fi

# the one-pack tool (OB01): as the three-pack app and tool look with one pack
fresh "$T/run1"
"$C1" "$T/run1/Digitone_II_OS1.12.syx" </dev/null >"$T/run1.log" 2>&1
cmp -s "$T/run1/OB01.syx" "$ROOT/out/OB01/OB01.syx" && ! grep -q "Which one?" "$T/run1.log" \
    && ok "one-pack tool: OB01.syx byte for byte, without asking which" || bad "one-pack: $(cat "$T/run1.log")"
[ "$(sha "$T/run1/Digitone_II_OS1.12.syx")" = 7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35 ] \
    && ok "one-pack tool: the input file is untouched" || bad "the input file changed"
lipo -info "$C1" | grep -q 'x86_64 arm64\|arm64 x86_64' && ok "one-pack tool: universal binary ($(stat -f %z "$C1") bytes)" \
    || bad "one-pack: not universal"

# the paths Terminal pastes on a drop (read from stdin, no argument)
D="$T/with space (copy)"; fresh "$D"
printf '%s \n' "$(printf '%s' "$D/Digitone_II_OS1.12.syx" | sed 's/\([ ()]\)/\\\1/g')" | "$C1" >/dev/null 2>&1
cmp -s "$D/OB01.syx" "$ROOT/out/OB01/OB01.syx" && ok "path: backslash-escaped, with a trailing space" || bad "path: escaped"
fresh "$D"
printf "'%s'\n" "$D/Digitone_II_OS1.12.syx" | "$C1" >/dev/null 2>&1
cmp -s "$D/OB01.syx" "$ROOT/out/OB01/OB01.syx" && ok "path: in single quotes" || bad "path: quoted"
fresh "$D"
printf '\n"%s"\n' "$D" | "$C1" >/dev/null 2>&1
cmp -s "$D/OB01.syx" "$ROOT/out/OB01/OB01.syx" && ok "path: the unzipped folder (in double quotes, after an empty line)" \
    || bad "path: folder"
expect "existing OB01.syx, the right file: nothing to do" 0 "Nothing to do" "$D" "$D/Digitone_II_OS1.12.syx"
Z="$T/zip (dl)"; rm -rf "$Z"; mkdir -p "$Z"; cp "$ZIP" "$Z/"
"$C1" "$Z/Digitone_II_OS1.12_dist.zip" </dev/null >"$T/zip.log" 2>&1
cmp -s "$Z/OB01.syx" "$ROOT/out/OB01/OB01.syx" && ok "Elektron's .zip itself: OB01.syx written next to it, byte for byte" \
    || bad "zip: $(cat "$T/zip.log")"

# refusals
R="$T/refuse"; fresh "$R"
[ -f "$OLD" ] && cp "$OLD" "$R/Digitone_II_OS1.11.syx" && \
    expect "refuses the 1.11 .syx" 1 "That's Digitone II OS 1.11" "$R" "$R/Digitone_II_OS1.11.syx"
head -c 1000000 "$STOCK" >"$R/truncated.syx"
expect "refuses a truncated 1.12 .syx" 1 "damaged or incomplete" "$R" "$R/truncated.syx"
printf 'not the OS\n' >"$R/readme.txt"; ditto -c -k "$R/readme.txt" "$R/other.zip"
expect "refuses a .zip without the stock file (and says to unzip it)" 1 "couldn't find" "$R" "$R/other.zip"
expect "refuses a missing file" 1 "can't open" "$R" "$R/nothing-here.syx"
cp "$ROOT/out/OB01/OB01.syx" "$R/mine.syx"
expect "refuses its own build as input" 1 "OB01.syx already" "$R" "$R/mine.syx"
cp "$ROOT/out/FL01/FL01.syx" "$R/fl.syx"
expect "refuses another build as input" 1 "That's FL01, a custom OS" "$R" "$R/fl.syx"
rm -f "$R/mine.syx" "$R/fl.syx"
printf 'not ours\n' >"$R/OB01.syx"
out=$("$C1" "$R/Digitone_II_OS1.12.syx" </dev/null 2>&1); got=$?
[ $got = 1 ] && [ "$(cat "$R/OB01.syx")" = "not ours" ] && echo "$out" | grep -q "left it alone" \
    && ok "an existing different OB01.syx: left alone, not replaced" || bad "existing file: $out"
rm -f "$R/OB01.syx"
for c in lit skel; do
    $PY mkpatch.py "$ROOT/out/OB01/OB01.syx" --name OB01 --out "$T/corrupt-$c" --corrupt $c >/dev/null 2>&1 &&
    $PY mkpatch.py --embed "$T/corrupt-$c/packs.h" "$T/corrupt-$c/patch.bin" >/dev/null &&
    cc $CFLAGS -I. -I"$T/corrupt-$c" -o "$T/corrupt-$c/digiwerk-patch" cli.c dn2patch.c 2>/dev/null \
        || { bad "corrupt-$c: build"; continue; }
    out=$("$T/corrupt-$c/digiwerk-patch" "$R/Digitone_II_OS1.12.syx" </dev/null 2>&1); got=$?
    [ $got = 1 ] && none "$R" && echo "$out" | grep -q "nothing was saved" \
        && ok "a pack corrupted in its $c: refused, no file ($(echo "$out" | grep -o 'not exactly the [A-Z0-9]* we tested\|while making'))" \
        || bad "corrupt-$c: exit $got: $out"
done

# mkpatch's refusals
$PY mkpatch.py "$OLD" --name BAD --out "$T/bad" >"$T/bad.log" 2>&1
[ $? != 0 ] && grep -q "refused: the build replaces" "$T/bad.log" && [ ! -e "$T/bad" ] \
    && ok "mkpatch refuses a file that is not a build of 1.12" || bad "mkpatch 1.11: $(cat "$T/bad.log")"
mkdir -p "$T/no-reports"
$PY mkpatch.py "$ROOT/out/OB01/OB01.syx" --name OB01 --brickcheck "$T/no-reports" --out "$T/nobc" >"$T/nobc.log" 2>&1
[ $? != 0 ] && grep -q "no brickcheck ACCEPT report for OB01" "$T/nobc.log" && [ ! -e "$T/nobc" ] \
    && ok "mkpatch refuses a pack with no brickcheck ACCEPT report" || bad "mkpatch no report: $(cat "$T/nobc.log")"
$PY mkpatch.py "$ROOT/out/FL01/FL01.syx" --name OB01 --out "$T/wrongbc" >"$T/wrongbc.log" 2>&1
[ $? != 0 ] && grep -q "no brickcheck ACCEPT report for OB01" "$T/wrongbc.log" && [ ! -e "$T/wrongbc" ] \
    && ok "mkpatch refuses a brickcheck report made for another file (OB01's report, FL01's .syx)" \
    || bad "mkpatch wrong report: $(cat "$T/wrongbc.log")"
$PY mkpatch.py "$ROOT/out/OB01/OB01.syx" --name ZZ01 --out "$T/nojson" >"$T/nojson.log" 2>&1
[ $? != 0 ] && grep -q "no pack JSON for ZZ01" "$T/nojson.log" && [ ! -e "$T/nojson" ] \
    && ok "mkpatch refuses a pack with no pack JSON" || bad "mkpatch no json: $(cat "$T/nojson.log")"
printf '{"title": "x", "description": "x", "tested_on_unit": "", "effects": ["cmp3", "zzzz"], "base": []}\n' >"$T/badfx.json"
$PY mkpatch.py "$ROOT/out/OB01/OB01.syx" --name OB01 --pack-json "$T/badfx.json" --out "$T/badfx" >"$T/badfx.log" 2>&1
[ $? != 0 ] && grep -q '"effects" must list effect ids' "$T/badfx.log" && [ ! -e "$T/badfx" ] \
    && ok "mkpatch refuses a pack with an unknown effect id" || bad "mkpatch bad effects: $(cat "$T/badfx.log")"
printf '{"title": "x", "description": "x", "tested_on_unit": "", "effects": ["cmp3"], "base": ["overbridge", "zzzz"]}\n' \
    >"$T/badbase.json"
$PY mkpatch.py "$ROOT/out/OB01/OB01.syx" --name OB01 --pack-json "$T/badbase.json" --out "$T/badbase" >"$T/badbase.log" 2>&1
[ $? != 0 ] && grep -q '"base" must list base ids' "$T/badbase.log" && [ ! -e "$T/badbase" ] \
    && ok "mkpatch refuses a pack with an unknown base id" || bad "mkpatch bad base: $(cat "$T/badbase.log")"
$PY - "$ROOT/out/patcher/OB01/patch.bin" "$STOCK" "$T/tampered.bin" <<'EOF'
import sys; sys.path.insert(0, '.')
import mkpatch
blob = open(sys.argv[1], 'rb').read()
stock = mkpatch.Syx(open(sys.argv[2], 'rb').read())
_h, secs = mkpatch.sections(blob)
at = secs[0]['at']['ilit']                               # 16 bytes of the stock main OS in our literal data
open(sys.argv[3], 'wb').write(blob[:at] + stock.decoded[3][0x1000:0x1010] + blob[at + 16:])
EOF
$PY stockcheck.py "$T/tampered.bin" >"$T/tampered.log" 2>&1
[ $? = 1 ] && grep -q "HITS" "$T/tampered.log" && ok "stockcheck fails a pack with 16 stock bytes in its literal data" \
    || bad "stockcheck tamper: $(cat "$T/tampered.log")"

# the check stage: a hook that says no withholds the output; one that says yes hands it over
cat >"$T/hook.c" <<'EOF'
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dn2patch.h"
static uint8_t *rd(const char *path, size_t *n)
{
    FILE *f = fopen(path, "rb");
    uint8_t *p = malloc(DN2P_MAX_IN);
    *n = f && p ? fread(p, 1, DN2P_MAX_IN, f) : 0;
    if (f)
        fclose(f);
    return p;
}
static int no(const uint8_t *o, size_t n, const dn2p_info *pk, char *t, size_t tn, void *c)
{
    (void)o, (void)n, (void)c;
    snprintf(t, tn, "the test stage says no to %s", pk->id);
    return 1;
}
static int yes(const uint8_t *o, size_t n, const dn2p_info *pk, char *t, size_t tn, void *c)
{
    (void)t, (void)tn, (void)pk;
    *(size_t *)c = n && o ? n : 0;
    return 0;
}
int main(int argc, char **argv)
{
    size_t pn, n, on, seen = 0;
    uint8_t *pack = rd(argv[1], &pn), *in = rd(argv[2], &n), *o;
    char msg[DN2P_MSG];
    dn2p_info i;
    if (argc < 3 || dn2patch_info(pack, pn, &i))
        return 2;
    int a = dn2patch_run(pack, pn, in, n, no, NULL, &o, &on, msg, sizeof msg);
    if (a != DN2P_CHECK_FAILED || o || !strstr(msg, "the test stage says no"))
        return puts("a failing check stage did not withhold the output"), 1;
    int b = dn2patch_run(pack, pn, in, n, yes, &seen, &o, &on, msg, sizeof msg);
    if (b != DN2P_OK || !o || seen != on || !dn2patch_is_build(pack, pn, o, on))
        return puts("a passing check stage did not hand the output over"), 1;
    printf("%s: a failing stage withholds it (status %d), a passing one saw %zu bytes; dsp fields %ld %ld %g\n",
           i.id, a, seen, i.dsp_code_bytes, i.dsp_state_bytes, i.dsp_load_percent);
    return 0;
}
EOF
cc -std=c99 -O2 -Wall -Wextra -I. "$T/hook.c" dn2patch.c -o "$T/hook" 2>"$T/hook-cc.log" \
    && "$T/hook" "$ROOT/out/patcher/FL01/patch.bin" "$STOCK" >"$T/hook.log" 2>&1 \
    && ok "check stage: $(cat "$T/hook.log")" || bad "check stage: $(cat "$T/hook-cc.log" "$T/hook.log" 2>/dev/null)"

if command -v swiftc >/dev/null; then
    sh app/test.sh >"$T/app-test.log" 2>&1 && ok "app/test.sh: the app's Core, $(grep -c '^PASS' "$T/app-test.log") checks over the six-pack and one-pack sets" \
        || bad "app/test.sh: $(grep FAIL "$T/app-test.log" | head -5; tail -3 "$T/app-test.log")"
fi

echo "$pass passed, $fail failed"
[ $fail = 0 ]
