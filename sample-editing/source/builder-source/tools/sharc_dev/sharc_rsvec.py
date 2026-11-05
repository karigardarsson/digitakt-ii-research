"""Capture block test vectors for the native SHARC+ core from a replay.

Replays a DSPI2 capture through the Python reference (tools/sharc_dev/sharc_core,
driven the way tools/sharc_dev/sharc_replay.replay() drives it) and, for each
listed block, records up to N entries: the machine state at the block
entry, every memory byte the block reads before writing it, and the state,
written bytes, exit PC and instruction count when control leaves the block
(a native DO-loop back edge to the block start stays inside). The Rust
side (packages/emulator/sharc, sharc-check and sharc-bench) loads these with
sharc_native::vectors. The file is firmware-derived: write it under out/.

    tools/sharc_dev/sharc_rsvec.py dt2-1.16 CAPTURE --blocks-from out/native/gen/report.json \\
        --frames 1-4 --samples 3 --out out/native/vectors.txt
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))

NUREG = 128


def _value(v):
    """(bits, mask) of a reference register value; mask 0 when unknown."""
    from tools.sharc_dev.sharc_core.values import Const, PartialConst

    if isinstance(v, Const):
        return v.value, 0xFFFFFFFF
    if isinstance(v, PartialConst):
        return v.bits, v.mask
    return 0, 0


def snapshot(state) -> dict:
    from tools.sharc_dev.sharc_core.encoding import UREG_CODES

    regs = {}
    for code in range(NUREG):
        bits, mask = _value(state.uregs.get(code))
        regs[code] = (bits, mask)
    return {
        "regs": regs,
        "astat": [regs[UREG_CODES["ASTATX"]], regs[UREG_CODES["ASTATY"]]],
        "loops": [
            (lp.start_sw, lp.end_sw, lp.remaining, lp.mode) for lp in state.loops
        ],
        "pcstk": list(state.call_stack),
    }


def snapshot_lines(snap: dict) -> list[str]:
    lines = []
    for code, (bits, mask) in snap["regs"].items():
        lines.append("R %d %x" % (code, bits) if mask == 0xFFFFFFFF else "X %d" % code)
    (ax, axm), (ay, aym) = snap["astat"]
    lines.append("A %x %x %x %x" % (ax, axm, ay, aym))
    lines += ["L %x %x %d %d" % loop for loop in snap["loops"]]
    lines.append("C" + "".join(" %x" % pc for pc in snap["pcstk"]))
    return lines


def byte_runs(data: dict[int, int]) -> list[tuple[int, bytes]]:
    runs: list[tuple[int, bytearray]] = []
    for addr in sorted(data):
        if runs and runs[-1][0] + len(runs[-1][1]) == addr:
            runs[-1][1].append(data[addr])
        else:
            runs.append((addr, bytearray([data[addr]])))
    return [(a, bytes(b)) for a, b in runs]


class Recorder:
    """Watches sharc_trace._execute and the DM accessors."""

    def __init__(self, blocks: dict[int, list[int]], samples: int, max_insns: int):
        self.blocks = blocks  # start -> instruction PCs
        self.samples = samples
        self.max_insns = max_insns
        self.counts = {b: 0 for b in blocks}
        self.active: dict | None = None
        self.enabled = False
        self.vectors: list[list[str]] = []
        self.aborted: dict[str, int] = {}

    # memory hooks ------------------------------------------------------
    def on_read(self, address, width, value) -> None:
        rec = self.active
        if rec is None:
            return
        from tools.sharc_dev.sharc_core.values import Const

        addr = address.value if isinstance(address, Const) else address
        if not isinstance(addr, int) or value is None:
            rec["bad"] = "unreadable memory"
            return
        raw = value.value & ((1 << (8 * width)) - 1)
        for k in range(width):
            a = addr + k
            if a not in rec["touched"]:
                rec["touched"].add(a)
                rec["mem"][a] = (raw >> (8 * k)) & 0xFF

    def on_write(self, address, width, value, ok) -> None:
        rec = self.active
        if rec is None:
            return
        from tools.sharc_dev.sharc_core.values import Const

        addr = address.value if isinstance(address, Const) else address
        if not ok or not isinstance(addr, int) or not isinstance(value, Const):
            rec["bad"] = "dropped store"
            return
        raw = value.value & 0xFFFFFFFF
        for k in range(width):
            rec["touched"].add(addr + k)
            rec["writes"][addr + k] = (raw >> (8 * k)) & 0xFF

    # step hooks --------------------------------------------------------
    def before(self, state) -> None:
        if not self.enabled or self.active is not None:
            return
        pc = state.pc_sw
        if pc not in self.blocks or state.pending is not None:
            return
        if self.counts[pc] >= self.samples:
            return
        self.active = {
            "block": pc,
            "pcs": self.blocks[pc],
            "entry": snapshot(state),
            "mem": {},
            "writes": {},
            "touched": set(),
            "n": 0,
            "bad": None,
        }

    def after(self, pc_before: int, out) -> None:
        rec = self.active
        if rec is None:
            return
        if len(out) != 1 or out[0].stopped:
            self.abort("fork or stop")
            return
        state = out[0]
        rec["n"] += 1
        pcs = rec["pcs"]
        k = pcs.index(pc_before) if pc_before in pcs else -1
        nxt = state.pc_sw
        if k < 0:
            self.abort("left the block")
            return
        stay = (k < len(pcs) - 1 and nxt == pcs[k + 1]) or (
            k == len(pcs) - 1
            and nxt == rec["block"]
            and state.loops
            and state.loops[-1].start_sw == rec["block"]
        )
        if stay and rec["n"] < self.max_insns:
            return
        if stay:
            self.abort("too long")
            return
        if rec["bad"]:
            self.abort(rec["bad"])
            return
        lines = ["V %x %d" % (rec["block"], self.counts[rec["block"]])]
        lines += snapshot_lines(rec["entry"])
        lines += ["M %x %s" % (a, b.hex()) for a, b in byte_runs(rec["mem"])]
        lines.append("E")
        lines += snapshot_lines(snapshot(state))
        lines += ["W %x %s" % (a, b.hex()) for a, b in byte_runs(rec["writes"])]
        lines.append(
            "P %x %x %d" % (nxt, rec["n"], 1 if state.pending is not None else 0)
        )
        lines.append("END")
        self.vectors.append(lines)
        self.counts[rec["block"]] += 1
        self.active = None

    def abort(self, why: str) -> None:
        self.aborted[why] = self.aborted.get(why, 0) + 1
        self.active = None


def install(rec: Recorder):
    """Wrap sharc_trace._execute and every sharc_core module's DM accessors."""
    from tools.sharc_dev import sharc_core
    from tools.sharc_dev import sharc_trace as st
    from tools.sharc_dev.sharc_core import memory

    orig_exec = st._execute
    orig_read, orig_write = memory._dm_read, memory._dm_write

    def execute(state, insn):
        pc = state.pc_sw
        rec.before(state)
        out = orig_exec(state, insn)
        rec.after(pc, out)
        return out

    def dm_read(state, address, width, signed=False):
        value = orig_read(state, address, width, signed)
        rec.on_read(address, width, value)
        return value

    def dm_write(state, address, width, value):
        ok = orig_write(state, address, width, value)
        rec.on_write(address, width, value, ok)
        return ok

    st._execute = execute
    for name, module in list(sys.modules.items()):
        if name == "tools.sharc_dev.sharc_core" or name.startswith(
            "tools.sharc_dev.sharc_core."
        ):
            if getattr(module, "_dm_read", None) is orig_read:
                module._dm_read = dm_read
            if getattr(module, "_dm_write", None) is orig_write:
                module._dm_write = dm_write
    del sharc_core


