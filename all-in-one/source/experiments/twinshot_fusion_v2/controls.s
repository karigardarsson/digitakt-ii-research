    .text
    .globl fusion_sync_fmt
    .globl fusion_mode_fmt
    .globl fusion_widget
    .extern fusion_sync
    .extern fusion_mode
    .extern fusion_sample1
    .extern fusion_sample2
    .extern twinshot_widget
    .extern twinshot_sample_fmt
    .globl fusion2_sync_fmt, fusion2_mode_fmt
    .globl fusion2_sync_info_fmt
    .globl fusion2_header_text
    .extern dx7_header_text, fusion2_header_return
    .extern fusion2_sync, fusion2_mode, fusion2_sample1, fusion2_sample2
    .extern fusion2_start1, fusion2_start2, fusion2_depth, fusion2_shape
    .extern fusion2_tunea_ui, fusion2_relative_ui, fusion2_balance_ui, fusion2_sweep_ui
    .extern fusion2_sync_rel_ui
    .extern pf_table
    .globl fusion2_percent_fmt
    .globl fusion2_recovery_fmt, fusion2_auto_fmt

| Bounded integer time readouts. The stored response remains0..32512, with
| exactly the existing quadratic DSP mapping. Rounded fixed-point display is
| within2ms recovery/16ms auto, no float ABI or unbounded formatted string.
fusion2_auto_fmt:
    move.l 4(%sp),%d0
    bgt.s 70f
    movea.l 8(%sp),%a0
    move.l #0x4f464600,(%a0)
    rts
70: move.l #500,%d1
    bra.s 72f
fusion2_recovery_fmt:
    move.l #1000,%d1
72: lea -16(%sp),%sp
    movem.l %d2-%d3/%a2-%a3,(%sp)
    move.l 20(%sp),%d0
    movea.l 24(%sp),%a2
    move.l #1990,%d2
    cmpi.l #500,%d1
    bne.s 73f
    move.l #8000,%d2
73: tst.l %d0
    bpl.s 74f
    moveq #0,%d0
74: cmpi.l #32512,%d0
    ble.s 75f
    move.l #32512,%d0
75: mulu.l %d1,%d0
    addi.l #16256,%d0
    move.l #32512,%d3
    divu.l %d3,%d0
    mulu.l %d0,%d0
    mulu.l %d2,%d0
    addi.l #500000,%d0
    move.l #1000000,%d3
    cmpi.l #8000,%d2
    bne.s 76f
    move.l #250000,%d3
    subi.l #375000,%d0
76: divu.l %d3,%d0
    addi.l #10,%d0
    move.l #1000,%d2
    moveq #0,%d3
77: move.l %d0,%d1
    divu.l %d2,%d1
    tst.l %d1
    bne.s 78f
    tst.l %d3
    bne.s 78f
    cmpi.l #1,%d2
    bne.s 79f
78: move.l %d1,%a3
    addi.l #48,%d1
    move.b %d1,(%a2)+
    move.l %a3,%d1
    mulu.l %d2,%d1
    sub.l %d1,%d0
    moveq #1,%d3
79: moveq #10,%d1
    divu.l %d1,%d2
    bne.s 77b
    move.b #'m',(%a2)+
    move.b #'s',(%a2)+
    clr.b (%a2)
    movem.l (%sp),%d2-%d3/%a2-%a3
    lea 16(%sp),%sp
    rts

| Rounded 0..100%, same stored 0..32512 range. No float, heap or static buffer.
fusion2_percent_fmt:
    move.l 4(%sp),%d0
    movea.l 8(%sp),%a0
    tst.l %d0
    bpl.s 20f
    moveq #0,%d0
20: cmpi.l #32512,%d0
    bls.s 21f
    move.l #32512,%d0
21: moveq #100,%d1
    mulu.l %d1,%d0
    addi.l #16256,%d0
    move.l #32512,%d1
    divu.l %d1,%d0
    cmpi.l #100,%d0
    bne.s 22f
    move.l #0x31303025,(%a0)
    clr.b 4(%a0)
    rts
22: cmpi.l #10,%d0
    blt.s 23f
    movea.l %d0,%a1
    moveq #10,%d1
    divu.l %d1,%d0
    addi.l #48,%d0
    move.b %d0,(%a0)+
    subi.l #48,%d0
    mulu.l %d1,%d0
    move.l %a1,%d1
    sub.l %d0,%d1
    move.l %d1,%d0
23: addi.l #48,%d0
    move.b %d0,(%a0)+
    move.b #'%',(%a0)+
    clr.b (%a0)
    rts

fusion2_sync_fmt:
    move.l 4(%sp),%d0
    tst.l %d0
    bpl.s 24f
    moveq #0,%d0
24: addi.l #128,%d0
    asr.l #8,%d0
    cmpi.l #2,%d0
    bls.s 11f
    moveq #2,%d0
11: moveq #6,%d1
    mulu.l %d1,%d0
    lea fusion2_syncs,%a0
    adda.l %d0,%a0
    movea.l 8(%sp),%a1
