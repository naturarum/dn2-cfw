| SPDX-License-Identifier: GPL-2.0-or-later
| lfo-dejavu-dn2 0.1 (Digitone II OS 1.12): the entries from the two LFO evaluators into dejavu.c.
| ColdFire V4; assemble with -mcpu=54455. Every site was read instruction by instruction and is checked against
| the stock bytes by dn2-cfw's scripts/dj01_make_mod.py. The LFO code itself stays stock: these stubs only pick
| what it reads for MODE on the added waves, give it our waveform for WAVE 7..15, and round the result on the
| scale waves.
|
| Evaluator A, FUN_40137a06 (every voice row's three LFOs, once per frame, called only from the frame handler):
|   0x400272d4  jsr 0x40137a06                       -> jsr dv_frame_hook (counts frames, then the stock call)
|   0x40137b60  moveq #6,d7 ; 0x40137b66 moveq #6,d3  the WAVE clamp 0..6 -> 0..15 (data: 7e06 -> 7e0f, 7606 -> 760f)
|   0x40137b88  mvs.b 0x50(a4),d1 ; move.l d6,d2      -> jsr dv_mode_a: MODE, or 0 (FREE) when WAVE >= 7
|   0x40137cda  lea 0x4020b7f8,a0 (the waveform table) -> lea dv_wtab_a (keep2)
|   0x40137d98  mvs.w (a6),d1 ; add.l d1,d0 ; bpl     -> jmp dv_add_a: the stock add and clamp, the scale's
|               rounding on WAVE 9..15, the stock store, back at 0x40137db2
| Evaluator B, FUN_401376bc (a MIDI track's three LFOs, called by the MIDI task):
|   0x40137770  mvs.b 0x2e(a4),d7 ; mvs.b 0x2a(a4),d3 -> jsr dv_mode_b ; nop
|   0x40137788  moveq #6,d6 ; 0x4013778e moveq #6,d3  the WAVE clamp 0..6 -> 0..15 (7c06 -> 7c0f, 7606 -> 760f)
|   0x401378ce  lea 0x4020b7f8,a1                      -> lea dv_wtab_b (keep2)
| Registers at A's waveform call (0x40137ce6, jsr (a0) with the scaled phase on the stack): a2 + 0x50 = the LFO's
| state (its phase already stored), a4 = the LFO's parameters in the smoothed row (SPH 0x4e, MODE 0x50, the
| stock A4 - 0x10 per LFO), d3 = WAVE, a5 = the voice row; in the caller's frame (0x4c bytes of locals, +8 here
| for its push and the return address) the trig flag local_20 at 0x2c and the LFO local_1c (2, 1, 0) at 0x30.
| At B's (0x401378f0, jsr (a1)): a2 + 0x50 = the state, a4 = the parameters (SPH 0x2c, MODE 0x2e), d3 = WAVE,
| d5 = the trig flag; in its frame (0x3c bytes) the LFO local_c at 0x30 and the track, its second argument, at
| 0x44. The callers treat the waveforms as C functions (d0 the result; d1, a0, a1 free; B saves its d1
| itself); so do these stubs.
        .section .run, "ax"
        .globl  dv_frame_hook, dv_mode_a, dv_add_a, dv_mode_b, dv_wtab_a, dv_wtab_b

dv_frame_hook:
        addq.l  #1, dv_frame
        jmp     0x40137a06

| d3 = WAVE (0..15 after the clamp); d1 = MODE as stock (mvs.b), or 0 on the added waves; d2 = d6, as stock
dv_mode_a:
        move.l  %d6, %d2
        moveq   #7, %d1
        cmp.l   %d1, %d3
        bge.s   1f
        mvs.b   0x50(%a4), %d1
        rts
1:      moveq   #0, %d1
        rts

dv_wave_a_s:
        move.l  0x38(%sp), -(%sp)       | the LFO, 2..0 (the caller's local_1c)
        move.l  0x38(%sp), -(%sp)       | trig (the caller's local_20, 0x34(sp) before the push)
        mvs.b   0x50(%a4), %d0
        move.l  %d0, -(%sp)             | MODE: the length
        mvs.w   0x4e(%a4), %d0
        asr.l   #8, %d0
        move.l  %d0, -(%sp)             | SPH: the mutation
        move.l  %d3, -(%sp)             | WAVE
        move.l  %a5, -(%sp)             | the voice row
        pea     0x50(%a2)               | the LFO's state
        jsr     dv_wave_a
        lea     28(%sp), %sp
        rts

| d0 = the LFO's amount, a6 = &row[DEST], d7 = DEST, d3 = WAVE, a5 = the voice row
dv_add_a:
        mvs.w   (%a6), %d1
        add.l   %d1, %d0
        bpl.s   1f
        moveq   #0, %d0
        bra.s   2f
1:      cmpi.l  #0x7f00, %d0
        ble.s   2f
        move.l  #0x7f00, %d0
2:      moveq   #9, %d1
        cmp.l   %d1, %d3
        blt.s   3f
        move.l  %a5, -(%sp)             | the voice row
        move.l  %d7, -(%sp)             | DEST
        move.l  %d3, -(%sp)             | WAVE
        move.l  %d0, -(%sp)             | the value
        jsr     dv_quant_a
        lea     16(%sp), %sp
3:      move.w  %d0, (%a6)
        jmp     0x40137db2

| d3 = WAVE (as stock, mvs.b); d7 = MODE as stock, or 0 on the added waves
dv_mode_b:
        mvs.b   0x2a(%a4), %d3
        moveq   #7, %d7
        cmp.l   %d7, %d3
        bge.s   1f
        mvs.b   0x2e(%a4), %d7
        rts
1:      moveq   #0, %d7
        rts

dv_wave_b_s:
        move.l  0x38(%sp), -(%sp)       | the LFO, 2..0 (the caller's local_c)
        move.l  %d5, -(%sp)             | trig
        mvs.b   0x2e(%a4), %d0
        move.l  %d0, -(%sp)             | MODE: the length
        mvs.w   0x2c(%a4), %d0
        asr.l   #8, %d0
        move.l  %d0, -(%sp)             | SPH: the mutation
        move.l  %d3, -(%sp)             | WAVE
        move.l  0x60(%sp), -(%sp)       | the track (the caller's second argument: 0x4c(sp) + the five pushes)
        pea     0x50(%a2)               | the LFO's state
        jsr     dv_wave_b
        lea     28(%sp), %sp
        rts

| the waveform tables: the stock six (TRI SINE SQR SAW EXPO RAMP, 0x4020b7f8's entries), RAND's place (never
| called: RAND has its own code in both evaluators), then the added waves
        .balign 4
dv_wtab_a:
        .long   0x4013753e, 0x40137554, 0x40137520, 0x40137532, 0x401375ae, 0x4013759e, 0
        .long   dv_wave_a_s, dv_wave_a_s, dv_wave_a_s, dv_wave_a_s, dv_wave_a_s, dv_wave_a_s, dv_wave_a_s
        .long   dv_wave_a_s, dv_wave_a_s
dv_wtab_b:
        .long   0x4013753e, 0x40137554, 0x40137520, 0x40137532, 0x401375ae, 0x4013759e, 0
        .long   dv_wave_b_s, dv_wave_b_s, dv_wave_b_s, dv_wave_b_s, dv_wave_b_s, dv_wave_b_s, dv_wave_b_s
        .long   dv_wave_b_s, dv_wave_b_s
