"""Audit sharc_core form handlers against the memory-access width/scale
their own decoded l/x/w/sw/ex fields say they should use.

Two independent checks, run separately or together:

**Concrete mode** (default): run one root -- the voice render
(``tools/sharc_dev/sharc_harness/``'s ``call_render``, from a ``run_init()`` state
with one voice set up), the frame render (``call_frame``), or an explicit
``--pc``) with ``sharc_core.state.State.record_events`` turned on, so every
form handler's own ``_event()`` call (forms_move.py/forms_dag.py/
forms_compute.py) logs a full "load"/"store" record: pc, form, space,
access_width, addressing_mode (``sharc_core/state.py``'s ``_event``/
``State.record_events`` -- off by default in a concrete run for speed, see
``sharc_run.make_state``). For every such record whose form has a
width-selecting field (l, x, w, sw, ex -- see ``tools/sharc_dev/sharcspec/
decode_table.json``), this tool independently re-decodes that pc's own raw
fields (the Runner's own decode cache -- the same ``Instruction`` the run
itself executed) and recomputes the width the PRM's ACCESS_WIDTHS table
(``sharc_core/encoding.py``) and the per-form rules in ``WIDTH_RULES`` below
say those fields select -- then reports every pc where the handler's own
``access_width`` disagrees with that independently recomputed one, grouped
by form.

**Static mode** (``--static``): for every form in
``tools/sharc_dev/sharcspec/decode_table.json`` that has a width/scale field (l, x, w,
sw, nw, bw, lw, ex), grep its ``sharc_core`` handler's own source (via
``inspect.getsource`` on ``sharc_core.forms.FORMS``) for that field's name,
and list any handler whose source never mentions it. This is a lint, not a
semantics proof -- a handler can read a field and still get it wrong, the
way Type3d did before this tool's own fix (see ``WIDTH_RULES["3d"]``'s
docstring) -- but it is cheap, exhaustive, and catches the "field decoded,
never consulted" half of that bug class outright.

    uv run python -m tools.sharc_dev.sharc_widthaudit                    # voice root
    uv run python -m tools.sharc_dev.sharc_widthaudit --root frame
    uv run python -m tools.sharc_dev.sharc_widthaudit --pc 0x1c50fb
    uv run python -m tools.sharc_dev.sharc_widthaudit --static
    uv run python -m tools.sharc_dev.sharc_widthaudit --json

Library use:

    import sharc_widthaudit as wa
    report = wa.audit_root("voice", image="dt2-1.16")
    report.mismatches  # []
"""

from __future__ import annotations

import argparse
import collections
import inspect
import json
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc_harness as h  # noqa: E402
from tools.sharc_dev import sharc_run as sr  # noqa: E402
from tools.sharc_dev import sharcinv  # noqa: E402
from tools.sharc_dev.sharc_core.encoding import (  # noqa: E402
    ACCESS_WIDTHS,
    TYPE4B_ACCESS_WIDTHS,
)
from tools.sharc_dev.sharc_core.forms import FORMS  # noqa: E402

# --- static field/width tables ----------------------------------------------

# tools/sharc_dev/sharcspec/decode_table.json forms carrying a width- or scale-select
# field, and which of that form's own field labels (bit-range suffixes
# stripped, e.g. "l" for both "l" and "l[3:0]") select it. Derived once by
# scanning decode_table.json's own "fields" lists for {l, x, w, sw, nw, bw,
# lw, ex} (this tool's own --static mode re-derives the *set* of forms this
# way at runtime -- see ``_forms_with_width_fields`` -- so this table only
# needs to stay in sync with which of those fields each form's WIDTH_RULES
# entry actually consults).
_WIDTH_FIELD_NAMES = frozenset({"l", "x", "w", "sw", "nw", "bw", "lw", "ex"})


def _forms_with_width_fields(
    decode_table_path: str | None = None,
) -> dict[str, set[str]]:
    """{form key (FORMS-table style, e.g. "3d") -> {field names}} for every
    decode_table.json form with at least one width/scale field."""
    path = decode_table_path or os.path.join(HERE, "sharcspec", "decode_table.json")
    with open(path, encoding="utf-8") as fh:
        table = json.load(fh)
    result: dict[str, set[str]] = {}
    for form in table["forms"]:
        names = set()
        for fl in form["fields"]:
            base = fl["label"].split("[")[0]
            if base in _WIDTH_FIELD_NAMES:
                names.add(base)
        if names:
            assert form["name"].startswith("Type"), form["name"]
            result[form["name"][4:]] = names
    return result


