/* Additional DV-only gate required by stock 0x40139520..26:
 * stock MODE can jump directly to 0x40139590 before waveform dispatch.
 * WAVE 0..6 replay both original tests and successors.
 */
        .text
        .global dv_gate
dv_gate:
        moveq   #7,%d7
        cmp.l   %d3,%d7
        beq     to_dispatch
        moveq   #8,%d7
        cmp.l   %d3,%d7
        beq     to_dispatch
        tst.b   (%a2)
        beq     to_dispatch
        tst.l   %d1
        beq     stock_skip
to_dispatch:
        jmp     0x40139528
stock_skip:
        jmp     0x40139590
