| Original native SLICE descriptor initialization. AGPL-3.0-or-later.
| Replaces only the boot-time clear of its existing C field. No SRC callback,
| persistent state, waveform/editor hook or parameter allocation is added.
    .text
    .globl slice_cfade_init
slice_cfade_init:
    move.l %d0,-(%sp)
    move.l #250,%d0
    move.l %d0,0x4293ba78
    move.l (%sp)+,%d0
    rts
