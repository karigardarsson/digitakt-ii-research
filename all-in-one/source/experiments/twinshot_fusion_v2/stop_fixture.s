| Actual STOP event setter -> native vector-191 STOP/frame-mask slice.
| No transport/UI timing is simulated; return before unrelated frame fields.
    .text
    .globl fusion_stop_fixture
    .globl fusion_stop_done
fusion_stop_fixture:
    link.w %a6,#-208
    movem.l %d2-%d7/%a2-%a5,(%sp)
    move.l 8(%a6),-(%sp)
    jsr 0x4002d754
    addq.l #4,%sp
    movea.l #0x80005b50,%a2
    moveq #0,%d2
    moveq #0,%d3
    moveq #0,%d4
    clr.l -144(%a6)
    clr.l -148(%a6)
    jmp 0x4002e9fe
fusion_stop_done:
    lea -208(%a6),%sp
    movem.l (%sp),%d2-%d7/%a2-%a5
    unlk %a6
    rts
