| New type 14 only. Original type13/12/Plaits/stock are fall-throughs.
    .text
    .globl fusion2_split_predicate, fusion2_typearg, fusion2_param, fusion2_draw_route
    .extern fusion2_start1, fusion2_start2, fusion2_shape
    .extern fusion2_sample1, fusion2_sample2, fusion2_sync, fusion2_mode, fusion2_tune2
    .extern fusion2_tunea_ui, fusion2_balance_ui, fusion2_ring_ui, fusion2_duck_ui
    .extern fusion2_morph_ui, fusion2_gate_ui, fusion2_dry_ui, fusion2_recovery_ui
    .extern fusion2_sweep_ui, fusion2_relative_ui
    .extern fusion2_sync_off_ui, fusion2_sync_match_ui, fusion2_sync_rel_ui
    .extern mf_typearg, fusion2_typearg_exit

fusion2_split_predicate:
    move.l 4(%sp),%d1
    | This is the shared AUDIO-track predicate, also used by native note-on
    | 40120258 / note-off 401205ae. Only these two frame-builder callers may
    | suppress bank shadows; never report FUS2 as a non-audio track elsewhere.
    move.l (%sp),%d0
    cmpi.l #0x4002e8dc,%d0
    beq.s 20f
    cmpi.l #0x4002e972,%d0
    bne.s 1f
20:
    cmpi.l #15,%d1
    bhi.s 1f
    move.l %a0,%d0
    move.l %d0,-(%sp)
    move.l #154,%d0
    muls.l %d1,%d0
    addi.l #0x80003cd0,%d0
    movea.l %d0,%a0
    mvs.b (%a0),%d0
    movea.l (%sp)+,%a0
    cmpi.l #14,%d0
    bne.s 1f
    moveq #0,%d0
    rts
1:  moveq #15,%d0
    jmp 0x4002dcf4

| Hook convention is core/machines' mid-function typearg site.
fusion2_typearg:
    move.l 0x10(%sp),%d0
    cmpi.l #14,%d0
    bne.s 2f
    cmpa.l #29,%a0
    beq.s 3f
    cmpa.l #30,%a0
    bne.s 2f
    move.l #fusion2_start2,%d0
    jmp fusion2_typearg_exit
3:  move.l #fusion2_start1,%d0
    jmp fusion2_typearg_exit
2:  jmp mf_typearg

| d0=type, callee-save a2. Keep native active-track resolution.
fusion2_page_type:
    move.l 0x74(%a2),-(%sp)
    jsr 0x4004abb8
    move.l %d0,-(%sp)
    move.l 0x78(%a2),-(%sp)
    jsr 0x40035cca
    addq.l #8,%sp
    move.l %d0,(%sp)
    jsr 0x4005063e
    addq.l #4,%sp
    rts

| SourcePageView vtable 47: code(view,knob). The native setter/header/lock
| routes all see the same descriptor IDs; no display-only fake controls.
fusion2_param:
    move.l %a2,-(%sp)
    movea.l 8(%sp),%a2
    bsr.w fusion2_page_type
    cmpi.l #14,%d0
    bne.w 4f
    move.l 12(%sp),%d0
    | The separate LEVEL encoder uses logical knob8 on every SRC page.
    | Keep native Track Level available on page2, not an empty control.
    cmpi.l #8,%d0
    beq.w 21f
    tst.l 0x90(%a2)
    beq.w fusion2_page_one
    cmpi.l #0,%d0
    beq.s 5f
    cmpi.l #1,%d0
    beq.s 6f
    cmpi.l #2,%d0
    beq.s 7f
    moveq #0,%d0
    movea.l (%sp)+,%a2
    rts
5:  move.l #fusion2_start1,%d0
    bra.s 8f
6:  move.l #fusion2_start2,%d0
    bra.s 8f
7:  bsr.w fusion2_current_mode
    lsl.l #2,%d0
    lea fusion2_response_codes,%a0
    move.l (%a0,%d0.l),%d0
    bra.s 8f
21: moveq #10,%d0
8:  movea.l (%sp)+,%a2
    rts
4:  movea.l (%sp)+,%a2
    jmp 0x40017cd4

| Page1 groups each sample with its tuning; the secondary response lives only
| on page2. Aliases share the ORIGINAL mirrors/ranges; no saved field is added.
fusion2_page_one:
    cmpi.l #2,%d0
    beq.w fusion2_sync_code
    cmpi.l #7,%d0
    bcc.s 31f
    cmpi.l #6,%d0
    beq.s 32f
    cmpi.l #5,%d0
    beq.s 33f
    lsl.l #2,%d0
    lea fusion2_page_one_codes,%a0
    move.l (%a0,%d0.l),%d0
    bra.s 8b
