"""Lane H3 (2026-09-26): trace FUN_1c642a's per-voice arm/dispatch guard by
real execution -- which memory cells decide whether a voice iteration takes
the direct "arm" path (0x1c6540/0x1c6549 -> 0x1c657b -> `CALL FUN_1c4eaf` at
0x1c6582, which sets a voice record's own +0x1b8/+0x1ba fields) instead of
falling through into the per-slot-type jump table at 0x1c6553/0x8055c840
(docs/findings/06's Lane E2: on a real capture, all 32 voice iterations
land on 0x1c6553, never on either guard).

    uv run python -m tools.sharc_dev.sharc_armpath dt2-1.16 CAPTURE.dt2cap \
        [--frames N] [--report OUT.json]

**The guard, decoded once from `out/sharcdb`'s own disassembly of dt2-1.16
(not re-derived from raw bytes here -- this module only adds a pc-hit +
register-sample trace on top of it):**

    0x1c6530  R2 = DM(I1 - 32)             # word-scaled: I1-byte - 128
    0x1c6535  R2 = leftz(R2, R0)           # SZ=MSB(old R2); SV=(old R2==0)
    0x1c6538  JUMP IF SV -> 0x1c6543       # field1==0: SKIP GUARD_A (see
                                           # below -- this jump has TWO
                                           # delay slots, confirmed by
                                           # execution: sharc_core.
                                           # sequencer._transfer's own
                                           # Pending(slots=2, ...))
    0x1c653b  R6 = DM(I4, M5)              # delay slot 1 (always runs):
                                           # a bitmask, base set earlier
    0x1c653c  IF NOT SV R2 = DM(I6 - 2)    # delay slot 2 (always runs too
                                           # -- delay slots are
                                           # unconditional; only once IT
                                           # retires does a taken 0x1c6538
                                           # redirect to 0x1c6543, skipping
                                           # 0x1c653e/0x1c6540 entirely).
                                           # Its OWN body is conditional on
                                           # SV, which has not changed
                                           # since 0x1c6538 read it -- so it
                                           # assigns R2 exactly when
                                           # GUARD_SKIP will NOT redirect.
    0x1c653e  R2 = btgl(R6, R2)            # only reached when GUARD_SKIP's
                                           # own redirect did NOT fire; SZ
                                           # = (result == 0)
    0x1c6540  JUMP IF SZ -> 0x1c657b       # GUARD_A (non-delayed, reached
                                           # only when field1 != 0): arm
                                           # iff R6 == 1<<pos
    0x1c6543  M4 = 0xffa0                  # -96 words = -384 bytes
    0x1c6545  R2 = DM(I1, M4)              # a second per-voice field
    0x1c6547  R2 = leftz(R2, R0)           # SV = (that field == 0)
    0x1c6549  JUMP IF NOT SV -> 0x1c657b   # GUARD_B: arm iff field != 0
    0x1c654c..0x1c6579                     # neither guard taken: the
                                           # per-slot-type jump table body
                                           # (0x8055c840) E2 already found
                                           # every real voice lands in
    0x1c657b  I4 = I15; ...; R4 = I4       # shared arm preamble (record
                                           # address argument)
    0x1c6582  CALL FUN_1c4eaf              # arm: record+0x1b8/+0x1ba = 1

`sharc_core.flags._astatx_leftz`/`_astatx_bit_field` (bset/bclr/btgl) give
the exact flag semantics quoted above -- SV/SZ are not guessed from the
public PRM's mnemonic names alone.

`FUN_1c4eaf` itself (0x1c4eaf-0x1c4ec8): `I4 = R4` (the argument, a record
base), `I4 = modify(I4, 0x1ba)` -- **this one really is a raw-byte offset**
(out/sharcdb's own decode flags it `[raw-byte offset space=DM]`, unlike the
two guard loads above, which are the default word-scaled addressing) --
then `DM(I4, M5) u=0 = M14` at 0x1c4eb9 (record+0x1ba) and
`DM(I4, M7) u=0 = M14` at 0x1c4ebb (record+0x1ba+M7, the task's own
+0x1b8 -- M7's own value is read from the same trace this module records,
not assumed).

`FUN_1c2ac9`'s own two `CALL FUN_1c4eaf` sites (0x1c2b0a, 0x1c2b14) pass
fixed addresses (0x252730, 0x252908 -- confirmed by
`tools/sharc_dev/sharc.py`'s static decode, not a voice-record base) as their own R4
argument, so a hit there arms something in the `render_frame` master-bus/
dynamics scratch tables (0x2524xx-0x2529xx, the same forest `FUN_1c2b24`'s
own compile-time GLOBALS list writes at every frame -- see
`out/ghidra/sharc-dt2-1.16/functions.jsonl`'s `FUN_001c2b24` entry), not a
voice: this module still traces both call sites (`FUN_1C2AC9`/
`CALL_252730`/`CALL_252908` below) so a report can say whether they run at
all, without claiming they arm a *voice*.

This module never edits `sharc_core`, `emu/`, or
`sharc_harness.FRAME_PATCH_TABLE` (other lanes' files): it only drives the
existing replay path (`tools/sharc_dev/sharc_replay.write_dma_transfer`/
`drive_dma_completion`/`call_frame_collect_all_with_hits`, the last with
this lane's own opt-in `sample_fn` hook) with a wider breakpoint/sample set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc_capture  # noqa: E402
from tools.sharc_dev import sharc_harness as h  # noqa: E402
from tools.sharc_dev import sharc_replay as replay  # noqa: E402
from tools.sharc_dev import sharc_run as sr  # noqa: E402
from tools.sharc_dev import sharc_trace as st  # noqa: E402

# --- FUN_1c642a's per-voice dispatch (word/pc_sw addresses; see the module
# docstring for the decode each of these comes from). One breakpoint per
# instruction whose *result* (not just its own reachability) this module
# wants a register sample of: the breakpoint sits at the pc right AFTER the
# instruction that produced the value, since Runner.step() halts BEFORE
# decoding the pc it stops at (tools/sharc_dev/sharc_run.py's own Runner.step()
# docstring) -- so a hit's sample already reflects everything strictly
# before it, including the one instruction a caller cares about.
LOOP_TOP = 0x1C6530  # R2 = DM(I1 - 32); one hit per voice iteration
AFTER_LOAD1 = 0x1C6532  # after LOOP_TOP's own load: R2 = the raw field
GUARD_SKIP = 0x1C6538  # JUMP IF SV -> FALLTHRU_A (bypasses GUARD_A)
AFTER_R6_LOAD = 0x1C653C  # after R6 = DM(I4, M5)
GUARD_A = 0x1C6540  # JUMP IF SZ -> ARM_PREP
FALLTHRU_A = 0x1C6543  # M4 = 0xffa0 (GUARD_A not taken, or GUARD_SKIP was)
AFTER_LOAD2 = 0x1C6547  # after R2 = DM(I1, M4): R2 = the raw 2nd field
GUARD_B = 0x1C6549  # JUMP IF NOT SV -> ARM_PREP
DISPATCH_TOP = 0x1C654C  # neither guard taken: enters the jump-table body
COMPU = 0x1C6553  # docs/findings/06's own "compu(R6,6)" case start
TABLE_GUARD = 0x1C6561  # a second, jump-table-local conditional jump
ARM_PREP = 0x1C657B  # both guards' shared target: I4 = I15; ...; R4 = I4
ARM_CALL = 0x1C6582  # CALL FUN_1c4eaf, right before it executes
ARM_FN = 0x1C4EAF  # FUN_1c4eaf's own entry (I4 = R4)
ARM_WRITE_1BA = 0x1C4EB9  # DM(record+0x1ba) = M14
ARM_WRITE_1B8_OR_OTHER = 0x1C4EBB  # DM(record+0x1ba+M7) = M14

FUN_1C2AC9 = 0x1C2AC9  # the other FUN_1c4eaf caller
CALL_252730 = 0x1C2B0A  # FUN_1c2ac9's own first CALL FUN_1c4eaf (R4=0x252730)
CALL_252908 = 0x1C2B14  # FUN_1c2ac9's own second CALL FUN_1c4eaf (R4=0x252908)

TRIGGER_JUMP_TABLE = 0x8055C840  # docs/findings/06's own name for this table

VOICE_RECORD_STRIDE = h.VOICE_RECORD_STRIDE  # 0x1d8 bytes

ARM_PATH_PCS = frozenset(
    {
        LOOP_TOP,
        AFTER_LOAD1,
        GUARD_SKIP,
        AFTER_R6_LOAD,
        GUARD_A,
        FALLTHRU_A,
        AFTER_LOAD2,
        GUARD_B,
        DISPATCH_TOP,
        COMPU,
        TABLE_GUARD,
        ARM_PREP,
        ARM_CALL,
        ARM_FN,
        FUN_1C2AC9,
        CALL_252730,
        CALL_252908,
    }
)

# The registers/ASTATX bits worth a snapshot at every hit above -- chosen to
# cover both guards' own inputs (I1 the per-voice pointer, R2/R6 the values
# they test, SV/SZ the flags the jumps read) and the arm call's own argument
# (R4, I15) without dumping the whole register file.
_SAMPLE_REGS = ("R0", "R1", "R2", "R4", "R6", "I1", "I4", "I14", "I15", "M4", "M7")
_SAMPLE_BITS = (("SV", st.SV_BIT), ("SZ", st.SZ_BIT), ("SS", st.SS_BIT))


def _reg(state, name: str) -> int | None:
    """UREG NAME as a plain (unsigned 32-bit) int, or None if it is not a
    concrete Const right now (Unknown/Affine/PartialConst) -- see
    sharc_core.state._ureg()'s own docstring for why this, not
    ``state.uregs[code]`` directly, is the safe read."""
    value = st._ureg(state.uregs, st.UREG_CODES[name])
    return value.value & 0xFFFFFFFF if isinstance(value, st.Const) else None


def _astatx_bit(state, bit: int) -> bool | None:
    """ASTATX bit BIT (sharc_core.encoding's SV_BIT/SZ_BIT/SS_BIT), or None
    if that bit is not currently known (a PartialConst with the bit outside
    its known mask) -- read via ``_ureg_raw``, the only reader allowed to
    see a PartialConst (``sharc_core.state``'s own docstring)."""
    raw = st._ureg_raw(state.uregs, st.UREG_CODES["ASTATX"])
    if isinstance(raw, st.Const):
        return bool(raw.value & (1 << bit))
    mask = getattr(raw, "mask", 0)
    if (mask >> bit) & 1:
        return bool((raw.bits >> bit) & 1)
    return None


def _sample(runner: sr.Runner, pc: int) -> dict:
    """`sharc_replay.call_frame_collect_all_with_hits`'s own `sample_fn`
    hook: a register/ASTATX-flag snapshot taken right before PC's own
    instruction executes (see the module docstring's note on why the
    breakpoints sit one instruction after the value they sample)."""
    state = runner.state
    out: dict = {}
    for name in _SAMPLE_REGS:
        value = _reg(state, name)
        out[name] = "%#x" % value if value is not None else None
    for bitname, bit in _SAMPLE_BITS:
        out[bitname] = _astatx_bit(state, bit)
    return out


def _hexset(hits, pc: int) -> str:
    return "%#x" % pc


def analyze_iterations(hits: list[dict]) -> list[dict]:
    """Group HITS (oldest first, as `call_frame_collect_all_with_hits`
    returns them) into one record per `LOOP_TOP` hit -- one per voice
    iteration this frame's own dispatch loop ran.

    **`GUARD_SKIP` (0x1c6538, `JUMP IF SV`) is a *delayed* jump with TWO
    delay slots** -- confirmed by execution (`sharc_core.sequencer._transfer`
    sets `Pending(target, call=False, slots=2, ...)` for this exact form;
    a one-off probe of this pc, stepped instruction-by-instruction, showed
    `pending.slots` go 2 -> 1 -> resolved over the next two pc_sw values,
    `0x1c653b` then `0x1c653c`), **not** a compound/bundled instruction as
    an earlier version of this function's docstring guessed. Concretely:
    `AFTER_R6_LOAD` (0x1c653c, "IF NOT SV R2 = DM(I6-2)") is `GUARD_SKIP`'s
    own *second delay slot* -- it always executes, whether or not
    `GUARD_SKIP` is taken (delay slots are architecturally unconditional) --
    and only once it retires does a taken `GUARD_SKIP` redirect straight to
    `FALLTHRU_A` (0x1c6543), skipping `GUARD_A`'s own code (0x1c653e's
    `btgl` and the jump at 0x1c6540) entirely. So `AFTER_R6_LOAD`'s hit
    fires on *every* iteration regardless of `GUARD_SKIP`'s outcome, and
    that outcome itself only shows up in what pc follows AFTER_R6_LOAD's
    own hit: `FALLTHRU_A` means taken (GUARD_A's own code never ran this
    iteration), `GUARD_A` (0x1c6540, independently reachable and a plain
    *non-delayed* jump once actually reached) means not taken.

    `GUARD_A` and `GUARD_B` (0x1c6549, `JUMP IF NOT SV`, also non-delayed)
    are both ordinary reachable pc_sw values once their own code runs, so
    their own taken-vs-not-taken reads directly off the pc that follows
    their own hit."""
    loop_top_hex = _hexset(hits, LOOP_TOP)
    boundaries = [i for i, hit in enumerate(hits) if hit["pc"] == loop_top_hex]
    fallthru_a_hex = _hexset(hits, FALLTHRU_A)
    guard_a_hex = _hexset(hits, GUARD_A)
    arm_prep_hex = _hexset(hits, ARM_PREP)
    iterations = []
    for n, start in enumerate(boundaries):
        end = boundaries[n + 1] if n + 1 < len(boundaries) else len(hits)
        group = hits[start:end]
        pcs = [hit["pc"] for hit in group]
        after_r6_load_next = _next_pc_after(group, _hexset(hits, AFTER_R6_LOAD))
        guard_a_next = _next_pc_after(group, guard_a_hex)
        guard_b_next = _next_pc_after(group, _hexset(hits, GUARD_B))
        guard_skip_taken = (
            (after_r6_load_next == fallthru_a_hex)
            if after_r6_load_next is not None
            else None
        )
        guard_a_reached = guard_a_hex in pcs
        guard_a_taken = (guard_a_next == arm_prep_hex) if guard_a_reached else None
        iterations.append(
            {
                "iteration": n,
                "n_hits": len(group),
                "pcs": pcs,
                "loop_top_sample": group[0].get("sample") if group else None,
                "field1_sample": _sample_at(group, _hexset(hits, AFTER_LOAD1)),
                "guard_skip_sample": _sample_at(group, _hexset(hits, GUARD_SKIP)),
                "guard_skip_taken": guard_skip_taken,
                "after_r6_load_sample": _sample_at(group, _hexset(hits, AFTER_R6_LOAD)),
                "guard_a_reached": guard_a_reached,
                "guard_a_sample": _sample_at(group, guard_a_hex),
                "guard_a_taken": guard_a_taken,
                "field2_sample": _sample_at(group, _hexset(hits, AFTER_LOAD2)),
                "guard_b_sample": _sample_at(group, _hexset(hits, GUARD_B)),
                "guard_b_taken": (
                    guard_b_next == arm_prep_hex if guard_b_next is not None else None
                ),
                "reached_dispatch_top": _hexset(hits, DISPATCH_TOP) in pcs,
                "reached_arm_prep": arm_prep_hex in pcs,
                "reached_arm_call": _hexset(hits, ARM_CALL) in pcs,
                "arm_call_sample": _sample_at(group, _hexset(hits, ARM_CALL)),
                "reached_arm_fn": _hexset(hits, ARM_FN) in pcs,
            }
        )
    return iterations


def _next_pc_after(group: list[dict], pc_hex: str) -> str | None:
    for i, hit in enumerate(group):
        if hit["pc"] == pc_hex and i + 1 < len(group):
            return group[i + 1]["pc"]
    return None


def _sample_at(group: list[dict], pc_hex: str) -> dict | None:
    for hit in group:
        if hit["pc"] == pc_hex:
            return hit.get("sample")
    return None


def trace_arm_path(
    image: str,
    capture_path: str,
    *,
    n_frames: int | None = None,
    start_frame: int = 0,
    max_hits_per_frame: int = 8000,
) -> dict:
    """Replay CAPTURE_PATH's real DSPI2 frames through the real hardware
    landing zone (`sharc_harness.write_dma_transfer`/`drive_dma_completion`,
    the same delivery `tools/sharc_dev/sharc_replay.replay()` uses -- lane G1,
    2026-09-26) with one hand-set-up voice (voice 0, `sharc_harness.
    setup_voice`, the same convention every other replay in this project
    uses so results are directly comparable), and record, per frame, every
    hit on `ARM_PATH_PCS` with a register/ASTATX sample at each
    (`_sample()`), grouped into per-voice-iteration records
    (`analyze_iterations()`).

    `start_frame` (Lane J1, 2026-09-26) skips the first `start_frame`
    captured frames without replaying them through the SHARC at all --
    just `write_dma_transfer`/`drive_dma_completion` are not even called
    for them. This only saves time when the frames being skipped are known
    not to matter for the question at hand (e.g. bracketing a panel event
    deep into a long idle capture, as `unicorn-reference-final:tools/sharc_capture_run.py --kind
    note`'s default `trig_at` does): every companding-record/arm-guard
    finding this project has made (docs/findings/06, Lane E2/H1-H3) shows
    those cells depend only on `command_word_shift_src` (toggled by a
    transfer-complete DMA callback, unrelated to any frame's own content)
    and on a per-track bitfield-unpack function (`FUN_1c24e9`) that reads
    each frame fresh with no cross-frame accumulation of the fields this
    lane's own diff touched -- so replaying from an arbitrary frame index
    is equivalent to replaying from 0 for this specific question, at a
    fraction of the cost. `per_frame[i]["index"]` is still the frame's real
    index in CAPTURE_PATH (`start_frame + i`), not a re-based 0.

    Returns a dict with `per_frame`, each entry's own `iterations` list
    naming, per voice, whether `GUARD_A`/`GUARD_B` fired and what pc the
    dispatch reached instead, plus whether `FUN_1c4eaf` (the arm function)
    or `FUN_1c2ac9` (the OTHER caller, fixed non-voice targets -- see the
    module docstring) ran at all this frame."""
    cap = sharc_capture.load(capture_path)
    frames = cap.dspi2_frames[start_frame:]
    if n_frames is not None:
        frames = frames[:n_frames]

    memory = h.load_image_memory(image)
    init = h.run_init(memory, image)
    if not init.ran:
        return {
            "capture": capture_path,
            "frames_in_capture": len(cap.dspi2_frames),
            "frames_replayed": 0,
            "error": "run_init failed: %s" % init.error,
        }

    p = h.profile(image)
    runner = h.new_runner(memory, image, init=init)
    state = runner.state
    h.setup_voice(state, image, voice=0, sample_len=4096)
    h.setup_frame_dma(state, image)

    per_frame = []
    for offset, frame in enumerate(frames):
        idx = start_frame + offset
        cmd = replay.frame_command(frame.tx)
        h.write_dma_transfer(state, image, frame.tx)
        runner = h.drive_dma_completion(runner, image)
        state = runner.state

        runner, result, hits = replay.call_frame_collect_all_with_hits(
            runner,
            image,
            trace_pcs=ARM_PATH_PCS,
            patch_table=h.FRAME_PATCH_TABLE,
            max_hits=max_hits_per_frame,
            sample_fn=_sample,
        )
        state = runner.state
        terminal = result.terminal
        iterations = analyze_iterations(hits)
        companding_fields = h.companding_record_fields(state, image)
        frame_voices_active = h.scan_voice_active(state, image, exclude=(0,))
        per_frame.append(
            {
                "index": idx,
                "command": cmd,
                "stop_reason": terminal.category,
                "stop_pc": "%#x" % terminal.pc,
                "instructions": result.instructions,
                "n_hits": len(hits),
                "n_iterations": len(iterations),
                "iterations": iterations,
                "any_guard_a_taken": any(it["guard_a_taken"] for it in iterations),
                "any_guard_b_taken": any(it["guard_b_taken"] for it in iterations),
                "any_arm_fn_hit": any(it["reached_arm_fn"] for it in iterations),
                "fun_1c2ac9_hits": sum(
                    1 for hit in hits if hit["pc"] == _hexset(hits, FUN_1C2AC9)
                ),
                "call_252730_hits": sum(
                    1 for hit in hits if hit["pc"] == _hexset(hits, CALL_252730)
                ),
                "call_252908_hits": sum(
                    1 for hit in hits if hit["pc"] == _hexset(hits, CALL_252908)
                ),
                "companding_fields": ["%#x" % v for v in companding_fields],
                "other_voices_active_by_firmware": frame_voices_active,
            }
        )

    return {
        "capture": capture_path,
        "kind": cap.kind,
        "frames_in_capture": len(cap.dspi2_frames),
        "start_frame": start_frame,
        "frames_replayed": len(frames),
        "voice_records": "%#x" % p.voice_records,
        "voice_record_stride": "%#x" % VOICE_RECORD_STRIDE,
        "per_frame": per_frame,
        "any_guard_a_taken": any(f["any_guard_a_taken"] for f in per_frame),
        "any_guard_b_taken": any(f["any_guard_b_taken"] for f in per_frame),
        "any_arm_fn_hit": any(f["any_arm_fn_hit"] for f in per_frame),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("image")
    parser.add_argument("capture")
    parser.add_argument("--frames", type=int, default=None)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--report", help="write the full trace as JSON")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    result = trace_arm_path(
        args.image, args.capture, n_frames=args.frames, start_frame=args.start_frame
    )
    if args.report:
        with open(args.report, "w") as fh:
            json.dump(result, fh, indent=1)
    print(
        "%s: %d/%d frame(s), any_guard_a_taken=%s, any_guard_b_taken=%s, "
        "any_arm_fn_hit=%s"
        % (
            args.capture,
            result.get("frames_replayed", 0),
            result.get("frames_in_capture", 0),
            result.get("any_guard_a_taken"),
            result.get("any_guard_b_taken"),
            result.get("any_arm_fn_hit"),
        )
    )
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
