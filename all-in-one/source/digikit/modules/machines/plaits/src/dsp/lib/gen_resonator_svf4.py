"""Writes resonator_svf4.s, the SHARC+ kernel of ResonatorSvf<4>'s sample loop (resonator.c), from the modulo
schedule below.

    uv run python modules/machines/plaits/src/dsp/lib/gen_resonator_svf4.py          # rewrites resonator_svf4.s
    uv run python modules/machines/plaits/src/dsp/lib/gen_resonator_svf4.py --check  # exit 1 if it is stale

The per-filter recurrence (resonator.c, the C loop; selcc's compiled order per filter i):
    m1 = r_plus_g * s1;  a1 = s_in - m1;  a2 = a1 - s2;  hp = a2 * h;  m2 = g * hp;  bp = m2 + s1;
    s1 = m2 + bp;  m3 = g * bp;  lp = m3 + s2;  s2 = m3 + lp;  p = gain * bp
    s_out = (((0 + p0) + p1) + p2) + p3;  out[n] += s_out
Multiplication and addition are commutative bit for bit on the core (a NaN operand gives the all-ones NaN either
way), so only the order of the additions in s_out and the subtraction operands are fixed.

SIMD layout: filters 0 and 1 are pair A (PEx = filter 0, PEy = filter 1), filters 2 and 3 pair B; a normal-word SIMD
load of word w gives the pair (w, w + 1), so state_1_[0..1] is s1A, state_1_[2..3] s1B and so on. s_in reaches both
PEs through M4 (an uncomplemented register: `M4 = DM(..)` has no PEy half, `Rn = M4` writes Rn and Sn). The serial
s_out sum crosses lanes with `Rn = Sn` (in SIMD a complemented move: PEx Rn = Sn, PEy Sn = Rn, a lane swap). The
running sum's PEx lane is s_out; it is stored to the scratch row (the PEy half lands one word higher and is
overwritten by the next sample; the row has size + 1 words). A SIMD pass then adds the row into out[] two samples
at a time.

The loop body is one modulo schedule of II = 16 instructions per sample (found by sched_resonator_svf4.py, a z3 search over the instruction
forms that occur in stock 1.17 DSP code: multiply results only in registers stock uses for that form and operation,
multifunction operands in the R0-3 x R4-7 / R8-11 +- R12-15 files, one transfer per instruction, `Rn = Sn` / `Rn =
M4` only beside a single-function compute because Selache mis-encodes them beside a multifunction one). An op at time
t (0..47) of sample n runs in loop window n + t // 16 at slot t % 16; windows 0, 1 (prologue) and size, size + 1
(epilogue) are emitted with only the ops of existing samples, so nothing reads or writes outside in[0..size-1],
out[0..size-1], the coefficients and tmp[0..size].
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "resonator_svf4.s"
OUT_SM = HERE / "resonator_svf4_sm.s"  # the variant with no data transfer in SIMD mode (H-opt1 hung)
II = 16

# (time, op): time = 16 * stage + slot
SCHEDULE: dict[str, int] = {
    "a1A": 16,
    "ld_hB": 16,
    "pB": 32,  # slot 0
    "a2B": 17,
    "pA": 33,  # slot 1
    "c1": 34,
    "swA": 34,  # slot 2
    "hpB": 19,
    "ld_gB": 19,
    "s2A": 35,  # slot 3
    "ldM4": 4,
    "a2A": 20,
    "m2B": 20,  # slot 4
    "bpB": 21,  # slot 5
    "m3B": 22,
    "ld_gA": 22,
    "c2": 38,  # slot 6
    "bcast": 7,
    "c3": 39,  # slot 7
    "s1B": 24,
    "swB": 40,  # slot 8
    "ld_rpgB": 9,
    "lpB": 25,
    "hpA": 25,  # slot 9
    "m1B": 10,
    "ld_hA": 10,
    "s2B": 26,  # slot 10
    "a1B": 11,
    "m2A": 27,  # slot 11
    "ld_rpgA": 12,
    "bpA": 28,  # slot 12
    "m3A": 29,
    "ld_gainA": 29,
    "c4": 45,  # slot 13
    "s1A": 30,
    "stc4": 46,  # slot 14
    "m1A": 15,
    "lpA": 31,
    "ld_gainB": 31,  # slot 15
}
# value -> register number
REG: dict[str, int] = {
    "TA": 0,
    "TB": 1,
    "s1A": 2,
    "s1B": 3,
    "rpgA": 4,
    "hB": 4,
    "gainB": 4,
    "hA": 5,
    "rpgB": 5,
    "gA": 6,
    "gainA": 6,
    "gB": 6,
    "Z": 7,
    "pB": 8,
    "c4": 8,
    "m3A": 9,
    "m2B": 9,
    "sin": 10,
    "a1A": 10,
    "a1B": 11,
    "m3B": 11,
    "c1": 11,
    "m1A": 12,
    "m1B": 12,
    "lpB": 12,
    "m2A": 12,
    "pA": 12,
    "c2": 12,
    "lpA": 13,
    "c3": 13,
    "s2B": 14,
    "s2A": 15,
}

# Svf4Kernel word offsets (resonator.h)
OFF = {
    "gA": 0,
    "gB": 2,
    "rpgA": 4,
    "rpgB": 6,
    "hA": 8,
    "hB": 10,
    "gainA": 12,
    "gainB": 14,
}
K_TMP, K_AREA = 16, 34  # tmp: words 16 .. 16 + size; the area: 34 words on the C stack

ALU = {
    "a1A": ("a1A", "-", "sin", "m1A"),
    "a2A": ("TA", "-", "a1A", "s2A"),
    "a1B": ("a1B", "-", "sin", "m1B"),
    "a2B": ("TB", "-", "a1B", "s2B"),
    "bpA": ("TA", "+", "m2A", "s1A"),
    "s1A": ("s1A", "+", "m2A", "TA"),
    "bpB": ("TB", "+", "m2B", "s1B"),
    "s1B": ("s1B", "+", "m2B", "TB"),
    "lpA": ("lpA", "+", "m3A", "s2A"),
    "s2A": ("s2A", "+", "m3A", "lpA"),
    "lpB": ("lpB", "+", "m3B", "s2B"),
    "s2B": ("s2B", "+", "m3B", "lpB"),
    "c1": ("c1", "+", "Z", "pA"),
    "c2": ("c2", "+", "c1", "pA"),
    "c3": ("c3", "+", "c2", "pB"),
    "c4": ("c4", "+", "c3", "pB"),
}
MUL = {
    "m1A": ("m1A", "s1A", "rpgA"),
    "hpA": ("TA", "TA", "hA"),
    "m2A": ("m2A", "TA", "gA"),
    "m3A": ("m3A", "TA", "gA"),
    "pA": ("pA", "TA", "gainA"),
    "m1B": ("m1B", "s1B", "rpgB"),
    "hpB": ("TB", "TB", "hB"),
    "m2B": ("m2B", "TB", "gB"),
    "m3B": ("m3B", "TB", "gB"),
    "pB": ("pB", "TB", "gainB"),
}


# multiply result registers stock 1.17 code uses in Type 2a multifunction instructions (no transfer), per ALU operation
RM_2A = {"+": {0, 1, 2, 7, 10, 15}, "-": {2, 12, 15}}


def r(v: str) -> int:
    return REG[v]


def alu_text(op: str, multifunction: bool) -> str:
    dst, sign, x, y = ALU[op]
    if multifunction and sign == "+" and r(x) >= 12:
        x, y = y, x  # the multifunction adder takes R8-11 + R12-15
    return f"F{r(dst)} = F{r(x)} {sign} F{r(y)}"


def mul_text(op: str) -> str:
    dst, x, y = MUL[op]
    return f"F{r(dst)} = F{r(x)} * F{r(y)}"


def move_text(op: str) -> str:
    if op == "ldM4":
        return "M4 = DM(I0, M6)"
    if op == "bcast":
        return f"R{r('sin')} = M4"
    if op in ("swA", "swB"):
        p = r("pA" if op == "swA" else "pB")
        return f"R{p} = S{p}"
    if op == "stc4":
        return f"DM(I1, M6) = R{r('c4')}"
    assert op.startswith("ld_"), op
    c = op[3:]
    return f"R{r(c)} = DM({OFF[c.rstrip('2')]}, I4)"


def window(stages: set[int]) -> list[str]:
    """One window of the modulo schedule with only the ops of the given stages (an op at time t has stage t // 16):
    {0} and {0, 1} are windows 0 and 1, {0, 1, 2} the loop, {1, 2} and {2} windows size and size + 1."""
    out = []
    for k in range(II):
        ops = [o for o, t in SCHEDULE.items() if t % II == k and t // II in stages]
        alu = [o for o in ops if o in ALU]
        mul = [o for o in ops if o in MUL]
        mov = [o for o in ops if o not in ALU and o not in MUL]
        assert len(alu) <= 1 and len(mul) <= 1 and len(mov) <= 1, (k, ops)
        parts = []
        if (
            alu
            and mul
            and not mov
            and MUL[mul[0]][0]
            and r(MUL[mul[0]][0]) not in RM_2A[ALU[alu[0]][1]]
        ):
            # the loop's form of this slot carries a transfer; without it the multifunction instruction would be
            # Type 2a with a multiply result register stock never uses there: two instructions
            out.append(f"    {mul_text(mul[0])};".ljust(64) + f"// {mul[0]}")
            mul = []
        if alu and mul:
            parts = [mul_text(mul[0]), alu_text(alu[0], True)]
        elif alu:
            parts = [alu_text(alu[0], False)]
        elif mul:
            parts = [mul_text(mul[0])]
        if mov:
            if mul and not alu:
                # a lone multiply beside a transfer: two instructions (keeps to stock forms)
                out.append(f"    {parts[0]};".ljust(64) + f"// {mul[0]}")
                parts = []
            parts.append(move_text(mov[0]))
        if parts:
            out.append(f"    {', '.join(parts)};".ljust(64) + f"// {' '.join(ops)}")
    return out


HEADER = """\
// machines/plaits: the SHARC+ kernel of RINGS' resonator bank (lib/resonator.c, resonator_svf4_band_pass_add: the
// coefficients of four band-pass SVFs and their sample loop), GENERATED by lib/gen_resonator_svf4.py from its modulo
// schedule; edit the generator.
//
// void resonator_svf4_kernel(ResonatorSvf4 *s, const float *f, const float *q, const float *gain, const float *in,
//                            float *out, int size)
// resonator_svf4_band_pass_add's arguments and selcc's calling convention (R4 = s, R8 = f, R12 = q; gain, in, out
// and size at I6 + 1 .. 4; frame by CJUMP; return through I12 = DM(-1, I6) with RFRAME), for even sizes 4..16 (the
// caller checks). Output: bit-identical to the C function (see the generator's docstring).
//
// State on return (tests/plaits/test_resonator_kernel.py compares every register before and after): changed are the C
// ABI's scratch registers R0-R8, R12, I4, I12, LCNTR and the flags (ASTATx/y, STKYx/y: PEy's flags too, as in stock
// SIMD code; no stock code reads ASTATy/STKYy outside the interrupt and task save/restore, OPTIMIZING.md); every other
// register is as on entry: R9-R11, R13-R15, S0-S15 (saved and restored in SIMD pairs), M4, M6, I0, I1, L0, L1, L4
// (saved on the C stack above I7, so an interrupt push cannot reach them; M6 = 1 and L0 = L1 = L4 = 0 are set here,
// not assumed), MODE1 (PEYEN set for the kernel, cleared on the way out: the C caller runs in SISD), MODE2, the loop,
// PC and status stacks (two DO loops, both run to their end), I6, I7 (RFRAME) and the B registers. Assumes MODE1.RND32
// = 1 (32-bit float rounding, set at boot, 0x1c0f82): with 40-bit rounding the register-held states would differ from
// the C, which stores them each sample. Instruction forms: all occur in stock 1.17 DSP code; no PM transfers.

.SECTION/PM seg_pmco;
.GLOBAL resonator_svf4_kernel.;
.NOCOMPRESS;

resonator_svf4_kernel.:
    R0 = M4;                                            // SISD: the DAG registers it uses or relies on, through R0
                                                                // (a DAG1 register stored through DAG1 is illegal, PRM p.14-15)
    DM(I7, M7) = R0;
    R0 = I0;
    DM(I7, M7) = R0;
    R0 = I1;
    DM(I7, M7) = R0;
    R0 = M6;
    DM(I7, M7) = R0;
    R0 = L0;
    DM(I7, M7) = R0;
    R0 = L1;
    DM(I7, M7) = R0;
    R0 = L4;
    DM(I7, M7) = R0;
    M6 = 1;                                                     // its own values: no reliance on the caller's
    L0 = 0;
    L1 = 0;
    L4 = 0;
    I7=MODIFY (I7,-34)(NW);                                   // the coefficient and tmp area [I7 + 1, I7 + 34]
    I4 = I7;
    I4=MODIFY (I4,1)(NW);                                     // its base: coefficients at I4 + 0..15
    I7=MODIFY (I7,-32)(NW);                                   // the pair area [I7 + 1, I7 + 32]
    BIT SET MODE1 0x200000;                                     // PEYEN: SIMD
    NOP;
"""


def coefficients(f: int, q: int, g_off: int, rpg_off: int, h_off: int, store: bool = True) -> list[str]:
    """g = tan_fast(f), r = 1 / q, h = 1 / (1 + r g + g g), r + g for one pair (f in Rf, q in Rq), stored to the
    coefficient area: selcc's operations for resonator_svf4_band_pass_add's setup and tan_fast, in its order (a
    division is RECIPS, two Newton steps y = y (2 - d y), then q0 = x y, q0 + (x - d q0) y with x = 1.0).
    Constants: R4 = 10.1080465f, R5 = 55.7873878f, R6 = pi, R7 = 2.0f, R8 = 1.0f; scratch R9-R15."""
    out = [
        f"    F9 = F{f} * F{f};                                             // tan_fast: f2",
        "    F10 = F5 * F9;",
        "    F10 = F4 + F10;",
        "    F10 = F9 * F10;",
        "    F10 = F6 + F10;",
        f"    F11 = F{f} * F10;                                             // g",
        f"    DM({g_off}, I4) = R11;",
        ".COMPRESS;                                                      // RECIPS: stock uses only the short form",
        f"    F9 = RECIPS F{q};                                             // r = 1 / q",
        ".NOCOMPRESS;",
        f"    F10 = F{q} * F9;",
        "    F10 = F7 - F10;",
        "    F9 = F9 * F10;",
        f"    F10 = F{q} * F9;",
        "    F10 = F7 - F10;",
        "    F9 = F9 * F10;",
        "    F12 = F8 * F9;",
        f"    F10 = F{q} * F12;",
        "    F10 = F8 - F10;",
        "    F10 = F10 * F9;",
        "    F12 = F12 + F10;                                            // r",
        "    F10 = F12 * F11;                                            // d = 1 + r g + g g",
        "    F10 = F8 + F10;",
        "    F13 = F11 * F11;",
        "    F10 = F10 + F13;",
        ".COMPRESS;",
        "    F9 = RECIPS F10;                                            // h = 1 / d",
        ".NOCOMPRESS;",
        "    F13 = F10 * F9;",
        "    F13 = F7 - F13;",
        "    F9 = F9 * F13;",
        "    F13 = F10 * F9;",
        "    F13 = F7 - F13;",
        "    F9 = F9 * F13;",
        "    F14 = F8 * F9;",
        "    F13 = F10 * F14;",
        "    F13 = F8 - F13;",
        "    F13 = F13 * F9;",
        "    F14 = F14 + F13;                                            // h",
        f"    F15 = F12 + F11, DM({h_off}, I4) = R14;                          // r + g",
        f"    DM({rpg_off}, I4) = R15;",
    ]
    if not store:  # results stay in R11 (g), R14 (h), R15 (r + g): no memory access in SIMD mode
        out = [x.replace(f", DM({h_off}, I4) = R14", "") for x in out if not x.lstrip().startswith("DM(")]
    return out


def build() -> str:
    L = [HEADER.rstrip("\n")]
    L += [f"    DM({1 + 2 * p}, I7) = R{p};" for p in range(16)]  # R0-R15 with S0-S15
    st = {
        "s1A": 0,
        "s1B": 2,
        "s2A": 4,
        "s2B": 6,
    }  # ResonatorSvf4 word offsets of the state pairs
    L += [
        "    // ---- the coefficients of the four filters, two pairs (resonator.c's setup loop)",
        "    I0 = R8;                                                    // f",
        "    I1 = R12;                                                   // q",
        "    R4 = 0x4121BA8F;                                            // 10.1080465f",
        "    R5 = 0x425F2649;                                            // 55.7873878f",
        "    R0 = DM(0, I0);                                             // f[0], f[1]",
        "    R2 = DM(2, I0);                                             // f[2], f[3]",
        "    R1 = DM(0, I1);                                             // q[0], q[1]",
        "    R3 = DM(2, I1);                                             // q[2], q[3]",
        "    I1 = DM(1, I6);                                             // gain",
        "    R6 = 0x40490FDB;                                            // 3.14159274f",
        "    R7 = 0x40000000;                                            // 2.0f",
        "    R8 = 0x3F800000;                                            // 1.0f",
        "    R9 = DM(0, I1);",
        "    R10 = DM(2, I1);",
        f"    DM({OFF['gainA']}, I4) = R9;",
        f"    DM({OFF['gainB']}, I4) = R10;",
        *coefficients(0, 1, OFF["gA"], OFF["rpgA"], OFF["hA"]),
        *coefficients(2, 3, OFF["gB"], OFF["rpgB"], OFF["hB"]),
        "    // ---- the sample loop's set-up",
        "    I1 = DM(9, I7);                                             // s: R4 on entry (pair 4's PEx half)",
        "    I0 = DM(2, I6);                                             // in: M4 = DM(I0, M6) (no PEy half)",
        "    R0 = DM(4, I6);                                             // size",
        "    R1 = 2;",
        "    R0 = R0 - R1;",
        "    I12 = R0;                                                   // the loop's trip count: size - 2",
        f"    R{r('Z')} = 0;                                                    // 0.0f in both PEs: s_out's start",
    ]
    L += [f"    R{r(v)} = DM({o}, I1);".ljust(64) + f"// {v}" for v, o in st.items()]
    L += [
        "    I1 = I4;",
        f"    I1=MODIFY (I1,{K_TMP})(NW);                                  // tmp: s_out per sample (PEx half)",
        "    // ---- prologue: windows 0 and 1 of the modulo schedule",
        *window({0}),
        *window({0, 1}),
        "    // ---- the loop: windows 2 .. size - 1, one sample each",
        "    LCNTR = I12, DO (PC, _rsk_loop_end) UNTIL LCE;",
    ]
    body = window({0, 1, 2})
    L += [*body[:-1], "_rsk_loop_end:", body[-1]]
    L += [
        "    // ---- epilogue: windows size and size + 1",
        *window({1, 2}),
        *window({2}),
        "    // ---- the states back, then out[n] += tmp[n] two samples per pass (the C loop's last addition)",
        "    I0 = DM(9, I7);                                             // s",
        "    I1 = I4;",
        f"    I1=MODIFY (I1,{K_TMP})(NW);",
    ]
    L += [f"    DM({o}, I0) = R{r(v)};".ljust(64) + f"// {v}" for v, o in st.items()]
    L += [
        "    R2 = DM(4, I6);                                             // size",
        "    I0 = DM(3, I6);                                             // out, loads",
        "    I4 = DM(3, I6);                                             // out, stores",
        "    M4 = 2;",
        "    R3 = -2;",
        "    R2 = R2 + R3;",
        "    R3 = -1;",
        "    R2 = LSHIFT R2 BY R3;                                       // size / 2 - 1: passes after the first",
        "    R8 = DM(I0, M4);",
        "    R12 = DM(I1, M4);",
        "    LCNTR = R2, DO (PC, _rsk_add_end) UNTIL LCE;",
        "    F10 = F8 + F12, R8 = DM(I0, M4);",
        "    R12 = DM(I1, M4);",
        "_rsk_add_end:",
        "    DM(I4, M4) = R10;",
        "    F10 = F8 + F12;",
        "    DM(I4, M4) = R10;",
        "    // ---- restore and return",
    ]
    L += [f"    R{p} = DM({1 + 2 * p}, I7);" for p in range(16)]
    L += [
        "    BIT CLR MODE1 0x200000;                                     // SISD again",
        "    NOP;",
        f"    I7=MODIFY (I7,{32 + K_AREA})(NW);                                 // back to the seven pushes",
        "    L4 = DM(1, I7);",
        "    L1 = DM(2, I7);",
        "    L0 = DM(3, I7);",
        "    M6 = DM(4, I7);",
        "    I1 = DM(5, I7);",
        "    I0 = DM(6, I7);",
        "    M4 = DM(7, I7);",
        "// selcc's own return sequence, compressed: under .NOCOMPRESS Selache encodes the indirect jump as Type 9a with",
        "// the wrong registers and RFRAME as a NOP (PORTING.md drift 1)",
        ".COMPRESS;",
        "    I12 = DM(M7, I6);",
        "    JUMP (M14, I12) (DB);",
        "    RFRAME;",
        "    NOP;",
        ".NOCOMPRESS;",
    ]
    return "\n".join(L)


SM_HEADER = """\
// machines/plaits: RINGS' resonator kernel, SISD-MEMORY VARIANT (lib/resonator.c, resonator_svf4_band_pass_add),
// GENERATED by lib/gen_resonator_svf4.py (build_sm); edit the generator.
//
// Same function, arguments, result (bit-identical to the C) and register contract as resonator_svf4.s, but no data
// transfer happens in SIMD mode: H-opt1 (resonator_svf4.s, SIMD loads and stores in L2 byte space) hung the DSP on the
// unit, and stock 1.17 does SIMD data accesses only in L1. Here PEYEN brackets compute only (stock loops toggle it per
// pass, e.g. sw 0x1c1544); every load and store runs in SISD, filling S registers by explicit `Sn = DM(..)` /
// `DM(..) = Sn` transfers and `Sn = Rn` moves. One sample per pass: s_in loaded in SISD (R12, S12 = R12), the two
// filter pairs in SIMD (22 single-function computes), then in SISD the serial sum p0..p3 (lane halves by `Rn = Sn`),
// out[n] += s_out, and the two reloaded coefficient pairs the pass reused as scratch.
//
// State on return: R0-R8, R12, I4, I12, LCNTR and the flags (ASTATx/y, STKYx/y) change; every other register is as on
// entry: R9-R11, R13-R15 and S0-S15 (SISD saves above I7), I0, I1, I2, M6, L0, L1, L2, L4 (pushed; M6 = 1 and L0 = L1 =
// L2 = L4 = 0 set here), MODE1 (PEYEN cleared on the way out), MODE2, the stacks, I6/I7 (RFRAME), B. Assumes MODE1.RND32 = 1.

.SECTION/PM seg_pmco;
.GLOBAL resonator_svf4_kernel.;
.NOCOMPRESS;

resonator_svf4_kernel.:
    R0 = I0;                                            // SISD throughout, except the PEYEN compute brackets; DAG
                                                                // registers pushed through R0 (PRM p.14-15)
    DM(I7, M7) = R0;
    R0 = I1;
    DM(I7, M7) = R0;
    R0 = I2;
    DM(I7, M7) = R0;
    R0 = M6;
    DM(I7, M7) = R0;
    R0 = L0;
    DM(I7, M7) = R0;
    R0 = L1;
    DM(I7, M7) = R0;
    R0 = L2;
    DM(I7, M7) = R0;
    R0 = L4;
    DM(I7, M7) = R0;
    M6 = 1;
    L0 = 0;
    L1 = 0;
    L2 = 0;
    L4 = 0;                                                     // I4's immediate offsets wrap too when L4 != 0
    I7=MODIFY (I7,-36)(NW);                                   // [I7 + 1, I7 + 36]: saves, coefficients, s
"""

SM_AREA = {"gA": 1, "hA": 3, "rpgA": 5, "gB": 7, "hB": 9, "rpgB": 11, "s": 13}  # word offsets from I4 = I7 + 22


def build_sm() -> str:
    L = [SM_HEADER.rstrip("\n")]
    L += [f"    DM({1 + p}, I7) = S{p};" for p in range(16)]
    L += [f"    DM({17 + k}, I7) = R{r};" for k, r in enumerate((9, 10, 11, 13, 14, 15))]
    L += [
        "    I4 = I7;",
        "    I4=MODIFY (I4,22)(NW);                                    // the coefficient area",
        f"    DM({SM_AREA['s']}, I4) = R4;                                       // s",
        "    I1 = R8;                                                    // f",
        "    R0 = DM(0, I1);",
        "    S0 = DM(1, I1);",
        "    R2 = DM(2, I1);",
        "    S2 = DM(3, I1);",
        "    I1 = R12;                                                   // q",
        "    R1 = DM(0, I1);",
        "    S1 = DM(1, I1);",
        "    R3 = DM(2, I1);",
        "    S3 = DM(3, I1);",
    ]
    for reg, val in ((4, "0x4121BA8F"), (5, "0x425F2649"), (6, "0x40490FDB"), (7, "0x40000000"), (8, "0x3F800000")):
        L += [f"    R{reg} = {val};", f"    S{reg} = {val};"]
    for pair, (f, q) in (("A", (0, 1)), ("B", (2, 3))):
        L += [
            f"    // ---- pair {pair}'s coefficients: compute in SIMD, stored in SISD",
            "    BIT SET MODE1 0x200000;",
            "    NOP;",
            *coefficients(f, q, 0, 0, 0, store=False),
            "    BIT CLR MODE1 0x200000;",
            "    NOP;",
        ]
        for name, reg in (("g", 11), ("h", 14), ("rpg", 15)):
            o = SM_AREA[name + pair]
            L += [f"    DM({o}, I4) = R{reg};", f"    DM({o + 1}, I4) = S{reg};"]
    loads = [
        ("R5", "hA", 0), ("S5", "hA", 1), ("R6", "gA", 0), ("S6", "gA", 1), ("R4", "rpgA", 0), ("S4", "rpgA", 1),
        ("R9", "hB", 0), ("S9", "hB", 1), ("R10", "gB", 0), ("S10", "gB", 1), ("R8", "rpgB", 0), ("S8", "rpgB", 1),
    ]
    L += ["    // ---- the loop's registers (SISD)", "    I1 = DM(1, I6);                                             // gain",
          "    R7 = DM(0, I1);", "    S7 = DM(1, I1);", "    R11 = DM(2, I1);", "    S11 = DM(3, I1);"]
    L += [f"    {reg} = DM({SM_AREA[c] + k}, I4);" for reg, c, k in loads]
    L += [f"    I1 = DM({SM_AREA['s']}, I4);"]
    L += [f"    {('R' if k % 2 == 0 else 'S')}{k // 2} = DM({k}, I1);" for k in range(8)]
    L += [
        "    I0 = DM(2, I6);                                             // in",
        "    I2 = DM(3, I6);                                             // out",
        "    R13 = DM(4, I6);                                            // size",
        "    LCNTR = R13, DO (PC, _rsm_end) UNTIL LCE;",
        "    R12 = DM(I0, M6);                                           // s_in",
        "    S12 = R12;",
        "    BIT SET MODE1 0x200000;",
        "    NOP;",
        "    F13 = F4 * F0;                                              // m1 = r_plus_g * s1 (A in R13, B in R14)",
        "    F14 = F8 * F1;",
        "    F13 = F12 - F13;                                            // a1 = s_in - m1",
        "    F14 = F12 - F14;",
        "    F13 = F13 - F2;                                             // a2 = a1 - s2",
        "    F14 = F14 - F3;",
        "    F13 = F13 * F5;                                             // hp = a2 * h",
        "    F14 = F14 * F9;",
        "    F15 = F6 * F13;                                             // m2 = g * hp (A in R15, B in R12)",
        "    F12 = F10 * F14;",
        "    F13 = F15 + F0;                                             // bp = m2 + s1",
        "    F14 = F12 + F1;",
        "    F0 = F15 + F13;                                             // s1 = m2 + bp",
        "    F1 = F12 + F14;",
        "    F15 = F6 * F13;                                             // m3 = g * bp",
        "    F12 = F10 * F14;",
        "    F4 = F15 + F2;                                              // lp = m3 + s2 (A in R4, B in R8)",
        "    F8 = F12 + F3;",
        "    F2 = F15 + F4;                                              // s2 = m3 + lp",
        "    F3 = F12 + F8;",
        "    F13 = F7 * F13;                                             // p = gain * bp",
        "    F14 = F11 * F14;",
        "    BIT CLR MODE1 0x200000;",
        "    NOP;",
        "    R4 = 0;                                                     // s_out = 0.0f",
        "    F4 = F4 + F13, R15 = S13;                                   // + p0; p1",
        "    F4 = F4 + F15, R12 = S14;                                   // + p1; p3",
        "    F4 = F4 + F14, R15 = DM(0, I2);                             // + p2; out[n]",
        f"    F4 = F4 + F12, R8 = DM({SM_AREA['rpgB']}, I4);                             // + p3; r_plus_g B",
        f"    F15 = F15 + F4, R4 = DM({SM_AREA['rpgA']}, I4);                            // out[n] + s_out; r_plus_g A",
        "    DM(I2, M6) = R15;",
        f"    S4 = DM({SM_AREA['rpgA'] + 1}, I4);",
        "_rsm_end:",
        f"    S8 = DM({SM_AREA['rpgB'] + 1}, I4);",
        "    // ---- states back, restore, return",
        f"    I1 = DM({SM_AREA['s']}, I4);",
    ]
    L += [f"    DM({k}, I1) = {('R' if k % 2 == 0 else 'S')}{k // 2};" for k in range(8)]
    L += [f"    S{p} = DM({1 + p}, I7);" for p in range(16)]
    L += [f"    R{r} = DM({17 + k}, I7);" for k, r in enumerate((9, 10, 11, 13, 14, 15))]
    L += [
        "    I7=MODIFY (I7,36)(NW);",
        "    L4 = DM(1, I7);",
        "    L2 = DM(2, I7);",
        "    L1 = DM(3, I7);",
        "    L0 = DM(4, I7);",
        "    M6 = DM(5, I7);",
        "    I2 = DM(6, I7);",
        "    I1 = DM(7, I7);",
        "    I0 = DM(8, I7);",
        ".COMPRESS;",
        "    I12 = DM(M7, I6);",
        "    JUMP (M14, I12) (DB);",
        "    RFRAME;",
        "    NOP;",
    ]
    # assembled compressed throughout (the assembler keeps a DO loop's instructions 48-bit, as the PRM requires for a
    # loop's last 11): the prologue and epilogue shrink, so less hot code per RINGS voice (Task O5)
    text = "\n".join(L).replace(".NOCOMPRESS;", ".COMPRESS;")
    first = text.index(".COMPRESS;") + len(".COMPRESS;")
    return text[:first] + "\n".join(x for x in text[first:].split("\n") if not x.startswith(".COMPRESS;"))


def main(argv: list[str]) -> int:
    stale = 0
    for path, text in ((OUT, build() + "\n"), (OUT_SM, build_sm() + "\n")):
        if "--check" in argv:
            if not path.exists() or path.read_text() != text:
                print(f"{path} is stale: run {Path(__file__).name}")
                stale = 1
        else:
            path.write_text(text)
    return stale


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
