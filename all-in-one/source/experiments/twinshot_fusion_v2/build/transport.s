    .text
    .globl twinshot_smoother
    .globl twinshot_discrete
    .globl twinshot_sample_fmt
    .globl twinshot_widget
    .globl twinshot_slot_mask
    .extern twinshot_type
    .extern native_sample_fmt
    .extern twin_sample1
    .extern twin_sample2
    .extern twin_slot_mask_return

| Native pre-LFO split/reconstruction supports 10-bit stock slots. Twinshot
| carries direction in bit 10; preserve that bit in its bank byte only.
twinshot_slot_mask:
    lea -8(%sp),%sp
    movem.l %d0/%a0,(%sp)
    move.l #154,%d0
    muls.l %d7,%d0
    addi.l #0x80003cd0,%d0
    movea.l %d0,%a0
    mvs.b (%a0),%d0
    cmpi.l #twinshot_type,%d0
    beq.s 8f
    cmpi.l #13,%d0
    beq.s 8f
    andi.l #0x700,%d1
    bra.s 9f
8:  andi.l #0xf00,%d1
9:  movem.l (%sp),%d0/%a0
    lea 8(%sp),%sp
    jmp twin_slot_mask_return

| Call replacement: original argument and return stack layout preserved.
twinshot_smoother:
    move.l 4(%sp),-(%sp)
    jsr 0x400d9406
    addq.l #4,%sp
    bra.w twinshot_discrete

| Post-smoother, before stock split/repack and LFO tick. Only two discrete
| selectors of the new machine bypass smoothing; live numeric controls do not.
twinshot_discrete:
    lea -24(%sp),%sp
    movem.l %d0/%d2/%a2-%a5,(%sp)
    lea 0x80003340+0x5a,%a2
    lea 0x80005b50+0x5a,%a3
    lea 0x80003cd0,%a4
    moveq #15,%d2
1:  mvs.b (%a4),%d0
    cmpi.l #twinshot_type,%d0
    beq.s 6f
    cmpi.l #13,%d0
    beq.s 7f
    cmpi.l #14,%d0
    bne.s 2f
7:  move.w -4(%a2),-4(%a3)
    move.w -2(%a2),-2(%a3)
6:  move.w (%a2),(%a3)
    move.w 10(%a2),10(%a3)
2:  lea 0x8e(%a2),%a2
    lea 0x8e(%a3),%a3
    lea 0x9a(%a4),%a4
    subq.l #1,%d2
    bpl.s 1b
    movem.l (%sp),%d0/%d2/%a2-%a5
    lea 24(%sp),%sp
    rts

| Keep stock sample text (including native slot-name lookup where available).
| Prefix direction; raw encoding remains nonnegative for native p-lock storage.
twinshot_sample_fmt:
    move.l 4(%sp),%d0
    movea.l 8(%sp),%a0
    move.b #'F',(%a0)+
    btst #10,%d0
    beq.s 3f
    move.b #'R',-1(%a0)
3:  move.b #':',(%a0)+
    andi.l #1023,%d0
    move.l %a0,-(%sp)
    move.l %d0,-(%sp)
    jsr native_sample_fmt
    addq.l #8,%sp
    rts

| Chain the existing private Plaits widget. Sample-class bar scaling changes
| its DRAW COPY to a level (7 -> 111), so do not reuse that glyph as a slot
| number. Retain native labels/lock frames, then draw the actual F:/R: selector.
| Only our two descriptor IDs are intercepted; every existing code tail-jumps
| to the exact previous all-in-one UI entry with its original stack/registers.
twinshot_widget:
    move.l %d0,-(%sp)
    move.l 24(%sp),%d0
    cmpi.l #twin_sample1,%d0
    beq.s 4f
    cmpi.l #twin_sample2,%d0
    beq.s 4f
    move.l (%sp)+,%d0
    jmp 0x47ea0000
4:  move.l (%sp)+,%d0
    link %fp,#-64
    movem.l %d2-%d4/%a2-%a3,(%sp)
    move.l 48(%fp),-(%sp)
    move.l 44(%fp),-(%sp)
    move.l 40(%fp),-(%sp)
    move.l 36(%fp),-(%sp)
    move.l 32(%fp),-(%sp)
    move.l 28(%fp),-(%sp)
    move.l 24(%fp),-(%sp)
    move.l 20(%fp),-(%sp)
    move.l 16(%fp),-(%sp)
    move.l 12(%fp),-(%sp)
    move.l 8(%fp),-(%sp)
    jsr 0x47ea0000
    lea 44(%sp),%sp
    lea -16(%fp),%a0
    move.l %a0,-(%sp)
    move.l 28(%fp),-(%sp)
    bsr.w twinshot_sample_fmt
    addq.l #8,%sp
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
    pea tw_slot_text
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
    movem.l (%sp),%d2-%d4/%a2-%a3
    unlk %fp
    rts
    .section .rodata
tw_slot_text: .asciz "%s"
