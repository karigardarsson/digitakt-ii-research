"""Generate the firmware part of the native SHARC+ core: block code.

tools/sharc_dev/sharc_transpile.py translates tools/sharc_dev/sharc_core into Rust (the
``core_i``/``core_g`` modules). This generator writes what depends on the
image, under out/ (it embeds firmware):

- ``blocks_NN.rs``: region functions. Each basic block (``bblocks`` in the
  program database, or a PC the interpreter was seen entering, ``--entries``)
  becomes a body: its instructions run through the transpiled
  ``forms._execute`` partially evaluated in block mode (Translator.blk):
  the decoded fields, the PC, "no delayed transfer pending" and MODE1 are
  constants, and the registers live in the region's local register file
  (rt.rs Rf) from entry to exit, so LLVM keeps them in host registers.
  Registers the region reads must be known at entry (their known-bit
  masks become constants); a value that would become unknown, or a trap in
  the core, undoes the instruction (its written registers from copies, the
  rest from the state's undo log) and leaves it to the one-instruction
  interpreter. Bodies whose blocks follow each other often (``--transitions``)
  share a region and pass control directly, within ``--region-insns``
  instructions and ``--region-regs`` registers (a region's registers are
  live across it).
- ``spec_NN.rs``: the specialised core functions the bodies call.
- ``insns.rs``: the instruction statics the code still names.
- ``image.rs``: includes the above, the PC -> block table, the image's DO
  loop ends and hash; ``insns.bin`` holds every decoded instruction for the
  interpreter. ``--decode-range START:END`` adds short-word PCs absent from
  the program database, such as a loader entry and its following zero fill.
  It uses the ordinary loaded-memory decoder and adds no execution shortcuts.

Which blocks to generate, and for which MODE1 values, comes from where the
interpreter ran (``sharc-frames --coverage``: the executed set, not the whole
image). It also (re)writes the transpiled core next to them, so the interned
string numbers the block code uses are the core's.

    sharc-frames PACK --coverage COV           # interpreter only
    sharc-frames PACK --interp-coverage I --transitions T   # a first build
    uv run python -m tools.sharc_dev.sharc_rsgen dt2-1.16 --coverage COV \\
        --entries I.entries --transitions T --out out/native/opt/gen
    SHARC_GEN_DIR=$PWD/out/native/opt/gen cargo build --release \\
        --manifest-path packages/emulator/sharc/Cargo.toml
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

DEFAULT_OUT = os.path.join(ROOT, "out", "native", "gen", "tx")


def decode_range(text: str) -> tuple[int, int]:
    """An explicit, bounded half-open range of short-word execution PCs."""
    try:
        start, end = (int(part, 0) for part in text.split(":"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "expected START:END, e.g. 0x1c1338:0x1c13e6"
        ) from exc
    if not 0 <= start < end <= 1 << 24 or end - start > 1 << 20:
        raise argparse.ArgumentTypeError(
            "range must fit 24-bit PCs and contain at most 1048576 short words"
        )
    return start, end


@dataclass
class Block:
    start: int
    end: int
    insns: list  # [(pc, Instruction)]


def ident(pc: int) -> str:
    return "%X" % pc


def field_entry(syms, key: str, value: int) -> str:
    """rt::FieldEntry(key, stem, hi, lo, value) for decoded field KEY."""
    stem, _, rng = key.partition("[")
    hi = lo = -1
    if rng.endswith("]") and ":" in rng:
        h, _, lo_text = rng[:-1].partition(":")
        if h.isdigit() and lo_text.isdigit():
            hi, lo = int(h), int(lo_text)
    return "FieldEntry(%s, %s, %d, %d, %d)" % (
        syms.ident(key),
        syms.ident(stem),
        hi,
        lo,
        value,
    )


def split_compute(fields: dict[str, int]) -> dict[str, int]:
    """encoding._split_compute_fields, applied at generation time to every
    instruction with a whole ``compute`` field (the runtime's
    _split_compute_fields is then the identity)."""
    if "compute" not in fields:
        return dict(fields)
    out = dict(fields)
    out["compute[22:16]"] = fields["compute"] >> 16
    out["compute[15:0]"] = fields["compute"] & 0xFFFF
    return out


def insn_static(syms, pc: int, insn) -> str:
    fields = split_compute(insn.fields)
    entries = [field_entry(syms, k, v) for k, v in fields.items()]
    if len(entries) > 12:
        raise ValueError("decoded instruction has more than 12 fields")
    entries.extend(["FieldEntry(S_EMPTY, S_EMPTY, -1, -1, 0)"] * (12 - len(entries)))
    length = "None" if insn.length_bytes is None else "Some(%d)" % insn.length_bytes
    return (
        "static I_%s: Insn = Insn { type_name: %s, fields: Fields::from_entries([%s], %d), "
        "length_bytes: %s, kind: %s, offset: %d };"
        % (
            ident(pc),
            syms.ident(insn.type_name or "unknown"),
            ", ".join(entries),
            len(fields),
            length,
            syms.ident(insn.kind),
            insn.offset,
        )
    )


# --- model-safe blocks -----------------------------------------------------
#
# The native core can run with opt-in models on (register banks, physical
# stacks, core timer, software IRQs, peripherals, instruction clock). Block
# code is the partial evaluation of the same core, and the core reads those
# models' switches at run time, so what block code does not do is what the
# *engine* does between instructions: complete a pending bank or PC-stack
# change, tick the core timer, take an interrupt, set EMUCLK, step a
# peripheral. A block is "model-safe" when none of those can matter inside
# it, given the engine's entry gate (packages/emulator/sharc/src/lib.rs block_plan):
#
# - it writes none of MODE1 (a bank request), PCSTKP/PCSTK/MODE1STK/LADDR/
#   CURLCNTR (stack models), IRPTL/IMASK/IMASKP/MMASK (interrupt state),
#   TCOUNT/TPERIOD/MODE2 (timer) and neither reads nor writes EMUCLK, TCOUNT
#   (they are advanced by instruction count, not per instruction);
# - it holds no instruction that pushes or pops a stack explicitly (PUSH/POP),
#   aborts a loop, returns from or enters/leaves an interrupt (RTI, CI), and
#   no other system-register form (DO loops and the instruction at a loop's
#   last address are allowed: block code runs the core's own loop logic, with
#   loop_depth restored if the instruction traps);
# - what it has left is compute, moves, DAG modifies, memory accesses, jumps,
#   calls and RTS (also the (DB) return idiom, CJUMP and RFRAME: the stack
#   pushes and pops of a call or return are the core's own steps, and only a
#   PCSTKP write requests the PC-stack completion the engine does between
#   instructions) and the 21a/21c no-operations. Stores the peripheral model
#   acts on leave the block at run time (TRAP_BLOCK_MODEL).
MODEL_REG_NAMES = (
    "PCSTK",
    "PCSTKP",
    "LADDR",
    "CURLCNTR",
    "EMUCLK",
    "EMUCLK2",
    "TPERIOD",
    "TCOUNT",
    "MODE1",
    "MMASK",
    "MODE2",
    "IRPTL",
    "IMASK",
    "IMASKP",
    "MODE1STK",
)
# Jumps are model-safe when they are not loop aborts (a) or interrupt-driven
# (ci). Calls (b) and the (DB) register-indirect return idiom are too: the PC
# and call stacks they push and pop are the core's own (translated) steps, and
# neither requests a PC-stack truncation (only a PCSTKP write does, which the
# register rule below excludes), so nothing the engine completes between
# instructions differs. The same holds for RTS (Type 11a/11c with x=0; RTI,
# x=1, changes IMASKP/IRPTL/MODE1 and is excluded), CJUMP and RFRAME (frame
# pointer moves plus a jump or call) and the Type 21a/21c no-operations.
JUMP_FORMS = ("8a_abs", "8a_rel", "9a_abs", "9a_rel", "9b_abs", "9b_rel")
RETURN_FORMS = ("11a", "11c")
SAFE_FLOW_FORMS = ("25a_direct", "25a_pcrel", "25c_rframe", "21a", "21c")
SAFE_FORM_MODULES = ("forms_compute", "forms_move", "forms_dag")
# DO loop setup. With the physical stacks a DO also pushes a loop slot and a PC
# stack entry and the loop's last instruction pops or updates them; those are
# the core's own (translated) steps, and a trap after them restores the one
# scalar (loop_depth) the undo log does not hold (body_lines).
LOOP_FORMS = ("12a_imm", "12a_ureg")


def _field_or(fields: dict, stem: str, default: int = 0) -> int:
    from tools.sharc_dev.sharc_core.encoding import _field

    try:
        return _field(fields, stem)
    except KeyError:
        return default


def ureg_codes(fields: dict) -> set[int]:
    """Every UREG code an instruction names in a register field."""
    codes = set()
    for key, value in fields.items():
        if "ureg" not in key or key.startswith("srcureg"):
            continue
        codes.add(value & 0x7F)
    if any(k.startswith("srcureg") for k in fields):
        codes.add(
            (
                _field_or(fields, "srcureghigh") << 2
                | _field_or(fields, "srcureglow[1:1]") << 1
                | _field_or(fields, "srcureglow[0:0]")
            )
            & 0x7F
        )
    return codes


def model_unsafe_reason(insn, pc: int, loop_end_set: frozenset) -> str | None:
    """Why INSN (at PC) is not model-safe, or None when it is."""
    from tools.sharc_dev.sharc_core.encoding import UREG_CODES
    from tools.sharc_dev.sharc_core.forms import FORMS

    name = insn.type_name or "unknown"
    f = insn.fields
    handler = FORMS.get(name)
    if handler is None:
        return "no handler"
    if name in JUMP_FORMS:
        if _field_or(f, "a") or _field_or(f, "ci"):
            return "loop abort or CI jump"
    elif name in RETURN_FORMS:
        if _field_or(f, "x"):
            return "RTI"
    elif name not in LOOP_FORMS and name not in SAFE_FLOW_FORMS:
        module = handler.__module__.rsplit(".", 1)[-1]
        if module not in SAFE_FORM_MODULES or "undoc" in name:
            return "form %s (%s)" % (name, module)
    bad = {UREG_CODES[n] for n in MODEL_REG_NAMES}
    named = ureg_codes(f) & bad
    if named:
        names = {v: k for k, v in UREG_CODES.items()}
        return "register " + ", ".join(sorted(names[c] for c in named))
    return None


# Named model registers decided by what the instruction's variant reads and
# writes (model_regs_verdict) rather than by naming alone:
# - reading one is safe, except EMUCLK/EMUCLK2/TCOUNT, which the engine
#   advances per instruction only outside block code;
# - writing MODE1, IRPTL, IMASK, IMASKP or MMASK is safe as a body's last
#   instruction (TERMINAL): the engine then does what the interpreter's
#   commit does after it (St::bank_complete moves a MODE1 bank request to
#   pending) and takes an interrupt it enabled at the next boundary, as
#   after an interpreted instruction;
# - writing any other model register is not.
# Type 18a bit operations name their system register in "sreg" (USTAT1 +
# sreg), which the same rule decides.
TERMINAL = "terminal"
TERMINAL_WRITE_NAMES = ("MODE1", "IRPTL", "IMASK", "IMASKP", "MMASK")
UNSAFE_READ_NAMES = ("EMUCLK", "EMUCLK2", "TCOUNT")


def keeps_loop_sync(pc: int, insn) -> bool:
    """Whether a model-safe body's instruction INSN at PC leaves the loop
    stack (loop_depth, loop_slots) and LADDR/CURLCNTR as they were after its
    own leading sync, so the next instruction's sync (forms._execute) would
    write the same values. In a model-safe body only a DO and the
    instruction at a DO loop's end change them: loop aborts, PUSH/POP and
    writes of LADDR, CURLCNTR or the PC stack registers are never in one
    (model_unsafe_reason, model_regs_verdict), and calls and returns leave
    the loop stack alone. Outside --model-safe every instruction syncs."""
    if not MODEL_SAFE_ONLY:
        return False
    return not (pc in LOOP_END_SET or (insn.type_name or "") in LOOP_FORMS)


def model_regs_decide(why: str) -> bool:
    """Whether a model_unsafe_reason WHY is about named registers only, so
    the instruction's register reads and writes decide."""
    return why.startswith("register ") or why == "form 18a (forms_system)"