31: moveq #0,%d0
    bra.s 8b
32: bsr.w fusion2_current_mode
    lsl.l #2,%d0
    lea fusion2_amount_codes,%a0
    move.l (%a0,%d0.l),%d0
    bra.s 8b
33: pea -1
    pea fusion2_sync
    move.l %a2,-(%sp)
    jsr 0x4006978e
    lea 12(%sp),%sp
    cmpi.l #128,%d0
    blt.s 34f
    cmpi.l #384,%d0
    bge.s 35f
    moveq #0,%d0             | MATCH follows A; independent B tuning is inactive.
    bra.s 8b
34: move.l #fusion2_tune2,%d0
    bra.w 8b
35: move.l #fusion2_relative_ui,%d0
    bra.w 8b

fusion2_sync_code:
    pea -1
    pea fusion2_sync
    move.l %a2,-(%sp)
    jsr 0x4006978e
    lea 12(%sp),%sp
    cmpi.l #128,%d0
    blt.s 40f
    cmpi.l #384,%d0
    blt.s 41f
    move.l #fusion2_sync_rel_ui,%d0
    bra.w 8b
40: move.l #fusion2_sync_off_ui,%d0
    bra.w 8b
41: move.l #fusion2_sync_match_ui,%d0
    bra.w 8b

| Use the native effective UI getter, including its p-lock edit context. Do not
| mutate shared descriptors, Sound, globals or cached mode state while drawing.
fusion2_current_mode:
    pea -1
    pea fusion2_mode
    move.l %a2,-(%sp)
    jsr 0x4006978e
    lea 12(%sp),%sp
    tst.l %d0
    bpl.s 36f
    moveq #0,%d0
36: asr.l #8,%d0
    cmpi.l #4,%d0
    bls.s 37f
    moveq #4,%d0
37: rts

| Entry at draw+0x10, A2=view,D2=bitmap. Only FUS2 bypasses SLICE's
| second-page decoration; its page index remains live for fusion2_param.
fusion2_draw_route:
    lea -16(%sp),%sp
    movem.l %d0-%d1/%a0-%a1,(%sp)
    bsr.w fusion2_page_type
    cmpi.l #14,%d0
    bne.s 9f
    movem.l (%sp),%d0-%d1/%a0-%a1
    lea 16(%sp),%sp
    jmp 0x400170aa
9:  movem.l (%sp),%d0-%d1/%a0-%a1
    lea 16(%sp),%sp
    tst.l 0x90(%a2)
    beq.s 10f
    jmp 0x40016ec4
10: jmp 0x400170aa

    .section .rodata
fusion2_page_one_codes:
    .long fusion2_sample1, fusion2_tunea_ui, fusion2_sync, fusion2_mode, fusion2_sample2
fusion2_amount_codes:
    .long fusion2_balance_ui, fusion2_ring_ui, fusion2_duck_ui, fusion2_morph_ui, fusion2_gate_ui
fusion2_response_codes:
    .long 0, fusion2_dry_ui, fusion2_recovery_ui, fusion2_sweep_ui, fusion2_recovery_ui

    .text
    .globl fusion2_delta
| The stock accumulator and its native p-lock setter remain authoritative.
| Clamp only its final discrete increment: SYNC/FUSE one choice; SMP one slot
| for ordinary delta<=8. Deliberately fast/pressed sample browsing stays stock.
| Hook replaces the single JSR at4006cc62, not the shared WAVE/Zoom entry hook.
fusion2_delta:
    move.l 20(%sp),-(%sp)
    move.l 20(%sp),-(%sp)
    move.l 20(%sp),-(%sp)
    move.l 20(%sp),-(%sp)
    move.l 20(%sp),-(%sp)
    jsr 0x40111cda
    lea 20(%sp),%sp
    cmpi.l #fusion2_mode,%d2
    beq.s 50f
    cmpi.l #fusion2_sync,%d2
    beq.s 50f
    cmpi.l #fusion2_sync_off_ui,%d2
    blt.s 45f
    cmpi.l #fusion2_sync_rel_ui,%d2
    ble.s 50f
45: cmpi.l #fusion2_sample1,%d2
    beq.s 46f
    cmpi.l #fusion2_sample2,%d2
    bne.s 49f
46: movea.l %d4,%a0
    tst.b 20(%a0)
    bne.s 49f
    move.l 16(%a0),%d1
    cmpi.l #-8,%d1
    blt.s 49f
    cmpi.l #8,%d1
    bgt.s 49f
    moveq #1,%d1
    bra.s 51f
50: move.l #256,%d1
51: cmp.l %d1,%d0
    ble.s 52f
    move.l %d1,%d0
52: neg.l %d1
    cmp.l %d1,%d0
    bge.s 49f
    move.l %d1,%d0
49: rts
