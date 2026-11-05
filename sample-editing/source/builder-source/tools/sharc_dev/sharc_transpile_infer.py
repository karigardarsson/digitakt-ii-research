"""Type facts for tools/sharc_dev/sharc_transpile/: mypy's view of tools/sharc_dev/sharc_core,
with the core's unannotated handler parameters typed from their callers.

The translator needs a static type for every expression it emits. mypy
already has one for the annotated core. About 100 compute handlers are
written without parameter annotations (they are reached through op tables,
``ALU_OPS[opcode](rn, rx, ry, left, right, values, special, approx_recips)``),
so mypy sees their parameters as ``Any``. This module finds each such
parameter's type from its call sites -- direct calls, calls through a
table or a function-valued parameter (a small points-to analysis over the
core's live module objects), and the table's declared ``Callable`` type --
writes an annotated copy of the core under a work directory (same line
numbers: only the ``def`` lines change), and re-runs mypy on it until the
annotations stop changing. The core itself is never modified.

Public entry point: ``load(work_dir)`` -> ``Core``.
"""

from __future__ import annotations

import ast
import importlib
import os
import sys
import types as pytypes
from dataclasses import dataclass, field
from typing import Any

from tools import REPO_ROOT

HERE = os.path.dirname(os.path.abspath(__file__))

from mypy import build  # noqa: E402
from mypy import nodes as mn  # noqa: E402
from mypy import types as mt  # noqa: E402
from mypy.modulefinder import BuildSource  # noqa: E402
from mypy.options import Options  # noqa: E402
from mypy.typeops import make_simplified_union  # noqa: E402

CORE_DIR = os.path.join(HERE, "sharc_core")
PACKAGE = "sharc_core"
# mypy checks the annotated copy as top-level `sharc_core`; the live modules are
# imported under the package path.
LIVE_PACKAGE = "tools.sharc_dev.sharc_core"

# Modules that need names in the added annotations.
_ANNOTATION_IMPORTS = (
    "import builtins, typing, collections.abc, types, "
    "tools.sharc_dev.sharc_disasm, tools.sharc_dev.sharcldr, "
    "sharc_core.values, sharc_core.state"
)


def live_module(name: str) -> str:
    """The importable name of a core module given by its mypy name."""
    if name == PACKAGE or name.startswith(PACKAGE + "."):
        return LIVE_PACKAGE + name[len(PACKAGE) :]
    return name


def core_modules(core_dir: str = CORE_DIR) -> list[str]:
    """Module names of the core, sorted (``sharc_core``, ``sharc_core.x``)."""
    names = []
    for fn in sorted(os.listdir(core_dir)):
        if fn.endswith(".py"):
            names.append(PACKAGE if fn == "__init__.py" else PACKAGE + "." + fn[:-3])
    return names


def module_file(directory: str, module: str) -> str:
    if module == PACKAGE:
        return os.path.join(directory, "__init__.py")
    return os.path.join(directory, module.split(".", 1)[1] + ".py")


# ---------------------------------------------------------------------------
# mypy
# ---------------------------------------------------------------------------


_SOFT_ERRORS: list[str] = []


def _mypy(directory: str) -> build.BuildResult:
    opts = Options()
    opts.python_version = (3, 11)
    opts.mypy_path = [str(REPO_ROOT)]
    opts.ignore_missing_imports = True
    opts.check_untyped_defs = True
    opts.follow_imports = "silent"
    opts.preserve_asts = True
    opts.export_types = True
    opts.incremental = False
    sources = [BuildSource(module_file(directory, m), m) for m in core_modules()]
    result = build.build(sources, opts)
    # Precision complaints about the added annotations (a table's declared
    # Value where a caller passes Operand) do not matter to the translation:
    # every value class maps to the same native type. Syntax or name errors
    # would, so those stop the run.
    errors = [e for e in result.errors if " error: " in e]
    fatal = [
        e
        for e in errors
        if "[arg-type]" not in e
        and "[dict-item]" not in e
        and "[return-value]" not in e
        and "[assignment]" not in e
    ]
    if fatal:
        raise SystemExit(
            "sharc_transpile: mypy errors on %s:\n  %s"
            % (directory, "\n  ".join(fatal[:20]))
        )
    _SOFT_ERRORS[:] = errors
    return result


