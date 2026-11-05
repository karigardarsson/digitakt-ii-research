"""Attach pc breakpoints at a list of function entries during a
tools/sharc_dev/sharc_replay.py-style continuous replay, and log every hit's own
registers, stack args and (once the matching call returns) its R0 return
value.

    uv run python -m tools.sharc_dev.sharc_calltrace dt2-1.16 CAPTURE.dt2cap \
        [--function HEX ...] [--frames A:B] [--json]

The default entry list (docs/findings/06, tools/sharc_dev/sharc_armpath.py) is:

    0x1c7442  0x1c4e70  0x1c4eaf  0x1c3289
    0x1c60a2  0x1c642a  0x1c4ecf  0x1c4f81

Pass one or more --function HEX to trace a different (or narrower) set --
this replaces the default list rather than adding to it, both for which
pcs get breakpoints attached AND for which hits are reported.

For each hit at one of these pcs (an ordinary CALL target, reached the
normal way -- `sequencer.py` pushes the return address onto
`state.call_stack` at the CALL itself, before the callee's first
instruction, so a hit's own `state.call_stack[-1]` is exactly this call's
return address, not a stale one from an outer call) this tool records:

    frame, instructions (this frame's own running count at the hit),
    pc, R0-R3, R4, R8, R12, I6, and the first four stack args the caller
    pushed -- DM(I6+1)..DM(I6+4), each a 4-byte normal-word read scaled by
    sharc_core.memory._access_modifier_scale("normal-word", assume_nw32=True)
    (== 4 under this tracer's default assume_nw32=True), i.e. byte
    addresses I6+4, I6+8, I6+12, I6+16.

A second, dynamically-grown breakpoint set watches for that same call's
own return address; once reached, the ORIGINAL hit record (matched
last-in-first-out per return address, correct for nested/recursive calls
to the same target) gets its own `return_value` (R0 at the return pc,
before that pc's own instruction executes) and `return_instructions`
filled in. A call that never returns before its own frame ends (or before
--max-hits) simply keeps `return_value: None`.

Like tools/sharc_dev/sharc_armpath.py, this tool never edits sharc_core, emu/, or
any other lane's tools/sharc_dev/sharc_harness.FRAME_PATCH_TABLE: it only drives
tools/sharc_dev/sharc_replay.py's existing write_dma_transfer()/
drive_dma_completion() delivery and tools/sharc_dev/sharc_survey.run_collect_all()
with its own breakpoint set.
"""

from __future__ import annotations

import sys


def _maybe_reexec_under_pypy() -> None:
    """See tools/sharc_dev/sharc_memdiff.py's own copy of this function for the full
    rationale; kept as a small, deliberate duplicate rather than a new
    shared module, since this lane owns exactly these two CLI files."""
    if "--cpython" in sys.argv[1:]:
        return
    if sys.implementation.name == "pypy":
        return
    import os
    import shutil

    if shutil.which("uv") is None:
        return
    from tools import python_env

    cmd = [
        "uv",
        "run",
        "--no-project",
        "--python",
        "pypy3.11",
        "--with",
        "networkx",
        "python",
        "-m",
        "tools.sharc_dev.sharc_calltrace",
        *sys.argv[1:],
    ]
    try:
        os.execvpe(cmd[0], cmd, python_env())
    except OSError as exc:
        print(
            "sharc_calltrace: PyPy re-exec failed (%s), running as-is" % exc,
            file=sys.stderr,
        )


if __name__ == "__main__":
    _maybe_reexec_under_pypy()

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc_capture  # noqa: E402
from tools.sharc_dev import sharc_harness as h  # noqa: E402
from tools.sharc_dev import sharc_replay as replay  # noqa: E402
from tools.sharc_dev import sharc_run as sr  # noqa: E402
from tools.sharc_dev import sharc_survey as sv  # noqa: E402
from tools.sharc_dev import sharc_trace as st  # noqa: E402

DEFAULT_ENTRY_PCS: tuple[int, ...] = (
    0x1C7442,
    0x1C4E70,
    0x1C4EAF,
    0x1C3289,
    0x1C60A2,
    0x1C642A,
    0x1C4ECF,
    0x1C4F81,
)

_SAMPLE_REGS = ("R0", "R1", "R2", "R3", "R4", "R8", "R12", "I6")


def _reg(state, name: str) -> int | None:
    """UREG NAME as a plain unsigned 32-bit int, or None if it is not
    currently a concrete Const (tools/sharc_dev/sharc_armpath.py's own `_reg()`)."""
    value = st._ureg(state.uregs, st.UREG_CODES[name])
    return value.value & 0xFFFFFFFF if isinstance(value, st.Const) else None


def _stack_args(state, i6: int | None) -> list[int | None]:
    """DM(I6+1)..DM(I6+4), each a 4-byte normal-word read (see the module
    docstring for the x4 scale) -- the first four stack args as the caller
    pushed them, or four Nones if I6 is not currently concrete."""
    if i6 is None:
        return [None, None, None, None]
    out: list[int | None] = []
    for k in (1, 2, 3, 4):
        value = st._dm_read(state, i6 + k * 4, 4)
        out.append(value.value & 0xFFFFFFFF if value is not None else None)
    return out


