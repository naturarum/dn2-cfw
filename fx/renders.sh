#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Renders for the T3.11 A/B page. Usage: ./renders.sh OUTDIR  (run from fx/, after make dry)
# Writes OUTDIR/wav/*.wav and OUTDIR/manifest.tsv (group, id, file, title, settings, description, args),
# which make_ab.py turns into OUTDIR/ab.html.
set -e
OUT=${1:-../out/T3.11}
R="$OUT/bin/fxrender"
D="$OUT/dry"
W="$OUT/wav"
M="$OUT/manifest.tsv"
mkdir -p "$W"
: > "$M"
# the new effects' renders are all made again below: drop the old ones, so renamed or removed
# renders do not linger (stock_* files belong to stock_renders.py, which tidies its own)
rm -f "$W"/td_*.wav "$W"/mb_*.wav "$W"/fl_*.wav "$W"/fl[cu]*_*.wav "$W"/se_*.wav "$W"/ftd_*.wav "$W"/sp_*.wav "$W"/ts_*.wav

# group header: g GROUP DRYFILE "group title" "group description"
g() {
    printf 'group\t%s\tdry/%s\t%s\t\t%s\n' "$1" "$2" "$3" "$4" >> "$M"
}
# render: r GROUP ID "title" "description" fxrender-args...
r() {
    grp=$1; id=$2; title=$3; desc=$4; shift 4
    "$R" "$@" -o "$W/$id.wav" 2>&1 | sed 's/^/  /'
    args=$(echo "$*" | sed -e "s|$D/||g" -e 's|-i [^ ]* ||' -e 's|-key [^ ]* ||' -e 's|-fx [a-z0-9]* ||')
    # column 7: the full fxrender arguments (dry paths relative to OUTDIR), read by stock_renders.py
    full=$(echo "$*" | sed -e "s|$D/|dry/|g")
    printf '%s\t%s\twav/%s.wav\t%s\t%s\t%s\t%s\n' "${KIND:-render}" "$grp" "$id" "$title" "$args" "$desc" "$full" >> "$M"
}
# a reference render shown in its own row of the page (T5.2: the one-head tape delay, FTD1)
tp() { KIND=tape r "$@"; }
# SC01: the same render with SE01's Space Echo (tools/dspsplice/src/se01/spaceecho.c: INTENSITY 1.1 (INT/127)^1.5)
rse01() { R="$OUT/bin/fxrender_se01" KIND=se01 r "$@"; }
# T5.6: the same unit knobs on CMP4's map (C = HPF, the centre fixed at 0.9 ms): -fx flgunit1
rc4() { KIND=cmp4 r "$@"; }
# SC02: the same render with SC01's Space Echo (tools/dspsplice/src/sc01/spaceecho.c: FDBK/64 per head)
rsc01() { R="$OUT/bin/fxrender_sc01" KIND=sc01 r "$@"; }

TD="-fx tapedelay -tail 4 -bpm 120 -vol 64"
CLEAN="-p WOW=0 -p FLUT=0 -p AGE=0"

echo "space echo (T5.2)"
# The Space Echo: three heads at 1x, 2x, 3x the head-1 time. Each group also has FTD1 (the one-head tape
# delay as it runs on the unit: RES 32, WOW 20, FLUT 20, AGE 24, DRIV 32 fixed, notes/T3.8 section 9) at the
# same head-1 time and the same feedback per head, and, after make stock, the stock delay at its closest knobs.
# SC01 (2026-10-21): INTENSITY is on the stock delay's FDBK law, INT/64 per head (64 = 100 %); every INT below was
# converted from SE01's 1.1 (INT/127)^1.5 to the same gain per head (INT 64 -> 25.2, 50 -> 17.4, ...), so these
# renders sound as before (FTD1's FDBK, 1.1 FDBK/127 per pass, unchanged). The new law itself: group se_fdbk.
# SC02 (2026-10-21): the playing heads share that gain (1/2 or 1/3 each), so 64 is endless in every MODE; every INT
# with two or three heads below was multiplied by the head count (25.2 -> 50.4, 17.4 -> 52.2, ...), so the gain per
# head and the sound stay as they were. The change itself: group se_share.
SE="-fx spaceecho -tail 5 -bpm 120 -vol 64 -gain -4"
TP="-fx tapedelay -tail 5 -bpm 120 -vol 64 -gain -4 -p RES=32 -p WOW=20 -p FLUT=20 -p AGE=24 -p DRIV=32"
SEP="-p TIME=15 -p INT=25.2 -p AGE=48 -p BW=127 -p PAN=1 -p WID=100"

g se_modes pluck.wav "TAPE: the seven head modes (plucked melody)" "T5.2. Three playback heads on one tape at 1x, 2x and 3x the head-1 time (Roland's 1:2:3). TIME 15: head 1 an eighth note, head 2 a quarter, head 3 a dotted quarter. MODE picks the heads (RE-202 order: 1, 2, 3, 1+2, 2+3, 1+3, 1+2+3); the feedback is the sum of the heads that sound, so more heads repeat denser. 39 % per head: INTENSITY 25.2 on one head, 50.4 on two (the heads share it), AGE 48, no band filter (BW 127), ALT at WIDTH 100. Send mix, VOL 64, the input 4 dB down (the heads add up, and a hard-panned head is +3 dB on its side)."
r se_modes se_m1 "MODE 1: head 1" "Head 1 alone: an eighth-note tape echo, the one-head delay's job." $SE -i $D/pluck.wav $SEP -p MODE=0
r se_modes se_m2 "MODE 2: head 2" "Head 2 alone: a quarter note." $SE -i $D/pluck.wav $SEP -p MODE=1
r se_modes se_m3 "MODE 3: head 3" "Head 3 alone: a dotted quarter (3 eighths)." $SE -i $D/pluck.wav $SEP -p MODE=2
r se_modes se_m12 "MODE 4: heads 1+2" "Eighth and quarter: the repeats fill the eighths." $SE -i $D/pluck.wav $SEP -p MODE=3 -p INT=50.4
r se_modes se_m23 "MODE 5: heads 2+3" "Quarter and dotted quarter: a lopsided, swinging pattern." $SE -i $D/pluck.wav $SEP -p MODE=4 -p INT=50.4
r se_modes se_m13 "MODE 6: heads 1+3" "Eighth and dotted quarter." $SE -i $D/pluck.wav $SEP -p MODE=5 -p INT=50.4
r se_modes se_m123 "MODE 7: heads 1+2+3, INTENSITY 52.2" "All three heads. INTENSITY 52.2 (82 %, 27 % per head): the loop runs away from 64 as on one head (SC01: from about 21, SE01's law: 57)." $SE -i $D/pluck.wav $SEP -p MODE=6 -p INT=52.2
r se_modes se_mstep "MODE steps through all seven" "MODE 1, 2, 3, 1+2, 2+3, 1+3, 1+2+3, one every 2 s, INTENSITY 40 (62 % shared by the heads that play): heads fade in and out in 20 ms, no clicks; what is on the tape keeps playing from the heads that stay, and the repeats die away about as fast in every mode." $SE -i $D/pluck.wav $SEP -p INT=40 -p MODE=0 -at 2 MODE=1 -at 4 MODE=2 -at 6 MODE=3 -at 8 MODE=4 -at 10 MODE=5 -at 12 MODE=6
tp se_modes ftd_modes "FTD1: one head, same time and feedback" "The one-head tape delay as it runs on the unit (RES 32, WOW 20, FLUT 20, AGE 24, DRIV 32 fixed; its own HPF 50 Hz and LPF 6.5 kHz), TIME 15, FDBK 45 (the same 39 % per pass as INTENSITY 64), WID 127 (ping-pong, as ALT)." $TP -i $D/pluck.wav -p TIME=15 -p FDBK=45 -p HPF=22 -p LPF=102 -p WID=127

