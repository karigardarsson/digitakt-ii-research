"""Trace tools/sharc_dev/sharc_core form handlers into lowering programs.

The generator behind the load-time SHARC+ translator (docs/findings/18-dsp-performance.md).
It runs the real handlers of sharc_core.forms, unmodified, over a tracing
machine state:

- the instruction's decoded fields and the lowering context (MODE1, cfg
  flags) are *static symbols*: they are known when the translator lowers one
  instruction, not when the generator runs. Arithmetic on them builds
  expressions; a test on one (``if f["u"]:``) forks the trace, so each leaf
  carries a path condition over fields that the Rust emitter turns into an
  ``if``;
- the register file contents and memory words are *dynamic nodes*: a
  register read yields ``Const(Dyn)``, so the unchanged ``values`` helpers
  build an expression DAG that the emitter turns into kernel IR calls;
- the few functions that hold range analysis or whole instruction
  semantics (memory transfers, compute bodies, flag replay) are
  intrinsics: the trace records them as effects in program order.

The result per form is a decision tree whose leaves are effect programs.
"""

from __future__ import annotations

import contextlib
import itertools
import os
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc_core  # noqa: E402,F401
from tools.sharc_dev.sharc_core import (  # noqa: E402
    compute,
    encoding,
    forms,
)
from tools.sharc_dev.sharc_core import (  # noqa: E402
    state as st,
)
from tools.sharc_dev.sharc_core.state import State  # noqa: E402
from tools.sharc_dev.sharc_core.values import Const  # noqa: E402

MASK32 = 0xFFFFFFFF
# The real compute decoder, before installed() replaces it by an opaque token.
ORIG_COMPUTE = compute._compute


class Concretize(Exception):
    """A static symbol was used where only a number will do."""

    def __init__(self, sym: S) -> None:
        super().__init__(repr(sym))
        self.sym = sym


class Stop(Exception):
    """The handler stops the instruction (the core's Trap): refuse."""


# ---------------------------------------------------------------------------
# Static symbols (known to the translator when it lowers one instruction)
# ---------------------------------------------------------------------------


def _s(x):
    return x if isinstance(x, S) else S(("c", int(x)))


class S:
    """A static integer expression over fields and lowering context."""

    __slots__ = ("e",)

    def __init__(self, e: tuple) -> None:
        self.e = e

    # arithmetic
    def _bin(self, op, o, rev=False):
        if isinstance(o, D):
            return NotImplemented
        a, b = (_s(o), self) if rev else (self, _s(o))
        if a.e[0] == "c" and b.e[0] == "c":
            return S(("c", _EVAL[op](a.e[1], b.e[1])))
        e = (op, a.e, b.e)
        if op in ("&", "|", "^", "<<", ">>"):
            bv = _bitvec(e)
            if bv is not None:
                simple = _from_bitvec(bv)
                if simple is not None:
                    return S(simple)
        kb = _known_bits(e)
        if kb is not None and kb[0] == -1:
            return S(("c", kb[1]))
        if op == "&":
            # (x & m1) & m2 == x & (m1 & m2); x & 0xFFFFFFFF stays x & mask.
            if b.e[0] == "c" and a.e[0] == "&" and a.e[2][0] == "c":
                return S(("&", a.e[1], ("c", a.e[2][1] & b.e[1])))
            if a.e[0] == "c" and b.e[0] == "&" and b.e[2][0] == "c":
                return S(("&", b.e[1], ("c", b.e[2][1] & a.e[1])))
        return S((op, a.e, b.e))

    def __add__(self, o):
        return self._bin("+", o)

    def __radd__(self, o):
        return self._bin("+", o, True)

    def __sub__(self, o):
        return self._bin("-", o)

    def __rsub__(self, o):
        return self._bin("-", o, True)

    def __mul__(self, o):
        return self._bin("*", o)

    def __rmul__(self, o):
        return self._bin("*", o, True)

    def __and__(self, o):
        return self._bin("&", o)

    def __rand__(self, o):
        return self._bin("&", o, True)

    def __or__(self, o):
        return self._bin("|", o)

    def __ror__(self, o):
        return self._bin("|", o, True)

    def __xor__(self, o):
        return self._bin("^", o)

    def __rxor__(self, o):
        return self._bin("^", o, True)

    def __lshift__(self, o):
        return self._bin("<<", o)

    def __rlshift__(self, o):
        return self._bin("<<", o, True)

    def __rshift__(self, o):
        return self._bin(">>", o)

    def __rrshift__(self, o):
        return self._bin(">>", o, True)

    def __floordiv__(self, o):
        return self._bin("//", o)

    def __invert__(self):
        return S(("~", self.e))

    def __neg__(self):
        return S(("-", ("c", 0), self.e))

    def __abs__(self):
        return -self if self < 0 else self

    # comparison -> predicate
    def _cmp(self, op, o):
        if isinstance(o, D):
            return NotImplemented
        return P(op, self.e, _s(o).e)

    def __eq__(self, o):  # type: ignore[override]
        if o is None:
            return False
        return self._cmp("==", o)

    def __ne__(self, o):  # type: ignore[override]
        if o is None:
            return True
        return self._cmp("!=", o)

    def __lt__(self, o):
        return self._cmp("<", o)

    def __le__(self, o):
        return self._cmp("<=", o)

    def __gt__(self, o):
        return self._cmp(">", o)

    def __ge__(self, o):
        return self._cmp(">=", o)

    def __hash__(self):
        return hash(CTX.pin(self.e))

    def __bool__(self):
        return CTX.decide(P("!=", self.e, ("c", 0)))

    def __index__(self):
        return CTX.concretize(self)

    __int__ = __index__

    def __repr__(self):
        return "S" + fmt_e(self.e)

    def __format__(self, spec):
        return "<sym>"

    def __str__(self):
        return "<sym>"


