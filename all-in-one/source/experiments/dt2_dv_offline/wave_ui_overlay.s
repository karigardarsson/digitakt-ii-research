/* Guest-only DT2 1.16 WAVE formatter extension.
 *
 * The real stock formatter at 0x400e1a04 is tail-called for every value
 * except the two DV Q8 enum values, keeping WAVE 0..6 byte-for-byte on the
 * original path.  This object is assembled only into the isolated test output.
 */
        .equ STOCK_WAVE_FORMATTER, 0x400e1a04

        .text
        .global wave_ui_formatter
wave_ui_formatter:
        move.l  4(%sp),%d0
        cmpi.l  #0x700,%d0
        beq     dv_f
        cmpi.l  #0x800,%d0
        beq     dv_t
        jmp     STOCK_WAVE_FORMATTER

dv_f:
        movea.l 8(%sp),%a0
        move.l  #0x44562d46,(%a0)       /* "DV-F" */
        clr.b   4(%a0)
        moveq   #4,%d0
        rts

dv_t:
        movea.l 8(%sp),%a0
        move.l  #0x44562d54,(%a0)       /* "DV-T" */
        clr.b   4(%a0)
        moveq   #4,%d0
        rts
