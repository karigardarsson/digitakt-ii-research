| core/expansion: the reset-path loader (ColdFire, ISA_A).
|
| patchkit redirects the reset code's two calls (copy .data helper, clear bss helper) to pk_loader. The loader
| walks the segment chain patchkit appended to the MAIN OS image, copies each payload to its destination in the
| expansion region and zeroes the module's bss behind it, then runs the two original helpers and returns, so
| the reset path goes on exactly as before. It runs before the caches are on and before the bss clear, which
| is why it must copy the segments out of the range the clear is about to zero.
|
| Segment chain at pk_segs (the end of the stock MAIN OS image), all words big endian, 4-byte aligned:
|     [u32 len][u32 dest][u32 bss] [payload: len bytes]      repeated
|     [u32 0xffffffff]                                        end
| len and bss are multiples of 4. d0, d1, a0 and a1 are free at this point of the reset code.

    .text
    .globl pk_loader
pk_loader:
    lea     pk_segs,%a0
1:  move.l  (%a0)+,%d0              | payload bytes, or 0xffffffff at the end
    addq.l  #1,%d0
    beq.s   4f
    subq.l  #1,%d0
    movea.l (%a0)+,%a1              | destination
    move.l  (%a0)+,%d1              | bss bytes behind the payload
    lsr.l   #2,%d0
    beq.s   3f
2:  move.l  (%a0)+,(%a1)+
    subq.l  #1,%d0
    bne.s   2b
3:  lsr.l   #2,%d1
    beq.s   1b
5:  clr.l   (%a1)+
    subq.l  #1,%d1
    bne.s   5b
    bra.s   1b
4:  jsr     pk_copy_helper
    jsr     pk_clear_helper
    rts
