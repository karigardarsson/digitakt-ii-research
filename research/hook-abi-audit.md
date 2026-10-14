# Digitakt II OS 1.16: ColdFire hook ABI audit

Scope: hash-gated stock MAIN OS in scratch-RAM CFV4E Unicorn; no patched
firmware, live frame tick, note-on, or device. The executable checks and full
register/write trace are `hook_abi_probe.py` and
`out/dt2-dv-offline/hook-abi-probe.json`. Stock `.syx` SHA-256 is
`278541e466edcd77d6b3e018a91fb90185932d3c7de224dd3e68294dddf3a9ec`;
extracted MAIN OS SHA-256 is
`57bb4dfa8df07d846adc72fdb4fb0d3cd3c5680c524bf498338460207e008e7d`.

## Byte-verified execution map

| PC | Stock bytes | Meaning / evidence |
| --- | --- | --- |
| `0x4002e912` | `2f06 2f39 402b62bc 2f0a 4eb9 40139342` | Caller pushes final D6 track mask, global rate, value pointer A2, then calls tick; static bytes. |
| `0x40139342` | `4fef ffc0 48d7 7cfc` | Allocates `0x40` local bytes and saves registers; static bytes. |
| `0x40139360` | `242f 004c ... 2f42 0034 c1af 0034` | Reads full mask at frame `+0x4c`; saves one track bit at `+0x34`; static bytes and synthetic register/stack trace. |
| `0x401393bc` | `772b 0002 6c04 4283 6008 7e06 be83 6c02 7606` | Reads signed WAVE byte at `+2(A3)`, clamps below 0 to 0 and above 6 to 6. Upper clamp is `0x401393c6..ce`; byte and WAVE 7/8 synthetic tests. |
| `0x401393ee` | `732b 0006 2405` | Reads MODE byte and copies D5 for phase math; static bytes. |
| `0x401393f4` | `206a ffdc d488` | Reads previous phase at `-0x24(A2)` and adds it to D2, which was loaded from D5; static plus synthetic execution. |
| `0x4013948a` | `4aaf 0034 676c` | Tests saved track mask bit in mode/reset path; static plus synthetic reset. |
| `0x401394fe` | `2542 ffdc` | Stores post-wrap phase; observed stock write. |
| `0x40139528` | `7e06 be83 6728` | Branches WAVE 6 to RAND, else continues to WAVE 0–5 table; static plus executed paths. |
| `0x4013953c` | `43f9 4022231c 2271 3c00 2f02 2f41 0034 4e91` | Loads 0–5 function table and calls chosen generator; byte verified. |
| `0x40139556` | `4a85 6d08 2002 90aa fff0 6006` | RAND-specific branch and phase/target path; executed. |
| `0x40138fb8` | `2239 402b62cc 2001 d0b9 402b62c8 23c1 402b62c8 23c0 402b62cc 4e75` | Shared RNG update `(A,B)->(B,A+B mod 2^32)`; byte verified and both writes observed. |

At the WAVE upper clamp (`0x401393c6`), the observed track-0/LFO-1
registers are D3=raw WAVE, D5=unscaled signed SPD difference (`0x2000` in
the positive test), A2=`0x44e66e9c` (state+`0x24`), A3=`0x1000002a`
(parameter row), A4=`0x44e66e7c` (output at state+4), A5=`0x10000022`
(value row+`0x22`), and D6=`0x44e66eec` (state cursor, **not** mask).
The parameters relative to A3 are SPD `-6`, MULT `-4`, FADE `-2`, DEST `0`,
WAVE `+2`, SPH `+4`, MODE `+6`, DEP `+8`. This layout was exercised on a
synthetic track-0/LFO-1 call; the common per-track loop gives the other LFO
rows but a native hook must check them too.

At dispatch (`0x40139528`) in the same case: D2=`0x07080000` post-wrap
phase, D5=`0x07080000` signed pre-wrap increment, D3=clamped WAVE, D4=MODE,
A2/A3/A4/A5 still point as above, and D6 remains the state cursor. SP is
`0x10017fc0`. Relative to this SP: `+0x2c` saved SPH, `+0x34` track mask
bit, `+0x38` track index, `+0x3c` LFO countdown, `+0x40` return PC,
`+0x44` value pointer, `+0x48` rate, `+0x4c` full mask. WAVE 0–5 reuses
`+0x34` after dispatch; capture the mask there or earlier. The trace records
all D0–D7/A0–A6, SR, and these stack fields at each watch PC. This is an
observed ABI for this invocation, not a proven free-register list. A
trampoline must preserve all live registers, CCR/SR, stack and relevant EMAC
state until liveness and displacement are checked across every successor.

