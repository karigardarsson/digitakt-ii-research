/* OS 1.17 only. Independent button/rotation callbacks; private snapshots.
   DV allocation ownership and expansion loader are unchanged. */
        .equ DV_PTR,0x40311ff0
        .equ FACTORY,0x401a04e4
        .equ RECORD,0x1c80
        .equ CLONE,0x1e14
        .equ STATE,0x1e54
        .text
        .global source_zoom_hook
source_zoom_hook:
        link.w %a6,#-52
        movem.l %d2-%d7/%a2-%a4,(%sp)
        movea.l 8(%a6),%a2
        movea.l 12(%a6),%a3
        bsr .context
        tst.l %d0
        beq .button_pass
        move.l 12(%a3),%d0
        cmpi.l #43,%d0
        bne .button_pass
        move.l 16(%a3),%d3
        move.l 8(%a4),%d2
        btst #4,%d3
        beq .button_press
        btst #2,%d2
        beq .button_pass
        andi.l #0xfffffffb,%d2
        move.l %d2,8(%a4)
        bra .button_done
.button_press:
        btst #0,%d3
        beq .button_pass
        btst #3,%d3
        bne .button_repeat
        andi.l #3,%d2
        addq.l #1,%d2
        cmpi.l #3,%d2
        bne .cycle
        moveq #0,%d2
.cycle: ori.l #4,%d2
        move.l %d2,8(%a4)
        bsr .redraw
        bra .button_done
.button_repeat:
        btst #2,%d2
        beq .button_pass
.button_done:
        movem.l (%sp),%d2-%d7/%a2-%a4
        moveq #1,%d0
        unlk %a6
        rts
.button_pass:
        jmp 0x40018206

        .global source_zoom_rotation_hook
source_zoom_rotation_hook:
        link.w %a6,#-52
        movem.l %d2-%d7/%a2-%a5,(%sp)
        movea.l 8(%a6),%a2
        movea.l 12(%a6),%a3
        bsr .context
        tst.l %d0
        beq .rotation_pass
        move.l 12(%a3),%d0
        cmpi.l #3,%d0
        bne .rotation_pass
        move.l 8(%a4),%d2
        move.l %d2,%d1
        andi.l #3,%d1
        beq .rotation_pass
        move.l %d2,%d3
        lsr.l #8,%d3
        andi.l #511,%d3
        add.l 16(%a3),%d3
        bpl .pan_nonnegative
        moveq #0,%d3
.pan_nonnegative:
        move.l #180,%d4
        cmpi.l #1,%d1
        beq .pan_limit
        move.l #264,%d4
.pan_limit:
        cmp.l %d4,%d3
        ble .pan_store
        move.l %d4,%d3
.pan_store:
        lsl.l #8,%d3
        andi.l #0xfffe00ff,%d2
        or.l %d3,%d2
        move.l %d2,8(%a4)
        bsr .redraw
        movem.l (%sp),%d2-%d7/%a2-%a5
        moveq #1,%d0
        unlk %a6
        rts
.rotation_pass:
        jmp 0x400184f2

/* Both callers saved A2-A4. Never bind a SourcePageView as waveform owner.
   Require a previous stock ONESHOT draw, current sample and stock selector0. */
.context:
        move.l DV_PTR,%d0
        beq .context_no
        cmpi.l #-1,%d0
        beq .context_no
        movea.l %d0,%a4
        lea STATE(%a4),%a4
        lea 0x1d0(%a2),%a0
        cmpa.l (%a4),%a0
        bne .context_no
        move.l (%a0),%d0
        cmp.l 4(%a4),%d0
        bne .context_no
        tst.l 0x90(%a2)
        beq .context_no
        move.l %a2,-(%sp)
        jsr 0x4005f6bc
        addq.l #4,%sp
        tst.l %d0
        bne .context_no
        moveq #1,%d0
        rts
.context_no:
        moveq #0,%d0
        rts
.redraw:
        movea.l (%a4),%a0
        move.b #1,0x31(%a0)
        move.l %a2,-(%sp)
        jsr 0x4011b126
        addq.l #4,%sp
        rts

        .global oneshot_zoom_wrapper
oneshot_zoom_wrapper:
        lea -52(%sp),%sp
        movem.l %d2-%d7/%a2-%a6,(%sp)
        movea.l 56(%sp),%a2
        move.l 60(%sp),%d2
        move.l (%a2),%d5
        move.l DV_PTR,%d0
        beq .stock
        cmpi.l #-1,%d0
        beq .stock
        movea.l %d0,%a4
        lea STATE(%a4),%a5
        cmpa.l (%a5),%a2
        bne .reset
        cmp.l 4(%a5),%d5
        bne .reset
        bra .bound
.reset:
        move.l %a2,(%a5)
        move.l %d5,4(%a5)
        clr.l 8(%a5)
.bound:
        move.l 8(%a5),%d7
        andi.l #3,%d7
        beq .stock
        cmpi.l #2,%d7
        bhi .stock
        cmpi.l #1024,%d5
        bcc .stock
        jsr FACTORY
        move.l %d0,44(%sp)
        /* Overview vector lock: +16. Detail vector lock: +44.
           Never hold both locks at once. Each destination is DV-owned. */
        addi.l #16,%d0
        move.l %d0,-(%sp)
        jsr 0x40001608
        addq.l #4,%sp
        movea.l 44(%sp),%a6
        move.l 4(%a6),%d0
        beq .low_bad
        move.l %d5,%d1
        move.l #401,%d4
        muls.l %d4,%d1
        add.l %d1,%d0
        movea.l %d0,%a3
        addi.l #401,%d0
        cmp.l 8(%a6),%d0
        bhi .low_bad
        move.l 0x402ab508,%d0
        cmp.l (%a3),%d0
        beq .low_bad
        lea RECORD(%a4),%a0
        move.l #400,%d4
