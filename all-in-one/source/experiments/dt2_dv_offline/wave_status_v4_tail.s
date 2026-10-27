/* DT2 OS 1.16 V4 formatter continuation in the free post-dispatch span. */
        .equ LATCH_BASE,      0x40311fe4
        .equ MALLOC_PENDING,  0x4d414c3f
        .equ PUBLISH_MAGIC,   0x44565033
        .equ V4_EMIT,         0x40311c80
        .equ PENDING_HELPER,   0x403117c4

        .text
        .global wave_status_formatter_v4_tail
wave_status_formatter_v4_tail:
        movea.l #LATCH_BASE,%a0
        move.l  8(%a0),%d1
        cmpi.l  #PUBLISH_MAGIC,%d1
        bne     pending

        move.l  4(%a0),%d1
        beq     show_bad
        cmpi.l  #-1,%d1
        beq     show_bad
        cmpi.l  #MALLOC_PENDING,%d1
        beq     show_bad
        move.l  12(%a0),%d0
        beq     show_lost
        cmpi.l  #-1,%d0
        beq     show_lost
        cmp.l   %d1,%d0
        beq     show_good
        bra     show_bad

pending:
        jmp     PENDING_HELPER
show_lost:
        move.l  #0x4c4f5354,%d1
        bra     emit
show_good:
        move.l  #0x474f4f44,%d1
        bra     emit
show_bad:
        move.l  #0x42414421,%d1
emit:
        jmp     V4_EMIT
