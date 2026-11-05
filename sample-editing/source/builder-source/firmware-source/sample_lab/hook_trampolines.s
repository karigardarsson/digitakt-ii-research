/* Exact complete displaced prologues from hash-gated OS1.17 combined MAIN. */
.text
.balignw 4,0x4e71
.globl sl_stock_encoder
sl_stock_encoder:
.byte 0x4f,0xef,0xff,0xe4,0x48,0xd7,0x3c,0x1c
jmp 0x40020968
.balignw 4,0x4e71
.globl sl_stock_key
sl_stock_key:
.byte 0x4e,0x56,0xff,0x6c,0x48,0xd7,0x3c,0xfc
jmp 0x40022c76
.balignw 4,0x4e71
.globl sl_stock_draw
sl_stock_draw:
.byte 0x4f,0xef,0xff,0xb8,0x48,0xd7,0x7c,0xfc
jmp 0x40021de8
.balignw 4,0x4e71
.globl sl_stock_tick
sl_stock_tick:
.byte 0x4f,0xef,0xff,0xc8,0x48,0xd7,0x0c,0x3c
jmp 0x400211c2
.balignw 4,0x4e71
.globl sl_stock_destroy
sl_stock_destroy:
.byte 0x2f,0x0a,0x20,0x3c,0x40,0x1e,0xf0,0xb8
jmp 0x4019bf5e

/* Same register-returned aggregates as native SamplePicker4001dc08..1dc14
 * and the established Credits view adoption wrapper. */
.globl sl_browser_reference
sl_browser_reference:
 move.l %a2,-(%sp)
 move.l 8(%sp),%a2
 clr.l -(%sp)
 pea 0x40225cec
 pea 0x40225c24
 move.l %a2,-(%sp)
 jsr 0x401e317c
 lea 16(%sp),%sp
 tst.l %d0
 beq 1f
 move.l 16(%sp),%a0
 move.l 12(%sp),-(%sp)
 move.l %d0,-(%sp)
 jsr 0x40159a50
 addq.l #8,%sp
 moveq #0,%d0
 bra 2f
1: moveq #-1,%d0
2: move.l (%sp)+,%a2
 rts
.globl sl_adopt
sl_adopt:
 move.l 12(%sp),%a0
 move.l 8(%sp),-(%sp)
 move.l 8(%sp),-(%sp)
 jsr 0x4011a0ba
 addq.l #8,%sp
 rts
