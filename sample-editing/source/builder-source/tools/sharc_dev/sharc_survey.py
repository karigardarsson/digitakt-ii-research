"""Reusable frame-render/routine survey driver.

Promoted from a scratch driver (a lane-L7 survey of FUN_1c2b24, the frame
render root) into a general tool: start from an already-booted state (a
tools/sharc_dev/sharc_run.py snapshot, or a fresh tools/sharc_dev/sharc_harness.run_init()),
jump to an arbitrary root with Runner.fresh_call(), apply an accumulated
table of register/memory hypotheses each time a given pc is about to
execute, run with diagnose_unknown=True, and print a rich diagnosis of
whatever it stops on.

    uv run python -m tools.sharc_dev.sharc_survey dt2-1.16 --root 0x1c2b24 \\
        --snapshot /path/to/init_cache.snap

    uv run python -m tools.sharc_dev.sharc_survey dt2-1.16 --root 0x1c2b24 --run-init

    uv run python -m tools.sharc_dev.sharc_survey dt2-1.16 --root 0x1c2b24 \\
        --snapshot /path/to/init_cache.snap --patch-table hyps.py

Workflow: run once, read the printed diagnosis (unknown bits, the last
instruction that wrote each one, a bounded backward slice from the stop,
and the call stack with function names), add a hypothesis to a patch-table
file for whatever it found, and re-run -- the patch table is applied fresh
on every pc recurrence, so an already-diagnosed stop is skipped over on the
next run and only a genuinely new one halts it. A patch-table file's
hypotheses accumulate; this tool itself never resumes from where it halted
(no ad hoc, unrepeatable in-memory state) -- see run_with_patches()'s and
--patch-table's docstrings.

Library use:

    import sharc_survey as sv
    stop = sv.survey(image="dt2-1.16", root=0x1c2b24, snapshot=path)
    print(stop.halt.reason)
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc  # noqa: E402
from tools.sharc_dev import sharc_run as sr  # noqa: E402
from tools.sharc_dev import sharc_trace as st  # noqa: E402

# {trigger pc_sw: [("reg", NAME, value) | ("mem", addr, value), ...]},
# applied the instant that pc is about to execute, every time it recurs --
# see run_with_patches().
PatchTable = dict[int, list[tuple[str, int | str, int]]]

DEFAULT_MAX_STEPS = 4_000_000
DEFAULT_SLICE_DEPTH = 6

# REG.BIT name (e.g. "ASTATX.BTF", as tools/sharc_dev/sharc_run.py's Halt.unknowns
# reports it -- see its _ASTAT_BIT_NAME) -> the tools/sharc_dev/sharc.py ASTAT group
# pseudo-register name it belongs to (e.g. "ASTAT.BTF"), composed from both
# modules' own bit tables rather than a third, hand-copied one: sharc_run's
# bit-number -> name and sharc's bit-number -> group must already agree on
# every bit number, or a Halt's own "ASTATX.BTF" text and sharc.py's
# defuse()/slice() group split would disagree about what that text means.
_UNKNOWN_RE = re.compile(r"^(ASTATX|ASTATY)\.(\w+)$")
_BIT_NAME_TO_GROUP = {
    name: sharc._ASTAT_GROUP_BIT[bit]
    for bit, name in sr._ASTAT_BIT_NAME.items()
    if bit in sharc._ASTAT_GROUP_BIT
}


def _astat_groups_from_unknowns(unknowns: Sequence[str]) -> set[str]:
    """The tools/sharc_dev/sharc.py ASTAT group(s) (see Image._ASTAT_GROUPS)
    implicated by a fork Halt's own unknowns tuple -- e.g. {"BTF"} for
    ("cond=TF", "ASTATX.BTF"). Empty when nothing in UNKNOWNS names an
    ASTATX/ASTATY bit at all (a MODE1-only fork, or diagnose_unknown gave
    up entirely -- see _fork_diagnosis()'s own docstring)."""
    groups: set[str] = set()
    for text in unknowns:
        match = _UNKNOWN_RE.match(text)
        if match is None:
            continue
        group = _BIT_NAME_TO_GROUP.get(match.group(2))
        if group is not None:
            groups.add(group)
    return groups


def load_patch_table(path: str | None) -> PatchTable:
    """A PatchTable from PATH: a .json file (keys are decimal or "0x..."
    strings, values are [kind, target, value] lists -- JSON has no tuples)
    or a .py file defining a module-level ``PATCH_TABLE`` dict (the same
    convention the scratch survey driver this tool was promoted from used:
    edit a Python literal between runs, keys already ints, values already
    tuples). None (no --patch-table) is an empty table."""
    if path is None:
        return {}
    if path.endswith(".json"):
        with open(path) as fh:
            raw = json.load(fh)
        table: PatchTable = {}
        for key, entries in raw.items():
            pc = int(key, 0) if isinstance(key, str) else int(key)
            table[pc] = [
                (
                    kind,
                    target,
                    int(value, 0) if isinstance(value, str) else int(value),
                )
                for kind, target, value in entries
            ]
        return table
    spec = importlib.util.spec_from_file_location("_sharc_survey_patch_table", path)
    if spec is None or spec.loader is None:
        raise SystemExit("--patch-table: cannot load %r" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    py_table: PatchTable | None = getattr(module, "PATCH_TABLE", None)
    if py_table is None:
        raise SystemExit("--patch-table: %r defines no PATCH_TABLE dict" % path)
    return py_table


def apply_patches(runner: sr.Runner, patch_table: PatchTable) -> list[str]:
    """Apply every "reg"/"mem" PATCH_TABLE entry for runner.state.pc_sw, if
    any (in place, before that instruction executes); returns a
    one-line-per-patch description of what was applied, for the caller to
    log. A "branch" entry (see _BRANCH_PATCH_KIND, run_collect_all()'s own
    fork-resolution -- checked only once a fork has actually happened, not
    unconditionally like this function) is silently skipped here rather
    than applied: it names a successor to pick, not a register/memory value
    to poke before the instruction runs, and a plain run_with_patches()
    caller that never forks at that pc still needs this to be a no-op."""
    entries = patch_table.get(runner.state.pc_sw)
    if not entries:
        return []
    applied = []
    for kind, target, value in entries:
        if kind == "reg":
            if isinstance(target, str):
                code, name = st.UREG_CODES[target], target
            else:
                code, name = target, st.UREG_NAMES[target]
            runner.state.uregs[code] = st.Const(value)
            applied.append("reg %s = %#x" % (name, value))
        elif kind == "mem":
            if not isinstance(target, int):
                raise ValueError(
                    "mem patch target must be an int address, got %r" % (target,)
                )
            ok = st._dm_write(runner.state, target, 4, st.Const(value & 0xFFFFFFFF))
            applied.append(
                "mem[%#x] = %#x%s" % (target, value, "" if ok else " (FAILED)")
            )
        elif kind == _BRANCH_PATCH_KIND:
            continue
        else:
            raise ValueError("bad patch kind %r at pc=%#x" % (kind, runner.state.pc_sw))
    return applied


def run_with_patches(
    runner: sr.Runner, patch_table: PatchTable, max_steps: int, *, verbose: bool = False
) -> sr.Halt:
    """Step RUNNER forward, applying PATCH_TABLE's entries for the current
    pc immediately before it executes (every time that pc recurs, e.g. on
    each iteration of a loop) until it halts or MAX_STEPS is used up.
    Returns the Halt (a "max-steps" one if the budget ran out)."""
    steps = 0
    while steps < max_steps:
        applied = apply_patches(runner, patch_table)
        if verbose and applied:
            print("  patched at %#x: %s" % (runner.state.pc_sw, "; ".join(applied)))
        try:
            runner.step()
        except sr.Halt as exc:
            return exc
        steps += 1
    return sr.Halt("max-steps", runner.state.pc_sw)


# --- collect-all mode --------------------------------------------------------
#
# survey()/run_with_patches() stop at the FIRST halt: useful once a patch
# table already resolves everything upstream of the one new stop being
# diagnosed, but turning a whole call's worth of unknown forks into a punch
# list this way is one run per fork, by hand. run_collect_all() instead
# keeps going through a fork by picking a branch itself -- the one named for
# that pc by a "branch" patch-table entry (see BranchPatchTable below), else
# a documented default (not-taken, i.e. the predicate assumed False) -- and
# records every stop (fork or otherwise) it passes on the way, so one run
# lists every blocker on a single path through the firmware instead of only
# the nearest one.
#
# Only a fork is something this can drive through on its own: every fork
# this tracer raises comes from exactly one idiom (SHARC_core's own
# "taken, not_taken = _copy(state), _copy(state)" / "executed, skipped ="
# pattern, repeated across sharc_core/forms_flow.py, forms_compute.py,
# forms_move.py, forms_dag.py and sequencer.py -- see _fork_diagnosis()'s
# docstring in tools/sharc_dev/sharc_run.py for the full call-site count), which
# always returns the True-predicate outcome(s) first and the False-predicate
# outcome(s) second. Every call site observed produces exactly one State per
# side (`out` has length 2); a length other than 2 is a fork shape this
# tool has not seen and does not know how to split into "taken"/"not-taken",
# so it is recorded and treated as terminal rather than guessed at. An
# unmodeled MMR read and a return-target mismatch are also recorded, but
# never continued past: an MMR has no documented default value to assume,
# and a return mismatch means this run's own state is already wrong (see
# SurveyStop.category's docstring) -- stepping further from either would
# just manufacture more "blockers" downstream of bad state, not real ones.

# A PatchTable entry ("branch", <anything>, 0|1) resolves a fork at that pc
# to the not-taken (0) or taken (1) successor -- checked only once a fork
# has actually happened (out has more than one successor), unlike a "reg"/
# "mem" entry (applied unconditionally before the instruction executes, via
# apply_patches(), and often enough to avoid the fork in the first place).
# Both kinds may sit in the same PatchTable under the same pc.
_BRANCH_PATCH_KIND = "branch"


@dataclass
class CollectStop:
    """One recorded stop from a run_collect_all() pass: everything
    report_stop() would print for a single survey() halt, plus how (and
    whether) this run continued past it."""

    index: int
    category: str
    pc: int
    form: str | None
    text: str
    unknowns: tuple[str, ...]
    last_writer: dict[str, int]
    slice_summary: str
    call_stack: list[str]
    # Only set for category == "fork": how this run picked a successor, e.g.
    # "taken (patch-table)", "not-taken (default)", or "unresolved (N
    # successors)" for a non-binary fork this tool refuses to guess through
    # (see the module note above) -- always terminal when unresolved.
    resolution: str | None = None
    # Set iff resolving THIS stop consumed a fresh guess (an unpatched
    # default, not a patch-table entry) -- its 1-based number.
    guess_number: int | None = None
    # Guess numbers made strictly before this stop, oldest first -- empty
    # until the first unpatched default fork.
    downstream_of: tuple[int, ...] = ()

    def to_json(self) -> dict:
        return {
            "index": self.index,
            "category": self.category,
            "pc": self.pc,
            "form": self.form,
            "text": self.text,
            "unknowns": list(self.unknowns),
            "last_writer": dict(self.last_writer),
            "slice_summary": self.slice_summary,
            "call_stack": list(self.call_stack),
            "resolution": self.resolution,
            "guess_number": self.guess_number,
            "downstream_of": list(self.downstream_of),
        }


@dataclass
class CollectAllResult:
    """Every stop run_collect_all() passed on a single continuous path
    through ROOT, oldest first; the last entry is always terminal (a
    non-fork halt, an unresolved/non-binary fork, or a max-steps budget)."""

    stops: list[CollectStop]
    instructions: int
    elapsed: float
    guesses: int

    @property
    def terminal(self) -> CollectStop:
        return self.stops[-1]


def _short_slice(
    img: sharc.Image | None,
    pc_sw: int,
    *,
    reg: str | None = None,
    depth: int,
    max_chars: int = 300,
) -> str:
    """A one-line (whitespace-collapsed, length-bounded) version of
    img.print_slice() -- report_stop()'s full multi-line dump is too wide
    for a table with one row per stop. img.print_slice() also prints its
    own full text as a side effect (tools/sharc_dev/sharc.py, not this lane's own
    file); every collect-all stop computes this eagerly (not only when a
    caller prints the report), so that side effect is suppressed here --
    otherwise --json output would have that raw text spliced into it."""
    if img is None:
        return ""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            text = img.print_slice(pc_sw, reg=reg, depth=depth)
    except Exception as exc:  # noqa: BLE001 -- report and move on, never abort the survey
        return "(unavailable: %s)" % exc
    if not text:
        return "(empty)"
    flat = " / ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(flat) > max_chars:
        flat = flat[: max_chars - 3] + "..."
    return flat


def _finish_step(
    runner: sr.Runner,
    pc_sw: int,
    insn,
    state,
    diagnosing: bool,
    before,
) -> sr.Halt | None:
    """The bookkeeping tools/sharc_dev/sharc_run.py's Runner.step() does once it has a
    single successor STATE for the instruction at PC_SW: install it as
    runner.state, update _last_writer, and either report the Halt
    state.stopped names or advance the ordinary counters. Mirrors
    Runner.step() exactly (see its own docstring) so run_collect_all()'s
    bookkeeping can never drift from a plain survey()/Runner.run() -- this
    duplicates that block rather than editing sharc_run.py, which this
    lane does not own."""
    runner.state = state
    if diagnosing:
        astatx_code, astaty_code, mode1_code = runner._diagnose_codes
        uregs = state.uregs
        before_astatx, before_astaty, before_mode1 = before
        if uregs[astatx_code] is not before_astatx:
            runner._last_writer["ASTATX"] = pc_sw
        if uregs[astaty_code] is not before_astaty:
            runner._last_writer["ASTATY"] = pc_sw
        if uregs[mode1_code] is not before_mode1:
            runner._last_writer["MODE1"] = pc_sw
    if state.stopped:
        return sr.Halt(state.stopped, state.pc_sw, insn.type_name, insn.note)
    runner.instructions += 1
    runner.form_counts[insn.type_name] += 1
    depth = len(state.call_stack)
    if depth > runner.max_call_depth_reached:
        runner.max_call_depth_reached = depth
    return None


def run_collect_all(
    runner: sr.Runner,
    patch_table: PatchTable,
    max_steps: int,
    *,
    img: sharc.Image | None = None,
    slice_depth: int = DEFAULT_SLICE_DEPTH,
    verbose: bool = False,
) -> CollectAllResult:
    """Step RUNNER forward like run_with_patches(), but instead of stopping
    at the first Halt, record every stop (pc, form, unknown bits, last flag
    writer, a short slice summary, call stack) and keep going whenever the
    stop is a binary fork -- see the module note above for exactly which
    stops are continued through and which are terminal. RUNNER must have
    diagnose_unknown=True (its _last_writer/_fork_diagnosis are what make a
    fork's unknowns and "last flag writer" meaningful); this is not
    enforced, since a caller probing a run with no forks at all may not
    care.

    Returns a CollectAllResult whose last stop is always terminal. Bounded
    by MAX_STEPS the same way run_with_patches() is -- an exhausted budget
    is recorded as a "max-steps" stop, not silently dropped."""
    patch_table = patch_table or {}
    stops: list[CollectStop] = []
    guess_numbers: list[int] = []
    # A fork pc resolved once (guessed or patched) is remembered and, on every
    # later recurrence -- e.g. each iteration of a loop -- reapplied silently
    # instead of being recorded and re-guessed again: "one run lists all
    # blockers" means one row per distinct fork this path depends on, not one
    # per loop iteration it happens to run (a real loop can recur thousands
    # of times before this function's own max_steps is reached at all).
    fork_choice: dict[int, bool] = {}

    def record(category, pc, form, text, unknowns, *, resolution=None) -> CollectStop:
        stop = CollectStop(
            index=len(stops),
            category=category,
            pc=pc,
            form=form,
            text=text,
            unknowns=tuple(unknowns),
            last_writer=dict(runner._last_writer),
            slice_summary=_short_slice(img, pc, depth=slice_depth),
            call_stack=_call_stack_lines(img, runner)
            if img is not None
            else ["%#x" % a for a in runner.state.call_stack],
            resolution=resolution,
            downstream_of=tuple(guess_numbers),
        )
        stops.append(stop)
        return stop

    def finish(steps: int) -> CollectAllResult:
        return CollectAllResult(
            stops=stops,
            instructions=runner.instructions,
            elapsed=0.0,
            guesses=len(guess_numbers),
        )

    steps = 0
    while steps < max_steps:
        applied = apply_patches(runner, patch_table)
        if verbose and applied:
            print("  patched at %#x: %s" % (runner.state.pc_sw, "; ".join(applied)))
        state = runner.state
        pc_sw = state.pc_sw
        if pc_sw in runner.breakpoints:
            record("breakpoint", pc_sw, None, "", ())
            return finish(steps)
        insn = runner._decode(pc_sw)
        if runner._watch is not None:
            runner._watch.begin_step(pc_sw, insn.type_name)
        diagnosing = runner.diagnose_unknown
        before = None
        if diagnosing:
            astatx_code, astaty_code, mode1_code = runner._diagnose_codes
            uregs = state.uregs
            before = (uregs[astatx_code], uregs[astaty_code], uregs[mode1_code])
        try:
            out = st._execute(state, insn)
        except sr.UnmodeledMMR as exc:
            record(
                "mmr",
                pc_sw,
                insn.type_name,
                "unmodeled MMR %#x (%s)" % (exc.address, exc.name or "unnamed"),
                (),
            )
            return finish(steps)
        except sr._WatchpointStop as exc:
            record("watchpoint", pc_sw, insn.type_name, str(exc.event), ())
            return finish(steps)

        if len(out) == 1:
            halt = _finish_step(runner, pc_sw, insn, out[0], diagnosing, before)
            if halt is not None:
                record(
                    _categorize_reason(halt.reason),
                    halt.pc_sw,
                    halt.form,
                    halt.text,
                    halt.unknowns,
                )
                return finish(steps)
            steps += 1
            continue

        # A fork: len(out) != 1.
        if pc_sw in fork_choice:
            # Already diagnosed on an earlier visit to this pc (a loop):
            # reapply the same choice, but do not re-record or re-guess it.
            chosen_state = out[0] if fork_choice[pc_sw] else out[1]
            halt = _finish_step(runner, pc_sw, insn, chosen_state, diagnosing, before)
            if halt is not None:
                record(
                    _categorize_reason(halt.reason),
                    halt.pc_sw,
                    halt.form,
                    halt.text,
                    halt.unknowns,
                )
                return finish(steps)
            steps += 1
            continue

        unknowns = (
            sr._fork_diagnosis(state, insn, runner._last_writer) if diagnosing else ()
        )
        if len(out) != 2:
            record(
                "fork",
                pc_sw,
                insn.type_name,
                insn.note,
                unknowns,
                resolution="unresolved (%d successors)" % len(out),
            )
            return finish(steps)

        branch_entries = [
            e for e in patch_table.get(pc_sw, ()) if e[0] == _BRANCH_PATCH_KIND
        ]
        if branch_entries:
            chosen_true = bool(branch_entries[-1][2])
            resolution = "%s (patch-table)" % ("taken" if chosen_true else "not-taken")
            is_guess = False
        else:
            chosen_true = False
            resolution = "not-taken (default)"
            is_guess = True
        fork_choice[pc_sw] = chosen_true

        stop = record(
            "fork", pc_sw, insn.type_name, insn.note, unknowns, resolution=resolution
        )
        if is_guess:
            guess_numbers.append(len(guess_numbers) + 1)
            stop.guess_number = guess_numbers[-1]

        chosen_state = out[0] if chosen_true else out[1]
        halt = _finish_step(runner, pc_sw, insn, chosen_state, diagnosing, before)
        if halt is not None:
            record(
                _categorize_reason(halt.reason),
                halt.pc_sw,
                halt.form,
                halt.text,
                halt.unknowns,
            )
            return finish(steps)
        steps += 1

    record("max-steps", runner.state.pc_sw, None, "", ())
    return finish(steps)


def collect_all(
    *,
    image: str,
    root: int,
    snapshot: str | None = None,
    run_init: bool = False,
    regs: Mapping[str | int, int | str] | None = None,
    return_address: int | None = None,
    patch_table: PatchTable | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_call_depth: int = 64,
    explicit_memory_model: bool = True,
    approx_recips: bool = True,
    follow_loaded_calls: bool = True,
    slice_depth: int = DEFAULT_SLICE_DEPTH,
    verbose: bool = False,
) -> CollectAllResult:
    """The --collect-all counterpart to survey(): same starting points
    (_start_runner()), but run_collect_all() instead of run_with_patches(),
    so the result is every stop on one path through ROOT rather than only
    the first."""
    runner, img = _start_runner(
        image=image,
        root=root,
        snapshot=snapshot,
        run_init=run_init,
        regs=regs,
        return_address=return_address,
        max_call_depth=max_call_depth,
        explicit_memory_model=explicit_memory_model,
        approx_recips=approx_recips,
        follow_loaded_calls=follow_loaded_calls,
    )
    t0 = time.perf_counter()
    result = run_collect_all(
        runner,
        patch_table or {},
        max_steps,
        img=img,
        slice_depth=slice_depth,
        verbose=verbose,
    )
    result.elapsed = time.perf_counter() - t0
    return result


def report_collect_all(result: CollectAllResult) -> None:
    """A table, one row per CollectStop, plus a full report_stop()-shaped
    detail block for the terminal one."""
    print(
        "instructions=%d elapsed=%.3fs (%.0f instr/s)"
        % (
            result.instructions,
            result.elapsed,
            result.instructions / result.elapsed
            if result.elapsed > 0
            else float("inf"),
        )
    )
    print("%d stop(s), %d guess(es)" % (len(result.stops), result.guesses))
    print(
        "%-4s %-10s %-10s %-10s %-28s %s"
        % ("#", "category", "pc", "form", "resolution", "downstream-of")
    )
    for s in result.stops:
        downstream = (
            ",".join("#%d" % g for g in s.downstream_of) if s.downstream_of else "-"
        )
        guess = " (guess #%d)" % s.guess_number if s.guess_number is not None else ""
        print(
            "%-4d %-10s %-10s %-10s %-28s %s"
            % (
                s.index,
                s.category,
                "%#x" % s.pc,
                s.form or "-",
                (s.resolution or "-") + guess,
                downstream,
            )
        )
    last = result.terminal
    print("\nterminal stop detail:")
    print("  pc=%#x form=%s category=%s" % (last.pc, last.form, last.category))
    if last.text:
        print("  text: %s" % last.text)
    if last.unknowns:
        print("  unknowns: %s" % ", ".join(last.unknowns))
    print("  last flag writer:")
    for name, pc in last.last_writer.items():
        print("    %-8s 0x%x" % (name, pc))
    print("  slice: %s" % last.slice_summary)
    print("  call stack (return addrs, innermost last):")
    if not last.call_stack:
        print("    (empty)")
    for line in last.call_stack:
        print("    %s" % line)


def _categorize_reason(reason: str) -> str:
    """A short label for a Halt.reason, beyond its own free text:
    "return-mismatch" gets its own category (see the module docstring and
    _check_return_target() in sharc_core/sequencer.py) -- a prior poke or an
    inherited call_stack entry disagreeing with what the sequencer's own
    I12/M14 computed is a sign that THIS RUN's state is wrong, not a new
    thing to explain about the firmware. Shared by SurveyStop.category and
    run_collect_all()'s own per-stop category, so the two never disagree
    about what a given reason string means."""
    if (
        reason.startswith("return target ")
        and " differs from recorded return " in reason
    ):
        return "return-mismatch"
    if reason.startswith("fork"):
        return "fork"
    if reason == "mmr":
        return "mmr"
    if reason == "watchpoint":
        return "watchpoint"
    if reason == "return without followed call":
        return "frame-returned"
    if reason == "max-steps":
        return "max-steps"
    return reason


@dataclass
class SurveyStop:
    """One survey run's outcome: the Halt itself, the Runner that produced
    it (still positioned at the halt, so its state/call_stack/_last_writer
    are all inspectable), and the Image used to diagnose it."""

    halt: sr.Halt
    runner: sr.Runner
    img: sharc.Image
    instructions: int
    elapsed: float

    @property
    def category(self) -> str:
        return _categorize_reason(self.halt.reason)


def _call_stack_lines(img: sharc.Image, runner: sr.Runner) -> list[str]:
    lines = []
    for addr in runner.state.call_stack:
        fn = img.func(addr)
        name = fn["name"] if fn else None
        lines.append("0x%x%s" % (addr, (" (%s)" % name) if name else ""))
    return lines


def _last_writer_lines(runner: sr.Runner) -> list[str]:
    lines = []
    for name in sr._DIAGNOSE_TRACKED_UREGS:
        pc = runner._last_writer.get(name)
        lines.append(
            "%-8s %s"
            % (name, ("0x%x" % pc) if pc is not None else "(never written this run)")
        )
    return lines


def _print_bounded_slice(
    img: sharc.Image, pc_sw: int, *, reg: str | None, depth: int, label: str
) -> None:
    print("  %s:" % label)
    try:
        text = img.print_slice(pc_sw, reg=reg, depth=depth)
    except Exception as exc:  # noqa: BLE001 -- report and move on, never abort the survey
        print("    (unavailable: %s)" % exc)
        return
    if not text:
        print("    (empty)")


def report_stop(stop: SurveyStop, *, slice_depth: int = DEFAULT_SLICE_DEPTH) -> None:
    """Print pc, form, unknown bits, last flag writer, a bounded
    img.print_slice from the stop, and the call stack with function names
    -- the fixed shape every survey stop gets, regardless of category."""
    halt, runner, img = stop.halt, stop.runner, stop.img
    print(
        "instructions=%d elapsed=%.3fs (%.0f instr/s)"
        % (
            stop.instructions,
            stop.elapsed,
            stop.instructions / stop.elapsed if stop.elapsed > 0 else float("inf"),
        )
    )
    print("halt: reason=%s pc=%#x form=%s" % (halt.reason, halt.pc_sw, halt.form))
    if halt.text:
        print("  text: %s" % halt.text)
    print("  category: %s" % stop.category)
    if halt.unknowns:
        print("  unknowns:")
        for text in halt.unknowns:
            print("    %s" % text)
    print("  last flag writer:")
    for line in _last_writer_lines(runner):
        print("    %s" % line)
    fn = img.func(halt.pc_sw)
    print("  function: %s" % (fn["name"] if fn else "(none)"))
    _print_bounded_slice(
        img, halt.pc_sw, reg=None, depth=slice_depth, label="slice (all inputs)"
    )
    if stop.category == "fork":
        groups = _astat_groups_from_unknowns(halt.unknowns)
        if len(groups) == 1:
            group = next(iter(groups))
            reg = sharc._ASTAT_GROUP_REG[group]
            _print_bounded_slice(
                img,
                halt.pc_sw,
                reg=reg,
                depth=slice_depth,
                label="slice (reg=%r -- the precise group this cond reads)" % reg,
            )
        elif groups:
            print(
                "  (cond reads more than one ASTAT group: %s -- see the "
                "whole-register slice above)" % ", ".join(sorted(groups))
            )
    elif stop.category == "mmr":
        match = re.search(r"0x[0-9a-fA-F]+", halt.text)
        if match:
            addr = int(match.group(0), 16)
            print("  writers(%#x): %s" % (addr, img.writers(addr)))
            print("  readers(%#x): %s" % (addr, img.readers(addr)))
            print("  refs(%#x): %s" % (addr, img.refs(addr)))
    elif stop.category == "return-mismatch":
        print(
            "  a prior poke or state is wrong: the sequencer's own I12/M14 "
            "computed a return address that disagrees with the pushed "
            "call_stack entry (see run_init()/fresh_call()'s own note on "
            "why pending/stopped/call_stack must all be cleared for a new "
            "root) -- this is not new firmware behaviour to explain."
        )
    print("  call stack (return addrs, innermost last):")
    stack_lines = _call_stack_lines(img, runner)
    if not stack_lines:
        print("    (empty)")
    for line in stack_lines:
        print("    %s" % line)


def _start_runner(
    *,
    image: str,
    root: int,
    snapshot: str | None = None,
    run_init: bool = False,
    regs: Mapping[str | int, int | str] | None = None,
    return_address: int | None = None,
    max_call_depth: int = 64,
    explicit_memory_model: bool = True,
    approx_recips: bool = True,
    follow_loaded_calls: bool = True,
) -> tuple[sr.Runner, sharc.Image]:
    """The Runner/Image pair every entry point in this module starts from:
    ROOT called fresh (Runner.fresh_call()) on top of SNAPSHOT, a fresh
    run_init(), or (neither given) a bare Runner started directly at ROOT --
    see survey()'s own docstring for the two documented starting points and
    why a snapshot/run_init Runner needs fresh_call() rather than just
    overwriting its pc_sw. Shared by survey() and collect_all() so the two
    can never disagree about how a root is reached."""
    img = sharc.load(image)
    memory = sr._load_image_memory(image)

    if snapshot is not None:
        base = sr.load_snapshot(snapshot, memory)
        runner = base.fresh_call(
            root, regs=regs, return_address=return_address, diagnose_unknown=True
        )
    elif run_init:
        from tools.sharc_dev import (
            sharc_harness as h,  # lazy: only this path needs the harness
        )

        init = h.run_init(memory, image)
        if not init.ran or init.runner is None:
            raise SystemExit("run_init failed: %s" % init.error)
        runner = init.runner.fresh_call(
            root, regs=regs, return_address=return_address, diagnose_unknown=True
        )
    else:
        runner = sr.Runner(
            memory,
            root,
            regs=regs,
            follow_loaded_calls=follow_loaded_calls,
            max_call_depth=max_call_depth,
            explicit_memory_model=explicit_memory_model,
            approx_recips=approx_recips,
            diagnose_unknown=True,
        )
        if return_address is not None:
            runner.state.call_stack = [return_address]
    return runner, img


def survey(
    *,
    image: str,
    root: int,
    snapshot: str | None = None,
    run_init: bool = False,
    regs: Mapping[str | int, int | str] | None = None,
    return_address: int | None = None,
    patch_table: PatchTable | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_call_depth: int = 64,
    explicit_memory_model: bool = True,
    approx_recips: bool = True,
    follow_loaded_calls: bool = True,
    verbose: bool = False,
) -> SurveyStop:
    """Run ROOT from SNAPSHOT, a fresh run_init(), or (neither given) a
    bare Runner started directly at ROOT, applying PATCH_TABLE, and return
    the resulting SurveyStop. See _start_runner() for the two documented
    starting points and why a snapshot/run_init Runner needs
    Runner.fresh_call() rather than just overwriting its pc_sw."""
    runner, img = _start_runner(
        image=image,
        root=root,
        snapshot=snapshot,
        run_init=run_init,
        regs=regs,
        return_address=return_address,
        max_call_depth=max_call_depth,
        explicit_memory_model=explicit_memory_model,
        approx_recips=approx_recips,
        follow_loaded_calls=follow_loaded_calls,
    )
    patch_table = patch_table or {}

    t0 = time.perf_counter()
    halt = run_with_patches(runner, patch_table, max_steps, verbose=verbose)
    elapsed = time.perf_counter() - t0
    return SurveyStop(
        halt=halt,
        runner=runner,
        img=img,
        instructions=runner.instructions,
        elapsed=elapsed,
    )


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("image", help='sharc.py image name, e.g. "dt2-1.16"')
    p.add_argument(
        "--root", required=True, type=lambda x: int(x, 0), help="pc_sw to call"
    )
    start = p.add_mutually_exclusive_group()
    start.add_argument(
        "--snapshot",
        metavar="PATH",
        help="sr.save_snapshot() file to fresh_call() from",
    )
    start.add_argument(
        "--run-init",
        action="store_true",
        help="run tools/sharc_dev/sharc_harness.run_init() first",
    )
    p.add_argument(
        "--reg",
        dest="regs",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="register value for the fresh call (repeatable)",
    )
    p.add_argument(
        "--return-address",
        type=lambda x: int(x, 0),
        default=None,
        help="push this pc_sw as the call's return address",
    )
    p.add_argument("--patch-table", metavar="PATH", help=".json or .py PatchTable file")
    p.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    p.add_argument("--max-call-depth", type=int, default=64)
    p.add_argument("--slice-depth", type=int, default=DEFAULT_SLICE_DEPTH)
    p.add_argument(
        "--no-explicit-memory-model",
        dest="explicit_memory_model",
        action="store_false",
        default=True,
        help="bare-Runner mode only: do not assume unwritten internal RAM reads as 0",
    )
    p.add_argument(
        "--no-approx-recips",
        dest="approx_recips",
        action="store_false",
        default=True,
        help="bare-Runner mode only: do not substitute recips's approximate seed",
    )
    p.add_argument("--verbose", action="store_true", help="log every applied patch")
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--collect-all",
        action="store_true",
        help="do not stop at the first halt: guess through every binary fork "
        '(the patch table\'s own "branch" entries, else not-taken) and report '
        "every stop on the one path this makes, not just the first",
    )
    a = p.parse_args(argv)

    regs: dict[str | int, int | str] = {}
    for name, value in sr._parse_kv(a.regs, "--reg").items():
        regs[name] = int(value, 0)

    patch_table = load_patch_table(a.patch_table)

    if a.collect_all:
        result = collect_all(
            image=a.image,
            root=a.root,
            snapshot=a.snapshot,
            run_init=a.run_init,
            regs=regs,
            return_address=a.return_address,
            patch_table=patch_table,
            max_steps=a.max_steps,
            max_call_depth=a.max_call_depth,
            explicit_memory_model=a.explicit_memory_model,
            approx_recips=a.approx_recips,
            slice_depth=a.slice_depth,
            verbose=a.verbose,
        )
        if a.json:
            print(
                json.dumps(
                    {
                        "stops": [s.to_json() for s in result.stops],
                        "instructions": result.instructions,
                        "elapsed_s": result.elapsed,
                        "guesses": result.guesses,
                    },
                    indent=2,
                )
            )
        else:
            report_collect_all(result)
        return 0

    stop = survey(
        image=a.image,
        root=a.root,
        snapshot=a.snapshot,
        run_init=a.run_init,
        regs=regs,
        return_address=a.return_address,
        patch_table=patch_table,
        max_steps=a.max_steps,
        max_call_depth=a.max_call_depth,
        explicit_memory_model=a.explicit_memory_model,
        approx_recips=a.approx_recips,
        verbose=a.verbose,
    )

    if a.json:
        payload = stop.halt.to_json()
        payload["category"] = stop.category
        payload["instructions"] = stop.instructions
        payload["elapsed_s"] = stop.elapsed
        payload["last_writer"] = dict(stop.runner._last_writer)
        payload["call_stack"] = _call_stack_lines(stop.img, stop.runner)
        print(json.dumps(payload, indent=2))
    else:
        report_stop(stop, slice_depth=a.slice_depth)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