# --- per-form expected-width rules ------------------------------------------
#
# Each rule takes MERGED fields (sharcinv.merge_fields(insn.fields): plain
# {"l": 0, "x": 1, ...} ints, multi-part fields already combined) and
# returns the ACCESS_WIDTHS-style width string this tool independently
# expects, or None when the fields select a combination this tracer's own
# handler deliberately refuses (WACCESS, exclusive access, ...) -- a form
# handler that reaches a load/store event can never actually have hit one
# of those (it would have stopped first), so a rule returning None is never
# compared against an event; it exists so a rule can be written for a
# form's *documented* field space without also re-deriving which corners
# the handler currently refuses.


def _w_access3(f: dict) -> str | None:
    """Type3b/Type4d's 3-bit ACCESS/BH/BHSE table (SHARC+ Core Programming
    Reference pp.13-16--13-19/13-34--13-35): width is ACCESS_WIDTHS[(l, x,
    w)] unconditionally -- see sharc_core/encoding.py's own citation."""
    return ACCESS_WIDTHS.get((f.get("l", 0), f.get("x", 0), f.get("w", 0)))


def _w_4b(f: dict) -> str | None:
    """Type4b's own BH/BHSE tables (PRM p.13-32): (1, 1, 1) is the plain
    normal-word access and there is no (lw) or (0, 1, 1) row
    (TYPE4B_ACCESS_WIDTHS, sharc_core/encoding.py)."""
    return TYPE4B_ACCESS_WIDTHS.get((f.get("l", 0), f.get("x", 0), f.get("w", 0)))


def _w_3d(f: dict) -> str | None:
    """Type3d (SHARC+ Core Programming Reference pp.13-22--13-25, Figure
    13-9): w=1 selects WACCESS, a disjoint opcode group this tracer's
    ``_type_3d`` refuses outright (see its own docstring) -- no width rule
    applies there. w=0 selects ACCESS, whose own encode table (p.324) names
    every row's modifier from the BH (store) or BHSE (load) sub-table
    that follows it -- both indexed by (l, x), independent of ex -- so w=0
    always uses ACCESS_WIDTHS[(l, x, 0)], the same slice Type3b/Type4d use.
    Before this tool's own fix (2026-09-25, see git history), ``_type_3d``
    hardcoded w=0 as always normal-word: dt2-1.16's own Type3d population
    (``tools/sharc_dev/sharc.py``: 46/72 instances at (ex=0,w=0,l=0,x=0), plus 3/5/10
    more at the other three (l, x) combinations for that same (ex, w))
    only makes sense if l/x actually select the width, which is what
    refuted that hardcoding (docs/findings/06's "Wrap copies +0x1bc")."""
    if f.get("w"):
        return None
    return ACCESS_WIDTHS.get((f.get("l", 0), f.get("x", 0), 0))


def _w_14d(f: dict) -> str | None:
    """Type14d (SHARC+ Core Programming Reference pp.384-387, Figure 15-2):
    mirrors ``_type_14d``'s own w=0,ex=0 execution branch (BH for a store,
    BHSE for a load) -- ACCESS_WIDTHS[(l, x, 0)]. w=1 (EX/LWEX) and a store
    with x=1 are refused there (see ``_type_14d``'s docstring), so no rule
    applies to them either."""
    if f.get("w") or f.get("ex") or (f.get("d") and f.get("x")):
        return None
    return ACCESS_WIDTHS.get((f.get("l", 0), f.get("x", 0), 0))


def _w_3a(f: dict) -> str:
    """Type3a (SHARC+ Core Programming Reference p.13-15's ACCESS Encode
    Table, every u/g/d row repeated with "(lw)" appended): l=1 selects the
    same long-word register-pair access as Type14a/15a/15b's (lw) option
    (``_w_pair``, PRM p.2-4 "Data Register Neighbor Pairing"); l=0 is an
    ordinary normal-word transfer. ``_type_3a`` executes l=1 for every
    ureg, odd or even (``sharc_core.state._lw_pair_mate``), so this rule
    applies regardless of parity."""
    return "long-word" if f.get("l") else "normal-word"


def _w_pair(f: dict) -> str:
    """Type14a/Type15a/Type15b's shared (lw) register-pair option (SHARC+
    Core Programming Reference pp.373-383/387-392): l=1 is a long-word
    (register-pair) access, l=0 an ordinary normal-word one."""
    return "long-word" if f.get("l") else "normal-word"


def _w_7a(f: dict) -> str:
    """Type7a MODIFY's BH sub-table (SHARC+ Core Programming Reference
    p.348): (w, l) = (0, 0) blank and (1, 0) "(nw)" both mean normal-word,
    (0, 1) and the undocumented (1, 1) both mean short-word -- so the
    width the field selects depends only on l (see ``_type_7a``'s own
    docstring for why w is inconsequential here)."""
    return "short-word" if f.get("l") else "normal-word"