def _named_model_regs(insn) -> set[int]:
    from tools.sharc_dev.sharc_core.encoding import UREG_CODES

    named = ureg_codes(insn.fields)
    if (insn.type_name or "") == "18a":
        named.add(UREG_CODES["USTAT1"] + _field_or(insn.fields, "sreg"))
    return named & {UREG_CODES[n] for n in MODEL_REG_NAMES}


def model_regs_verdict(insn, regs: tuple) -> str | None:
    """None (model-safe), TERMINAL (safe as a body's last instruction) or
    why not, from the variant's (reads, snapshot reads, writes) REGS of the
    model registers INSN names."""
    from tools.sharc_dev.sharc_core.encoding import UREG_CODES

    names = {v: k for k, v in UREG_CODES.items()}
    reads, olds, writes = regs
    named = _named_model_regs(insn)
    bad_read = {c for c in named & (reads | olds) if names[c] in UNSAFE_READ_NAMES}
    if bad_read:
        return "reads " + ", ".join(sorted(names[c] for c in bad_read))
    written = named & writes
    if not written:
        return None
    bad = {c for c in written if names[c] not in TERMINAL_WRITE_NAMES}
    if bad:
        return "register " + ", ".join(sorted(names[c] for c in bad))
    return TERMINAL


def terminal_names(insn, regs: tuple) -> str:
    from tools.sharc_dev.sharc_core.encoding import UREG_CODES

    names = {v: k for k, v in UREG_CODES.items()}
    return ", ".join(sorted(names[c] for c in _named_model_regs(insn) & regs[2]))


@dataclass
class Body:
    """One block specialised on one MODE1 value (None: not a fact), as a
    body of its region's function."""

    block: Block
    mode1: int | None
    idx: int
    # (pc, insn, variant path, (reads, snapshot reads, writes),
    #  pending known none, MODE1 fact after it (None: unknown))
    steps: list
    stopped: tuple | None
    read_any: set
    writes: set


def plan_body(block: Block, tr, mode1: int | None, idx: int) -> Body | None:
    """Specialise BLOCK's instructions on their decode, their PC, "no
    delayed transfer pending" until an instruction may start one, and MODE1
    while it is known (tracking the value an instruction leaves)."""
    from tools.sharc_dev import sharc_transpile as tp

    steps: list = []
    pending_none = True
    stopped: tuple | None = None
    entry_mode1 = mode1
    for i, (pc, insn) in enumerate(block.insns):
        why = None
        if MODEL_SAFE_ONLY:
            # --model-safe: the body ends before the first instruction a
            # model could make differ; the interpreter runs it and the
            # blocks resume at the next entry.
            why = model_unsafe_reason(insn, pc, LOOP_END_SET)
            if why is not None and not model_regs_decide(why):
                if i == 0:
                    UNSAFE_REASONS[block.start] = why
                    return None
                stopped = (pc, insn, why)
                break
        facts: dict = {"pc_sw": pc}
        if pending_none:
            facts["pending"] = None
        if mode1 is not None:
            facts["MODE1"] = mode1
        if steps and keeps_loop_sync(steps[-1][0], steps[-1][1]):
            # Step i-1 ran right before this one (a body is straight-line;
            # any other way in enters at a body's first step, which syncs)
            # and changed neither the loop stack nor LADDR/CURLCNTR.
            facts["loop_synced"] = True
        try:
            path = tr.variant("sharc_core.forms._execute", {"insn": insn}, facts)
        except tp.TranspileError as exc:
            # The one-instruction interpreter runs it (and what follows).
            tr.block_failures.append((pc, str(exc)))
            if i == 0:
                return None
            stopped = (pc, insn, exc)
            break
        if tr.variant_memwrites.get(path, True) and not tr.variant_memreads.get(
            path, True
        ):
            # No memory read: its writes need no undo (see MEM_READS).
            with contextlib.suppress(tp.TranspileError):
                path = tr.variant(
                    "sharc_core.forms._execute",
                    {"insn": insn},
                    {**facts, "nolog": True},
                )
        terminal = False
        if why is not None:
            # Named model registers: what the variant reads and writes of
            # them decides (model_regs_verdict).
            verdict = model_regs_verdict(insn, tr.variant_regs[path])
            if verdict not in (None, TERMINAL):
                if i == 0:
                    UNSAFE_REASONS[block.start] = verdict
                    return None
                stopped = (pc, insn, verdict)
                break
            terminal = verdict == TERMINAL
        effects = tr.variant_effects.get(path, {"pending", "MODE1"})
        if "pending" in effects:
            pending_none = False
        if "MODE1" in effects:
            mode1 = tr.variant_facts_out.get(path, {}).get("MODE1")
        steps.append((pc, insn, path, tr.variant_regs[path], "pending" in facts, mode1))
        if terminal:
            # The body ends after it: the engine completes the instruction
            # (a requested bank change) and checks interrupts at the next
            # boundary, as after an interpreted instruction.
            after = block.insns[i + 1] if i + 1 < len(block.insns) else (pc, insn)
            stopped = (
                after[0],
                after[1],
                "after a write of " + terminal_names(insn, tr.variant_regs[path]),
            )
            break
    read_any = set().union(*(r | o for _p, _i, _x, (r, o, _w), _n, _m in steps))
    writes = set().union(*(w for _p, _i, _x, (_r, _o, w), _n, _m in steps))
    return Body(block, entry_mode1, idx, steps, stopped, read_any, writes)