NBITS = 72


def _bitvec(e: tuple):
    """Static expression E as NBITS bit sources (0, 1 or ("b", field, j)),
    or None when it is not a bitwise function of fields and constants."""
    k = e[0]
    if k == "c":
        v = e[1] & ((1 << NBITS) - 1)
        return [(v >> i) & 1 for i in range(NBITS)]
    if k == "f":
        w = FIELD_DOMAIN.get(e[1])
        if w is None:
            return None
        return [("b", e[1], i) if i < w else 0 for i in range(NBITS)]
    if k == "v" or k in ("+", "-", "*", "//"):
        return None
    if k == "~":
        a = _bitvec(e[1])
        if a is None or any(isinstance(x, tuple) for x in a):
            return None
        return [1 - x for x in a]
    a, b = _bitvec(e[1]), _bitvec(e[2])
    if a is None or b is None:
        return None
    if k in ("<<", ">>"):
        if e[2][0] != "c":
            return None
        n = e[2][1]
        if n < 0 or n >= NBITS:
            return None
        if k == "<<":
            return [0] * n + a[: NBITS - n]
        return a[n:] + [0] * n
    out = []
    for x, y in zip(a, b, strict=False):
        if k == "&":
            if x == 0 or y == 0:
                out.append(0)
            elif x == 1:
                out.append(y)
            elif y == 1 or x == y:
                out.append(x)
            else:
                return None
        elif k == "|":
            if x == 1 or y == 1:
                out.append(1)
            elif x == 0:
                out.append(y)
            elif y == 0 or x == y:
                out.append(x)
            else:
                return None
        elif k == "^":
            if x == 0:
                out.append(y)
            elif y == 0:
                out.append(x)
            else:
                return None
        else:
            return None
    return out


def _from_bitvec(bv):
    """The simplest static expression for bit sources BV, or None."""
    if all(isinstance(x, int) for x in bv):
        return ("c", sum(x << i for i, x in enumerate(bv)))
    # A run of consecutive bits of one field at the bottom, zero above.
    n = 0
    while n < NBITS and isinstance(bv[n], tuple):
        n += 1
    if any(x != 0 for x in bv[n:]):
        return None
    label, j0 = bv[0][1], bv[0][2]
    for i in range(n):
        if bv[i] != ("b", label, j0 + i):
            return None
    w = FIELD_DOMAIN[label]
    base = ("f", label)
    if j0 == 0 and n == w:
        return base
    e = base if j0 == 0 else (">>", base, ("c", j0))
    if j0 + n == w:
        return e
    return ("&", e, ("c", (1 << n) - 1))


