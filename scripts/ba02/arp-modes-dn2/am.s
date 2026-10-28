| SPDX-License-Identifier: GPL-2.0-or-later
| arp-modes-dn2 0.1 (Digitone II OS 1.12): the arpeggiator's SHUF and RAND. Stock names eight modes (OFF TRUE UP DOWN
| CYCL SHUF RAND CHRD) but its step, FUN_4002a0bc, plays four: TRUE, UP, DOWN, and CYCL for everything else, and the
| menu, FUNC + ARP and the project loader keep MODE to 0..4. (Research findings of dn2_firmware_explore, angellinares,
| on OS 1.11; 1.12 has the same code at the same addresses, read here instruction by instruction. Our own code.)
| ColdFire V4; assemble with -mcpu=54455. dn2-cfw's scripts/ba02_make_arp.py checks every site against the stock bytes.
|
|   0x4002a13c  moveq #3,d0; cmp.l d4,d0; beq.w DOWN; bra.w CYCL (12 bytes)  -> jmp am_mode + 3 nops
|   0x4004befa, 0x4004bf00  setMode's clamp to 4                             -> 6
|   0x4004bfb4, 0x4004bfc6  FUNC + ARP's restore of a parked MODE, to 1..4   -> 1..6
|   0x400dd694  the project loader: a MODE above 4 becomes 0 (OFF)           -> above 6
| A stock OS loads a kit saved with SHUF or RAND with its arp OFF (the loader above, unchanged there).
|
| At 0x4002a13c (MODE > 1, not UP): d4 = MODE, d1 = the track, d3 = the step (for the step's offset), a0 = the
| track's arp state (0x405994a8 + 40 t: +0x18 the arp's id, +0x1c the octave), a1 = the track's sound (MODE +0x15f,
| RANGE +0x161). The common tail 0x4002a376 takes the note in d0 (negative: a rest), adds 12 x the octave (+0x1c)
| and the step's offset, and returns; it reads a0, a1, d3.
        .section .run, "ax"
        .globl  am_mode

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
        jmp     0x4002a28a              | CYCL and anything else, as stock
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
