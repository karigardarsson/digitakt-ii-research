| core/machines on the Digitone II (1.11, 1.12): the ColdFire routines the hooks of module.toml enter.
|
| The framework does not know the machines at build time. A content module adds an `[[entry]] registry =
| "machines"`; patchkit lays the entries out as the table `machines.rows` (row layout in module.toml, offsets O_*
| below), gives every entry its permanent type number (types.toml) and copies the stock tables (names, permission masks,
| per-type bytes, DSP selector) with one row appended per entry. These routines look the rows up at run time for a
| type >= machines.first, and give every other type to the stock code unchanged.
|
| Hook convention (patchkit "hook" op, displaced = "run"): the site is overwritten with `jmp.l <routine>`; a routine
| that does not take the type over ends with `jmp <hook tail>` (the displaced instructions, then the rest of the stock
| code). A routine may use only the registers its site frees (stated per routine); everything else it saves.
| Registers named in a comment as "free" are scratch at that point of the stock code (read from the disassembly).
|
| Lazily built tables (mf_ensure, once, the first time a routine needs them): the displayed order of the machine
| list (stock order with every entry inserted at its `position`; gap rows, position -1, are not listed) and the std::string representations the dispatch
| descriptors point to (upper-cased name and short name). The Digitone II has no sort comparator, per-type byte
| table, clone tests, parameter type argument or DSP selector table; it has the type maps, the frame type and the
| type reader instead (finding 02, "core.machines on Digitone II 1.12").

    .text

| row layout (bytes), the field order of module.toml's [[registry.field]]
    .equ O_LONG,   0           | descriptor: chars of the std::string long name (built by mf_ensure)
    .equ O_SHORT,  4           | descriptor: chars of the std::string short name (built by mf_ensure)
    .equ O_F0,     8           | descriptor: the nine parameter descriptor IDs, O_F0 .. O_F0 + 32
    .equ O_CLONE,  44
    .equ O_POS,    48
    .equ O_OWN,    52          | Digitone II: the DSP is told the entry's own type (unused here)
    .equ O_NAME,   56          | pointer to the display name (C string)
    .equ O_SNAME,  60          | pointer to the short label (C string)
    .equ ROWSIZE,  64
    .equ MAXN,     32          | registry max
| row layout of the `pages` registry (module.toml): name, title (std::string chars after mf_ensure), nine codes, the
| machine's type, nine slots
    .equ PG_NAME,  0
    .equ PG_TITLE, 4
    .equ PG_C,     8
    .equ PG_MACH,  44
    .equ PG_S,     48
    .equ PG_SIZE,  84
    .equ PGMAX,    32          | registry max of `pages`
    .equ REPSIZE,  48          | bytes per std::string representation in mf_strbuf

| ---- mf_ensure: build the run-time tables once. Preserves every register (not the flags).
mf_ensure:
    tst.b   mf_ready
    bne.w   9f
    lea     -60(%sp),%sp
    movem.l %d0-%d7/%a0-%a6,(%sp)
    | list: the stock order, then each entry inserted at min(position, length)
    lea     mf_list_buf,%a0
    lea     _mf_list_stock,%a1
    move.l  #_mf_first,%d2
    move.l  %d2,%d3                 | d3 = length
1:  move.l  (%a1)+,(%a0)+
    subq.l  #1,%d2
    bne.s   1b
    lea     _mf_rows,%a2
    move.l  #_mf_count,%d4
    move.l  #_mf_first,%d5          | d5 = the type of entry k
    tst.l   %d4
    beq.s   5f
2:  move.l  O_POS(%a2),%d0
    addq.l  #1,%d0
    beq.s   20f                     | position -1: a gap row (a number nobody selected): not listed
    subq.l  #1,%d0
    cmp.l   %d3,%d0
    bls.s   3f
    move.l  %d3,%d0
3:  lea     mf_list_buf,%a0
    move.l  %d3,%d1
4:  cmp.l   %d0,%d1                 | shift the entries from position up by one
    bls.s   6f
    move.l  -4(%a0,%d1.l*4),%d6
    move.l  %d6,(%a0,%d1.l*4)
    subq.l  #1,%d1
    bra.s   4b
