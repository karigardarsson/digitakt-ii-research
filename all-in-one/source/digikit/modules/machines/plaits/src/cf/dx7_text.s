| machines/plaits on the Digitakt II (1.17): the DX7 patch name in the knob-turn header of HARMONICS.
|
| On a PLAITS track whose MODEL is DX7-1..4 (knob positions 18..21 = banks 0..3), the header `Harmonics=...` shows the name
| of the patch that HARMONICS selects, the patch the DSP plays (the DX7 engine's quantiser is memoryless: patch = a fixed
| function of the HARMONICS word, one generated table for the DSP and for this file: lib/dx7_banks.c pl_dx7_patch_word and
| dx7_names.s, both from lib/gen_dx_tables.py). Every other model, and every other parameter, shows the number as before.
|
| Where: the knob-turn header (the view's draw function at 0x4006cb14 in 1.17, finding 02) formats the value with
| `jsr param_value_text` (0x400c8952, code and value pushed). A hook replaces that one jsr (a 6-byte instruction, so the
| site is exactly one `jmp`): at entry a2 = the SRC page view, d2 = the code, 0(sp) = code, 4(sp) = value (no return address;
| the hook jumps).
|   - not HARMONICS, or MODEL not a DX7 model: `jmp tail` runs the displaced jsr and goes on (the stock behaviour);
|   - else the name is written into param_value_text's own static buffer (0x4293b818), d0 = that address and
|     `jmp return` (the address after the site): what param_value_text would have returned.
| MODEL is read as the header reads HARMONICS itself: the view's value getter (vtable +0x54, `getter(view, code, -1)`) on the
| same view, so both values are the live ones of the track on screen. Nothing else shows a patch name: the other places that
| format a parameter value (param_value_text's two other callers, ParameterSet::vfunc_23) keep the number, because they do
| not give the formatter the track or its MODEL.
|
| The inline value label under each knob of the SRC page (a second hook, `dx7_label_text`): the page draws the label through
| ParameterSet::vfunc_23(this, code, value, buf) (the row's formatter, how MODEL shows `SQUARE`). The label is about 6
| characters wide, so it shows the short name (`dx7_short`, lib/gen_dx_tables.py `short_name`: the first characters, keeping a
| trailing number). The effective MODEL is the one the page itself uses for that cell: the firmware's own cell-value function
| (0x4006aa32 `(view, code, &locked)`, anchor plaits.dx7.effval), which follows the held trig: the step's p-lock of MODEL
| when it has one, else the track's. HARMONICS' `value` argument is that function's result for HARMONICS, so name and value
| come from one context. If MODEL is not a DX7 model the stock call runs (the number).
|
| Registers: d0, d1, a0, a1 are scratch (the cdecl rules of the code around the site); d2/a2 are kept.

    .text

| void plaits_dx7_name(int raw, int bank, char *buf)    cdecl; d0/d1/a0/a1 scratch, d2/d3 kept
| writes the NUL-terminated name of the patch HARMONICS word `raw` selects in DX7 bank `bank` (0..3) to buf (at most 11 bytes).
    .globl plaits_dx7_name
plaits_dx7_name:
    lea     -8(%sp),%sp
    movem.l %d2-%d3,(%sp)
    move.l  12(%sp),%d0         | raw
    move.l  16(%sp),%d1         | bank
    movea.l 20(%sp),%a1         | buf
    lea     dx7_thr,%a0
    moveq   #0,%d2              | patch = the number of boundaries raw has reached (the words rise)
    moveq   #31,%d3
1:  cmp.l   (%a0)+,%d0          | unsigned: raw - boundary
    bcs.s   2f
    addq.l  #1,%d2
    subq.l  #1,%d3
    bne.s   1b
2:  lsl.l   #5,%d1
    add.l   %d2,%d1             | 32 * bank + patch
    moveq   #12,%d3
    mulu.l  %d3,%d1             | * the row size
    lea     dx7_names,%a0
    adda.l  %d1,%a0
3:  move.b  (%a0)+,(%a1)+       | the name and its NUL
    bne.s   3b
    movem.l (%sp),%d2-%d3
    lea     8(%sp),%sp
    rts

| void plaits_dx7_short(int raw, int bank, char *buf)    as plaits_dx7_name, the short name (at most 7 bytes with the NUL)
    .globl plaits_dx7_short
plaits_dx7_short:
    lea     -8(%sp),%sp
    movem.l %d2-%d3,(%sp)
    move.l  12(%sp),%d0
    move.l  16(%sp),%d1
    movea.l 20(%sp),%a1
    lea     dx7_thr,%a0
    moveq   #0,%d2
    moveq   #31,%d3
1:  cmp.l   (%a0)+,%d0
    bcs.s   2f
    addq.l  #1,%d2
    subq.l  #1,%d3
    bne.s   1b
2:  lsl.l   #5,%d1
    add.l   %d2,%d1
    lsl.l   #3,%d1              | * 8
    lea     dx7_short,%a0
    adda.l  %d1,%a0
3:  move.b  (%a0)+,(%a1)+
    bne.s   3b
    movem.l (%sp),%d2-%d3
    lea     8(%sp),%sp
    rts

    .globl dx7_header_text
dx7_header_text:                | hooked at the jsr of the header: see above
    cmpi.l  #_dn_harm,%d2
    bne.s   9f
    moveq   #-1,%d0
    move.l  %d0,-(%sp)
    move.l  #_dn_model,-(%sp)
    move.l  %a2,-(%sp)
    movea.l (%a2),%a0
    movea.l 0x54(%a0),%a0
    jsr     (%a0)               | the MODEL word of this view's track
    lea     12(%sp),%sp
    asr.l   #8,%d0              | knob position
    subi.l  #18,%d0
    cmpi.l  #3,%d0
    bhi.s   9f                  | unsigned: below 18 fails too
    move.l  4(%sp),%d1          | HARMONICS' value, as the header got it
    pea     0x4293b818          | param_value_text's buffer
    move.l  %d0,-(%sp)          | the bank
    move.l  %d1,-(%sp)
    bsr.w   plaits_dx7_name
    lea     12(%sp),%sp
    move.l  #0x4293b818,%d0
    jmp     _dn_ret
9:  jmp     _dn_tail

    .globl dx7_nop
dx7_nop:
    rts

    .globl dx7_label_text
dx7_label_text:                 | hooked at `movea.l 0x5c(a1),a1; move.l d0,0x44(a7)` before the `jsr (a1)` of vfunc_23 (0x40069cc8)
    cmpi.l  #_dn_harm,%d2       | d2 = the code; a5 = the view; 0(sp) this, 4 code, 8 value, 12 buf; a1 = this's vtable; d0 is stored below
    bne.w   9f
    lea     -12(%sp),%sp
    move.l  %a1,(%sp)
    move.l  %d0,4(%sp)
    clr.l   8(%sp)              | the "locked" flag
    pea     8(%sp)
    move.l  #_dn_model,-(%sp)
    move.l  %a5,-(%sp)
    jsr     _dn_effval          | MODEL as this cell's page sees it
    lea     12(%sp),%sp
    asr.l   #8,%d0
    subi.l  #18,%d0
    cmpi.l  #3,%d0
    bhi.s   8f
    move.l  %d0,%d1             | the bank
    move.l  4(%sp),%d0          | the displaced `move.l d0,0x44(a7)`
    move.l  %d0,0x50(%sp)
    movea.l 24(%sp),%a0         | buf
    move.l  20(%sp),%d0         | HARMONICS' value
    move.l  %a0,-(%sp)
    move.l  %d1,-(%sp)
    move.l  %d0,-(%sp)
    bsr.w   plaits_dx7_short
    lea     12(%sp),%sp
    lea     dx7_nop,%a1         | the `jsr (a1)` that follows calls this instead of the number formatter
    lea     12(%sp),%sp
    jmp     _dn_lret
8:  movea.l (%sp),%a1
    move.l  4(%sp),%d0
    lea     12(%sp),%sp
9:  jmp     _dn_ltail
