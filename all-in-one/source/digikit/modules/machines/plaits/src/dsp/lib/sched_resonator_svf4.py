"""The search that found gen_resonator_svf4.py's modulo schedule: z3 places the 26 compute operations of one sample
(two SIMD filter pairs and the serial s_out sum) and the transfers into II instructions, with the register allocation
and the instruction form of every slot, under the constraints that keep the result inside pk_encoding_gate and
Selache's working subset:
  - one ALU operation per instruction; a multiply only beside a multifunction ALU operation (operands R0-3 x R4-7 and
    R8-11 +- R12-15), its result register one that stock 1.17 code uses for that form and operation (RM below, from
    out/sharcdb/dt2-1.17.sqlite, real code only);
  - one transfer per instruction: coefficient loads as Type 4a (immediate offset, no update), the s_in load (M4) as
    Type 3a, the s_out store as Type 3a; `Rn = M4` and the lane swaps `Rn = Sn` only beside a single-function compute
    (Type 5a: Selache mis-encodes them beside a multifunction one);
  - dependencies with one instruction of latency, loop-carried states, register lifetimes that never overlap modulo II.
Not a project dependency; run it in a throwaway environment (about 10 minutes for II = 16 on a laptop):

    uv run --no-project --with z3-solver python modules/machines/plaits/src/dsp/lib/sched_resonator_svf4.py 16

It prints the schedule and the registers; paste them into SCHEDULE and REG of gen_resonator_svf4.py and run the kernel
test (tests/plaits/test_resonator_kernel.py), which is the proof: this model is only the search."""

import json
import sys

import z3

II = int(sys.argv[1]) if len(sys.argv) > 1 else 16
SPLIT_G = "--split-g" in sys.argv  # load g twice (once per use) instead of keeping it

ADD, SUB = "add", "sub"
RM = {  # (form, op) -> allowed multiplier result registers (real stock code)
    ("2a", ADD): {0, 1, 2, 7, 10, 15},
    ("2a", SUB): {2, 12, 15},
    ("3ald", ADD): {2, 8, 10, 11, 14},
    ("3ald", SUB): {1, 2, 9, 15},
    ("3ast", ADD): {0, 2, 3, 8, 10, 11},
    ("3ast", SUB): {0, 1, 8},
    ("4ald", ADD): {0, 1, 6, 8, 9, 11, 12, 13},
    ("4ald", SUB): {1, 2, 6, 8},
    ("4ast", ADD): {0, 2, 3, 5, 8, 11},
    ("4ast", SUB): {0, 2, 9, 12, 15},
}
FORMS = ["2a", "3ald", "3ast", "4ald", "4ast", "5a"]
X, Y = range(8, 12), range(12, 16)
ALL = range(8, 16)

# ALU ops: name -> (kind C/S, op, dest value, operands)
alu = {
    "a1A": ("C", SUB, "a1A", ["sin", "m1A"]),
    "a2A": ("C", SUB, "TA", ["a1A", "s2A"]),
    "a1B": ("C", SUB, "a1B", ["sin", "m1B"]),
    "a2B": ("C", SUB, "TB", ["a1B", "s2B"]),
    "bpA": ("S", ADD, "TA", ["m2A", "s1A"]),
    "s1A": ("S", ADD, "s1A", ["m2A", "TA"]),
    "bpB": ("S", ADD, "TB", ["m2B", "s1B"]),
    "s1B": ("S", ADD, "s1B", ["m2B", "TB"]),
    "lpA": ("C", ADD, "lpA", ["m3A", "s2A"]),
    "s2A": ("C", ADD, "s2A", ["m3A", "lpA"]),
    "lpB": ("C", ADD, "lpB", ["m3B", "s2B"]),
    "s2B": ("C", ADD, "s2B", ["m3B", "lpB"]),
    "c1": ("S", ADD, "c1", ["Z", "pA"]),
    "c2": ("C", ADD, "c2", ["c1", "pA"]),
    "c3": ("S", ADD, "c3", ["c2", "pB"]),
    "c4": ("C", ADD, "c4", ["c3", "pB"]),
}
mul = ["m1A", "hpA", "m2A", "m3A", "pA", "m1B", "hpB", "m2B", "m3B", "pB"]
moves = ["bcast", "swA", "swB", "ldM4", "stc4"]
coefs = ["rpgA", "hA", "gA", "gainA", "rpgB", "hB", "gB", "gainB"]
if SPLIT_G:
    coefs += ["gA2", "gB2"]
coef_use = {
    "rpgA": ["m1A"],
    "hA": ["hpA"],
    "gA": ["m2A", "m3A"],
    "gainA": ["pA"],
    "rpgB": ["m1B"],
    "hB": ["hpB"],
    "gB": ["m2B", "m3B"],
    "gainB": ["pB"],
}
if SPLIT_G:
    coef_use["gA"], coef_use["gA2"] = ["m2A"], ["m3A"]
    coef_use["gB"], coef_use["gB2"] = ["m2B"], ["m3B"]

