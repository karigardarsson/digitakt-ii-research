#!/usr/bin/env python3
"""Hand-assembled test programs for the generated lowering (no firmware).

Encodes instructions from tools/sharc_dev/sharcspec/decode_table.json (the public ISA
field layout) and writes packages/emulator/sharc/src/tests_gen_programs.rs: for each
program a list of 16-bit parcels. The SIMD loop below has the shape of the
MODE1/PEYEN-toggling loops the Digitone II firmware runs (a SIMD enable, a DO
loop of pointer moves, loads, float adds and stores with SIMD companions, a
SIMD disable), written here from the ISA, not copied.

    uv run python -m tools.sharc_dev.sharc_lowergen_programs --out packages/emulator/sharc/src/tests_gen_programs.rs
"""

from __future__ import annotations

import argparse
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))


def table() -> dict:
    d = json.load(open(os.path.join(HERE, "sharcspec", "decode_table.json")))  # noqa: SIM115
    return {re.sub(r"\s*\((\w+)\)", r"_\1", f["name"][4:]): f for f in d["forms"]}


TAB = table()


def enc(form: str, **fields) -> list[int]:
    f = TAB[form]
    word = int(f["value"], 16)
    labels = {x["label"]: x for x in f["fields"]}
    stems: dict[str, list[str]] = {}
    for label in labels:
        stems.setdefault(label.split("[")[0], []).append(label)
    for key, v in fields.items():
        if key in labels:
            x = labels[key]
            word |= (v & ((1 << (x["hi"] - x["lo"] + 1)) - 1)) << x["lo"]
        else:
            # A whole field given by its stem (compute, data, addr, reladdr...):
            # split over the labelled pieces, most significant first.
            pieces = sorted((labels[l] for l in stems[key]), key=lambda x: -x["lo"])  # noqa: E741
            width = sum(x["hi"] - x["lo"] + 1 for x in pieces)
            shift = width
            for x in pieces:
                w = x["hi"] - x["lo"] + 1
                shift -= w
                word |= ((v >> shift) & ((1 << w) - 1)) << x["lo"]
    n = f["width"] // 16
    ps = [(word >> 32) & 0xFFFF, (word >> 16) & 0xFFFF, word & 0xFFFF]
    return ps[:n]


def compute(op: int, rn: int, rx: int, ry: int, cu: int = 0) -> int:
    return (cu << 20) | (op << 12) | (rn << 8) | (rx << 4) | ry


FADD = 0x81


# UREG codes
def R(n):
    return n


def I(n):  # noqa: E743
    return 16 + n


def M(n):
    return 32 + n


def simd_loop(trips: int = 16) -> list[int]:
    """SIMD on; I4 = I13, I2 = I12; DO trips { I0 = I4 + 8; I1 = I2 + 8;
    R1 = DM(I0 + 30); R0 = DM(I2, M5); R12 = DM(I4, 2) (post);
    F12 = F0 + F12 || R0 = DM(I1 + 30); F1 = F0 + F1 || DM(I2, 2) = R12 (post);
    DM(I1 + 30) = R1 }; SIMD off."""
    body = []
    body += enc(
        "19a", g=0, **{"is[2:0]": 4, "idis[2:0]": 4}, data=8
    )  # I0 = modify(I4, 8)
    body += enc(
        "19a", g=0, **{"is[2:0]": 2, "idis[2:0]": 3}, data=8
    )  # I1 = modify(I2, 8)
    body += enc(
        "15a", d=0, g=0, l=0, **{"i[2:0]": 0}, ureg=R(1), addr=30
    )  # R1 = DM(I0 + 30)
    body += enc(
        "3a", cond=0x1F, d=0, g=0, i=2, l=0, m=5, u=1, ureg=R(0), compute=0
    )  # R0 = DM(I2, M5)
    body += enc(
        "4a", cond=0x1F, d=0, g=0, **{"i[2:0]": 4}, u=1, dreg=12, data=2, compute=0
    )  # R12 = DM(I4, 2)
    body += enc(
        "4a",
        cond=0x1F,
        d=0,
        g=0,
        **{"i[2:0]": 1},
        u=0,
        dreg=0,
        data=30,
        compute=compute(FADD, 12, 0, 12),
    )
    body += enc(
        "4a",
        cond=0x1F,
        d=1,
        g=0,
        **{"i[2:0]": 2},
        u=1,
        dreg=12,
        data=2,
        compute=compute(FADD, 1, 0, 1),
    )
    body += enc(
        "15a", d=1, g=0, l=0, **{"i[2:0]": 1}, ureg=R(1), addr=30
    )  # DM(I1 + 30) = R1
    prog = []
    prog += enc("18a", bop=0, sreg=2, data=0x200000)  # MODE1 = set(MODE1, PEYEN)
    prog += enc(
        "5b_move",
        cond=0x1F,
        dstureg=I(4),
        srcureghigh=I(13) >> 2,
        **{"srcureglow[1:1]": (I(13) >> 1) & 1, "srcureglow[0:0]": I(13) & 1},
    )
    prog += enc(
        "5b_move",
        cond=0x1F,
        dstureg=I(2),
        srcureghigh=I(12) >> 2,
        **{"srcureglow[1:1]": (I(12) >> 1) & 1, "srcureglow[0:0]": I(12) & 1},
    )
    body_words = len(body)
    # DO (to the last instruction of the body) UNTIL LCE: the end address is
    # relative to the DO; the last body instruction starts at do_at + 3 + body_words - 3.
    rel = 3 + body_words - 3
    prog += enc("12a_imm", mode=1, data=trips, reladdr=rel)
    prog += body
    prog += enc("18a", bop=1, sreg=2, data=0x200000)  # MODE1 = clear(MODE1, PEYEN)
    return prog