25: move.b (%a0)+,%d0
    move.b %d0,(%a1)+
    bne.s 25b
    rts

| Native INFO caches its descriptor identity before applying an encoder edit.
| A stable title plus a value-derived full explanation cannot lag one choice.
| ONLY the header wrapper below calls this, using its native64-byte buffer.
| Descriptor/ParameterSet values MUST remain short for the native knob-label
| path, which runs before our widget and does not own a header-sized buffer.
fusion2_sync_info_fmt:
    move.l 4(%sp),%d0
    lea fusion2_info_off,%a0
    cmpi.l #128,%d0
    blt.s 80f
    lea fusion2_info_match,%a0
    cmpi.l #384,%d0
    blt.s 80f
    lea fusion2_info_rel,%a0
80: movea.l 8(%sp),%a1
81: move.b (%a0)+,%d0
    move.b %d0,(%a1)+
    bne.s 81b
    rts

| Chain the existing single-owner Plaits header hook; no overlapping hook.
| Same proven jump-site ABI as dx7_header_text: d2=code,a2=view,
| 0(sp)=code,4(sp)=raw. Other parameters go through the original DX7/stock path.
fusion2_header_text:
    cmpi.l #fusion2_sync,%d2
    beq.s 83f
    cmpi.l #311,%d2
    blt.s 82f
    cmpi.l #313,%d2
    bgt.s 82f
83: pea 0x4293b818
    move.l 8(%sp),-(%sp)
    bsr.w fusion2_sync_info_fmt
    addq.l #8,%sp
    move.l #0x4293b818,%d0
    jmp fusion2_header_return
82: jmp dx7_header_text

fusion2_mode_fmt:
    move.l 4(%sp),%d0
    tst.l %d0
    bpl.s 26f
    moveq #0,%d0
26:
    asr.l #8,%d0
    cmpi.l #4,%d0
    bls.s 12f
    moveq #4,%d0
12: lsl.l #2,%d0
    lea fusion2_modes,%a0
    move.l (%a0,%d0.l),%d0
    movea.l 8(%sp),%a0
    move.l %d0,(%a0)
    clr.b 4(%a0)
    rts

fusion_sync_fmt:
    move.l 4(%sp),%d0
    movea.l 8(%sp),%a0
    cmpi.l #128,%d0
    bge.s 1f
    move.l #0x4f464600,(%a0)
    rts
1:  move.l #0x4f4e0000,(%a0)
    rts

fusion_mode_fmt:
    move.l 4(%sp),%d0
    asr.l #8,%d0
    andi.l #3,%d0
    lsl.l #2,%d0
    lea fusion_modes,%a0
    move.l (%a0,%d0.l),%d0
    movea.l 8(%sp),%a0
    move.l %d0,(%a0)
    clr.b 4(%a0)
    rts

| Draw only our four discrete glyphs; chain the exact existing UI for all
| names, lock frames, ordinary numeric controls and every previous machine.
fusion_widget:
    move.l %d0,-(%sp)
    move.l 24(%sp),%d0
    cmpi.l #fusion2_sync,%d0
    blt.s 27f
    cmpi.l #fusion2_sync_rel_ui,%d0
    ble.w 2f
27:
    cmpi.l #fusion_sync,%d0
    beq.s 2f
    cmpi.l #fusion_mode,%d0
    beq.s 2f
    cmpi.l #fusion_sample1,%d0
    beq.s 2f
    cmpi.l #fusion_sample2,%d0
    beq.s 2f
    cmpi.l #fusion2_sync,%d0
    beq.s 2f
    cmpi.l #fusion2_mode,%d0
    beq.s 2f
    cmpi.l #fusion2_sample1,%d0
    beq.s 2f
    cmpi.l #fusion2_sample2,%d0
    beq.s 2f
    move.l (%sp)+,%d0
    jmp twinshot_widget
2:  move.l (%sp)+,%d0
    link %fp,#-64
    movem.l %d2-%d4/%a2-%a3,(%sp)
    move.l 48(%fp),-(%sp)
    move.l 44(%fp),-(%sp)
    move.l 40(%fp),-(%sp)
    move.l 36(%fp),-(%sp)
    move.l 32(%fp),-(%sp)
    move.l 28(%fp),-(%sp)
    move.l 24(%fp),%d0
    cmpi.l #311,%d0
    blt.s 64f
    cmpi.l #313,%d0
    bgt.s 64f
    move.l #fusion2_sync,%d0 | native grid must never draw the long INFO value
    bra.s 61f
64:
    cmpi.l #fusion2_tunea_ui,%d0
    beq.s 60f
    cmpi.l #295,%d0
    beq.s 60f
    cmpi.l #fusion2_relative_ui,%d0
    bne.s 61f