## Native phase increment and limitation

D5 is the native signed phase increment at `0x401393f2` and remains present
at `0x40139528`, after the phase store. The call uses signed SPD relative to
`0x4000`: `0x40139384..90` reads SPD, subtracts `0x4000`, then doubles it;
`0x4013939a..b8` clamps/selects MULT and chooses the caller rate or fixed
`14400`; `0x401393ce` is `muls.l D1,D5`; `0x401393e2` is
`asr.l D7,D5` with shift count `11 - adjusted MULT`. These instruction
bytes and register observations establish the phase-delta source. In bounded
stock calls:

| Case | D5 | Stored phase | Evidence |
| --- | --- | --- | --- |
| Forward, SPD `0x5000`, rate `14400` | `0x07080000` | `0x07080000` | function-level |
| Reverse from phase 0, SPD `0x3000` | `0xf8f80000` (-`0x07080000`) | `0x4b5dc000` | function-level |
| Forward from `P-0x100000` | `0x07080000` | `0x06f80000` | function-level |
| SPD `0x7fff`, rate `14400` | `0x1c1f8f80` | `0x1c1f8f80` | function-level multi-step |
| SPD `0x7fff`, rate `50000` | `0x61a67960` | `0x0f40b960` | **synthetic** rate, one full-cycle crossing |

`P=0x5265c000`. The V1 adapter can consume D5, D2 and the track mask
without guessing direction or turns from endpoints. Whether the real frame
rate reaches a full-cycle delta is unknown. The stock phase code has been
observed for one wrap; do not assume arbitrary multi-cycle deltas are handled
by the native tick. On a reset tick, D5 remains available but D2 can be
forced to zero; the DV-T adapter must explicitly apply its track mask bit.

## WAVE 6 isolated stock regression

Four repeated WAVE 6 ticks with seed `A=0x13579bdf`, `B=0x2468ace0`,
SPD `0x5000`, MULT 11, rate 14400 produced phases `0x07080000`,
`0x0e100000`, `0x15180000`, `0x1c200000`, identical to WAVE 0 control.
The state+4 outputs were `0x0df0122e`, `0x170a3d66`, `0x24fa4f96`,
`0x3c048cfc`; target state+8 was `0x37c048bf`, `0x5c28f59f`,
`0x93e93e5e`, `0xf01233fd`. State+`0xc` slew and state+`0x14` RAND
phase writes, as well as both shared RNG writes, were checked on each tick.
Same-seed rerun matched exactly. Reverse WAVE 6 phase progression was
`0x4b5dc000`, `0x4455c000`, `0x3d4dc000`, `0x3645c000`.
WAVE 0 did not write shared RNG. Raw WAVE 7 and 8 each clamped to D3=6
and took RAND in stock, demonstrating why a dispatch-only hook is insufficient.
MODE 1 plus synthetic mask 1 reset track-0/LFO-1 phase to zero. These are
isolated stock function calls, **not** natural frame or note-on proof.

## Integration decision

Candidate paired hook sites are `0x401393c6` (the 8 complete bytes
`7e06 be83 6c02 7606`) and `0x40139528` (the 6 complete bytes
`7e06 be83 6728`). The first must preserve the exact 0–6 stock clamp while
retaining 7/8 distinctly; the second must replay the displaced compare and
branch for stock WAVE 0–6, then route 7/8 to a DV handler. The handler would
receive D5 signed delta, D2 stored phase, `SP+0x34` mask bit, and the
state/parameter pointers. Its return into the stock apply path is **not yet
verified**. Earlier WAVE-dependent branches, register/EMAC liveness,
trampoline reach, owned DV RAM, and both original branch successors remain
implementation blockers. The six-entry waveform table cannot be enlarged by
appending pointers because its WAVE-6 adjacency is RAND slew data.

Next executable step: in a **scratch RAM copy only**, test the paired
trampolines with WAVE 7/8 and a stub returning a known Q31 value. Re-run the
stock 0–6 differential (including the four RAND ticks and RNG writes), then
check every register, stack byte, phase, output/apply write, and return PC.
Resolve the DV handler rejoin and RAM ownership before implementing the real
handler or assembling a firmware image. Existing UI bounds, save/load, live
note-on/frame tick and hardware remain separate gates for one future build
and planned flash.
