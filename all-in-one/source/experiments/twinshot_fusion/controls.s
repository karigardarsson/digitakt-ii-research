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
    cmpi.l #fusion_sync,%d0
    beq.s 2f
    cmpi.l #fusion_mode,%d0
    beq.s 2f
    cmpi.l #fusion_sample1,%d0
    beq.s 2f
    cmpi.l #fusion_sample2,%d0
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
    move.l 24(%fp),-(%sp)
    move.l 20(%fp),-(%sp)
    move.l 16(%fp),-(%sp)
    move.l 12(%fp),-(%sp)
    move.l 8(%fp),-(%sp)
    jsr twinshot_widget
    lea 44(%sp),%sp
    lea -16(%fp),%a0
    move.l %a0,-(%sp)
    move.l 28(%fp),-(%sp)
    move.l 24(%fp),%d0
    cmpi.l #fusion_sync,%d0
    beq.s 3f
    cmpi.l #fusion_mode,%d0
    beq.s 4f
    jsr twinshot_sample_fmt
    bra.s 5f
3:  bsr.w fusion_sync_fmt
    bra.s 5f
4:  bsr.w fusion_mode_fmt
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
    movem.l (%sp),%d2-%d4/%a2-%a3
    unlk %fp
    rts
    .section .rodata
fusion_modes: .ascii "BLNDRINGDUCKMRPH"
fusion_text: .asciz "%s"