def _hex_or_none(value: int | None) -> str | None:
    return None if value is None else "%#x" % value


def _sample_entry(state) -> dict:
    regs = {name: _reg(state, name) for name in _SAMPLE_REGS}
    return {
        "regs": {name: _hex_or_none(value) for name, value in regs.items()},
        "stack_args": [_hex_or_none(v) for v in _stack_args(state, regs.get("I6"))],
    }


def _run_frame_calltrace(
    runner: sr.Runner,
    image: str,
    entry_pcs: frozenset[int],
    patch_table: sv.PatchTable,
    *,
    max_steps: int = 4_000_000,
    max_events: int = 4000,
) -> tuple[sr.Runner, sv.CollectAllResult, list[dict]]:
    """Run one frame's own block_handler call (a fresh_call() off RUNNER,
    like tools/sharc_dev/sharc_replay.call_frame_collect_all_with_hits()) with
    ENTRY_PCS attached as breakpoints, plus every entry hit's own return
    address (state.call_stack[-1] at the moment of the hit -- see the
    module docstring), added to the breakpoint set as soon as it is known
    and removed once every pending call to that address has returned.

    Returns (runner, result, events): RUNNER is the fresh_call runner
    positioned at the frame's true terminal (never at a breakpoint --
    every hit is stepped past before resuming, the same convention
    call_frame_collect_all_with_hits uses); RESULT is that terminal leg's
    own CollectAllResult; EVENTS is one dict per entry hit, oldest first,
    each already carrying its own `return_value`/`return_instructions`
    once (if) the matching return was reached within this same frame."""
    p = h.profile(image)
    new_runner = runner.fresh_call(p.block_handler, diagnose_unknown=True)
    breakpoints = set(entry_pcs)
    pending: dict[int, list[dict]] = {}
    events: list[dict] = []
    remaining = max_steps
    result = None
    while remaining > 0 and len(events) < max_events:
        new_runner.breakpoints = frozenset(breakpoints)
        result = sv.run_collect_all(new_runner, patch_table or {}, remaining, img=None)
        terminal = result.terminal
        if terminal.category != "breakpoint":
            break
        pc = terminal.pc
        state = new_runner.state
        if pc in entry_pcs:
            sample = _sample_entry(state)
            entry = {
                "pc": "%#x" % pc,
                "instructions": new_runner.instructions,
                "regs": sample["regs"],
                "stack_args": sample["stack_args"],
                "return_value": None,
                "return_instructions": None,
            }
            events.append(entry)
            return_pc = state.call_stack[-1] if state.call_stack else None
            if return_pc is not None:
                pending.setdefault(return_pc, []).append(entry)
                breakpoints.add(return_pc)
        else:
            waiting = pending.get(pc)
            if waiting:
                matching = waiting.pop()
                matching["return_value"] = _hex_or_none(_reg(state, "R0"))
                matching["return_instructions"] = new_runner.instructions
                if not waiting:
                    del pending[pc]
                    if pc not in entry_pcs:
                        breakpoints.discard(pc)
            # else: a breakpoint fired with no entry or pending-return match
            # (should not happen -- breakpoints only ever holds entry_pcs
            # and pcs this loop itself added); ignore and keep going.
        saved = new_runner.breakpoints
        new_runner.breakpoints = frozenset()
        try:
            new_runner.step()
        except sr.Halt as exc:
            events.append({"halt": str(exc)})
            return new_runner, result, events
        new_runner.breakpoints = saved
        remaining = max_steps - new_runner.instructions
    return new_runner, result, events


def _setup_runner(image: str, tone_freq: float, sample_len: int) -> sr.Runner:
    """Same post-init setup as tools/sharc_dev/sharc_memdiff.py's own copy (and
    tools/sharc_dev/sharc_replay.replay()'s): one hand-set-up voice 0 with a --freq
    Hz sine."""
    memory = h.load_image_memory(image)
    init = h.run_init(memory, image)
    if not init.ran:
        raise RuntimeError("run_init failed: %s" % init.error)
    runner = h.new_runner(memory, image, init=init)
    state = runner.state
    sample_base = 0x310000
    tone = [
        math.sin(2 * math.pi * (tone_freq / h.SOURCE_SAMPLE_RATE) * i)
        for i in range(sample_len)
    ]
    h._write_samples(state, sample_base, tone, "int16")
    h.setup_voice(state, image, voice=0, sample_len=sample_len, sample_base=sample_base)
    h.setup_frame_dma(state, image, ring_flag=0)
    return runner


