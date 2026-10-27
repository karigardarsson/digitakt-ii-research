| core/params on the Digitakt II 1.17: the knob bar of an added parameter spans the row's own range.
|
| The knob slot of an added parameter draws through its template's value-class object (T2): ParameterSet::vfunc_24 =
| 0x4003a768(this, code, value, ...) runs the std::function at +0x14 of it with the value, and the LEV template's drawer
| (fader and bar, 0x400c32c0 and its kin) scales `value * 14 / 0x7f00`: the fixed 0 .. 0x7f00 of a level. A row with
| another range (PLAITS' MODEL, 0 .. 0x1800) therefore reached 0x1800 / 0x7f00 = 19 % of the bar at its maximum.
|
| The hook (displaced = "run") on that function's entry maps `value` of an added code from min .. max onto 0 .. 0x7f00
| before the drawer sees it; a row whose range is already 0 .. 0x7f00 is untouched (the same value), and so is every
| stock code. The value text and the stored value are not involved (the drawer gets a copy on the stack).
|
| Stack at the hook: 0 return address, 4 this, 8 code, 12 value. d0/d1/a0/a1 are free.

    .text
    .equ P_MIN, 16              | row layout (module.toml [[registry.field]] order): name, short, template, slot, min,
    .equ P_MAX, 20              | max, ...

    .globl pf_drawone
pf_drawone:
    move.l  8(%sp),%d0
    subi.l  #_pf_first,%d0
    cmpi.l  #_pf_count,%d0
    bcc.s   9f                  | a stock code
    move.l  #_pf_rowsize,%d1
    muls.l  %d1,%d0
    addi.l  #_pf_rows,%d0
    movea.l %d0,%a0
    move.l  P_MAX(%a0),%d1
    sub.l   P_MIN(%a0),%d1      | the range
    ble.s   9f                  | an empty range: leave it
    cmpi.l  #0x7f00,%d1
    beq.s   9f                  | already the level's range
    move.l  12(%sp),%d0
    sub.l   P_MIN(%a0),%d0
    bge.s   1f
    moveq   #0,%d0
1:  cmp.l   %d1,%d0
    ble.s   2f
    move.l  %d1,%d0
2:  lsl.l   #8,%d0
    movea.l %d0,%a1             | value << 8
    lsl.l   #7,%d0              | value << 15
    sub.l   %a1,%d0             | value * 0x7f00
    divu.l  %d1,%d0
    move.l  %d0,12(%sp)
9:  jmp     _pf_t_draw
