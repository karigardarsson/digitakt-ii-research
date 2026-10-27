| machines/plaits on the Digitakt II (1.17): SRC+YES (randomize page) picks MODEL among the models that exist.
|
| core/params calls this through MODEL's `randomize` field (module.toml): the page randomizer finds the added codes in the
| machine's descriptor, and core/params' hook on ParameterSet::vfunc_7 asks the row for a value (src/cf/dt2/randomize.s).
|
|     uint32_t plaits_model_rand(uint32_t rnd, uint32_t min, uint32_t max)
|
| cdecl: rnd (0 .. 0x7fff, the firmware's generator) at 4(sp), min at 8(sp), max at 12(sp) (unused: the table is the
| range); d0/d1/a0/a1 scratch, the rest preserved; the caller pops. Returns the raw 8.8 value, a number from the table
| below times 0x100, uniform over the table (no gaps: a model that is not built never appears).
|
| The table is the models the build has: every number of machines/plaits-48k's engines. All 25 models (0 to 24, DX7-1 to DX7-4 at 18 to 20 and 24)
| are built. A model that is dropped or added is a change to this table only (the count is taken from the table).

    .text
    .globl plaits_model_rand
plaits_model_rand:
    move.l  4(%sp),%d0
    moveq   #(pr_end - pr_models),%d1
    mulu.l  %d1,%d0
    lsr.l   #8,%d0
    lsr.l   #7,%d0              | (rnd * n) >> 15 = 0 .. n - 1
    lea     pr_models,%a0
    moveq   #0,%d1
    move.b  (%a0,%d0.l),%d1
    lsl.l   #8,%d1
    move.l  %d1,%d0
    rts

    .section .rodata
pr_models:
    .byte   0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17
    .byte   18, 19, 20, 21, 22, 23, 24
pr_end:
