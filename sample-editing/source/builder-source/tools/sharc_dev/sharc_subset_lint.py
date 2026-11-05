"""Check tools/sharc_dev/sharc_core against its translatable subset.

The subset is specified in tools/sharc_dev/sharc_core/SUBSET.md. This lint finds the
constructs it can see in the syntax tree, counts them per module and rule,
and compares the counts with the allowlist in
tools/sharc_dev/sharc_core/subset_allowlist.json. The allowlist only shrinks: a
count above its allowance is a new violation, and a count below it means
the allowance must be lowered (``--update`` rewrites the file).

    uv run python -m tools.sharc_dev.sharc_subset_lint            # table, exit 1 on a regression
    uv run python -m tools.sharc_dev.sharc_subset_lint --detail   # every violation with its line
    uv run python -m tools.sharc_dev.sharc_subset_lint --update   # lower the allowlist to the counts

Rules (see SUBSET.md for the reasons and the allowed forms):

  observability-effect  a call with a possible effect inside the arguments
                      of an observability call (erased by a transpiler)
  lambda              a lambda expression
  closure             a def nested inside a def
  isinstance-type     isinstance() against a type other than the value lattice
                      (int, str, tuple, MR, ...): run-time type unions
  isinstance-symbolic isinstance() against Affine, Unknown or PartialConst
  struct-math         struct.* or math.* outside the float primitives
  try                 a try statement outside the float primitives, other
                      than the trap-to-stop form (TRAP_EXCEPTIONS)
  dict-build          a dict or set built inside a function: literal,
                      comprehension, or dict()/set()/frozenset() call
  comprehension       a list comprehension or generator expression inside a function
  dynamic             getattr/setattr/hasattr/eval/exec/globals/locals/vars/type,
                      global/nonlocal, yield, with, *args/**kwargs
  host-builtin        round/pow/divmod/sorted/hash, or ** on a non-literal
                      operand, inside a function

Not checked: the BOUNDARY functions (replaced wholesale by a concrete
specialisation), ``_build_*`` functions (run once at import to build a
table), docstrings, ``assert`` and ``raise`` statements, and the
arguments of the observability calls in OBSERVABILITY (checked only for
effects). Module-level tables may use dict and set literals.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CORE = os.path.join(ROOT, "tools", "sharc_dev", "sharc_core")
ALLOWLIST = os.path.join(CORE, "subset_allowlist.json")

RULES = (
    "observability-effect",
    "lambda",
    "closure",
    "isinstance-type",
    "isinstance-symbolic",
    "struct-math",
    "try",
    "dict-build",
    "comprehension",
    "dynamic",
    "host-builtin",
)

# The value lattice: the only types a semantic function may test. Const
# (and MR for the 80-bit accumulators) is the known-ness test every driver
# shares; the others are symbolic.
LATTICE_TYPES = frozenset({"Const", "MR", "Affine", "Unknown", "PartialConst"})
SYMBOLIC_TYPES = frozenset({"Affine", "Unknown", "PartialConst"})

# The boundary: code a concrete specialisation replaces wholesale instead of
# translating. values.py is the value lattice (Const/Affine/Unknown/
# PartialConst arithmetic and ASTATX FlagUpdate application); the named
# state.py functions are the 80-bit MR lattice, register reads that
# collapse PartialConst, the fork copy, the register snapshot and the
# observability renderers; sequencer.decode_at runs at generation time;
# memory._dossier builds the external-call report. All rules skip them.
# A whole module is named by its file name alone.
BOUNDARY = frozenset(
    {
        "values.py",
        "state.py:MR.__post_init__",
        "state.py:MR.known",
        "state.py:MR.signed",
        "state.py:_mr_from_signed",
        "state.py:_mr_read_word",
        "state.py:_mr_write_word",
        "state.py:_render",
        "state.py:_json_value",
        "state.py:_event",
        "state.py:_stop",
        "state.py:_copy",
        "state.py:_note_provisional",
        "state.py:_snapshot_uregs",
        "state.py:_ureg",
        "state.py:_ureg_raw",
        "state.py:_pey_view",
        "state.py:_pey_special",
        "sequencer.py:decode_at",
        "encoding.py:_split_compute_fields",
        "memory.py:_dossier",
    }
)

# A compute result's destination is a tagged union (ComputeDest in
# values.py: a register number, a special-register name, or a tuple of
# those; a Rust enum). These functions are the only ones that match on it,
# so isinstance() against int, str and tuple is allowed there.
DEST_MATCH = frozenset(
    {
        "compute.py:_apply_compute",
        "compute.py:_apply_compute_pey",
        "compute.py:_shift_immediate_pey",
    }
)
DEST_TYPES = frozenset({"int", "str", "tuple"})

# Float primitives: the only code that may use struct, math or try. Each
# has an exact Rust equivalent, listed in SUBSET.md.
FLOAT_PRIMITIVES = frozenset(
    {
        "floats.py:_f32_from_bits",
        "floats.py:_float32_bits",
        "floats.py:_f64_from_words",
        "floats.py:_double_pair_bits",
        "floats.py:_ldexp",
        "floats.py:_trunc_int",
        "floats.py:_round_even_int",
        "floats.py:<module>",
    }
)

# Calls whose arguments are observability only (erased by a transpiler):
# the trace log, stop reasons and Unknown reasons. Their arguments may call
# only the pure functions in PURE, so erasing them loses no effect.
OBSERVABILITY = frozenset({"_event", "_json_value", "_render", "Unknown", "_stop"})
PURE = frozenset(
    {
        "_json_value",
        "_render",
        "_field",
        "str",
        "hex",
        "len",
        "bool",
        "int",
        "list",
        "tuple",
        "all",
        "any",
        "abs",
        "zip",
        "range",
        "enumerate",
        "isinstance",
        "Const",
        "_ureg",
        "_ureg_raw",
        # Address classification reads only the value and fixed memory map.
        "_normal_word_stride",
    }
)
# String methods allowed inside observability arguments.
PURE_METHODS = frozenset({"get", "format", "join", "upper", "lower"})
# A try whose every handler catches only these and just returns turns a
# raise (a trap) into a stop: the one allowed form (SUBSET.md, "Traps").
TRAP_EXCEPTIONS = frozenset({"ValueError", "UnmodeledMMR"})

DYNAMIC_CALLS = frozenset(
    {
        "getattr",
        "setattr",
        "hasattr",
        "delattr",
        "eval",
        "exec",
        "globals",
        "locals",
        "vars",
        "type",
        "callable",
    }
)
HOST_BUILTINS = frozenset({"round", "pow", "divmod", "sorted", "hash"})
DICT_CALLS = frozenset({"dict", "set", "frozenset"})


@dataclass(frozen=True)
class Violation:
    module: str
    function: str
    line: int
    rule: str
    text: str


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _type_names(node: ast.expr) -> list[str]:
    """The class names an isinstance() second argument names."""
    if isinstance(node, ast.Tuple):
        return [name for elt in node.elts for name in _type_names(elt)]
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _type_names(node.left) + _type_names(node.right)
    return ["?"]


def _is_docstring(node: ast.AST, parent: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and parent is not None
        and bool(getattr(parent, "body", None))
        and parent.body[0] is node  # type: ignore[attr-defined]
    )


class _Checker:
    def __init__(self, module: str, source: str):
        self.module = module
        self.lines = source.splitlines()
        self.found: list[Violation] = []

    def _add(self, node: ast.AST, function: str, rule: str) -> None:
        line = getattr(node, "lineno", 0)
        text = self.lines[line - 1].strip() if 0 < line <= len(self.lines) else ""
        self.found.append(Violation(self.module, function, line, rule, text))

    def _exempt(self, function: str, names: frozenset[str]) -> bool:
        return self.module in names or "%s:%s" % (self.module, function) in names

    def visit(
        self,
        node: ast.AST,
        function: str,
        depth: int,
        parent: ast.AST | None = None,
    ) -> None:
        """Walk NODE. FUNCTION is the enclosing top-level function (or
        class.method, or <module>); DEPTH counts enclosing defs."""
        if _is_docstring(node, parent):
            return
        if isinstance(node, (ast.Assert, ast.Raise)):
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if depth >= 1:
                self._add(node, function, "closure")
            name = node.name if depth == 0 else function
            if depth == 0 and isinstance(parent, ast.ClassDef):
                name = "%s.%s" % (parent.name, node.name)
            if depth == 0 and (
                self._exempt(name, BOUNDARY) or node.name.startswith("_build_")
            ):
                return
            for arg in (node.args.vararg, node.args.kwarg):
                if arg is not None and not self._observability_def(node):
                    self._add(node, name, "dynamic")
            for decorator in node.decorator_list:
                self.visit(decorator, name, depth, node)
            for child in node.body:
                self.visit(child, name, depth + 1, node)
            return
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                self.visit(child, function, 0, node)
            return
        in_function = depth >= 1
        if isinstance(node, ast.Lambda):
            self._add(node, function, "lambda")
        elif isinstance(node, ast.Call):
            callee = _call_name(node)
            if callee in OBSERVABILITY or _is_trace_call(node):
                self._check_pure(node, function)
                return
            if callee == "isinstance" and len(node.args) == 2:
                types = _type_names(node.args[1])
                dest_match = self._exempt(function, DEST_MATCH) and all(
                    t in DEST_TYPES for t in types
                )
                if any(t not in LATTICE_TYPES for t in types) and not dest_match:
                    self._add(node, function, "isinstance-type")
                elif any(t in SYMBOLIC_TYPES for t in types):
                    self._add(node, function, "isinstance-symbolic")
            elif callee in DYNAMIC_CALLS:
                self._add(node, function, "dynamic")
            elif (
                in_function
                and callee in HOST_BUILTINS
                and not self._exempt(function, FLOAT_PRIMITIVES)
            ):
                self._add(node, function, "host-builtin")
            elif in_function and callee in DICT_CALLS:
                self._add(node, function, "dict-build")
            if any(isinstance(a, ast.Starred) for a in node.args) or any(
                k.arg is None for k in node.keywords
            ):
                self._add(node, function, "dynamic")
        elif isinstance(node, ast.Attribute):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id in ("struct", "math")
                and not self._exempt(function, FLOAT_PRIMITIVES)
            ):
                self._add(node, function, "struct-math")
        elif isinstance(node, ast.Try):
            if not self._exempt(function, FLOAT_PRIMITIVES) and not _is_trap_catch(
                node
            ):
                self._add(node, function, "try")
        elif isinstance(node, (ast.Dict, ast.Set, ast.DictComp, ast.SetComp)):
            if in_function:
                self._add(node, function, "dict-build")
        elif isinstance(node, (ast.ListComp, ast.GeneratorExp)):
            if in_function:
                self._add(node, function, "comprehension")
        elif isinstance(
            node, (ast.Global, ast.Nonlocal, ast.Yield, ast.YieldFrom, ast.With)
        ):
            self._add(node, function, "dynamic")
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Pow)
            and in_function
            and not (
                isinstance(node.left, ast.Constant)
                and isinstance(node.right, (ast.Constant, ast.UnaryOp))
            )
        ):
            self._add(node, function, "host-builtin")
        for sub in ast.iter_child_nodes(node):
            self.visit(sub, function, depth, node)

    @staticmethod
    def _observability_def(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        return node.name in OBSERVABILITY

    def _check_pure(self, call: ast.Call, function: str) -> None:
        """Flag any call with a possible effect inside an observability
        call's arguments."""
        for arg in [*call.args, *(k.value for k in call.keywords)]:
            for sub in ast.walk(arg):
                if not isinstance(sub, ast.Call):
                    continue
                name = _call_name(sub)
                if name in PURE or name in OBSERVABILITY:
                    continue
                if (
                    isinstance(sub.func, ast.Attribute)
                    and sub.func.attr in PURE_METHODS
                ):
                    continue
                self._add(sub, function, "observability-effect")


def _is_trace_call(node: ast.Call) -> bool:
    """A call on a State's trace log, e.g. state.trace[-1].update(...)."""
    func = node.func
    while isinstance(func, (ast.Attribute, ast.Subscript)):
        if isinstance(func, ast.Attribute) and func.attr == "trace":
            return True
        func = func.value
    return False


def _is_trap_catch(node: ast.Try) -> bool:
    """try: ... except ValueError [as e]: return ... -- the trap-to-stop form."""
    if node.orelse or node.finalbody or not node.handlers:
        return False
    for handler in node.handlers:
        if handler.type is None:
            return False
        names = (
            [e for e in handler.type.elts]
            if isinstance(handler.type, ast.Tuple)
            else [handler.type]
        )
        if not all(isinstance(e, ast.Name) and e.id in TRAP_EXCEPTIONS for e in names):
            return False
        if len(handler.body) != 1 or not isinstance(handler.body[0], ast.Return):
            return False
    return True


def check_source(module: str, source: str) -> list[Violation]:
    tree = ast.parse(source)
    checker = _Checker(module, source)
    for node in tree.body:
        checker.visit(node, "<module>", 0, tree)
    return checker.found


def core_modules(core: str = CORE) -> list[str]:
    return sorted(
        name
        for name in os.listdir(core)
        if name.endswith(".py") and not name.startswith(".")
    )