def render_type(t: mt.Type) -> str:
    """A Python annotation for mypy type T, in fully qualified names."""
    p = mt.get_proper_type(t)
    if isinstance(p, mt.AnyType):
        return "typing.Any"
    if isinstance(p, mt.NoneType):
        return "None"
    if isinstance(p, mt.Instance):
        name = p.type.fullname
        # mypy represents a variable-length tuple as an Instance of builtins.tuple with one argument; a fixed-length tuple is a TupleType.
        if name == "builtins.tuple" and len(p.args) == 1:
            return "builtins.tuple[%s, ...]" % render_type(p.args[0])
        if p.args:
            return "%s[%s]" % (name, ", ".join(render_type(a) for a in p.args))
        return name
    if isinstance(p, mt.TupleType):
        fallback = p.partial_fallback.type.fullname
        if fallback != "builtins.tuple":
            return fallback
        if not p.items:
            return "tuple[()]"
        return "tuple[%s]" % ", ".join(render_type(i) for i in p.items)
    if isinstance(p, mt.UnionType):
        return " | ".join(render_type(i) for i in p.items)
    if isinstance(p, mt.CallableType):
        return "collections.abc.Callable[[%s], %s]" % (
            ", ".join(render_type(a) for a in p.arg_types),
            render_type(p.ret_type),
        )
    if isinstance(p, mt.LiteralType):
        return render_type(p.fallback)
    if isinstance(p, mt.UninhabitedType):
        return "typing.NoReturn"
    raise ValueError("cannot render type %s (%s)" % (p, type(p).__name__))


# ---------------------------------------------------------------------------
# Tree walking (mypy's compiled node classes cannot be subclassed as
# visitors, so walk them by attribute).
# ---------------------------------------------------------------------------

_CHILD_ATTRS: dict[str, tuple[str, ...]] = {
    "Block": ("body",),
    "ExpressionStmt": ("expr",),
    "AssignmentStmt": ("lvalues", "rvalue"),
    "OperatorAssignmentStmt": ("lvalue", "rvalue"),
    "IfStmt": ("expr", "body", "else_body"),
    "ForStmt": ("index", "expr", "body", "else_body"),
    "WhileStmt": ("expr", "body", "else_body"),
    "ReturnStmt": ("expr",),
    "RaiseStmt": ("expr", "from_expr"),
    "AssertStmt": ("expr", "msg"),
    "DelStmt": ("expr",),
    "TryStmt": ("body", "types", "handlers", "else_body", "finally_body"),
    "CallExpr": ("callee", "args"),
    "MemberExpr": ("expr",),
    "IndexExpr": ("base", "index"),
    "ConditionalExpr": ("cond", "if_expr", "else_expr"),
    "OpExpr": ("left", "right"),
    "ComparisonExpr": ("operands",),
    "UnaryExpr": ("expr",),
    "TupleExpr": ("items",),
    "ListExpr": ("items",),
    "SetExpr": ("items",),
    "SliceExpr": ("begin_index", "end_index", "stride"),
    "StarExpr": ("expr",),
    "ListComprehension": ("generator",),
    "SetComprehension": ("generator",),
    "GeneratorExpr": ("left_expr", "sequences", "condlists"),
    "DictionaryComprehension": ("key", "value", "sequences", "condlists"),
    "CastExpr": ("expr",),
    "AssignmentExpr": ("target", "value"),
    "LambdaExpr": ("body",),
    "ReturnStmt_": (),
}


def children(node: Any) -> list[Any]:
    out: list[Any] = []
    kind = type(node).__name__
    if kind == "DictExpr":
        for k, v in node.items:
            if k is not None:
                out.append(k)
            out.append(v)
        return out
    for attr in _CHILD_ATTRS.get(kind, ()):
        value = getattr(node, attr, None)
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                if isinstance(item, list):
                    out.extend(x for x in item if x is not None)
                elif item is not None:
                    out.append(item)
        else:
            out.append(value)
    return out


def walk(node: Any):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(children(n)))