s = z3.Solver()
NAMES = list(alu) + mul + moves + [f"ld_{c}" for c in coefs]
slot = {n: z3.Int(f"k_{n}") for n in NAMES}
stage = {n: z3.Int(f"g_{n}") for n in NAMES}
T = {}
for n in NAMES:
    s.add(slot[n] >= 0, slot[n] < II, stage[n] >= 0, stage[n] <= 2)
    T[n] = stage[n] * II + slot[n]
s.add(slot["a1A"] == 0, stage["a1A"] == 1)


def dep(
    a, b, lat=1, dist=0
):  # b at least lat after a; b may belong to the next sample (dist)
    s.add(T[b] + dist * II >= T[a] + lat)


# data dependencies (same sample)
for a, b in [
    ("m1A", "a1A"),
    ("a1A", "a2A"),
    ("a2A", "hpA"),
    ("hpA", "m2A"),
    ("m2A", "bpA"),
    ("bpA", "s1A"),
    ("bpA", "m3A"),
    ("bpA", "pA"),
    ("m3A", "lpA"),
    ("lpA", "s2A"),
    ("m2A", "s1A"),
    ("m1B", "a1B"),
    ("a1B", "a2B"),
    ("a2B", "hpB"),
    ("hpB", "m2B"),
    ("m2B", "bpB"),
    ("bpB", "s1B"),
    ("bpB", "m3B"),
    ("bpB", "pB"),
    ("m3B", "lpB"),
    ("lpB", "s2B"),
    ("m2B", "s1B"),
    ("pA", "c1"),
    ("c1", "c2"),
    ("c2", "c3"),
    ("pB", "c3"),
    ("c3", "c4"),
    ("c4", "stc4"),
    ("bcast", "a1A"),
    ("bcast", "a1B"),
    ("ldM4", "bcast"),
]:
    dep(a, b)
# swaps between c1 (may share) and c2
s.add(T["swA"] >= T["c1"], T["swA"] < T["c2"], T["swB"] >= T["c3"], T["swB"] < T["c4"])
# loop-carried
for a, b in [("s1A", "m1A"), ("s2A", "a2A"), ("s1B", "m1B"), ("s2B", "a2B")]:
    dep(a, b, 1, 1)
# old values read before being overwritten (anti deps, same instruction allowed)
for a, b in [
    ("s1A", "a2A"),
    ("m3A", "a2A"),
    ("pA", "a2A"),
    ("s1B", "a2B"),
    ("m3B", "a2B"),
    ("pB", "a2B"),
    ("a2A", "s2A"),
    ("lpA", "s2A"),
]:
    dep(a, b, 0, 1 if b in ("a2A", "a2B") else 0)
s.add(T["a1A"] + II >= T["bcast"])  # next bcast after this sample's last sin use
s.add(T["a1B"] <= T["bcast"] + II)
s.add(T["ldM4"] + 0 >= T["bcast"] - II)  # next M4 load after this bcast read
s.add(T["stc4"] - T["c4"] <= II)
for c in coefs:
    for u in coef_use[c]:
        dep(f"ld_{c}", u)
        s.add(T[u] - T[f"ld_{c}"] <= II)

# slots: one ALU op each
for k in range(II):
    s.add(z3.PbEq([(slot[a] == k, 1) for a in alu], 1))
    s.add(z3.PbLe([(slot[m] == k, 1) for m in mul], 1))
# form per slot
form = {k: z3.Int(f"form_{k}") for k in range(II)}  # index into FORMS
isC = {
    k: z3.Or([z3.And(slot[a] == k) for a in alu if alu[a][0] == "C"]) for k in range(II)
}
isAdd = {k: z3.Or([slot[a] == k for a in alu if alu[a][1] == ADD]) for k in range(II)}
for k in range(II):
    s.add(form[k] >= 0, form[k] < len(FORMS))
    s.add(z3.Implies(form[k] == FORMS.index("5a"), z3.Not(isC[k])))
for m in mul:  # mult only in constrained slots
    for k in range(II):
        s.add(z3.Implies(slot[m] == k, isC[k]))
F = FORMS.index
for mv in ("bcast", "swA", "swB"):
    for k in range(II):
        s.add(z3.Implies(slot[mv] == k, form[k] == F("5a")))
for k in range(II):
    s.add(z3.Implies(slot["ldM4"] == k, form[k] == F("3ald")))
    s.add(z3.Implies(slot["stc4"] == k, form[k] == F("3ast")))
    for c in coefs:
        s.add(z3.Implies(slot[f"ld_{c}"] == k, form[k] == F("4ald")))
# one move per slot
mvs = moves + [f"ld_{c}" for c in coefs]
for k in range(II):
    s.add(z3.PbLe([(slot[m] == k, 1) for m in mvs], 1))
    # a slot whose form is not 2a has a move
    s.add(z3.Implies(form[k] != F("2a"), z3.Or([slot[m] == k for m in mvs])))

# registers
R = {}