g se_age pad.wav "TAPE: AGE, one knob for all the wear (held chords)" "Wow, flutter, hiss, crackle, top-end loss and a little more drive, all on AGE. 0 = clean tape (no wow, no noise, no filter: the echo is the input through the tape saturator), 64 = a vintage machine, 127 = worn out. Wow and flutter act like a real tape: each head plays at the tape speed now against the speed when that bit was recorded, so the farther heads wobble differently. MODE 4 (heads 1+2), TIME 31 (a quarter and a half note), INTENSITY 45.8 (36 % per head), BW 127, MONO at WIDTH 127 (the pad's own stereo). Send mix, VOL 64, the input 4 dB down (the heads add up, and a hard-panned head is +3 dB on its side)."
SEA="-p TIME=31 -p MODE=3 -p INT=45.8 -p BW=127 -p PAN=0 -p WID=127"
r se_age se_age0 "AGE 0: clean tape" "No wear at all: the reference." $SE -i $D/pad.wav $SEA -p AGE=0
r se_age se_age32 "AGE 32" "Slight: wow and flutter about 0.04 % and 0.02 % of speed, hiss about 74 dB under the echoes, the loop's top-end loss from about 10.8 kHz (one pole per pass)." $SE -i $D/pad.wav $SEA -p AGE=32
r se_age se_age64 "AGE 64: a vintage machine" "Wow 0.15 %, flutter 0.08 % of tape speed (the heads hear up to twice that), hiss 62 dB under the echoes, a soft tick now and then, top-end loss from 6.5 kHz per pass, +3.9 dB of drive." $SE -i $D/pad.wav $SEA -p AGE=64
r se_age se_age96 "AGE 96" "Between: wow 0.34 %, flutter 0.17 %, top-end loss from 4.3 kHz." $SE -i $D/pad.wav $SEA -p AGE=96
r se_age se_age127 "AGE 127: worn out" "Wow 0.6 %, flutter 0.3 % of speed, hiss 50 dB under the echoes, about 6 soft ticks a second, top-end loss from 3.1 kHz per pass, +7.8 dB drive." $SE -i $D/pad.wav $SEA -p AGE=127
r se_age se_agesweep "AGE sweep 0 -> 127" "AGE rises from 0 to 127 over 14 s." $SE -i $D/pad.wav $SEA -p AGE=0 -ramp 0 14 AGE 0 127
tp se_age ftd_age "FTD1: its fixed wear" "The one-head tape delay as on the unit (WOW 20, FLUT 20, AGE 24, RES 32, HPF 50 Hz, LPF 6.5 kHz), TIME 31, FDBK 41 (as INTENSITY 60 per head), WID 64 (the pad's stereo)." $TP -i $D/pad.wav -p TIME=31 -p FDBK=41 -p HPF=22 -p LPF=102 -p WID=64

g se_hits clave.wav "TAPE: AGE on single hits" "One clave hit per bar, lots of silence: hiss and ticks live in the echoes' tails and die with them. All three heads, TIME 23 (dotted eighth, dotted quarter, and 9 sixteenths), INTENSITY 52.2 (27 % per head), ALT at WIDTH 100. Send mix, VOL 64, the input 4 dB down (the heads add up, and a hard-panned head is +3 dB on its side)."
r se_hits se_hits_age "AGE sweep 0 -> 127" "AGE rises from 0 to 127 over 12 s." $SE -i $D/clave.wav -p TIME=23 -p MODE=6 -p INT=52.2 -p BW=127 -p PAN=1 -p WID=100 -p AGE=0 -ramp 0 12 AGE 0 127
tp se_hits ftd_hits "FTD1, ping-pong" "One head, TIME 23, FDBK 31 (as INTENSITY 50), WID 127." $TP -i $D/clave.wav -p TIME=23 -p FDBK=31 -p HPF=22 -p LPF=102 -p WID=127

g se_band stabs.wav "TAPE: the band filter, CENTER and BW (dub style)" "A resonant high-pass and low-pass in the loop, so every repeat is filtered again. BW 127 = no filter at all; closing BW narrows the band around CENTER to half an octave, and the band edges resonate more as it narrows (Q 0.7 at 8 octaves and wider, 3.5 at half an octave; one pass peaks at about +6 dB at the narrowest). Wide and off-centre it is a low-pass or a high-pass. Head 1 (TIME 15) unless said, INTENSITY 42 (66 % per pass), AGE 40, ALT at WIDTH 100. Send mix, VOL 64, the input 4 dB down (the heads add up, and a hard-panned head is +3 dB on its side)."
SEB="-p TIME=15 -p MODE=0 -p INT=42 -p AGE=40 -p PAN=1 -p WID=100"
r se_band se_band_open "BW 127: no filter" "The reference: the loop unfiltered (only AGE's gentle top-end loss)." $SE -i $D/stabs.wav $SEB -p BW=127
r se_band se_band_wide "BW 64: a tape band" "CENTER 64 (820 Hz), BW 64 (3.2 octaves: about 270 Hz to 2.5 kHz), a little resonance at the edges (Q 1.2)." $SE -i $D/stabs.wav $SEB -p CTR=64 -p BW=64
r se_band se_band_narrow "BW 24: narrow and resonant" "CENTER 72 (1.16 kHz), BW 24 (1 octave: 820 Hz to 1.6 kHz, Q 2.3), INTENSITY 38.5 (60 % per pass): telephone-like repeats that ring at the band." $SE -i $D/stabs.wav $SEB -p CTR=72 -p BW=24 -p INT=38.5
r se_band se_band_ctr "CENTER sweep, BW 24" "CENTER sweeps 30 -> 110 -> 30 (185 Hz -> 6.1 kHz -> 185 Hz) over 12 s with BW 24: the classic dub filter move." $SE -i $D/stabs.wav $SEB -p CTR=30 -p BW=24 -ramp 0 6 CTR 30 110 -ramp 6 12 CTR 110 30
r se_band se_band_bw "BW sweep 127 -> 0 -> 127" "The band closes around CENTER 64 over 6 s and opens again; the resonance rises as it closes." $SE -i $D/stabs.wav $SEB -p CTR=64 -p INT=35.2 -p BW=127 -ramp 0 6 BW 127 0 -ramp 6 12 BW 0 127
r se_band se_band_lp "As a low-pass" "CENTER 24 (140 Hz), BW 84 (5.7 octaves): edges near 20 Hz and 1 kHz, so the repeats get darker and darker." $SE -i $D/stabs.wav $SEB -p CTR=24 -p BW=84
r se_band se_band_hp "As a high-pass" "CENTER 112 (6.6 kHz), BW 70 (3.8 octaves): edges near 1.8 kHz and above hearing: thinner every pass." $SE -i $D/stabs.wav $SEB -p CTR=112 -p BW=70
r se_band se_sing "Self-oscillation, held" "Heads 1+2+3, BW 20 (0.9 octave, Q 2.5), CENTER 60 (690 Hz); INTENSITY rises 37.2 -> 127 at 2-4 s and stays, CENTER drifts 60 -> 90, then INTENSITY drops to 24.3 at 10 s (SC01: 12.4 -> 56.7 and 8.1, the same gain per head but at the top, which is now the stock maximum, 66 % per head instead of 89 %). The loop sings at the band, bounded by the saturator, and dies away. VOL 20: three heads singing together are about 10 dB louder than one echo." $SE -i $D/stabs.wav -vol 20 -p TIME=15 -p MODE=6 -p INT=37.2 -p AGE=40 -p PAN=1 -p WID=100 -p CTR=60 -p BW=20 -ramp 2 4 INT 37.2 127 -ramp 2 10 CTR 60 90 -at 10 INT=24.3
tp se_band ftd_band "FTD1 at the narrow band's edges" "The one-head tape delay with its resonant filters at the edges of the narrow render (HPF 89 = 820 Hz, LPF 68 = 1.6 kHz; RES 32 fixed on the unit), TIME 15, FDBK 70 (as INTENSITY 85), WID 127." $TP -i $D/stabs.wav -p TIME=15 -p FDBK=70 -p HPF=89 -p LPF=68 -p WID=127