def _known_bits(e: tuple):
    """(mask, bits) of the bits of static expression E known whatever the
    fields are (mask -1: the whole value; Python ints, so unbounded)."""
    k = e[0]
    if k == "c":
        return (-1, e[1])
    if k == "f":
        w = FIELD_DOMAIN.get(e[1])
        if w is None:
            return (0, 0)
        return (~((1 << w) - 1), 0)
    if k == "v":
        return (0, 0)
    if k == "~":
        m, b = _known_bits(e[1])
        return (m, ~b & m) if m != -1 else (-1, ~b)
    m1, b1 = _known_bits(e[1])
    m2, b2 = _known_bits(e[2])
    if k == "&":
        return (
            (m1 & m2) | (m1 & ~b1) | (m2 & ~b2),
            b1 & b2 & (m1 & m2) if m1 != -1 or m2 != -1 else b1 & b2,
        )
    if k == "|":
        return (
            (m1 & m2) | (m1 & b1) | (m2 & b2),
            (b1 | b2) & ((m1 & m2) | (m1 & b1) | (m2 & b2)),
        )
    if k == "^":
        return (m1 & m2, (b1 ^ b2) & m1 & m2)
    if k in ("<<", ">>") and e[2][0] == "c":
        n = e[2][1]
        if n < 0 or n > 200:
            return (0, 0)
        if k == "<<":
            return (
                ((m1 << n) | ((1 << n) - 1)),
                (b1 << n) & ((m1 << n) | ((1 << n) - 1)),
            )
        return ((m1 >> n), (b1 >> n) & (m1 >> n))
    if m1 == -1 and m2 == -1 and k in ("+", "-", "*"):
        return (-1, _EVAL[k](b1, b2))
    return (0, 0)


_EVAL = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "&": lambda a, b: a & b,
    "|": lambda a, b: a | b,
    "^": lambda a, b: a ^ b,
    "<<": lambda a, b: a << b,
    ">>": lambda a, b: a >> b,
    "//": lambda a, b: a // b,
}


def fmt_e(e: tuple) -> str:
    k = e[0]
    if k == "c":
        return str(e[1])
    if k == "f":
        return e[1]
    if k == "v":
        return e[1]
    if k == "~":
        return "~" + fmt_e(e[1])
    return "(%s %s %s)" % (fmt_e(e[1]), k, fmt_e(e[2]))


@dataclass(frozen=True)
class P:
    """A static predicate."""

    op: str
    a: tuple
    b: tuple

    def negated(self) -> P:
        flip = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}
        return P(flip[self.op], self.a, self.b)

    def __bool__(self):
        return CTX.decide(self)

    def __repr__(self):
        return "P(%s %s %s)" % (fmt_e(self.a), self.op, fmt_e(self.b))


# ---------------------------------------------------------------------------
# Dynamic nodes (run-time register and memory contents)
# ---------------------------------------------------------------------------


class D:
    """A dynamic 32-bit-wrapping integer: a node of the effect program."""

    __slots__ = ("n",)

    def __init__(self, n: int) -> None:
        self.n = n

    def _bin(self, op, o, rev=False):
        a, b = (o, self) if rev else (self, o)
        return CTX.emit(("bin", op, CTX.val(a), CTX.val(b)))

    def __add__(self, o):
        return self._bin("add", o)

    def __radd__(self, o):
        return self._bin("add", o, True)

    def __sub__(self, o):
        return self._bin("sub", o)

    def __rsub__(self, o):
        return self._bin("sub", o, True)

    def __mul__(self, o):
        return self._bin("mul", o)

    def __rmul__(self, o):
        return self._bin("mul", o, True)

    def __and__(self, o):
        return self._bin("and", o)

    def __rand__(self, o):
        return self._bin("and", o, True)

    def __or__(self, o):
        return self._bin("or", o)

    def __ror__(self, o):
        return self._bin("or", o, True)

    def __xor__(self, o):
        return self._bin("xor", o)

    def __rxor__(self, o):
        return self._bin("xor", o, True)

    def __lshift__(self, o):
        return self._bin("shl", o)

    def __rshift__(self, o):
        return self._bin("shru", o)

    def __invert__(self):
        return CTX.emit(("not", self.n))

    def __neg__(self):
        return CTX.emit(("bin", "sub", CTX.val(0), self.n))

    def __abs__(self):
        raise Stop("dynamic abs")

    def __bool__(self):
        raise Stop("dynamic branch")

    def __index__(self):
        raise Stop("dynamic value used as an index")

    def __repr__(self):
        return "D%d" % self.n

    def __eq__(self, o):  # type: ignore[override]
        raise Stop("dynamic comparison")

    __hash__ = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# The trace context: decisions, effects, concretization
# ---------------------------------------------------------------------------


@dataclass
class Leaf:
    path: list  # [(P, bool)] decisions in order
    effects: list
    refused: str | None = None