def route(
    body: Body,
    mode1_after: int | None,
    by_start: dict,
    succ: set | None,
    indent: str,
    chain: dict | None = None,
) -> list[str]:
    """Where control goes when an instruction of BODY leaves the PC off its
    straight line: another body of the region (the one for the MODE1 value
    known here, or chosen by the run-time MODE1), a block of another region
    seen to follow it (CHAIN: pc -> index, a direct call after the
    write-back), else the dispatcher."""
    arms = []
    starts = sorted(by_start)
    # Chained successors (blocks of other regions) are one out-of-line
    # function per distinct set, called for every PC that is not a body of
    # this region: the compare tree is not repeated at every exit site.
    fallback = "break 'r crate::EXIT_NEXT,"
    if succ is not None:
        starts = [p for p in starts if p in succ or p == body.block.start]
        if chain is not None:
            ks = []
            for pc in sorted(succ):
                if pc in by_start:
                    continue
                ks.append((pc, chain.setdefault(pc, len(chain))))
            if ks:
                fn = TAIL.chain_fn(tuple(ks))
                fallback = "break 'r %s(rf.pc as u32)," % fn
    for pc in starts:
        targets = by_start[pc]  # [Body]
        if pc == body.block.start and (body.mode1 is None or body.mode1 == mode1_after):
            # Back to its own start (a DO loop body): no entry checks again.
            arms.append("%s    %#x => continue 'b%d," % (indent, pc, body.idx))
            continue
        exact = [t for t in targets if t.mode1 is None]
        if exact:
            t = exact[0]
            arms.append("%s    %#x => { at = %d; continue 'r; }" % (indent, pc, t.idx))
            continue
        if mode1_after is not None:
            same = [t for t in targets if t.mode1 == mode1_after]
            if same:
                arms.append(
                    "%s    %#x => { at = %d; continue 'r; }" % (indent, pc, same[0].idx)
                )
            continue
        sel = ", ".join("%#x => %d" % (t.mode1, t.idx) for t in targets)
        arms.append(
            "%s    %#x => { let m = rf.r[%d]; if m.m != u32::MAX { break 'r crate::EXIT_NEXT; } "
            "at = match m.b { %s, _ => break 'r crate::EXIT_NEXT }; continue 'r; }"
            % (indent, pc, MODE1, sel)
        )
    if not arms:
        return [indent + fallback.replace(",", ";")]
    return (
        [indent + "match rf.pc as u32 {"]
        + arms
        + [indent + "    _ => " + fallback, indent + "}"]
    )


class Tail:
    """What the region function being generated shares between its exit
    sites: the out-of-line chain functions and whether any site traps (one
    trap tail per region: the sites only record the PC, the trap and the
    pending transfer)."""

    def __init__(self) -> None:
        self.name = ""
        self.trap = False
        self.fns: dict[tuple, str] = {}

    def reset(self, name: str) -> None:
        self.name = name
        self.trap = False
        self.fns = {}

    def chain_fn(self, ks: tuple) -> str:
        if ks not in self.fns:
            self.fns[ks] = "%s_c%d" % (self.name, len(self.fns))
        return self.fns[ks]

    def chain_fn_text(self) -> str:
        out = []
        for ks, name in self.fns.items():
            out.append("#[inline(never)]")
            out.append("fn %s(pc: u32) -> u32 {" % name)
            out.append("    match pc {")
            for pc, k in ks:
                out.append("        %#x => crate::EXIT_CHAIN + %d," % (pc, k))
            out.append("        _ => crate::EXIT_NEXT,")
            out.append("    }")
            out.append("}")
            out.append("")
        return "\n".join(out)


TAIL = Tail()


# --split-budget: two copies of each body (one without per-step budget checks);
# about 2% faster, about twice the generated source and compile time. Off by default.
SPLIT_BUDGET = False
# --step-budget: with the models on, check the budget before every instruction
# (the pre-2026-10-10 form). Off: a body is entered only when it fits under the
# limit, else the call ends at once with no progress and the interpreter runs
# up to the limit (exact state; every early exit edge costs about 80 bytes of
# register fix-up, so the per-instruction form was 12% of the AOT code).
STEP_BUDGET = False
TR = None  # the translator of the current generation (tr_logs)


def tr_logs(path: str) -> bool:
    """Whether the instruction's code writes the undo log."""
    return TR is None or TR.variant_logs.get(path, True)


def _loop_empty_exit(pc: int, ind: str) -> list[str]:
    """After an instruction at a DO loop end: when the engine asked for it
    (a latched interrupt that only the loop stack defers) and the stack is
    now empty, end the call here like a budget exit (state written back)."""
    if pc not in LOOP_END_SET:
        return []
    return [
        ind + "if s.stop_at_loop_empty && s.loops.n == 0 {",
        ind + "    break 'r crate::EXIT_BUDGET;",
        ind + "}",
    ]


def _after_step(i, n, pc, insn, m_after, body, by_start, succ, chain, ind):
    """Control flow after step I of N: the PC check against the straight line
    and the exit of the body's last step."""
    lines: list[str] = []
    last = i + 1 == n
    if not last:
        nxt = pc + insn.length_bytes // 2
        lines.append(ind + "if rf.pc != %#x {" % nxt)
        lines.extend(route(body, m_after, by_start, succ, ind + "    ", chain))
        lines.append(ind + "}")
    elif body.stopped is None:
        lines.extend(route(body, m_after, by_start, succ, ind, chain))
    else:
        spc, sinsn, exc = body.stopped
        lines.append(
            ind + "// %#x %s: not specialized (%s)" % (spc, sinsn.type_name, exc)
        )
        lines.append(ind + "break 'r crate::EXIT_NEXT;")
    return lines