def calltrace(
    image: str,
    capture_path: str,
    *,
    entry_pcs: tuple[int, ...] = DEFAULT_ENTRY_PCS,
    frame_lo: int = 0,
    frame_hi: int | None = None,
    tone_freq: float = 1000.0,
    sample_len: int = 4096,
    max_events_per_frame: int = 4000,
) -> dict:
    """Replay CAPTURE_PATH continuously from frame 0 (real DMA delivery,
    tools/sharc_dev/sharc_harness.FRAME_PATCH_TABLE) through frame_hi (or the whole
    capture if None), reporting only frames in [frame_lo, frame_hi]
    (inclusive) -- execution is never skipped ahead of frame_lo, only
    reporting is windowed, same convention as tools/sharc_dev/sharc_memdiff.py."""
    cap = sharc_capture.load(capture_path)
    n_avail = len(cap.dspi2_frames)
    n_exec = n_avail if frame_hi is None else min(n_avail, frame_hi + 1)
    entry_pc_set = frozenset(entry_pcs)

    runner = _setup_runner(image, tone_freq, sample_len)
    per_frame = []
    total_hits = 0
    frames_executed = 0
    for idx in range(n_exec):
        frame = cap.dspi2_frames[idx]
        cmd = replay.frame_command(frame.tx)
        h.write_dma_transfer(runner.state, image, frame.tx)
        runner = h.drive_dma_completion(runner, image)
        runner, result, events = _run_frame_calltrace(
            runner,
            image,
            entry_pc_set,
            h.FRAME_PATCH_TABLE,
            max_events=max_events_per_frame,
        )
        frames_executed = idx + 1

        if idx < frame_lo:
            continue

        for event in events:
            event["frame"] = idx
        terminal = result.terminal
        per_frame.append(
            {
                "frame": idx,
                "command": cmd,
                "stop_reason": terminal.category,
                "stop_pc": "%#x" % terminal.pc,
                "instructions": result.instructions,
                "events": events,
            }
        )
        total_hits += len(events)

    return {
        "image": image,
        "capture": capture_path,
        "entry_pcs": ["%#x" % pc for pc in entry_pcs],
        "frame_window": [frame_lo, frame_hi],
        "frames_executed": frames_executed,
        "total_hits": total_hits,
        "per_frame": per_frame,
    }


def _print_summary(result: dict) -> None:
    print(
        "%s: %d frame(s) executed, window=%s, entries=%s, total_hits=%d"
        % (
            result["capture"],
            result["frames_executed"],
            result["frame_window"],
            ",".join(result["entry_pcs"]),
            result["total_hits"],
        )
    )
    for f in result["per_frame"]:
        if not f["events"]:
            continue
        print(
            "  frame %d (cmd=%s, stop=%s@%s, %d instr):"
            % (
                f["frame"],
                f["command"],
                f["stop_reason"],
                f["stop_pc"],
                f["instructions"],
            )
        )
        for e in f["events"]:
            if "halt" in e:
                print("    HALT: %s" % e["halt"])
                continue
            r = e["regs"]
            print(
                "    pc=%s instr=%d R0=%s R1=%s R2=%s R3=%s R4=%s R8=%s R12=%s I6=%s "
                "args=%s return=%s@instr=%s"
                % (
                    e["pc"],
                    e["instructions"],
                    r["R0"],
                    r["R1"],
                    r["R2"],
                    r["R3"],
                    r["R4"],
                    r["R8"],
                    r["R12"],
                    r["I6"],
                    e["stack_args"],
                    e["return_value"],
                    e["return_instructions"],
                )
            )


def _parse_frames(spec: str | None) -> tuple[int, int | None]:
    if not spec:
        return 0, None
    a_str, _, b_str = spec.partition(":")
    return int(a_str), int(b_str)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("image")
    p.add_argument("capture")
    p.add_argument(
        "--function",
        action="append",
        default=[],
        metavar="HEX",
        help="restrict to this function entry pc (repeatable); default: "
        "the 8 built-in entries (see the module docstring)",
    )
    p.add_argument(
        "--frames",
        metavar="A:B",
        help="only report hits in frames A..B (inclusive); the capture is "
        "still executed continuously from frame 0 through B",
    )
    p.add_argument("--max-events", type=int, default=4000, help="per-frame hit cap")
    p.add_argument("--json", action="store_true")
    p.add_argument("--report", help="write the full trace as JSON")
    p.add_argument("--freq", type=float, default=1000.0)
    p.add_argument("--sample-len", type=int, default=4096)
    p.add_argument(
        "--cpython",
        action="store_true",
        help="skip the PyPy re-exec and run under the interpreter that launched this",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    entry_pcs = (
        tuple(int(f, 16) for f in args.function) if args.function else DEFAULT_ENTRY_PCS
    )
    frame_lo, frame_hi = _parse_frames(args.frames)
    start = time.perf_counter()
    result = calltrace(
        args.image,
        args.capture,
        entry_pcs=entry_pcs,
        frame_lo=frame_lo,
        frame_hi=frame_hi,
        tone_freq=args.freq,
        sample_len=args.sample_len,
        max_events_per_frame=args.max_events,
    )
    elapsed = time.perf_counter() - start
    result["elapsed_seconds"] = elapsed
    result["seconds_per_frame"] = (
        elapsed / result["frames_executed"] if result["frames_executed"] else None
    )
    if args.report:
        with open(args.report, "w") as fh:
            json.dump(result, fh, indent=1)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        _print_summary(result)
        print(
            "  %d frame(s), %.3fs total, %.3fs/frame"
            % (result["frames_executed"], elapsed, result["seconds_per_frame"] or 0.0)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