class Ctx:
    def __init__(self) -> None:
        self.reset([])

    def reset(self, script: list) -> None:
        self.script = script
        self.pos = 0
        self.path: list = []  # ("d", P, taken) | ("e", ((label, value), ...))
        self.effects: list = []
        self.nnodes = 0
        self.known: dict = {}
        self.pinned: dict = {}
        self.discovered: list = []

    # -- decisions ------------------------------------------------------
    def _const_of(self, e):
        """The value of static expression E when every field in it is pinned
        and it has no run-time symbol, else None."""
        used: set = set()
        _fields_of(e, used)
        if any(l not in self.pinned for l in used) or _has_var(e):  # noqa: E741
            return None
        return _eval_e(e, self.pinned)

    def _bounds(self, e: tuple):
        """(lo, hi, excluded values) of static expression E on this path."""
        if e in self.known:
            return self.known[e]
        if e[0] == "f":
            return (0, (1 << FIELD_DOMAIN[e[1]]) - 1, frozenset())
        if e[0] == "c":
            return (e[1], e[1], frozenset())
        return (-(1 << 80), 1 << 80, frozenset())

    @staticmethod
    def _norm(e, b):
        """E + k vs B is E vs B - k: bounds are kept on the bare expression."""
        while b is not None and e[0] in ("+", "-") and e[2][0] == "c":
            b = b - e[2][1] if e[0] == "+" else b + e[2][1]
            e = e[1]
        return e, b

    def decide(self, p: P) -> bool:
        a, b = self._const_of(p.a), self._const_of(p.b)
        if a is not None and b is not None:
            return bool(_CMP[p.op](a, b))
        ne_, nb = self._norm(p.a, b)
        if b is not None:
            lo, hi, ne = self._bounds(ne_)
            b = nb
            op = p.op
            sure = None
            if op == "==":
                sure = (
                    True
                    if lo == hi == b
                    else (False if b < lo or b > hi or b in ne else None)
                )
            elif op == "!=":
                sure = (
                    False
                    if lo == hi == b
                    else (True if b < lo or b > hi or b in ne else None)
                )
            elif op == "<":
                sure = True if hi < b else (False if lo >= b else None)
            elif op == "<=":
                sure = True if hi <= b else (False if lo > b else None)
            elif op == ">":
                sure = True if lo > b else (False if hi <= b else None)
            elif op == ">=":
                sure = True if lo >= b else (False if hi < b else None)
            if sure is not None:
                return sure
        if self.pos < len(self.script):
            kind, sp, taken = self.script[self.pos]
            assert kind == "d" and sp == p, (sp, p)
        else:
            taken = True
            self.discovered.append(self.pos)
        self.pos += 1
        self.path.append(("d", p, taken))
        if b is not None:
            lo, hi, ne = self._bounds(ne_)
            op = p.op if taken else p.negated().op
            if op == "==":
                lo = hi = b
            elif op == "!=":
                ne = ne | {b}
            elif op == "<":
                hi = min(hi, b - 1)
            elif op == "<=":
                hi = min(hi, b)
            elif op == ">":
                lo = max(lo, b + 1)
            elif op == ">=":
                lo = max(lo, b)
            self.known[ne_] = (lo, hi, frozenset(ne))
        return taken

    def concretize(self, s: S) -> int:
        caller = sys._getframe(2)
        line = _source_line(caller)
        if "%" in line and ('"' in line or "'" in line) and "UREG_CODES[" not in line:
            return 0  # observability text (descriptions); never structural
        return self.pin(s.e)

    def pin(self, e: tuple) -> int:
        """The number E stands for, enumerating its unpinned fields."""
        used: set = set()
        _fields_of(e, used)
        free = sorted(l for l in used if l not in self.pinned)  # noqa: E741
        if free:
            if self.pos < len(self.script):
                kind, assign = self.script[self.pos]
                assert kind == "e"
            else:
                size = 1
                for l in free:  # noqa: E741
                    size <<= FIELD_DOMAIN[l]
                if size > 4096:
                    raise Stop("enumeration of %s too large" % (free,))
                assign = tuple((l, 0) for l in free)  # noqa: E741
                self.discovered.append(self.pos)
            self.pos += 1
            self.path.append(("e", assign))
            for l, v in assign:  # noqa: E741
                self.pinned[l] = v
        return _eval_e(e, self.pinned)

    # -- effects ----------------------------------------------------------
    def emit(self, eff: tuple) -> D:
        self.effects.append(eff)
        n = self.nnodes
        self.nnodes += 1
        self.effects[-1] = (eff[0], n) + eff[1:]
        return D(n)

    def val(self, x):
        """A node for X (a D, or a static value to be materialised)."""
        if isinstance(x, D):
            return x.n
        s = _s(x) if not isinstance(x, S) else x
        return self.emit(("const", s.e)).n