# ---------------------------------------------------------------------------
# Points-to analysis over callables and tables
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Obj:
    """An abstract value: a module-level core function ('fn', fullname), a
    live constant container ('const', id), a builtin function ('builtin',
    name), or None ('none',)."""

    kind: str
    key: Any


NONE_OBJ = Obj("none", None)


@dataclass
class Core:
    """mypy's result for the (annotated) core plus the analyses the
    translator consumes."""

    directory: str
    result: build.BuildResult
    trees: dict[str, mn.MypyFile]
    types: dict[mn.Expression, mt.Type]
    modules: dict[str, pytypes.ModuleType]
    funcs: dict[str, mn.FuncDef]  # fullname -> FuncDef
    live_funcs: dict[int, str]  # id(function object) -> fullname
    consts: dict[int, Any]  # id -> live constant object
    # points-to: id(expression node) -> frozenset[Obj]
    pts: dict[int, frozenset[Obj]] = field(default_factory=dict)
    # (function fullname, parameter name) -> frozenset[Obj]
    param_pts: dict[tuple[str, str], frozenset[Obj]] = field(default_factory=dict)
    # dispatch call sites: id(CallExpr) -> frozenset of target Obj
    dispatch: dict[int, frozenset[Obj]] = field(default_factory=dict)
    annotated: dict[str, dict[str, str]] = field(default_factory=dict)

    def type_of(self, expr: mn.Expression) -> mt.ProperType | None:
        t = self.types.get(expr)
        return None if t is None else mt.get_proper_type(t)

    def live_value(self, fullname: str) -> Any:
        module, _, name = fullname.rpartition(".")
        if not module:
            return None
        mod = self.modules.get(module)
        if mod is None:
            try:
                mod = importlib.import_module(live_module(module))
            except (ImportError, ValueError):
                return None
        return getattr(mod, name, None)


def _import_live() -> dict[str, pytypes.ModuleType]:
    return {m: importlib.import_module(live_module(m)) for m in core_modules()}


def _obj_of_live(core: Core, value: Any) -> Obj | None:
    if value is None:
        return NONE_OBJ
    if isinstance(value, pytypes.FunctionType):
        name = core.live_funcs.get(id(value))
        if name is not None:
            return Obj("fn", name)
        return None
    if isinstance(value, pytypes.BuiltinFunctionType) or value is abs:
        return Obj("builtin", getattr(value, "__name__", repr(value)))
    if isinstance(value, (dict, tuple, list, pytypes.MappingProxyType, frozenset)):
        core.consts[id(value)] = value
        return Obj("const", id(value))
    return None


def _elements(core: Core, obj: Obj, index: Any = None) -> set[Obj]:
    """Abstract values stored in container OBJ (all of them, or the one at
    a constant INDEX)."""
    if obj.kind != "const":
        return set()
    value = core.consts[obj.key]
    items: list[Any]
    if isinstance(value, (dict, pytypes.MappingProxyType)):
        if index is not None and index in value:
            items = [value[index]]
        else:
            items = list(value.values())
    elif isinstance(value, (tuple, list)):
        if isinstance(index, int) and -len(value) <= index < len(value):
            items = [value[index]]
        else:
            items = list(value)
    else:
        items = list(value)
    out: set[Obj] = set()
    for item in items:
        o = _obj_of_live(core, item)
        if o is not None:
            out.add(o)
    return out