g se_pan pluck.wav "TAPE: PAN and WIDTH" "PAN is a switch: MONO (the heads are not panned; WIDTH is then the echoes' own stereo: 0 mono, 127 the input's stereo as it comes), ALT (the input, summed to mono, goes on the tape's first track; the feedback crosses tracks, so the passes alternate sides, and heads 1 and 3 play on one side, head 2 on the other: WIDTH is how far apart), RND (each head jumps to a random place once per its own period, gliding there in at most 32 ms: WIDTH is how far it may go). WIDTH 0 is the same mono echo in all three. Heads 1+2+3, TIME 15, INTENSITY 52.2 (27 % per head), AGE 32, BW 127. Send mix, VOL 64, the input 4 dB down (the heads add up, and a hard-panned head is +3 dB on its side)."
SEW="-p TIME=15 -p MODE=6 -p INT=52.2 -p AGE=32 -p BW=127"
r se_pan se_pan_mono "MONO, WIDTH 0: mono echoes" "Every echo in the middle." $SE -i $D/pluck.wav $SEW -p PAN=0 -p WID=0
r se_pan se_pan_stereo "MONO, WIDTH 127: the input's stereo" "The melody's notes are panned a little; the echoes keep that." $SE -i $D/pluck.wav $SEW -p PAN=0 -p WID=127
r se_pan se_pan_alt "ALT, WIDTH 127" "Head 1 left, head 2 right, head 3 left, and every repeat crosses to the other side." $SE -i $D/pluck.wav $SEW -p PAN=1 -p WID=127
r se_pan se_pan_alt1 "ALT, one head: ping-pong" "MODE 1 (head 1), INTENSITY 25.2: the classic ping-pong, L, R, L, R." $SE -i $D/pluck.wav $SEW -p MODE=0 -p INT=25.2 -p PAN=1 -p WID=127
r se_pan se_pan_rnd "RND, WIDTH 127" "Each head moves to a new random place once per its own period (head 1 every eighth, head 2 every quarter, head 3 every dotted quarter)." $SE -i $D/pluck.wav $SEW -p PAN=2 -p WID=127
r se_pan se_pan_wid "ALT, WIDTH sweep 0 -> 127" "From mono to hard left/right over 12 s." $SE -i $D/pluck.wav $SEW -p PAN=1 -p WID=0 -ramp 0 12 WID 0 127
tp se_pan ftd_pan "FTD1, ping-pong" "One head, TIME 15, FDBK 31 (as INTENSITY 50), WID 127." $TP -i $D/pluck.wav -p TIME=15 -p FDBK=31 -p HPF=22 -p LPF=102 -p WID=127

g se_drums drums.wav "TAPE on drums" "TIME 7: head 1 a sixteenth, head 2 an eighth, head 3 a dotted eighth. Send mix, VOL 64, the input 4 dB down (the heads add up, and a hard-panned head is +3 dB on its side)."
r se_drums se_drums_23 "Heads 2+3, RND, a band" "MODE 5 (2+3), INTENSITY 29.6 (23 % per head), AGE 64, CENTER 72, BW 56 (2.5 octaves: 480 Hz to 2.8 kHz, Q 1.4), RND at WIDTH 90." $SE -i $D/drums.wav -p TIME=7 -p MODE=4 -p INT=29.6 -p AGE=64 -p CTR=72 -p BW=56 -p PAN=2 -p WID=90
r se_drums se_drums_123 "All heads, ALT, worn" "MODE 7 (1+2+3), INTENSITY 34.5 (18 % per head), AGE 96, CENTER 64, BW 64 (270 Hz to 2.5 kHz), ALT at WIDTH 100." $SE -i $D/drums.wav -p TIME=7 -p MODE=6 -p INT=34.5 -p AGE=96 -p CTR=64 -p BW=64 -p PAN=1 -p WID=100
tp se_drums ftd_drums "FTD1, one head on the eighths" "TIME 15, FDBK 25 (between the two renders), WID 96." $TP -i $D/drums.wav -p TIME=15 -p FDBK=25 -p HPF=22 -p LPF=102 -p WID=96

g se_glide pluck.wav "TAPE: TIME moves, all three heads bend" "TIME is the tape speed: changing it bends every head by the same pitch ratio, the farther heads for longer (head k glides k times slower), and the bend starts and ends smoothly (motor inertia). Heads 1+2+3, INTENSITY 44.4 (23 % per head), AGE 40, ALT at WIDTH 100. Send mix, VOL 64, the input 4 dB down (the heads add up, and a hard-panned head is +3 dB on its side)."
r se_glide se_glide "TIME 15 -> 7 -> 23" "TIME jumps from 15 to 7 at 3 s and to 23 at 6.5 s." $SE -i $D/pluck.wav -p TIME=15 -p MODE=6 -p INT=44.4 -p AGE=40 -p BW=127 -p PAN=1 -p WID=100 -at 3 TIME=7 -at 6.5 TIME=23
tp se_glide ftd_glide "FTD1, the same moves" "One head, FDBK 27, WID 127." $TP -i $D/pluck.wav -p TIME=15 -p FDBK=27 -p HPF=22 -p LPF=102 -p WID=127 -at 3 TIME=7 -at 6.5 TIME=23

echo "tape delay"
g pluck pluck.wav "Tape delay on a plucked melody" "One playback head (2026-10-06). Send-effect mix as on the unit: dry + VOL (64, a gain of 0.5) x wet. TIME 23 = 24/128 of a whole note = a dotted eighth at 120 BPM."
r pluck td_clean "Clean echo" "No wow, flutter or AGE, no resonance: the tape loop's filters (50 Hz..6.5 kHz) and soft clip only. The reference for the others." \
  $TD -i $D/pluck.wav -p TIME=23 -p FDBK=60 $CLEAN
r pluck td_light "Light wow and flutter" "WOW 40 (0.2 %), FLUT 35 (0.06 %): the amount a good tape machine has." \
  $TD -i $D/pluck.wav -p TIME=23 -p FDBK=60 -p WOW=40 -p FLUT=35
r pluck td_age64 "Worn tape, AGE 64" "AGE 64: hiss about 62 dB under the echoes, a soft crackle every 2/3 s or so, the loop low-pass 0.4 octave lower; plus WOW 70, FLUT 50. Hiss and crackle follow the echoes and die with them." \
  $TD -i $D/pluck.wav -p TIME=23 -p FDBK=65 -p WOW=70 -p FLUT=50 -p AGE=64
r pluck td_age127 "AGE 127 (the most there is)" "AGE 127: hiss about 50 dB under the echoes, about 6 soft ticks per second, the loop low-pass 0.75 octave lower. Meant to stay tasteful at the top of the knob." \
  $TD -i $D/pluck.wav -p TIME=23 -p FDBK=65 -p WOW=40 -p FLUT=30 -p AGE=127
r pluck td_res "Resonant loop" "RES 90 (Q 2.8), LPF 80 (2.6 kHz), FDBK 75: every repeat rings a little more at 2.6 kHz, the old-delay colour." \
  $TD -i $D/pluck.wav -p TIME=23 -p FDBK=75 -p RES=90 -p LPF=80 -p WOW=30 -p FLUT=25
r pluck td_glide "TIME jump: tape glide" "TIME jumps from 23 to 11 at 3 s and back to 31 at 6 s. The delay glides like a tape motor changing speed (the pitch bends), instead of clicking." \
  $TD -i $D/pluck.wav -p TIME=23 -p FDBK=65 -p WOW=20 -p FLUT=20 -at 3 TIME=11 -at 6 TIME=31

g dub stabs.wav "Resonant loop filters, dub style" "The loop filters are now resonant (RES: low-pass Q 0.7..5, high-pass Q 0.7..1.4, passband trimmed as RES rises). High FDBK with high RES makes the loop sing at the corner; the saturator holds it. Send mix, VOL 64."
r dub td_dub_lp "Low-pass sweep, RES 110, FDBK 90" "LPF sweeps 40 -> 115 -> 40 (510 Hz -> 11 kHz -> 510 Hz) over 12 s, RES 110 (Q 3.9), FDBK 90 (78 %), WID 100 (towards ping-pong), DRIV 50." \
  $TD -i $D/stabs.wav -p TIME=15 -p FDBK=90 -p RES=110 -p LPF=40 -p WID=100 -p DRIV=50 -p WOW=20 -p FLUT=15 -ramp 0 6 LPF 40 115 -ramp 6 12 LPF 115 40
r dub td_dub_hp "High-pass sweep, RES 100" "HPF sweeps 10 -> 100 -> 10 (30 Hz -> 1.3 kHz) over 12 s with RES 100 and LPF 100: the repeats thin out and whistle at the high-pass corner." \
  $TD -i $D/stabs.wav -vol 50 -p TIME=15 -p FDBK=85 -p RES=100 -p HPF=10 -p LPF=100 -ramp 0 6 HPF 10 100 -ramp 6 12 HPF 100 10
r dub td_dub_band "Narrow band, RES 90" "HPF 88 (790 Hz) and LPF 80 (2.6 kHz) with RES 90: a resonant band-pass, telephone-like repeats." \
  $TD -i $D/stabs.wav -p TIME=23 -p FDBK=80 -p RES=90 -p HPF=88 -p LPF=80 -p WOW=25