.copy_low:
        move.b (%a3)+,(%a0)+
        subq.l #1,%d4
        bpl .copy_low
        moveq #1,%d6
        bra .low_unlock
.low_bad:
        moveq #0,%d6
.low_unlock:
        lea 16(%a6),%a0
        move.l %a0,-(%sp)
        jsr 0x4000172a
        addq.l #4,%sp
        tst.l %d6
        beq .stock
        movea.l 44(%sp),%a6
        lea 44(%a6),%a0
        move.l %a0,-(%sp)
        jsr 0x40001608
        addq.l #4,%sp
        movea.l 44(%sp),%a6
        move.l 32(%a6),%d0
        beq .high_bad
        move.l %d5,%d1
        move.l #1457,%d4
        muls.l %d4,%d1
        add.l %d1,%d0
        movea.l %d0,%a3
        addi.l #1457,%d0
        cmp.l 36(%a6),%d0
        bhi .high_bad
        lea RECORD(%a4),%a0
        movea.l %a3,%a1
        moveq #3,%d4
.identity:
        move.l (%a1)+,%d0
        cmp.l (%a0)+,%d0
        bne .high_bad
        subq.l #1,%d4
        bpl .identity
        move.b 1456(%a3),%d0
        cmp.b RECORD+400(%a4),%d0
        bne .high_bad
        move.l 8(%a5),%d3
        lsr.l #8,%d3
        andi.l #511,%d3
        move.l #180,%d0
        cmpi.l #1,%d7
        beq .limit
        move.l #264,%d0
.limit:
        cmp.l %d0,%d3
        ble .limited
        move.l %d0,%d3
.limited:
        lea RECORD+16(%a4),%a0
        lea 16(%a3),%a1
        lea zoom2_offsets(%pc),%a6
        moveq #3,%d6
.plane:
        moveq #0,%d4
.pixel:
        move.l %d4,%d1
        cmpi.l #1,%d7
        bne .direct
        moveq #0,%d1
        move.b (%a6,%d4.l),%d1
.direct:
        add.l %d3,%d1
        move.b (%a1,%d1.l),%d0
        move.b %d0,(%a0,%d4.l)
        addq.l #1,%d4
        cmpi.l #96,%d4
        bne .pixel
        lea 360(%a1),%a1
        lea 96(%a0),%a0
        subq.l #1,%d6
        bpl .plane
        moveq #1,%d6
        bra .high_unlock
.high_bad:
        moveq #0,%d6
.high_unlock:
        movea.l 44(%sp),%a6
        lea 44(%a6),%a0
        move.l %a0,-(%sp)
        jsr 0x4000172a
        addq.l #4,%sp
        tst.l %d6
        beq .stock
        /* Construct only a 60-byte private factory. No stock mutex copied. */
        lea CLONE(%a4),%a0
        moveq #14,%d4
.clear_clone:
        clr.l (%a0)+
        subq.l #1,%d4
        bpl .clear_clone
        lea CLONE(%a4),%a0
        move.l (%a6),(%a0)
        lea RECORD(%a4),%a1
        move.l %d5,%d0
        move.l #401,%d4
        muls.l %d4,%d0
        suba.l %d0,%a1
        move.l %a1,CLONE+4(%a4)
        lea RECORD+401(%a4),%a0
        move.l %a0,CLONE+8(%a4)
        move.l %a0,CLONE+12(%a4)
        move.l 8(%a5),%d0
        ori.l #8,%d0
        move.l %d0,8(%a5)
        move.l %d2,-(%sp)
        move.l %a2,-(%sp)
        jsr 0x4010ee64
        addq.l #8,%sp
        move.w %sr,%d1
        move.l 8(%a5),%d0
        andi.l #0xfffffff7,%d0
        move.l %d0,8(%a5)
        movem.l (%sp),%d2-%d7/%a2-%a6
        lea 52(%sp),%sp
        move.w %d1,%sr
        rts
.stock:
        movem.l (%sp),%d2-%d7/%a2-%a6
        lea 52(%sp),%sp
        jmp 0x4010ee64

        .global zoom_factory_hook
zoom_factory_hook:
        move.l DV_PTR,%d0
        beq .factory
        cmpi.l #-1,%d0
        beq .factory
        movea.l %d0,%a0
        lea STATE(%a0),%a1
        cmpa.l (%a1),%a2
        bne .factory
        move.l 8(%a1),%d1
        btst #3,%d1
        beq .factory
        lea CLONE(%a0),%a0
        move.l %a0,%d0
        rts
.factory:
        jmp FACTORY

        .global waveform_view_init_hook
waveform_view_init_hook:
        move.l DV_PTR,%d0
        beq .init_stock
        cmpi.l #-1,%d0
        beq .init_stock
        movea.l %d0,%a0
        move.l 4(%sp),%d0
        cmp.l STATE(%a0),%d0
        bne .init_stock
        clr.l STATE(%a0)
        clr.l STATE+8(%a0)
.init_stock:
        clr.b %d0
        move.l %a2,-(%sp)
        movea.l 8(%sp),%a2
        jmp 0x4010ecd8

zoom2_offsets:
        .byte 0,1,3,5,7,9,11,13,15,16,18,20,22,24,26,28
        .byte 30,31,33,35,37,39,41,43,45,46,48,50,52,54,56,58
        .byte 60,61,63,65,67,69,71,73,75,76,78,80,82,84,86,88
        .byte 90,91,93,95,97,99,101,103,105,106,108,110,112,114,116,118
        .byte 120,121,123,125,127,129,131,133,135,136,138,140,142,144,146,148
        .byte 150,151,153,155,157,159,161,163,165,166,168,170,172,174,176,178
        .balign 4