6:  move.l  %d5,(%a0,%d0.l*4)
    addq.l  #1,%d3
20: addq.l  #1,%d5
    lea     ROWSIZE(%a2),%a2
    subq.l  #1,%d4
    bne.s   2b
5:    | descriptors: std::string representations of the upper-cased names
    moveq   #1,%d6
    lea     _mf_rows,%a2
    lea     mf_strbuf,%a3
    move.l  #_mf_count,%d4
    tst.l   %d4
    beq.s   8f
10: movea.l O_NAME(%a2),%a0
    bsr.w   mf_rep
    move.l  %d0,O_LONG(%a2)
    movea.l O_SNAME(%a2),%a0
    bsr.w   mf_rep
    move.l  %d0,O_SHORT(%a2)
    lea     ROWSIZE(%a2),%a2
    subq.l  #1,%d4
    bne.s   10b
8:    | page descriptors: the names and titles as they are (the row is the descriptor)
    moveq   #0,%d6
    lea     _mf_pgrows,%a2
    lea     mf_pgstr,%a3
    move.l  #_mf_pgn,%d4
    tst.l   %d4
    beq.s   12f
11: movea.l PG_NAME(%a2),%a0
    bsr.w   mf_rep
    move.l  %d0,PG_NAME(%a2)
    movea.l PG_TITLE(%a2),%a0
    bsr.w   mf_rep
    move.l  %d0,PG_TITLE(%a2)
    lea     PG_SIZE(%a2),%a2
    subq.l  #1,%d4
    bne.s   11b
12: moveq   #-1,%d0
    move.b  %d0,mf_ready
    movem.l (%sp),%d0-%d7/%a0-%a6
    lea     60(%sp),%sp
9:  rts

| a0 = C string, a3 = destination, d6 = 1 to upper-case; builds {length, capacity, refcount -1, upper-cased chars, 0} at a3, returns the
| address of the chars in d0 and advances a3 to the next 4-byte boundary. Clobbers d1, d2, a0, a1.
mf_rep:
    moveq   #0,%d1
    lea     12(%a3),%a1
1:  move.b  (%a0)+,%d2
    beq.s   2f
    tst.l   %d6                     | d6 = 0: copy as it is
    beq.s   3f
    cmpi.b  #'a',%d2
    blo.s   3f
    cmpi.b  #'z',%d2
    bhi.s   3f
    subi.l  #32,%d2
3:  move.b  %d2,(%a1)+
    addq.l  #1,%d1
    bra.s   1b
2:  clr.b   (%a1)
    addq.l  #1,%a1
    move.l  %d1,(%a3)
    move.l  %d1,4(%a3)
    moveq   #-1,%d2
    move.l  %d2,8(%a3)
    move.l  %a3,%d0
    addi.l  #12,%d0
    move.l  %a1,%d2
    addq.l  #3,%d2
    andi.l  #-4,%d2
    movea.l %d2,%a3
    rts

| ---- dispatch: `FUN_xxxx(type)` returns the descriptor of a machine type. Site: function entry (d0, d1 free).
    .globl mf_dispatch
mf_dispatch:
    move.l  4(%sp),%d0
    subi.l  #_mf_first,%d0
    cmpi.l  #_mf_count,%d0
    bcc.s   1f
    bsr.w   mf_ensure
    move.l  #ROWSIZE,%d1
    muls.l  %d1,%d0
    addi.l  #_mf_rows,%d0
    rts
1:  jmp     _mf_t_dispatch

| ---- list builder entry: make sure the tables exist before the builder reads mf_list_buf (the builder's literals
| point there). Site: function entry; every register is preserved.
    .globl mf_list_entry
mf_list_entry:
    bsr.w   mf_ensure
    jmp     _mf_t_list


| mf_row: d0 = index k (< count) -> a0 = &machines.rows[k]. Preserves every other register (not the flags).
mf_row:
    move.l  %d1,-(%sp)
    move.l  #ROWSIZE,%d1
    muls.l  %d0,%d1
    addi.l  #_mf_rows,%d1
    movea.l %d1,%a0
    move.l  (%sp)+,%d1
    rts

