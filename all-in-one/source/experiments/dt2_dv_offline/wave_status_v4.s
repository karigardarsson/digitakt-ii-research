/* DT2 OS 1.16 V4 WAVE formatter front end and common emitter. */
        .equ STOCK_FORMATTER, 0x400e1a04
        .equ LATCH_BASE,      0x40311fe4
        .equ HOOK_MAGIC,      0x44564833
        .equ PUBLISH_PENDING, 0x5055423f
        .equ PUBLISH_MAGIC,   0x44565033
        .equ CLASSIFY_MORE,   0x40311f3e

        .text
        .global wave_status_formatter_v4
wave_status_formatter_v4:
        move.l  4(%sp),%d0
        cmpi.l  #0x700,%d0
        beq     show_dvf
        cmpi.l  #0x800,%d0
        beq     classify
        jmp     STOCK_FORMATTER
show_dvf:
        move.l  #0x44562d46,%d1
        bra     v4_emit

        .org 0x30
        .global v4_emit
v4_emit:
        movea.l 8(%sp),%a0
        move.l  %d1,(%a0)
        clr.b   4(%a0)
        moveq   #4,%d0
        rts

classify:
        movea.l #LATCH_BASE,%a0
        move.l  (%a0),%d1
        beq     no_hook
        cmpi.l  #HOOK_MAGIC,%d1
        bne     show_bad
        move.l  8(%a0),%d1
        cmpi.l  #PUBLISH_MAGIC,%d1
        beq     more
        cmpi.l  #PUBLISH_PENDING,%d1
        beq     more
        tst.l   %d1
        bne     show_bad
        tst.l   4(%a0)
        bne     show_bad
        tst.l   12(%a0)
        beq     show_wait
        bra     show_bad
more:
        jmp     CLASSIFY_MORE
no_hook:
        tst.l   4(%a0)
        bne     show_bad
        tst.l   8(%a0)
        bne     show_bad
        tst.l   12(%a0)
        beq     show_skip
show_bad:
        move.l  #0x42414421,%d1
        bra     v4_emit
show_skip:
        move.l  #0x534b4950,%d1
        bra     v4_emit
show_wait:
        move.l  #0x57414954,%d1
        bra     v4_emit
