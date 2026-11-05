"""Bounded native SHARC startup from loader memory and fresh reference reset.

No captured running state, Python instruction fallback or provisional forms.
Use --compare to check each instruction against the Python reference. A
completed budget is an execution probe, not proof of completed DSP boot.
The flattened loader image does not reproduce the ROM's earlier INIT callback
or its application handoff context.

    uv run python -m tools.sharc_dev.sharc_reset_check dt2-1.16 --lib LIB --report OUT
    uv run python -m tools.sharc_dev.sharc_reset_check dt2-1.16 --lib LIB --compare
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import time

from tools.sharc_dev import sharc_diff as sd
from tools.sharc_dev import sharc_run as sr
from tools.sharc_dev import sharc_trace as st
from tools.sharc_dev import sharc_transpile_run as nr
from tools.sharc_dev import sharcldr
from tools.sharc_dev.sharc_mmr_reset import parse_reset_tables


def bounded_steps(text: str) -> int:
    value = int(text, 0)
    if not 1 <= value <= 1_000_000_000:
        raise argparse.ArgumentTypeError("steps must be between 1 and 1000000000")
    return value


def probe(
    image: str,
    library: str,
    steps: int,
    compare: bool = False,
    *,
    runtime_decode: bool = False,
    mmr_resets: pathlib.Path | None = None,
    approx_recips: bool = False,
    instruction_clock: bool = False,
    banks: bool = False,
    stacks: bool = False,
    stop_software_interrupt: bool = False,
    software_interrupts: bool = False,
    core_timer: bool = False,
    compare_every: int = 1,
    seconds: float = 60,
) -> dict:
    if compare and stop_software_interrupt:
        raise ValueError(
            "software-interrupt diagnostic stop requires non-comparison mode"
        )
    if (software_interrupts or core_timer) and not (banks and stacks):
        raise ValueError("software interrupts require --banks and --stacks")
    data = sr._load_image_memory(image)
    entries = sharcldr.entry_points(data.blocks)
    if not entries:
        raise ValueError("loader has no FIRST entry")
    entry = entries[-1]
    core = nr.NativeCore(nr.pack_image(data), library)
    build = core.info()
    image_hash = hashlib.sha256(data.data).hexdigest()
    if not runtime_decode and build["image_sha256"] != image_hash:
        raise ValueError("native library's instruction image does not match the loader")
    runner = sr.Runner(data, entry, approx_recips=approx_recips)
    runner.state.bank_model = banks
    runner.state.stack_model = stacks
    runner.state.software_interrupts = software_interrupts
    runner.state.core_timer = core_timer
    resets_source = None
    if mmr_resets is not None:
        raw = mmr_resets.read_bytes()
        resets = parse_reset_tables(raw.decode())
        runner.state.mmrs.update(
            {a: st.Const(value) for a, (value, _) in resets.items()}
        )
        resets_source = {
            "path": str(mmr_resets),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "register_count": len(resets),
            "meaning": "documented reset values; no peripheral side effects",
        }
    nr.to_native(core, runner.state)
    if runtime_decode:
        core.set_option(nr.OPT_RUNTIME_DECODE, 1)
    if instruction_clock:
        core.set_option(nr.OPT_INSTRUCTION_CLOCK, 1)
    if stop_software_interrupt:
        core.set_option(nr.OPT_STOP_SOFTWARE_INTERRUPT, 1)
    started = time.perf_counter()
    differences: list[str] = []
    if compare:

        class Reference(sd.PythonEngine):
            def step(self, n: int = 1) -> None:
                for _ in range(n):
                    if instruction_clock:
                        count = self.runner.instructions
                        self.runner.state.uregs[st.UREG_CODES["EMUCLK"]] = st.Const(
                            count & 0xFFFFFFFF
                        )
                        self.runner.state.uregs[st.UREG_CODES["EMUCLK2"]] = st.Const(
                            count >> 32
                        )
                    super().step(1)

        reference = Reference(data)
        reference.load_runner(runner)
        compared = 0
        while compared < steps and time.perf_counter() - started < seconds:
            count = min(compare_every, steps - compared)
            result = sd.run_lockstep(
                reference, core, max_steps=count, compare_every=count
            )
            differences = result.diff
            # Lockstep counts the requested interval even when both engines
            # stop partway through it. Report only completed instructions
            # observed at agreeing comparison boundaries.
            if not differences:
                compared = min(
                    reference.runner.instructions, core.stats()["instructions"]
                )
            if differences or reference.halted or core.halted:
                break
    else:
        while (
            core.stats()["instructions"] < steps
            and time.perf_counter() - started < seconds
        ):
            core.run(min(250_000, steps - core.stats()["instructions"]))
            if core.halted:
                break
        compared = None
    stats = core.stats()
    return {
        "image": image,
        "loader_sha256": image_hash,
        "build": build,
        "seed": "fresh sharc_run.make_state concrete reference reset defaults",
        "captured_state": False,
        "loader_init_executed": False,
        "qualified_fresh_boot": False,
        "runtime_decode": runtime_decode,
        "banks": banks,
        "stacks": stacks,
        "stop_software_interrupt": stop_software_interrupt,
        "software_interrupts": software_interrupts,
        "core_timer": "functional: one clock per completed instruction"
        if core_timer
        else "static",
        "mmr_resets": resets_source,
        "approx_recips": approx_recips,
        "clock_policy": "diagnostic: one tick per instruction, not cycle accurate"
        if instruction_clock
        else "static reference reset",
        "python_instruction_fallback": False,
        "provisional_forms": [],
        "initial_pc": entry,
        "final_pc": core.pc(),
        "instruction_budget": steps,
        "instructions_completed": stats["instructions"],
        "instructions_compared": compared,
        "compare_every": compare_every if compare else None,
        "seconds_budget": seconds,
        "differences": differences,
        "native_halt": core.halt_reason,
        "outcome": "state_divergence"
        if differences
        else "native_halt"
        if core.halted
        else "time_budget"
        if stats["instructions"] < steps
        else "instruction_budget",
        "execution_seconds": time.perf_counter() - started,
        "stats": stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--lib", required=True)
    parser.add_argument("--steps", type=bounded_steps, default=10_000)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument(
        "--runtime-decode",
        action="store_true",
        help="decode loaded memory; permits a firmware-independent native library",
    )
    parser.add_argument(
        "--mmr-resets",
        type=pathlib.Path,
        help="public HWR extraction containing register reset tables",
    )
    parser.add_argument(
        "--approx-recips",
        action="store_true",
        help="use the existing approximate reciprocal seed mode",
    )
    parser.add_argument(
        "--instruction-clock",
        action="store_true",
        help="diagnostic EMUCLK ticks; not hardware cycle timing",
    )
    parser.add_argument(
        "--banks",
        action="store_true",
        help="enable opt-in MODE1 alternate register banks",
    )
    parser.add_argument(
        "--stop-software-interrupt",
        action="store_true",
        help="stop at an unmasked software-interrupt candidate; does not deliver it",
    )
    parser.add_argument(
        "--software-interrupts",
        action="store_true",
        help="deliver software IRQs through the L1 ISA vector table (functional timing)",
    )
    parser.add_argument(
        "--core-timer",
        action="store_true",
        help="countdown and timer IRQs, one clock per instruction; omits pipeline timing",
    )
    parser.add_argument(
        "--stacks",
        action="store_true",
        help="enable independent hardware PC stack and MODE1STK access",
    )
    parser.add_argument(
        "--compare-every",
        type=int,
        default=1,
        help="state comparison interval; 1 checks every instruction",
    )
    parser.add_argument(
        "--seconds", type=float, default=60, help="wall-time guard, at most 300 seconds"
    )
    parser.add_argument("--report", type=pathlib.Path)
    args = parser.parse_args()
    if not 1 <= args.compare_every <= 10_000:
        parser.error("compare-every must be between 1 and 10000")
    if not 0 < args.seconds <= 300:
        parser.error("seconds must be greater than 0 and at most 300")
    if args.compare and args.steps > (10_000 if args.compare_every == 1 else 1_000_000):
        parser.error(
            "comparison budget is at most 10000 individual steps or 1000000 with intervals"
        )
    try:
        result = probe(
            args.image,
            args.lib,
            args.steps,
            args.compare,
            runtime_decode=args.runtime_decode,
            mmr_resets=args.mmr_resets,
            approx_recips=args.approx_recips,
            instruction_clock=args.instruction_clock,
            banks=args.banks,
            stacks=args.stacks,
            stop_software_interrupt=args.stop_software_interrupt,
            software_interrupts=args.software_interrupts,
            core_timer=args.core_timer,
            compare_every=args.compare_every,
            seconds=args.seconds,
        )
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(2, f"sharc-reset-check: {error}\n")
    text = json.dumps(result, indent=2)
    if args.report:
        args.report.write_text(text + "\n")
    print(text)
    return int(result["outcome"] != "instruction_budget")


if __name__ == "__main__":
    raise SystemExit(main())