| ---- group helper (types 0..3 group 1, 4 group 2, anything else 0): every added type is group 1. The stock code
| compares d0 with 3 first; an added type is turned into 0 (d0 is only the argument of the test). Site: function
| entry, d1 is set by the first instruction.
    .globl mf_group
mf_group:
    move.l  %d0,%d1
    subi.l  #_mf_first,%d1
    cmpi.l  #_mf_count,%d1
    bcc.s   1f
    moveq   #0,%d0
1:  jmp     _mf_t_group

| ---- type maps: three functions index per-type tables built at start-up with the machine type they get as a stack
| argument, and fall back to MIDI's row above type 4. A machine with page rows (registry `pages`) answers them itself
| (page count, page descriptor, slot -> code from its rows); any other added type is replaced by its clone_of in the
| argument slot before the stock code loads it (the slot is the function's own parameter). Site: function entry; off
| = the argument's offset from the entry stack pointer. d0, d1, a0, a1 are free (a0 holds the slot in mf_typemap2).
    .macro CLONEMAP off                 | d0 = the type (>= first)
    lea     -12(%sp),%sp
    movem.l %d0-%d1/%a0,(%sp)
    subi.l  #_mf_first,%d0
    cmpi.l  #_mf_count,%d0
    bcc.s   1f
    bsr.w   mf_row
    move.l  O_CLONE(%a0),%d0
    move.l  %d0,(12 + \off,%sp)
1:  movem.l (%sp),%d0-%d1/%a0
    lea     12(%sp),%sp
    .endm

| FUN(type) -> the number of pages
    .globl mf_typemap0
mf_typemap0:
    move.l  4(%sp),%d0
    cmpi.l  #_mf_first,%d0
    bcs.s   9f                          | a stock type (or a negative one: the range check below refuses it)
    movea.l %d0,%a1
    bsr.w   mf_pg_count
    tst.l   %d0
    beq.s   1f
    rts                                 | the count of the machine's own pages
1:  move.l  %a1,%d0
    CLONEMAP 4
9:  jmp     _mf_t_typemap0

| FUN(type, page) -> the page descriptor (D2 is saved by the stock code before the site)
    .globl mf_typemap1
mf_typemap1:
    move.l  8(%sp),%d0
    cmpi.l  #_mf_first,%d0
    bcs.s   9f
    movea.l %d0,%a1
    bsr.w   mf_pg_count
    tst.l   %d0
    beq.s   1f
    move.l  %d0,%d1                     | count
    move.l  0xc(%sp),%d0                | page
    bmi.s   9f                          | out of range: the stock code answers MIDI's row (the type is above 4)
    cmp.l   %d1,%d0
    bcc.s   9f
    move.l  %d0,%d1
    move.l  %a1,%d0
    bsr.w   mf_pg_page                  | a0 = the row (the descriptor)
    move.l  %a0,%d0
    move.l  (%sp)+,%d2
    rts
1:  move.l  %a1,%d0
    CLONEMAP 8
9:  jmp     _mf_t_typemap1

| FUN(slot, type, sub) -> the parameter code (D2 saved by the stock code; a0 = the slot and d1 = 0x64 are loaded already; both are kept)
    .globl mf_typemap2
mf_typemap2:
    move.l  0xc(%sp),%d0
    cmpi.l  #_mf_first,%d0
    bcs.s   9f
    move.l  %a0,%d1
    movea.l %d0,%a1
    bsr.w   mf_pg_slot                  | d0 = the code, d1 = 1 when the type has pages; a0 is lost
    movea.l 8(%sp),%a0
    tst.l   %d1
    beq.s   1f
    move.l  %a0,%d1
    subi.l  #0x19,%d1
    cmpi.l  #0x27,%d1
    bls.s   2f
    moveq   #0x64,%d1                   | not a machine slot: the stock code does not look at the type
    bra.s   9f
2:  move.l  (%sp)+,%d2
    rts
1:  move.l  %a1,%d0
    CLONEMAP 0xc
    moveq   #0x64,%d1                   | the stock code set d1 before the site and reads it again
9:  jmp     _mf_t_typemap2

| ---- page rows. mf_pg_count: d0 = type -> d0 = the number of its pages (builds the tables first); clobbers d1, a0.
mf_pg_count:
    bsr.w   mf_ensure
    move.l  %d2,-(%sp)
    move.l  #_mf_pgn,%d2
    lea     _mf_pgrows,%a0
    moveq   #0,%d1
    tst.l   %d2
    beq.s   9f
1:  cmp.l   PG_MACH(%a0),%d0
    bne.s   2f
    addq.l  #1,%d1
2:  lea     PG_SIZE(%a0),%a0
    subq.l  #1,%d2
    bne.s   1b
9:  move.l  %d1,%d0
    move.l  (%sp)+,%d2
    rts

| mf_pg_page: d0 = type, d1 = page (< its count) -> a0 = the page's row; clobbers d1.
mf_pg_page:
    move.l  %d2,-(%sp)
    move.l  #_mf_pgn,%d2
    lea     _mf_pgrows,%a0
    tst.l   %d2
    beq.s   8f
1:  cmp.l   PG_MACH(%a0),%d0
    bne.s   2f
    tst.l   %d1
    beq.s   7f
    subq.l  #1,%d1
2:  lea     PG_SIZE(%a0),%a0
    subq.l  #1,%d2
    bne.s   1b
8:  suba.l  %a0,%a0
7:  move.l  (%sp)+,%d2
    rts

| mf_pg_slot: d0 = type, d1 = sound-array slot -> d0 = the code of the knob that has that slot on one of the type's
| pages (0 if none), d1 = 1 when the type has pages at all; clobbers a0.
mf_pg_slot:
    lea     -12(%sp),%sp
    movem.l %d2-%d4,(%sp)
    move.l  #_mf_pgn,%d2
    lea     _mf_pgrows,%a0
    moveq   #0,%d3
    moveq   #0,%d4
    tst.l   %d2
    beq.w   8f
1:  cmp.l   PG_MACH(%a0),%d0
    bne.w   6f
    moveq   #1,%d3
    .irp i,0,1,2,3,4,5,6,7,8
    cmp.l   PG_S + 4 * \i(%a0),%d1
    bne.s   5f
    move.l  PG_C + 4 * \i(%a0),%d4
    bra.w   8f
5:
    .endr
6:  lea     PG_SIZE(%a0),%a0
    subq.l  #1,%d2
    bne.w   1b
8:  move.l  %d4,%d0
    move.l  %d3,%d1
    movem.l (%sp),%d2-%d4
    lea     12(%sp),%sp
    rts

| ---- frame type: the frame builder (the vector-191 handler) writes the track's machine type into TX frame word
| 0x94 + 2t. The DSP's FM engine knows five types; a type above 4 is treated like MIDI (nothing rendered), so an
| added type is sent as its clone_of unless the entry has own_dsp (a registered voice routine renders it). Site:
| `move.b (0xd8c,A0),D0b; ext.w D0w` (6 bytes), a0 = the track; d0 is the result.
    .globl mf_frametype
mf_frametype:
    mvs.b   0xd8c(%a0),%d0              | the stock move.b and ext.w in one (the upper word of d0 is dead)
    cmpi.l  #_mf_first,%d0
    blt.s   9f
    lea     -12(%sp),%sp
    movem.l %d1-%d2/%a1,(%sp)
    move.l  %d0,%d1
    subi.l  #_mf_first,%d1
    cmpi.l  #_mf_count,%d1
    bcc.s   1f
    move.l  #ROWSIZE,%d2
    muls.l  %d2,%d1
    addi.l  #_mf_rows,%d1
    movea.l %d1,%a1
    move.l  O_OWN(%a1),%d2
    bne.s   1f
    move.l  O_CLONE(%a1),%d0
1:  movem.l (%sp),%d1-%d2/%a1
    lea     12(%sp),%sp
9:  jmp     _mf_ft + 6

| ---- type reader: `FUN_4004b7f2` returns the machine type of a sound (`mvs.b (0xde,A0),D0`, then a bra.b to the
| epilogue). Its callers are pages and parameter code that test the type against FM TONE's and so on, so a clone
| must look like its original to them. The identity code (list stepping, the name of the machine, sound snapshots,
| preset and clear: nine call sites) wants the entry's own type: the routine compares the return address of the
| reader's caller (4(sp): the reader pushed one register) with the end of each of those calls. Site: the load
| (6 bytes: the load and the bra.b); the routine ends at the epilogue (site + 8).
    .globl mf_reader
mf_reader:
    mvs.b   0xde(%a0),%d0
    cmpi.l  #_mf_first,%d0
    blt.w   9f
    move.l  %d0,%d1
    subi.l  #_mf_first,%d1
    cmpi.l  #_mf_count,%d1
    bcc.w   9f
    move.l  4(%sp),%d1
    cmpi.l  #_mf_rc0 + 4,%d1
    beq.w   9f
    cmpi.l  #_mf_rc1 + 4,%d1
    beq.w   9f
    cmpi.l  #_mf_rc2 + 6,%d1
    beq.w   9f
    cmpi.l  #_mf_rc3 + 6,%d1
    beq.w   9f
    cmpi.l  #_mf_rc4 + 6,%d1
    beq.w   9f
    cmpi.l  #_mf_rc5 + 6,%d1
    beq.w   9f
    cmpi.l  #_mf_rc6 + 6,%d1
    beq.w   9f
    cmpi.l  #_mf_rc7 + 6,%d1
    beq.w   9f
    cmpi.l  #_mf_rc8 + 6,%d1
    beq.w   9f
    | a machine with own pages is its own type for every caller (the page, draw, defaults and clipboard code all index
    | per-type structures that have its rows): the other added types look like their clone
    move.l  %d0,-(%sp)
    bsr.w   mf_pg_count
    move.l  %d0,%d1
    move.l  (%sp)+,%d0
    tst.l   %d1
    bne.w   9f
    move.l  %a1,-(%sp)
    subi.l  #_mf_first,%d0
    move.l  #ROWSIZE,%d1
    muls.l  %d1,%d0
    addi.l  #_mf_rows,%d0
    movea.l %d0,%a1
    move.l  O_CLONE(%a1),%d0
    movea.l (%sp)+,%a1
9:  jmp     _mf_rd + 8

| `FUN_4004b7f2(project, track)` (the track's type, 4 when it is not a synth track) ends in a tail jump to the reader;
| its callers (the machine list and selection views, names, kit events) want the real type too. They get a copy of
| the function (section mf_reader_real, made by patchkit from the image), which keeps the unhooked load.

| ---- owner test of `SoundParameterSet::vfunc_21` (type == the parameter's owner machine): a clone looks like its
| original. Site: `mvs.b (0xde,A0),D3; move.l D2,-(SP)` (6 bytes). d0 and a1 are saved.
    .globl mf_owner
mf_owner:
    mvs.b   0xde(%a0),%d3
    cmpi.l  #_mf_first,%d3
    blt.s   9f
    lea     -8(%sp),%sp
    movem.l %d0/%a1,(%sp)
    move.l  %d3,%d0
    subi.l  #_mf_first,%d0
    cmpi.l  #_mf_count,%d0
    bcc.s   1f
    move.l  #ROWSIZE,%d3
    muls.l  %d3,%d0
    addi.l  #_mf_rows,%d0
    movea.l %d0,%a1
    move.l  O_CLONE(%a1),%d3
    bra.s   2f
1:  move.l  %d0,%d3
    addi.l  #_mf_first,%d3
2:  movem.l (%sp),%d0/%a1
    lea     8(%sp),%sp
9:  move.l  %d2,-(%sp)
    jmp     _mf_ow + 6


    .bss
    .balign 4
    .globl mf_list_buf
mf_ready:    .space 4
mf_list_buf: .space 4 * 64
mf_strbuf:   .space REPSIZE * 2 * MAXN
mf_pgstr:    .space REPSIZE * 2 * PGMAX