r dub td_dub_sing "Self-oscillation, held" "RES 127, FDBK ramps 70 -> 127 (110 %) at 2-4 s and stays, LPF sweeps slowly 60 -> 95 (1.2 -> 4.9 kHz), then FDBK drops to 40 at 9 s. The loop sings at the filter corner, bounded by the saturator, and dies away cleanly." \
  $TD -i $D/stabs.wav -p TIME=15 -p FDBK=70 -p RES=127 -p LPF=60 -p WID=100 -ramp 2 4 FDBK 70 127 -ramp 2 9 LPF 60 95 -at 9 FDBK=40

g width pluck.wav "Stereo: WID" "The melody's notes are panned a little left and right. WID 0 folds the echoes to mono, 64 keeps the input's own stereo (as the clean echo above), 127 is ping-pong. Send mix, VOL 64."
r width td_wid_mono "WID 0: mono" "Echoes centred." $TD -i $D/pluck.wav -p TIME=23 -p FDBK=60 $CLEAN -p WID=0
r width td_wid_pp "WID 127: ping-pong" "The input (summed to mono) enters on the left; each repeat crosses to the other side." $TD -i $D/pluck.wav -p TIME=23 -p FDBK=60 $CLEAN -p WID=127
r width td_wid_sweep "WID sweep 0 -> 127" "From mono through stereo to full ping-pong over 12 s, no clicks." $TD -i $D/pluck.wav -p TIME=23 -p FDBK=60 $CLEAN -p WID=0 -ramp 0 12 WID 0 127

g agehits clave.wav "AGE on single hits" "One clave hit per bar, lots of silence. Send mix."
r agehits td_age_sweep "AGE sweep 0 -> 127 on single hits" "AGE rises from 0 to 127 over 8 s, FDBK 75: listen to the echoes' tails for hiss and ticks; between hits it stays quiet, because both follow the echoes." \
  $TD -i $D/clave.wav -p TIME=31 -p FDBK=75 -p WOW=20 -p FLUT=20 -ramp 0 8 AGE 0 127

g pad pad.wav "Wow and flutter on a held chord pad" "Sustained chords make pitch drift easy to hear. Send mix, TIME 31 (a quarter note), FDBK 60."
r pad td_pad_clean "No wow, no flutter" "The reference." $TD -i $D/pad.wav -p TIME=31 -p FDBK=60 $CLEAN
r pad td_pad_wowsweep "WOW sweep 0 -> 127" "WOW rises from 0 to 127 over 8 s (0 to 2 % pitch): the echoes drift further and further out of tune. FLUT 0." \
  $TD -i $D/pad.wav -p TIME=31 -p FDBK=60 $CLEAN -ramp 0 8 WOW 0 127
r pad td_pad_flut "Flutter only, FLUT 110" "FLUT 110 (0.6 %): the fast warble, no slow drift." $TD -i $D/pad.wav -p TIME=31 -p FDBK=60 $CLEAN -p FLUT=110
r pad td_pad_wf "W&F macro sweep 0 -> 127" "The one-knob W&F of the 1-page layout: wow first, flutter joins past 40." \
  $TD -i $D/pad.wav -p TIME=31 -p FDBK=60 -p AGE=0 -ramp 0 8 WF 0 127
r pad td_pad_dark "Dark loop, TONE 20" "TONE 20 (the 1-page macro): the loop low-pass falls to about 800 Hz, so each repeat is darker; RES 40; light wow." \
  $TD -i $D/pad.wav -p TIME=31 -p FDBK=80 -p TONE=20 -p RES=40 -p WOW=30 -p FLUT=20

g stabs stabs.wav "Feedback and drive on chord stabs" "Send mix."
r stabs td_selfosc "Runaway feedback, tamed" "FDBK ramps to 127 (110 %) at 2-3 s, stays for 3 s, then drops to 50. Above 100 % the soft clip holds the level instead of exploding. TONE 40." \
  $TD -i $D/stabs.wav -p TIME=15 -p FDBK=50 -p TONE=40 -p WOW=30 -p FLUT=20 -ramp 2 3 FDBK 50 127 -at 6 FDBK=50
r stabs td_drive "High drive, DRIV 110" "DRIV 110 (+13.5 dB into the saturator, level kept): the repeats get thicker and rounder." \
  $TD -i $D/stabs.wav -p TIME=15 -p FDBK=75 -p DRIV=110 $CLEAN

g drums drums.wav "Tape delay on drums" "Send mix, eighth-note echo (TIME 15)."
r drums td_drums "Eighth-note tape echo" "TIME 15, FDBK 50, WOW 30, FLUT 30, AGE 40, WID 96 (half ping-pong)." $TD -i $D/drums.wav -p TIME=15 -p FDBK=50 -p WOW=30 -p FLUT=30 -p AGE=40 -p WID=96

echo "compressor"
MB="-fx mbcomp -meter"
OFF="-p L.RAT=0 -p M.RAT=0 -p H.RAT=0"
ALL() { echo "-p L.$1 -p M.$1 -p H.$1"; }
g mix mix.wav "3 bands, each like the stock compressor, vs the stock compressor" "Insert: the compressor's output only. Each band now has the stock master compressor's design (peak detector, hard knee, stock ratios and time laws, makeup 0..+24 dB). Here all three bands get the same moderate settings, and the stock button is the stock compressor at exactly those knobs, full band. MUP per band gives each band its average level back. Crossovers 196 Hz and 2.6 kHz."
r mix mb_glue "Glue: -20 dB, 2:1, 10 ms, 150 ms" "Every band THR 85 (-19.8 dB), RAT 2:1, ATK 40 (10 ms), REL 44 (151 ms); MUP L 24, M 14, H 7 (+4.6, +2.7, +1.3 dB). Average reduction 2.1 / 1.7 / 0.8 dB." \
  $MB -i $D/mix.wav $(ALL THR=85) $(ALL RAT=2) $(ALL ATK=40) $(ALL REL=44) -p L.MUP=24 -p M.MUP=14 -p H.MUP=7
r mix mb_punch "Firmer: -23 dB, 3:1, 6 ms, 200 ms" "Every band THR 78 (-23.1 dB), RAT 3:1, ATK 30 (5.7 ms), REL 50 (203 ms); MUP 43 / 29 / 18. Average reduction 4.5 / 3.9 / 2.3 dB." \
  $MB -i $D/mix.wav $(ALL THR=78) $(ALL RAT=3) $(ALL ATK=30) $(ALL REL=50) -p L.MUP=43 -p M.MUP=29 -p H.MUP=18

g bands mix.wav "One band, or different times per band, on a mix" "Insert. Compressing one band changes that band's level against the other two, so a heavy single-band setting can sound like an EQ; MUP gives the band its level back, and the numbers under each render show what is left."
r bands mb_bass "Bass tamed (low band only)" "Low band THR 80 (-22.2 dB), 4:1, ATK 40 (10 ms), REL 50 (203 ms), MUP 41 (+7.8 dB); mid and high at 1:1 (untouched). The bass sits steadier; its average level is back." \
  $MB -i $D/mix.wav -p L.THR=80 -p L.RAT=4 -p L.ATK=40 -p L.REL=50 -p L.MUP=41 -p M.RAT=0 -p H.RAT=0
r bands mb_bass_nomup "Bass tamed, no makeup" "The same without MUP: the low band ends up about 8 dB lower than the rest. This is the 'EQ on a band' effect: here it is only the missing makeup." \
  $MB -i $D/mix.wav -p L.THR=80 -p L.RAT=4 -p L.ATK=40 -p L.REL=50 -p M.RAT=0 -p H.RAT=0
r bands mb_times "Fast highs, slow lows" "Every band 3:1. Low THR 85, ATK 64 (25 ms), REL 90 (850 ms); mid THR 80, ATK 40 (10 ms), REL 50 (203 ms); high THR 75, ATK 8 (0.5 ms), REL 25 (47 ms). MUP 30 / 27 / 17 (the low band's 25 ms attack lets the kick through, so less makeup there)." \
  $MB -i $D/mix.wav -p L.THR=85 -p L.RAT=3 -p L.ATK=64 -p L.REL=90 -p M.THR=80 -p M.RAT=3 -p M.ATK=40 -p M.REL=50 -p H.THR=75 -p H.RAT=3 -p H.ATK=8 -p H.REL=25 -p L.MUP=30 -p M.MUP=27 -p H.MUP=17

