// SPDX-License-Identifier: GPL-2.0-or-later
// faulttone.s - MR03 (notes/MR03-eeem.md): fault tones. Elektron's DSP parks the core for good on a fault: the
// ILOPI, PARI and EMUI handlers and the default core-interrupt handler write a record at DM 0x26ef88..0x26ef94
// ({8, code, address, address}; code 0x501 ILOPI, 0x502 PARI, 0x507 EMUI, 0xb default) and jump to 0x1c07c6
// (NOP; IDLE 0x0081 at 0x1c07c7; JUMP 0x1c07c7), and the abort path (FreeRTOS's stack-overflow hook 0x1cd96a ->
// 0xb8b538; also the end of main if the scheduler returned) jumps to 0x1c1430 (NOP; IDLE at 0x1c1431; JUMP 0x1c1431).
// The SPORT4A DMA keeps sending the last two codec output buffers (0x2c0478 and 0x2c0578, contiguous, 64 words each =
// 32 frames x (L, R), Q31) for ever. build.py (recipe key fault_tone) puts `JUMP ft_fault.` at 0x1c07c7 and
// `JUMP ft_abort.` at 0x1c1431 (each followed by a NOP over the old JUMP's last parcel); this code fills both buffers
// with a square wave whose pitch names the fault, then parks as stock did.
//
// Every pitch repeats exactly in the 64-frame two-buffer loop at 48 kHz (period P frames, P divides 64; 750 Hz, P 64,
// is the natural freeze buzz and is not used):
//   0x501 ILOPI                      1500 Hz   P 32
//   0x502 PARI                       3000 Hz   P 16
//   0x507 EMUI, 0xb default, other   6000 Hz   P 8
//   abort / stack check (0x1c1431)   1500 Hz + 6000 Hz, each at half amplitude (a two-tone)
// Amplitude: each frame's word is h x s1 + h x s2, s = +1 in the first half of a period and -1 in the second, h =
// 0x06666666 (0.05): 0x0CCCCCCC (0.1, -20 dBFS) peak; a single tone has s1 = s2. L and R get the same word. The
// words are written as the DMA sends them (no negation: the stock block tail negates its 64 words after the
// interleave, which only flips the square's sign).
// No stack, no C runtime, no hardware loop, no shift by an immediate, no DAG register stored, interrupts off.

.SECTION/SW seg_swco;

.GLOBAL ft_fault.;
ft_fault.:
    BIT CLR MODE1 0xE0B000;         // IRPTEN off (the handlers did already), SIMD and broadcast off, ALUSAT/TRUNC off
    NOP;
    R0 = DM (0x26EF8C);             // the record's code
    R9 = 0x4;                       // 6000 Hz: half period 4 frames (EMUI, default, any other code)
    R1 = 0x501;
    COMP (R0, R1);
    IF EQ JUMP .L_ft_ilop;
    R1 = 0x502;
    COMP (R0, R1);
    IF EQ JUMP .L_ft_par;
    JUMP .L_ft_one;
.L_ft_ilop:
    R9 = 0x10;                      // 1500 Hz: half period 16 frames
    JUMP .L_ft_one;
.L_ft_par:
    R9 = 0x8;                       // 3000 Hz: half period 8 frames
.L_ft_one:
    R10 = PASS R9;                  // one tone: both squares the same
    JUMP .L_ft_fill;

.GLOBAL ft_abort.;
ft_abort.:
    BIT CLR MODE1 0xE0B000;
    NOP;
    R9 = 0x10;                      // 1500 Hz
    R10 = 0x4;                      // + 6000 Hz

.L_ft_fill:
    R11 = 0x6666666;                // +h
    R12 = -0x6666666;               // -h
    R2 = 0x0;                       // frame n, 0..63 over both buffers
    R3 = 0x40;
    R4 = 0x8;                       // bytes a frame (L, R)
    R13 = 0x4;                      // bytes a word
    R14 = 0x1;
    R8 = 0x2C0478;                  // byte address of frame n's L word (0x2c0478 then 0x2c0578, contiguous)
.L_ft_loop:
    R6 = PASS R11;
    R5 = R2 AND R9;
    IF EQ JUMP .L_ft_a;
    R6 = PASS R12;
.L_ft_a:
    R7 = PASS R11;
    R5 = R2 AND R10;
    IF EQ JUMP .L_ft_b;
    R7 = PASS R12;
.L_ft_b:
    R6 = R6 + R7;
    I4 = R8;
    DM (I4,M5) = R6;                // L
    R5 = R8 + R13;
    I4 = R5;
    DM (I4,M5) = R6;                // R
    R8 = R8 + R4;
    R2 = R2 + R14;
    COMP (R2, R3);
    IF LT JUMP .L_ft_loop;
.L_ft_park:
    IDLE;
    JUMP .L_ft_park;
.ENDSEG;
