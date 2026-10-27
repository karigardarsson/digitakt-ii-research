/* DT2 OS 1.16 DV V5 one-shot initializer.
 *
 * Entered by the replacement JSR at 0x40033948 after both boot-mode setup
 * branches converge and after the firmware allocator has already been used.
 * The original return address remains on the stack.  After restoring the
 * complete caller-visible state, tail-call the displaced stock function so it
 * returns directly to 0x4003394e.
 */
        .equ PTR_WORD,         0x40311ff0
        .equ HOOK_SEEN,        0x40311fe4
        .equ MALLOC_RESULT,    0x40311fe8
        .equ PUBLISH_OK,       0x40311fec
        .equ MALLOC,           0x4011e674
        .equ STOCK_CALLEE,     0x400c14dc
        .equ SEED,             0x51a6d20f
        .equ HOOK_MAGIC,       0x44564833
        .equ MALLOC_PENDING,   0x4d414c3f
        .equ PUBLISH_PENDING,  0x5055423f
        .equ PUBLISH_MAGIC,    0x44565033

        .text
        .global dv_startup_common_v5
dv_startup_common_v5:
        lea     -64(%sp),%sp
        .word   0x48d7,0x7fff       /* D0-D7/A0-A6 */
        move.w  %sr,%d0
        move.w  %d0,60(%sp)

        move.l  #HOOK_MAGIC,%d0
        move.l  %d0,HOOK_SEEN
        move.l  PTR_WORD,%d0
        beq     allocate
        cmpi.l  #-1,%d0
        bne     ready               /* successful prior initialization */

allocate:
        move.l  #MALLOC_PENDING,%d0
        move.l  %d0,MALLOC_RESULT
        move.l  #PUBLISH_PENDING,%d0
        move.l  %d0,PUBLISH_OK
        moveq   #-1,%d0
        move.l  %d0,PTR_WORD
        pea     8192
        jsr     MALLOC
        addq.l  #4,%sp
        move.l  %d0,MALLOC_RESULT
        tst.l   %d0
        beq     ready               /* -1 pointer + zero result = retryable FAIL */

        movea.l %d0,%a0
        move.l  #2048,%d1
zero_state:
        clr.l   (%a0)+
        subq.l  #1,%d1
        bne     zero_state
        move.l  #SEED,%d1
        move.l  %d1,-16(%a0)
        move.l  %d0,PTR_WORD
        move.l  #PUBLISH_MAGIC,%d1
        move.l  %d1,PUBLISH_OK

ready:
        move.w  60(%sp),%d0
        move.w  %d0,%sr
        .word   0x4cd7,0x7fff
        lea     64(%sp),%sp
        jmp     STOCK_CALLEE