CTX = Ctx()
_CMP = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def _source_line(frame) -> str:
    import linecache

    return linecache.getline(frame.f_code.co_filename, frame.f_lineno)


# ---------------------------------------------------------------------------
# The tracing register file
# ---------------------------------------------------------------------------

MODE1 = encoding.UREG_CODES["MODE1"]


class OldView:
    """The registers as they were before the instruction."""

    def get(self, code, default=None):
        return TR.read(code, "old")

    def __getitem__(self, code):
        return TR.read(code, "old")

    def __contains__(self, code):
        return True


class TraceRegs:
    def read(self, code, view):
        if isinstance(code, int) and code == MODE1:
            return Const(S(("v", "mode1")))
        if isinstance(code, int) and 48 <= code < 80:
            # L and B are baked into the lowering (`Lower::sv_lb`).
            return Const(S(("v", "%s%d" % ("L" if code < 64 else "B", code & 15))))
        c = code.e if isinstance(code, S) else ("c", code)
        d = CTX.emit(("rd", c, view))
        return Const(d)

    def get(self, code, default=None):
        return self.read(code, "cur")

    def __getitem__(self, code):
        return self.read(code, "cur")

    def __setitem__(self, code, value):
        c = code.e if isinstance(code, S) else ("c", code)
        CTX.emit(("wr", c, _node_of(value)))

    def __contains__(self, code):
        return True

    def copy(self):
        return OldView()


TR = TraceRegs()


def _node_of(v):
    """The node of a register value (a Const around a D or a static)."""
    if isinstance(v, Const):
        x = v.value
        return CTX.val(x)
    raise Stop("write of a non-constant value: %r" % (v,))


# ---------------------------------------------------------------------------
# Intrinsics: what the trace records instead of running
# ---------------------------------------------------------------------------


def _ce(code):
    return code.e if isinstance(code, S) else ("c", code)


def i_snapshot(uregs):
    return OldView()


def i_write_ureg(state, code, value):
    CTX.emit(("wr", _ce(code), _node_of(value)))


def _static_plain(address, normal_word):
    """An address known to the lowering (14a's absolute one): the kernel runs
    only addresses where a normal-word access is a plain byte address (a
    mapped one is a different address), one range test on the field."""
    v = address.value if isinstance(address, Const) else address
    if isinstance(v, D) or not normal_word:
        return
    if not isinstance(v, S):
        return
    if not (v >= 0xE8000 and v < 0x4000000):
        raise Stop("normal-word address outside the plain range")


def i_dm_read(state, address, width, signed=False, normal_word=False):
    _static_plain(address, normal_word)
    a = CTX.val(address.value if isinstance(address, Const) else address)
    d = CTX.emit(("load", a, _ce(width), bool(signed), bool(normal_word)))
    return Const(d)


def i_dm_write(state, address, width, value, normal_word=False):
    _static_plain(address, normal_word)
    a = CTX.val(address.value if isinstance(address, Const) else address)
    CTX.emit(("store", a, _ce(width), _node_of(value), bool(normal_word)))
    return True


def i_access_modifier_scale(access_width, assume_nw32, address=None):
    if access_width.startswith("short-word"):
        return 2
    if access_width in ("normal-word", "long-word"):
        if not assume_nw32:
            raise Stop("assume_nw32 off")
        if address is not None:
            a = address.value if isinstance(address, Const) else address
            _static_plain(a, True)
            CTX.emit(("plain", CTX.val(a)))
        return 4
    return 1


def i_post_modify_index(state, index, iv, delta, scale, expression):
    r = CTX.emit(
        (
            "postmod",
            _ce(index),
            CTX.val(iv.value),
            CTX.val(delta.value),
            _ce(scale),
        )
    )
    return Const(r)


class CircD(D):
    """The result of a circular MODIFY wrap. The handlers only compare it for
    an event's text (19a_scaled's `wrapped`), which the trace drops."""

    __slots__ = ()

    def __ne__(self, o):  # type: ignore[override]
        return True


