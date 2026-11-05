"""Replay a DN2 capture's DSPI2 frames through the native SHARC+ core.

Per frame: the SPI2 exchange, one `--gap` instruction run, one SPORT4 block.
It reports the instructions run, how many ran in generated blocks and how many
the idle skip replayed, the wall time (also for the `--meas` frame range), the
SHA-256 of the canonical state at the `--check` frames and of all PCM, so two
builds or two configurations compare byte for byte. `--profile PREFIX` turns
on the interpreter profile (native option 26) and writes PREFIX (coverage),
PREFIX.entries, PREFIX.trans and PREFIX.exits, the inputs of
`tools/sharc_dev/sharc_rsgen.py --coverage/--entries/--transitions`.

    uv run python -m tools.sharc_dev.sharc_dn2_replay --lib LIB --state STATE.bin \\
        --capture NOTE.dt2cap --out result.json [--no-blocks] [--no-skip]
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import time
from pathlib import Path

from tools.sharc_dev import sharc_capture as cap  # noqa: E402
from tools.sharc_dev import sharc_diff as sd  # noqa: E402
from tools.sharc_dev import sharc_run as sr  # noqa: E402
from tools.sharc_dev import sharc_transpile_run as nr  # noqa: E402

# The diagnostic run configuration of the DN2 audio replays (see
# docs/findings/07-emulator.md): runtime decoding, instruction clock, software
# IRQs, the core timer, the peripheral model and an unmodeled-MMR-tolerant
# memory model. Bank and stack models come from the state blob.
CLOCK_BASE = 573627620


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--lib", default=nr.DEFAULT_LIB, help="libsharc_native")
    p.add_argument("--image", default="dn2-1.11", help="program database image")
    p.add_argument("--state", required=True, help="canonical state to start from")
    p.add_argument("--capture", required=True, help=".dt2cap with DSPI2 frames")
    p.add_argument("--out", required=True, help="result JSON")
    p.add_argument("--start", type=int, default=4600, help="first frame")
    p.add_argument("--end", type=int, default=5600, help="last frame")
    p.add_argument("--check", default="5599", help="frames to hash the state at")
    p.add_argument("--meas", default="5400,5600", help="frame range timed [a,b)")
    p.add_argument("--gap", type=int, default=667000, help="instructions per frame")
    p.add_argument("--clock-base", type=int, default=CLOCK_BASE)
    p.add_argument("--no-blocks", action="store_true", help="interpreter only")
    p.add_argument("--no-skip", action="store_true", help="no idle-loop skip")
    p.add_argument("--profile", default="", help="write the interpreter profile")
    p.add_argument("--pcm", default="", help="write the PCM bytes here")
    return p.parse_args(argv)


def raw_state(core: nr.NativeCore) -> bytes:
    """The canonical state with every memory byte, as exported."""
    core.set_option(nr.OPT_EXPORT_RANGES, 1)
    cap_ = 1 << 24
    while True:
        buf = ctypes.create_string_buffer(cap_)
        n = core._lib.sharc_native_export_state(core._handle, buf, cap_)
        if n >= 0:
            break
        cap_ = -n
    core.set_option(nr.OPT_EXPORT_RANGES, 0)
    return buf.raw[:n]


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    check = {int(x) for x in a.check.split(",") if x}
    meas = tuple(int(x) for x in a.meas.split(","))
    image = sr._load_image_memory(a.image)
    core = nr.NativeCore(nr.pack_image(image), a.lib)
    core.load_state(sd.unpack_state(Path(a.state).read_bytes()))
    options = [
        (nr.OPT_RUNTIME_DECODE, 1),
        (nr.OPT_INSTRUCTION_CLOCK, 1),
        (nr.OPT_INSTRUCTION_CLOCK_BASE, a.clock_base),
        (nr.OPT_SOFTWARE_INTERRUPTS, 1),
        (nr.OPT_EXPLICIT_MEMORY_MODEL, 0),
        (nr.OPT_APPROX_RECIPS, 1),
        (nr.OPT_CORE_TIMER, 1),
        (nr.OPT_PERIPHERAL_MODEL, 1),
        (nr.OPT_ASSUME_NW32, 1),
        (nr.OPT_BLOCKS, 0 if a.no_blocks else 1),
    ]
    for key, value in options:
        core.set_option(key, value)
    if a.profile:
        core.set_option(nr.OPT_PROFILE, 1)
    if not a.no_skip:
        core.set_option(nr.OPT_IDLE_HEAD, nr.DN2_IDLE_HEAD)
        core.set_option(nr.OPT_IDLE_LO, nr.DN2_IDLE_RANGE[0])
        core.set_option(nr.OPT_IDLE_HI, nr.DN2_IDLE_RANGE[1])
    frames = cap.load(a.capture).dspi2_frames
    pcm = hashlib.sha256()
    pcm_bytes = bytearray()
    states: dict[int, str] = {}
    total = timed = timed_blocks = timed_idle = 0
    timed_wall = 0.0
    halt = None
    t0 = time.perf_counter()
    for i in range(a.start, a.end + 1):
        core.spi2_exchange(bytes(frames[i].tx))
        measured = meas[0] <= i < meas[1]
        before = core.stats() if measured else {}
        t = time.perf_counter()
        n = core.run(a.gap)
        dt = time.perf_counter() - t
        total += n
        if measured:
            after = core.stats()
            timed_wall += dt
            timed += n
            timed_blocks += after["block_instructions"] - before["block_instructions"]
            timed_idle += after["idle_instructions"] - before["idle_instructions"]
        if core.halted:
            halt = (i, core.halt_reason, hex(core.pc()))
            break
        block = core.sport_block()
        if block is not None:
            pcm.update(block)
            pcm_bytes += block
        if i in check:
            states[i] = hashlib.sha256(raw_state(core)).hexdigest()
    result = {
        "args": vars(a),
        "states": states,
        "pcm": pcm.hexdigest(),
        "total": total,
        "halt": halt,
        "wall": time.perf_counter() - t0,
        "meas_wall": timed_wall,
        "meas_instr": timed,
        "meas_block_instr": timed_blocks,
        "meas_idle_instr": timed_idle,
        "pc": hex(core.pc()),
        "stats": core.stats(),
    }
    if a.profile:
        for kind, suffix in enumerate(("", ".entries", ".trans", ".exits")):
            Path(a.profile + suffix).write_text(core.profile(kind))
    if a.pcm:
        Path(a.pcm).write_bytes(bytes(pcm_bytes))
    Path(a.out).write_text(json.dumps(result))
    print(json.dumps({k: v for k, v in result.items() if k != "args"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