def parse_frames(spec: str) -> range:
    lo, _, hi = spec.partition("-")
    return range(int(lo), int(hi or lo) + 1)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("image")
    p.add_argument("capture", help=".dt2cap DSPI2 capture")
    p.add_argument("--blocks", default="", help="comma-separated hex block starts")
    p.add_argument(
        "--blocks-from",
        help="tools/sharc_dev/sharc_rsgen.py report.json: its translated blocks",
    )
    p.add_argument(
        "--frames", default="1-3", help="frames to record in (others run unrecorded)"
    )
    p.add_argument("--samples", type=int, default=3, help="vectors per block")
    p.add_argument("--max-insns", type=int, default=2_000_000)
    p.add_argument("--lp0", help="FlexBus log to feed over LP0 first (slow)")
    p.add_argument("--out", default="out/native/vectors.txt")
    args = p.parse_args(argv)

    from tools.sharc_dev import sharc_capture
    from tools.sharc_dev import sharc_harness as h
    from tools.sharc_dev import sharc_lp0 as lp0
    from tools.sharc_dev import sharc_survey as sv
    from tools.sharc_dev.sharc_core.sequencer import decode_at

    starts = [int(x, 16) for x in args.blocks.split(",") if x]
    if args.blocks_from:
        with open(args.blocks_from) as fh:
            starts += [b["start"] for b in json.load(fh)["blocks"]]
    memory = h.load_image_memory(args.image)
    import sqlite3

    con = sqlite3.connect(os.path.join("out", "sharcdb", args.image + ".sqlite"))
    blocks = {}
    for start in starts:
        (end,) = con.execute(
            "select end_sw from bblocks where image=? and start_sw=?",
            (args.image, start),
        ).fetchone()
        pcs, pc = [], start
        while pc < end:
            insn = decode_at(memory, None, pc)
            pcs.append(pc)
            pc += insn.length_bytes // 2
        blocks[start] = pcs
    rec = Recorder(blocks, args.samples, args.max_insns)

    t0 = time.perf_counter()
    init = h.run_init(memory, args.image)
    if not init.ran:
        raise SystemExit("run_init failed: %s" % init.error)
    runner = h.new_runner(memory, args.image, init=init)
    state = runner.state
    sample_base, sample_len = 0x310000, 4096
    tone = [
        math.sin(2 * math.pi * (1000.0 / h.SOURCE_SAMPLE_RATE) * i)
        for i in range(sample_len)
    ]
    h._write_samples(state, sample_base, tone, "int16")
    h.setup_voice(
        state, args.image, voice=0, sample_len=sample_len, sample_base=sample_base
    )
    h.setup_frame_dma(state, args.image, ring_flag=0)
    if args.lp0:
        runner, _ = lp0.feed(runner, args.image, list(lp0.read_log(args.lp0)))
        state = runner.state
    print("setup %.1fs" % (time.perf_counter() - t0), flush=True)
    install(rec)
    frames = parse_frames(args.frames)
    cap = sharc_capture.load(args.capture)
    for idx, frame in enumerate(cap.dspi2_frames[: frames.stop]):
        h.write_dma_transfer(state, args.image, frame.tx)
        runner = h.drive_dma_completion(runner, args.image)
        new_runner = runner.fresh_call(h.profile(args.image).block_handler)
        rec.enabled = idx in frames
        t = time.perf_counter()
        result = sv.run_collect_all(
            new_runner, h.FRAME_PATCH_TABLE, 4_000_000, img=None
        )
        rec.enabled = False
        rec.active = None
        runner, state = new_runner, new_runner.state
        print(
            "frame %d: %d instructions, %s, %.1fs, %d vectors"
            % (
                idx,
                result.instructions,
                result.terminal.category,
                time.perf_counter() - t,
                len(rec.vectors),
            ),
            flush=True,
        )
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        for lines in rec.vectors:
            fh.write("\n".join(lines) + "\n")
    missing = [hex(b) for b, c in rec.counts.items() if c == 0]
    print(
        "%d vectors for %d blocks; aborted %s; no entry: %s"
        % (
            len(rec.vectors),
            sum(1 for c in rec.counts.values() if c),
            rec.aborted,
            missing,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