class _PointsTo:
    """Flow-insensitive points-to sets for every expression, local and
    parameter of the core, iterated to a fixed point."""

    def __init__(self, core: Core) -> None:
        self.core = core
        self.locals: dict[tuple[str, str], set[Obj]] = {}
        self.params: dict[tuple[str, str], set[Obj]] = {}
        self.changed = True

    def _add(self, table: dict, key: Any, objs: set[Obj]) -> None:
        cur = table.setdefault(key, set())
        before = len(cur)
        cur |= objs
        if len(cur) != before:
            self.changed = True

    def expr(self, fn: str, e: Any) -> set[Obj]:
        core = self.core
        kind = type(e).__name__
        if kind == "NameExpr":
            node = e.node
            if isinstance(node, mn.FuncDef) and node.fullname in core.funcs:
                return {Obj("fn", node.fullname)}
            if isinstance(node, mn.Var) and e.kind == mn.LDEF:
                key = (fn, e.name)
                if key in self.params:
                    return set(self.params[key])
                return set(self.locals.get(key, set()))
            if e.fullname:
                if e.fullname == "builtins.abs":
                    return {Obj("builtin", "abs")}
                if e.fullname == "builtins.None":
                    return {NONE_OBJ}
                value = core.live_value(e.fullname)
                o = _obj_of_live(core, value)
                return {o} if o is not None else set()
            return set()
        if kind == "MemberExpr":
            if e.fullname:
                value = core.live_value(e.fullname)
                o = _obj_of_live(core, value)
                return {o} if o is not None else set()
            return set()
        if kind == "IndexExpr":
            base = self.expr(fn, e.base)
            index = _const_index(e.index)
            out: set[Obj] = set()
            for o in base:
                out |= _elements(core, o, index)
            return out
        if kind == "CallExpr":
            callee = e.callee
            if type(callee).__name__ == "MemberExpr" and callee.name == "get":
                base = {o for o in self.expr(fn, callee.expr) if o.kind == "const"}
                if base:
                    index = _const_index(e.args[0]) if e.args else None
                    out = set()
                    for o in base:
                        out |= _elements(core, o, index)
                    if len(e.args) > 1:
                        out |= self.expr(fn, e.args[1])
                    else:
                        out.add(NONE_OBJ)
                    return out
            return set()
        if kind == "ConditionalExpr":
            return self.expr(fn, e.if_expr) | self.expr(fn, e.else_expr)
        if kind == "OpExpr" and e.op in ("or", "and"):
            return self.expr(fn, e.left) | self.expr(fn, e.right)
        return set()

    def _assign(self, fn: str, target: Any, objs_of_item) -> None:
        kind = type(target).__name__
        if kind == "NameExpr" and target.kind == mn.LDEF:
            objs = objs_of_item(None)
            if objs:
                key = (fn, target.name)
                if key in self.params:
                    self._add(self.params, key, objs)
                else:
                    self._add(self.locals, key, objs)
        elif kind in ("TupleExpr", "ListExpr"):
            for i, item in enumerate(target.items):
                self._assign(fn, item, lambda _x, i=i: objs_of_item(i))

    def function(self, fname: str, fdef: mn.FuncDef) -> None:
        core = self.core
        for arg in fdef.arguments:
            self.params.setdefault((fname, arg.variable.name), set())
        for node in walk(fdef.body):
            kind = type(node).__name__
            if kind == "AssignmentStmt":
                rvalue = node.rvalue
                for lvalue in node.lvalues:
                    self._assign(
                        fname, lvalue, lambda i, rv=rvalue: self._item(fname, rv, i)
                    )
            elif kind == "ForStmt":
                seq = self.expr(fname, node.expr)
                elems: set[Obj] = set()
                for o in seq:
                    elems |= _elements(core, o)
                self._assign(
                    fname,
                    node.index,
                    lambda i, el=elems: (
                        el
                        if i is None
                        else {x for o in el for x in _elements(core, o, i)}
                    ),
                )
            elif kind == "CallExpr":
                self._call(fname, node)

    def _item(self, fn: str, rvalue: Any, index: int | None) -> set[Obj]:
        if index is None:
            return self.expr(fn, rvalue)
        if type(rvalue).__name__ in ("TupleExpr", "ListExpr"):
            if index < len(rvalue.items):
                return self.expr(fn, rvalue.items[index])
            return set()
        out: set[Obj] = set()
        for o in self.expr(fn, rvalue):
            out |= _elements(self.core, o, index)
        return out

    def _call(self, fname: str, call: Any) -> None:
        core = self.core
        callee = call.callee
        targets: set[Obj]
        if type(callee).__name__ == "NameExpr" and isinstance(callee.node, mn.FuncDef):
            if callee.node.fullname not in core.funcs:
                return
            targets = {Obj("fn", callee.node.fullname)}
            direct = True
        else:
            targets = {o for o in self.expr(fname, callee) if o.kind == "fn"}
            direct = False
            if targets or type(callee).__name__ == "NameExpr":
                all_targets = {
                    o for o in self.expr(fname, callee) if o.kind in ("fn", "builtin")
                }
                if all_targets:
                    prev = core.dispatch.get(id(call), frozenset())
                    if not all_targets <= prev:
                        core.dispatch[id(call)] = prev | frozenset(all_targets)
                        self.changed = True
        del direct
        for target in targets:
            tdef = core.funcs[target.key]
            params = [a.variable.name for a in tdef.arguments]
            for i, (arg, akind, aname) in enumerate(
                zip(call.args, call.arg_kinds, call.arg_names, strict=True)
            ):
                if akind == mn.ARG_POS and i < len(params):
                    pname = params[i]
                elif akind == mn.ARG_NAMED and aname in params:
                    pname = aname
                else:
                    continue
                objs = self.expr(fname, arg)
                if objs:
                    self._add(self.params, (target.key, pname), objs)

    def run(self) -> None:
        core = self.core
        rounds = 0
        while self.changed:
            self.changed = False
            rounds += 1
            if rounds > 50:
                raise SystemExit("sharc_transpile: points-to analysis does not settle")
            for fname, fdef in core.funcs.items():
                self.function(fname, fdef)
        # Record the final sets per expression for the translator.
        for fname, fdef in core.funcs.items():
            for node in walk(fdef.body):
                if isinstance(node, mn.Expression):
                    objs = self.expr(fname, node)
                    if objs:
                        core.pts[id(node)] = frozenset(objs)
        for key, objs in self.params.items():
            if objs:
                core.param_pts[key] = frozenset(objs)


