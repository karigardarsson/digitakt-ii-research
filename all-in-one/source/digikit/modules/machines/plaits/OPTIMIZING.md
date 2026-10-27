# Optimising PLAITS on the SHARC+: shared learnings

Every agent doing performance work on the PLAITS DSP code **reads this file first and appends to it before finishing**,
whether the attempt succeeded or failed. It is how agents teach each other: one technique, one measured result, one
lesson per entry. Keep entries short and evidence-backed. When an entry turns out wrong, correct it in place with a
**[C]** note rather than deleting it.

Goal (owner): 16 simultaneous PLAITS voices at reference quality by using the DSP fully. Order: RINGS first (furthest
to go: about 125k instructions per block against a target of about 16k cycles per voice), then the other engines.

**Stock DSP code is in scope too** (owner), for blatant easy wins only: the stock firmware takes about 46 % of each
block, so every cycle saved there is a cycle for PLAITS voices. A stock change must leave the stock audio bit-identical:
the DT2 scenario oracles and PCM pins (`mise run verify` steps dt2-check / dt2-tier-*) pass unchanged, alongside the
rules below. Profile the stock renderer before choosing a target, and record stock wins and failures here too.

## Hard rules (never break)

1. **The output stays bit-identical.** The existing host oracle, compiled-equivalence and digest tests must pass
   unchanged. Never re-record a digest to make an optimisation pass. An approximation is out of scope unless the owner
   approves it explicitly.
2. **Encodings need a stock precedent.** Every instruction form must pass `pk_encoding_gate` (no
   `--no-encoding-gate`). The emulator has accepted encodings that stalled the real DSP. Indirect jumps and calls must
   be exact stock words.
3. **Restore global state on every exit.** That covers MODE1 (SIMD `PEYEN`, rounding, saturation), MODE2, loop and
   status stacks, L/B registers on circular buffers, and the circular C stack (L6/L7). Return the DSP exactly as the
   stock renderer handed it over. Test this on every exit path.
4. **Measure every change.** Run `pk_plaits_cpu --fuzz --model N` (same seed) before and after, plus one `--confirm`
   run per merged milestone. A change that doesn't measurably help is reverted.
5. **Profile before optimising.** Find where the cycles go (per function, instruction mix, hot loops) before changing
   code.

## Method

1. Profile: instruction counts per function and per loop on the isolated core, and the mix of instruction types
   (loads/stores, branches, calls, compute).
   `uv run python -m tools.patchkit.pk_dsp_waste --model N` does this and ranks the easy wins by estimated cycles saved
   (`--stock` for the stock renderer; JSON in `out/runs/dsp-waste/`; detectors and their assumptions in
   `tools/patchkit/dsp_waste.py`). Its figures are estimates: confirm each with the paired fuzz before and after.
2. Remove overhead: per-sample calls, spills, recomputed addresses, library calls, conversions.
3. Restructure: block-at-a-time loops, state in registers across the block, hardware loops (LCNTR/DO).
4. Coalesce: SIMD pairs of independent work (PEx/PEy), parallel DM/PM moves folded into compute.
5. Record the result here.

## Industry practice for this platform

Distilled from public sources: the SHARC+ Core Programming Reference and the ADSP-2156x Hardware Reference
(docs/sharc/SOURCES.md; chapter-level references, page numbers not re-checked here) and the general compiler
literature. Practical order at the end.

- **Profile-guided optimisation.** Measure first, change only what is hot (Pettis and Hansen 1990 for profile-based
  layout; the gprof paper, Graham et al. 1982; Ball and Larus 1994 for cheap path profiles). Here: per-pc counts from the
  concrete core (`pk_dsp_waste`), not wall-clock. The fuzz worst block is the design point, because the budget is the
  worst block, not the mean.
- **Peephole optimisation** (McKeeman 1965; Davidson and Fraser 1980). Slide a small window over straight-line code and
  rewrite known-wasteful patterns: reload of a value still in a register, store of an unchanged value, copy then
  overwrite, recomputed expression. Safe only inside a basic block with honest dependence checks (registers, flags,
  memory). Compiled C on this core is full of them (spill traffic is a third of the RINGS block).
- **Multifunction instructions.** One SHARC+ instruction can issue a compute plus a DM and/or PM transfer
  (compute + data move, compute + dual move, compute + register move). A standalone load or store next to an independent
  compute is a lost cycle. The immediate-offset move form has a short signed offset field (6 bits in the compute form),
  so a frame slot far from the base needs a second base register close to it.
- **Circular buffers and DAG addressing.** The DAGs have I (index), M (modifier), L (length) and B (base) registers;
  a post-modify walks an array for free and L/B wrap it, so address arithmetic (`idx << k`, `base + that`, move to I)
  disappears from per-sample loops. Delay lines and ring buffers want L/B, not modulo code. MODE1 CBUFEN must be on and
  every L/B left as found (hard rule 3).
- **Zero-overhead hardware loops** (`DO ... UNTIL LCE`, LCNTR). A software counted loop pays a decrement, a branch and
  the taken-branch stall every iteration; the hardware loop pays none. Loop nest depth is limited by the loop stack.
  Unroll only to expose parallel work (to fill load-use gaps, pair loads, form SIMD), not to save loop overhead, which
  is already zero.
- **Delayed branches.** A delayed branch executes the two following instructions; filling them with independent work
  removes the taken-branch stall. Conditional branches only take instructions that do not set the flags they test.
- **Software pipelining and modulo scheduling** (Lam 1988; Rau 1994). Overlap iteration i's loads and multiplies with
  iteration i-1's adds and stores so every slot of a short loop body is used; the stock firmware's 30-trip DO loops are
  written this way. Needs registers for the in-flight values: register pressure is the limit here (16 data registers).
- **SIMD pairing.** PEx and PEy run the same instruction on R and S registers (complementary pairs); long-word loads fetch two
  words. It pays when two independent streams do the same operation (two biquads, two channels, the four modes of one
  resonator). Cost: data layout, the SIMD mode bit set and restored on every exit, and exactly equal results per lane.
- **Superoptimisation** (Massalin 1987; Denali, Joshi et al. 2002; Bansal and Aiken 2006; STOKE, Schkufza et al. 2013;
  Souper, Sasnauskas et al. 2017). Search for the cheapest instruction sequence equivalent to a given one. How we could do
  it here: take hot windows of 3 to 6 instructions from `pk_dsp_waste` (a spill-heavy address computation, a copy
  chain); enumerate or randomly mutate candidates (STOKE style) drawn only from instruction forms in
  `tools/patchkit/encoding_proven.toml` (so `pk_encoding_gate` passes); cost = our cycle model; reject by running the
  original and the candidate on a few thousand random register and memory states on the concrete core
  (`tools/sharc_dev/sharc_run.py`), comparing every register, flag, mode bit and memory word that can be live; confirm
  survivors with `tools/sharc_dev/sharc_trace.py` run symbolically from the same unknown inputs on both sequences (equal
  expressions prove it; unequal is not a proof of difference). Floating point: no reassociation, results must match bit
  for bit (hard rule 1), so the search space is moves, addressing and scheduling, not algebra.

What to try first, in order of risk:
1. Pure deletions: stores of unchanged values, reloads of live registers, self copies, dead definitions (bit-identical by
   construction; `redundant_store`, `spill_reload`, `copy_removal`).
2. Fill delay slots and fold moves into computes (`fill_delay_slots`, `fold_transfer`); a second frame pointer unlocks the
   far ones (`fold_transfer_far`).
3. Counted software loops to DO loops (`sw_loop_to_hw`), small leaf calls inlined (`call_inline`).
4. Rebuild per-element addressing as post-modified pointers or circular buffers (`address_arith`): the big one in
   RINGS, but it changes register allocation, so re-run the full fuzz and every exit-path check.
5. Registers for loop-invariant stack slots (`invariant_stack_reload`), SIMD and multifunction pairs: a restructuring of
   the loop, plan it per engine.
6. Superoptimise the few windows left that the detectors cannot name.

## Learnings

Template, one entry per attempt:

```
### <date> <engine/function>: <technique>, <WIN|FAIL|NEUTRAL>
- Before → after: <instructions/block, fuzz seed>, <% change>
- What: <one or two lines>
- Why it worked or failed: <the lesson>
- Reuse: <where else this applies / the helper or pattern to use>
- Evidence: <commit, test, run dir>
```

