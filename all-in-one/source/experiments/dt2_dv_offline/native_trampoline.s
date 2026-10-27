/* Replaces only the WAVE 7/8 fixed-Q31 stub in the proven dispatch hook.
 * Linked at 0x10003100 inside Unicorn guest RAM. The stock clamp hook
 * remains byte-identical to the previous trampoline prototype.
 */
        .text
        .global dv_dispatch
dv_dispatch:
        cmpi.l  #7,%d3
        beq     dv_route
        cmpi.l  #8,%d3
        beq     dv_route
        moveq   #6,%d7
        cmp.l   %d3,%d7
        beq     stock_rand
        jmp     0x4013952e
stock_rand:
        jmp     0x40139556
dv_route:
        lea     -56(%sp),%sp
        .word   0x48d7,0x7ffe     /* movem.l D1-D7/A0-A6,(SP) */
        lea     56(%sp),%a6
        jsr     0x10004000
        .word   0x4cd7,0x7ffe     /* movem.l (SP),D1-D7/A0-A6 */
        lea     56(%sp),%sp
        jmp     0x401395c2
