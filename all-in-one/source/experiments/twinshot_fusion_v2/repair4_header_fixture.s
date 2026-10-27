| Test-only real shared header hook, with native continuation observed.
    .text
    .globl repair4_header,repair4_header_done
repair4_header:
    link.w %a6,#-16
    movem.l %d2/%a2,(%sp)
    movea.l 8(%a6),%a2
    move.l 12(%a6),%d2
    move.l 16(%a6),-(%sp)
    move.l %d2,-(%sp)
    jmp 0x4006ce92
repair4_header_done:
    move.l %d2,0x47f10100
    move.l %a2,0x47f10104
    addq.l #8,%sp
    movem.l (%sp),%d2/%a2
    unlk %a6
    rts