def _const_index(e: Any) -> Any:
    kind = type(e).__name__
    if kind == "IntExpr":
        return e.value
    if kind == "StrExpr":
        return e.value
    if kind == "UnaryExpr" and e.op == "-" and type(e.expr).__name__ == "IntExpr":
        return -e.expr.value
    return None


# ---------------------------------------------------------------------------
# Annotation inference
# ---------------------------------------------------------------------------


def _collect_funcs(trees: dict[str, mn.MypyFile]) -> dict[str, mn.FuncDef]:
    funcs: dict[str, mn.FuncDef] = {}
    for module, tree in trees.items():
        for d in tree.defs:
            if isinstance(d, mn.FuncDef):
                funcs[module + "." + d.name] = d
    return funcs


def _untyped_params(fdef: mn.FuncDef) -> list[str]:
    return [
        a.variable.name
        for a in fdef.arguments
        if a.type_annotation is None and a.kind not in (mn.ARG_STAR, mn.ARG_STAR2)
    ]


def _needs_return(fdef: mn.FuncDef) -> bool:
    t = fdef.type
    if not isinstance(t, mt.CallableType):
        return True
    ret = mt.get_proper_type(t.ret_type)
    if isinstance(ret, mt.AnyType):
        return True
    if isinstance(ret, mt.Instance) and ret.type.fullname == "builtins.tuple":
        return isinstance(mt.get_proper_type(ret.args[0]), mt.AnyType)
    return False


def _table_callables(core: Core) -> dict[str, list[mt.CallableType]]:
    """Declared Callable types of module-level function tables, per member
    function: ``ALU_OPS: dict[int, Handler]`` types every handler in it."""
    out: dict[str, list[mt.CallableType]] = {}
    for module, tree in core.trees.items():
        for d in tree.defs:
            if not isinstance(d, mn.AssignmentStmt) or d.type is None:
                continue
            declared = mt.get_proper_type(d.type)
            if not isinstance(declared, mt.Instance) or len(declared.args) != 2:
                continue
            value_type = mt.get_proper_type(declared.args[1])
            if not isinstance(value_type, mt.CallableType):
                continue
            for lv in d.lvalues:
                if not isinstance(lv, mn.NameExpr):
                    continue
                live = core.live_value(module + "." + lv.name)
                if not isinstance(live, dict):
                    continue
                for fn in live.values():
                    name = core.live_funcs.get(id(fn))
                    if name is not None:
                        out.setdefault(name, []).append(value_type)
    # Builder functions returning tables (compute_mult._build_mult_ops).
    for fdef in core.funcs.values():
        t = fdef.type
        if not isinstance(t, mt.CallableType):
            continue
        ret = mt.get_proper_type(t.ret_type)
        parts = ret.items if isinstance(ret, mt.TupleType) else [ret]
        for part in parts:
            part = mt.get_proper_type(part)
            if not isinstance(part, mt.Instance) or len(part.args) != 2:
                continue
            value_type = mt.get_proper_type(part.args[1])
            if not isinstance(value_type, mt.CallableType):
                continue
            for node in walk(fdef.body):
                if type(node).__name__ != "AssignmentStmt":
                    continue
                for lv in node.lvalues:
                    if type(lv).__name__ == "IndexExpr":
                        rv = node.rvalue
                        if type(rv).__name__ == "NameExpr" and isinstance(
                            rv.node, mn.FuncDef
                        ):
                            out.setdefault(rv.node.fullname, []).append(value_type)
    return out