g hats drums.wav "One band: taming harsh hats" "Insert, drums. Only the high band (above 2.6 kHz) compresses; kick and snare body stay as they are."
r hats mb_hats "Hats tamed (high band only)" "High band THR 66 (-28.8 dB), 4:1, ATK 12 (1.0 ms), REL 30 (64 ms), MUP 36 (+6.8 dB); low and mid at 1:1. The hat peaks come down, the average level of the band stays." \
  $MB -i $D/drums.wav -p H.THR=66 -p H.RAT=4 -p H.ATK=12 -p H.REL=30 -p H.MUP=36 -p L.RAT=0 -p M.RAT=0

g duck bassline.wav "Sidechain: a kick ducks the bass" "Program: held bass and pad. Key: a four-on-the-floor kick (dry/kick4.wav), SCS 1 (one key for all bands). Insert; the key itself is not in the output."
r duck mb_duck_low "Keyed, low band only" "SCS 1; low band THR 70 (-26.9 dB), 6:1, ATK 0 (0.1 ms), REL 50 (203 ms); mid and high at 1:1, so only the bass ducks and the pad keeps going." \
  $MB -i $D/bassline.wav -key $D/kick4.wav -p SCS=1 -p L.THR=70 -p L.RAT=6 -p L.ATK=0 -p L.REL=50 -p M.RAT=0 -p H.RAT=0
r duck mb_duck_all "Keyed, all bands" "The same settings on every band: the classic full-mix pump." \
  $MB -i $D/bassline.wav -key $D/kick4.wav -p SCS=1 $(ALL THR=70) $(ALL RAT=6) $(ALL ATK=0) $(ALL REL=50)

g setup mix.wav "Setup only: the crossover alone" "Not a compressor demo. All ratios 1:1: only the crossover, whose bands sum to a flat allpass. (The band solos, LSTN, were dropped with T6.3 CMP0: no knob for them on the unit.)"
r setup mb_neutral "Setup: all bands, 1:1" "Only the crossover: the bands sum to a flat allpass, so it should sound the same as dry (the waveform changes, the spectrum does not)." $MB -i $D/mix.wav $OFF

echo "flanger"
FL="-fx flanger -tail 3 -bpm 120 -vol 127 -gain -12"
g flswell chords.wav "Flanger: swells on bright chords" "The chorus slot's new effect. Send mix with VOL 127 (dry + wet at unity, so the comb's notches are full depth) and the input 12 dB down: resonant settings add 10 dB or more at full send. The stock button is the stock chorus at the same DPTH/SPD/HPF/WDTH knobs (its rate law: 0.28 + 2.82 x SPD/127 Hz)."
r flswell fl_swell "Default patch: slow, deep, resonant" "DPTH 96 (0.16..5 ms), SPD 30 (0.087 Hz: 11.5 s per sweep), sine, FDBK 107 (+68 %), DAMP 12 (10 kHz), MAN 50 (0.9 ms), WDTH 48 (68 degrees), HPF 32 (64 Hz)." \
  $FL -i $D/chords.wav
r flswell fl_classic "Classic triangle sweep" "SHP 1 (triangle: an even sweep in pitch), SPD 50 (0.23 Hz), DPTH 80, FDBK 100 (+57 %), MAN 45, WDTH 32." \
  $FL -i $D/chords.wav -p SHP=1 -p SPD=50 -p DPTH=80 -p FDBK=100 -p MAN=45 -p WDTH=32
r flswell fl_negative "Negative: hollow" "FDBK 20 (-68 %) and POL inverted: the notches move to the even places, the sound turns hollow and nasal. Otherwise the default patch." \
  $FL -i $D/chords.wav -p FDBK=20 -p POL=1
r flswell fl_rotate "Stereo rotation" "WDTH 127 (left and right LFOs 180 degrees apart) plus OFS 40 (0.5 ms): the sweep moves across the stereo field. SPD 40 (0.14 Hz)." \
  $FL -i $D/chords.wav -p WDTH=127 -p OFS=40 -p SPD=40
r flswell fl_fast "Fast, shallow" "SPD 100 (2.7 Hz), DPTH 40, FDBK 90 (+40 %): towards a vibrato-flange." \
  $FL -i $D/chords.wav -p SPD=100 -p DPTH=40 -p FDBK=90
r flswell fl_extreme "Extreme: full depth and feedback" "DPTH 127, FDBK 127 (+99 %), DAMP 0, SPD 60 (0.38 Hz). The soft limit keeps the loop in check." \
  $FL -i $D/chords.wav -p DPTH=127 -p FDBK=127 -p DAMP=0 -p SPD=60

g fldrums drums.wav "Flanger on drums: jets and zaps" "Send mix, VOL 127, input -12 dB. The stock button is the stock chorus at the same DPTH/SPD/HPF/WDTH knobs."
r fldrums fl_jet "Jet sweep" "The default patch on drums: the classic jet." $FL -i $D/drums.wav
r fldrums fl_zap_sq "Stepped zaps: square, synced" "SHP 4 (square), SYNC 10 (one cycle per 1/8), SLEW 30 (4 ms), DPTH 70, MAN 40 (0.5 ms), FDBK 110 (+72 %): the delay jumps between two places on the eighths, rhythmic zaps without clicks." \
  $FL -i $D/drums.wav -p SHP=4 -p SYNC=10 -p SLEW=30 -p DPTH=70 -p MAN=40 -p FDBK=110
r fldrums fl_zap_rnd "Random steps, synced" "SHP 5 (random step), SYNC 11 (a new value every 1/16), SLEW 20 (3 ms), DPTH 90, FDBK 105 (+64 %)." \
  $FL -i $D/drums.wav -p SHP=5 -p SYNC=11 -p SLEW=20 -p DPTH=90 -p FDBK=105

g flks hits.wav "Comb resonator: Karplus-Strong plucks" "Short delay, feedback near 100 %, damped loop: each hit rings like a plucked string at the pitch MAN sets (1 semitone per step, MAN 66 = A4 440 Hz). DPTH 0, HPF 0. Hits: noise bursts, snare, kick. Send mix, VOL 127, input -16 dB (the ringing is loud)."
r flks fl_ks_a2 "A2 (110 Hz), DAMP 30" "MAN 90, FDBK 127 (+99 %), DAMP 30 (about 4.8 kHz)." $FL -gain -16 -i $D/hits.wav -p DPTH=0 -p HPF=0 -p FDBK=127 -p MAN=90 -p DAMP=30
r flks fl_ks_a3 "A3 (220 Hz), DAMP 48" "MAN 78, FDBK 127, DAMP 48 (about 2.6 kHz): darker, dies faster in the highs." $FL -gain -16 -i $D/hits.wav -p DPTH=0 -p HPF=0 -p FDBK=127 -p MAN=78 -p DAMP=48
r flks fl_ks_e4 "E4 (330 Hz), negative" "MAN 71 (E4 as a comb), FDBK 0 (-99 %): negative feedback rings an octave lower (E3) with odd harmonics only: hollow, clarinet-like." $FL -gain -16 -i $D/hits.wav -p DPTH=0 -p HPF=0 -p FDBK=0 -p MAN=71 -p DAMP=30
r flks fl_ks_sweep "Pitched comb, slow sweep" "MAN 78, DPTH 12 (+-0.3 octave), SPD 20, FDBK 124: the ringing pitch glides." $FL -gain -16 -i $D/hits.wav -p DPTH=12 -p SPD=20 -p HPF=0 -p FDBK=124 -p MAN=78 -p DAMP=36

g flslap pluck.wav "Past flanging: chorus and slapback range" "MAN reaches 77 ms. Send mix, VOL 127, input -12 dB."
r flslap fl_chorus "Chorus range" "MAN 100 (16 ms), DPTH 10, SPD 70 (0.6 Hz), FDBK 64 (none), WDTH 90, OFS 30 (0.3 ms): a chorus without feedback." \
  $FL -i $D/pluck.wav -p MAN=100 -p DPTH=10 -p SPD=70 -p FDBK=64 -p WDTH=90 -p OFS=30
r flslap fl_slap "Slapback" "MAN 122 (58 ms), DPTH 0, FDBK 74 (+16 %), OFS 80 (2 ms: the right repeat a little later), DAMP 50 (about 2.5 kHz)." \
  $FL -i $D/pluck.wav -p MAN=122 -p DPTH=0 -p FDBK=74 -p OFS=80 -p DAMP=50