def check_core(core: str = CORE) -> list[Violation]:
    found: list[Violation] = []
    for module in core_modules(core):
        with open(os.path.join(core, module)) as fh:
            found.extend(check_source(module, fh.read()))
    return found


def counts(found: list[Violation], core: str = CORE) -> dict[str, dict[str, int]]:
    """{module: {rule: count}} with every module present, zero rules omitted."""
    table: dict[str, dict[str, int]] = {m: {} for m in core_modules(core)}
    for (module, rule), n in Counter((v.module, v.rule) for v in found).items():
        table[module][rule] = n
    return {m: dict(sorted(r.items())) for m, r in sorted(table.items())}


def load_allowlist() -> dict[str, dict[str, int]]:
    with open(ALLOWLIST) as fh:
        return json.load(fh)["allowed"]


def compare(
    actual: dict[str, dict[str, int]], allowed: dict[str, dict[str, int]]
) -> tuple[list[str], list[str]]:
    """(regressions, stale allowances) as readable lines."""
    regressions, stale = [], []
    for module in sorted(set(actual) | set(allowed)):
        have, may = actual.get(module, {}), allowed.get(module, {})
        for rule in RULES:
            n, limit = have.get(rule, 0), may.get(rule, 0)
            if n > limit:
                regressions.append("%s %s: %d > allowed %d" % (module, rule, n, limit))
            elif n < limit:
                stale.append("%s %s: %d < allowed %d" % (module, rule, n, limit))
    return regressions, stale