def body_lines(
    body: Body,
    by_start: dict,
    succ: set | None,
    chain: dict | None = None,
    *,
    allow_unknown: bool = False,
) -> list[str]:

    ind = "            "
    n = len(body.steps)
    known = [] if allow_unknown else sorted(body.read_any)
    lines = [
        "        %d => {" % body.idx,
        "            // %#x%s"
        % (
            body.block.start,
            "" if body.mode1 is None else " with MODE1 = %#x" % body.mode1,
        ),
    ]
    if known:
        # Registers read must be known (their masks become constants).
        lines.append(
            "            if (%s) != u32::MAX {"
            % " & ".join("rf.r[%d].m" % c for c in known)
        )
        lines.append("                core::hint::cold_path();")
        lines.append("                break 'r crate::EXIT_BUDGET;")
        lines.append("            }")
    lines += [
        "            'b%d: loop {" % body.idx,
        "            // Entered at the start with no delayed transfer pending (the",
        "            // dispatcher single-steps otherwise).",
    ]
    if MODEL_SAFE_ONLY and STEP_BUDGET:
        # With the models on, the limit is often the core timer's (the
        # instruction before its expiry), which a whole body would often
        # overshoot: then check before each instruction and stop there, so
        # the interpreter only runs from the limit on.
        lines += [
            "            if rf.pending.is_some() {",
            "                core::hint::cold_path();",
            "                break 'r crate::EXIT_BUDGET;",
            "            }",
            "            let short = s.icount + ic + %d > s.limit;" % n,
        ]
    else:
        lines += [
            "            if s.icount + ic + %d > s.limit || rf.pending.is_some() {" % n,
            "                core::hint::cold_path();",
            "                break 'r crate::EXIT_BUDGET;",
            "            }",
        ]

    def emit(checked: bool) -> list[str]:
        lines: list[str] = []
        for i, (pc, insn, path, (_r, o, w), no_pending, m_after) in enumerate(
            body.steps
        ):
            lines.append(ind + "// %#x %s" % (pc, insn.type_name))
            if MODEL_SAFE_ONLY and STEP_BUDGET and checked:
                # `short` (the limit falls inside this body) is rare: the
                # exact stop test is out of line.
                lines.append(ind + "if short {")
                lines.append(ind + "    core::hint::cold_path();")
                lines.append(ind + "    if s.icount + ic >= s.limit {")
                lines.append(ind + "        break 'r crate::EXIT_BUDGET;")
                lines.append(ind + "    }")
                lines.append(ind + "}")
            for c in sorted(o):
                lines.append(ind + "rf.o[%d] = rf.r[%d];" % (c, c))
            # A step whose code cannot return Err has no trap path: no undo copies,
            # no restore (the transpiler proves it per variant, _can_trap).
            traps = TR is None or TR.variant_traps.get(path, True)
            if not traps:
                lines.append(ind + "let _ = %s(s, &mut rf);" % path)
                if tr_logs(path):
                    lines.append(ind + "s.un = 0;")
                lines.append(ind + "ic += 1;")
                lines.extend(_loop_empty_exit(pc, ind))
                lines.extend(
                    _after_step(
                        i, n, pc, insn, m_after, body, by_start, succ, chain, ind
                    )
                )
                continue
            for c in sorted(w):
                lines.append(ind + "let sv%d = rf.r[%d];" % (c, c))
            # What a trap restores besides registers (the undo log holds the
            # rest): the PC is this instruction's, the pending transfer is
            # known to be none unless a delay slot.
            if not no_pending:
                lines.append(ind + "let pd0 = rf.pending;")
            loopy = pc in LOOP_END_SET or (insn.type_name or "") in LOOP_FORMS
            if loopy:
                # The physical loop stack's depth is not in the undo log.
                lines.append(ind + "let ld%d = s.loop_depth;" % i)
            banky = MODE1 in w
            if banky:
                # Nor is a MODE1 write's bank request.
                lines.append(ind + "let bk%d = s.bank_requested_mask;" % i)
            if TR is not None and path in TR.variant_inline:
                # The instruction only moves the PC.
                lines.append(ind + TR.variant_inline[path])
                lines.append(ind + "if false {")
                lines.append(ind + "    let t = Trap(0);")
            else:
                lines.append(ind + "if let Err(t) = %s(s, &mut rf) {" % path)
                lines.append(ind + "    core::hint::cold_path();")
            for c in sorted(w):
                lines.append(ind + "    rf.r[%d] = sv%d;" % (c, c))
            if loopy:
                lines.append(ind + "    s.loop_depth = ld%d;" % i)
            if banky:
                lines.append(ind + "    s.bank_requested_mask = bk%d;" % i)
            # One trap tail per region (region_text): the site records the
            # PC, the trap and the pending transfer.
            TAIL.trap = True
            lines.append(ind + "    tp = %#x;" % pc)
            if not no_pending:
                lines.append(ind + "    tpd = pd0;")
            lines.append(ind + "    tt = t;")
            lines.append(ind + "    break 'r crate::EXIT_TRAP;")
            lines.append(ind + "}")
            if tr_logs(path):
                lines.append(ind + "s.un = 0;")
            lines.append(ind + "ic += 1;")
            lines.extend(_loop_empty_exit(pc, ind))
            lines.extend(
                _after_step(i, n, pc, insn, m_after, body, by_start, succ, chain, ind)
            )
        return lines

    if MODEL_SAFE_ONLY and STEP_BUDGET and SPLIT_BUDGET:
        # Two copies of the steps: when the body fits under the limit (the
        # usual case) the copy without per-step budget checks runs; a body
        # that would cross the limit runs the checked copy, which stops
        # before the exact instruction as before.
        lines.append("            if short {")
        lines.extend(emit(True))
        lines.append("            } else {")
        lines.extend(emit(False))
        lines.append("            }")
    else:
        lines.extend(emit(True))
    lines.append("            }")
    lines.append("        }")
    return lines


def region_text(
    name: str,
    bodies: list[Body],
    succ_of: dict | None,
    *,
    unknown_fallback: bool = False,
    allow_unknown: bool = False,
) -> str:
    """One function for a region's bodies: the registers any of them uses
    live in a local register file (rt.rs Rf) from entry to exit, loaded
    once and written back once; control moves between bodies without the
    dispatcher. An instruction that traps is undone (its written registers
    restored from copies taken at its start, the rest through the state's
    undo log) and the dispatcher runs it through the one-instruction
    interpreter."""
    by_start: dict[int, list[Body]] = {}
    for b in bodies:
        by_start.setdefault(b.block.start, []).append(b)
    loads = sorted(set().union(*(b.read_any | b.writes for b in bodies)))
    writes = sorted(set().union(*(b.writes for b in bodies)))
    from tools.sharc_dev import sharc_transpile as tp

    puts = set()
    for b in bodies:
        for step in b.steps:
            puts |= (
                TR.variant_putregs.get(step[2], set(range(128)))
                if TR
                else set(range(128))
            )

    # The flag registers (ASTATX, ASTATY, STKYX, STKYY) are required fully
    # known at entry like every other register read, so their known-bit
    # logic folds; an update that would leave a bit unknown is rf_set's
    # cold exit (docs/design/aot-mask-free.md section 7).
    known = [] if allow_unknown else sorted(set().union(*(b.read_any for b in bodies)))
    TAIL.reset(name)
    chain: dict[int, int] | None = {} if CHAINING else None
    body_chunks: list[str] = []
    for b in bodies:
        succ = None if succ_of is None else succ_of.get(b.block.start, set())
        body_chunks.extend(
            body_lines(b, by_start, succ, chain, allow_unknown=allow_unknown)
        )
    lines = [
        "#[inline(never)]",
        "pub fn %s(s: &mut St, mut at: u32) -> u32 {" % name,
        "    // The caller checked s.cfg.block_ok and s.loops_ok (Engine::step).",
    ]
    if known:
        # Registers read must be known at entry: their masks become
        # constants (a register that would become unknown leaves early).
        lines.append(
            "    if (%s) != u32::MAX {" % " & ".join("s.r[%d].m" % c for c in known)
        )
        lines.append("        core::hint::cold_path();")
        lines.append(
            "        return %s_unknown(s, at);" % name
            if unknown_fallback
            else "        return crate::EXIT_BUDGET;"
        )
        lines.append("    }")
    # Bodies start with no delayed transfer pending: so does the region.
    lines.append("    if s.pending.is_some() {")
    lines.append("        core::hint::cold_path();")
    lines.append("        return crate::EXIT_BUDGET;")
    lines.append("    }")
    # Registers that are PartialConst in practice: their usual known-bit
    # mask is required (and so a constant), anything else leaves early.
    partial = sorted(set(loads) & set(tp.PARTIAL_MASKS) & REQUIRE_MASKS)
    for c in partial:
        lines.append("    if s.r[%d].m != %#x {" % (c, tp.PARTIAL_MASKS[c]))
        lines.append("        return crate::EXIT_BUDGET;")
        lines.append("    }")
    lines.append("    let mut rf = Rf::default();")
    if allow_unknown:
        lines.append("    rf.allow_unknown = true;")
    lines.append("    rf.pc = s.pc_sw;")
    lines.append("    rf.pending = None;")
    for c in loads:
        if c in known:
            lines.append("    rf.r[%d] = V { b: s.r[%d].b, m: u32::MAX };" % (c, c))
        elif c in partial:
            lines.append(
                "    rf.r[%d] = V { b: s.r[%d].b, m: %#x };"
                % (c, c, tp.PARTIAL_MASKS[c])
            )
        else:
            lines.append("    rf.r[%d] = s.r[%d];" % (c, c))
    lines += [
        "    // Instructions completed in this call (s.icount at the exit).",
        "    let mut ic: u64 = 0;",
    ]
    if TAIL.trap:
        lines += [
            "    // The trap that ended the call (the one trap tail below).",
            "    let mut tp: Int = 0;",
            "    let mut tt = Trap(0);",
            "    let mut tpd: Option<Pending> = None;",
        ]
    lines += [
        "    let exit: u32 = 'r: loop {",
        "        match at {",
    ]
    lines.extend(body_chunks)
    lines += [
        "            _ => break 'r crate::EXIT_BUDGET,",
        "        }",
        "    };",
    ]
    if TAIL.trap:
        lines += [
            "    if exit == crate::EXIT_TRAP {",
            "        rf.pc = tp;",
            "        rf.pending = tpd;",
            "        s.trap = Some(tt);",
            "    }",
        ]
    lines += [
        "    s.icount += ic;",
        "    s.pc_sw = rf.pc;",
        "    s.pending = rf.pending;",
    ]
    for c in writes:
        if c in known and c not in puts:
            # Known at entry and only ever written known: the mask stays.
            lines.append("    s.r[%d].b = rf.r[%d].b;" % (c, c))
        else:
            lines.append("    s.r[%d] = rf.r[%d];" % (c, c))
    lines += [
        "    if exit == crate::EXIT_TRAP {",
        "        s.rollback_log();",
        "    }",
    ]
    if chain:
        # A block of another region follows: call it directly (a tail call)
        # unless its PC is in the engine's run-time stop set; the chain length
        # is bounded in case it is not one. The links are two tables, so the
        # stop test and the call are code once, not once per link.
        links = sorted(chain.items(), key=lambda kv: kv[1])
        lines += [
            "    if exit >= crate::EXIT_CHAIN {",
            "        if s.chain >= crate::CHAIN_MAX {",
            "            return crate::EXIT_NEXT;",
            "        }",
            "        s.chain += 1;",
            "        static PCS: [u32; %d] = [%s];"
            % (
                len(links),
                ", ".join("%#x" % pc for pc, _k in links),
            ),
            "        static FNS: [fn(&mut St) -> u32; %d] = [%s];"
            % (
                len(links),
                ", ".join("@@B_%X@@" % pc for pc, _k in links),
            ),
            "        let k = (exit - crate::EXIT_CHAIN) as usize;",
            "        if k >= PCS.len() {",
            "            return crate::EXIT_NEXT;",
            "        }",
            # The engine's stop set holds every PC the fast tier wants control
            # at: a link into one ends the chain (the PC is already written back).
            "        if s.chain_stops_at(PCS[k]) {",
            "            return crate::EXIT_NEXT;",
            "        }",
            "        return FNS[k](s);",
            "    }",
        ]
    lines += [
        "    exit",
        "}",
    ]
    text = "\n".join(lines)
    if TAIL.fns:
        text += "\n\n" + TAIL.chain_fn_text()
    if unknown_fallback and known:
        # Keep the usual known-mask path; only a rejected entry uses the
        # same instruction bodies with runtime masks and full writeback.
        text += "\n\n" + region_text(
            name + "_unknown", bodies, succ_of, allow_unknown=True
        )
    return text


