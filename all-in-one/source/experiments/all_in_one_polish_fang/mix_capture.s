| Test-only actual vfunc45 arguments, no firmware instrumentation.
    .text
    .globl mix_capture
    .extern panel_original
mix_capture:
    move.l 20(%sp),%d0
    cmpi.l #284,%d0
    bne.s 2f
    lea 4(%sp),%a0
    lea 0x47f10000,%a1
    moveq #10,%d1
1:  move.l (%a0)+,(%a1)+
    subq.l #1,%d1
    bpl.s 1b
2:  jmp panel_original

    .globl probe_previous
    .extern previous_original
probe_previous:
    move.l (%sp),0x47f10100
    move.l %a6,0x47f10104
    lea 3f,%a0
    move.l %a0,(%sp)
    jmp previous_original
3:  move.l %a6,0x47f10108
    move.l 0x47f10100,%a0
    jmp (%a0)

    .globl probe_fill
probe_fill:
    move.l (%sp),0x47f10110
    move.l %a6,0x47f10114
    lea 4f,%a0
    move.l %a0,(%sp)
    jmp 0x401131ae
4:  move.l %a6,0x47f10118
    move.l 0x47f10110,%a0
    jmp (%a0)

    .globl probe_text
probe_text:
    move.l 28(%sp),%d0
    move.l %d0,0x47f10120
    move.l %a6,0x47f10124
    jmp 0x40113dc8
