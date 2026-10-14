# Digitakt II OS 1.16: native ColdFire DV-F/DV-T research prototype

This is a hash-gated, non-installable Unicorn guest-memory build. Stock `.syx`,
extracted MAIN OS and tracked digikit source were not changed. `native_dv.s`
is executable ColdFire code, not a host callback or fixed-Q31 stub. The full
deterministic report is `out/dt2-dv-offline/native-dv-probe.json`.

## Code and control flow

| Guest code | Bytes | Function |
| --- | ---: | --- |
| `native_dv.s` at `0x10004000` | 850 | Derives all 48 seeds from one global seed, initializes 32 Q31 slots per voice, advances cursor, refreshes/reorders, returns signed Q31. |
| `native_trampoline.s` at `0x10003100` | 72 | Routes WAVE 0–5 and RAND 6 to original successors; saves D1–D7/A0–A6, calls native generator for 7/8, restores registers, rejoins stock apply at `0x401395c2`. |
| Prior clamp trampoline at `0x10003000` | 32 | Replays stock 0–6 clamp and retains 7/8. |
| `native_gate.s` at `0x10003300` | 40 | Lets DV 7/8 pass a measured stock pre-dispatch gate; replays both stock branch outcomes for 0–6. |

The third gate is necessary: `0x40139520..26` tests a stock state flag and
D1, and in one MODE 3 fixture branched straight to `0x40139590` without
visiting the planned dispatch hook at `0x40139528`. An unmodified paired
hook cannot run a DV generator on that path. The new gate is patched only
in the emulator at `0x40139520` from stock bytes `4a12 6704 4a81 6768`
to `4ef9 10003300 4e71`; stock 0–6 still follows its original outcomes.
The clamp and dispatch guest patches remain at `0x401393c6` and
`0x40139528`. No patched firmware file was emitted.

## Guest state and ABI

Each voice uses a `0x98`-byte record: `+0` RNG, `+4` last stock phase,
`+8` logical step, `+12` cursor, `+16` init flag, `+20..+147` thirty-two
Q31 slots, and `+148` old-phase temporary. Forty-eight records use
7,296 bytes (`0x1c80`). One 32-bit global seed is at `0x11001ff0` in an
8,192-byte guest arena `0x11000000..0x11001fff`. The arena is explicitly
mapped after checking it does not overlap any existing Unicorn region.
Redzones between records and the seed, and after the seed, stayed unchanged.
All generator writes were confined to its records or its bounded call stack;
no generator write entered stock LFO state or shared RNG.

The native call consumes D2 post-wrap phase, D5 signed delta, D3 WAVE,
D4 MODE, A2 stock state pointer, A3 SPH parameter and A6 original tick
frame. `SP+0x34` supplies the per-track trigger bit. The dispatch trampoline
preserves D1–D7/A0–A6 and its original SP; D0 is the intended Q31 return.
CCR changes before `0x401395c2` are overwritten by stock `tst.b (A2)` at
`0x401395c6`. The generator uses no EMAC instructions. Guest-read ACC0–3,
ACCext01/23, MASK and MACSR matched at dispatch and apply rejoin for every
tested voice. WAVE 0–6 final registers, SR, stack, both stock LFO state
arrays, value rows, RNG and every write matched stock execution.

The exact phase-to-bin conversion uses a 15-entry threshold table derived
from `((phase * 0x636ba875) >> 32) << 2`, not an approximate division.
All boundaries `k * 0x05265c00 + 1` (k=1..15) and adjacent values were
tested in native code. The kernel counts forward/reverse crossings using
signed D5 and checks the resulting wrapped phase against stock D2. The
existing V1 policy advances the random cursor for every crossed boundary
in either direction; the musical intent of reverse DV remains unverified.

## Results and limits

| Test | Result | Evidence level |
| --- | --- | --- |
| Native phase bins including 15 exact boundaries | 96/96 | SYNTHETIC UNICORN |
| Stock WAVE 0–6, including RAND and MODE 3/4 gate | 70/70 byte-for-byte differential | SYNTHETIC UNICORN |
| Python V1 vs native RNG, all 32 slots, cursor, logical step, phase and Q31 for 48 voices over normal, trigger and positive full-cycle runs | 432/432 voice-ticks | HOST oracle vs SYNTHETIC UNICORN |
| DV-F and DV-T, MODE 2/4/8/16/32, SPH 0/64/127, forward/reverse, wrap, multi-step, track 0 and track 7 trigger masks | Included in 432 comparisons | SYNTHETIC UNICORN |
| Native DEP/DEST apply controls | Q31 stayed `0x31000000`; DEP change wrote 0 vs 32512 at `0x10000022`; DEST change wrote at `0x10000024` | SYNTHETIC UNICORN |
| Existing V1 and stock suites | 16/16 host, 30/30 stock waveform calls; prior 42/12 stub baseline still passed | HOST / SYNTHETIC UNICORN |

The positive full-cycle fixture uses synthetic rate 50000, SPD `0x7fff`;
all 48 voices matched the oracle over two ticks. A bounded **reverse**
full-cycle probe with synthetic rate 50000 and SPD 0 produced D5
`-1638400000` and stock phase `0xf0bdc000` on track 0/LFO 1, outside
`[0,0x5265c000)`. The stock tick makes one negative wrap correction and
cannot represent that input as a valid phase. The test records
`BLOCKED_STOCK_PHASE`; it is not counted as a native DV pass. Whether the
natural frame rate can reach this condition remains unknown. Do not extend
the native kernel by guessing a second wrap while the stock state is invalid.

## Next integration gate

The 8 KiB arena is owned only by this Unicorn fixture. Real DT2 RAM
ownership, lifetime, allocator/stack collision, and a safe physical code
location are unproved. The binary is linked to guest scratch addresses and
cannot be installed as-is. The next bounded test is to identify a real OS
1.16 RAM region of at least 8 KiB with an allocation/lifetime trace, then
repeat this exact matrix after relocating the generator and gate into a
verified in-memory code-cave copy. A natural frame tick and note record must
then show the actual trigger bit; UI bounds and save/load remain separate
gates before one integrated CFW build and one planned flash.

Evidence: instruction bytes and gate/control-flow are **SOURCE/STATIC**;
all execution results above are **SYNTHETIC UNICORN**; **NATURAL EMULATED**
frame/note-on and **HARDWARE** behavior are untested.