def entry_text(start: int, region: str, bodies: list[Body]) -> str:
    """The dispatcher's function for block START: pick the body for the
    run-time MODE1 (the value each was specialised on) and enter the region."""
    free = [b for b in bodies if b.mode1 is None]
    lines = ["pub fn b_%s(s: &mut St) -> u32 {" % ident(start)]
    if free:
        lines.append("    %s(s, %d)" % (region, free[0].idx))
    else:
        lines += [
            "    let m = s.r[%d];" % MODE1,
            "    if m.m != u32::MAX {",
            "        return crate::EXIT_BUDGET;",
            "    }",
            "    let at = match m.b {",
        ]
        for b in bodies:
            assert b.mode1 is not None
            lines.append("        %#x => %d," % (b.mode1, b.idx))
        lines += [
            "        _ => return crate::EXIT_BUDGET,",
            "    };",
            "    %s(s, at)" % region,
        ]
    lines.append("}")
    return "\n".join(lines)


def build_regions(
    blocks: list[Block],
    tr,
    mode1: dict[int, list[int]] | None,
    func_of: dict[int, int],
    succ_of: dict | None,
    max_insns: int = 1200,
    counts: dict[tuple[int, int], int] | None = None,
    max_regs: int = 28,
    unknown_fallbacks: set[int] | None = None,
) -> list[tuple[str, list[int], str]]:
    """Group the blocks into regions and generate each: [(name, block
    starts, text)].

    With COUNTS (block-to-block transition counts), a block joins the
    region of the block that most often precedes it (when that edge carries
    at least half of its entries), as long as the region stays within
    MAX_INSNS body instructions and MAX_REGS registers: every register a
    region uses is live across it, so a large one spills. Without, blocks
    group by program-db function in address order (MAX_INSNS 0: one block
    each)."""
    planned: dict[int, list[Body]] = {}
    safe_of: dict[int, bool] = {}
    for blk in sorted(blocks, key=lambda x: x.start):
        values: list[int | None] = list((mode1 or {}).get(blk.start) or []) or [None]
        found: list[Body] = []
        for m in values:
            body = plan_body(blk, tr, m, 0)
            if body is not None:
                body.mode1 = m
                found.append(body)
        if found:
            planned[blk.start] = found
            reasons = [
                model_unsafe_reason(insn, pc, LOOP_END_SET)
                for body in found
                for pc, insn, *_rest in body.steps
                if not MODEL_SAFE_ONLY
            ]
            safe_of[blk.start] = all(r is None for r in reasons)
            if not safe_of[blk.start]:
                UNSAFE_REASONS[blk.start] = next(r for r in reasons if r is not None)

    def size_of(starts: set) -> tuple[int, int]:
        regs: set = set()
        n = 0
        for st in starts:
            for body in planned[st]:
                regs |= body.read_any | body.writes
                n += len(body.steps)
        return n, len(regs)

    groups: list[list[int]] = []
    if counts is not None and max_insns > 0:
        group_of: dict[int, set[int]] = {st: {st} for st in planned}
        incoming: dict[int, int] = {}
        for (_a, b), c in counts.items():
            incoming[b] = incoming.get(b, 0) + c
        for (a, b), c in sorted(counts.items(), key=lambda kv: -kv[1]):
            if a not in planned or b not in planned or a == b:
                continue
            if not CROSS_FUNCTIONS and func_of.get(a) != func_of.get(b):
                continue
            if c * 2 < incoming.get(b, 0):
                continue
            if safe_of[a] != safe_of[b]:
                continue
            ga, gb = group_of[a], group_of[b]
            if ga is gb:
                continue
            n, r = size_of(ga | gb)
            if n > max_insns or r > max_regs:
                continue
            merged = ga | gb
            for st in merged:
                group_of[st] = merged
        seen: set = set()
        for st in sorted(planned):
            members = group_of[st]
            if id(members) in seen:
                continue
            seen.add(id(members))
            groups.append(sorted(members))
    else:
        by_fn: dict[tuple, list[int]] = {}
        for st in planned:
            by_fn.setdefault((func_of.get(st, st), safe_of[st]), []).append(st)
        for fn in sorted(by_fn):
            chunk: list[int] = []
            size = 0
            for st in sorted(by_fn[fn]):
                n = sum(len(b.steps) for b in planned[st])
                if chunk and (size + n > max_insns or max_insns == 0):
                    groups.append(chunk)
                    chunk, size = [], 0
                chunk.append(st)
                size += n
            if chunk:
                groups.append(chunk)
    out = []
    for g in groups:
        bodies: list[Body] = []
        for st in g:
            for body in planned[st]:
                body.idx = len(bodies)
                bodies.append(body)
        name = "r_%X" % g[0]
        text = region_text(
            name,
            bodies,
            succ_of,
            unknown_fallback=bool(set(g) & (unknown_fallbacks or set())),
        )
        ents = [
            entry_text(st, name, [b for b in bodies if b.block.start == st]) for st in g
        ]
        out.append((name, list(g), text + "\n\n" + "\n\n".join(ents)))
        for st in g:
            if safe_of[st]:
                MODEL_SAFE_STARTS.add(st)
    return out