def write_allowlist(actual: dict[str, dict[str, int]]) -> None:
    data = {
        "comment": (
            "Per-module allowance of translatable-subset violations "
            "(tools/sharc_dev/sharc_subset_lint.py, tools/sharc_dev/sharc_core/SUBSET.md). "
            "Counts only go down."
        ),
        "allowed": {m: r for m, r in actual.items() if r},
    }
    with open(ALLOWLIST, "w") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write("\n")


def _table(actual: dict[str, dict[str, int]]) -> Iterator[str]:
    short = [r.replace("isinstance-", "isi-") for r in RULES]
    yield "%-18s %s %6s" % ("module", " ".join("%6s" % s[:6] for s in short), "total")
    totals = Counter[str]()
    for module, rules in actual.items():
        totals.update(rules)
        yield "%-18s %s %6d" % (
            module,
            " ".join("%6d" % rules.get(r, 0) for r in RULES),
            sum(rules.values()),
        )
    yield "%-18s %s %6d" % (
        "total",
        " ".join("%6d" % totals.get(r, 0) for r in RULES),
        sum(totals.values()),
    )
    yield "columns: " + ", ".join(RULES)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--detail", action="store_true", help="list every violation")
    p.add_argument("--module", action="append", help="limit --detail to MODULE")
    p.add_argument("--json", action="store_true", help="print the counts as JSON")
    p.add_argument(
        "--core",
        default=CORE,
        help="check another copy of the core (report only; no allowlist check)",
    )
    p.add_argument(
        "--update", action="store_true", help="lower the allowlist to the counts"
    )
    a = p.parse_args(argv)
    found = check_core(a.core)
    actual = counts(found, a.core)
    if a.json:
        print(json.dumps(actual, indent=1, sort_keys=True))
    else:
        for line in _table(actual):
            print(line)
    if a.detail:
        for v in found:
            if a.module and v.module not in a.module:
                continue
            print("%s:%d %s [%s] %s" % (v.module, v.line, v.function, v.rule, v.text))
    if a.core != CORE:
        return 0
    if a.update:
        allowed = load_allowlist() if os.path.exists(ALLOWLIST) else {}
        regressions, _ = compare(actual, allowed) if allowed else ([], [])
        if regressions:
            print("\n".join(["refusing to raise an allowance:"] + regressions))
            return 1
        write_allowlist(actual)
        return 0
    if not os.path.exists(ALLOWLIST):
        print("no allowlist at %s (run with --update)" % ALLOWLIST)
        return 1
    regressions, stale = compare(actual, load_allowlist())
    for line in regressions:
        print("REGRESSION " + line)
    for line in stale:
        print("STALE      " + line + " (run --update)")
    return 1 if regressions or stale else 0


if __name__ == "__main__":
    sys.exit(main())