def _infer(core: Core) -> dict[str, dict[str, str]]:
    """{function fullname: {param or 'return': annotation}} for every
    untyped parameter and bare return that the call sites determine."""
    candidates: dict[tuple[str, str], list[mt.Type]] = {}
    table_candidates: dict[tuple[str, str], list[mt.Type]] = {}

    def add(fn: str, param: str, t: mt.Type | None, into: dict | None = None) -> None:
        if t is None:
            return
        p = mt.get_proper_type(t)
        if isinstance(p, mt.AnyType):
            return
        if isinstance(p, mt.TupleType) and any(
            isinstance(mt.get_proper_type(i), mt.AnyType) for i in p.items
        ):
            return
        try:
            render_type(t)
        except ValueError:
            return
        (candidates if into is None else into).setdefault((fn, param), []).append(t)

    for name, callables in _table_callables(core).items():
        fdef = core.funcs[name]
        params = [a.variable.name for a in fdef.arguments]
        for ct in callables:
            if ct.is_ellipsis_args:
                continue
            for pname, at in zip(params, ct.arg_types, strict=False):
                add(name, pname, at, table_candidates)

    for fdef in core.funcs.values():
        for node in walk(fdef.body):
            if type(node).__name__ != "CallExpr":
                continue
            callee = node.callee
            if type(callee).__name__ == "NameExpr" and isinstance(
                callee.node, mn.FuncDef
            ):
                targets = (
                    [callee.node.fullname] if callee.node.fullname in core.funcs else []
                )
            else:
                targets = [
                    o.key for o in core.dispatch.get(id(node), ()) if o.kind == "fn"
                ]
            for target in targets:
                tdef = core.funcs[target]
                params = [a.variable.name for a in tdef.arguments]
                for i, (arg, akind, aname) in enumerate(
                    zip(node.args, node.arg_kinds, node.arg_names, strict=True)
                ):
                    if akind == mn.ARG_POS and i < len(params):
                        pname = params[i]
                    elif akind == mn.ARG_NAMED and aname in params:
                        pname = aname
                    else:
                        continue
                    add(target, pname, core.types.get(arg))

    # Function-valued parameters: the functions passed in.
    for (fname, pname), objs in core.param_pts.items():
        for o in objs:
            if o.kind == "fn":
                add(fname, pname, core.funcs[o.key].type)

    out: dict[str, dict[str, str]] = {}
    for fname, fdef in core.funcs.items():
        if fname.startswith("sharc_core.values."):
            continue
        entry: dict[str, str] = {}
        for pname in _untyped_params(fdef):
            cands = candidates.get((fname, pname)) or table_candidates.get(
                (fname, pname)
            )
            if cands:
                entry[pname] = render_type(make_simplified_union(cands))
        if _needs_return(fdef):
            rets = []
            for node in walk(fdef.body):
                if type(node).__name__ == "ReturnStmt" and node.expr is not None:
                    t = core.types.get(node.expr)
                    if t is not None:
                        rets.append(t)
            if rets:
                u = make_simplified_union(rets)
                p = mt.get_proper_type(u)
                if not isinstance(p, mt.AnyType) and "typing.Any" not in render_type(u):
                    entry["return"] = render_type(u)
        if entry:
            out[fname] = entry
    return out