MODE1 = 114
# Set by generate(): every DO loop end of the image; the entries of the
# blocks classed model-safe (build_regions), and why the others are not.
LOOP_END_SET: frozenset = frozenset()
# --model-safe: generate only model-safe instruction prefixes of the blocks.
MODEL_SAFE_ONLY = False
# --explicit-memory-model: the memory model block code is generated for.
GEN_EXPLICIT_MEMORY_MODEL = True
MODEL_SAFE_STARTS: set[int] = set()
UNSAFE_REASONS: dict[int, str] = {}
# Regions may span program-db functions (a call and its callee's blocks).
CROSS_FUNCTIONS = True
# Partial registers whose usual mask block code requires at entry (none: the
# flag registers are required fully known at entry, see region_text).
REQUIRE_MASKS: set[int] = set()
# Direct calls between regions (--chain; slower on the drive3 frames:
# the dispatcher's indirect call predicts well).
CHAINING = False


def read_coverage(path: str) -> dict[int, dict[int, int]]:
    """sharc-frames --coverage output: pc -> {MODE1 value: count} (known
    MODE1 only; an unknown one counts under -1)."""
    out: dict[int, dict[int, int]] = {}
    with open(path) as fh:
        for line in fh:
            parts = line.split()
            if len(parts) != 4:
                continue
            pc, m, known, count = (
                int(parts[0], 0),
                int(parts[1], 0),
                int(parts[2]),
                int(parts[3]),
            )
            key = m if known else -1
            d = out.setdefault(pc, {})
            d[key] = d.get(key, 0) + count
    return out


BLOCK_PRELUDE = """use super::*;
use crate::generated::core_i as ci;
use crate::generated::syms::*;
use crate::rt::*;

"""


SPEC_PRELUDE = """// Generated by tools/sharc_dev/sharc_rsgen.py: core functions specialized on the
// image's instructions (tools/sharc_dev/sharc_transpile.py, Translator.variant).
use super::*;
use crate::generated::syms::*;
use crate::generated::tables::*;
use crate::rt::*;
"""


def load_blocks(db: str, image: str, starts: list[int], mem) -> list[Block]:
    from tools.sharc_dev.sharc_core.sequencer import decode_at

    con = sqlite3.connect(db)
    out = []
    for start in starts:
        row = con.execute(
            "select end_sw from bblocks where image=? and start_sw=?", (image, start)
        ).fetchone()
        if row is None:
            # An entry inside a program-database block (a return address
            # after a call, an indirect jump target): the rest of that block.
            row = con.execute(
                "select end_sw from bblocks where image=? and start_sw<=? and end_sw>?"
                " order by start_sw desc limit 1",
                (image, start, start),
            ).fetchone()
        if row is None:
            continue  # outside every program-db block: interpreted
        end = row[0]
        insns = []
        pc = start
        while pc < end:
            insn = decode_at(mem, None, pc)
            if insn.length_bytes is None:
                break
            insns.append((pc, insn))
            pc += insn.length_bytes // 2
        if insns:
            out.append(Block(start, end, insns))
    return out


def all_block_starts(db: str, image: str) -> list[int]:
    con = sqlite3.connect(db)
    return [
        r[0]
        for r in con.execute(
            "select start_sw from bblocks where image=? order by start_sw", (image,)
        )
    ]


def loop_ends(decoded: dict) -> list[int]:
    from tools.sharc_dev.sharc_core.encoding import _field
    from tools.sharc_dev.sharc_core.values import _signed

    ends = []
    for pc, insn in decoded.items():
        if insn.type_name in ("12a_imm", "12a_ureg") and insn.length_bytes is not None:
            reladdr = (_field(insn.fields, "reladdr[22:16]") << 16) | _field(
                insn.fields, "reladdr[15:0]"
            )
            ends.append(pc + _signed(reladdr, 23))
    return sorted(set(ends))


def table_pcs(db: str, image: str) -> list[int]:
    """Every PC the program database decoded (aligned or not): a branch or
    call may reach any of them."""
    con = sqlite3.connect(db)
    return [
        r[0]
        for r in con.execute("select sw from insn where image=? order by sw", (image,))
    ]


def field_tuple(key: str) -> tuple[str, int, int]:
    stem, _, rng = key.partition("[")
    hi = lo = -1
    if rng.endswith("]") and ":" in rng:
        h, _, lo_text = rng[:-1].partition(":")
        if h.isdigit() and lo_text.isdigit():
            hi, lo = int(h), int(lo_text)
    return stem, hi, lo


def insn_blob(syms, decoded: dict) -> bytes:
    """The one-instruction interpreter's table (native canon.rs
    parse_insn_table): magic "SHIX", count, then per instruction pc:u32,
    type:u16, kind:u16, length:i8 (-1 None), nfields:u8, and per field
    key:u16, stem:u16, hi:i8, lo:i8, value:i64."""
    import struct

    out = bytearray(b"SHIX")
    rows = [
        (pc, insn)
        for pc, insn in sorted(decoded.items())
        if insn.length_bytes is not None
    ]
    out += struct.pack("<I", len(rows))
    for pc, insn in rows:
        fields = split_compute(insn.fields)
        out += struct.pack(
            "<IHHbB",
            pc,
            syms.intern(insn.type_name or "unknown"),
            syms.intern(insn.kind),
            insn.length_bytes,
            len(fields),
        )
        for key, value in fields.items():
            stem, hi, lo = field_tuple(key)
            out += struct.pack(
                "<HHbbq", syms.intern(key), syms.intern(stem), hi, lo, value
            )
    return bytes(out)


CODE_LOOKAHEAD = 6  # short words past a block the decoder may read


def code_ranges(mem, blocks: list[Block]) -> list[tuple[int, int, str]]:
    """Merged short-word ranges covering the generated blocks (plus
    CODE_LOOKAHEAD words), each with the SHA-256 packages/emulator/sharc/src/lib.rs
    verify_code computes over the loaded words."""
    import hashlib

    spans = sorted((b.start, b.end + CODE_LOOKAHEAD) for b in blocks)
    merged: list[list[int]] = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    out = []
    for lo, hi in merged:
        h = hashlib.sha256()
        for pc in range(lo, hi):
            word = mem.read_sw(pc, 2)
            h.update(word if word is not None else b"\xde\xad\xbe")
        out.append((lo, hi - lo, h.hexdigest()))
    return out