echo "flanger on the unit's knobs (T5.6)"
FU2="-fx flgunit2 -tail 3 -bpm 120 -vol 127 -gain -12"
FU1="-fx flgunit1 -tail 3 -bpm 120 -vol 127 -gain -12"
g flunit chords.wav "Flanger retune (T5.6): one knob from phaser-like to chorus" "On the unit the flanger gets only the chorus page's four knobs: A DPTH, B SPD, C, D WDTH. On CMP4 C is HPF and the centre delay is fixed at 0.9 ms with +68 % feedback, so neither end is reachable. Retuned, C is TIME: the centre delay from 0.05 ms (phaser-like) through 0.9 ms (TIME 32, exactly CMP4's flanger) to 26 ms (chorus); from TIME 64 to 100 the feedback fades to 0 and the depth range to a tenth. The input high-pass is fixed at 64 Hz. Each render has CMP4 at the same four knobs next to it (row CMP4). Send mix, VOL 127, input -12 dB."
for c in "phaser 8 45 50 64 Phaser-like:_TIME_8_(0.1_ms)" "jet 20 100 30 48 Jet:_TIME_20_(0.3_ms)" "flanger 32 96 30 48 Flanger:_TIME_32_(0.9_ms,_CMP4's_own)" "deep 56 70 40 90 Long_flanger:_TIME_56_(2.1_ms)" "blend 82 60 60 100 Flange-chorus:_TIME_82_(5.3_ms,_halfway_through_the_fade)" "chorus 110 50 70 127 Chorus:_TIME_110_(14_ms)" "wide 127 100 80 127 Wide_chorus:_TIME_127_(26_ms)"; do
    set -- $c
    t=$(echo "$6" | tr _ ' ')
    r flunit flu_$1 "$t" "TIME $2, DPTH $3, SPD $4, WDTH $5." $FU2 -i $D/chords.wav -p TIME=$2 -p DPTH=$3 -p SPD=$4 -p WDTH=$5
    rc4 flunit flc4_$1 "CMP4 at the same knobs" "HPF $2, DPTH $3, SPD $4, WDTH $5 on CMP4: MAN 0.9 ms, +68 %." $FU1 -i $D/chords.wav -p HPF=$2 -p DPTH=$3 -p SPD=$4 -p WDTH=$5
done
g flunitd drums.wav "Flanger retune (T5.6) on drums" "As above, on drums. Row CMP4: the same four knobs on CMP4."
for c in "phaser 6 50 45 64 Phaser-like:_TIME_6" "jet 24 110 25 48 Jet:_TIME_24" "flanger 32 96 30 48 Flanger:_TIME_32_(CMP4's_own)" "chorus 105 60 75 127 Chorus:_TIME_105"; do
    set -- $c
    t=$(echo "$6" | tr _ ' ')
    r flunitd flud_$1 "$t" "TIME $2, DPTH $3, SPD $4, WDTH $5." $FU2 -i $D/drums.wav -p TIME=$2 -p DPTH=$3 -p SPD=$4 -p WDTH=$5
    rc4 flunitd flc4d_$1 "CMP4 at the same knobs" "HPF $2, DPTH $3, SPD $4, WDTH $5 on CMP4." $FU1 -i $D/drums.wav -p HPF=$2 -p DPTH=$3 -p SPD=$4 -p WDTH=$5
done
g flunitk pluck.wav "Flanger retune (T5.6): a new kit and the WDTH 127 fix" "A new kit's stock chorus knobs: DPTH 127, SPD 32, C 0, WDTH 127. On CMP4 that is the 0.9 ms flanger, but WDTH 127 plays both sides in phase there: selcc converts the half-cycle LFO offset with a signed TRUNC that overflows to about 0 on the DSP (fx/flanger_unit.h). Retuned, C 0 is the shortest, phaser-like end (if new kits should start as the flanger instead, C's default can be 32: one number in the main OS) and WDTH 127 is 180 degrees. Also TIME 32 with WDTH 126 and 127 on both."
r flunitk fluk_new "New kit, retuned" "DPTH 127, SPD 32, TIME 0, WDTH 127 (180 degrees)." $FU2 -i $D/pluck.wav -p DPTH=127 -p SPD=32 -p TIME=0 -p WDTH=127
rc4 flunitk flc4k_new "New kit on CMP4" "DPTH 127, SPD 32, HPF 0, WDTH 127: as the unit plays it, both sides in phase." $FU1 -i $D/pluck.wav -p DPTH=127 -p SPD=32 -p HPF=0 -p WDTH=127
r flunitk fluk_w127 "TIME 32, WDTH 127, retuned" "The classic flanger with the two sides 180 degrees apart." $FU2 -i $D/pluck.wav -p DPTH=96 -p SPD=30 -p TIME=32 -p WDTH=127
rc4 flunitk flc4k_w127 "HPF 32, WDTH 127 on CMP4" "As the unit plays it: in phase." $FU1 -i $D/pluck.wav -p DPTH=96 -p SPD=30 -p HPF=32 -p WDTH=127
r flunitk fluk_w126 "TIME 32, WDTH 126, retuned" "177 degrees: the same on CMP4 and retuned." $FU2 -i $D/pluck.wav -p DPTH=96 -p SPD=30 -p TIME=32 -p WDTH=126

g se_fdbk pluck.wav "TAPE: FDBK on the stock law (SC01)" "SC01 puts INTENSITY (FDBK) on the stock delay's law: each head feeds back FDBK/64, so the display's 100 % (FDBK 64) repeats for ever and above it the repeats grow until the tape's saturator holds them (and the echo's wet ceiling, 0.7, holds what leaves the slot), as on the stock delay. SE01 had 1.1 (FDBK/127)^1.5: 14 % at 32, 39 % at 64, 77 % at 100, 110 % at 127. The input at the unit's level (13 dB down: a full-scale sound reaches the delay slot at about 0.13, notes/T5.3 10.2), VOL 127, no band (BW 127), AGE 0, MONO, TIME 15. Each FDBK with SE01's law next to it (se01_*). Since SC02 two or three heads share the gain (group se_share). Keep the volume low at 64 and above."
SEF="-fx spaceecho -tail 8 -bpm 120 -vol 127 -gain -13 -p TIME=15 -p AGE=0 -p BW=127 -p PAN=0 -p WID=0"
r se_fdbk se_fb32_1 "FDBK 32, head 1: 50 % per pass" "The repeats halve each time (-6 dB)." $SEF -i $D/pluck.wav -p MODE=0 -p INT=32
rse01 se_fdbk se01_fb32_1 "SE01's law, FDBK 32, head 1: 14 %" "SE01: three repeats and gone." $SEF -i $D/pluck.wav -p MODE=0 -p INT=32
r se_fdbk se_fb64_1 "FDBK 64, head 1: 100 %, endless" "The display's 100 %: the repeats hold their level (only the 15 Hz DC blocker and the saturator's top take a little)." $SEF -i $D/pluck.wav -p MODE=0 -p INT=64
rse01 se_fdbk se01_fb64_1 "SE01's law, FDBK 64, head 1: 39 %" "SE01 at the same knob." $SEF -i $D/pluck.wav -p MODE=0 -p INT=64
r se_fdbk se_fb100_1 "FDBK 100, head 1: 156 %" "Each pass is louder than the last until the saturator holds it: a held, saturated loop." $SEF -i $D/pluck.wav -p MODE=0 -p INT=100
rse01 se_fdbk se01_fb100_1 "SE01's law, FDBK 100, head 1: 77 %" "SE01 at the same knob." $SEF -i $D/pluck.wav -p MODE=0 -p INT=100
r se_fdbk se_fb127_1 "FDBK 127, head 1: 198 %" "The stock maximum: the loop saturates within a few passes and holds." $SEF -i $D/pluck.wav -p MODE=0 -p INT=127
rse01 se_fdbk se01_fb127_1 "SE01's law, FDBK 127, head 1: 110 %" "SE01's maximum: a slow build to the saturator." $SEF -i $D/pluck.wav -p MODE=0 -p INT=127
r se_fdbk se_fb32_123 "FDBK 32, heads 1+2+3: 50 % shared" "Three heads sharing 50 % (17 % each): the repeats die away as on one head, in a denser pattern." $SEF -i $D/pluck.wav -p MODE=6 -p INT=32
rse01 se_fdbk se01_fb32_123 "SE01's law, FDBK 32, heads 1+2+3: 3 x 14 %" "SE01 at the same knob." $SEF -i $D/pluck.wav -p MODE=6 -p INT=32
r se_fdbk se_fb64_123 "FDBK 64, heads 1+2+3: 100 % shared, endless" "The display's 100 % on three heads (33 % each): the pattern keeps its level, as one head does at 64." $SEF -i $D/pluck.wav -p MODE=6 -p INT=64
rse01 se_fdbk se01_fb64_123 "SE01's law, FDBK 64, heads 1+2+3: 3 x 39 %" "SE01 at the same knob." $SEF -i $D/pluck.wav -p MODE=6 -p INT=64
r se_fdbk se_fb100_123 "FDBK 100, heads 1+2+3: 156 % shared" "Grows until the saturator holds it, as one head does at 100." $SEF -i $D/pluck.wav -p MODE=6 -p INT=100
r se_fdbk se_fb127_123 "FDBK 127, heads 1+2+3: 198 % shared" "The stock maximum: held at the ceiling." $SEF -i $D/pluck.wav -p MODE=6 -p INT=127
r se_fdbk se_fb_sweep "FDBK sweep 0 -> 127 -> 0, head 1" "FDBK rises over 8 s and falls again over 8 s: the decay stretches to endless at 64, then the loop holds itself; turning it down below 64 lets it die away." $SEF -i $D/pluck.wav -p MODE=0 -p INT=0 -ramp 0 8 INT 0 127 -ramp 8 16 INT 127 0

