| Test-only terminal observation of actual vfunc45 arguments for TUNA.
    .text
    .globl repair4_capture
repair4_capture:
    move.l 20(%sp),%d0
    cmpi.l #301,%d0
    bne.s 2f
    lea 4(%sp),%a0
    lea 0x47f10000,%a1
    moveq #10,%d1
1:  move.l (%a0)+,(%a1)+
    subq.l #1,%d1
    bpl.s 1b
2:  jmp 0x47ea0000