def i_circular_wrap_const(index, base, length, delta):
    """`memory._circular_wrap_const`: the wrapped sum, or a refusal when the
    modifier is larger than the buffer (the handler's None). Recorded as one
    effect: the lowering knows B, L and (for a constant modifier) the delta."""
    unsigned = isinstance(delta, D)
    node = CTX.emit(
        (
            "circ",
            CTX.val(index),
            _s(base).e,
            _s(length).e,
            CTX.val(delta),
            unsigned,
        )
    )
    return CircD(node.n)


def i_modify_scale(option, assume_nw32, address=None):
    """`memory._modify_scale` (Type7a/7b MODIFY): a normal-word scaling in byte
    space is valid only for a plain RAM address, which no memory access
    checks here (unlike a load or store): a `plainmod` effect."""
    if option is None:
        return 1
    r = i_access_modifier_scale(option, assume_nw32, address)
    if address is not None and option in ("normal-word", "long-word"):
        a = address.value if isinstance(address, Const) else address
        CTX.emit(("plainmod", CTX.val(a)))
    return r


def i_advance(state, insn):
    CTX.emit(("advance",))
    return [state]


def i_event(state, insn, action, **extra):
    return None


def i_stop(state, insn, reason):
    raise Stop(reason)


def i_copy(state):
    raise Stop("fork on an unknown predicate")


class CompTok:
    """A decoded compute (or shift-immediate) result: opaque, applied later."""

    def __init__(self, kind: str, pe: str, short: bool) -> None:
        self.kind, self.pe, self.short = kind, pe, short


def _compute_field(f, short):
    if short:
        return f.get("compute")
    if "compute" in f:
        return f["compute"]
    return (f["compute[22:16]"] << 16) | f["compute[15:0]"]


def i_compute(f, short, values, special=None, approx_recips=False):
    field = _compute_field(f, short)
    if isinstance(field, S) and not CTX.decide(P("!=", field.e, ("c", 0))):
        return None
    return CompTok("compute", "x", bool(short))


def i_compute_pey(f, short, values, special=None, approx_recips=False):
    field = _compute_field(f, short)
    if isinstance(field, S) and not CTX.decide(P("!=", field.e, ("c", 0))):
        return None
    return CompTok("compute", "y", bool(short))


def i_shift_immediate(f, values, special=None):
    return CompTok("shiftimm", "x", False)


def i_shift_immediate_pey(f, values, special=None):
    return CompTok("shiftimm", "y", False)


def i_apply_compute(state, insn, result):
    CTX.emit(("apply", result.kind, result.pe, result.short))


def i_ureg_raw(values, code):
    return values.get(code)


FIELD_DOMAIN: dict[str, int] = {}  # label -> bit width, set by trace_form


def _fields_of(e: tuple, out: set) -> None:
    if e[0] == "f":
        out.add(e[1])
    elif e[0] in ("c", "v"):
        pass
    else:
        for x in e[1:]:
            _fields_of(x, out)


def _eval_e(e: tuple, env: dict) -> int:
    k = e[0]
    if k == "v":
        raise Stop("run-time symbol %s in a number" % e[1])
    if k == "c":
        return e[1]
    if k == "f":
        return env[e[1]]
    if k == "~":
        return ~_eval_e(e[1], env)
    return _EVAL[k](_eval_e(e[1], env), _eval_e(e[2], env))


def _has_var(e: tuple) -> bool:
    if e[0] == "v":
        return True
    if e[0] in ("c", "f"):
        return False
    return any(_has_var(x) for x in e[1:])


