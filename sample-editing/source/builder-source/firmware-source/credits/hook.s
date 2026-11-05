    .text
    .global credits_ctor_tail
credits_ctor_tail:
    move.l %a2,-(%sp)
    jsr credits_append
    addq.l #4,%sp
    movem.l 8(%sp),%d2-%d6/%a2-%a6
    lea 112(%sp),%sp
    rts
    .global adopt
adopt:
    move.l 12(%sp),%a0
    move.l 8(%sp),-(%sp)
    move.l 8(%sp),-(%sp)
    jsr 0x4011a0ba
    addq.l #8,%sp
    rts