def generate(
    image: str,
    starts: list[int],
    out_dir: str,
    *,
    per_module: int = 64,
    work_dir: str | None = None,
    mode1: dict[int, list[int]] | None = None,
    successors: dict[int, set[int]] | None = None,
    region_insns: int = 1200,
    transition_counts: dict[tuple[int, int], int] | None = None,
    region_regs: int = 28,
    decode_ranges: list[tuple[int, int]] | None = None,
    unknown_fallbacks: set[int] | None = None,
) -> dict:
    from tools.sharc_dev import sharc
    from tools.sharc_dev import sharc_run as sr
    from tools.sharc_dev import sharc_transpile as tp
    from tools.sharc_dev import sharc_transpile_infer as infer
    from tools.sharc_dev.sharc_core.sequencer import decode_at

    t0 = time.perf_counter()
    img = sharc.load(image)
    mem = img._mem()
    db = os.path.join(ROOT, "out", "sharcdb", image + ".sqlite")

    core = infer.load(work_dir or os.path.join(ROOT, "out", "native", "transpile-work"))
    out = tp.translate(core)
    tr = out.translator  # type: ignore[attr-defined]
    tr.blk = True
    # Block code folds the run configuration; explicit_memory_model is the one
    # a diagnostic run changes (the runtime refuses blocks for any other).
    tr.gen_cfg["explicit_memory_model"] = GEN_EXPLICIT_MEMORY_MODEL
    global TR
    TR = tr
    syms = tr.syms
    t1 = time.perf_counter()

    pcs = set(table_pcs(db, image))
    for start, end in decode_ranges or []:
        pcs.update(range(start, end))
    decoded = {}
    for pc in sorted(pcs):
        insn = decode_at(mem, None, pc)
        decoded[pc] = insn
    blocks = load_blocks(db, image, starts, mem)
    for b in blocks:
        for pc, insn in b.insns:
            decoded.setdefault(pc, insn)
    # Every DO loop's end address (sequencer._start_counted_loop's end_sw):
    # block code knows a loop can only end at one of these.
    tr.loop_ends = frozenset(loop_ends(decoded))
    global LOOP_END_SET
    LOOP_END_SET = tr.loop_ends
    MODEL_SAFE_STARTS.clear()
    UNSAFE_REASONS.clear()
    # Specialized code refers to an instruction by its static.
    named = list(decoded.items()) + [(pc, insn) for b in blocks for pc, insn in b.insns]
    for pc, insn in named:
        tr.static_names[id(insn)] = "(&I_%s)" % ident(pc)
        tr.static_names[id(insn.fields)] = "(&F_%s)" % ident(pc)
    t2 = time.perf_counter()

    os.makedirs(out_dir, exist_ok=True)
    for name in os.listdir(out_dir):
        if name.startswith(("blocks_", "spec_")) and name.endswith(".rs"):
            os.remove(os.path.join(out_dir, name))

    # Statics for the instructions block code runs (their fields fold);
    # every decoded instruction goes in the blob.
    insn_lines = [
        "// Generated by tools/sharc_dev/sharc_rsgen.py from %s. Firmware-derived: do not commit."
        % image
    ]
    in_blocks = sorted({pc for b in blocks for pc, _ in b.insns})
    table = [pc for pc in decoded if decoded[pc].length_bytes is not None]

    tr.block_failures = []
    con = sqlite3.connect(os.path.join(ROOT, "out", "sharcdb", image + ".sqlite"))
    func_of = {
        pc: fn
        for pc, fn in con.execute(
            "select sw, function_sw from insn where image=? and function_sw is not null",
            (image,),
        )
    }
    regions = build_regions(
        blocks,
        tr,
        mode1,
        func_of,
        successors,
        region_insns,
        transition_counts,
        region_regs,
        unknown_fallbacks,
    )
    by_start = {b.start: b for b in blocks}
    generated = {st for _n, starts_, _t in regions for st in starts_}
    blocks = [by_start[st] for st in sorted(generated)]
    # Regions per file: about PER_MODULE blocks each.
    modules: list[list[tuple[str, list[int], str]]] = [[]]
    count = 0
    for reg in regions:
        if count >= per_module:
            modules.append([])
            count = 0
        modules[-1].append(reg)
        count += len(reg[1])
    # Statics only for the instructions the code still names (partial
    # evaluation folds the rest into constants).
    still_named: set[int] = set()
    for _n, _s, text in regions:
        still_named |= {int(x, 16) for x in re.findall(r"&[IF]_([0-9A-F]+)\)", text)}
    for _m, text in tr.variant_texts:
        still_named |= {int(x, 16) for x in re.findall(r"&[IF]_([0-9A-F]+)\)", text)}
    for pc in in_blocks:
        if pc in still_named:
            insn_lines.append(insn_static(syms, pc, decoded[pc]))
    files = {"insns.rs": "\n".join(insn_lines) + "\n"}
    blob = insn_blob(syms, decoded)
    where = {
        st: m for m, chunk in enumerate(modules) for _n, sts, _t in chunk for st in sts
    }

    def link(mm: re.Match) -> str:
        pc = int(mm.group(1), 16)
        if pc not in where:
            return "crate::no_block"
        return "crate::generated::image::blocks_%02d::b_%X" % (where[pc], pc)

    for m, chunk in enumerate(modules):
        body = "\n\n".join(text for _n, _s, text in chunk)
        body = re.sub(r"@@B_([0-9A-F]+)@@", link, body)
        files["blocks_%02d.rs" % m] = BLOCK_PRELUDE + "\n" + body + "\n"
    t3 = time.perf_counter()
    spec: dict[int, list[str]] = {}
    for module, text in tr.variant_texts:
        spec.setdefault(module, []).append(text)
    for module in range(tr.variant_modules):
        files["spec_%02d.rs" % module] = (
            SPEC_PRELUDE + "\n" + "\n\n".join(spec.get(module, [])) + "\n"
        )
    # The variants added lookup tables, strings and trap sites.
    out.files["tables.rs"] = tp.render_tables(tr)
    out.files["syms.rs"] = syms.rust()

    gen_dir = 'concat!(env!("SHARC_GEN_DIR"), "/%s")'
    image_lines = [
        "// Generated by tools/sharc_dev/sharc_rsgen.py from %s. Firmware-derived: do not commit."
        % image,
        "use crate::rt::*;",
        "use crate::generated::syms::*;",
        "use crate::generated::core_i as ci;",
        "",
        "pub const IMAGE: &str = %s;" % json.dumps(image),
        "/// Block code was generated for this explicit_memory_model setting",
        "/// (the engine runs blocks only under the same one).",
        "pub const GEN_EXPLICIT_MEMORY_MODEL: bool = %s;"
        % ("true" if GEN_EXPLICIT_MEMORY_MODEL else "false"),
        "pub const IMAGE_SHA256: &str = %s;" % json.dumps(sr._image_sha256(mem)),
        "",
        "include!(%s);" % (gen_dir % "insns.rs"),
        "",
        "/// Every decoded instruction (tools/sharc_dev/sharc_rsgen.py insn_blob).",
        "pub static INSN_BLOB: &[u8] = include_bytes!(%s);" % (gen_dir % "insns.bin"),
        "",
        "/// Every DO loop's end address (block code assumes the loop stack",
        "/// holds only these; the runtime checks at import).",
        "pub static LOOP_ENDS: &[i64] = &[%s];"
        % ", ".join("%d" % e for e in sorted(tr.loop_ends)),
        "",
        "/// Entry PCs of the blocks classed model-safe (tools/sharc_dev/sharc_rsgen.py",
        "/// model_unsafe_reason): the engine runs only these with a model on.",
        "pub static MODEL_SAFE: &[u32] = &[%s];"
        % ", ".join("%#x" % st for st in sorted(MODEL_SAFE_STARTS & generated)),
        "",
        "/// The short-word ranges the blocks were generated from (with the words",
        "/// the decoder looks ahead at) and the SHA-256 of their loaded bytes (an",
        "/// absent word counts as DE AD BE): with runtime decoding the engine runs",
        "/// blocks only while its memory still hashes to these.",
        "pub static CODE_RANGES: &[(u32, u32, &str)] = &[%s];"
        % ", ".join(
            '(%#x, %d, "%s")' % (lo, n, h) for lo, n, h in code_ranges(mem, blocks)
        ),
        "",
        "/// The entry PCs of each generated region (a region's bodies pass control",
        "/// directly, so the engine bars all of a region's entries when any of its",
        "/// code ranges no longer matches CODE_RANGES).",
        "pub static REGION_ENTRIES: &[&[u32]] = &[%s];"
        % ", ".join(
            "&[%s]" % ", ".join("%#x" % st for st in sorted(starts_))
            for _n, starts_, _t in regions
        ),
        "",
        "/// The decoded instruction at PC (sequencer.decode_at over the image).",
        "pub fn insn_at(pc: Int) -> Option<Insn> {",
        "    crate::canon::insn_table(INSN_BLOB).get(pc)",
        "}",
        "",
    ]
    for m in range(len(modules)):
        image_lines.append(
            "pub mod blocks_%02d {\n    include!(%s);\n}"
            % (m, gen_dir % ("blocks_%02d.rs" % m))
        )
    for m in range(tr.variant_modules):
        image_lines.append(
            "pub mod spec_%02d {\n    include!(%s);\n}"
            % (m, gen_dir % ("spec_%02d.rs" % m))
        )
    entries = []
    for m, chunk in enumerate(modules):
        for _name, starts_, _text in chunk:
            for st in starts_:
                entries.append(
                    "(%#x, blocks_%02d::b_%s as crate::BlockFn)" % (st, m, ident(st))
                )
    image_lines.append(
        "pub static BLOCKS: &[(u32, crate::BlockFn)] = &[\n    %s\n];"
        % ",\n    ".join(entries)
    )
    files["image.rs"] = "\n".join(image_lines) + "\n"

    tp.write(out, out_dir)
    for name, text in files.items():
        with open(os.path.join(out_dir, name), "w") as fh:
            fh.write(text)
    with open(os.path.join(out_dir, "insns.bin"), "wb") as bfh:
        bfh.write(blob)

    report = {
        "image": image,
        "blocks": len(blocks),
        "block_instructions": sum(len(b.insns) for b in blocks),
        "instructions_in_table": len(table),
        "decode_ranges": decode_ranges or [],
        "modules": len(modules),
        "regions": len(regions),
        "per_module": per_module,
        "bytes": {name: len(text) for name, text in files.items()},
        "variants": len(tr.variants),
        "seconds": {
            "transpile": round(t1 - t0, 2),
            "decode": round(t2 - t1, 2),
            "specialize": round(t3 - t2, 2),
            "total": round(time.perf_counter() - t0, 2),
        },
        "block_starts": [b.start for b in blocks],
        "model_safe_blocks": len(MODEL_SAFE_STARTS & generated),
        "model_unsafe_reasons": {
            "%#x" % st: UNSAFE_REASONS[st]
            for st in sorted(UNSAFE_REASONS)
            if st in generated
        },
        "specializer_failures": [["%#x" % pc, why] for pc, why in tr.block_failures],
        "transpile": out.report,
    }
    fallback_regions = [
        n for n, _starts, text in regions if "pub fn %s_unknown(" % n in text
    ]
    if fallback_regions:
        report["unknown_fallback_regions"] = fallback_regions
    with open(os.path.join(out_dir, "rsgen-report.json"), "w") as fh:
        json.dump(report, fh, indent=1)
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("image", help='program database image, e.g. "dt2-1.16"')
    p.add_argument("--out", default=DEFAULT_OUT, help="output directory (under out/)")
    p.add_argument("--blocks", default="", help="comma-separated hex block starts")
    p.add_argument(
        "--unknown-fallbacks",
        default="",
        help="comma-separated hex block starts whose regions should also compile "
        "a fallback preserving unknown register values (use option-26 entry bails)",
    )
    p.add_argument(
        "--decode-range",
        type=decode_range,
        action="append",
        default=[],
        help="add interpreter decode coverage for START:END short-word PCs (repeatable)",
    )
    p.add_argument(
        "--blocks-from", help="a report.json listing blocks (phase 0 or this tool)"
    )
    p.add_argument("--counts", help="JSON with a 'ranked' list of [block_start, count]")
    p.add_argument(
        "--top", type=int, default=0, help="the N most executed blocks from --counts"
    )
    p.add_argument("--all", action="store_true", help="every basic block of the image")
    p.add_argument(
        "--only",
        help="comma-separated hex block starts: generate only these (with the "
        "MODE1 values --coverage gives them)",
    )
    p.add_argument(
        "--work",
        help="the inference work directory sharc_transpile.py --work wrote "
        "(default out/native/transpile-work)",
    )
    p.add_argument(
        "--explicit-memory-model",
        type=int,
        choices=(0, 1),
        default=1,
        help="the explicit_memory_model setting block code is generated for "
        "(DN2 diagnostics run with 0)",
    )
    p.add_argument(
        "--model-safe",
        action="store_true",
        help="cut each block before its first instruction a run-time model "
        "(banks, stacks, timer, IRQs, clock, peripherals) could make differ, "
        "so every generated block may run with the models on",
    )
    p.add_argument(
        "--chain",
        action="store_true",
        help="direct calls between regions (else every exit returns to the dispatcher)",
    )
    p.add_argument("--no-chain", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--step-budget",
        action="store_true",
        help="check the budget before every instruction of a body (default: once "
        "per body or loop iteration)",
    )
    p.add_argument(
        "--split-budget",
        action="store_true",
        help="two copies of each block body, one without per-step budget checks "
        "(about 2%% faster, about twice the source and compile time)",
    )
    p.add_argument(
        "--exclude",
        type=decode_range,
        action="append",
        default=[],
        help="no block starts in START:END short-word PCs (repeatable): code the "
        "engine never dispatches, such as an idle-skip range, which a --chain "
        "call would otherwise enter",
    )
    p.add_argument(
        "--region-regs",
        type=int,
        default=36,
        help="registers a region may use at most (they are live across it)",
    )
    p.add_argument(
        "--region-insns",
        type=int,
        default=120,
        help="instructions per region function at most (0: one block each)",
    )
    p.add_argument(
        "--transitions",
        help="sharc-frames --transitions output: the block-to-block moves "
        "seen; a region routes each block's exits only to those",
    )
    p.add_argument(
        "--entries",
        help="sharc-frames --interp-coverage FILE's FILE.entries: PCs the "
        "interpreter was entered at; each starts a block (to its program-db "
        "block's end), specialised on the MODE1 values --coverage saw there",
    )
    p.add_argument(
        "--coverage",
        help="sharc-frames --coverage output: the blocks whose start ran, "
        "each specialised on the MODE1 values it was entered with",
    )
    p.add_argument(
        "--per-module", type=int, default=64, help="block functions per Rust file"
    )
    args = p.parse_args(argv)

    db = os.path.join(ROOT, "out", "sharcdb", args.image + ".sqlite")
    starts = [int(x, 16) for x in args.blocks.split(",") if x]
    if args.blocks_from:
        with open(args.blocks_from) as fh:
            rep = json.load(fh)
        blocks_field = rep.get("blocks", [])
        for b in blocks_field if isinstance(blocks_field, list) else []:
            starts.append(b["start"] if isinstance(b, dict) else int(b))
        starts += rep.get("block_starts", [])
    if args.counts and args.top:
        with open(args.counts) as fh:
            ranked = json.load(fh)["ranked"]
        starts += [int(b) for b, _ in ranked[: args.top]]
    if args.all:
        starts = all_block_starts(db, args.image)
    mode1: dict[int, list[int]] | None = None
    if args.coverage:
        cov = read_coverage(args.coverage)
        every = set(all_block_starts(db, args.image))
        mode1 = {}
        for pc, seen in cov.items():
            if pc not in every:
                continue
            starts.append(pc)
            known = sorted((m for m in seen if m >= 0), key=lambda m: -seen[m])
            mode1[pc] = known[:4]
        if args.entries:
            with open(args.entries) as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) != 2:
                        continue
                    pc = int(parts[0], 0)
                    at = cov.get(pc)
                    if not at:
                        continue
                    starts.append(pc)
                    known = sorted((m for m in at if m >= 0), key=lambda m: -at[m])
                    mode1[pc] = known[:4]
    if args.only:
        keep = {int(x, 16) for x in args.only.split(",") if x}
        starts = [st for st in starts if st in keep]
    for lo, hi in args.exclude:
        starts = [st for st in starts if not lo <= st < hi]
    starts = sorted(set(starts))
    global CHAINING, MODEL_SAFE_ONLY, GEN_EXPLICIT_MEMORY_MODEL, SPLIT_BUDGET
    global STEP_BUDGET
    SPLIT_BUDGET = args.split_budget
    STEP_BUDGET = args.step_budget
    GEN_EXPLICIT_MEMORY_MODEL = bool(args.explicit_memory_model)
    CHAINING = args.chain and not args.no_chain
    MODEL_SAFE_ONLY = args.model_safe
    successors: dict[int, set[int]] | None = None
    counts: dict[tuple[int, int], int] | None = None
    if args.transitions:
        successors = {}
        counts = {}
        with open(args.transitions) as fh:
            for line in fh:
                parts = line.split()
                if len(parts) == 3:
                    a, b = int(parts[0], 0), int(parts[1], 0)
                    successors.setdefault(a, set()).add(b)
                    counts[(a, b)] = counts.get((a, b), 0) + int(parts[2])
    report = generate(
        args.image,
        starts,
        args.out,
        per_module=args.per_module,
        mode1=mode1,
        successors=successors,
        region_insns=args.region_insns,
        transition_counts=counts,
        region_regs=args.region_regs,
        decode_ranges=args.decode_range,
        work_dir=args.work,
        unknown_fallbacks={int(x, 16) for x in args.unknown_fallbacks.split(",") if x},
    )
    print(
        "%d blocks (%d instructions), %d instructions in the table, %d files -> %s (%.1fs)"
        % (
            report["blocks"],
            report["block_instructions"],
            report["instructions_in_table"],
            report["modules"],
            args.out,
            report["seconds"]["total"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