def _w_19a_scaled(f: dict) -> str:
    """Type19a_scaled MODIFY (Enhanced MODIFY for Address Scaling, SHARC+
    Core Programming Reference Sec. 6): w=1 selects normal-word scaling,
    w=0 short-word -- mirrors ``_type_19a``'s own ``name == "19a_scaled"``
    branch."""
    return "normal-word" if f.get("w") else "short-word"


WIDTH_RULES: dict[str, Callable[[dict], str | None]] = {
    "3a": _w_3a,
    "3b": _w_access3,
    "4b": _w_4b,
    "4d": _w_access3,
    "3d": _w_3d,
    "14d": _w_14d,
    "14a": _w_pair,
    "15a": _w_pair,
    "15b": _w_pair,
    "7a": _w_7a,
    "19a_scaled": _w_19a_scaled,
}


# --- concrete audit -----------------------------------------------------


@dataclass
class Mismatch:
    pc: int
    form: str
    action: str
    space: str
    actual_width: str
    expected_width: str
    fields: dict


@dataclass
class AuditReport:
    root: str
    image: str
    instructions: int
    halt_reason: str
    halt_pc: int
    halt_form: str | None
    events_checked: int
    per_form_checked: dict[str, int] = field(default_factory=dict)
    mismatches: list[Mismatch] = field(default_factory=list)

    def to_json(self) -> dict:
        d = asdict(self)
        return d


def _merged_fields(insn) -> dict:
    return sharcinv.merge_fields(insn.fields)


def _audit_trace(
    runner: sr.Runner, trace: list[dict]
) -> tuple[int, dict[str, int], list[Mismatch]]:
    """Scan TRACE (a State.trace list recorded with record_events=True) for
    every load/store event whose form has a WIDTH_RULES entry, and compare
    the handler's own recorded ``access_width`` against that rule's answer
    for the same pc's independently re-decoded fields. Returns (events
    checked, per-form checked counts, mismatches). RUNNER's own decode
    cache (``Runner._decode``) supplies the fields, so this never
    re-disassembles anything the run itself did not already decode."""
    checked = 0
    per_form: collections.Counter[str] = collections.Counter()
    mismatches: list[Mismatch] = []
    for ev in trace:
        if ev.get("action") not in ("load", "store"):
            continue
        form = ev.get("form")
        access_width = ev.get("access_width")
        if access_width is None or form not in WIDTH_RULES:
            continue
        pc = ev["pc_sw"]
        insn = runner._decode(pc)
        fields = _merged_fields(insn)
        expected = WIDTH_RULES[form](fields)
        checked += 1
        per_form[form] += 1
        if expected is not None and expected != access_width:
            mismatches.append(
                Mismatch(
                    pc=pc,
                    form=form,
                    action=ev["action"],
                    space=ev.get("space", ""),
                    actual_width=access_width,
                    expected_width=expected,
                    fields=fields,
                )
            )
    return checked, dict(per_form), mismatches


def _voice_setup(image: str, *, sample_len: int = 4096):
    memory = h.load_image_memory(image)
    init = h.run_init(memory, image)
    if not init.ran:
        raise RuntimeError("run_init did not complete: %s" % init.error)
    runner = h.new_runner(memory, image, init=init)
    state = runner.state
    samples = h._make_test_input("sine", sample_len)
    h._write_samples(state, 0x310000, samples, "int16")
    record = h.setup_voice(state, image, 0, sample_len=sample_len, sample_base=0x310000)
    return runner, record


def audit_root(
    root: str,
    *,
    image: str = "dt2-1.16",
    pc: int | None = None,
    max_steps: int = 4_000_000,
) -> AuditReport:
    """Run ROOT ("voice", "frame", or "pc") concretely with
    ``State.record_events`` enabled and audit its own trace -- see this
    module's docstring."""
    if root == "voice":
        runner, record = _voice_setup(image)
        runner.state.record_events = True
        result, _floats = h.call_render(runner, image, record)
        final_runner = runner
        root_label = root
    elif root == "frame":
        runner, record = _voice_setup(image)
        h.setup_frame(runner.state, image)
        runner.state.record_events = True
        final_runner, result = h.call_frame(
            runner, image, patch_table=h.FRAME_PATCH_TABLE, max_steps=max_steps
        )
        root_label = root
    elif root == "pc":
        if pc is None:
            raise ValueError("root='pc' needs pc=<address>")
        memory = h.load_image_memory(image)
        init = h.run_init(memory, image)
        if not init.ran:
            raise RuntimeError("run_init did not complete: %s" % init.error)
        base_runner = h.new_runner(memory, image, init=init)
        base_runner.state.record_events = True
        final_runner = base_runner.fresh_call(pc, diagnose_unknown=True)
        result = final_runner.run(max_steps=max_steps)
        root_label = "pc %#x" % pc
    else:
        raise ValueError("unknown root %r (want 'voice', 'frame' or 'pc')" % root)

    checked, per_form, mismatches = _audit_trace(final_runner, final_runner.state.trace)
    return AuditReport(
        root=root_label,
        image=image,
        instructions=result.instructions,
        halt_reason=result.halt.reason,
        halt_pc=result.halt.pc_sw,
        halt_form=result.halt.form,
        events_checked=checked,
        per_form_checked=per_form,
        mismatches=mismatches,
    )


