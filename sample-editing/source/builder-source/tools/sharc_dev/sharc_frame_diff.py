"""One-frame differential: the native engine against the Python reference.

    uv run python -m tools.sharc_dev.sharc_frame_diff IMAGE STATE FRAMES_LOG [--frame N]
        [--steps K] [--every E] [--lib LIB]

IMAGE is a tools/sharc_dev/sharc.py name (`dt2-1.16`), STATE a canonical blob (raw
`Engine::export`, e.g. sharc_live's DSP_EXPORT, or a DT2DSP01 sidecar) and
FRAMES_LOG a `DIGI_DSPI_LOG` file (DSPILOG1: the DSPI2 frames the ColdFire
sent). Both engines load STATE, take frame N through the SPI2 DMA model, and
step K instructions in lockstep, comparing the exported state every E
instructions. The first disagreement is narrowed to one instruction (the
run restarts and compares every instruction over the last interval).

The native side is set up as `open_dn2_engine` does (runtime decode,
instruction clock, software IRQs, core timer, peripheral model, approximate
RECIPS); the Python side gets the same flags. Needs libsharc_native (the cdylib
of packages/emulator/sharc built with the generated core): `--lib` or SHARC_NATIVE_LIB.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

from tools.sharc_dev import sharc_diff as sd  # noqa: E402
from tools.sharc_dev import sharc_periph_host as ph  # noqa: E402
from tools.sharc_dev import sharc_run as sr  # noqa: E402
from tools.sharc_dev import sharc_transpile_run as nr  # noqa: E402

BASE_CLOCK = 1 << 20


def read_frames(path: Path) -> list[bytes]:
    data = path.read_bytes()
    if data[:8] != b"DSPILOG1":
        raise SystemExit("not a DIGI_DSPI_LOG file")
    pos, frames = 8, []
    while pos < len(data):
        pos += 9  # u64 index, u8 attached
        (n,) = struct.unpack_from(">I", data, pos)
        pos += 4
        frames.append(data[pos : pos + n])
        pos += n
        (m,) = struct.unpack_from(">I", data, pos)
        pos += 4 + m
    return frames


def load_fields(path: Path) -> dict:
    blob = path.read_bytes()
    if blob[:8] == b"DT2DSP01":
        blob = blob[24:]
    return sd.unpack_state(blob)


def make_engines(image: str, fields: dict, lib: str, frame: bytes):
    data = sr._load_image_memory(image)
    native = nr.NativeCore(nr.pack_image(data), lib)
    native.load_state(fields)
    for key, value in (
        (4, 1), (5, 1), (6, BASE_CLOCK), (9, 1), (10, 0),
        (11, 1), (12, 1), (21, 1), (22, 1),
    ):  # fmt: skip
        native.set_option(key, value)
    ref = sd.PythonEngine(data)
    ref.load_state(fields)
    state = ref._runner.state
    state.approx_recips = True
    state.assume_nw32 = True
    state.explicit_memory_model = False
    state.bank_model = fields.get("bank_model", True)
    state.stack_model = fields.get("stack_model", True)
    state.software_interrupts = True
    state.core_timer = True
    state.peripheral_model = True
    # The frame, through both SPI2 DMA models.
    native.spi2_exchange(frame)
    ph.spi2_exchange(state, frame)
    return native, ref


class Clocked(sd.PythonEngine):
    """PythonEngine with the instruction clock the native option 5 keeps."""

    count = 0

    def step(self, n: int = 1) -> None:
        from tools.sharc_dev import sharc_trace as st

        for _ in range(n):
            tick = BASE_CLOCK + self.count
            u = self._runner.state.uregs
            u[st.UREG_CODES["EMUCLK"]] = st.Const(tick & 0xFFFFFFFF)
            u[st.UREG_CODES["EMUCLK2"]] = st.Const(tick >> 32)
            super().step(1)
            self.count += 1
            if self.halted:
                return


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("image")
    p.add_argument("state", type=Path)
    p.add_argument("frames", type=Path)
    p.add_argument("--frame", type=int, default=-1)
    p.add_argument("--steps", type=int, default=250_000)
    p.add_argument("--every", type=int, default=25_000)
    p.add_argument("--lib", default=nr.DEFAULT_LIB)
    a = p.parse_args(argv)
    frames = read_frames(a.frames)
    frame = frames[a.frame]
    fields = load_fields(a.state)

    def fresh():
        native, ref = make_engines(a.image, fields, a.lib, frame)
        clocked = Clocked(ref.data)
        clocked._runner = ref._runner
        return native, clocked

    native, ref = fresh()
    done = 0
    while done < a.steps:
        n = min(a.every, a.steps - done)
        result = sd.run_lockstep(native, ref, max_steps=n, compare_every=n)
        done += n
        print(
            f"{done} steps: {'DIFF' if result.diverged else 'same'} pc={result.pc_sw}",
            flush=True,
        )
        if result.diverged:
            break
    else:
        print("no divergence")
        return 0
    # Narrow: restart, step to the start of the failing interval, then compare
    # every instruction.
    lo = done - a.every
    native, ref = fresh()
    native.step(lo)
    ref.step(lo)
    result = sd.run_lockstep(native, ref, max_steps=a.every, compare_every=1)
    print(f"first difference at step {lo + result.steps_agreed}, pc {result.pc_sw}")
    for line in result.diff[:20]:
        print("  ", line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
