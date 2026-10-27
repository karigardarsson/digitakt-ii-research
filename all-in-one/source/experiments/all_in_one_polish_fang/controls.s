| Small type16 extensions around the established FUS2 ownership wrappers.
    .text
    .globl fang_typearg, fang_param, fang_split
    .extern fusion2_typearg, fusion2_param, fusion2_split_predicate
    .extern fang_res, fang_wave, fang_cut, fang_env, fang_dec, fang_glide, fang_bite
    .extern fang_typearg_exit
fang_typearg:
    move.l 0x10(%sp),%d0
    cmpi.l #16,%d0
    bne.s 1f
    cmpa.l #29,%a0
    bne.s 1f
    move.l #fang_res,%d0
    jmp fang_typearg_exit
1:  jmp fusion2_typearg

fang_param:
    move.l %a2,-(%sp)
    movea.l 8(%sp),%a2
    move.l 0x74(%a2),-(%sp)
    jsr 0x4004abb8
    move.l %d0,-(%sp)
    move.l 0x78(%a2),-(%sp)
    jsr 0x40035cca
    addq.l #8,%sp
    move.l %d0,(%sp)
    jsr 0x4005063e
    addq.l #4,%sp
    cmpi.l #12,%d0
    beq.s 6f
    cmpi.l #13,%d0
    beq.s 7f
    cmpi.l #16,%d0
    bne.s 2f
    move.l 12(%sp),%d0
    cmpi.l #8,%d0
    bhi.s 3f
    lsl.l #2,%d0
    lea fang_codes,%a0
    move.l (%a0,%d0.l),%d0
    movea.l (%sp)+,%a2
    rts
3:  moveq #0,%d0
    movea.l (%sp)+,%a2
    rts
2:  movea.l (%sp)+,%a2
    jmp fusion2_param
6:  lea twin_codes,%a1
    bra.s 8f
7:  lea fuse_codes,%a1
8:  move.l 12(%sp),%d0
    cmpi.l #8,%d0
    bhi.s 3b
    lsl.l #2,%d0
    move.l (%a1,%d0.l),%d0
    movea.l (%sp)+,%a2
    rts

fang_split:
    move.l 4(%sp),%d1
    move.l (%sp),%d0
    cmpi.l #0x4002e8dc,%d0
    beq.s 4f
    cmpi.l #0x4002e972,%d0
    bne.s 5f
4:  cmpi.l #15,%d1
    bhi.s 5f
    move.l %a0,-(%sp)
    move.l #154,%d0
    muls.l %d1,%d0
    addi.l #0x80003cd0,%d0
    movea.l %d0,%a0
    mvs.b (%a0),%d0
    movea.l (%sp)+,%a0
    cmpi.l #16,%d0
    bne.s 5f
    moveq #0,%d0
    rts
5:  jmp fusion2_split_predicate
    .section .rodata
fang_codes:
    .long 248,fang_wave,fang_cut,fang_res,fang_env,fang_dec,fang_glide,fang_bite,10
twin_codes:
    .long 282,248,280,281,285,283,284,254,10
fuse_codes:
    .long 288,248,286,287,291,289,290,254,10
