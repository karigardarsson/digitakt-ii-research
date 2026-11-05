"""Count circular-buffer wraps of ordinary load/store post-modifies in a
bounded Python-only SHARC+ cold-start run (no native core).

    uv run python -m tools.sharc_dev.sharc_circ_census dn2-1.11 --steps 300000 \\
        --mmr-resets out/refs/adsp-2156x-hwr/all.txt

Reports, per PC, how many post-modifies ran with circular buffering active
(MODE1.CBUFEN set and the index's L register nonzero) and how many of those
wrapped (result differs from the plain I + M). Setup mirrors
sharc_reset_check.py's reference side.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import time

from tools.sharc_dev import sharc_run as sr
from tools.sharc_dev import sharc_trace as st
from tools.sharc_dev import sharcldr
from tools.sharc_dev.sharc_core import forms_compute, forms_move, memory
from tools.sharc_dev.sharc_core.values import Const
from tools.sharc_dev.sharc_mmr_reset import parse_reset_tables

ACTIVE: collections.Counter = collections.Counter()
WRAPPED: collections.Counter = collections.Counter()
EXAMPLES: dict[int, tuple[int, int, int, int, int]] = {}


def instrument() -> None:
    original = memory._post_modify_index

    def counting(state, index, iv, delta, scale, expression):
        result = original(state, index, iv, delta, scale, expression)
        length = state.uregs.get(st.UREG_CODES["L%d" % index])
        mode1 = state.uregs.get(st.UREG_CODES["MODE1"])
        if (
            isinstance(length, Const)
            and length.value
            and isinstance(mode1, Const)
            and mode1.value & memory.MODE1_CBUFEN
        ):
            pc = state.pc_sw
            ACTIVE[pc] += 1
            plain = memory._add(iv, delta, expression)
            if result != plain:
                WRAPPED[pc] += 1
                if pc not in EXAMPLES and isinstance(iv, Const):
                    base = state.uregs.get(st.UREG_CODES["B%d" % index])
                    EXAMPLES[pc] = (
                        index,
                        iv.value,
                        base.value if isinstance(base, Const) else -1,
                        length.value * scale,
                        result.value if isinstance(result, Const) else -1,
                    )
        return result

    memory._post_modify_index = counting
    forms_move._post_modify_index = counting
    forms_compute._post_modify_index = counting


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--steps", type=int, default=200_000)
    parser.add_argument("--seconds", type=float, default=240)
    parser.add_argument("--mmr-resets", type=pathlib.Path)
    parser.add_argument("--no-linear-baseline", action="store_true")
    args = parser.parse_args()
    data = sr._load_image_memory(args.image)
    entry = sharcldr.entry_points(data.blocks)[-1]
    runner = sr.Runner(data, entry, approx_recips=True)
    runner.state.bank_model = True
    runner.state.stack_model = True
    if args.mmr_resets:
        resets = parse_reset_tables(args.mmr_resets.read_text())
        runner.state.mmrs.update({a: Const(v) for a, (v, _) in resets.items()})
    instrument()
    started = time.perf_counter()
    reason = "step budget"
    steps = 0
    while steps < args.steps and time.perf_counter() - started < args.seconds:
        count = runner.instructions
        runner.state.uregs[st.UREG_CODES["EMUCLK"]] = Const(count & 0xFFFFFFFF)
        runner.state.uregs[st.UREG_CODES["EMUCLK2"]] = Const(count >> 32)
        try:
            runner.step()
        except sr.Halt as exc:
            reason = "halt: %s" % exc.reason
            break
        steps += 1
    print(
        "%s: %d instructions, %.1fs, stopped by %s; final pc %#x"
        % (
            args.image,
            runner.instructions,
            time.perf_counter() - started,
            reason,
            runner.state.pc_sw,
        )
    )
    print(
        "post-modifies with circular buffering active: %d at %d PCs; wrapped: %d at %d PCs"
        % (sum(ACTIVE.values()), len(ACTIVE), sum(WRAPPED.values()), len(WRAPPED))
    )
    for pc, count in WRAPPED.most_common(40):
        index, i, b, scaled, result = EXAMPLES.get(pc, (-1, -1, -1, -1, -1))
        print(
            "  pc %#x wrapped %d / %d  e.g. I%d=%#x B=%#x L*scale=%#x -> %#x"
            % (pc, count, ACTIVE[pc], index, i, b, scaled, result)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
