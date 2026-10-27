| Kari UI 2.1: compact names in upstream's 25-position knob order.
| cdecl, d0/d1/a0/a1 scratch; writes at most 5 characters and NUL.
    .text
    .globl plaits_model_short
plaits_model_short:
    move.l 4(%sp),%d0
    asr.l #8,%d0
    movea.l 8(%sp),%a1
    cmpi.l #24,%d0
    bls.s 1f
    lea pm25_err,%a0
    bra.s 2f
1:  lea pm25_names,%a0
    movea.l (%a0,%d0.l*4),%a0
2:  move.b (%a0)+,(%a1)+
    bne.s 2b
    rts
    .section .rodata
    .balign 4
pm25_names:
    .long pm25_0,pm25_1,pm25_2,pm25_3,pm25_4,pm25_5,pm25_6,pm25_7
    .long pm25_8,pm25_9,pm25_10,pm25_11,pm25_12,pm25_13,pm25_14,pm25_15
    .long pm25_16,pm25_17,pm25_18,pm25_19,pm25_20,pm25_21,pm25_22,pm25_23,pm25_24
pm25_err: .asciz "ERR"
pm25_0: .asciz "VA"
pm25_1: .asciz "ASYTR"
pm25_2: .asciz "2FM"
pm25_3: .asciz "FORMT"
pm25_4: .asciz "ADDIT"
pm25_5: .asciz "WVTBL"
pm25_6: .asciz "CHORD"
pm25_7: .asciz "SPECH"
pm25_8: .asciz "SWARM"
pm25_9: .asciz "NOISE"
pm25_10: .asciz "DUST"
pm25_11: .asciz "STRNG"
pm25_12: .asciz "RINGS"
pm25_13: .asciz "KICK"
pm25_14: .asciz "SNARE"
pm25_15: .asciz "HIHAT"
pm25_16: .asciz "VCFVA"
pm25_17: .asciz "PHASE"
pm25_18: .asciz "DX7-1"
pm25_19: .asciz "DX7-2"
pm25_20: .asciz "DX7-3"
pm25_21: .asciz "DX7-4"
pm25_22: .asciz "TERRN"
pm25_23: .asciz "STRMC"
pm25_24: .asciz "SQUAR"
