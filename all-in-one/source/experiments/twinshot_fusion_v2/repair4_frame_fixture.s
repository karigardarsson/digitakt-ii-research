| Real smoother -> split -> LFO -> reconstruction -> native outgoing copy.
| Only caller bookkeeping and exit after first-track SRC and type packing.
    .text
    .globl repair4_frame, repair4_done
repair4_frame:
    link.w %a6,#-208
    movem.l %d2-%d7/%a2-%a5,(%sp)
    move.l %macsr,%d0
    move.l %d0,-164(%a6)
    move.l #0x20,%macsr
    moveq #0,%d2
    moveq #0,%d3
    moveq #0,%d4
    moveq #1,%d5
    moveq #0,%d6
    moveq #0,%d7
    jmp 0x4002e8c0
repair4_done:
    move.l -164(%a6),%d0
    move.l %d0,%macsr
    lea -208(%a6),%sp
    movem.l (%sp),%d2-%d7/%a2-%a5
    unlk %a6
    rts
