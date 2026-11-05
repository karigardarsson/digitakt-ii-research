"""Concrete, single-path SHARC+ runner built on tools/sharc_dev/sharc_trace.py.

tools/sharc_dev/sharc_trace.py is a *symbolic* tracer: registers and memory it has not
been told about read back as ``Unknown``, an unresolved conditional forks
into several states, and every state deep-copies on each fork. That is right
for exploring what a routine's callers could pass; it is the wrong shape for
"run this one routine forward with real inputs to see what it actually
does" -- the question this module answers.

This module seeds every register with a real value (0, or the documented
SHARC+ reset value where sharc_trace already has one), keeps memory as the
loader image plus one flat write overlay (no per-path copies), and steps
:func:`tools.sharc_trace._execute` one instruction at a time. With fully
concrete inputs, every predicate sharc_trace would need to fork on should
already be resolvable, so this runner treats a fork -- or any state it
tracks as ``stopped`` -- as a hard stop: see :class:`Halt`.

    uv run python -m tools.sharc_dev.sharc_run dt2-1.16 --start 0x1c4ecf --max-steps 20000

Library use goes through :class:`Runner` directly, without sharc.py's
sqlite/networkx machinery, given any already-loaded
:class:`tools.sharcldr.LoadedMemory`:

    from tools.sharc_dev import sharc_run
    runner = sharc_run.Runner(loaded_memory, start=0x1c4ecf)
    result = runner.run(max_steps=20000)
    print(result.halt.reason, result.instructions_per_second)
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import os
import pickle
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc_trace as st  # noqa: E402
from tools.sharc_dev import sharcfn  # noqa: E402
from tools.sharc_dev.sharc_core.addressing import normal_word_to_byte  # noqa: E402
from tools.sharc_dev.sharc_core.memory import (  # noqa: E402
    UnmodeledMMR,
    _canonical_dm_address,
)
from tools.sharc_dev.sharc_core.periph import _sec_line  # noqa: E402
from tools.sharc_dev.sharc_core.sequencer import (  # noqa: E402
    _core_timer_tick,
    _enter_interrupt,
    _interrupt_candidate,
)
from tools.sharc_dev.sharc_disasm import Instruction  # noqa: E402
from tools.sharc_dev.sharcldr import LoadedMemory  # noqa: E402

# The firmware's own hardware DAG-modify reset values (docs/findings/06),
# duplicated from tools/sharc_dev/sharc.py's _DEFAULT_TRACE_REGS rather than importing
# sharc.py -- that module's sqlite/networkx machinery is otherwise unneeded
# here, and this runner also has to work from a bare LoadedMemory in tests.
DEFAULT_REGS: dict[str, int] = {
    "M5": 0,
    "M6": 1,
    "M7": -1,
    "M13": 0,
    "M14": 1,
    "M15": -1,
}


class Halt(Exception):
    """Why a single-path concrete run stopped.

    Every stop this runner recognises -- a natural tracer stop
    (``state.stopped``, which is also how a depth-0 RTS, a max-call-depth
    trip, or an unfollowable call surface), a breakpoint, running out of
    ``max_steps``, or a fork sharc_trace would need but concrete state
    cannot resolve -- is reported through this one exception so callers have
    a single place to look.
    """

    def __init__(
        self,
        reason: str,
        pc_sw: int,
        form: str | None = None,
        text: str = "",
        unknowns: tuple[str, ...] = (),
    ) -> None:
        self.reason = reason
        self.pc_sw = pc_sw
        self.form = form
        self.text = text
        # Populated only when the caller opted into Runner(diagnose_unknown=True)
        # (see _fork_diagnosis()): empty by default, so to_json()'s default
        # shape -- and therefore tests/sharc/integration/test_sharc_golden.py's hashes -- never
        # change for a caller that did not ask for this.
        self.unknowns = unknowns
        message = "%s at %#x (%s)" % (reason, pc_sw, form or "?")
        if text:
            message += ": " + text
        if unknowns:
            message += " [unknown: %s]" % ", ".join(unknowns)
        super().__init__(message)

    def to_json(self) -> dict:
        result = {
            "reason": self.reason,
            "pc_sw": self.pc_sw,
            "form": self.form,
            "text": self.text,
        }
        if self.unknowns:
            result["unknowns"] = list(self.unknowns)
        return result


# Forms whose predicate goes through sequencer._predicate_simd_branch()
# (PEx's condition, and -- unless PEx alone already settles it -- PEy's
# too, AND'ed) rather than plain sequencer._predicate() (PEx/ASTATX only):
# sharc_core/forms_flow.py's _type_25a_direct, the handler for all four of
# these type names.
_SIMD_BRANCH_FORMS = frozenset({"25a_direct", "25a_pcrel", "8a_abs", "8a_rel"})

# ASTATX/ASTATY bit number -> its PRM name (ch.4 REGF_ASTATX/REGF_ASTATY),
# read off the same sharc_core.encoding bit constants sharc_trace re-exports
# (never a second, hand-copied set of bit numbers) so a Halt's "ASTATX.BTF"
# can never disagree with tools/sharc_dev/sharc_core/flags.py's own bit assignment.
_ASTAT_BIT_NAME: dict[int, str] = {
    st.AZ_BIT: "AZ",
    st.AV_BIT: "AV",
    st.AN_BIT: "AN",
    st.AC_BIT: "AC",
    st.AS_BIT: "AS",
    st.AI_BIT: "AI",
    st.AF_BIT: "AF",
    st.MN_BIT: "MN",
    st.MV_BIT: "MV",
    st.MU_BIT: "MU",
    st.MI_BIT: "MI",
    st.SV_BIT: "SV",
    st.SZ_BIT: "SZ",
    st.SS_BIT: "SS",
    st.BTF_BIT: "BTF",
}

# The real UREGs a fork Halt's predicate could ever have read (see
# _fork_diagnosis' docstring): Runner(diagnose_unknown=True) keeps a last-
# writer PC for each of these (Runner._last_writer, updated once per step --
# see Runner.step()) so a Halt can name not just which bit was unknown but
# the last instruction that touched its register at all.
_DIAGNOSE_TRACKED_UREGS = ("ASTATX", "ASTATY", "MODE1")


def _fork_diagnosis(
    state: st.State,
    insn: Instruction,
    last_writer: Mapping[str, int] | None = None,
) -> tuple[str, ...]:
    """Condition name, exact register.bit(s), (when LAST_WRITER is given --
    Runner(diagnose_unknown=True) only, from its own Runner._last_writer)
    the last instruction that wrote each involved register, and (only when
    already available -- see note_reason() below) that register's own
    Unknown.reason, for a fork Halt's message (Runner(diagnose_unknown=True)
    only -- see Halt.unknowns' docstring note).

    Every fork this runner has ever raised (there are exactly 13 ``_copy()``
    call sites across sharc_core/forms_*.py) is reached only after
    ``sharc_core.sequencer._predicate(state, cond)`` -- or, for
    ``_SIMD_BRANCH_FORMS``, ``_predicate_simd_branch(state, cond)`` --
    returns None, which comes down to one of: ASTATX's (or, for a SIMD
    branch form, also ASTATY's) AF/AN/AZ/AV bits (SHARC+ PRM p.4-53's
    LT/GE/LE/GT rule, or a SIMPLE_COND_BITS flag), or MODE1 (EQ/NE's PEYEN
    check, LT/GE/LE/GT's ALUSAT term, or -- for a SIMD branch form only --
    ``_predicate_simd_branch``'s own PEx/PEy combination, which reads
    MODE1 for *every* cond, not only EQ/NE and LT/GE/LE/GT). Rather than
    re-deriving from sequencer's private control flow -- which this runner
    does not own and would either have to import or monkeypatch across a
    module boundary -- this reads the same registers those functions would,
    using only the already re-exported sharc_trace helpers, and reports
    every one of them that is not concretely known, as "REG.BIT" (e.g.
    "ASTATX.BTF") rather than the old, coarser "REG flag" -- see
    _ASTAT_BIT_NAME, sourced from the same sharc_core.encoding bit numbers
    flags.py itself defines them with, never a second hand-copied table.
    "MODE1" alone (no bit) is reported as before: it is read/write as one
    whole register here, never through _astatx_known_bit's per-bit view.

    This is deliberately *eager*, not a re-implementation of that
    short-circuiting: for LT/GE/LE/GT it checks AF, AN, AZ, AV and MODE1
    unconditionally, and for a SIMD branch form it always checks MODE1 and
    both PEs' flags, so it can occasionally name a register the real branch
    would not actually have consulted (e.g. AV/MODE1 when AF alone already
    made the AV/ALUSAT term moot, or ASTATY/MODE1 when PEx's own condition
    alone already settled a SIMD branch) -- but it never misses the true
    cause, since every register the relevant predicate function could
    possibly read for that cond is checked. A COND field this cannot find,
    or a fork not reached through either predicate function at all, reports
    no names rather than guessing -- and, in that case, no "cond=" prefix
    either (see the end of this function): a cond value alone, with no
    attributed cause, would be misleading noise, not a diagnosis.
    """
    try:
        cond = st._field(insn.fields, "cond")
    except (KeyError, TypeError):
        return ()

    names: list[str] = []

    def note(text: str) -> None:
        if text not in names:
            names.append(text)

    def note_writer(reg_name: str) -> None:
        # Cheap and always available once Runner.step() has populated
        # LAST_WRITER (a handful of dict lookups per step -- see its own
        # docstring); a bare register name with no last_writer entry means
        # nothing in this run ever wrote it (still seeded at reset, so this
        # is normal for e.g. MODE1 in a short run), not a bug.
        if last_writer is None:
            return
        pc = last_writer.get(reg_name)
        if pc is not None:
            note("last write to %s at %#x" % (reg_name, pc))

    def note_reason(reg_name: str, value: st.Value) -> None:
        # Cheap, and only ever fires when sharc_core already attached a
        # reason to this exact Value: a PartialConst (an otherwise-known
        # ASTATX/ASTATY with only some bits forgotten -- by far the common
        # case after an ordinary ALU/MULT/SHIFT compute) carries no reason
        # at all, so this is silent for it. A raw Unknown does carry one --
        # reachable here when the whole register was set from an Unknown
        # source verbatim (an explicit UREG move whose own source, or
        # MODE1/ASTATX/ASTATY itself, was never seeded/computed), including
        # a deliberately-unmodelled op's own documented reason (e.g.
        # BITEXT's "RN is always Unknown" note in compute_shift.py) when
        # that Unknown reached this register directly. Never invented here,
        # and sharc_core is not changed to manufacture one where it has
        # none today.
        if isinstance(value, st.Unknown):
            note("%s reason: %s" % (reg_name, value.reason))

    def astat_bit_unknown(reg_name: str, bit: int) -> None:
        astat = st._ureg_raw(state.uregs, st.UREG_CODES[reg_name])
        if st._astatx_known_bit(astat, bit) is None:
            note("%s.%s" % (reg_name, _ASTAT_BIT_NAME.get(bit, "bit%d" % bit)))
            note_writer(reg_name)
            note_reason(reg_name, astat)

    def mode1_unknown() -> None:
        mode1 = st._ureg(state.uregs, st.UREG_CODES["MODE1"])
        if not isinstance(mode1, st.Const):
            note("MODE1")
            note_writer("MODE1")
            note_reason("MODE1", mode1)

    simd_branch = insn.type_name in _SIMD_BRANCH_FORMS
    astat_regs = ("ASTATX", "ASTATY") if simd_branch else ("ASTATX",)

    if cond == 0x1F:  # TRUE: always resolves, in either predicate function.
        return ()
    # _predicate_simd_branch() reads MODE1 (_simd_active()) to decide
    # whether/how to combine PEx and PEy, for every cond -- not only the
    # EQ/NE and LT/GE/LE/GT cases plain _predicate() itself reads it for.
    if simd_branch:
        mode1_unknown()
    if cond in (0x00, 0x10):  # EQ / NE
        if not simd_branch:
            mode1_unknown()
        for reg in astat_regs:
            astat_bit_unknown(reg, st.AZ_BIT)
    elif cond in (0x01, 0x02, 0x11, 0x12):  # LT / GE / LE / GT
        for reg in astat_regs:
            astat_bit_unknown(reg, st.AF_BIT)
            astat_bit_unknown(reg, st.AN_BIT)
            astat_bit_unknown(reg, st.AZ_BIT)
            astat_bit_unknown(reg, st.AV_BIT)
        if not simd_branch:
            mode1_unknown()
    else:
        bits = st.SIMPLE_COND_BITS.get(cond)
        if bits is not None:
            bit, _negate = bits
            for reg in astat_regs:
                astat_bit_unknown(reg, bit)
    if names:
        names = ["cond=%s" % sharcfn.cond_name(cond)] + names
    return tuple(names)


def reset_uregs(
    overrides: Mapping[str | int, int | str] | None = None,
) -> dict[int, st.Value]:
    """Every UREG code seeded with a real value.

    sharc_trace.CORE_UREG_RESET_VALUES already documents the reset value for
    the core status/loop registers it needs for its own ``--core-reset-state``
    option; every other UREG (R0-15, I/M/L/B/S0-15, PX, TPERIOD, USTAT1-4,
    ...) resets to 0 on real SHARC+ hardware, and sharc_trace leaves an
    unseeded UREG as ``Unknown`` -- exactly the thing a concrete, single-path
    run cannot tolerate, since it is what forces a fork or a stop.
    """
    uregs: dict[int, st.Value] = {
        code: st.Const(st.CORE_UREG_RESET_VALUES.get(name, 0))
        for code, name in enumerate(st.UREG_NAMES)
    }
    for key, value in (overrides or {}).items():
        uregs[st._seed_code(key)] = st._seed_value(value)
    return uregs


def fresh_call_state(
    state: st.State,
    pc_sw: int,
    *,
    regs: Mapping[str | int, int | str] | None = None,
    return_address: int | None = None,
) -> st.State:
    """A copy of STATE ready to run forward as an independent call starting
    at PC_SW -- e.g. handing a post-init Runner's State to a *different*
    root than the one it actually halted at (tools/sharc_dev/sharc_survey.py's whole
    reason for existing: probing an arbitrary function from already-booted
    state without re-running init for every hypothesis).

    dataclasses.replace() keeps every scalar config field the source had
    (explicit_memory_model, approx_recips, follow_loaded_calls, ... -- the
    same reasoning tools/sharc_dev/sharc_harness/'s own _clone_state() docstring
    gives for why this is not sharc_core.state._copy(): that helper's
    positional State(...) construction always resets a clone's
    explicit_memory_model to False) and gives every mutable container this
    function itself might touch a fresh, independent copy, so running the
    result can never write back into STATE.

    Besides pc_sw, call_stack/loops/status_stack are all reset empty: PC_SW
    is a fresh call, not a continuation of wherever STATE's own pc_sw was.
    ``pending``/``stopped`` must ALSO be cleared, and are not covered by
    resetting those three alone -- a Runner that halted on its own RTS's
    "return without followed call" (how tools/sharc_dev/sharc_harness/'s run_init()
    recognises completion) still carries that pending delayed-branch
    completion and the stopped marker on its State, and either one would
    otherwise immediately re-fire as this call's very first step. This is
    a real bug the frame-render survey hit cloning a halted init Runner for
    a new root the naive way (pc/call_stack/loops/status_stack alone).

    REGS overrides/adds register values the same way Runner(regs=...) and
    reset_uregs()'s own ``overrides`` do, applied after the clone. Given, a
    single RETURN_ADDRESS becomes the new call_stack's only entry, so a
    "return without followed call" halt inside this call resolves against
    a return PC you actually chose; a halt where the sequencer's own RTS
    machinery computed a *different* return address than this one is
    reported by tools/sharc_dev/sharc_survey.py as its own category ("a prior poke
    or state is wrong"), not folded into an ordinary fork/mmr halt.
    """
    new_uregs = dict(state.uregs)
    for key, value in (regs or {}).items():
        new_uregs[st._seed_code(key)] = st._seed_value(value)
    result = dataclasses.replace(
        state,
        pc_sw=pc_sw,
        uregs=new_uregs,
        trace=[dict(event) for event in state.trace],
        overlay=dict(state.overlay),
        call_stack=[return_address] if return_address is not None else [],
        pc_stack=(
            [0x01000000 | (return_address & 0xFFFFFF)]
            if state.stack_model and return_address is not None
            else []
        ),
        pc_stack_pending=-1,
        pc_stack_requested=-1,
        loops=[],
        loop_depth=0,
        loop_slots=list(state.loop_slots),
        status_stack=[],
        mmrs=dict(state.mmrs),
        special=dict(state.special),
        bank_alt=dict(state.bank_alt),
        pending=None,
        stopped=None,
    )
    if result.stack_model:
        st._sync_pc_stack(result)
        st._sync_status_stack(result)
    return result


@dataclass(frozen=True)
class Watchpoint:
    """One DM byte-address range to watch: a ``start``/``end`` (exclusive)
    pair, whether it fires on a read, a write, or both, whether a hit
    stops the Runner or only logs it, and an optional ``label`` carried
    onto every :class:`WatchEvent` it produces.

    ``normal_word=True`` selects a normal-word address range, in word units;
    it is translated to bytes using the product memory map before attachment.
    Byte ranges remain the default, including an identical numeric address.

    ``start``/``end`` are given in the same terms a ``--poke`` or a
    disassembly listing uses -- an application DM pointer such as
    ``0x26968c`` -- not the loader's own byte-address alias. A watch
    against a still-unmapped low address (one the boot stream never wrote,
    so ``sharc_core.memory._dm_read()``/``_dm_write()`` only ever reach it
    at ``sharcldr.SW_ALIAS_BASE + address``) is therefore given in raw,
    unaliased terms too: this Watchpoint is resolved into the memory
    layer's own canonical address(es) once, when it is attached to a
    Runner (see ``_canonicalize_watchpoint()``/``_WatchTracker.
    canonicalize()``, both called from ``_install_watch_tracker()``),
    through ``sharc_core.memory._canonical_dm_address()`` -- the exact
    function ``_dm_read``/``_dm_write`` themselves call -- rather than a
    second, hand-rolled ``+SW_ALIAS_BASE`` rule that could drift from it.
    A range already given in canonical terms (already-mapped memory, an
    address at or above ``SW_ALIAS_BASE``, or an MMR) round-trips
    unchanged. ``covers()``/the write-overlap test in ``_WatchTracker``
    below only ever see the resolved, canonical range -- a caller reading
    ``Runner.watch_log``'s ``WatchEvent.address`` therefore also sees the
    canonical address a real access used, not the raw one this Watchpoint
    may have been constructed with.
    """

    start: int
    end: int
    on_read: bool = True
    on_write: bool = True
    stop: bool = True
    label: str = ""
    normal_word: bool = False

    def covers(self, address: int) -> bool:
        return self.start <= address < self.end


@dataclass(frozen=True)
class WatchEvent:
    """One watchpoint hit: PC, decoded form, the access, the touched
    address range (``[address, address + width)``), and old/new values as
    little-endian integers over that range (the old value is ``None`` for
    a read the image never had a defined byte for)."""

    pc_sw: int
    form: str | None
    access: str  # "read" or "write"
    address: int
    width: int
    old_value: int | None
    new_value: int | None
    label: str = ""

    def to_json(self) -> dict:
        return {
            "pc_sw": self.pc_sw,
            "form": self.form,
            "access": self.access,
            "address": self.address,
            "width": self.width,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "label": self.label,
        }

    def __str__(self) -> str:
        old = "?" if self.old_value is None else "%#x" % self.old_value
        new = "?" if self.new_value is None else "%#x" % self.new_value
        label = " [%s]" % self.label if self.label else ""
        return "%#x (%s) %s DM[%#x:%d] %s -> %s%s" % (
            self.pc_sw,
            self.form or "?",
            self.access,
            self.address,
            self.width,
            old,
            new,
            label,
        )


class _WatchpointStop(Exception):
    """Raised from inside a watched read/write (see _WatchingMemory /
    _WatchingOverlay below) to unwind out of sharc_core._execute() and
    become a Halt("watchpoint", ...) in Runner.step(); never seen outside
    this module."""

    def __init__(self, event: WatchEvent) -> None:
        self.event = event
        super().__init__(str(event))


def _canonicalize_watchpoint(state: st.State, wp: Watchpoint) -> tuple[Watchpoint, ...]:
    """WP's ``[start, end)`` translated into the DM byte-address space
    ``sharc_core.memory``'s ``_dm_read()``/``_dm_write()`` actually touch,
    via that module's own ``_canonical_dm_address()`` -- never a
    hand-rolled ``+SW_ALIAS_BASE`` -- so a Watchpoint given in raw,
    unaliased terms (see Watchpoint's own docstring) still matches once a
    real access resolves through the loader alias.

    Both endpoints are resolved with ``for_write=True`` (which never
    returns None: an as-yet-unmapped low address always aliases; anything
    else maps to itself -- see ``_canonical_dm_address``'s own docstring)
    and, when it resolves to a different address, also with
    ``for_write=False`` (which *can* return None -- a read of an address
    neither the loader nor an earlier write has ever made "mapped" never
    actually reaches ``state.concrete``/``state.overlay`` at all, so there
    is nothing for a read watchpoint to match yet). This never drops the
    write-side range; it only ever adds a second, read-side one when the
    two genuinely differ, so a Watchpoint that turns out not to need any
    resolving (already-canonical memory, an address at or above
    SW_ALIAS_BASE, an MMR) round-trips to exactly one, unchanged
    Watchpoint.

    Resolved once, at attach time (``_WatchTracker.canonicalize()``,
    called from ``_install_watch_tracker()``) -- not per-access -- because
    ``_canonical_dm_address(..., for_write=True)``'s answer for a given
    address does not depend on anything a later step of this same run
    does (an unmapped low address aliases from the very first write
    onward, never the reverse).
    """
    if wp.normal_word:
        start = normal_word_to_byte(wp.start)
        last = normal_word_to_byte(wp.end - 1) if wp.end > wp.start else start
        if (
            start is None
            or last is None
            or wp.end < wp.start
            or (wp.end > wp.start and last - start != (wp.end - wp.start - 1) * 4)
        ):
            raise ValueError(
                "normal-word watchpoint must stay within one mapped word window"
            )
        wp = dataclasses.replace(
            wp,
            start=start,
            end=last + 4 if wp.end > wp.start else start,
            normal_word=False,
        )
    seen: set[tuple[int, int]] = set()
    results: list[Watchpoint] = []
    for for_write in (True, False):
        start = _canonical_dm_address(state, wp.start, 1, for_write=for_write)
        if start is None:
            continue
        end = (
            start
            if wp.end == wp.start
            else _canonical_dm_address(state, wp.end, 1, for_write=for_write)
        )
        if end is None:
            end = start + (wp.end - wp.start)
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        results.append(dataclasses.replace(wp, start=start, end=end))
    return tuple(results)


class _WatchTracker:
    """Runner-owned bookkeeping for Watchpoint hits: which ranges to watch,
    the accumulating log (both stopping and log-only hits land here -- a
    stopping hit is also the one that raises), and per-step de-duplication
    for reads (see note_read()'s docstring)."""

    def __init__(self, watchpoints: Sequence[Watchpoint]) -> None:
        self.watchpoints = tuple(watchpoints)
        self.read_watchpoints = tuple(w for w in self.watchpoints if w.on_read)
        self.write_watchpoints = tuple(w for w in self.watchpoints if w.on_write)
        self.log: list[WatchEvent] = []
        self.pc_sw = 0
        self.form: str | None = None
        self._seen_reads: set[int] = set()
        self._canonicalized = False

    def canonicalize(self, state: st.State) -> None:
        """Replace ``self.watchpoints`` -- and the ``read_watchpoints``/
        ``write_watchpoints`` splits derived from it -- with each entry's
        canonical DM byte range(s) (see ``_canonicalize_watchpoint()``).
        Called exactly once, from ``_install_watch_tracker()``, with the
        State this tracker is about to watch; idempotent (a second call on
        the same tracker is a no-op) since nothing calls this more than
        once in practice today, but ``_canonicalize_watchpoint()`` is not
        itself safe to re-apply to its own output (an already-canonical,
        at-or-above-SW_ALIAS_BASE address is a no-op either way, so this
        guard is defensive, not load-bearing)."""
        if self._canonicalized:
            return
        self._canonicalized = True
        canonical: list[Watchpoint] = []
        for wp in self.watchpoints:
            canonical.extend(_canonicalize_watchpoint(state, wp))
        self.watchpoints = tuple(canonical)
        self.read_watchpoints = tuple(w for w in self.watchpoints if w.on_read)
        self.write_watchpoints = tuple(w for w in self.watchpoints if w.on_write)

    def begin_step(self, pc_sw: int, form: str | None) -> None:
        self.pc_sw = pc_sw
        self.form = form
        self._seen_reads.clear()

    def note_read(self, address: int, value: int) -> None:
        """A single byte at ADDRESS was read as VALUE. sharc_core.memory's
        _dm_read()/_canonical_dm_address() can probe the same address more
        than once while resolving one logical read (an unaliased-address
        check, then the real fetch) -- de-duplicated per step so one
        instruction's read of a watched address produces exactly one
        event/stop, however many times the underlying image is actually
        touched to serve it."""
        if address in self._seen_reads:
            return
        self._seen_reads.add(address)
        stop_event: WatchEvent | None = None
        for watchpoint in self.read_watchpoints:
            if not watchpoint.covers(address):
                continue
            event = WatchEvent(
                self.pc_sw, self.form, "read", address, 1, None, value, watchpoint.label
            )
            self.log.append(event)
            if watchpoint.stop and stop_event is None:
                stop_event = event
        if stop_event is not None:
            raise _WatchpointStop(stop_event)

    def note_write(self, address: int, width: int, old: int | None, new: int) -> None:
        stop_event: WatchEvent | None = None
        for watchpoint in self.write_watchpoints:
            if not (address < watchpoint.end and address + width > watchpoint.start):
                continue
            event = WatchEvent(
                self.pc_sw,
                self.form,
                "write",
                address,
                width,
                old,
                new,
                watchpoint.label,
            )
            self.log.append(event)
            if watchpoint.stop and stop_event is None:
                stop_event = event
        if stop_event is not None:
            raise _WatchpointStop(stop_event)


class _WatchingMemory(LoadedMemory):
    """A read-only proxy in front of a LoadedMemory that reports every
    byte read to TRACKER before returning it.

    Subclasses LoadedMemory -- rather than duck-typing it, as this class
    did before -- so ``isinstance(state.concrete, LoadedMemory)`` checks
    elsewhere still see a real LoadedMemory once a read watchpoint wraps
    ``state.concrete``. This matters concretely: ``sharc_core.sequencer.
    decode_at()`` branches on exactly that isinstance check to decide
    whether it can call ``decode_confident_loaded()`` (the LoadedMemory
    path, no ``base_sw`` needed) or must fall back to flat-image decoding,
    which raises ``ValueError("base_sw is required for flat image
    decoding")`` when ``base_sw`` is None -- always true for a concrete
    Runner (see ``make_state``). A register-indirect/delayed call or jump
    (e.g. a Type16a form) reaches this: ``sequencer._advance()`` calls
    ``decode_at(state.concrete, None, target)`` to decide whether the
    target is loaded code, so a duck-typed (non-LoadedMemory) proxy there
    crashed every such branch once any read watchpoint was active, even
    one that never actually covered the target address.

    ``__init__`` deliberately skips ``LoadedMemory.__init__`` (which parses
    a boot stream into ``.data``/``.blocks``/``._segments``): this proxy
    is a read-through wrapper around an already-built LoadedMemory, not a
    second loader image, and building the real one already did that work.
    Only ``.read()`` is overridden; every other LoadedMemory method this
    class inherits unmodified (``.read_sw()``, used by
    ``decode_confident_loaded()``/``decode_loaded_at()`` for the loader-
    backed decode path above) is implemented in terms of ``self.read()``
    on the base class, so it transparently goes through this override too
    -- there is no second copy of that address arithmetic here. Nothing
    else calls a LoadedMemory method on ``state.concrete`` today (checked
    across sharc_core and this module); if that changes, a method this
    class does not override would raise AttributeError against the
    never-populated ``.data``/``.blocks``/``._segments`` rather than
    silently reading the wrong bytes.
    """

    def __init__(self, backing: LoadedMemory, tracker: _WatchTracker) -> None:
        self._backing = backing
        self._tracker = tracker

    def read(self, address: int, size: int) -> bytes | None:
        raw = self._backing.read(address, size)
        if raw is not None and size == 1:
            self._tracker.note_read(address, raw[0])
        return raw


class _WatchingOverlay(dict):
    """State.overlay, instrumented for watchpoints. sharc_core.memory
    reads it with plain dict access (``here in state.overlay``,
    ``state.overlay[here]``) and writes it with one ``.update()`` call per
    store (``_dm_write``'s ``overlay.update(zip(range(addr, addr+width),
    raw))``) -- both are ordinary dict dispatch on this instance, so
    overriding them here is transparent to every module that already reads
    or writes ``state.overlay`` without going through Runner at all.

    Internal bookkeeping (looking up the byte before a write, replaying an
    already-applied write to compute its new value) reads via
    ``dict.__getitem__``/``dict.get`` directly, never through this
    instance's own overrides -- otherwise a watched write would report a
    phantom read of its own destination.
    """

    def __init__(self, tracker: _WatchTracker, backing: LoadedMemory) -> None:
        super().__init__()
        self._tracker = tracker
        self._backing = backing

    def _byte_before(self, address: int) -> int | None:
        if dict.__contains__(self, address):
            return dict.__getitem__(self, address)
        raw = self._backing.read(address, 1)
        return raw[0] if raw is not None else None

    def __getitem__(self, address):
        value = dict.__getitem__(self, address)
        if self._tracker.read_watchpoints:
            self._tracker.note_read(address, value)
        return value

    def update(self, *args, **kwargs):
        items = list(dict(*args, **kwargs).items())
        if not items:
            return
        if not self._tracker.write_watchpoints:
            dict.update(self, items)
            return
        addresses = [address for address, _ in items]
        lo, hi = min(addresses), max(addresses) + 1
        before = [self._byte_before(a) for a in range(lo, hi)]
        dict.update(self, items)
        after = [dict.__getitem__(self, a) for a in range(lo, hi)]
        old = (
            None
            if any(b is None for b in before)
            else int.from_bytes(bytes(b for b in before if b is not None), "little")
        )
        new = int.from_bytes(bytes(after), "little")
        self._tracker.note_write(lo, hi - lo, old, new)


def make_state(
    data: LoadedMemory,
    start: int,
    *,
    regs: Mapping[str | int, int | str] | None = None,
    pokes: Mapping[int, int] | None = None,
    follow_loaded_calls: bool = True,
    continue_external_calls: bool = False,
    assume_nw32: bool = True,
    max_call_depth: int = 64,
    provisional_forms: Sequence[str] = (),
    provisional_interpretations: Mapping[str, str] | None = None,
    explicit_memory_model: bool = False,
    approx_recips: bool = False,
    watch_tracker: _WatchTracker | None = None,
) -> st.State:
    """A fully concrete State ready to step, with sharc_trace's own
    _dm_write() used to apply pokes -- the same canonicalisation (loader
    alias, MMR routing, width gating) a real store instruction gets.

    ``provisional_forms``, ``provisional_interpretations`` and
    ``explicit_memory_model`` are all opt-in and change nothing when left
    at their defaults: see sharc_core/state.py's ``State.provisional_forms``
    /``State.provisional_interpretations``/``State.explicit_memory_model``
    docstrings. ``provisional_interpretations`` is a different mechanism
    from ``provisional_forms``: the latter gates *uncertain decode*, the
    former lets a *confirmed* decode with no confirmed execution semantics
    (e.g. 21p_undoc16) run as a named MODE (only "nop" is implemented)
    instead of stopping.
    ``approx_recips`` is sharc_trace.py's own pre-existing State field
    (State.approx_recips), exposed here too: a voice render's pitch/rate
    math (docs/findings/06's "voice record contract") goes through recips,
    whose ROM seed is undocumented without it.

    ``watch_tracker``, an already-constructed :class:`_WatchTracker` (see
    Runner's own ``watchpoints`` option), swaps in a watching overlay
    and/or a watching memory proxy *after* POKES are applied through the
    ordinary, untracked overlay -- so seeding a voice record before a run
    never itself trips a watchpoint on one of its own fields. Left at the
    default (None), ``state.overlay``/``state.concrete`` are exactly what
    they always were: a plain dict and DATA itself.
    """
    if not isinstance(data, LoadedMemory):
        raise ValueError(
            "sharc_run needs a LoadedMemory image; concrete execution has "
            "nothing else to read memory from"
        )
    combined_regs: dict[str | int, int | str] = dict(DEFAULT_REGS.items())
    combined_regs.update(regs or {})
    mmrs: dict[int, st.Value] = {
        address: st.Const(value) for address, value in st.CORE_MMR_RESET_VALUES.items()
    }
    state = st.State(
        pc_sw=start,
        uregs=reset_uregs(combined_regs),
        concrete=data,
        base_sw=None,
        follow_loaded_calls=follow_loaded_calls,
        continue_external_calls=continue_external_calls,
        max_call_depth=max_call_depth,
        assume_nw32=assume_nw32,
        core_reset_state=True,
        mmrs=mmrs,
        record_events=False,
        provisional_forms=tuple(provisional_forms),
        provisional_interpretations=dict(provisional_interpretations or {}),
        explicit_memory_model=explicit_memory_model,
        approx_recips=approx_recips,
    )
    for address, value in sorted((pokes or {}).items()):
        if not st._dm_write(state, address, 4, st.Const(value & 0xFFFFFFFF)):
            raise ValueError(
                "poke at %#x did not take effect (outside a mapped DM "
                "region, or not 32-bit-normal-word addressable)" % address
            )
    if watch_tracker is not None:
        _install_watch_tracker(state, watch_tracker, data)
    return state


def _install_watch_tracker(
    state: st.State, tracker: _WatchTracker, backing: LoadedMemory
) -> None:
    """Swap STATE's overlay/concrete for watching versions reporting to
    TRACKER -- the one place make_state() and Runner.attach_watchpoints()
    both do this, so the two ways of getting watchpoints onto a Runner (at
    construction, or bolted onto an already-running/restored one) install
    them identically. Always re-wraps (even if a previous, now-stale
    tracker was already installed), copying whatever bytes the current
    overlay holds into the new one, so calling this again with a different
    TRACKER retargets watching without losing state.

    The overlay is wrapped whenever there is *any* watchpoint, not only a
    write one: sharc_core.memory's _dm_read() reads an already-written
    byte straight out of ``state.overlay`` (``state.overlay[here]``), so a
    read-only watchpoint still needs ``_WatchingOverlay.__getitem__`` to
    see a byte this run wrote earlier -- only ``state.concrete`` (bytes
    that came from the loaded image itself) is conditioned on
    ``read_watchpoints`` alone.

    TRACKER's own watchpoints are resolved to canonical DM addresses here
    (``tracker.canonicalize(state)``, idempotent -- see its docstring)
    before either wrapper is built, so both ``_WatchingOverlay`` and
    ``_WatchingMemory`` always match against the same address space a real
    ``_dm_read``/``_dm_write`` touches, whatever terms the caller gave
    Runner(watchpoints=...)/attach_watchpoints() in (see Watchpoint's own
    docstring).
    """
    tracker.canonicalize(state)
    if tracker.watchpoints:
        current = dict(state.overlay)
        watching_overlay = _WatchingOverlay(tracker, backing)
        dict.update(watching_overlay, current)
        state.overlay = watching_overlay
    if tracker.read_watchpoints:
        already_watching = state.concrete
        unwrapped = (
            already_watching._backing
            if isinstance(already_watching, _WatchingMemory)
            else backing
        )
        state.concrete = _WatchingMemory(unwrapped, tracker)


@dataclass
class RunResult:
    halt: Halt
    instructions: int
    elapsed: float
    form_counts: collections.Counter[str]
    start_pc_sw: int
    final_pc_sw: int
    max_call_depth_reached: int
    # Empty unless Runner(watchpoints=...) was given at least one watchpoint
    # (see Runner.watch_log's docstring): to_json() omits the key entirely
    # in that (default) case, so a caller that never asked for watchpoints
    # sees byte-identical JSON to before this field existed.
    watch_log: tuple[WatchEvent, ...] = ()
    # Empty unless Runner(provisional_interpretations=...) was given at
    # least one form and this run actually executed it at least once (see
    # sharc_core.state.State.provisional_interpretations/
    # provisional_interpreted). (form, mode, count) triples, sorted by
    # form name. to_json() omits the "provisional" key entirely when this
    # is empty, so a caller that never opted in sees byte-identical JSON
    # to before this field existed -- the default run and the goldens are
    # unaffected.
    provisional: tuple[tuple[str, str, int], ...] = ()

    @property
    def instructions_per_second(self) -> float:
        return self.instructions / self.elapsed if self.elapsed > 0 else float("inf")

    def to_json(self) -> dict:
        result = {
            "start_pc_sw": self.start_pc_sw,
            "final_pc_sw": self.final_pc_sw,
            "instructions": self.instructions,
            "elapsed_s": self.elapsed,
            "instructions_per_second": self.instructions_per_second,
            "max_call_depth_reached": self.max_call_depth_reached,
            "halt": self.halt.to_json(),
            "form_counts": dict(self.form_counts.most_common()),
        }
        if self.watch_log:
            result["watch_log"] = [event.to_json() for event in self.watch_log]
        if self.provisional:
            result["provisional"] = [
                {"form": form, "mode": mode, "count": count}
                for form, mode, count in self.provisional
            ]
        return result


class Runner:
    """Steps one State through sharc_trace._execute, one instruction at a
    time, with a decode cache and no per-step trace/copy overhead.

    decode_at() (sharc_trace.py) reads only the immutable loader image, not
    a path's write overlay, so a cached Instruction can never go stale from
    a store this runner itself performs -- the firmware is not documented
    to self-modify PM either. invalidate() exists as a deliberate safety
    net anyway (call it with a pc_sw if that ever changes); it is not wired
    to every store automatically because nothing currently can make it
    fire, and a heuristic that fired on every store would defeat the cache.
    """

    def __init__(
        self,
        data: LoadedMemory,
        start: int,
        *,
        regs: Mapping[str | int, int | str] | None = None,
        pokes: Mapping[int, int] | None = None,
        follow_loaded_calls: bool = True,
        continue_external_calls: bool = False,
        assume_nw32: bool = True,
        max_call_depth: int = 64,
        breakpoints: Sequence[int] = (),
        provisional_forms: Sequence[str] = (),
        provisional_interpretations: Mapping[str, str] | None = None,
        explicit_memory_model: bool = False,
        approx_recips: bool = False,
        watchpoints: Sequence[Watchpoint] = (),
        diagnose_unknown: bool = False,
    ) -> None:
        self.data = data
        self._watch = _WatchTracker(watchpoints) if watchpoints else None
        self.state = make_state(
            data,
            start,
            regs=regs,
            pokes=pokes,
            follow_loaded_calls=follow_loaded_calls,
            continue_external_calls=continue_external_calls,
            assume_nw32=assume_nw32,
            max_call_depth=max_call_depth,
            provisional_forms=provisional_forms,
            provisional_interpretations=provisional_interpretations,
            explicit_memory_model=explicit_memory_model,
            approx_recips=approx_recips,
            watch_tracker=self._watch,
        )
        # Off by default: computing it is cheap (it only ever runs once, on
        # the Halt that ends a run -- see _fork_diagnosis()), but printing
        # or serialising it changes Halt.to_json()'s shape, and
        # tests/sharc/integration/test_sharc_golden.py hashes that shape (see Halt.unknowns'
        # docstring note).
        self.diagnose_unknown = diagnose_unknown
        # PC of the last step() that reassigned each _DIAGNOSE_TRACKED_UREGS
        # entry in state.uregs (see step()'s docstring): only maintained
        # when diagnose_unknown is True, so a caller that never asked for
        # diagnosis pays nothing beyond the one dict. The UREG codes are
        # looked up once here, not by name every step: step()'s own
        # tracking is a handful of tuple-indexed dict accesses and identity
        # comparisons, not a fresh dict/name lookup per instruction.
        self._last_writer: dict[str, int] = {}
        self._diagnose_codes = tuple(
            st.UREG_CODES[name] for name in _DIAGNOSE_TRACKED_UREGS
        )
        self.breakpoints = frozenset(breakpoints)
        self.instructions = 0
        self.form_counts: collections.Counter[str] = collections.Counter()
        self.max_call_depth_reached = 0
        self._cache: dict[int, Instruction] = {}

    @property
    def watch_log(self) -> tuple[WatchEvent, ...]:
        """Every Watchpoint hit so far (stopping or log-only), oldest
        first. Always empty when this Runner has no watchpoints."""
        return tuple(self._watch.log) if self._watch is not None else ()

    def attach_watchpoints(self, watchpoints: Sequence[Watchpoint]) -> None:
        """Replace this Runner's watchpoints (installing State.overlay/
        concrete's watching wrappers if this Runner had none yet -- see
        _install_watch_tracker()). Mainly for a Runner from load_snapshot(),
        which never has any (see its docstring): a fresh Runner can just
        pass ``watchpoints=`` to its constructor instead."""
        self._watch = _WatchTracker(watchpoints) if watchpoints else None
        if self._watch is not None:
            _install_watch_tracker(self.state, self._watch, self.data)

    def invalidate(self, pc_sw: int) -> None:
        """Evict pc_sw from the decode cache. See the class docstring for
        why nothing calls this automatically today."""
        self._cache.pop(pc_sw, None)

    def fresh_call(
        self,
        pc_sw: int,
        *,
        regs: Mapping[str | int, int | str] | None = None,
        return_address: int | None = None,
        diagnose_unknown: bool | None = None,
    ) -> Runner:
        """A new Runner for a fresh call at PC_SW, built from this Runner's
        *current* State via fresh_call_state() (see its docstring for
        exactly what that clears, and why -- pending/stopped, not just
        pc/call_stack/loops/status_stack). Typically called on a Runner
        this run reached by stepping/load_snapshot() to some already-booted
        point, to then probe a different function from there without
        re-running everything up to that point again for every hypothesis
        (tools/sharc_dev/sharc_survey.py's main use of this).

        Shares this Runner's ``data`` (the loaded image, immutable) and
        decode cache (keyed only by pc_sw, so reuse is always safe and
        saves re-decoding anything both runs execute) -- but nothing else:
        watchpoints are not carried over (pass them again via
        ``attach_watchpoints()`` on the result, if needed), and
        instructions/form_counts/max_call_depth_reached all start fresh at
        zero, since this is a new call being measured on its own, not a
        continuation of this Runner's own counters. ``diagnose_unknown``
        defaults to this Runner's own setting.
        """
        new_state = fresh_call_state(
            self.state, pc_sw, regs=regs, return_address=return_address
        )
        new_runner = Runner.__new__(Runner)
        new_runner.data = self.data
        new_runner.state = new_state
        new_runner._watch = None
        new_runner.diagnose_unknown = (
            self.diagnose_unknown if diagnose_unknown is None else diagnose_unknown
        )
        new_runner._last_writer = {}
        new_runner._diagnose_codes = self._diagnose_codes
        new_runner.breakpoints = self.breakpoints
        new_runner.instructions = 0
        new_runner.form_counts = collections.Counter()
        new_runner.max_call_depth_reached = 0
        new_runner._cache = self._cache
        return new_runner

    def _decode(self, pc_sw: int) -> Instruction:
        insn = self._cache.get(pc_sw)
        if insn is None:
            insn = st.decode_at(self.data, None, pc_sw)
            self._cache[pc_sw] = insn
        return insn

    def step(self) -> None:
        """Execute exactly one instruction, or raise Halt.

        When ``diagnose_unknown`` is set, this also updates
        ``self._last_writer`` (see __init__): before executing, it snapshots
        the ``state.uregs`` *object* currently stored for each of
        ``_DIAGNOSE_TRACKED_UREGS`` (ASTATX, ASTATY, MODE1), and afterwards
        records this step's pc_sw as that register's last writer wherever
        the stored object was replaced (an ``is not`` identity check, not a
        value comparison: sharc_core's ``_astatx_*``/UREG-move helpers
        always build a fresh ``Value`` when a form's handler actually
        assigns a uregister, and never touch the dict entry at all
        otherwise, so identity alone already means "this step's handler
        wrote it" -- including a write that happens to reproduce the same
        bits, and excluding every step that never touched the register).
        Three plain dict-by-int-code reads before and after (self._diagnose_
        codes, computed once in __init__, not looked up by name here), only
        when diagnose_unknown is on -- no comprehension, no per-step name
        lookup: see tools/sharc_dev/sharc_survey.py's own instr/s measurement and
        this module's docstring for what that costs in practice.
        """
        state = self.state
        state.timer_written = False
        if state.software_interrupts or state.core_timer or state.peripheral_model:
            try:
                allowed = (
                    (0xF0000000 if state.software_interrupts else 0)
                    | (0x00400800 if state.core_timer else 0)
                    | (0x00008000 if state.peripheral_model else 0)
                )
                if state.peripheral_model:
                    _sec_line(state)
                candidate = _interrupt_candidate(state, allowed)
                if candidate:
                    _enter_interrupt(state, candidate)
            except ValueError as error:
                raise Halt("interrupt", state.pc_sw, text=str(error)) from error
        if state.pc_sw in self.breakpoints:
            raise Halt("breakpoint", state.pc_sw)
        insn = self._decode(state.pc_sw)
        if self._watch is not None:
            self._watch.begin_step(state.pc_sw, insn.type_name)
        pc_sw = state.pc_sw
        diagnosing = self.diagnose_unknown
        if diagnosing:
            astatx_code, astaty_code, mode1_code = self._diagnose_codes
            uregs = state.uregs
            before_astatx = uregs[astatx_code]
            before_astaty = uregs[astaty_code]
            before_mode1 = uregs[mode1_code]
        try:
            out = st._execute(state, insn)
        except UnmodeledMMR as exc:
            raise Halt(
                "mmr",
                state.pc_sw,
                insn.type_name,
                "unmodeled MMR %#x (%s)" % (exc.address, exc.name or "unnamed"),
            ) from exc
        except _WatchpointStop as exc:
            raise Halt(
                "watchpoint", state.pc_sw, insn.type_name, str(exc.event)
            ) from exc
        if len(out) != 1:
            unknowns = (
                _fork_diagnosis(state, insn, self._last_writer)
                if self.diagnose_unknown
                else ()
            )
            raise Halt(
                "fork (%d successors): a predicate or address went Unknown "
                "despite concrete input -- see the form's _execute branch "
                "for what read an unseeded value" % len(out),
                state.pc_sw,
                insn.type_name,
                insn.note,
                unknowns=unknowns,
            )
        state = out[0]
        self.state = state
        if diagnosing:
            uregs = state.uregs
            if uregs[astatx_code] is not before_astatx:
                self._last_writer["ASTATX"] = pc_sw
            if uregs[astaty_code] is not before_astaty:
                self._last_writer["ASTATY"] = pc_sw
            if uregs[mode1_code] is not before_mode1:
                self._last_writer["MODE1"] = pc_sw
        if state.stopped:
            raise Halt(state.stopped, state.pc_sw, insn.type_name, insn.note)
        self.instructions += 1
        # sharc_disasm.disassemble() always sets type_name to a decode-table
        # name or the literal string "unknown", never None (see the same
        # assertion in sharc_core/forms.py's _execute).
        assert insn.type_name is not None, "instruction with no type_name"
        self.form_counts[insn.type_name] += 1
        depth = len(state.call_stack)
        if depth > self.max_call_depth_reached:
            self.max_call_depth_reached = depth
        if state.core_timer:
            try:
                _core_timer_tick(state)
            except ValueError as error:
                raise Halt("timer", state.pc_sw, text=str(error)) from error

    def run(self, max_steps: int) -> RunResult:
        start_pc_sw = self.state.pc_sw
        start_time = time.perf_counter()
        halt: Halt
        try:
            for _ in range(max_steps):
                self.step()
        except Halt as exc:
            halt = exc
        else:
            halt = Halt("max-steps", self.state.pc_sw)
        elapsed = time.perf_counter() - start_time
        interpreted = collections.Counter(self.state.provisional_interpreted)
        provisional = tuple(
            (form, self.state.provisional_interpretations[form], count)
            for form, count in sorted(interpreted.items())
        )
        return RunResult(
            halt=halt,
            instructions=self.instructions,
            elapsed=elapsed,
            form_counts=self.form_counts,
            start_pc_sw=start_pc_sw,
            final_pc_sw=self.state.pc_sw,
            max_call_depth_reached=self.max_call_depth_reached,
            watch_log=self.watch_log,
            provisional=provisional,
        )


# Snapshot file format (bumped whenever the pickled shape below changes in
# a way load_snapshot() cannot read forward-compatibly).
_SNAPSHOT_VERSION = 1


def _image_sha256(data: LoadedMemory) -> str:
    """The same digest tests/sharc/integration/test_sharc_golden.py's image_sha256() computes
    over the .bin file directly: LoadedMemory.data is that file's bytes
    verbatim (tools/sharc_dev/sharc.py's Image._mem()), so this is stable across a
    save_snapshot()/load_snapshot() round trip run against the same image,
    however it was loaded."""
    return hashlib.sha256(data.data).hexdigest()


def save_snapshot(runner: Runner, path: str) -> None:
    """Save RUNNER's State -- everything except its immutable ``concrete``
    LoadedMemory, which load_snapshot() reattaches -- plus Runner's own
    counters, to PATH.

    File format: a single pickled ``dict`` (pickle protocol
    ``pickle.HIGHEST_PROTOCOL``; this is a same-tree debugging aid, not an
    interchange format, so it is not guaranteed stable across a Python
    version, a sharc_run.py/sharc_core change that alters State's or
    Halt's dataclass fields, or ``_SNAPSHOT_VERSION``) with three keys:

    * ``"version"`` -- ``_SNAPSHOT_VERSION`` (int); load_snapshot() refuses
      any other value.
    * ``"image_sha256"`` -- hex sha256 of the LoadedMemory bytes this state
      came from (see ``_image_sha256()``); load_snapshot() refuses a
      mismatch rather than silently attaching a state to the wrong image.
    * ``"state"`` -- one entry per ``dataclasses.fields(State)`` except
      ``"concrete"``, keyed by field name (so a State field sharc_core
      later adds round-trips automatically -- ``State(**state_fields)``
      just gets its default; removing one is a shape change and belongs
      behind a ``_SNAPSHOT_VERSION`` bump). ``"overlay"`` is always saved
      as a
      plain ``dict``, even when a Watchpoint made it a ``_WatchingOverlay``
      at run time: a restored Runner has no watchpoints, so there is
      nothing for that subclass to do, and its own ``_backing``
      (a whole LoadedMemory) would otherwise get pickled right alongside
      the real one.
    * ``"runner"`` -- Runner's own non-State bookkeeping: ``instructions``,
      ``form_counts``, ``max_call_depth_reached``, ``breakpoints``.

    The decode cache (``Runner._cache``) is deliberately NOT saved: it
    holds only ``Instruction`` records decoded from the image itself, so
    it costs nothing but a little wall-clock time to rebuild lazily after
    a restore (the very first ``step()`` re-decodes each PC once), and
    that is a much smaller risk than pickling ``sharc_disasm.Instruction``
    across whatever changed between save and load.
    """
    state = runner.state
    state_fields: dict[str, object] = {}
    for f in dataclasses.fields(state):
        if f.name == "concrete":
            continue
        value = getattr(state, f.name)
        state_fields[f.name] = dict(value) if f.name == "overlay" else value
    payload = {
        "version": _SNAPSHOT_VERSION,
        "image_sha256": _image_sha256(runner.data),
        "state": state_fields,
        "runner": {
            "instructions": runner.instructions,
            "form_counts": dict(runner.form_counts),
            "max_call_depth_reached": runner.max_call_depth_reached,
            "breakpoints": sorted(runner.breakpoints),
        },
    }
    with open(path, "wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)


def load_snapshot(path: str, data: LoadedMemory) -> Runner:
    """The inverse of save_snapshot(): a new Runner for DATA -- the same
    image the snapshot was saved from, checked by sha256 (see
    save_snapshot()'s docstring); a mismatch raises ValueError rather than
    attaching a state to the wrong image -- with its State restored
    exactly and DATA reattached as ``State.concrete``. The restored
    Runner has no watchpoints and diagnose_unknown=False regardless of
    what the saved Runner had (see save_snapshot()'s docstring: neither is
    part of the persisted state); pass ``watchpoints=``/set
    ``.diagnose_unknown`` again on the result if that run needs them.
    """
    with open(path, "rb") as fh:
        payload = pickle.load(fh)
    if payload.get("version") != _SNAPSHOT_VERSION:
        raise ValueError(
            "sharc_run snapshot %r: unsupported version %r (expected %d)"
            % (path, payload.get("version"), _SNAPSHOT_VERSION)
        )
    want = _image_sha256(data)
    got = payload["image_sha256"]
    if got != want:
        raise ValueError(
            "sharc_run snapshot %r is for image sha256 %s, not the loaded "
            "image's %s" % (path, got, want)
        )
    runner = Runner.__new__(Runner)
    runner.data = data
    state_fields = dict(payload["state"])
    state_fields["concrete"] = data
    runner.state = st.State(**state_fields)
    saved_runner = payload["runner"]
    runner.breakpoints = frozenset(saved_runner["breakpoints"])
    runner.instructions = saved_runner["instructions"]
    runner.form_counts = collections.Counter(saved_runner["form_counts"])
    runner.max_call_depth_reached = saved_runner["max_call_depth_reached"]
    runner._cache = {}
    runner._watch = None
    runner.diagnose_unknown = False
    runner._last_writer = {}
    runner._diagnose_codes = tuple(
        st.UREG_CODES[name] for name in _DIAGNOSE_TRACKED_UREGS
    )
    return runner


def _parse_kv(items: Sequence[str], flag: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in items:
        try:
            name, value = item.split("=", 1)
        except ValueError:
            raise SystemExit("%s must be NAME=VALUE, got %r" % (flag, item)) from None
        values[name] = value
    return values


def _parse_watch_arg(
    spec: str, *, on_read: bool, on_write: bool, stop: bool
) -> Watchpoint:
    """START:END[:LABEL] (hex, END exclusive) -> Watchpoint, for the CLI's
    --watch-write/--watch-read/--watch-log-write/--watch-log-read."""
    parts = spec.split(":", 2)
    if len(parts) < 2:
        raise SystemExit("--watch...: expected START:END[:LABEL], got %r" % spec)
    start, end = int(parts[0], 16), int(parts[1], 16)
    label = parts[2] if len(parts) > 2 else ""
    return Watchpoint(
        start, end, on_read=on_read, on_write=on_write, stop=stop, label=label
    )


def _load_image_memory(name: str) -> LoadedMemory:
    """tools/sharc_dev/sharc.py's Image._mem(), reused rather than duplicated -- it
    already knows how the DB name maps to out/sections/<name>/section_7_BLOB.bin.
    Imported lazily so a library caller (or a test building its own
    LoadedMemory) never pays for sharc.py's sqlite3/networkx imports."""
    from tools.sharc_dev import sharc

    img = sharc.load(name)
    if img.meta.get("cpu") == "coldfire":
        raise SystemExit(
            "sharc_run.py: %r is a ColdFire image; this runner only "
            "executes SHARC+ code" % name
        )
    return img._mem()


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("image", help='sharc.py image name, e.g. "dt2-1.16"')
    p.add_argument(
        "--start",
        default=None,
        type=lambda x: int(x, 0),
        help="required unless --load-snapshot supplies a starting state",
    )
    p.add_argument("--max-steps", type=int, default=100_000)
    p.add_argument(
        "--poke",
        dest="pokes",
        action="append",
        default=[],
        metavar="ADDR=HEX",
        help="seed a 32-bit DM word before running (repeatable)",
    )
    p.add_argument(
        "--reg",
        dest="regs",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="override one register's reset value (repeatable)",
    )
    p.add_argument(
        "--breakpoint",
        dest="breakpoints",
        action="append",
        default=[],
        type=lambda x: int(x, 0),
        metavar="PC_SW",
        help="stop before executing this short-word PC (repeatable)",
    )
    p.add_argument("--max-call-depth", type=int, default=64)
    p.add_argument(
        "--no-follow-calls",
        dest="follow_loaded_calls",
        action="store_false",
        default=True,
        help="stop at the first call into loaded code instead of entering it",
    )
    p.add_argument(
        "--allow-provisional",
        dest="provisional_forms",
        action="append",
        default=[],
        metavar="FORM",
        help="execute this decode-table-unconfirmed form instead of "
        "halting on it (repeatable); sets state.provisional_forms. A form "
        "the runtime decoder marks uncertain (e.g. 21p_undoc16) needs this "
        "*and* --provisional FORM=MODE below to reach the handler at all",
    )
    p.add_argument(
        "--provisional",
        dest="provisional_interp",
        action="append",
        default=[],
        metavar="FORM=MODE",
        help="run FORM (a *confirmed*-decode form with no confirmed "
        "execution semantics, e.g. 21p_undoc16) as MODE instead of "
        "halting on it (repeatable; only MODE=nop is implemented); off by "
        "default, and the result/Halt JSON reports every form this fired "
        "on and how many times (sharc_core.State."
        "provisional_interpretations, distinct from --allow-provisional-"
        "form above, which is about uncertain decode, not confirmed-"
        "decode-but-unknown-semantics)",
    )
    p.add_argument(
        "--explicit-memory-model",
        action="store_true",
        default=False,
        help="internal RAM never written by the loader or this run reads "
        "as 0 instead of Unknown; a core/system MMR with no known reset "
        "value and no --poke halts naming the register instead of forking "
        "(sharc_core.State.explicit_memory_model; off by default)",
    )
    p.add_argument(
        "--approx-recips",
        action="store_true",
        default=False,
        help="substitute a documented-but-unverified numeric model for "
        "recips's undocumented ROM seed instead of leaving it Unknown "
        "(sharc_core.State.approx_recips, same as tools/sharc_dev/sharc_trace.py's "
        "own --approx-recips; off by default)",
    )
    p.add_argument(
        "--diagnose-unknown",
        action="store_true",
        default=False,
        help="on a fork halt, name the register(s)/flag(s) that read "
        "Unknown (Halt.unknowns); off by default, since computing it "
        "changes --json's halt shape (see Halt.unknowns' docstring)",
    )
    p.add_argument(
        "--watch-write",
        dest="watch_write",
        action="append",
        default=[],
        metavar="START:END[:LABEL]",
        help="stop when a store touches [START,END) (hex, exclusive end; repeatable)",
    )
    p.add_argument(
        "--watch-read",
        dest="watch_read",
        action="append",
        default=[],
        metavar="START:END[:LABEL]",
        help="stop when a load touches [START,END) (repeatable)",
    )
    p.add_argument(
        "--watch-log-write",
        dest="watch_log_write",
        action="append",
        default=[],
        metavar="START:END[:LABEL]",
        help="log (without stopping) every store touching [START,END) (repeatable)",
    )
    p.add_argument(
        "--watch-log-read",
        dest="watch_log_read",
        action="append",
        default=[],
        metavar="START:END[:LABEL]",
        help="log (without stopping) every load touching [START,END) (repeatable)",
    )
    p.add_argument(
        "--load-snapshot",
        metavar="PATH",
        default=None,
        help="resume from a Runner state saved with save_snapshot() "
        "instead of starting fresh at --start (which is then not needed)",
    )
    p.add_argument(
        "--save-snapshot",
        metavar="PATH",
        default=None,
        help="save the Runner's ending state to PATH with save_snapshot()",
    )
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    if a.start is None and a.load_snapshot is None:
        p.error("--start is required unless --load-snapshot is given")

    pokes: dict[int, int] = {}
    for name, value in _parse_kv(a.pokes, "--poke").items():
        # HEX per --help, but int(value, 0) also takes a "0x..."-prefixed
        # value without double-interpreting it.
        pokes[int(name, 0)] = (
            int(value, 0) if value.lower().startswith("0x") else int(value, 16)
        )

    regs: dict[str | int, int | str] = {}
    for name, value in _parse_kv(a.regs, "--reg").items():
        regs[name] = int(value, 0)

    provisional_interpretations = _parse_kv(a.provisional_interp, "--provisional")
    for form, mode in provisional_interpretations.items():
        if mode != "nop":
            p.error("--provisional %s=%s: only MODE=nop is implemented" % (form, mode))

    watchpoints = [
        *(
            _parse_watch_arg(spec, on_read=False, on_write=True, stop=True)
            for spec in a.watch_write
        ),
        *(
            _parse_watch_arg(spec, on_read=True, on_write=False, stop=True)
            for spec in a.watch_read
        ),
        *(
            _parse_watch_arg(spec, on_read=False, on_write=True, stop=False)
            for spec in a.watch_log_write
        ),
        *(
            _parse_watch_arg(spec, on_read=True, on_write=False, stop=False)
            for spec in a.watch_log_read
        ),
    ]

    memory = _load_image_memory(a.image)
    if a.load_snapshot:
        runner = load_snapshot(a.load_snapshot, memory)
        if watchpoints:
            runner.attach_watchpoints(watchpoints)
        if a.diagnose_unknown:
            runner.diagnose_unknown = True
        if a.breakpoints:
            runner.breakpoints = frozenset(a.breakpoints)
    else:
        runner = Runner(
            memory,
            a.start,
            regs=regs,
            pokes=pokes,
            follow_loaded_calls=a.follow_loaded_calls,
            max_call_depth=a.max_call_depth,
            breakpoints=a.breakpoints,
            provisional_forms=a.provisional_forms,
            provisional_interpretations=provisional_interpretations,
            explicit_memory_model=a.explicit_memory_model,
            approx_recips=a.approx_recips,
            watchpoints=watchpoints,
            diagnose_unknown=a.diagnose_unknown,
        )
    result = runner.run(a.max_steps)

    if a.save_snapshot:
        save_snapshot(runner, a.save_snapshot)

    if a.json:
        print(json.dumps(result.to_json(), indent=2))
    else:
        print("start:  %#x" % result.start_pc_sw)
        print("final:  %#x" % result.final_pc_sw)
        print("instructions: %d" % result.instructions)
        print(
            "elapsed: %.3fs (%.0f instr/s)"
            % (result.elapsed, result.instructions_per_second)
        )
        print("max call depth reached: %d" % result.max_call_depth_reached)
        print(
            "halt: %s at %#x (%s)%s"
            % (
                result.halt.reason,
                result.halt.pc_sw,
                result.halt.form,
                (": " + result.halt.text) if result.halt.text else "",
            )
        )
        if result.halt.unknowns:
            print("unknown operands: %s" % ", ".join(result.halt.unknowns))
        if result.watch_log:
            print("watch events:")
            for event in result.watch_log:
                print("  %s" % event)
        if result.provisional:
            print("provisional (opt-in, not a semantics claim):")
            for form, mode, count in result.provisional:
                print("  %-16s = %-4s x%d" % (form, mode, count))
        print("top forms:")
        for form, count in result.form_counts.most_common(15):
            print("  %-16s %d" % (form, count))
    if a.save_snapshot:
        print("saved snapshot: %s" % a.save_snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
