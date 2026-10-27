| core/machines on the Digitakt II 1.17: a machine with `one_page = 1` has one SRC page.
|
| The SRC key opens SourcePageView, whose pages are a vector (+0x7c .. +0x80, 4 bytes per page) with the current index at
| +0x90; a second press of SRC steps to the next page. For a clone of MANUAL SLICE the second page repeats the first one's
| controls (PLAITS, the owner's request), and the page vector belongs to the view, not to the machine. So two hooks
| (displaced = "run"), both only for SourcePageView (its vtable word at +0 is `cf.machines.sourcevt`) on a track whose
| machine row has `one_page`:
|
|   count   the page count accessor 0x4006b01e(view) returns 1 (the draw code shows its page marker only above 1) and
|           puts the current index back to 0;
|   setPage 0x400691e2(view, page): the page is 0 (the stock clamp to count - 1 reads the vector, which has two entries).
|
| Every other view, and every track of another machine, runs the stock code. The track's type is read as the draw code
| reads it: type = 0x4005063e(0x40035cca(view->+0x78, 0x4004abb8(view->+0x74))).

    .text
    .equ O_ONEPAGE, 64          | the row's `one_page` field (module.toml [[registry.field]] order, after `short`)
    .equ ROWSIZE,   68

| mo_is_one: a0 = a view -> d0 != 0 when its track's machine has one_page. Scratch: d0, d1, a0, a1.
mo_is_one:
    lea     -8(%sp),%sp
    movem.l %d2/%a2,(%sp)
    movea.l %a0,%a2
    move.l  0x74(%a2),-(%sp)
    jsr     _mo_cur             | the current track
    addq.l  #4,%sp
    move.l  %d0,-(%sp)
    move.l  0x78(%a2),-(%sp)
    jsr     _mo_track           | its object
    addq.l  #8,%sp
    move.l  %d0,-(%sp)
    jsr     _mo_type            | the machine type (-1: none)
    addq.l  #4,%sp
    subi.l  #_mo_first,%d0
    cmpi.l  #_mo_count,%d0
    bcc.s   1f                  | a stock type (or none)
    move.l  #ROWSIZE,%d1
    muls.l  %d1,%d0
    addi.l  #_mo_rows,%d0
    movea.l %d0,%a0
    move.l  O_ONEPAGE(%a0),%d0
    bra.s   2f
1:  moveq   #0,%d0
2:  movem.l (%sp),%d2/%a2
    lea     8(%sp),%sp
    tst.l   %d0
    rts

    .globl mo_count
mo_count:                       | at 0x4006b01e: 4(sp) = view
    movea.l 4(%sp),%a0
    move.l  #_mo_srcvt,%d0
    cmp.l   (%a0),%d0
    bne.s   9f
    bsr.w   mo_is_one
    beq.s   9f
    movea.l 4(%sp),%a0
    clr.l   0x90(%a0)
    moveq   #1,%d0
    rts
9:  jmp     _mo_t_count

    .globl mo_setpage
mo_setpage:                     | at 0x400691e2: 4(sp) = view, 8(sp) = page
    movea.l 4(%sp),%a0
    move.l  #_mo_srcvt,%d0
    cmp.l   (%a0),%d0
    bne.s   9f
    bsr.w   mo_is_one
    beq.s   9f
    clr.l   8(%sp)
9:  jmp     _mo_t_setpage