def lift_pure(fn, x):
    """Apply the pure integer function FN to a static symbol X by cases.

    FN is evaluated over every value X takes (its fields' whole domain);
    maximal runs of consecutive values where FN gives None, one constant, or
    X + k become decisions on X (``X <= hi``), so each leaf has a static
    expression (or None). Used for the register-class tables (_cureg_code,
    ...) that the semantics index with a register field.
    """
    if not isinstance(x, S):
        return fn(x)
    used: set = set()
    _fields_of(x.e, used)
    free = sorted(l for l in used if l not in CTX.pinned)  # noqa: E741
    if not free:
        return fn(_eval_e(x.e, CTX.pinned))
    size = 1
    for l in free:  # noqa: E741
        size <<= FIELD_DOMAIN[l]
    if size > 1 << 16:
        raise Stop("lift over %s too large" % (free,))
    values = set()
    for combo in itertools.product(*[range(1 << FIELD_DOMAIN[l]) for l in free]):  # noqa: E741
        env = dict(CTX.pinned)
        env.update(zip(free, combo, strict=False))
        values.add(_eval_e(x.e, env))
    xs = sorted(values)
    results = [fn(xv) for xv in xs]

    def fits(pat, xv, r):
        kind, k = pat
        if kind == "none":
            return r is None
        if r is None:
            return False
        if kind == "const":
            return r == k
        if kind == "aff":
            return r == xv + k
        return r == xv ^ k  # "xor"

    def pattern(xv, r):
        if r is None:
            return ("none", 0)
        if r == xv:
            return ("aff", 0)
        if r - xv in (80, -80, 1, -1, 16, -16):
            return ("aff", r - xv)
        if r ^ xv in (1,):
            return ("xor", r ^ xv)
        return ("const", r)

    runs: list = []
    i = 0
    while i < len(xs):
        # The longest run from i that one pattern describes.
        best = None
        for pat in {
            pattern(xs[i], results[i]),
            ("xor", 1)
            if results[i] is not None and results[i] ^ xs[i] == 1
            else ("aff", 0),
        }:
            j = i
            while j < len(xs) and fits(pat, xs[j], results[j]):
                j += 1
            if j > i and (best is None or j > best[1]):
                best = (pat, j)
        pat, j = best
        runs.append([xs[i], xs[j - 1], pat])
        i = j
    for _lo, hi, o in runs[:-1]:
        if CTX.decide(P("<=", x.e, ("c", hi))):
            return _lift_result(o, x)
    return _lift_result(runs[-1][2], x)


def _lift_result(o, x):
    kind, k = o
    if kind == "none":
        return None
    if kind == "const":
        return k
    if kind == "xor":
        return x ^ k
    return x + k if k else x


def i_predicate_pe(state, cond, pe):
    return i_predicate(state, cond, pe)


def i_predicate(state, cond, pe="x"):
    if not isinstance(cond, S):
        if cond == 0x1F:
            return True
        cond = _s(cond)
    if CTX.decide(P("==", cond.e, ("c", 0x1F))):
        return True
    CTX.emit(("cond", cond.e, pe))
    return True


def _pure(fn):
    return lambda code: lift_pure(fn, code)


def i_concrete_address(address):
    v = address.value if isinstance(address, Const) else address
    if isinstance(v, D):
        return None
    return v


def i_read_px48(state, address):
    raise Stop("combined PX transfer")


PATCHES = {
    "_read_px48": i_read_px48,
    "_concrete_address": i_concrete_address,
    "_predicate": i_predicate,
    "_predicate_pe": i_predicate_pe,
    "_cureg_code": _pure(st._cureg_code),
    "_lw_pair_mate": _pure(st._lw_pair_mate),
    "_render": lambda value: "<expr>",
    "_json_value": lambda value: 0,
    "_ureg_raw": i_ureg_raw,
    "_snapshot_uregs": i_snapshot,
    "_write_ureg": i_write_ureg,
    "_dm_read": i_dm_read,
    "_dm_write": i_dm_write,
    "_access_modifier_scale": i_access_modifier_scale,
    "_post_modify_index": i_post_modify_index,
    "_circular_wrap_const": i_circular_wrap_const,
    "_modify_scale": i_modify_scale,
    "_advance": i_advance,
    "_event": i_event,
    "_stop": i_stop,
    "_copy": i_copy,
    "_compute": i_compute,
    "_compute_pey": i_compute_pey,
    "_shift_immediate": i_shift_immediate,
    "_shift_immediate_pey": i_shift_immediate_pey,
    "_apply_compute": i_apply_compute,
    "_apply_compute_pey": i_apply_compute,
}


class LenientNames(list):
    """UREG_NAMES for descriptions: a symbolic code names itself."""

    def __getitem__(self, i):
        if isinstance(i, S):
            return "<reg>"
        return list.__getitem__(self, i)


_INSTALL_DEPTH = 0


