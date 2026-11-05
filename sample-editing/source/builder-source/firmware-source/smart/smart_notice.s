/* 0x401ae1c4 returns its shared-text owner through A0, not a normal first
 * stack argument.  This tiny ABI bridge is the stock call form lifted from
 * 0x4005fe94..0x4005fea2; it owns no state and preserves C's caller frame. */
        .text
        .global smart_make_text
smart_make_text:
        movea.l 4(%sp),%a0
        move.l  8(%sp),-(%sp)
        jsr     0x401ae1c4
        addq.l  #4,%sp
        rts