def move(dst: int, src: int) -> list[int]:
    return enc(
        "5b_move",
        cond=0x1F,
        dstureg=dst,
        srcureghigh=src >> 2,
        **{"srcureglow[1:1]": (src >> 1) & 1, "srcureglow[0:0]": src & 1},
    )


def circ_loop(trips: int = 16) -> list[int]:
    """DO trips { R0 = DM(I2, M5); R1 = DM(I2, 2) [post-modify]; I3 = MODIFY(I2,
    M5); I4 = MODIFY(I2, 3) (nw); I5 = MODIFY(I2, 8) (sw); I2 = I12 (below the
    buffer); R2 = DM(I2, M5); I2 = I13 (above it); R3 = DM(I2, 2) }.
    Run with L2 and B2 set (program_engine, name "circ_*"): I2 is also written
    plainly, so its circular updates and MODIFYs carry no in-buffer
    assumption (the plain-write path of the lowering)."""
    body = []
    body += enc("3a", cond=0x1F, d=0, g=0, i=2, l=0, m=5, u=1, ureg=R(0), compute=0)
    body += enc(
        "4a", cond=0x1F, d=0, g=0, **{"i[2:0]": 2}, u=1, dreg=1, data=2, compute=0
    )
    body += enc(
        "7b", cond=0x1F, g=0, **{"is[2:2]": 0, "is[1:0]": 2}, idis=1, m=5
    )  # I3 = MODIFY(I2, M5)
    body += enc(
        "19a_scaled", g=0, **{"is": 2, "idis": 6}, w=1, data=3
    )  # I4 = MODIFY(I2, 3) (nw)
    body += enc(
        "19a_scaled", g=0, **{"is": 2, "idis": 7}, w=0, data=8
    )  # I5 = MODIFY(I2, 8) (sw)
    body += move(I(2), I(12))
    body += enc("3a", cond=0x1F, d=0, g=0, i=2, l=0, m=5, u=1, ureg=R(2), compute=0)
    body += move(I(2), I(13))
    body += enc(
        "4a", cond=0x1F, d=0, g=0, **{"i[2:0]": 2}, u=1, dreg=3, data=2, compute=0
    )
    rel = 3 + len(body) - 3
    prog = enc("12a_imm", mode=1, data=trips, reladdr=rel)
    return prog + body


PROGRAMS = {"simd_loop": simd_loop, "circ_loop": circ_loop}


# -- control-flow programs (lane F3) ---------------------------------------------
#
# Calls, returns and conditional transfers with a compute, as short programs for
# `gen_control_programs_match_the_interpreter`: each runs on the interpreter and
# on the fast tier with regions at the entries listed (the function bodies and
# the instructions after the calls), 400 steps at several chunk sizes, and the
# whole machine state (stacks, pending transfer, registers, memory) must agree.
# Written from the ISA.