60: move.l #248,%d0
61: move.l %d0,-(%sp)
    move.l 20(%fp),-(%sp)
    move.l 16(%fp),-(%sp)
    move.l 12(%fp),-(%sp)
    move.l 8(%fp),-(%sp)
    jsr twinshot_widget
    lea 44(%sp),%sp
    move.l 24(%fp),%d0
    cmpi.l #fusion2_tunea_ui,%d0
    beq.w 62f
    cmpi.l #295,%d0
    beq.w 62f
    cmpi.l #fusion2_relative_ui,%d0
    beq.w 62f
    lea -16(%fp),%a0
    move.l %a0,-(%sp)
    move.l 28(%fp),-(%sp)
    move.l 24(%fp),%d0
    cmpi.l #fusion2_sync,%d0
    blt.s 28f
    cmpi.l #fusion2_sync_rel_ui,%d0
    bgt.s 28f
    cmpi.l #311,%d0
    blt.s 63f
    bsr.w fusion2_sync_fmt
    bra.s 5f
63:
    move.l #60,%d1
    muls.l %d1,%d0
    addi.l #pf_table,%d0
    movea.l %d0,%a1
    movea.l 52(%a1),%a1
    jsr (%a1)
    bra.s 5f
28:
    cmpi.l #fusion_sync,%d0
    beq.s 3f
    cmpi.l #fusion_mode,%d0
    beq.s 4f
    cmpi.l #fusion2_sync,%d0
    beq.s 13f
    cmpi.l #fusion2_mode,%d0
    beq.s 14f
    jsr twinshot_sample_fmt
    bra.s 5f
3:  bsr.w fusion_sync_fmt
    bra.s 5f
4:  bsr.w fusion_mode_fmt
    bra.s 5f
13: bsr.w fusion2_sync_fmt
    bra.s 5f
14: bsr.w fusion2_mode_fmt
5:  addq.l #8,%sp
    movea.l 12(%fp),%a2
    move.l 16(%fp),%d2
    move.l 20(%fp),%d3
    clr.l -(%sp)
    move.l %d3,%d0
    addi.l #24,%d0
    move.l %d0,-(%sp)
    move.l %d2,%d0
    addi.l #21,%d0
    move.l %d0,-(%sp)
    move.l %d3,%d0
    addq.l #7,%d0
    move.l %d0,-(%sp)
    move.l %d2,%d0
    subq.l #3,%d0
    move.l %d0,-(%sp)
    move.l %a2,-(%sp)
    jsr 0x401131ae
    lea 24(%sp),%sp
    pea -16(%fp)
    pea fusion_text
    pea 2
    move.l %d3,%d0
    addi.l #13,%d0
    move.l %d0,-(%sp)
    move.l %d2,%d0
    addi.l #9,%d0
    move.l %d0,-(%sp)
    pea 0x44d2e70c
    move.l %a2,-(%sp)
    jsr 0x40113dc8
    lea 28(%sp),%sp
    bra.s fusion_caption
62: movea.l 12(%fp),%a2
    move.l 16(%fp),%d2
    move.l 20(%fp),%d3
fusion_caption:
    | Native vfunc45 normally substitutes the current VALUE for the caption
    | while editing. We already show that value in the glyph: keep the FUS2
    | identity visible below it, instead of two numbers and no knob label.
    | Only the caption interior is ours; retain native outer/lock outlines.
    move.l 24(%fp),%d0
    cmpi.l #fusion2_sync,%d0
    blt.w 29f
    cmpi.l #fusion2_sync_rel_ui,%d0
    bgt.w 29f
    clr.l -(%sp)
    move.l %d3,%d0
    addq.l #4,%d0
    move.l %d0,-(%sp)
    move.l %d2,%d0
    addi.l #20,%d0
    move.l %d0,-(%sp)
    move.l %d3,-(%sp)
    move.l %d2,%d0
    subq.l #2,%d0
    move.l %d0,-(%sp)
    move.l %a2,-(%sp)
    jsr 0x401131ae
    lea 24(%sp),%sp
    move.l 24(%fp),%d0
    moveq #60,%d1
    muls.l %d1,%d0
    addi.l #pf_table,%d0
    movea.l %d0,%a0
    move.l 48(%a0),-(%sp)
    pea fusion_text
    pea 2
    move.l %d3,-(%sp)
    move.l %d2,%d0
    addq.l #8,%d0
    addq.l #1,%d0
    move.l %d0,-(%sp)
    pea 0x44d2e70c
    move.l %a2,-(%sp)
    jsr 0x40113dc8
    lea 28(%sp),%sp
29:
    movem.l (%sp),%d2-%d4/%a2-%a3
    unlk %fp
    rts
    .section .rodata
fusion_modes: .ascii "BLNDRINGDUCKMRPH"
fusion2_modes: .ascii "BLNDRINGDUCKMRPHGATE"
fusion2_syncs: .ascii "OFF\0\0\0MATCH\0REL\0\0\0"
fusion2_info_off: .asciz "Independent"
fusion2_info_match: .asciz "Fit to A"
fusion2_info_rel: .asciz "Fit+transpose"
fusion_text: .asciz "%s"