@contextlib.contextmanager
def installed():
    """Patch every sharc_core submodule for the symbolic trace; restore on
    exit. Re-entrant: only the outermost entry patches and restores."""
    global _INSTALL_DEPTH
    import importlib
    import pkgutil

    _INSTALL_DEPTH += 1
    saved: list = []
    try:
        if _INSTALL_DEPTH == 1:
            names = LenientNames(encoding.UREG_NAMES)

            def put(mod, name, value):
                saved.append((mod, name, hasattr(mod, name), getattr(mod, name, None)))
                setattr(mod, name, value)

            for m in pkgutil.iter_modules(sharc_core.__path__):
                mod = importlib.import_module("tools.sharc_dev.sharc_core." + m.name)
                for name, fn in PATCHES.items():
                    if hasattr(mod, name):
                        put(mod, name, fn)
                if hasattr(mod, "UREG_NAMES"):
                    put(mod, "UREG_NAMES", names)
        yield
    finally:
        _INSTALL_DEPTH -= 1
        for mod, name, existed, old in reversed(saved):
            if existed:
                setattr(mod, name, old)
            else:
                delattr(mod, name)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class SymFields(dict):
    """Instruction fields as static symbols."""

    def __init__(self, form_fields):
        super().__init__()
        for label, _hi, _lo in form_fields:
            self[label] = S(("f", label))


def form_fields(type_name: str) -> list:
    import json

    d = json.load(open(os.path.join(HERE, "sharcspec", "decode_table.json")))  # noqa: SIM115
    for f in d["forms"]:
        if f["name"] == type_name:
            return [(x["label"], x["hi"], x["lo"]) for x in f["fields"]]
    raise KeyError(type_name)


@dataclass
class FakeInsn:
    kind: str = "confident"
    length_bytes: int = 4
    type_name: str = ""
    fields: dict = field(default_factory=dict)
    note: str = ""


def trace_form(name: str, type_name: str):
    """All leaves of one form."""
    with installed():
        ff = form_fields(type_name)
        for label, hi, lo in ff:
            FIELD_DOMAIN[label] = hi - lo + 1
        fields = SymFields(ff)
        handler = forms.FORMS[name]
        leaves: list[Leaf] = []
        work = [[]]
        while work:
            script = work.pop()
            CTX.reset(script)
            state = State(pc_sw=0)
            state.uregs = TR  # type: ignore[assignment]
            state.assume_nw32 = True
            state.approx_recips = False
            state.special = {}
            state.trace = [{}]
            # memory is the kernel's
            state.concrete = object()  # type: ignore[assignment]
            insn = FakeInsn(type_name=name, fields=fields)
            refused = None
            try:
                handler(state, insn, fields, name)
            except Stop as e:
                refused = str(e)
            except ValueError as e:
                refused = "ValueError: " + str(e)
            leaves.append(Leaf(list(CTX.path), list(CTX.effects), refused))
            for pos in CTX.discovered:
                entry = CTX.path[pos]
                if entry[0] == "d":
                    work.append(CTX.path[:pos] + [("d", entry[1], False)])
                else:
                    labels = [l for l, _ in entry[1]]  # noqa: E741
                    for combo in itertools.product(
                        *[range(1 << FIELD_DOMAIN[l]) for l in labels]  # noqa: E741
                    ):
                        if combo == tuple(0 for _ in labels):
                            continue
                        work.append(
                            CTX.path[:pos]
                            + [("e", tuple(zip(labels, combo, strict=False)))]
                        )
        return leaves


def trace_call(fn, labels: dict[str, int], pre=None):
    """All leaves of FN(state): a state with the tracing registers; LABELS
    maps static field symbols to bit widths. FN returns a value (kept in
    ``Leaf.result``) or raises Stop."""
    with installed():
        for label, width in labels.items():
            FIELD_DOMAIN[label] = width
        leaves: list[Leaf] = []
        work = [[]]
        while work:
            script = work.pop()
            CTX.reset(script)
            refused = None
            result = None
            try:
                result = fn()
            except Stop as e:
                refused = str(e)
            except ValueError as e:
                refused = "ValueError: " + str(e)
            lf = Leaf(list(CTX.path), list(CTX.effects), refused)
            lf.result = result  # type: ignore[attr-defined]
            leaves.append(lf)
            for pos in CTX.discovered:
                entry = CTX.path[pos]
                if entry[0] == "d":
                    work.append(CTX.path[:pos] + [("d", entry[1], False)])
                else:
                    labs = [l for l, _ in entry[1]]  # noqa: E741
                    for combo in itertools.product(
                        *[range(1 << FIELD_DOMAIN[l]) for l in labs]  # noqa: E741
                    ):
                        if combo == tuple(0 for _ in labs):
                            continue
                        work.append(
                            CTX.path[:pos]
                            + [("e", tuple(zip(labs, combo, strict=False)))]
                        )
        return leaves