def reg(v, dom):
    R[v] = z3.Int(f"r_{v}")
    s.add(z3.Or([R[v] == r for r in dom]))


for v in ["sin", "a1A", "a1B", "m3A", "m3B"]:
    reg(v, X)
for v in ["m1A", "m1B", "s2A", "s2B", "lpA", "lpB"]:
    reg(v, Y)
for v in ["m2A", "m2B", "pA", "pB", "c1", "c2", "c3", "c4"]:
    reg(v, ALL)
reg("TA", [0, 1, 2, 3])
reg("TB", [0, 1, 2, 3])
reg("s1A", [0, 1, 2, 3])
reg("s1B", [0, 1, 2, 3])
s.add(z3.Distinct(R["TA"], R["TB"], R["s1A"], R["s1B"]))
reg("Z", [4, 5, 6, 7])
for c in coefs:
    reg(c, [4, 5, 6, 7])


def grp(v):
    return z3.If(R[v] >= 12, 1, 0)


s.add(grp("c1") != grp("pA"), grp("c3") != grp("pB"))
# Rm constraints
for m in mul:
    dest = {"hpA": "TA", "hpB": "TB"}.get(m, m)
    for k in range(II):
        for fi, fn in enumerate(FORMS):
            if fn == "5a":
                continue
            for op in (ADD, SUB):
                cond = z3.And(
                    slot[m] == k,
                    form[k] == fi,
                    isAdd[k] if op == ADD else z3.Not(isAdd[k]),
                )
                s.add(z3.Implies(cond, z3.Or([R[dest] == r for r in RM[(fn, op)]])))
# lifetimes: (value, def time, last-use time); def time = when written
uses = {
    "sin": ["a1A", "a1B"],
    "a1A": ["a2A"],
    "a1B": ["a2B"],
    "m1A": ["a1A"],
    "m1B": ["a1B"],
    "m2A": ["bpA", "s1A"],
    "m2B": ["bpB", "s1B"],
    "m3A": ["lpA", "s2A"],
    "m3B": ["lpB", "s2B"],
    "lpA": ["s2A"],
    "lpB": ["s2B"],
    "pA": ["c1", "c2"],
    "pB": ["c3", "c4"],
    "c1": ["c2"],
    "c2": ["c3"],
    "c3": ["c4"],
    "c4": ["stc4"],
}
defs = {
    "sin": "bcast",
    "a1A": "a1A",
    "a1B": "a1B",
    "m1A": "m1A",
    "m1B": "m1B",
    "m2A": "m2A",
    "m2B": "m2B",
    "m3A": "m3A",
    "m3B": "m3B",
    "lpA": "lpA",
    "lpB": "lpB",
    "pA": "pA",
    "pB": "pB",
    "c1": "c1",
    "c2": "c2",
    "c3": "c3",
    "c4": "c4",
}
ivals = []  # (value, start, end) absolute
for v, d in defs.items():
    end = z3.Int(f"end_{v}")
    s.add(z3.And([end >= T[u] for u in uses[v]]))
    s.add(z3.Or([end == T[u] for u in uses[v]]))
    s.add(end - T[d] <= II)
    ivals.append((v, T[d], end))
for c in coefs:
    end = z3.Int(f"end_{c}")
    s.add(
        z3.And([end >= T[u] for u in coef_use[c]]),
        z3.Or([end == T[u] for u in coef_use[c]]),
    )
    ivals.append((c, T[f"ld_{c}"], end))
# permanent registers
perm = ["s2A", "s2B", "Z"]
for v in perm:
    for w, _, _ in ivals:
        s.add(R[v] != R[w])
s.add(z3.Distinct([R[v] for v in perm]))


def overlap(a0, a1, b0, b1):
    # cyclic intervals (len <= II): [a0, a1] and [b0, b1] conflict unless one ends at/before the other starts,
    # under every shift by II
    cs = []
    for sh in [II * j for j in range(-3, 4)]:
        cs.append(z3.And(a0 < b1 + sh, b0 + sh < a1))
    return z3.Or(cs)


for i in range(len(ivals)):
    for j in range(i + 1, len(ivals)):
        v, a0, a1 = ivals[i]
        w, b0, b1 = ivals[j]
        s.add(z3.Implies(R[v] == R[w], z3.Not(overlap(a0, a1, b0, b1))))

r = s.check()
print(r)
if r != z3.sat:
    sys.exit(1)
M = s.model()
t = {n: M.eval(v).as_long() for n, v in T.items()}
reg_of = {v: M.eval(x).as_long() for v, x in R.items()}
fo = {k: FORMS[M.eval(form[k]).as_long()] for k in range(II)}
rows = {k: [] for k in range(II)}
for n, tt in sorted(t.items(), key=lambda x: x[1]):
    rows[tt % II].append(f"{n}@{tt}")
for k in range(II):
    print(f"{k:2} {fo[k]:5} {' '.join(rows[k])}")
print({v: f"R{r}" for v, r in sorted(reg_of.items(), key=lambda x: x[1])})
print(json.dumps({"t": t, "reg": reg_of}))
