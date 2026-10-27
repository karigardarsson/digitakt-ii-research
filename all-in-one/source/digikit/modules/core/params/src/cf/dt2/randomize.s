| core/params on the Digitakt II 1.17: SRC+YES (randomize page) for added parameters.
|
| Why an added parameter did nothing (finding 02, "SRC+YES randomizes a page"): the page randomizer walks the track's
| machine descriptor (core/machines: the row of the added type, so the added codes are in the list) and calls the
| per-code randomizer ParameterSet::vfunc_7 = 0x4003dbda(this, code) for each. That function takes the value-class
| object of the code (the second per-code table, T2, whose accessor gives an added code its template's object) and
| runs the std::function at +0x2c/+0x30 of it. The templates of the PLAITS rows (LEV of MANUAL SLICE, 0xfe) hold none,
| so the function returned 0 for every added code, as it does for TUNE and LEV of the stock machines.
|
| The hook (displaced = "run") sits on the entry of that function. For a code of this registry whose `randomize` field
| is not 0 it picks a value and writes it through the same call as the stock randomizer, 0x4003b570(this, code, value,
| -1, 0, 1, 1) (the value is clamped to the row's range there, the callbacks that follow make p-lock and undo behave as
| for a stock parameter); every other code runs the stock function.
|
|   randomize = 0   the template's randomizer (none for the LEV template: the parameter is left unchanged)
|   randomize = 1   a uniform value over min .. max in steps of 0x100 (the UI integer)
|   randomize = fn  fn(rnd, min, max): rnd = 0 .. 0x7fff from the firmware's generator, min and max the row's range;
|                   returns the raw value. cdecl (arguments at 4(sp), 8(sp), 12(sp)), d0/d1/a0/a1 scratch, the others
|                   preserved, the caller pops.
|
| Stack at the hook: 0 return address, 4 this, 8 code. d0/d1/a0/a1 are free.

    .text
    .equ P_MIN, 16              | row layout (module.toml [[registry.field]] order): name, short, template, slot, min,
    .equ P_MAX, 20              | max, default, formatter, randomize
    .equ P_RANDOMIZE, 32

    .globl pf_randone
pf_randone:
    move.l  8(%sp),%d0
    subi.l  #_pf_first,%d0
    cmpi.l  #_pf_count,%d0
    bcc.w   9f                  | a stock code
    move.l  #_pf_rowsize,%d1
    muls.l  %d1,%d0
    addi.l  #_pf_rows,%d0
    movea.l %d0,%a0
    tst.l   P_RANDOMIZE(%a0)
    beq.w   9f                  | none: the template's randomizer
    lea     -12(%sp),%sp        | ColdFire movem has no predecrement mode; the arguments are now at 16(sp) (this), 20(sp) (code)
    movem.l %d2-%d3/%a2,(%sp)
    movea.l %a0,%a2
    jsr     _pf_rand            | d0 = 0 .. 0x7fff
    move.l  P_MIN(%a2),%d1
    move.l  P_MAX(%a2),%d2
    move.l  P_RANDOMIZE(%a2),%d3
    cmpi.l  #1,%d3
    bne.s   2f
    sub.l   %d1,%d2
    lsr.l   #8,%d2
    addq.l  #1,%d2              | n = the number of steps
    mulu.l  %d2,%d0
    lsr.l   #8,%d0
    lsr.l   #7,%d0              | (rnd * n) >> 15 = 0 .. n - 1
    lsl.l   #8,%d0
    add.l   %d1,%d0             | the value
    bra.s   3f
2:  move.l  %d2,-(%sp)          | custom: fn(rnd, min, max)
    move.l  %d1,-(%sp)
    move.l  %d0,-(%sp)
    movea.l %d3,%a0
    jsr     (%a0)
    lea     12(%sp),%sp
3:  pea     1.w                 | the stock call's arguments, last first; this and code were at 16(sp) and 20(sp)
    pea     1.w
    clr.l   -(%sp)
    pea     -1.w
    move.l  %d0,-(%sp)          | value
    move.l  40(%sp),-(%sp)      | code
    move.l  40(%sp),-(%sp)      | this
    jsr     _pf_write
    lea     28(%sp),%sp
    movem.l (%sp),%d2-%d3/%a2
    lea     12(%sp),%sp
    moveq   #1,%d0
    rts
9:  jmp     _pf_t_rnd
