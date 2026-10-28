| SPDX-License-Identifier: GPL-2.0-or-later
| arp-modes-dn2 0.2 (Digitone II OS 1.12): the arpeggiator's SHUF, RAND and TM. Stock names eight modes (OFF TRUE UP
| DOWN CYCL SHUF RAND CHRD) but its step, FUN_4002a0bc, plays four: TRUE, UP, DOWN, and CYCL for everything else, and
| the menu, FUNC + ARP and the project loader keep MODE to 0..4. (Research findings of dn2_firmware_explore,
| angellinares, on OS 1.11; 1.12 has the same code at the same addresses, read here instruction by instruction. Our own
| code.) TM (0.2) is MODE 7..107, its LOOP CHANGE MODE - 7, so a kit keeps its format (am.c has the engine and the menu).
| ColdFire V4; assemble with -mcpu=54455. dn2-cfw's scripts/ba03_make_arp.py checks every site against the stock bytes.
|
|   0x4002a108  the step's rest test, 12 bytes (0.2)                   -> jmp am_rest + 3 nops
|   0x4002a13c  moveq #3,d0; cmp.l d4,d0; beq.w DOWN; bra.w CYCL (12 bytes)  -> jmp am_mode + 3 nops
|   0x4004befa, 0x4004bf00  setMode's clamp to 4                     -> 107 (0.1: 6)
|   0x4004bfb4, 0x4004bfc6  FUNC + ARP's restore of a parked MODE, to 1..4  -> 1..107 (0.1: 6)
|   0x400dd694  the project loader: a MODE above 4 becomes 0 (OFF)   -> above 107 (0.1: 6)
| A stock OS loads a kit saved with SHUF, RAND or TM with its arp OFF (the loader above, unchanged there), and BA02
| (0.1) one saved with TM.
|
| At 0x4002a108 and 0x4002a13c: d1 = the track, a0 = the track's arp state (0x405994a8 + 40 t: +0 the step, +0x0c the
| last note, +0x18 the arp's id, +0x1c the octave), a1 = the track's sound (MODE +0x15f, RANGE +0x161, LEN - 1 +0x163,
| the steps that are on +0x164). At 0x4002a13c also d4 = MODE and d3 = the step. The common tail 0x4002a376 takes the
| note in d0 (negative: a rest), adds 12 x the octave (+0x1c) and the step's offset, and returns; 0x4002a39a returns
| -1 (a rest) and 0x4002a39c returns d0 as it is, both restoring d2-d7/a2-a4 from the routine's frame.
        .section .run, "ax"
        .globl  am_rest
        .globl  am_mode

am_rest:
        mvz.w   356(%a1), %d0           | the steps that are on
        move.l  (%a0), %d3              | the step
        btst    %d3, %d0
        beq.s   1f
        jmp     0x4002a114              | on: the mode's dispatch, as stock
1:      move.b  351(%a1), %d0           | off, a rest; in TM the loop moves on all the same
        extb.l  %d0
        moveq   #7, %d2                 | d2..d7 are restored by the routine's end
        cmp.l   %d2, %d0
        blt.s   2f
        moveq   #107, %d2
        cmp.l   %d2, %d0
        bgt.s   2f
        move.l  %d0, -(%sp)             | MODE
        move.l  %a1, -(%sp)             | the sound
        move.l  %a0, -(%sp)             | the arp state
        move.l  %d1, -(%sp)             | the track
        jsr     am_tm_rest
        lea     16(%sp), %sp
2:      jmp     0x4002a39a              | a rest, as stock

am_mode:
        moveq   #3, %d0
        cmp.l   %d4, %d0
        bne.s   1f
        jmp     0x4002a208              | DOWN, as stock
1:      moveq   #5, %d0
        cmp.l   %d4, %d0
        beq.s   2f
        moveq   #6, %d0
        cmp.l   %d4, %d0
        beq.s   2f
        moveq   #7, %d0
        cmp.l   %d0, %d4
        blt.s   9f
        moveq   #107, %d0
        cmp.l   %d0, %d4
        ble.s   3f
9:      jmp     0x4002a28a              | CYCL and anything else, as stock
2:      move.l  %a0, -(%sp)
        move.l  %a1, -(%sp)
        move.l  %d4, -(%sp)             | MODE
        move.l  %a1, -(%sp)             | the sound
        move.l  %a0, -(%sp)             | the arp state
        move.l  %d1, -(%sp)             | the track
        jsr     am_pick
        lea     16(%sp), %sp
        move.l  (%sp)+, %a1
        move.l  (%sp)+, %a0
        jmp     0x4002a376              | the common tail: + 12 x octave + the step's offset
3:      move.l  %d4, -(%sp)             | MODE
        move.l  %a1, -(%sp)             | the sound
        move.l  %a0, -(%sp)             | the arp state
        move.l  %d1, -(%sp)             | the track
        jsr     am_tm
        lea     16(%sp), %sp
        jmp     0x4002a39c              | the routine's end with d0: TM adds no step offset
