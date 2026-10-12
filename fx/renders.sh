#!/bin/sh
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
rm -f "$W"/td_*.wav "$W"/mb_*.wav "$W"/fl_*.wav

# group header: g GROUP DRYFILE "group title" "group description"
g() {
    printf 'group\t%s\tdry/%s\t%s\t\t%s\n' "$1" "$2" "$3" "$4" >> "$M"
}
# render: r GROUP ID "title" "description" fxrender-args...
r() {
    grp=$1; id=$2; title=$3; desc=$4; shift 4
    "$R" "$@" -o "$W/$id.wav" 2>&1 | sed 's/^/  /'
    args=$(echo "$*" | sed -e "s|$D/||g" -e 's|-i [^ ]* ||' -e 's|-key [^ ]* ||' -e 's|-fx [a-z]* ||')
    # column 7: the full fxrender arguments (dry paths relative to OUTDIR), read by stock_renders.py
    full=$(echo "$*" | sed -e "s|$D/|dry/|g")
    printf 'render\t%s\twav/%s.wav\t%s\t%s\t%s\t%s\n' "$grp" "$id" "$title" "$args" "$desc" "$full" >> "$M"
}

TD="-fx tapedelay -tail 4 -bpm 120 -vol 64"
CLEAN="-p WOW=0 -p FLUT=0 -p AGE=0"

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

g setup mix.wav "Setup only: hearing the bands (LSTN)" "Not compressor demos: LSTN solos a band so the crossovers can be set by ear. All ratios 1:1 here."
r setup mb_neutral "Setup: all bands, 1:1" "Only the crossover: the bands sum to a flat allpass, so it should sound the same as dry (the waveform changes, the spectrum does not)." $MB -i $D/mix.wav $OFF
r setup mb_lsn_low "Setup: band solo, LOW" "LSTN 1: only the low band (below 196 Hz)." $MB -i $D/mix.wav $OFF -p LSTN=1
r setup mb_lsn_mid "Setup: band solo, MID" "LSTN 2: 196 Hz to 2.6 kHz." $MB -i $D/mix.wav $OFF -p LSTN=2
r setup mb_lsn_high "Setup: band solo, HIGH" "LSTN 3: above 2.6 kHz." $MB -i $D/mix.wav $OFF -p LSTN=3
r setup mb_xsweep "Setup: crossover sweep, MID soloed" "LSTN 2. Over 16 s XLO sweeps 10 -> 80 (49 Hz -> 1.6 kHz) while XHI sweeps 127 -> 90 (16 kHz -> 2.6 kHz): the mid band narrows. No zipper noise." \
  $MB -i $D/mix.wav $OFF -p LSTN=2 -ramp 0 16 XLO 10 80 -ramp 0 16 XHI 127 90

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

echo "manifest: $M"