### 2026-10-19 RINGS (model 12): pk_dsp_waste profile of the fuzz-worst block, NEUTRAL (observation, no change)
- Before → after: 125,766 instructions (about 182.6k cycles, lower bound), seed-1 fuzz worst, block 2 → unchanged.
- What: 84 % of the cycles are `resonator_svf4_band_pass_add.`; its software loop (0xb9bf79) holds 74 % of the
  instructions and its inner DO loop (4 trips, 768 iterations of 111 instructions) 68 %. Mix: compute 38 %, stack
  transfers 34 %, register moves 16 %, other transfers 10 %, branches 1.5 %. Top five (cycles saved are estimates and
  overlap, do not add them; the high-confidence ones are about 3 % of the block, so peepholes alone do not reach the
  target):
  1. `address_arith` (low): 19.5k, 10.7 %: 11 element accesses per iteration build their address as `idx << 2`, add the
     base reloaded from the stack, move to I4, then load: 2 of the 4 instructions per access are removable with
     induction pointers / post-modify (or M-register pre-modify).
  2. `fold_transfer_far` (low): 13.0k, 7.1 %: 31 stack loads and stores sit next to independent computes but their
     offsets (I6 - 0x3c ...) exceed the 6-bit field of the compute + move form: a second frame pointer biased to the loop's slots folds them.
  3. `invariant_stack_reload` (low): 4.9k, 2.7 %: 15 slots are loaded every iteration and never stored in the DO loop (register pressure is why).
  4. `redundant_store` (high 3.8k at 5 sites, low 2.1k at 14 sites, 3.3 % together): the loop stores back a register just
     loaded from the same slot and unchanged: pure deletion, bit-identical, the safest first win.
  5. `fold_transfer` (medium, 1.0k) with `copy_removal` (0.8k) and `fill_delay_slots` (1.0k): about 2 % of small peepholes.
- Why it matters: the loop spills because every value lives in memory and addresses are recomputed: a register-allocation
  and addressing problem, not a missing instruction. The first change to try is item 4, then 1.
- Reuse: `uv run python -m tools.patchkit.pk_dsp_waste --model N` for any model; same fuzz JSON, so the same block before and after.
- Evidence: out/runs/dsp-waste/plaits-m12.json (not committed), tools/patchkit/dsp_waste.py.

### 2026-10-19 Stock voice renderer region: pk_dsp_waste on the stock 1.17 profile, NEUTRAL (observation, no change)
- Before → after: region = call/jump closure of FUN_1c642a (81 functions): 811M of 1,436M busy instructions (56.5 %) in the
  recorded stock run (out/runs/final-fix/cpu/stock-2500.tsv, engine profile, dynamic; no new emulator run), about 897M
  cycles, lower bound → unchanged.
- What: the stock hot loops are already software-pipelined 30-trip DO loops (FUN_1cdbb2 and FUN_1cb3d8: 14 % and 12 %
  of the region), so there is no blatant peephole win; everything below is low confidence unless marked. Top three:
  1. `invariant_stack_reload` FUN_1cdbb2 (low): 19.0M cycles, 2.1 %: stack slot -4 (and two more) reloaded each iteration of the 30-trip loop.
  2. `simd_pair` FUN_1cb3d8 (low): 13.9M, 1.6 %: independent `fabs` pairs in the hot loop (needs PEy data and a SIMD mode bracket).
  3. `call_inline` FUN_1c642a (low): 10.5M, 1.2 %: 17 calls of small helpers (FUN_1cc1dd, 18 instructions) inside its voice loop; the medium
     ones are `sw_loop_to_hw` (8 counter-only loops in FUN_1c642a, 211k back edges, 4.2M, 0.5 %) and a leaf call in FUN_1c4914 (1.3M).
  Peephole classes (fold_transfer, NOPs, dead definitions, delay slots) total about 5M, 0.6 %.
- Why it matters: stock is near its floor for peepholes; the budget lies in the voice loop's call overhead and counted
  loops, and every change must keep the stock audio bit-identical (dt2-check, dt2-tier-*).
- Reuse: `--stock [--profile none]` (static fallback, loop-nesting weights, coarse; use the dynamic profile).
- Evidence: out/runs/dsp-waste/stock.json (not committed).

### 2026-10-19 RINGS (model 12) resonator_svf4_band_pass_add: hand-written SIMD kernel (modulo-scheduled DO loop), WIN
- Before → after: 125,766 → 27,214 instructions per block (-78.4 %), seed-1 fuzz worst (24 sequences, same block:
  sequence 11 block 2); mode 0 50,751. Per call (16 samples): 8,701 → 472 (484 after fix round 1's DAG save/restore; block 27,404). Inner loop: 111 instructions per filter and
  sample (about 485 per sample with the outer C loop) → 16 per sample for all four filters. Confirmed CPU (emulator,
  1 track, worst knobs): 73.1 % → 52.9 % of the DSP block at the high bound (41.9 % nominal; stock alone 45.9 %).
- What: `lib/resonator_svf4.s`, generated by `lib/gen_resonator_svf4.py`, replaces the whole function (the coefficient
  set-up with tan_fast and both divisions, and the sample loop) on the DSP; `resonator_process` calls it for even sizes
  4..16, the C stays for the host and as the `-DPL_SVF4_C` reference. Filters (0, 1) and (2, 3) are SIMD pairs; states
  in registers; coefficients reloaded each sample by Type 4a immediate-offset loads folded into multifunction computes;
  one 16-instruction modulo schedule (three stages, prologue and epilogue windows emitted with only the live stages, so
  no access leaves the buffers) found by a z3 search (`lib/sched_resonator_svf4.py`) under the gate's constraints.
  `tests/plaits/test_resonator_kernel.py`: bit-exact against selcc's C on the concrete core (sizes 4..16, denormals,
  ±0, ±Inf, NaN, guard words, 40 run-on blocks) and the full register state after the call.
- Why it worked: the C was register-starved -O0 code (34 % stack traffic); the kernel needs no stack in the loop and
  issues a multiply, an ALU operation and a transfer in most instructions for two filters at once. Set-up in C (about
  700 instructions per call, mostly the two RECIPS divisions and the call to tan_fast) was the next hotspot once the loop
  was fast; porting it was cheap because selcc's operation order is explicit in its listing.
- Lessons and traps (all caught by the gate, the cross-check or the kernel test, none by luck):
  - The gate's shape keeps the multiplier result register of a multifunction compute (compute[15:12]): stock uses only
    some (form, operation, Rm) combinations (table in the scheduler). Plan register allocation against that table.
  - Selache: `compute(multifunction), Rn = Sn` / `Rn = M4` (Type 5a) are mis-assembled (the move dropped, a dual
    add/sub emitted); the cross-check catches the M-register case but **not** `Rn = Sn` (it treats Sn as Rn); only the
    gate (shape not in stock) and the kernel test caught it. Use 5a moves only beside single-function computes.
  - Selache: `In = MODIFY (In, k)(NW)` with spaces around `=` parses `MODIFY` as a symbol (an immediate load); write
    `In=MODIFY (In,k)(NW)`. `MODIFY (I7,-32)` without (NW) is a byte offset.
  - Selache under .NOCOMPRESS: the selcc return `JUMP (M14, I12) (DB)` becomes Type 9a with other registers and
    `RFRAME` a NOP; RECIPS has no 48-bit stock shape. Emit those under `.COMPRESS`.
  - The SIMD ABI tricks that make it work: `M4 = DM(..)` has no PEy half and `Rn = M4` writes Rn and Sn (broadcast
    s_in); `Rn = Sn` in SIMD is a lane swap (the serial sum crosses lanes); a SIMD store of the running sum writes a junk
    PEy word one higher, so it goes to a scratch row of size + 1 words and a SIMD pass adds it to out[] two at a time.
  - Exact `0.0f + p0` kept (a register holding 0 in both lanes): dropping it changes -0 results.
  - STKYy/ASTATy cannot be saved (Selache encodes their moves wrongly), so the kernel leaves them changed, and that is
    safe: no stock code reads them outside the interrupt dispatcher's and the task switcher's save/restore (0x1c0414,
    0x1c0432, 0xb8825b), and an interrupt cannot see SIMD state either: boot writes MMASK = 0xe0b003 (0x1c0f85), which
    includes PEYEN, so interrupt entry clears SIMD and RTI restores MODE1 (review of Task O1).
  - Do not rely on the caller's DAG set-up: the kernel saves M6, L0, L1, L4, sets M6 = 1 and L0 = L1 = L4 = 0 itself
    and restores them (stock leaves them so at the hook, but CBUFEN is on, so a non-zero L would wrap its pointers). The
    state test seeds the hook's MODE1/MODE2 and circular buffers over the very buffers it walks, then compares every
    register before and after; a missing set-up or restore fails it.
  - The gate's precedent set: `aligned = 1, confidence = 'confident'` rows of the stock sweep include ASCII data decoded
    as code (0x16b8xx), so `stock_shapes` admits a few shapes no real code has (e.g. every Type 1a dual move). Take
    precedents only from rows with `function_sw IS NOT NULL` (this kernel does: 39 shapes, all in functions; one,
    `F9 = F1*F6, F0 = F10-F15; M4 = DM(I0,M6)`, occurs only in an unreached function). A stricter gate is worth it.
  - A precedent covers the encoding, not the behaviour: stock never runs RECIPS or multifunction forms under PEYEN, and
    every stock SIMD data access is in L1. The kernel's SIMD loads and stores go to L2 (`_pl_stack`, the voice state,
    out[]): defined by the PRM, modelled by the emulator, unproven on silicon until H-opt1 passes. Assumes MODE1.RND32 = 1.