g se_share pluck.wav "TAPE: FDBK shared by the heads (SC02)" "SC02: the heads that play share FDBK/64 (each feeds back 1/2 or 1/3 of it), so the display's 100 % (FDBK 64) is endless in every MODE, as on one head. SC01 gave each head FDBK/64, so two heads ran away from about 32 (the owner, 2026-10-21: MODE 4 ran away at 9 o'clock, \"weird\") and three from about 21. MODE 1 is unchanged. Each render with SC01's law next to it (sc01_*). The input at the unit's level (13 dB down), VOL 127, BW 127, AGE 0, MONO, TIME 15. Keep the volume low."
for m in 1 4 7; do
    for v in 32 64; do
        mi=$((m - 1))
        r se_share se_sh_m${m}_${v} "MODE $m, FDBK $v" "SC02: FDBK $v shared by the heads of MODE $m." $SEF -i $D/pluck.wav -p MODE=$mi -p INT=$v
        rsc01 se_share sc01_sh_m${m}_${v} "SC01's law: MODE $m, FDBK $v" "SC01: FDBK $v on every head of MODE $m." $SEF -i $D/pluck.wav -p MODE=$mi -p INT=$v
    done
done

echo "spring reverb (T5.5)"
# SPNG, the spring reverb for the reverb slot (fx/spring.c, notes/T5.5-spring-reverb.md). Send mix as on the unit: dry +
# VOL x wet, VOL 64; the input 12 dB down (about the level a full-scale track reaches the FX slots at, notes/T5.3 10.2,
# which is what DWEL's saturator is set for). After make stock, every SPNG render has the stock reverb next to it at the
# same A, B, E, F knobs (PRE, DEC, HPF and LPF = TONE; FREQ and GAIN neutral): the same kit on the stock firmware.
SP="-fx spring -tail 5 -vol 64 -gain -12"
SPA="the stock reverb at the same PRE, DEC, HPF and LPF (= TONE) knobs is next to it"
g sp_range pluck.wav "SPNG spring reverb: across the range (plucked melody)" "T5.5. Three springs (round trips 1 : 1.17 : 1.39) at 12 kHz, each a loop with 16 stretched allpasses that delay the lows more than the highs, so every click comes back as a falling chirp, the drip, smeared more on every round trip. Page A PRE, B DEC, C DRIP, D DWEL, E HPF, F TONE, G WDTH, H TENS. The init patch: PRE 0, DEC 64 (2.2 s), DRIP 64, DWEL 64 (0 dB), HPF 0 (40 Hz), TONE 127 (open), WDTH 96, TENS 64 (27 ms to the far end). Send mix, VOL 64, input -12 dB; $SPA."
r sp_range sp_def "Init patch: a classic tank" "The defaults: a medium tank, 2.2 s, a clear drip, the springs spread wide." $SP -i $D/pluck.wav
r sp_range sp_bright "Short and bright" "DEC 24 (0.6 s), TENS 110 (tight: 17 ms to the far end, a fast flutter), DRIP 40, HPF 50 (140 Hz), TONE 127: a small, splashy tank." $SP -i $D/pluck.wav -p DEC=24 -p TENS=110 -p DRIP=40 -p HPF=50
r sp_range sp_surf "Classic surf drip" "DRIP 112 (deep), DWEL 100 (+13.5 dB into the saturator, +6.8 dB after the make-up: the attacks rounded), DEC 72 (2.9 s), TENS 48 (slack: 31 ms), TONE 110 (6.6 kHz), HPF 24 (74 Hz)." $SP -i $D/pluck.wav -p DRIP=112 -p DWEL=100 -p DEC=72 -p TENS=48 -p TONE=110 -p HPF=24
r sp_range sp_dark "Long and dark" "DEC 112 (10 s), TONE 24 (1 kHz in the loops: every round trip darker), DRIP 72, TENS 28 (slack: 38 ms), WDTH 127." $SP -i $D/pluck.wav -p DEC=112 -p TONE=24 -p DRIP=72 -p TENS=28 -p WDTH=127
r sp_range sp_dwell "Heavy dwell" "DWEL 127 (+24 dB into the saturator, +12 dB after the make-up): the tank driven hard, the attacks crash and splash. DRIP 96, DEC 80 (3.7 s)." $SP -i $D/pluck.wav -p DWEL=127 -p DRIP=96 -p DEC=80
r sp_range sp_flat "DRIP 0: no dispersion" "The reference for DRIP: the same three loops without the allpasses' spread, a metallic flutter echo. Otherwise the init patch." $SP -i $D/pluck.wav -p DRIP=0
r sp_range sp_mono "WDTH 0: one mono tank" "All three springs in the middle, as a real tank (one output): mono. Otherwise the init patch." $SP -i $D/pluck.wav -p WDTH=0

g sp_drums drums.wav "SPNG spring reverb on drums" "The drip shows best on hits. Send mix, VOL 64, input -12 dB; $SPA."
r sp_drums sp_dr_def "Init patch" "The defaults." $SP -i $D/drums.wav
r sp_drums sp_dr_surf "Classic surf drip" "DRIP 112, DWEL 100, DEC 72, TENS 48, TONE 110, HPF 24 (as in the first group)." $SP -i $D/drums.wav -p DRIP=112 -p DWEL=100 -p DEC=72 -p TENS=48 -p TONE=110 -p HPF=24
r sp_drums sp_dr_dwell "Heavy dwell, short" "DWEL 127, DRIP 96, DEC 40 (1 s), HPF 40 (110 Hz): the kick stays out, the snare splashes." $SP -i $D/drums.wav -p DWEL=127 -p DRIP=96 -p DEC=40 -p HPF=40
r sp_drums sp_dr_tight "Tight and bright" "TENS 127 (tight: 14.5 ms), DEC 30 (0.8 s), DRIP 50, HPF 60 (180 Hz): a small, boingy tank." $SP -i $D/drums.wav -p TENS=127 -p DEC=30 -p DRIP=50 -p HPF=60

g sp_knobs clave.wav "SPNG: one knob at a time (single hits)" "One clave hit per bar, lots of silence, so each tail is heard on its own; one knob sweeps over 8 s, the others at the init patch. Send mix, VOL 64, input -12 dB."
r sp_knobs sp_k_drip "DRIP sweep 0 -> 127" "From a metallic flutter (no dispersion) to a deep drip: the chirp in each echo gets longer and lower." $SP -i $D/clave.wav -p DRIP=0 -ramp 0 8 DRIP 0 127
r sp_knobs sp_k_tens "TENS sweep 0 -> 127" "From a slack spring (50 ms to the far end: slow flutter) to a tight one (14.5 ms: fast). While it moves, the tails bend in pitch (the round trips glide, at most half a sample per sample)." $SP -i $D/clave.wav -p TENS=0 -ramp 0 8 TENS 0 127
r sp_knobs sp_k_tone "TONE sweep 127 -> 0" "The loop low-pass from open (9.6 kHz) to 600 Hz: the tails darken and get shorter at the top." $SP -i $D/clave.wav -p DEC=90 -ramp 0 8 TONE 127 0
r sp_knobs sp_k_dwel "DWEL sweep 0 -> 127" "Drive into the tank -24 -> +24 dB (half of it made up after): quiet and clean, then louder and saturated, the hits splash." $SP -i $D/clave.wav -p DWEL=0 -ramp 0 8 DWEL 0 127
r sp_knobs sp_k_pre "PRE sweep 0 -> 127" "Pre-delay 0 -> 250 ms (square law): the tank answers later. While it moves the input glides (a quarter sample per sample), so the tails bend a little." $SP -i $D/clave.wav -p PRE=0 -ramp 0 8 PRE 0 127

