| core/machines on the Digitakt II (1.16, 1.17): the ColdFire routines the hooks of module.toml enter.
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
| list (stock order with every entry inserted at its `position`; gap rows, position -1, are not listed), the rank pairs the sort comparator's std::map
| takes, and the std::string representations the dispatch descriptors point to (upper-cased name and short name).

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
    .equ ROWSIZE,  68
    .equ SLICE,    6           | the stock type whose `type == 6` tests an added clone of it also passes
    .equ MAXN,     32          | registry max
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
5:  | rank pairs {type, display position}
    lea     mf_list_buf,%a0
    lea     mf_rank_buf,%a1
    moveq   #0,%d0
7:  move.l  (%a0)+,(%a1)+
    move.l  %d0,(%a1)+
    addq.l  #1,%d0
    cmp.l   %d3,%d0
    bcs.s   7b
    | descriptors: std::string representations of the upper-cased names
    lea     _mf_rows,%a2
    lea     mf_strbuf,%a3
    move.l  #_mf_count,%d4
    tst.l   %d4
    beq.s   8f
10: movea.l O_NAME(%a2),%a0
    bsr.s   mf_rep
    move.l  %d0,O_LONG(%a2)
    movea.l O_SNAME(%a2),%a0
    bsr.s   mf_rep
    move.l  %d0,O_SHORT(%a2)
    lea     ROWSIZE(%a2),%a2
    subq.l  #1,%d4
    bne.s   10b
8:  moveq   #-1,%d0
    move.b  %d0,mf_ready
    movem.l (%sp),%d0-%d7/%a0-%a6
    lea     60(%sp),%sp
9:  rts

| a0 = C string, a3 = destination; builds {length, capacity, refcount -1, upper-cased chars, 0} at a3, returns the
| address of the chars in d0 and advances a3 to the next 4-byte boundary. Clobbers d1, d2, a0, a1.
mf_rep:
    moveq   #0,%d1
    lea     12(%a3),%a1
1:  move.b  (%a0)+,%d2
    beq.s   2f
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

| ---- rank: the std::map insert gets the (type, position) pairs of mf_rank_buf instead of the caller's array.
| Site: the `jsr` of the insert; stack: 0 map, 4 begin, 8 end. d0 free.
    .globl mf_rank
mf_rank:
    bsr.w   mf_ensure
    move.l  #mf_rank_buf,%d0
    move.l  %d0,4(%sp)
    move.l  #_mf_first,%d0
    addi.l  #_mf_entries,%d0        | the listed machines (gap rows are not in mf_rank_buf)
    lsl.l   #3,%d0
    addi.l  #mf_rank_buf,%d0
    move.l  %d0,8(%sp)
    jmp     _mf_t_rank

| mf_row: d0 = index k (< count) -> a0 = &machines.rows[k]. Preserves every other register (not the flags).
mf_row:
    move.l  %d1,-(%sp)
    move.l  #ROWSIZE,%d1
    muls.l  %d0,%d1
    addi.l  #_mf_rows,%d1
    movea.l %d1,%a0
    move.l  (%sp)+,%d1
    rts

| ---- clone tests. `type == SLICE` must also be true for an added type whose clone_of is SLICE. Each site is the
| whole stock test (a prefix, `cmp`, a conditional branch); the hook replaces it (displaced = "drop"): the routine
| runs the stock prefix and compare, and takes the branch itself: equal -> `eq`, otherwise it asks whether the type
| register holds an added type whose clone_of is SLICE (then equal) and goes to `eq` or `neq`. The two destinations
| are the end of the site and the branch target (an anchor `cf.machines.clone.N.alt`). Registers and the stack come
| out as the stock test leaves them (d0-d2 and a0 are saved around the lookup); the flags are not preserved on
| the paths after the branch (every site was read: none reads them).
    .macro CLONE_BODY treg, eq, neq
    beq.w   6f
    cmpi.l  #_mf_first,%d\treg
    bcc.s   4f
    jmp     \neq                        | a stock type other than SLICE: the stock answer
4:
    lea     -16(%sp),%sp
    movem.l %d0-%d2/%a0,(%sp)
    .if \treg < 3
    move.l  (\treg * 4,%sp),%d0
    .else
    move.l  %d\treg,%d0
    .endif
    subi.l  #_mf_first,%d0
    cmpi.l  #_mf_count,%d0
    bcc.s   5f
    bsr.w   mf_row
    move.l  O_CLONE(%a0),%d1
    cmpi.l  #SLICE,%d1
    bne.s   5f
    movem.l (%sp),%d0-%d2/%a0
    lea     16(%sp),%sp
6:  jmp     \eq
5:  movem.l (%sp),%d0-%d2/%a0
    lea     16(%sp),%sp
    jmp     \neq
    .endm

| clone.0: moveq #6,D1; cmp.l D0,D1; beq.w T (equal -> T)
    .globl mf_clone0