# --- static lint ---------------------------------------------------------


@dataclass
class StaticFinding:
    form: str
    handler: str
    missing_fields: list[str]


def static_audit(decode_table_path: str | None = None) -> list[StaticFinding]:
    """For every decode_table.json form with a width/scale field, check
    that its FORMS-table handler's own source mentions that field's name
    (a source grep via ``inspect.getsource``, not a semantic proof -- see
    this module's docstring). Returns one StaticFinding per form missing at
    least one field, empty when every width-bearing form's handler at
    least references all of its own width fields."""
    forms = _forms_with_width_fields(decode_table_path)
    findings: list[StaticFinding] = []
    for form_key, field_names in sorted(forms.items()):
        handler = FORMS.get(form_key)
        if handler is None:
            findings.append(
                StaticFinding(form_key, "<no handler>", sorted(field_names))
            )
            continue
        source = inspect.getsource(handler)
        missing = sorted(
            name
            for name in field_names
            if not re.search(r"""["']%s["']""" % re.escape(name), source)
        )
        if missing:
            findings.append(StaticFinding(form_key, handler.__name__, missing))
    return findings


# --- CLI -------------------------------------------------------------------


def _print_report(report: AuditReport) -> None:
    print(
        "%s (%s): %d instructions, halt=%s at %#x (%s)"
        % (
            report.root,
            report.image,
            report.instructions,
            report.halt_reason,
            report.halt_pc,
            report.halt_form or "?",
        )
    )
    print(
        "checked %d load/store events across %d width-bearing forms: %s"
        % (
            report.events_checked,
            len(report.per_form_checked),
            ", ".join(
                "%s=%d" % (form, n)
                for form, n in sorted(report.per_form_checked.items())
            )
            or "(none)",
        )
    )
    if not report.mismatches:
        print("0 mismatches")
        return
    by_form: dict[str, list[Mismatch]] = collections.defaultdict(list)
    for m in report.mismatches:
        by_form[m.form].append(m)
    print("%d mismatches:" % len(report.mismatches))
    for form, group in sorted(by_form.items()):
        print("  form %s (%d):" % (form, len(group)))
        for m in group:
            print(
                "    pc=%#x %s %s: handler=%s expected=%s fields=%s"
                % (
                    m.pc,
                    m.space,
                    m.action,
                    m.actual_width,
                    m.expected_width,
                    m.fields,
                )
            )


def _print_static(findings: list[StaticFinding]) -> None:
    if not findings:
        print("static: every width-bearing form's handler reads all its own fields")
        return
    print("static: %d form(s) with an unread width field:" % len(findings))
    for f in findings:
        print("  Type%s (%s): missing %s" % (f.form, f.handler, f.missing_fields))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="dt2-1.16")
    parser.add_argument(
        "--root",
        choices=("voice", "frame"),
        default="voice",
        help="which root to run concretely (ignored with --pc or --static)",
    )
    parser.add_argument(
        "--pc",
        type=lambda s: int(s, 0),
        default=None,
        help="run this address as a fresh call from a run_init() state instead",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=4_000_000,
        help="bound on the concrete run (default 4_000_000)",
    )
    parser.add_argument(
        "--static",
        action="store_true",
        help="run the static field-usage lint instead of a concrete audit",
    )
    parser.add_argument(
        "--json", action="store_true", help="print JSON instead of a table"
    )
    args = parser.parse_args(argv)

    if args.static:
        findings = static_audit()
        if args.json:
            print(json.dumps([asdict(f) for f in findings], indent=2))
        else:
            _print_static(findings)
        return 1 if findings else 0

    root = "pc" if args.pc is not None else args.root
    report = audit_root(root, image=args.image, pc=args.pc, max_steps=args.max_steps)
    if args.json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        _print_report(report)
    return 1 if report.mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