g sp_chords chords.wav "SPNG on held chords" "A sustained source: the springs' resonances and the flutter. Send mix, VOL 64, input -12 dB; $SPA."
r sp_chords sp_ch_def "Init patch" "The defaults." $SP -i $D/chords.wav
r sp_chords sp_ch_dark "Long and dark" "DEC 112, TONE 24, DRIP 72, TENS 28, WDTH 127." $SP -i $D/chords.wav -p DEC=112 -p TONE=24 -p DRIP=72 -p TENS=28 -p WDTH=127

# TAPE into SPNG: the parked tape echo (fx/spaceecho.c as it is) in the delay slot and SPNG in the reverb slot, as the FX
# bus runs them: out = dry + DVOL x echo + VOL x spring(RSEND x dry + DREV x echo) (fxrender -fx tapespring)
TS="-fx tapespring -tail 6 -bpm 120 -vol 64 -gain -12"
TSP="-p T.TIME=15 -p T.MODE=3 -p T.INT=40 -p T.AGE=48 -p T.BW=127 -p T.PAN=1 -p T.WID=100"
g sp_tape pluck.wav "TAPE into SPNG: the tape echo and the spring tank together" "The tape echo (TAPE, the parked delay-slot effect) and SPNG as the FX bus runs them: out = dry + DVOL x echo + VOL x spring(RSEND x dry + DREV x echo), where DREV is the delay page's REV (the echoes into the tank) and RSEND the track's reverb send. TAPE: heads 1+2 (TIME 15: an eighth and a quarter), INTENSITY 40 (62 % shared), AGE 48, no band, ALT at WIDTH 100. SPNG at its init patch unless said. DVOL 64, VOL 64, input -12 dB."
r sp_tape ts_tape "TAPE alone" "The echo with the spring silent (VOL 0): the reference." $TS -i $D/pluck.wav $TSP -vol 0 -drev 0
r sp_tape ts_spring "SPNG alone" "The spring with the echo silent (DVOL 0, DREV 0): the reference." $TS -i $D/pluck.wav $TSP -dvol 0 -drev 0
r sp_tape ts_into "TAPE into SPNG" "DREV 64 and RSEND 64: the dry and every echo go into the tank, so the repeats drip too: the classic tape echo with spring." $TS -i $D/pluck.wav $TSP -drev 64 -rsend 64
r sp_tape ts_par "TAPE and SPNG side by side" "DREV 0, RSEND 127: the tank hears only the dry; the echoes stay clean of it." $TS -i $D/pluck.wav $TSP -drev 0 -rsend 127
r sp_tape ts_dub "Dub: three heads, worn tape, a long dark tank" "TAPE: MODE 7 (heads 1+2+3), INTENSITY 52 (81 % shared), AGE 80, a band (CENTER 64, BW 60). SPNG: DEC 100 (6.9 s), TONE 40 (1.4 kHz), DRIP 96, DWEL 90, TENS 40 (34 ms). DREV 100, RSEND 48." $TS -i $D/pluck.wav -p T.TIME=15 -p T.MODE=6 -p T.INT=52 -p T.AGE=80 -p T.CTR=64 -p T.BW=60 -p T.PAN=1 -p T.WID=100 -p DEC=100 -p TONE=40 -p DRIP=96 -p DWEL=90 -p TENS=40 -drev 100 -rsend 48
r sp_tape ts_stabs "TAPE into SPNG on chord stabs" "Head 1 (TIME 23: a dotted eighth), INTENSITY 30 (47 %), AGE 40; SPNG surf (DRIP 112, DWEL 100, DEC 72, TENS 48, TONE 110); DREV 64, RSEND 64." $TS -i $D/stabs.wav -p T.TIME=23 -p T.MODE=0 -p T.INT=30 -p T.AGE=40 -p T.BW=127 -p T.PAN=1 -p T.WID=100 -p DRIP=112 -p DWEL=100 -p DEC=72 -p TENS=48 -p TONE=110 -drev 64 -rsend 64

# MODE (2026-10-24): one continuous knob through the characters of sp_range, the page's C (fx/spring.c spring_mode_map).
# Anchors: 0 short and bright, 64 classic (the init patch), 85 heavy dwell, 106 surf drip, 127 long and dark; linear in
# between, in the knobs' own (log) laws. The other knobs at their defaults trim nothing; the stock pair takes MODE's DEC,
# HPF and TONE as the stock reverb's DEC, HPF and LPF.
SPM="the stock reverb at the DEC, HPF and LPF that MODE puts there is next to each"
g sp_mode pluck.wav "SPNG MODE: one knob through the characters (plucked melody)" "MODE (C on the page) moves through the characters of the first SPNG group: 0 short and bright, 64 the classic tank (the init patch, bit for bit), 85 heavy dwell, 106 surf drip, 127 long and dark, blended in between. It sets the drip, the tension and the springs' spread, and a base for DEC, DWEL, HPF and TONE that those knobs trim (here they sit at their defaults). Send mix, VOL 64, input -12 dB; $SPM."
for c in "0 Short_and_bright DEC_0.64_s,_DRIP_40,_HPF_142_Hz,_TENS_17_ms_(tight),_springs_closer_(spread_0.85)" \
         "24 Small_tank DEC_1_s,_DRIP_49,_HPF_88_Hz,_TENS_20_ms,_spread_0.91" \
         "48 Almost_classic DEC_1.6_s,_DRIP_58,_HPF_55_Hz,_TENS_24_ms,_spread_0.96" \
         "64 Classic_tank_(the_init_patch) DEC_2.2_s,_DRIP_64,_HPF_40_Hz,_TENS_27_ms,_spread_1" \
         "80 Toward_heavy_dwell DEC_2.7_s,_DRIP_88,_DWEL_+18_dB_into_the_saturator,_TENS_27_ms" \
         "104 Near_surf_drip DEC_3.6_s,_DRIP_110,_DWEL_+14.5_dB,_HPF_69_Hz,_TONE_6.9_kHz,_TENS_31_ms,_spread_1.09" \
         "127 Long_and_dark DEC_10_s,_DRIP_72,_TONE_1_kHz,_TENS_38_ms_(slack),_springs_spread_wider_(1.25)"; do
    set -- $c
    r sp_mode sp_m_$1 "MODE $1: $(echo $2 | tr _ ' ')" "$(echo $3 | tr _ ' ')." $SP -i $D/pluck.wav -p MODE=$1
done
g sp_mode_drums drums.wav "SPNG MODE on drums" "The same MODE stills on drums. Send mix, VOL 64, input -12 dB; $SPM."
for c in "0 Short_and_bright" "24 Small_tank" "48 Almost_classic" "64 Classic_tank" "80 Toward_heavy_dwell" "104 Near_surf_drip" "127 Long_and_dark"; do
    set -- $c
    r sp_mode_drums sp_md_$1 "MODE $1: $(echo $2 | tr _ ' ')" "As in the plucked group." $SP -i $D/drums.wav -p MODE=$1
done
g sp_mode_hits clave.wav "SPNG MODE swept slowly (single hits)" "MODE 0 -> 127 over 12 s, one clave hit per bar: each tail is a step further along, from a small bright tank to a long dark one. While MODE moves, the tension changes and the tails bend in pitch (slew-limited, as TENS). Send mix, VOL 64, input -12 dB; $SPM."
r sp_mode_hits sp_ms_hits "MODE sweep 0 -> 127" "Over 12 s." $SP -i $D/clave.wav -p MODE=0 -ramp 0 12 MODE 0 127
g sp_mode_chords chords.wav "SPNG MODE swept slowly (held chords)" "MODE 0 -> 127 over 16 s on held chords. Send mix, VOL 64, input -12 dB; $SPM."
r sp_mode_chords sp_ms_chords "MODE sweep 0 -> 127" "Over 16 s." $SP -i $D/chords.wav -p MODE=0 -ramp 0 16 MODE 0 127

echo "manifest: $M"