- Reuse: the generator pattern (schedule table → loop body + peeled prologue/epilogue windows by stage) and the
  scheduler model fit any per-sample recurrence over independent lanes (svf1, the other engines' filter banks, chord
  voices). The save/restore pattern: three DAG registers pushed in SISD, a 32-word area above I7, R/S pairs saved and
  restored with 16 SIMD stores/loads at I7 + 1 + 2p, `BIT SET/CLR MODE1 0x200000` each followed by a NOP (stock pattern).
  Next for RINGS (pk_dsp_waste after): plaits_post 18.8 %, resonator_process's own mode loop 17.7 %, the kernel 23.5 %
  (one call per batch: a six-batch call would save about 750 instructions), resonator_svf1_low_pass 6.7 %.
- Evidence: commits 64d65c4, 376415d (lane rings-opt); tests/plaits/test_resonator_kernel.py; out/runs/dsp-waste/plaits-m12.json;
  out/runs/plaits-cpu/fuzz/m12-seed1.json.

### 2026-10-20 RINGS resonator kernel on the unit (H-opt1): SIMD-mode loads and stores in L2 byte space, FAIL (DSP hang)
- Before → after: H-opt1 (`plaits-14models-…-rings-opt.syx`, ceb8dec7). VA worked; the first RINGS block hung the DSP,
  and all audio died. The emulator ran the same code bit-exactly.
- What differed from stock: every instruction word had a stock precedent, but the *access mode and region* did not.
  Every stock SIMD-mode data access goes to L1 normal-word space (0x24f0ac..0x2641e0, e.g. the per-pass PEYEN loop at
  0x1c1544 through I3). The kernel made SIMD loads and stores (the implicit PEy access at +4 bytes) through byte-space
  pointers into L2 (`_pl_stack`, the voice state, out[]). Stock makes SIMD stores to DDR at most, and no SIMD loads
  outside L1.
- Root cause, re-ranked with the SHARC+ Core Programming Reference (out/refs/sc58x-2158x-prm), still unconfirmed on
  the unit:
  1. **Top: DAG registers stored through their own DAG.** The kernel pushed M4, I0, I1, M6, L0, L1 and L4 with
     `DM(I7, M7) = ...` and restored them on exit. Types 3 and 15: "the Ureg may not be from the same DAG (that is,
     DAG1 or DAG2) as Ia/Mb or Ic/Md" (pp. 14-12, 14-15, 16-7, 16-10). Stock 1.17 loads such registers through their
     own DAG thousands of times (as does core/dsp-inline, on the unit since H1), but never stores one. If the stored
     words are wrong, the firmware gets back corrupted DAG state (its L registers set circular buffers, CBUFEN is on)
     right after the first RINGS call. That fits "VA fine, hang on switching to RINGS".
  2. SIMD loads and stores in L2 byte space: documented as supported. The implicit PEy access goes to explicit + 4
     bytes on a 4-byte boundary (p. 7-6; Table 6-10 p. 6-28). Every H-opt1 access was 4-byte aligned (float pointers,
     the I7 frame). The IICDI unaligned check (p. 6-32) needs MODE2.U64MAE (bit 21), which stock leaves clear (MODE2 =
     0x1). The data cache supports misaligned accesses (p. 8-8). Unproven on silicon, but no manual rule against it.
  3. Ruled out by the manual: PEYEN's one-cycle effect delay (p. 3-39: "a one cycle delay after PEYEN is set or
     cleared"; a NOP follows every switch, Table 4-42 p. 4-68); loops (the last 11 instructions must be 48-bit,
     p. 4-42, and they are); interrupts in SIMD (MMASK clears PEYEN on entry, p. 4-29); LCNTR from I12 (any ureg,
     p. 2-12).
  Probes (branch probe/simd-l2): `probe-samedag-store` (5e4fae1d) does one `DM(I4, M6) = M4` and no SIMD;
  `probe-simd-l2-load` (d0085564) and `-store` (855474a8) each do one SIMD access.
- Lesson: **a stock-precedent encoding is necessary but not sufficient.** The gate's shape masks register numbers, so
  it could not see a DAG1 register stored through DAG1. Operand rules from the manual, and how stock combines operands
  (which registers through which DAG; the access mode and region), must match too. The emulator
  models the PRM's implicit-access rule (Table 6-10) for every region, so it cannot catch this.
- Now enforced (tests/patchkit/test_encoding_gate.py):
  - `check_same_dag` refuses a DAG register stored through its own DAG; both kernels now push through R0.
  - `check_simd_memory` refuses any data transfer in patched code while PEYEN is set. This is a precaution, not a
    manual rule: lift it once `probe-simd-l2-*` passes on the unit. The shipping kernel is `resonator_svf4_sm.s`:
  SIMD compute, every load and store in SISD (PEYEN toggled per sample, as at stock 0x1c1544), bit-identical. Cost:
  27,404 → 31,472 instructions per block (seed 1; was 125,766 in C), 823 per call, 37 per sample.
- Also learned: the emulator wraps immediate-offset (pre-modify, no update) accesses circularly when L != 0, so a kernel
  must own the L register of every I it uses that way (L4 here), not only the post-modified ones.
- Reuse: for any SIMD kernel, keep the data in registers and move it in SISD. Prove a new access mode or region with a
  one-variable probe image before building on it.
- Evidence: lane rings-opt commits 1e32d04 (gate), the fallback commit, branch probe/simd-l2 e23212d; owner report
  2026-10-20.

### 2026-10-20 KICK, SNARE, HIHAT (13-15): SISD register-state kernels for every sample loop, WIN
- Before → after (fuzz worst per block, seed 1, same sequence counts 40/34/50): KICK 67,911 → 23,375 (-65.6 %), SNARE
  74,168 → 26,340 (-64.5 %), HIHAT 72,783 → 27,959 (-61.6 %). Per render call (16 samples, kernel test): KICK 27,893 →
  7,243, SNARE 31,491 → 8,646. Confirmed CPU (emulator, 1 track, fuzz-worst knobs, windows 100,500, high bound):
  KICK 62.3 → 54.8 %, SNARE 63.3 → 53.9 %, HIHAT 64.6 → 55.0 %. HIHAT on 2 tracks: 265,191 busy instructions per block
  (counters only, stock-mix 55.9 % high; the cycle run hit its 5400 s cap), about +26k per track; 4 tracks unfinished
  (3600 s cap, machine shared), extrapolated about 72-81 % high (0.21 % per 1k busy instructions from the 1-track pair is optimistic, see the O2 review); not measured.