def _write_annotated(
    directory: str, annotations: dict[str, dict[str, str]], core_dir: str = CORE_DIR
) -> None:
    os.makedirs(directory, exist_ok=True)
    for module in core_modules(core_dir):
        src_path = module_file(core_dir, module)
        with open(src_path) as fh:
            source = fh.read()
        lines = source.split("\n")
        tree = ast.parse(source)
        edits: list[tuple[int, int, int | None, str]] = []  # (line, start, end, text)
        prefix = module + "."
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            entry = annotations.get(prefix + node.name)
            if not entry:
                continue
            for arg in node.args.args + node.args.kwonlyargs:
                if arg.annotation is None and arg.arg in entry:
                    end = arg.col_offset + len(arg.arg)
                    edits.append((arg.lineno, end, end, ": " + entry[arg.arg]))
            if "return" in entry:
                if node.returns is not None:
                    r = node.returns
                    assert r.lineno == r.end_lineno
                    edits.append(
                        (r.lineno, r.col_offset, r.end_col_offset, entry["return"])
                    )
                else:
                    # Insert before the ':' that ends the header.
                    body_line = node.body[0].lineno
                    for ln in range(body_line - 1, node.lineno - 1, -1):
                        text = lines[ln - 1]
                        idx = text.rfind(":")
                        if idx >= 0 and text.rstrip().endswith(":"):
                            edits.append((ln, idx, idx, " -> " + entry["return"]))
                            break
        for line, start, stop, text in sorted(edits, key=lambda e: (e[0], -e[1])):
            s = lines[line - 1]
            lines[line - 1] = (
                s[:start] + text + s[stop if stop is not None else start :]
            )
        out = "\n".join(lines)
        future = "from __future__ import annotations"
        if future in out:
            out = out.replace(future, future + "; " + _ANNOTATION_IMPORTS, 1)
        with open(module_file(directory, module), "w") as fh:
            fh.write(out)


def _build_core(directory: str, modules: dict[str, pytypes.ModuleType]) -> Core:
    result = _mypy(directory)
    trees: dict[str, mn.MypyFile] = {}
    for m in core_modules():
        tree = result.graph[m].tree
        assert tree is not None
        trees[m] = tree
    funcs = _collect_funcs(trees)
    live_funcs: dict[int, str] = {}
    for fullname in funcs:
        module, _, name = fullname.rpartition(".")
        fn = getattr(modules[module], name, None)
        if isinstance(fn, pytypes.FunctionType):
            live_funcs[id(fn)] = fullname
    core = Core(
        directory=directory,
        result=result,
        trees=trees,
        types=result.types,
        modules=modules,
        funcs=funcs,
        live_funcs=live_funcs,
        consts={},
    )
    _PointsTo(core).run()
    return core


def load(work_dir: str, *, max_rounds: int = 6, core_dir: str = CORE_DIR) -> Core:
    """mypy's view of the core with call-site annotations filled in (see
    the module docstring). WORK_DIR receives the annotated copy; CORE_DIR
    is the core's source (tests use a modified copy)."""
    modules = _import_live()
    annotations: dict[str, dict[str, str]] = {}
    directory = os.path.join(work_dir, PACKAGE)
    for _ in range(max_rounds):
        _write_annotated(directory, annotations, core_dir)
        core = _build_core(directory, modules)
        new = _infer(core)
        merged = {k: dict(v) for k, v in annotations.items()}
        for fn, entry in new.items():
            merged.setdefault(fn, {}).update(entry)
        if merged == annotations:
            core.annotated = annotations
            return core
        annotations = merged
    raise SystemExit("sharc_transpile: annotation inference does not settle")


if __name__ == "__main__":
    import time

    t0 = time.time()
    c = load(sys.argv[1] if len(sys.argv) > 1 else "out/native/transpile-work")
    print("functions", len(c.funcs), "annotated", len(c.annotated), time.time() - t0)
    missing = []
    for fname, fdef in c.funcs.items():
        if fname.startswith("sharc_core.values."):
            continue
        for p in _untyped_params(fdef):
            if p not in c.annotated.get(fname, {}):
                missing.append("%s(%s)" % (fname, p))
        if _needs_return(fdef) and "return" not in c.annotated.get(fname, {}):
            missing.append("%s -> ?" % fname)
    print("still untyped:", len(missing))
    for m in missing[:60]:
        print("  ", m)
