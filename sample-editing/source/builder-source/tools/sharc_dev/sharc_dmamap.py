"""Trace every core/system MMR write a SHARC+ entry point makes from a
post-init state, naming each address against tools/sharc_dev/sharcimm.py's peripheral
register tables (DMA channel register block layout, DMA channel ->
peripheral assignment, SPI register blocks -- ADSP-2156x SHARC+ Processor
Hardware Reference, Rev 1.0, Table 27-2 and Appendix A register lists,
out/refs/adsp-2156x-hwr).

    uv run python -m tools.sharc_dev.sharc_dmamap IMAGE [--entry PC_SW] [--limit N]
        [--regs R4=0x1,R8=0x2,...] [--provisional FORM=MODE ...]
        [--json OUT]

Default ``--entry`` is ``0x1c7ff9``: the DMA-descriptor-driver setup path
this project's own handover traces (HANDOVER-2026-09-26-sharc-audio.md's
"Update (J1, J2, K1 and static reading)") -- reached by a plain ``JUMP``
at ``0x1c147e`` (a manual, I7-stack-based call convention this project's
own disassembly confirms neither reads nor needs R4/R8/R12 as arguments:
FUN_1c7ff9's own first few instructions overwrite all three before using
them), NOT called during ``FUN_1c15e3``'s own init. It calls ``FUN_1c7bd4``
(``0x1c80a2``, ``R4=0xabc``), which builds the two DMA descriptor rings at
DM ``0x2641b0``/``0x2641cc`` and ``0x2641e8``/``0x264204`` this project's
static reading has already found, then the "driver services" ``0x1c8e21``,
``0x1c8e9f``, ``0x1c8e8c``, ``0x1c989b`` -- none of which reference a
peripheral MMR by a literal address in FUN_1c7bd4's own body (every access
there goes through an indirect control-block pointer): this tool exists
because the *emulator* seeing the concrete addresses those indirections
resolve to is the only way to find which physical DMA channel each ring's
DSCPTR_NXT actually lands on, short of hand-tracing every driver-service
callee.

Runs from ``tools/sharc_dev/sharc_harness.run_init()``'s post-init state via
``Runner.fresh_call()`` -- the same convention every other tool built on
this harness uses (``tools/sharc_dev/sharc_replay.py``, ``tools/sharc_dev/sharc_survey.py``) --
with the harness's usual ``explicit_memory_model`` already on
(``sharc_harness._make_runner()``): every DM read/write, MMR included,
is concrete or an explicit, reported stop, never a silent fork.

A run this deep into undiscovered driver code is expected to halt before
returning (an unmodeled MMR read, a fork, or an unsupported/undocumented
form): the halt is reported, not treated as a failure, and every MMR write
already made before it is still real and still printed in order.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc_harness as h  # noqa: E402
from tools.sharc_dev import sharc_run as sr  # noqa: E402
from tools.sharc_dev import sharc_trace as st  # noqa: E402
from tools.sharc_dev import sharcimm  # noqa: E402

# FUN_1c7ff9 -- see this module's own docstring for why this, not
# FUN_1c15e3 (init) or FUN_1c7bd4 (the ring-builder itself) directly, is
# the default entry: running from here reproduces the real call order
# (FUN_1c9fd5, FUN_1c9054, ... then FUN_1c7bd4, then the driver services)
# instead of guessing which of FUN_1c7bd4's own callers' side effects a
# caller who starts lower down would be missing.
DEFAULT_ENTRY = 0x1C7FF9


@dataclasses.dataclass(frozen=True)
class MmrWrite:
    """One write ``sharc_core.memory._dm_write()`` made to ``state.mmrs``
    (never a plain-DM overlay write -- see ``trace_mmr_writes()``'s own
    docstring for why that dict, not a Watchpoint, is what this module
    watches), in the order it happened."""

    pc_sw: int
    address: int
    old_value: int | None
    new_value: int
    name: str | None

    def to_json(self) -> dict:
        return {
            "pc_sw": self.pc_sw,
            "address": self.address,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "name": self.name,
        }

    def __str__(self) -> str:
        old = "?" if self.old_value is None else "%#x" % self.old_value
        name = " (%s)" % self.name if self.name else ""
        return "%#x: MMR[%#x]%s %s -> %#x" % (
            self.pc_sw,
            self.address,
            name,
            old,
            self.new_value,
        )


def trace_mmr_writes(
    runner: sr.Runner,
    *,
    limit: int = 20_000,
) -> tuple[list[MmrWrite], sr.Halt | None]:
    """Every MMR write RUNNER's own ``state.mmrs`` receives while stepping
    forward up to LIMIT instructions, in program order, plus the
    :class:`~tools.sharc_run.Halt` that stopped it (``None`` only if LIMIT
    was reached first, i.e. the run did not stop on its own).

    RUNNER must already have ``state.explicit_memory_model`` set (every
    entry point ``tools/sharc_dev/sharc_harness/`` builds already does --
    ``_make_runner()``): a bare Runner still writes ``state.mmrs`` on a
    fixed-width MMR store (``sharc_core.memory._dm_write``'s
    ``fixed_width_mmr`` branch fires regardless), but the very next
    *read* of a still-unwritten peripheral register only raises
    ``UnmodeledMMR`` -- surfaced here as a named ``Halt`` a caller can
    inspect -- when ``explicit_memory_model`` is on; off, that read
    silently forks or halts some other way before this function can even
    see the write that preceded it.

    ``state.mmrs`` is a plain ``dict`` with no history of its own:
    ``_dm_write`` replaces its entry in place (``state.mmrs[concrete] =
    value``), the same way a real MMR only ever holds its current value.
    This function recovers the write *history* the dict itself does not
    keep by snapshotting it before/after every single instruction
    (``Runner.step()``, never ``Runner.run()``: a whole run's before/after
    diff would only show the LAST value at each address, folding e.g. a
    channel enabled and then immediately reconfigured into one write) and
    comparing keys/values -- correct because ``Runner.step()``/
    ``sharc_core._execute()`` only ever mutate ``state.mmrs``'s entries,
    never replace the dict object itself.

    A single instruction that writes the same MMR address more than once
    (none of this project's known driver code does) would show as one
    write here, at that instruction's own pc, carrying only its final
    value -- the same per-step folding ``tools/sharc_dev/sharc_run.py``'s own
    ``_WatchingOverlay.update()`` applies to a multi-byte overlay write.
    """
    writes: list[MmrWrite] = []
    prev: dict[int, int] = {
        addr: value.value for addr, value in runner.state.mmrs.items()
    }
    halt: sr.Halt | None = None
    for _ in range(limit):
        pc_sw = runner.state.pc_sw
        try:
            runner.step()
        except sr.Halt as exc:
            halt = exc
            break
        current: dict[int, int] = {}
        for addr, value in runner.state.mmrs.items():
            new_value = value.value
            current[addr] = new_value
            old_value = prev.get(addr)
            if old_value != new_value:
                writes.append(
                    MmrWrite(
                        pc_sw, addr, old_value, new_value, sharcimm.name_address(addr)
                    )
                )
        prev = current
    return writes, halt


def _parse_pokes(entries: list[str]) -> dict[int, int]:
    """``ADDR=HEX`` (``tools/sharc_dev/sharc_run.py``'s own ``--poke`` convention) ->
    ``{address: value}``, applied to the fresh call's own State via
    ``sharc_trace._dm_write()`` (the same routing a real store instruction
    gets: a peripheral address lands in ``state.mmrs``, an ordinary DM
    address in the overlay) before stepping. Meant for unblocking a driver
    read this project has not modelled yet (e.g. a security/clock-gating
    status register some library preamble checks before touching a
    peripheral this tool actually cares about) -- a documented, visible
    substitute for the real reset value, never a silent one."""
    pokes: dict[int, int] = {}
    for entry in entries:
        addr_s, _, value_s = entry.partition("=")
        pokes[int(addr_s, 0)] = int(value_s, 0)
    return pokes


def _apply_pokes(runner: sr.Runner, pokes: dict[int, int]) -> None:
    for address, value in pokes.items():
        ok = st._dm_write(runner.state, address, 4, st.Const(value & 0xFFFFFFFF))
        if not ok:
            raise ValueError(
                "poke at %#x did not take effect (outside a mapped DM "
                "region, or not 32-bit-normal-word addressable)" % address
            )


def _parse_regs(spec: str) -> dict[str, int]:
    regs: dict[str, int] = {}
    for pair in spec.split(","):
        pair = pair.strip()
        if not pair:
            continue
        key, _, value = pair.partition("=")
        regs[key.strip()] = int(value.strip(), 0)
    return regs


def _parse_provisional(
    entries: list[str],
) -> tuple[list[str], dict[str, str]]:
    forms: list[str] = []
    interpretations: dict[str, str] = {}
    for entry in entries:
        form, _, mode = entry.partition("=")
        forms.append(form)
        interpretations[form] = mode
    return forms, interpretations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").splitlines()[0],
    )
    parser.add_argument("image")
    parser.add_argument(
        "--entry",
        type=lambda s: int(s, 0),
        default=DEFAULT_ENTRY,
        help="short-word PC to fresh_call() from post-init state (default: %#x)"
        % DEFAULT_ENTRY,
    )
    parser.add_argument("--limit", type=int, default=20_000)
    parser.add_argument(
        "--regs",
        default="",
        help="comma-separated REG=VALUE overrides for the fresh call (e.g. R4=0xabc)",
    )
    parser.add_argument(
        "--provisional",
        action="append",
        default=[],
        metavar="FORM=MODE",
        help="opt into sharc_run's provisional interpretation for an "
        "already-confirmed-decode form with no confirmed semantics "
        "(e.g. 21p_undoc16=nop); may repeat",
    )
    parser.add_argument(
        "--poke",
        dest="pokes",
        action="append",
        default=[],
        metavar="ADDR=HEX",
        help="seed a 32-bit DM/MMR word on the fresh call's own State before "
        "stepping (repeatable) -- see _parse_pokes()'s docstring",
    )
    parser.add_argument("--json", default=None, help="write the full report as JSON")
    args = parser.parse_args(argv)

    regs = _parse_regs(args.regs)
    pokes = _parse_pokes(args.pokes)
    provisional_forms, provisional_interpretations = _parse_provisional(
        args.provisional
    )

    memory = h.load_image_memory(args.image)
    init = h.run_init(
        memory,
        args.image,
        provisional_forms=provisional_forms,
        provisional_interpretations=provisional_interpretations,
    )
    if not init.ran or init.runner is None:
        print("run_init failed: %s" % init.error, file=sys.stderr)
        return 1

    runner = init.runner.fresh_call(args.entry, regs=regs, diagnose_unknown=True)
    _apply_pokes(runner, pokes)
    writes, halt = trace_mmr_writes(runner, limit=args.limit)

    for write in writes:
        print(write)
    if halt is not None:
        print("halt: %s" % halt, file=sys.stderr)
    else:
        print(
            "limit reached (%d instructions) with no halt" % args.limit,
            file=sys.stderr,
        )

    if args.json:
        payload = {
            "image": args.image,
            "entry": args.entry,
            "regs": regs,
            "pokes": {hex(addr): hex(value) for addr, value in pokes.items()},
            "limit": args.limit,
            "writes": [w.to_json() for w in writes],
            "halt": halt.to_json() if halt is not None else None,
        }
        with open(args.json, "w") as f:
            json.dump(payload, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