- What: `lib/drums_sm.s`, generated by `lib/gen_drum_kernels.py` (helpers `div`, `rng_float`, `svf_mem`, `soft_clip`,
  `absf`, `diode`), replaces 10 loops: KICK analog / synthetic / overdrive, SNARE analog / synthetic, HIHAT square
  noise, square and saw oscillators (the saw adds RingModNoise's product in place, fusing three loops), and the hat
  tail (coloration band pass, noise mix, VCA, high pass fused per sample: each stage owns its state, so the per-value
  operations are the loops'). The C computes the per-block values and the loop's literals into a float `k[]` (literals
  from C, so selcc's own constants and divisions are used) and calls the kernel; `-DPL_DRUMS_C` keeps the C as the
  reference. `tests/plaits/test_drum_kernels.py`: whole-engine renders bit for bit against the C (random states with
  specials, run-on from init), the register contract, and a strict gate (precedents only from stock rows inside
  functions).
- Why it worked: selcc's code keeps every value on the stack and rebuilds every struct address; plain SISD code with the
  state in registers and loads/stores folded into Type 4 computes is 4-5x smaller with no SIMD at all. After it, the
  fixed per-block cost (plaits_post 3.8k, render_engine 2.1k, dc block, hb_init: about 7.3k) and the C set-up (2-6k per
  drum) dominate; the kernels are about 10k of each block. SIMD was not tried: the remaining pairs (snare's six SVFs,
  the hat oscillators) would save about 2-3k per drum at the cost of the `_sm` register juggling.
- Reuse: selcc's division of a constant runs its two Newton steps on the divisor only: compute that reciprocal once in
  the prologue (`recip_const`) and pay 5 instructions per division instead of 12 (rng_float's / 2^32).
- Evidence: lane drums-opt commits c07d3b9..7c73637; out/runs/plaits-cpu/fuzz/m1{3,4,5}-seed1-j3-n*-20261020T*.json.

### 2026-10-20 drum kernels: assembler and gate traps, FAIL (caught by the tests, fixed)
- `JUMP (PC, label)` (as in `DO (PC, label)`) assembled to a jump to itself under selas: the kernel spun forever on
  the concrete core. Write `JUMP label` / `IF cond JUMP label` as selcc does.
- selas does not compress inside a DO loop, so `.COMPRESS` around RECIPS there still gives the 48-bit form with no
  transfer, which has no stock precedent in a function. Use the Type 5a shape stock has: `F9 = RECIPS F12, R12 = R12`
  (the divisor moved onto itself).
- Stock (rows inside functions) has no conditional `R = PASS R`, `F = -F` or `R = R XOR R`: select with
  `IF cond Rn = Rx OR Rx`, absf as `F = -F` then `COMP; IF GE Rn = Rx OR Rx`, integer equality with `COMP(R, R)`.
  Conditions copy selcc's: the same COMP operand order and the condition or its complement (GE/LT, GT/LE, EQ/NE).
- A random-state test must keep phases, frequencies and envelopes in the range the engine keeps them: a negative
  phase indexes outside the sine table, which reads unseeded memory on the concrete core (a fork, not a mismatch), at
  a different address in each build.

### 2026-10-21 shared per-voice overhead (every model): output-stage kernel, hb_init only at 96 kHz, odd-voice copy loop, WIN
- Before → after (instructions per voice per block, concrete core, KICK's seed-1 fuzz-worst sequence, pk_dsp_waste):
  shared code about 7,160 → about 2,130 on a block without a note-on (plaits_post 3,764, render_engine 2,120 with its
  mix and gain loops, plaits_dc_block 733, plaits_render / plaits_track / unit / the shim and `_di_frame_hw` about 540
  → render_engine 462, the kernel about 1,120, the rest unchanged); a note-on block also drops hb_init's 718. Fuzz
  worst per block (seed 1, same sequence counts) with this and the CHORD work: VA (0) 80,557 → 75,535, CHORD (6)
  56,771 → 30,711, RINGS (12) 31,494 → 25,083, KICK (13) 23,386 → 17,999. In the kernel test the stage alone is
  6,939 → 1,914 (VA gains) and 8,748 → 2,626 (RINGS, limiter on OUT) per block.
- What: `post_sm.s` (`plaits_stage_kernel`) replaces, per 16-sample sub-block, both plaits_post calls, the mix, the DC
  blocker and the output gain (the C ran the last two over the 32-sample block afterwards: a per-sample recurrence and
  a per-sample gain, so per sub-block in order gives the same words). Three DO loops: OUT and AUX in place (a plain
  variant and a limiter variant each, chosen by the gain's sign), then mix + DC + gain into dst. The limiter's division
  runs on every sample and is dropped by `IF LE` when peak <= 1 (selcc branches around it): same words, no branch.
  `-DPL_POST_C` keeps the C. hb_init now runs only when rate == 96000: at 48 kHz the interpolator is never read or
  written, so it stays the zero state of load. The odd voice's 32-word copy in the shim is a DO loop (2 instructions a
  word instead of 4 plus a taken branch).
- Why it worked: the same selcc pattern as the engines (every value on the stack, addresses rebuilt per access); the
  output stage was the largest fixed cost of every voice.
- Not done (small): render_engine's model if-chain (about 4 instructions per test, up to 15 tests per sub-block), the
  four unit() calls, folding the kernel's loads and stores into computes and unrolling it by 2 (about 2-3
  instructions per sample, some 200 per block).
- Reuse: tests/plaits/test_post_kernel.py runs plaits.c itself with every engine replaced by a stub that returns at
  once, so the seeded OUT/AUX words are what the stage sees: a cheap whole-wrapper oracle for any change to plaits.c.
  selcc's `offsetof` yields 0 (use sizeof of the members before, in bytes).
- Evidence: commits d1f17ad, 5aa3175 (lane opt-o3); out/runs/dsp-waste/plaits-m13.json, out/runs/opt-o3/.

### 2026-10-21 CHORD (6): kernels for both oscillators' sample loops and the engine's buffer loops, WIN
- Before → after: fuzz worst per block (seed 1, 54 sequences) 56,771 → 30,711 (-45.9 %, output stage included). Per
  oscillator call (16 samples, kernel tests): WavetableOscillator 3,574 → 1,647, StringSynthOscillator 4,298 → 2,341;
  whole chord_render (one sub-block, in the crossfade) 20,486 → 11,741.
  Confirmed (emulator, 1 track, the new fuzz-worst knobs TUNE 0x7c00 HARM 0 TIMB 0x826 MORPH 0x5043, windows 100,500,
  paired: the base image built from de3d230 against H-opt4): busy 261,263 → 235,405 instructions per block, load
  41.6 / 46.0 / 58.7 % → 38.0 / 43.3 / 57.5 % (low / nominal / high). **The high bound barely moves** (391,536 →
  383,248 cycles): the kernels cut stack traffic and instructions but keep every table read, and each load is used by
  the next instruction (no load scheduling), which the high bound charges at full L2 latency. Next for CHORD: issue the
  wave-table and k[] loads several instructions before their use (or software-pipeline by one sample) before SIMD.
- What: `lib/wavetable_sm.s` (two linear wave reads, the waveform crossfade, the differentiator and the one-pole, minf
  and itof_nonneg written out), `lib/string_synth_sm.s` (five interpolators, the segment logic, the polyBLEP with
  selcc's division, the four saws) and `lib/chord_loops_sm.s` (chord_render's clear loop and its out += aux, aux *= 3
  loop). The C computes the per-block values and passes the increments and the loop's literals in k[], as the drum
  kernels do; `-DPL_WT_C`, `-DPL_SS_C`, `-DPL_CHORD_C` keep the C. State in registers, SISD, no SIMD.
- Why it worked: the two oscillator loops held 48 % and 37 % of the worst block (helpers included), about 150 and 90
  instructions per sample; now about 62 and 70. What is left is C set-up: 25 interp() calls per sub-block (each a
  full selcc division by 16), the oscillators' prologues and chord_render itself (about 13k of the block).
- Next: the five voices are independent: wavetable and string-synth voice pairs in SIMD would halve the kernels (the
  `_sm` pattern: SISD transfers, SIMD compute). An interp() shortcut for size 16 is not exact: selcc's division by 16.0
  gives +0 where a multiply by 0.0625 gives -0.
- Evidence: commits d1f17ad, 03be31b, 4f3e564; tests/plaits/test_wavetable_kernel.py, test_string_synth_kernel.py,
  test_chord_kernels.py.

### 2026-10-21 assembly labels: a label in two sources resolves to one of them, FAIL (caught by the fuzz rig, fixed)
- `string_synth_sm.s` first used `_ss_done` / `_ss_end`, which `drums_sm.s` (snare synth) already defines. Each
  kernel's own test passed (each links only its own file); in the merged DSP object snare_synth_kernel's `JUMP _ss_done`
  and its DO end went into the string-synth kernel, and the fuzz rig's call-stack check stopped at the first SNARE
  block ("return target differs from recorded return"). No assembler or linker error.
- Now enforced: `test_assembly_labels_are_unique_across_the_dsp_object` (tests/plaits/test_post_kernel.py). Prefix a
  new kernel's labels with something no other file uses, and run the fuzz (any model: every run plays model changes)
  before an image.

### 2026-10-21 CHORD kernels on the cycle model: fewer instructions is not fewer cycles, LESSON (O3 fix round 1)
- Before → after: CHORD's isolated fuzz-worst block -46 % instructions (56,771 → 30,711), the emulated 1-track run
  -10 % busy instructions (261,263 → 235,405), but the high-bound load only 58.7 → 57.5 % (391,536 → 383,248 cycles).
- Why: the kernels removed selcc's stack traffic, not the L2 traffic. Every wave-table and k[] read is consumed by the
  very next instruction, so each one pays the full L2 load-use latency at the high bound; each `I1 = Rn` is followed
  at once by a load through I1 (a DAG write-to-use stall, PRM Table 4-38: 4 cycles, three per sample in
  wavetable_sm.s); and loop invariants (`waves`, `offsets` from the frame, k[0..3]; string_synth's k[5..12]) are
  reloaded every sample.
- Next technique, in this order: (1) load scheduling: issue each load several instructions before its use, keep
  invariants in free registers (R3 is free in wavetable_sm.s) and put independent work between a DAG write and its
  use; or software-pipeline by one sample (the next sample's table reads during this sample's filters); (2) only then
  SIMD voice pairs. Measure each with the paired emulator run (high bound), not only the isolated instruction count.
- Reuse: for any kernel whose hot loop reads L2 tables, judge it by the cycle model's high bound.
- **[C] (O4)** The mechanism above is wrong. The cycle model charges no load-to-use latency: its memory terms are
  per data-cache and instruction-cache *miss* (line fills, `tools/sharc_dev/sharc_cpi.py`), and its pair stalls are
  DAG write-to-use (4), float result to the next compute (1) and float result to a move (1). The PRM agrees: "cache
  hits do not stall the pipe" (p. 8-7), a miss stalls the core until the line is in, whatever the distance to the use,
  and Tables 4-35..4-43 list no load-to-use stall for a data register. CHORD's high bound was paid by instruction-cache
  fills (about 499 per block in the H-opt4 run), data-cache fills (237, inflated by the cycmix line bug below), the
  DAG writes `I1 = Rn` right before their loads and the serial float chains. Loading earlier changes nothing;
  spacing DAG writes from their use and interleaving float chains does (entries below).

### 2026-10-21 every model: where the stall cycles go (pk_dsp_stalls), NEUTRAL (observation)
- Before → after: no change; one fuzz-worst block per model (seed-1 files), on the isolated core.
- What: `uv run python -m tools.patchkit.pk_dsp_stalls --model N [--model M ..] [--loads]` charges per instruction the
  pair stalls of sharc_timing (dag, fwd, mov, taken branches) and simulates the two 16 KB 2-way caches over the block's
  real code and data addresses (cold, and warm = the block replayed once first); per function and loop, high bound
  = instructions + stalls + 48 per code miss + 96 per data miss. Ranking by stall cycles at the high bound: PHASE-DIST
  (17) 50.4k, ADDITIVE (4) 45.8k, VA (0) 44.6k, then 3 40.8k, NOISE (9) 37.5k, WAVETABLE (5) 37.2k, SPEECH (7) 33.7k;
  CHORD 28.4k, the drums 26-27k, RINGS 21.3k, SQUARE 19.8k. By kind, summed over the 18 models (cold caches):
  instruction-cache misses 33 %, DAG write-to-use (almost all in selcc C) 32 %, data-cache misses 18 %, float
  forwarding 14 %, taken branches 4 %. Data-cache misses are 38-89 per block cold and 7-19 warm everywhere except
  WAVETABLE (170 / 59): the tables mostly hit.
- Why it matters: memory is the *code* side, not the tables: every model touches 150-300 code lines (10-19 KB) of L2
  code per voice block, and 25-187 of them miss again when the block is replayed (drums worst: the hot code is larger
  than the 16 KB 2-way cache). The biggest pure-stall class is selcc's `In = Rm` / `In = DM(..)` followed by a use
  (`next.` of the interpolators: 8.7k stall cycles for 9.8k instructions in VA): only kernels remove it, selcc has no
  optimiser.
- Reuse: run it before choosing a target; `--loads` prints the load-to-use distances, which do not matter on this core.
- Evidence: out/runs/opt-o4/stalls-base.txt, out/runs/dsp-stalls/plaits-mN.json (not committed); commit 752c31b.

### 2026-10-21 CHORD wavetable kernel: software pipelining by one sample, WIN
- Before → after (CHORD's fuzz-worst block, seed 1, pk_dsp_stalls high bound): wavetable_kernel 7,455 → 7,476
  instructions, 13,119 → 9,493 cycles (dag 1,344 → 0, fwd 2,352 → 49); the loop 64 → 65 instructions and 97 → 65
  cycles per sample before misses.
- What: stage A(n + 1) (the interpolators, the phase wrap, the fractions, the offsets row, both table rows into I1 and
  I2) is interleaved with stage B(n) (the table reads, crossfade, both one-poles, out +=); prologue A(0), loop
  size - 1, epilogue B(size - 1), size 1 jumps past the loop (LCNTR = 0 would run 2^32 times). The offsets row goes
  through I3, so every DAG register is written 6 or more instructions before its use; no float result feeds the next
  instruction. Same operations, operands and order per value; the kernel test covers sizes 0, 1, 2, 7, 16 and the
  register contract (I2, I3, L2, L3 are now saved).
- Why it worked: the loop had independent work (next sample's index arithmetic) but the plain order serialised it.
- Reuse: the pattern for any per-sample loop whose addresses come from per-sample arithmetic: compute the next
  sample's addresses into spare I registers during this sample's filters.
- Evidence: commit 6eadede; tests/plaits/test_wavetable_kernel.py.

### 2026-10-21 CHORD string-synth kernel: four saws in their own registers, delayed branch, WIN
- Before → after (same block): the loop 7,546 → 6,378 high cycles (fwd 1,300 → 260, branch 200 → 72), instructions
  unchanged. The whole CHORD block 56,577 → 52,530 high cycles (-7.2 %) with the wavetable change, 28,158 → 28,396
  instructions (+0.8 %); fuzz worst 30,711 → 30,790 instructions (+0.3 %, same sequence).
- What: each saw in its own register (R0, R2, R3, R4) so the four chains interleave, the additions to next_sample
  in the plain order; `phase += f` hoisted between the gain updates; the same-segment test as `IF EQ JUMP (DB)` with
  the first saw's constant load and `discontinuity = 0` in the delay slots (neither sets flags).
- Why it worked: the chains were independent but shared one temporary.
- Evidence: commit b4dd1bb; tests/plaits/test_string_synth_kernel.py.

### 2026-10-21 load scheduling (issue loads earlier), NEUTRAL by construction
- Not applied: on this core a load has no load-to-use stall and a cache miss stalls regardless of distance (entry
  above). Loads moved earlier in the two CHORD kernels only where that freed a register or a slot.
- Reuse: schedule for the DAG write-to-use distance (4) and the float forward (1), not for loads.

### 2026-10-21 L1 copies of hot tables (research only), NEUTRAL
- Data-cache misses are 18 % of the stall cycles with cold caches and a few % warm; CHORD's wave reads hit (60 misses cold, 9 warm for 8.6k L2 data
  accesses), so a table copy in L1 would save at most about 5.8k high cycles per CHORD block cold and less than 1k warm.
  Only WAVETABLE (5) misses much (170 / 59). No L1 placement was tried and none is proposed: no evidence that stock
  leaves any L1 range unused. The bigger memory cost is code (33 %): smaller or contiguous hot code is the lever.

### 2026-10-21 CHORD SIMD voice pairs, NOT DONE (estimate)
- After scheduling the wavetable loop is 65 instructions per voice-sample, about 15 of them loads (k[0..3], offsets,
  waves, the offsets row, the four table words, out). A pair would keep every load (SISD, each lane its own k[] and
  table rows), add the moves into the S registers, the S-to-I moves for the y lane's rows, the serial out sum and the
  PEYEN toggles (the `_sm` pattern: RINGS went from 16 to 37 instructions per sample under it), and share only the
  compute: about 85-95 instructions per pair-sample against 130, at best 2k cycles per CHORD block (4 %), with the
  string synth's segment branch per lane on top. Not worth it before SIMD loads are proven on the unit.

### 2026-10-21 output stage kernel: scheduling, NOT DONE (estimate)
- plaits_stage_kernel's loops pay about 300 float-forward cycles per voice block (0.5 % of a voice) in short 16-trip
  loops (each also pays the E2-active loop exit, which pk_dsp_stalls does not count). A shared kernel: every model's
  digests would need a slow run for that gain; left for a batch with other shared changes.

### 2026-10-21 cycle model: byte-space L2 data counted in 16-byte lines, FAIL (measurement bug, open)
- packages/emulator/sharc/src/cycmix.rs puts an L2/external data access in line `a >> 4` for every address below
  0x60000000, so byte-space L2 (0x20000000..0x20118000, where every PLAITS pointer points) gets 16-byte lines, and
  it uses the I register's value, not the effective address. On the isolated core the same accesses give 176 misses
  per CHORD block that way against 60 with 64-byte lines (VA 120 / 38, KICK 119 / 45): at 96 cycles each the high bound
  overstates a voice by about 7-11k cycles (1-1.7 % of the DSP block). Fixing it changes every recorded high figure,
  so it was not changed here: fix it (byte space → `a >> 6`, effective address) in its own commit and re-baseline.

### 2026-10-21 CHORD on the emulator: the high bound moves 1 pp with code size alone, LESSON (O4 confirm)
- Before → after (1 track, knobs TUNE 0x7c00 HARM 0 TIMB 0x826 MORPH 0x5043, windows 100,500, high bound): H-opt4
  57.5 % (recorded) → H-opt5 58.0 % (386,878 cycles). The kernels grew the DSP code by 504 bytes, which moves every
  later function and all PLAITS data by 504 bytes. The base kernels rebuilt with a 504-byte pad (NOPs after the last
  kernel, same section size as H-opt5; not committed) measure 58.5 % (390,081): the layout alone cost 1 pp, the
  scheduled kernels gain 0.5 pp high and 0.6 pp nominal (43.5 → 42.9 %) against it. Per rule, pad base → H-opt5:
  DAG write-to-use 5,238 → 3,318, float forwarding 9,886 → 6,886, instructions +340, code fills +31 misses per block
  (+1,488 high: the prologue and epilogue add lines), data fills unchanged.
- Why: the data cache is shared with stock's DDR sample data, whose fills the high bound charges at 240 cycles; moving
  PLAITS' lines shifts which sets collide. The whole-run high bound is dominated by stock (branches 53k, DDR fills 51k
  of 387k cycles).
- Reuse: a paired A/B must hold the layout: pad the base to the same section size (or compare the isolated-core
  report, pk_dsp_stalls, which has no stock in its caches). Count code growth as a cost: every 64 bytes of hot code
  is a possible 48-cycle miss per voice block.
- Evidence: out/runs/opt-o4/m6-t1-{hopt4-before,padbase,hopt5-after}.json (not committed).


### 2026-10-21 O4 merge decision: tools and learnings only, NEUTRAL
- The O4 kernel commits (wavetable one-sample pipelining, string-synth interleave with a delayed conditional branch) were
  **not merged**: the layout-fair gain (CHORD 58.5 → 58.0 % high) is within noise, the as-built comparison was slightly
  worse (57.5 → 58.0 %), and the delayed branch inside a hardware loop has no stock precedent in that context and no
  gate check for delay slots (O4 review). They stay on lane/opt-o4 for reference.
- Merged: `pk_dsp_stalls`, `pk_plaits_cpu --models`, and the learnings above.
- Reuse: hold code layout fixed in every A/B (pad the base); fix the cycmix byte-space L2 line count before trusting
  small deltas; next levers are kernels for the compiled-C engines with the most DAG stalls (PHASE-DIST, ADDITIVE, VA)
  and hot-code size and layout (I-cache 33 % of stall cycles).

### 2026-10-22 cycle model: data-cache lines from the effective, physical address, FIX (O5)
- Before → after (same image 48cefe81, same block counts, windows 100,500, high bound): stock 1.17 with TRIG 1 45.5 →
  48.6 % (303,468 → 324,075 cycles per block; busy 216,151 both); 1 KICK track at its worst knobs 53.7 → 56.2 %
  (358,052 → 374,753; busy 235,554 both).
- What: packages/emulator/sharc/src/cycmix.rs took the line of an L2/external access as `I >> 4` for every address
  below 0x60000000 (byte-space L2 in 16-byte lines) and always from the I register, so every pre-modify access
  (selcc's whole frame, `DM(-k, I6)`, the kernels' `DM(k, I7)`) fell in I6's or I7's line and always hit. Now the
  memspec slots carry the pre-modify (`I`, `I+mM/S`, `I+K/S`, made by sharc_cpi from the program database, file
  `memspec-ea-<image>.txt`), the engine adds it (scaled by the access size in byte space, PRM Table 6-2) and maps
  the address to its physical byte address before `>> 6`. Bigger byte-space lines lower the count, real frame
  addresses raise it: net +3 pp on stock, +2.5 pp on a KICK voice.
- Reuse: an A/B against an engine built before this needs the old file: `pk_plaits_cpu --memspec
  out/clock/sharc/memspec-<image>.txt`. `pk_plaits_cpu --stock` measures the stock baseline through the same harness.
- Evidence: commits 99194c6, fa564a2; out/runs/opt-o5/{stock,before,after}-*.txt (not committed).

### 2026-10-22 PHASE-DIST (17): the oscillator loop and the 2x downsampling as SISD kernels, WIN
- Before → after (fuzz-worst block replayed by pk_dsp_stalls, isolated core): 97,034 → 33,270 instructions,
  147,574 → 56,463 high cycles (-66 % / -62 %). Fuzz (seed 0, 35 sequences, base vs after): 97,034 → 33,953.
- What: `lib/phase_dist_sm.s` (generated, `lib/gen_phase_dist_kernel.py`): pd_osc_kernel replaces pd_osc_render's
  per-sample loop (the five interpolators stepped in the state, both selcc divisions for the slopes interleaved, the
  sync reset and the blep while-loop as branches in a software loop, pd_naive written out with selects);
  pd_down_kernel replaces the downsampling loop (two sines per iteration interleaved, the negative-phase wrap
  computed for every sample and selected, so the DO loop has no branch). `-DPL_PD_C` keeps the C.
- Why it worked: the loop's work was mostly selcc overhead (calls to next/pd_naive/the bleps, `I4 = Rn` before each
  state access: 23.8k DAG stall cycles in one block). The rare paths (reset, transitions) run often at high notes, so
  they are written plainly but without calls.
- Reuse: the same oscillator is VA's shape_render (next entry); a software loop with branches is fine when the body
  is long (the loop overhead is 3 instructions against ~75).
- Evidence: commits d8863a9, 5c8690a; tests/plaits/test_phase_dist_kernel.py (mutation-checked: 8 edits all caught).

### 2026-10-22 ADDITIVE (4): harmonic oscillator and amplitudes_update as SISD kernels, WIN
- Before → after (same block): 86,666 → 23,811 instructions, 132,446 → 44,567 high (-73 % / -66 %). Fuzz (seed 1,
  26 sequences): 88,156 → 25,466.
- What: `lib/additive_sm.s` (generated): harmonic_kernel computes the 13 interpolator increments itself (one RECIPS
  and two Newton steps for the shared divisor itof_nonneg(size): they depend on the divisor only, so per target only
  selcc's last five operations run) and unrolls the 12 harmonics with the Chebyshev recurrence alternating two
  registers; amp_kernel runs amplitudes_update's two loops (Sine written out, amplitudes[j] through `DM(M4, I1)`
  pre-modify with M4 written ~40 instructions before its use).
- SIMD pairs (brief: partials and the two oscillators): not done. With SISD transfers only, each harmonic's three
  memory operations stay, and the recurrence is serial per oscillator; pairing the 13th..24th with the 1st..12th
  would share the 5 compute instructions per harmonic but add the S-register moves and PEYEN toggles: estimated
  under 1k cycles per block, not worth it before SIMD transfers are proven.
- Reuse: "divide many numerators by one divisor": compute y once, keep selcc's per-target tail.
- Evidence: commits 941c590, 3dac436; tests/plaits/test_additive_kernel.py.

### 2026-10-22 VA (0): shape_render on PHASE-DIST's kernel, saw_render as a kernel, WIN
- Before → after (same block): 75,589 → 33,866 instructions, 120,623 → 59,430 high (-55 % / -51 %). Fuzz (seed 1, 30
  sequences): 75,626 → 34,381.
- What: VA's three shapes are the same VariableShapeOscillator with output_phase off: pd_osc_kernel takes a flags
  word (bit 0 sync, bit 1 output_phase off: `2 this_sample - 1`, phase_modulation not stepped) and Shape has PdOsc's
  layout. `lib/va_sm.s` (generated) is the variable saw (the constant division of the output by 1.2f with the
  divisor's reciprocal computed once). Left: va_render's own mixing loops and set-up (~3.6k instructions, 70 code
  lines that miss every block).
- Evidence: commits 2542736, f468224; tests/plaits/test_va_kernel.py.

### 2026-10-22 hot code: assemble every kernel compressed, WIN; reorder the object by engine, NEUTRAL
- Compression: the kernels were assembled `.NOCOMPRESS` (48-bit everywhere). Under `.COMPRESS` the assembler
  keeps a DO loop's instructions 48-bit (the PRM: a loop's last 11 must be ISA forms; `pk_encoding_gate` now checks
  it) and shortens the rest: drums 10,476 → 8,956 bytes, RINGS kernel 1,588 → 1,118, output stage 1,056 → 932,
  PHASE-DIST 3,620 → 2,568, ADDITIVE (harmonic kernel) 3,608 → 2,870. VA's block: 63,211 → 59,179 high, code misses 337 / 214 → 253 /
  112 (cold / warm). Every kernel test and the strict gate pass unchanged.
- Layout: grouping the sources by engine (each engine's C next to its kernels, shared code first) against the old
  order, both compressed, summed over 18 models' fuzz-worst blocks: 1,247,456 → 1,252,592 high cycles, warm code
  misses 1,068 → 1,495 (worse); uncompressed it was -0.4 %. Single models move ±2.5k either way (CHORD and SNARE
  worse, VA and SPEECH better). Why: most models touch 180-300 code lines (11-19 KB) per block, near or above the
  16 KB 2-way cache, so the misses are capacity misses and the order only moves which sets thrash. The old order was
  kept. The lever is code size: compression, kernels instead of selcc C (va_render's set-up), fewer calls.
- Traps: under `.COMPRESS` the assembler can merge or drop a statement after a loop end or a `(DB)` branch (seen in
  test snippets: `R1 = R1 + R2; R2 = R2 + R3` after a loop end); pk_sharc's statement cross-check refuses it, so a
  kernel that assembles is safe. Write the delayed-branch tests under `.NOCOMPRESS`.
- Evidence: commit 3dac436; out/runs/opt-o5/stalls-all-{head,layout1,headc,layout1c}.txt.

### 2026-10-22 encoding gate: delay slots and loop ends, NEW CHECKS
- `check_delay_slots`: after a delayed branch (`j` = 1, or CJUMP), the two slots may hold no branch or DO, no PC
  stack push/pop/write, no IDLE, no write of an indirect branch's I/M (`JUMP (M14, I12) (DB); I12 = ..`), and, after
  a conditional one, no flag writer. The last is a precaution beyond the PRM's text: stock does it 339 times; every
  other rule has no stock exception. `check_loop_ends`: a DO loop's last 11 instructions are 48-bit and its end is an
  instruction. Both run in `check_image` and in the O5 kernel tests; the 48cefe81 image and every kernel pass.
- Evidence: commit 5c8690a; tests/patchkit/test_encoding_gate.py.

### 2026-10-22 O5 next steps
- DUST (10) is now the heaviest model (114k instructions, 193k high cycles per fuzz-worst block): kernel next.
- va_render's own set-up and mixing loops (about 70 code lines that miss every block), SPEECH (7) and STRING
  MACHINE (22) have the most code-cache misses (warm 150-180): smaller hot code there, not reordering.
- Calibration: choppy lies between 87.5 % and 101.3 % projected (finding 06); measure one more mix between them
  (e.g. 4 KICK + 2 RINGS + 1 VA) to narrow it.

### 2026-10-22 O6 (owner goal: 8 voices of the heaviest engines): six approaches evaluated, NEUTRAL (observations)
- Metric: the fuzz-worst block of each model on the isolated core (`pk_dsp_stalls`, high bound, cold caches), projected
  to 8 tracks of that model as 48.6 % (stock) + 8 x cold / 6,666.67 (the O5 calibration mix 4 KICK + 2 RINGS projects
  to 87.9 % this way against 87.5 % measured on the emulator; with 7 of 8 voices warm it would give 81 %). "Clean"
  means at most about 87 %: about 32k high cycles per voice. Base c8dcc0a: only SQUARE (94 %) and KICK (99 %) come near;
  DUST 280 %, FORMANT 201 %, TERRAIN 167 %, STRING MACHINE 165 %, NOISE 162 %, VCF 155 %, 2FM 151 %.
- Ranking (measured or estimated gain per voice):
  1. **Kernels for the engines still in selcc C** (measured: 40-70 % of the block per engine, entries below). By far the
     largest lever; selcc's C costs 500-850 instructions per oscillator-sample where a kernel needs 70-150.
  2. **Spike removal without clearing** (DUST, STRING MACHINE: the model-change block): measured -28k instructions and
     -258 data-cache fills in DUST's worst block (entry below).
  3. **Shared per-voice path** (hook, shim, plaits_track/render, render_engine, stage kernel): about 2,200 instructions
     and 9k high cycles per voice, mostly instruction-cache fills; the dispatch tree saved about 1k (entry below); a
     whole-path assembly rewrite would save an estimated 3-5k per voice (not done: it rewrites the hardware-proven shim).
  4. **Control-rate memoisation**: 0 on the worst block (a note-on or a knob move changes the inputs; 8 voices on one
     step all have note-ons together). Its exact part, sharing a pure computation between two consumers in the same
     sample, went into the kernels (NOISE's tan/r/h of the two filters with the same arguments).
  5. **Stock per-voice cost of a PLAITS track: none to remove.** Per-PC profiles (O5 runs, windows 100/500 ms): the stock
     code runs 216k instructions per block with no PLAITS track and the same with six; the stock voice loop renders and
     post-processes all 32 voices regardless. Only the hook itself (record-to-voice division loop, type scan) runs per
     voice: about 100 instructions, 3k cycles per block over all 32 voices (left: it is core/dsp-inline, shared).
  6. **Voice-pair SIMD across tracks: feasible to orchestrate, not worth building yet.** The render loop (FUN_1c642a,
     0x1c6ae8..0x1c6b09) calls the renderer for voices 0..31 in order with nothing between them, and every input of a
     later voice (frame, trigger latch, ACTIVE) is fixed before the loop, so voice B could be rendered with voice A and
     its block copied at B's turn (exact if B's per-voice renderer selector, the word read at 0x1c6ae7, is checked too).
     But with SISD transfers only the gain is the compute of the kernel loops (about 25-40 % of a kernelised voice):
     estimated 10-15 % per voice, every kernel rewritten as a pair kernel with per-lane branches. SIMD transfers need the
     existing probes `probe-simd-l2-load` / `-store` (d0085564, 855474a8) to pass on the unit first.
  7. **I-cache**: 150-330 cold and 20-190 warm fills per voice-block (7-16k high cycles) remain the largest stall class
     after the kernels; with eight voices of one model in a row only the warm figure repeats. Code size and fewer C
     frames are the lever (not the order, O5).
- Evidence: out/runs/opt-o6/stalls-base.txt and stalls-final.txt (not committed); the per-region split of the O5
  per-PC profiles out/runs/plaits-cpu/stock-o5new-500.tsv and mix-13_13_13_13_12_12-o5-500.tsv (minus their 100 ms runs).

### 2026-10-22 O6 kernels: FORMANT, NOISE, DUST, STRING MACHINE, SWARM, TERRAIN, 2FM, VCF-VA, WAVETABLE, SPEECH, WIN
- Before -> after (fuzz-worst block, pk_dsp_stalls, instructions / high cycles): FORMANT 84,911 -> 24,744 / 126,858 ->
  49,043; NOISE 56,611 -> 18,338 / 94,283 -> 36,180; DUST 114,042 -> 36,455 / 193,285 -> 59,403; STRING MACHINE 52,589 ->
  35,303 / 96,874 -> 65,580; SWARM 44,557 -> 32,826 / 75,633 -> 59,778; TERRAIN 68,600 -> 26,857 / 98,654 -> 50,475;
  2FM 55,857 -> 22,628 / 85,077 -> 38,064; VCF-VA 55,561 -> 25,604 / 88,891 -> 47,213; WAVETABLE 39,569 -> 20,486 /
  75,895 -> 49,484; SPEECH 33,239 -> 23,448 / 68,391 -> 53,108. HIHAT gains 3.7k high from the shared svf kernel.
- What: `lib/grain_sm.s` (both grainlets, the Z oscillator, the DC blockers), `noise_sm.s` (clocked noise, the filter
  loop), `dust_sm.s` (particles; post filter and diffuser fused per sample), `string_machine_sm.s` (mixdown and ensemble
  fused per sample), `swarm_sm.s` (the per-grain sample loop), `terrain_sm.s` (the whole loop, terrain() and
  terrain_sine as local CALL / RTS (DB) subroutines), `fm_sm.s`, `vcf_sm.s` (filter loop; VCF's oscillators run on
  pd_osc_kernel with flags 2), `wavetable_engine_sm.s`, `sam_sm.s` and `svf_sm.s` (svf_band_pass and _add). Each has a
  generator and a test (`tests/plaits/test_*_kernel.py`: bit-exact against the -DPL_X_C build on random states, run-on
  from init, register contract, strict gate). Every kernel passed its oracle at the first full run except two slips
  caught by it (a pointer register reused as a scratch DAG in the loop; see the next entries).
- Why it worked: the same as O1-O5 (selcc keeps every value on the stack and calls a function per Sine / next / blep).
  Writing them out with selcc's operation order, its division sequence and its comparison conditions was enough;
  reading the selcc listing (`selcc -S`) for each helper first (constants, operand order, which condition) made the
  kernels right first time.
- Reuse: the test template (copy test_noise_kernel.py, change the state generator); `.NOCOMPRESS` around integer
  additions; a local subroutine for a body used four times (TERRAIN) instead of inlining.
- Evidence: commits f14b4ed..a1b72f0 (lane opt-o6).

### 2026-10-22 DUST and STRING MACHINE: no clear at init (reads of cleared memory select zero), WIN (spike removed)
- Before -> after: DUST's model-change block (its fuzz worst) lost dust_init's 4,096-word clear: 28,901 instructions,
  258 cold data-cache fills, 70k high cycles; STRING MACHINE's ensemble_init (1,024 floats, 21.8k high) likewise.
- What: each delay line is written at a fixed offset B from the write pointer and read at offset o > B, the pointer
  moving one per sample, the lines disjoint: the word a read sees was written o - B samples earlier by its own line, and
  no other line writes it in between. So a read sees the C's cleared memory exactly when fewer than o - B samples have
  run since init. The DSP build skips the clear, counts samples in a new `fresh_` word (DUST +4 bytes: voices 273,920),
  and the kernel selects 0.0f / 0 for those reads; once every o - B is passed (DUST 3,412 samples, the ensemble 370) an
  unchecked loop runs. The test starts both builds from garbage memory and checks every output word and every word the
  C would read later.
- Lesson: an exact lazy clear needs the write-before-read structure proven per line, not a frontier; it costs nothing in
  the steady state.
- **[C] (same day) DUST clears again; STRING MACHINE keeps the lazy clear.** On the emulator (8 DUST tracks, image
  27f7255b) the DSP halted at once: `native-trap ... fork` at the DUST_WRITE clamp. The emulator treats L2 that was never
  written (the `.PKBSS` voices) as unknown per 32-bit word, and DUST packs two 16-bit samples per word: a valid read of
  one half whose sibling was never written (the line ends, the gap offsets) gives an unknown word. Silicon would read
  whatever the memory holds and select correctly, but the image must run on the emulator, so dust_init clears the
  memory with a one-store-per-word kernel (dust_clear_kernel: 4,096 instructions instead of 28,901; the 256 data-cache
  fills stay: DUST's model-change block is 89.5k high, steady blocks about 60k). The fresh_ selects stay (exact either
  way). STRING MACHINE's chorus line holds whole floats and its taps select before any arithmetic, so it never computes
  on an unknown word.

### 2026-10-22 shared path: dispatch tree WIN (small); function-pointer table FAIL (toolchain and rig)
- render_engine's model chain (up to 15 tests per sub-block, many code lines) became a binary tree of five tests
  (render_sub): KICK's block 41.4k -> 40.9k high; render_engine 2,985 -> 1,292 (+ render_sub 702) high.
- A table of render functions failed three ways: patchkit refuses data relocations (`relocation in unexpected section
  seg_dmda`); selcc compiles `t[0] = f` (a bare function designator) as a load from f's address (`R0 = DM(f.)`, write
  `&f`); and the rig's call-stack check rejects the indirect call (`JUMP (M13, I12) (DB)`, a stock word) because only
  CJUMP is recorded as a call. Not worth a rig change for about 1k cycles.

### 2026-10-22 encoding traps found in O6
- selas compresses `Rn = Rn + Rm` (and `Rn = Rn XOR Rm`) to 16-bit forms that our decoder sizes differently (the
  cross-check fails: N statements, M instructions) or that stock never uses (the strict gate: `2c:ce00`). Write integer
  additions and subtractions under `.NOCOMPRESS` (core/dsp-inline does the same).
- Stock functions contain no plain XOR at all: `r ^ 0x8000` for a 16-bit half was rewritten as (half + 0x8000) mod 2^16
  (add 0x8000 to the low half, 0x80000000 to the high half, then FEXT).
- The rig's tail block had to move (0xBAC000 -> 0xBC0000, RETURN_SW 0xBC3100): the module code passed 106k words.

### 2026-10-22 ASYM-TRI (1) kernels and VA's mixing loops, WIN
- Before -> after (fuzz-worst block, high): ASYM-TRI 70,840 -> 38,513 (instructions 40,733 -> 18,129); VA 60,208 -> 51,359.
- What: `lib/ws_sm.s` (slope_render's loop with both integrated bleps and selcc's two divisions per sample; ws_render's
  loop with ws_table, itof_small, the waveshaper lerps and InterpolateHermite / Sine with their range guards in selcc's
  comparison order: a NaN index reads the fold tables at 1.0f, as selcc's code does), `lib/va_mix_sm.s` (va_render's two
  mixing loops as DO loops). The waveshaper tables are static in waveshaping.c, so their addresses come in k[].
- Evidence: tests/plaits/test_ws_kernel.py, tests/plaits/test_va_kernel.py (lane opt-o6, merge commit 27bf26a).

### 2026-10-23 STRING (11): string_process loop and excitation as SISD kernels, delay lines not cleared at init, WIN
- Before -> after (fuzz seed 1, 53 sequences): fuzz worst 85,774 -> 32,041 instructions (-63 %). pk_dsp_stalls, high
  cycles cold: 155,483 (old worst, the init block) -> 58,662 (new worst, block 31 of sequence 38); the old init block
  alone 155,483 -> 54,074. 8-track projection (48.6 % + 8 x cold / 6,666.67): 235 % -> 119 %; with 7 of 8 voices
  warm 205 % -> 103 %.
- What: `lib/string_sm.s` (generated, `lib/gen_string_kernel.py`): string_loop_kernel (the sample loop with
  src_phase, both non-linearity modes, Hermite and linear reads, the all-pass, CONSTRAIN as two selects on selcc's
  conditions, the DC blocker and the damping low pass) and string_excite_kernel (noise, low pass, aux sum fused).
  Hermite taps through `DM(Mk, I2)` with the four masked indices in M0..M3 (one write each, no wrap branch).
  Lazy clear as STRING MACHINE's, per line: offset o is written iff ((o - 1) & mask) < writes since init; checked
  loops select 0.0f until string_ has 1024 writes (stretch_ 256 when dispersion is on), then unchecked loops.
  `-DPL_STRING_C` keeps the C. Test: tests/plaits/test_string_kernel.py (fresh and saturated states, garbage under
  the never-written words, run-on from init over garbage; a disabled stretch check is caught).
- Trap: the compressed `Rn = DM(Mk, Ii)` is sized differently by our decoder (pk_sharc cross-check); emit it 48-bit.
- Next: the shared per-voice path and string_process's C set-up (~2.4k) are now a third of the block.

### 2026-10-23 DX7 (18-20, 24): render_ops' sample loop as one kernel with eight renderer loops, WIN
- Before -> after (fuzz seed 1, 67 sequences, same worst block): 96,011 -> 48,893 instructions (-49 %); high cold
  156,066 -> 93,672. 8-track projection: 236 % -> 161 %; 7 of 8 warm 217 % -> 144 %.
- What: `lib/dx_sm.s` (generated, `lib/gen_dx_kernel.py`): dx_ops_kernel, one DO loop per renderer (n, ms, additive),
  SinePM written out (the x >= 2^31 test as a select), lut reads by `DM(M4, I2)` / `DM(M4, I3)` (lut + 1). render_ops'
  C set-up stays and calls it; the patch quantiser is untouched (lane names). Test: tests/plaits/test_dx_kernel.py
  (every patch of the four banks, run-on, and render_ops per renderer through a test-only wrapper, which is the only
  test that reaches the feedback paths of renderers 6 and 7: no bank patch with a 3-op loop has feedback > 0).
- Next for DX7: voice_render, six_op_render, env/keyboard C (~12k instructions) and 460 cold / 352 warm code misses
  per block: the remaining C is now the cost, and its code size.

### 2026-10-23 O7 scope cut (owner): deferred to a later round
- SWARM (8; the grain loop as pairs), TERRAIN (21) and STRING MACHINE (22) remainders, PHASE-DIST (17) and CHORD (6)
  hot-loop checks were not started in O7.

### 2026-10-23 DX7 (18-20, 24): the whole voice as kernels (voice_render, render_ops' set-up, patch_unpack, op_env_set, the buffer loops), WIN
- Before -> after (pk_dsp_stalls, seed-1 fuzz-worst block, high cold): 18 93,751 -> 45,619 (49,014 -> 20,404
  instructions), 19 92,094 -> 44,558, 20 67,365 -> 48,015 (a model-change block: the inits), 24 88,095 -> 41,969;
  code-cache fills 456/348 -> 211/72 (cold/warm). 8-track projection 161 % -> 103 % cold, 144 % -> 86 % with 7 warm.
- What: `lib/dx_sm.s` (generated, `lib/gen_dx_kernel.py`): dx_voice_kernel runs voice_render after voice_setup: the
  scales (pow2_fast_1), the pitch envelope, f0 (semitones_to_ratio_safe and SemitonesToRatio), the note-on with
  normalize_velocity, per operator f[i], rate scaling, env_render with env_value_at (a local subroutine with a reshape
  flag), keyboard_scaling, the level and both pow2_fast_2, then render_ops' set-up (ftou, the amplitude increment) and
  the algorithm's calls with one DO loop per renderer. The C computes only the two divisions (1 / size, fb_scale).
  dx_pre/post_kernel are six_op_render's buffer loops (SoftClip with selcc's division), dx_unpack_kernel is
  patch_unpack (the 128 bytes into a frame row with FEXT, then the fields with selects), dx_env_set_kernel is
  op_env_set (selcc's division as a local subroutine). The op loop keeps 32.0, 2^26, 2^31 and 1.0 in registers and
  uses FEXT for the mantissa: 23 instructions per operator-sample (was 29).
- Traps: a conditional `IF GT R1 = R2 OR R2` writes the flags when it executes, so a second select on the same COMP
  needs its own COMP (caught by the run-on test, a kb_scaling depth of 0). A test that only strikes bank patches never
  moves the pitch envelope: test_dx_kernel now pokes random pitch-envelope levels, algorithms and feedback amounts
  (a mutation of the * 12.0f went unnoticed before). A tiny constant mutation of a pitch path can stay invisible
  because SemitonesToRatio quantises to 1/256 semitone: mutate by a whole step.
- Reuse: the selcc ABI walk (word offsets of the C structs; a struct of per-call values filled by the C) and the env
  subroutine; the CALL/RTS (DB) local subroutine for anything called more than once per pass.
- Evidence: commits 39997087, 7daea5c5 (lane opt-o8); tests/plaits/test_dx_kernel.py (16, mutation-checked).

### 2026-10-23 shared wrapper: plaits_render, render_engine and render_sub in assembly with selcc's calling convention, WIN
- Before -> after (all 25 models, seed-1 fuzz-worst blocks, with the interp change below; layout not held, see the
  next entry): -420 to -700 instructions and -1.5k to -4.4k high cycles per voice block for the non-DX7 models;
  code-cache fills (mean over 25 models, cold/warm) 263/131 -> 208/74 with DX7.
- What: `wrap_sm.s` (generated, `lib/gen_wrap_kernel.py`) is plaits_render at 48 kHz: note_on through a C helper
  (plaits_note_on_c), the armed/active/ported tests, the DX7 release latch, Params with unit()'s operations and
  comparisons, the per-sub-block trigger, render_sub's tree with one CJUMP site per engine, the stage kernel, zeros.
  It is CJUMPed by plaits_track like the C, keeps its locals below I6, CJUMPs the C engines with selcc's argument
  registers and stack, and at any other rate jumps to the C (plaits_render_c) on the same frame. unit, the DX7 test
  and the ported test are macros (all 25 models are ported now: a range). tests/plaits/test_wrap_kernel.py runs both
  against the C with logging stubs for every engine and init function (dispatch, Params, trigger, note_on, zeros,
  96 kHz, register contract, strict gate; mutation-checked).
- Traps: a hand-written selcc-convention routine must end in `.COMPRESS` (the return `JUMP (M14, I12) (DB); RFRAME`
  is mis-assembled under .NOCOMPRESS, as in the O1 entry); a test stub that logs stack arguments of a function that has
  none reads the caller's frame, which differs between an assembly and a C caller.
- Evidence: commit a9cc12d2; tests/plaits/test_wrap_kernel.py, test_post_kernel.py.

### 2026-10-23 interp with itof_nonneg written out, WIN (small); hot-code layout, NEUTRAL (noise of +-20 fills)
- interp (21 engine files) called itof_nonneg as a function: written out it is the same operations, about 20
  instructions less per call (VA -440, CHORD -500, PHASE-DIST -220 instructions per block).
- Layout was not held in these A/Bs: each change shifts every later function. Models near the 16 KB cache (VA,
  FORMANT, WAVETABLE) moved +4..+50 warm fills while the instruction counts fell (VA warm 90 -> 142); most others
  fell. The cold/warm figures per model are within the +-20-fill layout noise O5 measured, so only the instruction and
  stall deltas are claimed per change; the per-model table (report of O8) is as built.
