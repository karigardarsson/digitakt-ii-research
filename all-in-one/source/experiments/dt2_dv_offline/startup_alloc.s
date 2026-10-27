/* Ephemeral OS 1.16 priority-6 entry trampoline. Never install this image.
 * 0x40133646 displaces LINK.W A6,#-156 and MOVEM.L D2-D7/A2-A5,(SP).
 * Rejoin is 0x4013364e. The cave word is used only in the guest fixture.
 */
        .equ PTR_WORD, 0x40311ff0
        .equ MALLOC, 0x4011e674
        .equ REJOIN, 0x4013364e
        .equ SEED, 0x51a6d20f

        .text
        .global dv_startup
dv_startup:
        .word   0x4e56,0xff64       /* original LINK.W A6,#-156 */
        .word   0x48d7,0x3cfc       /* original MOVEM.L D2-D7/A2-A5,(SP) */
        lea     -64(%sp),%sp
        .word   0x48d7,0x7fff       /* D0-D7/A0-A6, incl allocator-clobbered regs */
        move.w  %sr,%d0
        move.w  %d0,60(%sp)         /* allocator/memory operations change CCR */
        move.l  PTR_WORD,%d0
        bne     ready               /* valid pointer or failure marker: one shot */
        moveq   #-1,%d0
        move.l  %d0,PTR_WORD        /* failure marker until zeroing is complete */
        pea     8192
        jsr     MALLOC
        addq.l  #4,%sp
        tst.l   %d0
        beq     ready
        movea.l %d0,%a0
        move.l  #2048,%d1
zero_state:
        clr.l   (%a0)+
        subq.l  #1,%d1
        bne     zero_state
        move.l  #SEED,%d1
        move.l  %d1,-16(%a0)       /* pointer + 0x1ff0 */
        move.l  %d0,PTR_WORD        /* publish only initialized state */
ready:
        move.w  60(%sp),%d0
        move.w  %d0,%sr
        .word   0x4cd7,0x7fff       /* restore D0-D7/A0-A6 */
        lea     64(%sp),%sp
        jmp     REJOIN
