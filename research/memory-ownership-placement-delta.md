# OS 1.16 DV memory ownership and code placement: delta

**Gate: BLOCKED for native DV-state relocation.** The stock allocator can
own an 8 KiB block across a bounded emulated window, but no one-time startup
call and persistent pointer handoff to the DV kernel have been executed.
The native DV source, trampoline, stock image and tracked digikit source
remain unchanged.

## Allocator map and ABI

The extracted MAIN OS SHA-256 is
`57bb4dfa8df07d846adc72fdb4fb0d3cd3c5680c524bf498338460207e008e7d`.
Byte-verified `0x4011e674` accepts a 32-bit allocation size at `4(A7)`,
returns the pointer in D0, uses the mutex at `0x44e08654`, and checks lazy
initialization at `0x402b406c`. Free is `0x4011e87a`, pointer at `4(A7)`;
its three `illegal` checks enforce heap lower/upper bounds and 16-byte
alignment. These are **SOURCE/STATIC** facts corroborated by the synthetic
call below, not OS 1.15C addresses.

The metadata at `0x402b406c..0x402b4087` gives: init flag, pool base
`0x44f39c40`, bitmap base `0x44dc8654`, pool end `0x46f39c40`, max order
`0x15`, `0x00200000` units of 16 bytes, and live allocation count. The
pool spans **32 MiB**, not 2 MiB. Stock image init flag is 0; boot60M,
boot400M and post-intro 80M snapshots show it as 1. The pool and bitmap
fall inside the byte-verified reset clear
`0x40312000..0x47e28470`; the metadata words are below that start and
survive as static initialized data. The allocator builds the free tree on
first call when the flag is zero. **SOURCE/STATIC + HOST checkpoints.**

## Bounded allocation and runtime

From the manifest-matched post-intro 80M checkpoint, exactly one synthetic
`malloc(8192)` returned `0x45057c40`. The pointer is 16-byte aligned,
inside the 32 MiB pool, and aligned to an 8 KiB block relative to its base.
The live count changed `0x2cf3 -> 0x2cf4` and one bitmap byte changed.
Guest CPU context was restored before the OS continued. Over **10,108,801**
more emulated instructions: the 8 KiB canary and adjacent header were
unchanged; there were 0 writes to the block, 0 frees of its pointer,
104 natural allocator calls and 101 frees of other pointers. The final
live count was `0x2cf7`. This proves bounded allocator ownership in the
**SYNTHETIC UNICORN + EMULATED CONTINUATION** fixture, not a natural
firmware allocation or full musical lifetime.

Five task-create stack arguments in the boot400M snapshot cover
`0x409636f0..0x409646f0`, `0x40964e10..0x40965610`,
`0x42940aac..0x42944aac`, `0x42944b00..0x42948b00`, and
`0x43149558..0x4314d558`; none overlaps the heap pool. The checkpoint
CPU SP was `0x43176cb4`, also outside. Later task stacks were not fully
bounded by these records. During continuation, 219 eDMA TCD writes
occurred; channel 59 SADDR was set to `0x45077ab0`, **inside the heap
pool but outside the allocated DV block**. No recorded TCD write targeted
the DV block, and its canary stayed intact. This does not exclude future
DMA aliases or longer transfer ranges. **EMULATED, bounded.**

## Reservation and executable code

Allocator-backed lifetime (route A) is viable in the bounded test, but
still needs a boot-time one-shot call and a persistent pointer slot. A
static 8 KiB carve-out (route B) has no established firmware reservation:
the earlier `0x4031c000..0x4031e000` candidate is merely an unreferenced,
reset-cleared BSS gap. Trimming the 32 MiB buddy pool by 8 KiB would also
break the current power-of-two size/max-order relation without redesigning
its metadata. Route B is not currently the simpler proven path.

`0x4031be59..0x4031ff60` lies inside reset zeroing and **cannot hold
flashed executable code** through startup. The separate
`0x403117c4..0x40312000` cave ends exactly at the clear start; its 2,108
bytes match the stock image in boot60M, boot400M and post-intro 80M
checkpoints. Prior cold-boot canary evidence in `docs/findings/01-container-and-patching.md`
also supports survival. The current clamp/dispatch/gate/generator binaries
total 994 bytes, so their size fits there with 1,114 bytes left before
alignment and a pointer/boot stub. A post-clear copy is unnecessary **if**
they are linked into that persistent cave; relocation, executable rejoin,
cache behavior and collision with a future boot stub remain untested.

The priority-6 task entry at `0x40133646` is a byte-verified candidate for
one-time bootstrap after heap initialization: that task ran in the 80M
post-400M boot window, the heap was already initialized, and there were
0 natural hits on LFO initializers `0x40138f10/0x40138f64` or tick
`0x40139342` in that window. This is **not** a proven hook ABI or proof
that the full LFO lifetime follows it. No native state relocation or
96/70/432 regression rerun was warranted; the core was untouched.

## Next discriminating test

In guest memory only, trampoline the priority-6 entry once: preserve its
`link`/`movem` prologue and live registers, call byte-verified
`malloc(8192)`, save the pointer in a distinct cave-owned word, then
rejoin stock. Run a bounded stock-vs-hooked startup comparison through
the next 20M instructions with allocator count/bitmap, pointer canary,
stack, EMAC, TCD and PC watches. If that passes, use the retained pointer
for repeated function-level LFO calls before moving the known-good DV
kernel. This remains an offline preflight toward one integrated build and
one planned flash; **HARDWARE is untested**.

Machine-readable data: `out/dt2-dv-offline/allocator-ownership.json` and
`allocator-runtime-10M.json`; static/runtime logs alongside them.