mf_clone0:
    moveq   #6,%d1
    cmp.l   %d0,%d1
    CLONE_BODY 0, _mf_c0a, _mf_c0 + 8
| clone.1: moveq #6,D1; cmp.l D0,D1; bne.b U
    .globl mf_clone1
mf_clone1:
    moveq   #6,%d1
    cmp.l   %d0,%d1
    CLONE_BODY 0, _mf_c1 + 6, _mf_c1a
| clone.2: moveq #6,D1; cmp.l D0,D1; bne.w U
    .globl mf_clone2
mf_clone2:
    moveq   #6,%d1
    cmp.l   %d0,%d1
    CLONE_BODY 0, _mf_c2 + 8, _mf_c2a
| clone.3: addq.l #4,SP; cmp.l D0,D6; bne.b U (d6 holds SLICE)
    .globl mf_clone3
mf_clone3:
    addq.l  #4,%sp
    cmp.l   %d0,%d6
    CLONE_BODY 0, _mf_c3 + 6, _mf_c3a
| clone.4: moveq #6,D0; cmp.l D2,D0; bne.b U (the type is in d2)
    .globl mf_clone4
mf_clone4:
    moveq   #6,%d0
    cmp.l   %d2,%d0
    CLONE_BODY 2, _mf_c4 + 6, _mf_c4a
| clone.6: moveq #6,D1; mvs.b (0x990,A0),D0; cmp.l D0,D1; bne.w U
    .globl mf_clone6
mf_clone6:
    moveq   #6,%d1
    mvs.b   0x990(%a0),%d0
    cmp.l   %d0,%d1
    CLONE_BODY 0, _mf_c6 + 12, _mf_c6a

| clone.5 is the masked test `(type & ~2) == 4` (types 4 and 6): d0 becomes the clone_of of an added type before the
| stock mask. Site: the first four instructions of the test (d1 is set by them).
    .globl mf_clone5
mf_clone5:
    lea     -8(%sp),%sp
    movem.l %d1/%a0,(%sp)
    move.l  %d0,%d1
    subi.l  #_mf_first,%d0
    cmpi.l  #_mf_count,%d0
    bcc.s   1f
    bsr.w   mf_row
    move.l  O_CLONE(%a0),%d1
1:  move.l  %d1,%d0
    movem.l (%sp),%d1/%a0
    lea     8(%sp),%sp
    jmp     _mf_t_clone5

| ---- type argument of the parameter mapping: `FUN(index, type, filter)` answers the parameter code of a (mirror
| index 25..34, machine type) pair. For an added type: the slots with a descriptor field answer with the entry's
| field, the two without (29, 30: MANUAL SLICE's Sample Bank / Slot rows, which the stock map holds for its owner
| but no descriptor lists) answer as the clone does (the stack argument is replaced by the clone's type, then the
| stock code runs); every other index runs the stock code. Site: `move.l 0x10(sp),D0; movea.l 0x14(sp),A1` in the
| middle of the function; a0 = index, d1 live; d0, a1 are reloaded, d2 and a2 are saved by the function (they are
| saved here too).
    .globl mf_typearg
mf_typearg:
    move.l  0x10(%sp),%d0
    subi.l  #_mf_first,%d0
    cmpi.l  #_mf_count,%d0
    bcc.s   9f
    lea     -16(%sp),%sp
    movem.l %d1-%d2/%a0/%a2,(%sp)
    move.l  %a0,%d2
    subi.l  #25,%d2
    cmpi.l  #9,%d2
    bhi.s   7f
    lsl.l   #2,%d2
    lea     mf_slotmap,%a2
    move.l  (%a2,%d2.l),%d2
    bmi.s   6f
    bsr.w   mf_row
    move.l  (%a0,%d2.l),%d0
    movem.l (%sp),%d1-%d2/%a0/%a2
    lea     16(%sp),%sp
    jmp     _mf_x_typearg
6:  bsr.w   mf_row
    move.l  O_CLONE(%a0),%d0
    movem.l (%sp),%d1-%d2/%a0/%a2
    lea     16(%sp),%sp
    move.l  %d0,0x10(%sp)
    jmp     _mf_t_typearg
7:  movem.l (%sp),%d1-%d2/%a0/%a2
    lea     16(%sp),%sp
9:  jmp     _mf_t_typearg

| descriptor field offset of mirror index 25 .. 34 (-1: none)
    .section .rodata
    .balign 4
mf_slotmap:
    .long   8, 12, 16, 20, -1, -1, 24, 28, 32, 36

    .bss
    .balign 4
    .globl mf_list_buf, mf_rank_buf
mf_ready:    .space 4
mf_list_buf: .space 4 * 64
mf_rank_buf: .space 8 * 64
mf_strbuf:   .space REPSIZE * 2 * MAXN
