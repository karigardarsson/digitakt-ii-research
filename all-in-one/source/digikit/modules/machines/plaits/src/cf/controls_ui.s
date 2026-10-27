| SourcePageView slot 45, DT2 OS 1.17: render one parameter.
| Only codes 275..279 belong to PLAITS. Other codes tail-jump with the
| original stack and D0, executing the original function byte-for-byte.
    .text
    .globl plaits_ui_widget
plaits_ui_widget:
    move.l %d0,-(%sp)
    move.l 24(%sp),%d0
    subi.l #275,%d0
    cmpi.l #5,%d0
    bcs.s 1f
    move.l (%sp)+,%d0
    jmp plaits_stock_widget
1:  move.l (%sp)+,%d0
    jmp plaits_ui_draw