class Asm:
    """Two-pass assembler over `enc`: labels in short words, relative fields
    given as ("rel", label, bits)."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str, dict]] = []
        self.labels: dict[str, int] = {}
        self.pc = 0

    def label(self, name: str) -> None:
        self.labels[name] = self.pc

    def raw(self, *parcels: int) -> None:
        """Parcels as they are (an encoding `enc` does not make)."""
        self.items.append(("raw", self.pc, {"parcels": parcels}))
        self.pc += len(parcels)

    def ins(self, form: str, **fields) -> None:
        self.items.append((form, self.pc, fields))
        self.pc += TAB[form]["width"] // 16

    def link(self) -> list[int]:
        out: list[int] = []
        for form, pc, fields in self.items:
            if form == "raw":
                out += fields["parcels"]
                continue
            f = {}
            for k, v in fields.items():
                if isinstance(v, tuple) and v[0] == "rel":
                    v = self.labels[v[1]] - pc
                elif isinstance(v, tuple) and v[0] == "abs":
                    v = self.labels[v[1]]
                f[k] = v
            out += enc(form, **f)
        return out


ADD = 0x01


def finish(a: Asm, entries: list[str], cfgs: int) -> tuple[list[int], list[int], int]:
    """(parcels, region entries, configurations: 1 both, 2 the stack model only)."""
    code = a.link()
    return code, [0] + [a.labels[e] for e in entries], cfgs


def set_r(a: Asm, n: int, v: int) -> None:
    a.ins("17b", ureg=R(n), data=v)


def add(a: Asm, rn: int, rx: int, ry: int) -> None:
    a.ins("2a", cond=0x1F, compute=compute(ADD, rn, rx, ry))


def call(a: Asm, label: str, delayed: bool, cond: int = 0x1F) -> None:
    a.ins("8a_rel", b=1, a=0, cond=cond, j=int(delayed), ci=0, reladdr=("rel", label))


def jump_abs(a: Asm, label: str) -> None:
    a.ins("8a_abs", b=0, a=0, cond=0x1F, j=0, ci=0, addr=("abs", label))


def rts(a: Asm, delayed: bool, cond: int = 0x1F) -> None:
    a.ins("11c", x=0, j=int(delayed), cond=cond, lr=0)


def rts_compute(
    a: Asm, delayed: bool, cond: int, e: int, rn: int, rx: int, ry: int
) -> None:
    a.ins(
        "11a",
        x=0,
        cond=cond,
        j=int(delayed),
        e=e,
        lr=0,
        compute=compute(ADD, rn, rx, ry),
    )


def jump_compute(
    a: Asm, label: str, delayed: bool, cond: int, e: int, rn: int, rx: int, ry: int
) -> None:
    a.ins(
        "9a_rel",
        b=0,
        a=0,
        cond=cond,
        j=int(delayed),
        e=e,
        ci=0,
        reladdr=("rel", label),
        compute=compute(ADD, rn, rx, ry),
    )


NE, EQ = 0x10, 0x00


def ctl_call_ret() -> tuple[list[int], list[int], int]:
    """A delayed call and a delayed return, each with two slots that write."""
    a = Asm()
    jump_abs(a, "main")
    a.label("f")
    set_r(a, 9, 17)
    a.raw(0x0093)  # IDLE (22c), as the firmware encodes it
    add(a, 10, 9, 3)
    rts(a, True)
    set_r(a, 11, 19)
    set_r(a, 12, 21)
    a.label("main")
    set_r(a, 3, 7)
    set_r(a, 4, 9)
    call(a, "f", True)
    set_r(a, 5, 11)
    set_r(a, 6, 13)
    a.label("ret")
    set_r(a, 7, 15)
    add(a, 8, 7, 3)
    return finish(a, ["f", "ret", "main"], 1)


def ctl_call_plain() -> tuple[list[int], list[int], int]:
    """Plain calls and returns, a return that is taken and one that is not."""
    a = Asm()
    jump_abs(a, "main")
    a.label("f1")
    set_r(a, 9, 1)
    rts(a, False, NE)
    set_r(a, 10, 2)
    a.label("f2")
    set_r(a, 12, 4)
    rts(a, False, EQ)
    set_r(a, 13, 5)
    rts(a, False)
    a.label("main")
    set_r(a, 3, 7)
    call(a, "f1", False)
    a.label("r1")
    set_r(a, 1, 100)
    call(a, "f2", False)
    a.label("r2")
    set_r(a, 2, 200)
    call(a, "f1", True, NE)
    set_r(a, 14, 1)
    set_r(a, 15, 2)
    a.label("r3")
    set_r(a, 0, 300)
    return finish(a, ["f1", "f2", "r1", "r2", "r3", "main"], 1)


def ctl_jump_compute() -> tuple[list[int], list[int], int]:
    """Conditional jumps with a compute: taken, not taken, ELSE, delayed."""
    a = Asm()
    set_r(a, 1, 3)
    set_r(a, 2, 4)
    jump_compute(a, "t1", True, NE, 0, 3, 1, 2)
    set_r(a, 4, 1)
    set_r(a, 5, 2)
    set_r(a, 6, 99)
    a.label("t1")
    jump_compute(a, "t2", False, EQ, 0, 7, 1, 2)
    set_r(a, 8, 7)
    a.label("t2")
    jump_compute(a, "t3", False, EQ, 1, 9, 1, 2)
    set_r(a, 10, 8)
    a.label("t3")
    jump_compute(a, "t4", True, EQ, 1, 11, 1, 2)
    set_r(a, 12, 5)
    set_r(a, 13, 6)
    set_r(a, 14, 77)
    a.label("t4")
    jump_compute(a, "t5", False, 0x1F, 1, 15, 1, 2)
    set_r(a, 0, 78)
    a.label("t5")
    jump_compute(a, "t6", False, 0x1F, 0, 15, 1, 2)
    set_r(a, 0, 79)
    a.label("t6")
    return finish(a, [], 1)


def ctl_call_in_loop() -> tuple[list[int], list[int], int]:
    """A DO loop whose body calls a function (the loop's PC stack entry lies
    below the call's); the regions start before the loop and inside it."""
    a = Asm()
    jump_abs(a, "main")
    a.label("f")
    set_r(a, 9, 1)
    add(a, 10, 9, 3)
    rts(a, False)
    a.label("main")
    set_r(a, 3, 5)
    a.ins("12a_imm", mode=1, data=3, reladdr=("rel", "lend"))
    a.label("body")
    call(a, "f", False)
    a.label("r")
    add(a, 4, 4, 10)
    call(a, "f", True)
    set_r(a, 5, 1)
    set_r(a, 6, 2)
    a.label("r2")
    a.label("lend")
    add(a, 7, 7, 3)
    set_r(a, 8, 9)
    return finish(a, ["f", "body", "r", "r2", "lend", "main"], 1)


def ctl_return_rframe_simd() -> tuple[list[int], list[int], int]:
    """The return of ctl_return_slots with RFRAME in the slot, in SIMD mode
    (RFRAME has no SIMD companion)."""
    a = Asm()
    a.ins("18a", bop=0, sreg=2, data=0x200000)  # MODE1 = set(MODE1, PEYEN)
    a.ins("17a", ureg=I(12), data=("abs", "back"))
    set_r(a, 14 + 32, 0)
    a.ins(
        "9b_abs",
        b=0,
        a=0,
        cond=0x1F,
        **{"pmi[2:2]": 1, "pmi[1:0]": 0},
        **{"pmm[2:0]": 6},
        j=1,
        ci=0,
    )
    a.ins("25c_rframe")
    a.ins("21c")
    set_r(a, 1, 55)
    a.label("back")
    a.ins("18a", bop=1, sreg=2, data=0x200000)  # MODE1 = clear(MODE1, PEYEN)
    set_r(a, 2, 66)
    return finish(a, ["back"], 2)


def ctl_indirect_jump() -> tuple[list[int], list[int], int]:
    """JUMP (Md, Ic) through DAG2 registers: not delayed, then delayed (the
    stack model: no PC stack effect, the target is I + M)."""
    a = Asm()
    a.ins("17a", ureg=I(12), data=("abs", "t1"))
    set_r(a, 13 + 32, 0)  # M13 = 0
    a.ins(
        "9b_abs",
        b=0,
        a=0,
        cond=0x1F,
        **{"pmi[2:2]": 1, "pmi[1:0]": 0},
        **{"pmm[2:0]": 5},
        j=0,
        ci=0,
    )
    set_r(a, 1, 55)
    a.label("t1")
    set_r(a, 2, 66)
    a.ins("17a", ureg=I(13), data=("abs", "t2"))
    set_r(a, 14 + 32, 0)  # M14 = 0
    a.ins(
        "9b_abs",
        b=0,
        a=0,
        cond=0x1F,
        **{"pmi[2:2]": 1, "pmi[1:0]": 1},
        **{"pmm[2:0]": 6},
        j=1,
        ci=0,
    )
    set_r(a, 3, 77)
    add(a, 4, 3, 2)
    set_r(a, 5, 88)
    a.label("t2")
    set_r(a, 6, 99)
    # JUMP (M5, I12) (DB), R9 = R1 + R2: the compute runs, the target is read
    # before it.
    a.ins("17a", ureg=I(12), data=("abs", "t3"))
    set_r(a, 13 + 32, 0)
    a.ins(
        "9a_abs",
        b=0,
        a=0,
        cond=0x1F,
        **{"pmi[2:2]": 1, "pmi[1:0]": 0},
        **{"pmm[2:0]": 5},
        j=1,
        e=0,
        ci=0,
        compute=compute(ADD, 9, 1, 2),
    )
    set_r(a, 7, 5)
    set_r(a, 8, 6)
    set_r(a, 10, 7)
    a.label("t3")
    # The same with ELSE: the compute never runs.
    a.ins("17a", ureg=I(12), data=("abs", "t4"))
    a.ins(
        "9a_abs",
        b=0,
        a=0,
        cond=0x1F,
        **{"pmi[2:2]": 1, "pmi[1:0]": 0},
        **{"pmm[2:0]": 5},
        j=0,
        e=1,
        ci=0,
        compute=compute(ADD, 11, 1, 2),
    )
    set_r(a, 12, 8)
    a.label("t4")
    set_r(a, 14, 9)
    return finish(a, ["t1", "t2", "t3", "t4"], 2)


def ctl_rts_compute() -> tuple[list[int], list[int], int]:
    """Returns with a compute (taken and not, plain and ELSE)."""
    a = Asm()
    jump_abs(a, "main")
    a.label("f1")
    rts_compute(a, False, NE, 0, 3, 1, 2)
    set_r(a, 9, 1)
    a.label("f2")
    rts_compute(a, True, EQ, 1, 4, 1, 2)
    set_r(a, 10, 2)
    set_r(a, 11, 3)
    rts_compute(a, True, NE, 1, 5, 1, 2)
    set_r(a, 12, 4)
    set_r(a, 13, 5)
    a.label("f3")
    rts_compute(a, False, 0x1F, 0, 6, 1, 2)
    a.label("main")
    set_r(a, 1, 3)
    set_r(a, 2, 4)
    call(a, "f1", False)
    a.label("r1")
    call(a, "f2", False)
    a.label("r2")
    call(a, "f3", True)
    set_r(a, 14, 6)
    set_r(a, 15, 7)
    a.label("r3")
    set_r(a, 0, 8)
    return finish(a, ["f1", "f2", "f3", "r1", "r2", "r3", "main"], 1)


def ctl_return_slots() -> tuple[list[int], list[int], int]:
    """The compiler's return: JUMP (M14, I12) (DB) whose slots are I7 = I6 and
    I6 = DM(I6 + 0) (RFRAME as two moves), I6 a circular index register."""
    a = Asm()
    a.ins("17a", ureg=I(12), data=("abs", "back"))
    set_r(a, 14 + 32, 0)  # M14 = 0
    a.ins(
        "9b_abs",
        b=0,
        a=0,
        cond=0x1F,
        **{"pmi[2:2]": 1, "pmi[1:0]": 0},
        **{"pmm[2:0]": 6},
        j=1,
        ci=0,
    )
    a.ins(
        "5b_move",
        cond=0x1F,
        dstureg=I(7),
        srcureghigh=I(6) >> 2,
        **{"srcureglow[1:1]": (I(6) >> 1) & 1, "srcureglow[0:0]": I(6) & 1},
    )
    a.ins("15b", **{"i[2:0]": 6}, d=0, l=0, g=0, ureg=I(6), **{"data[6:0]": 0})
    set_r(a, 1, 55)
    a.label("back")
    set_r(a, 2, 66)
    return finish(a, ["back"], 2)


CONTROL = {
    "ctl_call_ret": ctl_call_ret,
    "ctl_call_plain": ctl_call_plain,
    "ctl_jump_compute": ctl_jump_compute,
    "ctl_rts_compute": ctl_rts_compute,
    "ctl_call_in_loop": ctl_call_in_loop,
    "ctl_indirect_jump": ctl_indirect_jump,
    "ctl_return_rframe_simd": ctl_return_rframe_simd,
    "ctl_return_slots": ctl_return_slots,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    lines = [
        "// Generated by tools/sharc_dev/sharc_lowergen_programs.py (public ISA encodings).",
        "pub const PROGRAMS: &[(&str, &[u16])] = &[",
    ]
    for name, fn in PROGRAMS.items():
        ps = fn()
        lines.append('    ("%s", &[%s]),' % (name, ", ".join("%#06x" % p for p in ps)))
    lines.append("];")
    lines.append(
        "/// (name, parcels, region entries, configurations: 1 both, 2 stack model only)"
    )
    lines.append("pub const CONTROL: &[(&str, &[u16], &[u32], u8)] = &[")
    for name, fn in CONTROL.items():
        ps, entries, cfgs = fn()
        lines.append(
            '    ("%s", &[%s], &[%s], %d),'
            % (
                name,
                ", ".join("%#06x" % p for p in ps),
                ", ".join(str(e) for e in entries),
                cfgs,
            )
        )
    lines.append("];")
    open(a.out, "w").write("\n".join(lines) + "\n")  # noqa: SIM115


if __name__ == "__main__":
    main()
