"""Writes drums_sm.s, the SHARC+ kernels of the drum engines' sample loops (engines/kick.c, snare.c, hihat.c).

    uv run python modules/machines/plaits/src/dsp/lib/gen_drum_kernels.py          # rewrites drums_sm.s
    uv run python modules/machines/plaits/src/dsp/lib/gen_drum_kernels.py --check  # exit 1 if it is stale

Each kernel replaces one sample loop of a drum engine on the DSP (the C stays for the host and as the -DPL_DRUMS_C
reference). The C computes the per-block values (and the literals the loop uses) into a float array `k` and calls the
kernel with (state, k, out, size); the kernel keeps the engine state in registers through the block and stores it
back. Bit-identical to selcc's code of the C loop: the same float operations on the same operands in the same order
(multiplication and addition are commutative bit for bit on the core), selcc's division (RECIPS, two Newton steps,
one correction: `div`) and the same comparison operand order and condition per branch (`IF cond` and its complement).

Hardware rules (OPTIMIZING.md, PORTING.md section 5): SISD only (no SIMD mode at all), no DAG register stored
through its own DAG (pushed through R0), every DAG register the kernel uses set and restored (I0, I1, I2, M6, L0, L1,
L2, L4; M6 = 1, L = 0), R9-R11 and R13-R15 saved above I7. Scratch (selcc's caller-saved set): R0-R8, R12, I4,
I12, LCNTR and the flags. Single-function computes only (a transfer folded beside one is Type 4), RECIPS in the short
form, the selcc return; the whole file is assembled compressed (Task O5: the assembler keeps DO loops 48-bit, as
the PRM requires for a loop's last 11). Every instruction form passes pk_encoding_gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "drums_sm.s"


class Asm:
    def __init__(
        self,
        name: str,
        saves: tuple[str, ...] = ("R9", "R10", "R11", "R13", "R14", "R15"),
    ) -> None:
        self.name = name
        self.lines: list[str] = []
        self.saves = saves
        self.dags = ("I0", "I1", "I2", "M6", "L0", "L1", "L2", "L4")

    def __call__(self, text: str, comment: str = "") -> None:
        import re

        for m in re.finditer(r"DM\((-?\d+), I\d\)", text):
            assert -32 <= int(m.group(1)) <= 31, (
                text
            )  # the 6-bit offset of the compute + transfer form
        s = f"    {text};"
        if comment:
            s = f"{s:<56}// {comment}"
        self.lines.append(s)

    def raw(self, text: str) -> None:
        self.lines.append(text)

    def label(self, name: str) -> None:
        self.lines.append(f"{name}:")

    def recips(self, dst: str, src: str, move: str = "") -> None:
        """RECIPS with a register move beside it (Type 5a: stock's only 48-bit RECIPS shapes carry a transfer; selas
        does not compress inside a DO loop, so the short form is not available there)."""
        move = (
            move or f"R{src[1:]} = R{src[1:]}"
        )  # the divisor onto itself: a no-op beside the compute
        self(f"{dst} = RECIPS {src}, {move}")

    def div(
        self, res: str, n: str, d: str, y: str, t: str, w: str, comment: str = ""
    ) -> None:
        """res = n / d as selcc divides; w holds 2.0 on entry (it becomes the first quotient). res may be y or t."""
        self.recips(y, d)
        self(f"{t} = {d} * {y}", comment or "selcc division")
        self(f"{t} = {w} - {t}")
        self(f"{y} = {y} * {t}")
        self(f"{t} = {d} * {y}")
        self(f"{t} = {w} - {t}")
        self(f"{y} = {y} * {t}")
        self(f"{w} = {n} * {y}")
        self(f"{t} = {d} * {w}")
        self(f"{t} = {n} - {t}")
        self(f"{t} = {t} * {y}")
        self(f"{res} = {w} + {t}")

    def prologue(self) -> None:
        self.raw(f".GLOBAL {self.name}.;")
        self.label(f"{self.name}.")
        for r in self.dags:
            self(f"R0 = {r}")
            self("DM(I7, M7) = R0")
        self("M6 = 1")
        for r in self.dags[4:]:
            self(f"{r} = 0")
        n = len(self.saves)
        self(f"I7=MODIFY (I7,-{n})(NW)")
        for i, r in enumerate(self.saves):
            self(f"DM({i + 1}, I7) = {r}")

    def epilogue(self) -> None:
        n = len(self.saves)
        for i, r in enumerate(self.saves):
            self(f"{r} = DM({i + 1}, I7)")
        self(f"I7=MODIFY (I7,{n})(NW)")
        for i, r in enumerate(reversed(self.dags)):
            self(f"{r} = DM({i + 1}, I7)")
        self("I12 = DM(M7, I6)")
        self("JUMP (M14, I12) (DB)")
        self("RFRAME")
        self("NOP")

    def text(self) -> str:
        return "\n".join(self.lines)


# ---------------------------------------------------------------------------------------------------------- KICK

# kick_analog_kernel(AnalogBassDrum *s, const float *k, float *out, int size): analog_render's sample loop.
# k[] (kick.c, KA_*): the per-block values and the loop's literals.
KA = {
    "KPD": 0,  # 1.0f - 1.0f / kPulseDecayTime
    "KPF": 1,  # 1.0f / kPulseFilterTime
    "KRP": 2,  # 1.0f - 1.0f / kRetrigPulseDuration
    "C044": 3,
    "C07": 4,
    "TWO": 5,
    "ONE": 6,
    "TEN": 7,
    "C17": 8,
    "AFA": 9,  # attack_fm_amount
    "C008": 10,
    "SFA": 11,  # self_fm_amount
    "F0": 12,
    "C04": 13,
    "PI": 14,
    "CDIRTY": 15,  # tan_dirty's 11.5839453f
    "Q": 16,
    "SCALE": 17,
    "C02": 18,
    "LEAK": 19,  # exciter_leak
    "TONEF": 20,
    "M08": 21,  # -0.8f
    "ZERO": 22,
    "PH": 23,  # pulse_height_
    "PH1": 24,  # pulse_height_ - 1.0f
}
# AnalogBassDrum words
KS = {
    "prs": 0,
    "fprs": 1,
    "pulse": 2,
    "ph": 3,
    "plp": 4,
    "fmlp": 5,
    "retrig": 6,
    "lpout": 7,
    "tone": 8,
    "g": 9,
    "r": 10,
    "h": 11,
    "s1": 12,
    "s2": 13,
}


def diode(a: Asm, x: str, tag: str) -> None:
    """x = Diode(x) in place (kick.c): x >= 0 ? x : 0.7f * (2x) / (1.0f + absf(2x)). Uses R4, R9-R12 (R10 = 0.0
    on entry)."""
    k = KA
    fx = x
    rx = "R" + x[1:]
    a(f"COMP({fx}, F10)", "Diode: x >= 0.0f")
    a(f"IF GE JUMP {tag}")
    a(f"R9 = DM({k['TWO']}, I4)")
    a(f"{fx} = {fx} * F9, R11 = DM({k['C07']}, I4)", "x *= 2.0f")
    a(f"F11 = F11 * {fx}", "0.7f * x")
    a(f"F12 = -{fx}", "absf(x)")
    a(f"COMP({fx}, F10)")
    a(f"IF GE R12 = {rx} OR {rx}")
    a(f"R9 = DM({k['ONE']}, I4)")
    a(f"F12 = F9 + F12, R4 = DM({k['TWO']}, I4)", "1.0f + absf(x)")
    a.div(fx, "F11", "F12", "F9", "F10", "F4")
    a.label(tag)


def kick_analog() -> str:
    a = Asm("kick_analog_kernel")
    k, s = KA, KS
    a.raw("")
    a.raw(
        "// ---- kick_analog_kernel(AnalogBassDrum *s, const float *k, float *out, int size): analog_render's loop"
    )
    a.raw(
        "// R0 pulse_, R1 pulse_lp_, R2 fm_pulse_lp_, R3 retrig_pulse_, R5/R6 state_1_/state_2_, R7 tone_lp_,"
    )
    a.raw(
        "// R13 lp_out_ (then the diode'd pulse), R14/R15 the pulse counters; I1 = s, I4 = k, I0 = out"
    )
    a.prologue()
    a("I1 = R4")
    a("I4 = R8")
    a("I0 = R12")
    for reg, f in (
        ("R14", "prs"),
        ("R15", "fprs"),
        ("R0", "pulse"),
        ("R1", "plp"),
        ("R2", "fmlp"),
        ("R3", "retrig"),
        ("R13", "lpout"),
        ("R7", "tone"),
        ("R5", "s1"),
        ("R6", "s2"),
    ):
        a(f"{reg} = DM({s[f]}, I1)")
    a("R4 = DM(1, I6)", "size")
    a("R8 = 0")
    a("COMP(R4, R8)")
    a("IF LE JUMP _ka_done")
    a("LCNTR = R4, DO (PC, _ka_end) UNTIL LCE")
    # punch = 0.7f + Diode(10.0f * lp_out_ - 1.0f)
    a(f"R8 = DM({k['TEN']}, I4)")
    a(f"F8 = F8 * F13, R9 = DM({k['ONE']}, I4)", "10.0f * lp_out_")
    a(f"F8 = F8 - F9, R10 = DM({k['ZERO']}, I4)", "- 1.0f")
    diode(a, "F8", "_ka_d0")
    a(f"R9 = DM({k['C07']}, I4)")
    a("F8 = F9 + F8", "punch = 0.7f + Diode(..)")
    # pulse
    a(f"R14 = PASS R14, R9 = DM({k['KPD']}, I4)", "pulse_remaining_samples_ ?")
    a("IF EQ JUMP _ka_pdecay")
    a(f"R14 = R14 - 1, R0 = DM({k['PH']}, I4)", "--; pulse = pulse_height_")
    a("IF NE JUMP _ka_pb")
    a(f"R0 = DM({k['PH1']}, I4)", "pulse_height_ - 1.0f")
    a("JUMP _ka_pb")
    a.label("_ka_pdecay")
    a("F0 = F0 * F9", "pulse_ *= 1 - 1/kPulseDecayTime")
    a.label("_ka_pb")
    a(f"F9 = F0 - F1, R10 = DM({k['KPF']}, I4)", "ONE_POLE(pulse_lp_, pulse, ..)")
    a("F9 = F10 * F9")
    a(f"F1 = F1 + F9, R10 = DM({k['C044']}, I4)")
    a("F10 = F0 * F10", "pulse * 0.044f")
    a("F9 = F0 - F1", "pulse - pulse_lp_")
    a(f"F13 = F9 + F10, R10 = DM({k['ZERO']}, I4)")
    diode(a, "F13", "_ka_d1")
    # fm pulse
    a(f"R15 = PASS R15, R9 = DM({k['KRP']}, I4)", "fm_pulse_remaining_samples_ ?")
    a("IF EQ JUMP _ka_fdecay")
    a(f"R15 = R15 - 1, R3 = DM({k['ZERO']}, I4)", "--; retrig_pulse_ = 0.0f")
    a("IF NE JUMP _ka_fon")
    a(f"R3 = DM({k['M08']}, I4)", "-0.8f")
    a.label("_ka_fon")
    a(f"R10 = DM({k['ONE']}, I4)", "fm_pulse = 1.0f")
    a("JUMP _ka_fb")
    a.label("_ka_fdecay")
    a(
        f"F3 = F3 * F9, R10 = DM({k['ZERO']}, I4)",
        "retrig_pulse_ *= ..; fm_pulse = 0.0f",
    )
    a.label("_ka_fb")
    a(
        f"F10 = F10 - F2, R9 = DM({k['KPF']}, I4)",
        "ONE_POLE(fm_pulse_lp_, fm_pulse, ..)",
    )
    a("F10 = F9 * F10")
    a(f"F2 = F2 + F10, R9 = DM({k['C17']}, I4)")
    a(f"F9 = F2 * F9, R10 = DM({k['AFA']}, I4)", "fm_pulse_lp_ * 1.7f")
    a(f"F9 = F9 * F10, R10 = DM({k['C008']}, I4)", "attack_fm")
    a(f"F8 = F8 * F10, R10 = DM({k['SFA']}, I4)", "punch * 0.08f")
    a(f"F8 = F8 * F10, R10 = DM({k['ONE']}, I4)", "self_fm")
    a("F9 = F10 + F9", "1.0f + attack_fm")
    a(f"F9 = F9 + F8, R10 = DM({k['F0']}, I4)", "+ self_fm")
    a(f"F8 = F10 * F9, R10 = DM({k['ZERO']}, I4)", "f")
    a("COMP(F8, F10)", "CONSTRAIN(f, 0.0f, 0.4f)")
    a("IF LT R8 = R10 OR R10")
    a(f"R10 = DM({k['C04']}, I4)")
    a("COMP(F8, F10)")
    a("IF GT R8 = R10 OR R10")
    # g = tan_dirty(f)
    a(f"R9 = DM({k['CDIRTY']}, I4)")
    a("F9 = F9 * F8", "11.5839453f * f")
    a(f"F9 = F9 * F8, R10 = DM({k['PI']}, I4)", "* f")
    a("F9 = F10 + F9")
    a(f"F9 = F8 * F9, R10 = DM({k['Q']}, I4)", "g")
    a(f"F10 = F10 * F8, R11 = DM({k['ONE']}, I4)", "q * f")
    a(f"F12 = F11 + F10, R4 = DM({k['TWO']}, I4)", "resonance = 1.0f + q * f")
    a(f"DM({s['g']}, I1) = R9")
    a.div("F8", "F11", "F12", "F8", "F10", "F4", "r = 1.0f / resonance")
    a(f"F10 = F8 * F9, R11 = DM({k['ONE']}, I4)", "r * g")
    a("F10 = F11 + F10", "1.0f + r g")
    a(f"F12 = F9 * F9, DM({s['r']}, I1) = R8", "g * g")
    a(f"F12 = F10 + F12, R4 = DM({k['TWO']}, I4)")
    a.div("F8", "F11", "F12", "F10", "F8", "F4", "h = 1.0f / (1 + r g + g g)")
    # svf_step((pulse - retrig_pulse_ * 0.2f) * scale)
    a(f"R10 = DM({k['C02']}, I4)")
    a(f"F10 = F3 * F10, R11 = DM({k['SCALE']}, I4)", "retrig_pulse_ * 0.2f")
    a(f"F10 = F13 - F10, DM({s['h']}, I1) = R8")
    a(f"F10 = F10 * F11, R11 = DM({s['r']}, I1)", "in")
    a("F12 = F11 * F5", "r * state_1")
    a("F10 = F10 - F12")
    a("F12 = F9 * F5", "g * state_1")
    a("F10 = F10 - F12")
    a("F10 = F10 - F6", "- state_2")
    a("F10 = F10 * F8", "hp")
    a("F10 = F9 * F10", "g * hp")
    a("F11 = F10 + F5", "bp")
    a("F5 = F10 + F11", "state_1")
    a("F10 = F9 * F11", "g * bp")
    a("F12 = F10 + F6", "lp")
    a(f"F6 = F10 + F12, R10 = DM({k['LEAK']}, I4)", "state_2")
    a(f"F10 = F13 * F10, R8 = DM({k['TONEF']}, I4)", "pulse * exciter_leak")
    a("F10 = F10 + F11", "+ resonator_out")
    a("F10 = F10 - F7", "ONE_POLE(tone_lp_, .., tone_f)")
    a("F10 = F8 * F10")
    a("F7 = F7 + F10")
    a("R13 = PASS R12", "lp_out_ = lp")
    a.label("_ka_end")
    a("DM(I0, M6) = R7")
    a.label("_ka_done")
    for reg, f in (
        ("R14", "prs"),
        ("R15", "fprs"),
        ("R0", "pulse"),
        ("R1", "plp"),
        ("R2", "fmlp"),
        ("R3", "retrig"),
        ("R13", "lpout"),
        ("R7", "tone"),
        ("R5", "s1"),
        ("R6", "s2"),
    ):
        a(f"DM({s[f]}, I1) = {reg}")
    a.epilogue()
    return a.text()


def rng_float(a: Asm, dst: str, k: dict[str, int]) -> None:
    """dst = rng_float(&rng) (drums.h) with the word in R13: rng = rng * 1664525 + 1013904223 (the shift sum of
    rng_word is the same product mod 2^32), (itof_nonneg(w >> 16) * 65536.0f + itof_nonneg(w & 0xFFFF)) / 2^32 with
    selcc's division by the constant (its two Newton steps depend on the divisor only: k[Y32], made in the
    prologue). Uses R4-R8."""
    a("R4 = 1664525")
    a("R5 = 1013904223")
    a(f"R13 = R13 * R4 (SSI), R6 = DM({k['BIG']}, I4)", "rng_word")
    a("R13 = R13 + R5")
    a("R7 = FEXT R13 BY 16:16", "w >> 16")
    a("R8 = FEXT R13 BY 0:16", "w & 0xFFFF")
    a("R7 = R7 OR R6", "itof_nonneg")
    a("R8 = R8 OR R6")
    a(f"F7 = F7 - F6, R5 = DM({k['C65536']}, I4)")
    a("F8 = F8 - F6")
    a(f"F7 = F7 * F5, R5 = DM({k['Y32']}, I4)", "hi * 65536.0f")
    a(f"F7 = F7 + F8, R6 = DM({k['D32']}, I4)", "+ lo")
    a("F8 = F7 * F5", "/ 4294967296.0f: q")
    a("F4 = F6 * F8")
    a("F4 = F7 - F4")
    a("F4 = F4 * F5")
    a(f"{dst} = F8 + F4")


def recip_const(a: Asm, k: dict[str, int], d: str, y: str) -> None:
    """k[y] = the reciprocal selcc's division by the constant k[d] reaches after its two Newton steps (prologue)."""
    a(f"R8 = DM({k[d]}, I4)")
    a(f"R4 = DM({k['TWO']}, I4)")
    a.recips("F9", "F8")
    a("F10 = F8 * F9")
    a("F10 = F4 - F10")
    a("F9 = F9 * F10")
    a("F10 = F8 * F9")
    a("F10 = F4 - F10")
    a("F9 = F9 * F10")
    a(f"DM({k[y]}, I4) = R9")


def absf(a: Asm, dst: str, x: str, zero: str) -> None:
    """dst = absf(x) (dsp_util.h: x < 0.0f ? -x : x, selcc: COMP(x, 0); IF GE x)."""
    a(f"{dst} = -{x}")
    a(f"COMP({x}, {zero})")
    a(f"IF GE R{dst[1:]} = R{x[1:]} OR R{x[1:]}")


# kick_synth_kernel(SyntheticBassDrum *s, float *k, float *out, int size, const float *sine,
# unsigned int *rng): synthetic_render's loop.
KB = {
    n: i
    for i, n in enumerate(
        (
            "C0002",
            "HALF",
            "C025",
            "FMDECAY",
            "FEA35",
            "ONE",
            "BODYDECAY",
            "TRANSDECAY",
            "C01",
            "DIRT",
            "FOUR",
            "TWO",
            "C075",
            "C512",
            "ONEMDIRT",
            "ZERO",
            "C005",
            "C0005",
            "C06",
            "THREE",
            "C03",
            "TLEVEL",
            "TONEF",
            "C04",
            "BIG",
            "C65536",
            "Y32",
            "D32",
            "INC",
            "SINE",
            "T_MIX",
            "T_TRANS",
        )
    )
}
# SyntheticBassDrum words
SB = {
    n: i
    for i, n in enumerate(
        (
            "f0",
            "phase",
            "pn",
            "fm",
            "fmlp",
            "be",
            "belp",
            "te",
            "telp",
            "tone",
            "clp",
            "chp",
            "cg",
            "cr",
            "ch",
            "cs1",
            "cs2",
            "nlp",
            "nhp",
            "bepw",
            "fmpw",
        )
    )
}


def one_pole_mem(a: Asm, out: str, x: str, c: str, xr: str = "") -> None:
    """ONE_POLE(s->out, x, k[c]) on a state word (I1) with x in register xr or the state word x. Uses R9-R11."""
    k, s = KB, SB
    if not xr:
        a(f"R9 = DM({s[x]}, I1)")
        xr = "F9"
    a(f"R10 = DM({s[out]}, I1)")
    a(f"F11 = {xr} - F10, R9 = DM({k[c]}, I4)", f"ONE_POLE({out}_, ..)")
    a("F11 = F9 * F11")
    a("F10 = F10 + F11")
    a(f"DM({s[out]}, I1) = R10")


def kick_synth() -> str:
    a = Asm("kick_synth_kernel")
    k, s = KB, SB
    a.raw("")
    a.raw(
        "// ---- kick_synth_kernel(SyntheticBassDrum *s, float *k, float *out, int size, const float *sine,"
    )
    a.raw("// unsigned int *rng):")
    a.raw(
        "// synthetic_render's loop. R13 rng, R14 f0_mod's value, R15 phase_, R0/R1 fm_pulse_width_ /"
    )
    a.raw(
        "// body_env_pulse_width_, R2/R3 the click filter's states; the other state words stay in *s (I1); I4 = k,"
    )
    a.raw("// I0 = out, I2 = the sine table read")
    a.prologue()
    a("I1 = R4")
    a("I4 = R8")
    a("I0 = R12")
    recip_const(a, k, "D32", "Y32")
    a("R12 = DM(2, I6)", "sine")
    a(f"DM({k['SINE']}, I4) = R12")
    a("I2 = DM(3, I6)", "rng")
    a("R13 = DM(0, I2)")
    a(f"R14 = DM({s['f0']}, I1)")
    a(f"R15 = DM({s['phase']}, I1)")
    a(f"R0 = DM({s['fmpw']}, I1)")
    a(f"R1 = DM({s['bepw']}, I1)")
    a(f"R2 = DM({s['cs1']}, I1)")
    a(f"R3 = DM({s['cs2']}, I1)")
    a("R4 = DM(1, I6)", "size")
    a("R5 = 0")
    a("COMP(R4, R5)")
    a("IF LE JUMP _kb_done")
    a("LCNTR = R4, DO _kb_end UNTIL LCE")
    # ONE_POLE(phase_noise_, rng_float(rng) - 0.5f, 0.002f)
    rng_float(a, "F9", k)
    a(f"R10 = DM({k['HALF']}, I4)")
    a("F9 = F9 - F10", "rng_float - 0.5f")
    one_pole_mem(a, "pn", "", "C0002", "F9")
    # fm pulse / phase
    a("R0 = PASS R0", "fm_pulse_width_ ?")
    a("IF EQ JUMP _kb_fmrun")
    a(f"R0 = R0 - 1, R15 = DM({k['C025']}, I4)", "--; phase_ = 0.25f")
    a("JUMP _kb_fmdone")
    a.label("_kb_fmrun")
    a(f"R4 = DM({s['fm']}, I1)")
    a(f"R5 = DM({k['FMDECAY']}, I4)")
    a(f"F4 = F4 * F5, R5 = DM({k['FEA35']}, I4)", "fm_ *= fm_decay")
    a(f"R6 = DM({s['fmlp']}, I1)")
    a(f"F5 = F5 * F6, R6 = DM({k['ONE']}, I4)", "fm_envelope_amount * 3.5f * fm_lp_")
    a(f"F5 = F6 + F5, R6 = DM({k['INC']}, I4)", "fm")
    a(f"F14 = F14 + F6, DM({s['fm']}, I1) = R4", "next(&f0_mod)")
    a(f"F5 = F14 * F5, R6 = DM({k['HALF']}, I4)")
    a("COMP(F5, F6)", "minf(.., 0.5f)")
    a("IF GE R5 = R6 OR R6")
    a(f"F15 = F15 + F5, R6 = DM({k['ONE']}, I4)", "phase_ +=")
    a("COMP(F15, F6)")
    a("IF GE F15 = F15 - F6", "phase_ >= 1.0f: -= 1.0f")
    a.label("_kb_fmdone")
    # body envelope
    a("R1 = PASS R1", "body_env_pulse_width_ ?")
    a("IF EQ JUMP _kb_berun")
    a("R1 = R1 - 1")
    a("JUMP _kb_bedone")
    a.label("_kb_berun")
    a(f"R4 = DM({s['be']}, I1)")
    a(f"R5 = DM({k['BODYDECAY']}, I4)")
    a(f"F4 = F4 * F5, R5 = DM({k['TRANSDECAY']}, I4)", "body_env_ *= body_env_decay")
    a(f"R6 = DM({s['te']}, I1)")
    a(f"F6 = F6 * F5, DM({s['be']}, I1) = R4", "transient_env_ *= transient_env_decay")
    a(f"DM({s['te']}, I1) = R6")
    a.label("_kb_bedone")
    one_pole_mem(a, "belp", "be", "C01")
    one_pole_mem(a, "telp", "te", "C01")
    one_pole_mem(a, "fmlp", "fm", "C01")
    # body = DistortedSine(phase_, phase_noise_, dirtiness)
    a(f"R4 = DM({s['pn']}, I1)")
    a(f"R5 = DM({k['DIRT']}, I4)")
    a(f"F4 = F4 * F5, R6 = DM({k['BIG']}, I4)", "phase_noise * dirtiness")
    a("F4 = F15 + F4", "phase += ..")
    a("R5 = TRUNC F4", "MAKE_INTEGRAL_FRACTIONAL")
    a("R5 = R5 OR R6")
    a("F5 = F5 - F6")
    a(f"F4 = F4 - F5, R7 = DM({k['ONE']}, I4)", "phase")
    a(f"F5 = F7 - F4, R8 = DM({k['HALF']}, I4)", "1.0f - phase")
    a("R9 = R4 OR R4")
    a("COMP(F4, F8)", "phase < 0.5f ?")
    a("IF GE R9 = R5 OR R5")
    a(f"R8 = DM({k['FOUR']}, I4)")
    a("F9 = F9 * F8", "* 4.0f")
    a(f"F9 = F9 - F7, R8 = DM({k['TWO']}, I4)", "triangle")
    a(f"F11 = F8 * F9, R10 = DM({k['ZERO']}, I4)", "2.0f * triangle")
    absf(a, "F12", "F9", "F10")
    a("F12 = F7 + F12", "1.0f + absf(triangle)")
    a(f"R10 = DM({k['C075']}, I4)")
    a("F4 = F4 + F10", "Sine(phase + 0.75f)")
    # sine = 2 t / (1 + |t|): divide with R5/R7/R8/R9/R10 (keep R4 = phase + 0.75)
    a(f"R10 = DM({k['TWO']}, I4)")
    a.div(
        "F11",
        "F11",
        "F12",
        "F9",
        "F8",
        "F10",
        "sine = 2 triangle / (1 + absf(triangle))",
    )
    # clean_sine = InterpolateWrap(sine, phase + 0.75f, 512.0f)
    a("R5 = TRUNC F4")
    a("R5 = R5 OR R6")
    a("F5 = F5 - F6")
    a(f"F4 = F4 - F5, R7 = DM({k['C512']}, I4)", "index -= itof_nonneg((int)index)")
    a("F4 = F4 * F7", "index *= 512.0f")
    a("R5 = TRUNC F4")
    a("R7 = R5 OR R6")
    a("F7 = F7 - F6")
    a("F4 = F4 - F7", "index_fractional")
    a("R7 = 2")
    a("R5 = LSHIFT R5 BY R7")
    a(f"R7 = DM({k['SINE']}, I4)")
    a("R5 = R5 + R7")
    a("I2 = R5")
    a("R7 = DM(0, I2)", "a")
    a("R8 = DM(1, I2)", "b")
    a("F8 = F8 - F7")
    a("F8 = F8 * F4")
    a(f"F7 = F7 + F8, R8 = DM({k['ONEMDIRT']}, I4)", "clean_sine")
    a("F7 = F7 - F11", "clean_sine - sine")
    a("F7 = F8 * F7", "(1.0f - dirtiness) * ..")
    a(f"F11 = F11 + F7, R4 = DM({k['C06']}, I4)", "body")
    # TransistorVCA(body, body_env_lp_)
    a(f"R5 = DM({s['belp']}, I1)")
    a("F11 = F11 - F4", "s - 0.6f")
    a(f"F11 = F11 * F5, R4 = DM({k['THREE']}, I4)", "* gain")
    a(f"F9 = F4 * F11, R10 = DM({k['ZERO']}, I4)", "3.0f * s")
    absf(a, "F12", "F11", "F10")
    a(f"R4 = DM({k['TWO']}, I4)")
    a("F12 = F4 + F12", "2.0f + absf(s)")
    a.div("F9", "F9", "F12", "F11", "F8", "F4", "3 s / (2 + absf(s))")
    a(f"R4 = DM({k['C03']}, I4)")
    a("F5 = F5 * F4", "gain * 0.3f")
    a("F9 = F9 + F5", "TransistorVCA")
    a("F9 = F10 - F9", "mix = 0.0f - VCA")
    # transient = click_process(&click_, body_env_pulse_width_ ? 0.0f : 1.0f)
    a(f"R4 = DM({k['ONE']}, I4)")
    a("R1 = PASS R1")
    a("IF NE R4 = R10 OR R10", "body_env_pulse_width_ ? 0.0f : 1.0f")
    a(f"R5 = DM({s['clp']}, I1)")
    a(f"F4 = F4 - F5, R6 = DM({k['C01']}, I4)", "SLOPE: error")
    a("COMP(F4, F10)")
    a(f"R7 = DM({k['HALF']}, I4)")
    a("IF GT R6 = R7 OR R7", "error > 0.0f ? 0.5f : 0.1f")
    a("F6 = F6 * F4")
    a(f"F5 = F5 + F6, R6 = DM({s['chp']}, I1)", "click lp_")
    a(f"F7 = F5 - F6, R8 = DM({k['C04']}, I4)", "ONE_POLE(hp_, lp_, 0.04f)")
    a(f"F7 = F8 * F7, DM({s['clp']}, I1) = R5")
    a("F6 = F6 + F7")
    a(f"F4 = F5 - F6, DM({s['chp']}, I1) = R6", "svf in = lp_ - hp_")
    a(f"R7 = DM({s['cr']}, I1)")
    a(f"F8 = F7 * F2, R7 = DM({s['cg']}, I1)", "r * state_1")
    a("F4 = F4 - F8")
    a("F8 = F7 * F2", "g * state_1")
    a(f"F4 = F4 - F8, R8 = DM({s['ch']}, I1)")
    a("F4 = F4 - F3")
    a("F4 = F4 * F8", "h")
    a("F4 = F7 * F4", "g * h")
    a("F5 = F4 + F2", "b")
    a("F2 = F4 + F5", "state_1")
    a("F5 = F7 * F5", "g * b")
    a("F11 = F5 + F3", "lp: transient")
    a("F3 = F5 + F11", "state_2")
    # noise (mix and transient wait in k's scratch words)
    a(f"DM({k['T_MIX']}, I4) = R9")
    a(f"DM({k['T_TRANS']}, I4) = R11")
    rng_float(a, "F12", k)
    one_pole_mem(a, "nlp", "", "C005", "F12")
    a(f"R12 = DM({s['nhp']}, I1)")
    a(
        f"F4 = F10 - F12, R5 = DM({k['C0005']}, I4)",
        "ONE_POLE(noise_hp_, noise_lp_, 0.005f)",
    )
    a("F4 = F5 * F4")
    a("F12 = F12 + F4")
    a(f"F4 = F10 - F12, DM({s['nhp']}, I1) = R12", "noise")
    a(f"R11 = DM({k['T_TRANS']}, I4)")
    a(f"F11 = F11 + F4, R5 = DM({s['telp']}, I1)", "transient += noise")
    a(f"F11 = F11 * F5, R5 = DM({k['TLEVEL']}, I4)", "transient * transient_env_lp_")
    a(f"F11 = F11 * F5, R9 = DM({k['T_MIX']}, I4)", "* transient_level")
    a(f"F9 = F9 - F11, R10 = DM({s['tone']}, I1)", "mix -=")
    a(f"F9 = F9 - F10, R5 = DM({k['TONEF']}, I4)", "ONE_POLE(tone_lp_, mix, tone_f)")
    a("F9 = F5 * F9")
    a("F10 = F10 + F9")
    a(f"DM({s['tone']}, I1) = R10")
    a.label("_kb_end")
    a("DM(I0, M6) = R10")
    a.label("_kb_done")
    a(f"DM({s['f0']}, I1) = R14", "commit(&f0_mod)")
    a(f"DM({s['phase']}, I1) = R15")
    a(f"DM({s['fmpw']}, I1) = R0")
    a(f"DM({s['bepw']}, I1) = R1")
    a(f"DM({s['cs1']}, I1) = R2")
    a(f"DM({s['cs2']}, I1) = R3")
    a("I2 = DM(3, I6)")
    a("DM(0, I2) = R13")
    a.epilogue()
    return a.text()


# kick_overdrive_kernel(float *k, float *in_out, int size): overdrive_process's loop; k[0..3] = pre value, increment,
# post value, increment (the values advance in place: the C commits them).
KO = {
    n: i
    for i, n in enumerate(
        ("PRE", "PREI", "POST", "POSTI", "M3", "P3", "M1", "P1", "C27", "C9", "TWO")
    )
}


def soft_clip(a: Asm, x: str, k: dict[str, int]) -> None:
    """x = SoftClip(x) (drums.h): x < -3 ? -1 : x > 3 ? 1 : x (27 + x x) / (27 + 9 x x). Uses R4, R8-R12."""
    a(f"F8 = {x} * {x}, R9 = DM({k['C27']}, I4)", "x * x")
    a(f"F8 = F9 + F8, R10 = DM({k['C9']}, I4)", "27.0f + x * x")
    a(f"F8 = {x} * F8", "x * (..)")
    a(f"F10 = F10 * {x}", "9.0f * x")
    a(f"F10 = F10 * {x}, R4 = DM({k['TWO']}, I4)", "* x")
    a("F10 = F9 + F10", "27.0f + 9 x x")
    a.div("F9", "F8", "F10", "F11", "F12", "F4", "SoftLimit")
    a(f"R10 = DM({k['P3']}, I4)")
    a(f"COMP({x}, F10)", "x > 3.0f")
    a(f"R10 = DM({k['P1']}, I4)")
    a("IF GT R9 = R10 OR R10")
    a(f"R10 = DM({k['M3']}, I4)")
    a(f"COMP({x}, F10)", "x < -3.0f (first in the C)")
    a(f"R10 = DM({k['M1']}, I4)")
    a("IF LT R9 = R10 OR R10")


def kick_overdrive() -> str:
    a = Asm("kick_overdrive_kernel")
    k = KO
    a.raw("")
    a.raw(
        "// ---- kick_overdrive_kernel(float *k, float *in_out, int size): overdrive_process's loop"
    )
    a.raw(
        "// R0/R1 pre value/increment, R2/R3 post value/increment; I4 = k, I0 = in_out"
    )
    a.prologue()
    a("I4 = R4")
    a("I0 = R8")
    a(f"R0 = DM({k['PRE']}, I4)")
    a(f"R1 = DM({k['PREI']}, I4)")
    a(f"R2 = DM({k['POST']}, I4)")
    a(f"R3 = DM({k['POSTI']}, I4)")
    a("R5 = 0")
    a("COMP(R12, R5)")
    a("IF LE JUMP _ko_done")
    a("LCNTR = R12, DO _ko_end UNTIL LCE")
    a("F0 = F0 + F1, R5 = DM(0, I0)", "next(&pre_mod); in_out[n]")
    a("F5 = F0 * F5", "pre")
    soft_clip(a, "F5", k)
    a("F2 = F2 + F3", "next(&post_mod)")
    a("F9 = F9 * F2")
    a.label("_ko_end")
    a("DM(I0, M6) = R9")
    a.label("_ko_done")
    a(f"DM({k['PRE']}, I4) = R0")
    a(f"DM({k['POST']}, I4) = R2")
    a.epilogue()
    return a.text()


# ---------------------------------------------------------------------------------------------------------- SNARE


def svf_mem(a: Asm, base: str, o: int, x: str) -> None:
    """svf_step on the Svf at word o of `base` with the input in x (not R4-R10): b in F4, lp in F8, the states
    stored back. Uses R4-R10."""
    a(f"R4 = DM({o + 1}, {base})", "svf_step: r")
    a(f"R5 = DM({o + 3}, {base})", "state_1")
    a(f"F9 = F4 * F5, R6 = DM({o}, {base})", "r * state_1; g")
    a(f"F10 = {x} - F9, R7 = DM({o + 4}, {base})", "; state_2")
    a(f"F9 = F6 * F5, R8 = DM({o + 2}, {base})", "g * state_1; h")
    a("F10 = F10 - F9")
    a("F10 = F10 - F7")
    a("F10 = F10 * F8", "hp")
    a("F9 = F6 * F10", "g * hp")
    a("F4 = F9 + F5", "bp")
    a("F5 = F9 + F4", "state_1")
    a("F9 = F6 * F4", "g * bp")
    a("F8 = F9 + F7", "lp")
    a(f"F7 = F9 + F8, DM({o + 3}, {base}) = R5", "state_2")
    a(f"DM({o + 4}, {base}) = R7")


# snare_analog_kernel(AnalogSnareDrum *s, float *k, float *out, int size, unsigned int *rng)
KSA = {
    n: i
    for i, n in enumerate(
        (
            "KPD",
            "C075",
            "C0006",
            "C0026",
            "LEAK",
            "ZERO",
            "G0",
            "G1",
            "G2",
            "G3",
            "G4",
            "TWO",
            "ONE",
            "NED",
            "SNAP",
            "ONEMSNAP",
            "BIG",
            "C65536",
            "Y32",
            "D32",
            "PH",
            "PH1",
            "C27",
            "C9",
            "M3",
            "P3",
            "M1",
            "P1",
        )
    )
}
SSA = {
    "prs": -5,
    "pulse": -4,
    "ph": -3,
    "plp": -2,
    "nenv": -1,
    "res": 0,
    "noise": 25,
}  # words from resonator_[0]


def snare_analog() -> str:
    a = Asm("snare_analog_kernel")
    k, s = KSA, SSA
    a.raw("")
    a.raw(
        "// ---- snare_analog_kernel(AnalogSnareDrum *s, float *k, float *out, int size, unsigned int *rng):"
    )
    a.raw(
        "// analog_render's loop. R13 rng, R14 pulse_remaining_samples_, R15 pulse_, R0 pulse_lp_, R1"
    )
    a.raw(
        "// noise_envelope_, R2 shell, R3 the excitation, R11 the modes' excitation 0.026f * pulse; the filters stay"
    )
    a.raw("// in *s (I1 = &resonator_[0]); I4 = k, I0 = out")
    a.prologue()
    a("I1 = R4")
    a("I1=MODIFY (I1,5)(NW)")
    a("I4 = R8")
    a("I0 = R12")
    recip_const(a, k, "D32", "Y32")
    a("I2 = DM(2, I6)", "rng")
    a("R13 = DM(0, I2)")
    a(f"R14 = DM({s['prs']}, I1)")
    a(f"R15 = DM({s['pulse']}, I1)")
    a(f"R0 = DM({s['plp']}, I1)")
    a(f"R1 = DM({s['nenv']}, I1)")
    a("R4 = DM(1, I6)", "size")
    a("R5 = 0")
    a("COMP(R4, R5)")
    a("IF LE JUMP _sa_done")
    a("LCNTR = R4, DO _sa_end UNTIL LCE")
    a(f"R14 = PASS R14, R9 = DM({k['KPD']}, I4)", "pulse_remaining_samples_ ?")
    a("IF EQ JUMP _sa_pdecay")
    a(f"R14 = R14 - 1, R15 = DM({k['PH']}, I4)", "--; pulse = pulse_height_")
    a("IF NE JUMP _sa_pb")
    a(f"R15 = DM({k['PH1']}, I4)", "pulse_height_ - 1.0f")
    a("JUMP _sa_pb")
    a.label("_sa_pdecay")
    a("F15 = F15 * F9", "pulse_ *= 1 - 1/kPulseDecayTime")
    a.label("_sa_pb")
    a(f"F9 = F15 - F0, R10 = DM({k['C075']}, I4)", "ONE_POLE(pulse_lp_, pulse, 0.75f)")
    a("F9 = F10 * F9")
    a(f"F0 = F0 + F9, R10 = DM({k['C0006']}, I4)")
    a("F10 = F10 * F15", "0.006f * pulse")
    a("F9 = F15 - F0", "pulse - pulse_lp_")
    a(f"F3 = F9 + F10, R10 = DM({k['C0026']}, I4)", "excitation 0")
    a(f"F11 = F10 * F15, R2 = DM({k['ZERO']}, I4)", "0.026f * pulse; shell = 0.0f")
    for i in range(5):
        if i == 1:
            a("R3 = PASS R11", "excitation 1..4")
        svf_mem(a, "I1", s["res"] + 5 * i, "F3")
        a(f"R9 = DM({k['LEAK']}, I4)")
        a(f"F9 = F3 * F9, R10 = DM({k['G%d' % i]}, I4)", "excitation * exciter_leak")
        a("F9 = F4 + F9", "bp + ..")
        a("F9 = F10 * F9", "gain * (..)")
        a("F2 = F2 + F9", "shell +=")
    soft_clip(a, "F2", k)
    a("R2 = PASS R9", "shell = SoftClip(shell)")
    rng_float(a, "F9", k)
    a(f"R10 = DM({k['TWO']}, I4)")
    a(f"F9 = F10 * F9, R10 = DM({k['ONE']}, I4)", "2.0f * rng_float")
    a(f"F9 = F9 - F10, R10 = DM({k['ZERO']}, I4)", "- 1.0f")
    a("COMP(F9, F10)")
    a("IF LT R9 = R10 OR R10", "noise < 0.0f: 0.0f")
    a(f"R10 = DM({k['NED']}, I4)")
    a(
        f"F1 = F1 * F10, R10 = DM({k['SNAP']}, I4)",
        "noise_envelope_ *= noise_envelope_decay",
    )
    a(f"F10 = F1 * F10, R11 = DM({k['TWO']}, I4)", "noise_envelope_ * snappy")
    a("F10 = F10 * F11", "* 2.0f")
    a("F3 = F9 * F10", "noise *=")
    svf_mem(a, "I1", s["noise"], "F3")
    a(f"R9 = DM({k['ONEMSNAP']}, I4)")
    a("F9 = F2 * F9", "shell * (1.0f - snappy)")
    a("F9 = F4 + F9", "noise + ..")
    a.label("_sa_end")
    a("DM(I0, M6) = R9")
    a.label("_sa_done")
    a(f"DM({s['prs']}, I1) = R14")
    a(f"DM({s['pulse']}, I1) = R15")
    a(f"DM({s['plp']}, I1) = R0")
    a(f"DM({s['nenv']}, I1) = R1")
    a("I2 = DM(2, I6)")
    a("DM(0, I2) = R13")
    a.epilogue()
    return a.text()


# snare_synth_kernel(SyntheticSnareDrum *s, float *k, float *out, int size, unsigned int *rng)
KST = {
    n: i
    for i, n in enumerate(
        (
            "DRUMDECAY",
            "SNAREDECAY",
            "FMDECAY",
            "C003",
            "ONE",
            "HALF",
            "MONE",
            "RNA025",
            "F0",
            "FMA",
            "FOUR",
            "C147",
            "C13",
            "TWO",
            "ZERO",
            "C06",
            "C025",
            "DLEVEL",
            "C01",
            "SLEVEL",
            "BIG",
            "C65536",
            "Y32",
            "D32",
            "RNA",
            "M01",
        )
    )
}
SST = {
    n: i
    for i, n in enumerate(
        (
            "p0",
            "p1",
            "damp",
            "samp",
            "fm",
            "hold",
            "dg",
            "dgi",
            "dst",
            "hg",
            "hgi",
            "hst",
            "svf",
        )
    )
}


def snare_ds(a: Asm, p: str, k: dict[str, int]) -> None:
    """F9 = DistortedSine(phase in register p) (snare.c): t = (p < 0.5f ? p : 1.0f - p) * 4.0f - 1.3f;
    2.0f * t / (1.0f + absf(t)). Uses R4-R12."""
    a(f"R5 = DM({k['ONE']}, I4)")
    a(f"F5 = F5 - {p}, R6 = DM({k['HALF']}, I4)", "1.0f - phase")
    a(f"R7 = R{p[1:]} OR R{p[1:]}")
    a(f"COMP({p}, F6)", "phase < 0.5f ?")
    a("IF GE R7 = R5 OR R5")
    a(f"R6 = DM({k['FOUR']}, I4)")
    a(f"F7 = F7 * F6, R6 = DM({k['C13']}, I4)", "* 4.0f")
    a(f"F7 = F7 - F6, R6 = DM({k['TWO']}, I4)", "- 1.3f: triangle")
    a(f"F11 = F6 * F7, R10 = DM({k['ZERO']}, I4)", "2.0f * triangle")
    absf(a, "F12", "F7", "F10")
    a(f"R4 = DM({k['ONE']}, I4)")
    a("F12 = F4 + F12", "1.0f + absf(triangle)")
    a("R10 = R6 OR R6", "2.0f")
    a.div("F9", "F11", "F12", "F5", "F8", "F10", "DistortedSine")


def snare_synth() -> str:
    a = Asm("snare_synth_kernel")
    k, s = KST, SST
    a.raw("")
    a.raw(
        "// ---- snare_synth_kernel(SyntheticSnareDrum *s, float *k, float *out, int size, unsigned int *rng):"
    )
    a.raw(
        "// synthetic_render's loop. R13 rng, R14 n (counting down as the C's), R15 hold_counter_, R0 drum_amplitude_,"
    )
    a.raw(
        "// R1 snare_amplitude_, R2 fm_, R3 drum; phases and filters stay in *s (I1); I4 = k, I0 = out"
    )
    a.prologue()
    a("I1 = R4")
    a("I4 = R8")
    a("I0 = R12")
    recip_const(a, k, "D32", "Y32")
    a("I2 = DM(2, I6)", "rng")
    a("R13 = DM(0, I2)")
    a(f"R15 = DM({s['hold']}, I1)")
    a(f"R0 = DM({s['damp']}, I1)")
    a(f"R1 = DM({s['samp']}, I1)")
    a(f"R2 = DM({s['fm']}, I1)")
    a("R14 = DM(1, I6)", "size")
    a("R5 = 0")
    a("COMP(R14, R5)")
    a("IF LE JUMP _ss_done")
    a("LCNTR = R14, DO _ss_end UNTIL LCE")
    a("R14 = R14 - 1", "n (the C's loop variable)")
    a(f"R4 = DM({k['ONE']}, I4)")
    a(f"R5 = DM({k['DRUMDECAY']}, I4)")
    a("R6 = 1")
    a("R6 = R14 AND R6", "!(n & 1) ?")
    a("IF EQ R4 = R5 OR R5")
    a(f"R6 = DM({k['C003']}, I4)")
    a("COMP(F0, F6)", "drum_amplitude_ > 0.03f ?")
    a("IF GT R4 = R5 OR R5")
    a("F0 = F0 * F4", "drum_amplitude_ *=")
    a("R15 = PASS R15", "hold_counter_ ?")
    a("IF EQ JUMP _ss_decay")
    a("R15 = R15 - 1")
    a("JUMP _ss_held")
    a.label("_ss_decay")
    a(f"R4 = DM({k['SNAREDECAY']}, I4)")
    a("F1 = F1 * F4", "snare_amplitude_ *= snare_decay")
    a.label("_ss_held")
    a(f"R4 = DM({k['FMDECAY']}, I4)")
    a(f"F2 = F2 * F4, R4 = DM({s['p0']}, I1)", "fm_ *= fm_decay; phase_[0]")
    # reset_noise
    a(f"R6 = DM({k['ONE']}, I4)")
    a(f"R7 = DM({k['MONE']}, I4)")
    a(f"R8 = DM({k['HALF']}, I4)")
    a("COMP(F4, F8)", "phase_[0] > 0.5f ? -1.0f : 1.0f")
    a("R9 = R6 OR R6")
    a(f"R5 = DM({s['p1']}, I1)")
    a("COMP(F4, F8)")
    a("IF GT R9 = R7 OR R7")
    a(f"R10 = DM({k['ZERO']}, I4)")
    a("F9 = F10 + F9", "reset_noise = 0.0f + ..")
    a("R10 = R6 OR R6")
    a("COMP(F5, F8)", "phase_[1] > 0.5f ?")
    a("IF GT R10 = R7 OR R7")
    a(f"F9 = F9 + F10, R10 = DM({k['RNA025']}, I4)")
    a("F9 = F9 * F10", "reset_noise *= reset_noise_amount * 0.025f")
    # f and the phases
    a(f"R10 = DM({k['FOUR']}, I4)")
    a(f"F10 = F10 * F2, R11 = DM({k['FMA']}, I4)", "4.0f * fm_")
    a("F10 = F11 * F10", "fm_amount * (..)")
    a(f"F10 = F6 + F10, R11 = DM({k['F0']}, I4)", "1.0f + ..")
    a(f"F10 = F11 * F10, R11 = DM({k['C147']}, I4)", "f")
    a("F4 = F4 + F10", "phase_[0] += f")
    a("F11 = F10 * F11", "f * 1.47f")
    a(f"F5 = F5 + F11, R10 = DM({k['RNA']}, I4)", "phase_[1] +=")
    a(f"R11 = DM({k['C01']}, I4)")
    a("COMP(F10, F11)", "reset_noise_amount > 0.1f ?")
    a("IF LE JUMP _ss_wrap")
    a("F10 = F6 + F9", "1.0f + reset_noise")
    a("F11 = F6 - F4", "1.0f - phase_[0]")
    a("COMP(F4, F10)")
    a("IF GE R4 = R11 OR R11")
    a("F11 = F6 - F5")
    a("COMP(F5, F10)")
    a("IF GE R5 = R11 OR R11")
    a("JUMP _ss_wrapped")
    a.label("_ss_wrap")
    a("COMP(F4, F6)", "phase_[i] >= 1.0f: -= 1.0f")
    a("IF GE F4 = F4 - F6")
    a("COMP(F5, F6)")
    a("IF GE F5 = F5 - F6")
    a.label("_ss_wrapped")
    a(f"DM({s['p0']}, I1) = R4")
    a(f"DM({s['p1']}, I1) = R5")
    # drum
    snare_ds(a, "F4", k)
    a(f"R10 = DM({k['C06']}, I4)")
    a(
        f"F9 = F9 * F10, R3 = DM({k['M01']}, I4)",
        "DistortedSine(phase_[0]) * 0.60f; drum = -0.1f",
    )
    a(f"F3 = F3 + F9, R4 = DM({s['p1']}, I1)")
    snare_ds(a, "F4", k)
    a(f"R10 = DM({k['C025']}, I4)")
    a(f"F9 = F9 * F10, R10 = DM({k['DLEVEL']}, I4)", "* 0.25f")
    a("F3 = F3 + F9")
    a("F10 = F0 * F10", "drum_amplitude_ * drum_level")
    a(f"F3 = F3 * F10, R4 = DM({s['dg']}, I1)", "drum *=")
    a(f"F4 = F4 * F3, R5 = DM({s['dst']}, I1)", "one_pole_low_pass: g * in")
    a(f"F4 = F4 + F5, R6 = DM({s['dgi']}, I1)", "+ state")
    a(f"F4 = F4 * F6, R5 = DM({s['dg']}, I1)", "lp")
    a("F6 = F3 - F4", "in - lp")
    a("F6 = F5 * F6", "g * ..")
    a("F6 = F6 + F4", "+ lp")
    a(f"R3 = PASS R4, DM({s['dst']}, I1) = R6", "drum")
    # snare
    rng_float(a, "F11", k)
    svf_mem(a, "I1", s["svf"], "F11")
    a(f"R4 = DM({s['hg']}, I1)")
    a(f"F4 = F4 * F8, R5 = DM({s['hst']}, I1)", "one_pole_high_pass: g * in")
    a(f"F4 = F4 + F5, R6 = DM({s['hgi']}, I1)")
    a(f"F4 = F4 * F6, R5 = DM({s['hg']}, I1)", "lp")
    a("F6 = F8 - F4", "in - lp")
    a("F6 = F5 * F6")
    a("F6 = F6 + F4")
    a(f"F8 = F8 - F4, DM({s['hst']}, I1) = R6", "snare = in - lp")
    a(f"R4 = DM({k['C01']}, I4)")
    a("F8 = F8 + F4", "snare + 0.1f")
    a(f"F4 = F1 + F2, R5 = DM({k['SLEVEL']}, I4)", "snare_amplitude_ + fm_")
    a("F8 = F8 * F4")
    a("F8 = F8 * F5", "* snare_level")
    a("F8 = F8 + F3", "snare + drum")
    a.label("_ss_end")
    a("DM(I0, M6) = R8")
    a.label("_ss_done")
    a(f"DM({s['hold']}, I1) = R15")
    a(f"DM({s['damp']}, I1) = R0")
    a(f"DM({s['samp']}, I1) = R1")
    a(f"DM({s['fm']}, I1) = R2")
    a("I2 = DM(2, I6)")
    a("DM(0, I2) = R13")
    a.epilogue()
    return a.text()


# ---------------------------------------------------------------------------------------------------------- HIHAT

# hat_square_noise_kernel(unsigned int *phase, const unsigned int *increment, float *out, int size, const float *k)
KQ = {"BIG": 0, "C033": 1, "ONE": 2}


def hat_square_noise() -> str:
    a = Asm("hat_square_noise_kernel")
    k = KQ
    ph = ("R0", "R1", "R2", "R3", "R13", "R14")
    inc = ("R15", "R9", "R10", "R11", "R12", "R7")
    a.raw("")
    a.raw(
        "// ---- hat_square_noise_kernel(unsigned int *phase, const unsigned int *increment, float *out, int size,"
    )
    a.raw(
        "// const float *k): SquareNoise::Render's loop. Phases in R0-R3, R13, R14, increments in R15, R9-R12, R7"
    )
    a.prologue()
    a("I1 = R4")
    a("I2 = R8")
    a("I0 = R12")
    a("I4 = DM(2, I6)", "k")
    for i in range(6):
        a(f"{ph[i]} = DM({i}, I1)")
        a(f"{inc[i]} = DM({i}, I2)")
    a("R4 = DM(1, I6)", "size")
    a("R5 = 0")
    a("COMP(R4, R5)")
    a("IF LE JUMP _hq_done")
    a("LCNTR = R4, DO _hq_end UNTIL LCE")
    for i in range(6):
        a(f"{ph[i]} = {ph[i]} + {inc[i]}", f"phase[{i}] += increment[{i}]")
    a(f"R4 = FEXT {ph[0]} BY 31:1", "noise += phase >> 31")
    for i in range(1, 6):
        a(f"R5 = FEXT {ph[i]} BY 31:1")
        a("R4 = R4 + R5")
    a(f"R5 = DM({k['BIG']}, I4)")
    a(f"R4 = R4 OR R5, R6 = DM({k['C033']}, I4)", "itof_nonneg(noise)")
    a("F4 = F4 - F5")
    a(f"F4 = F6 * F4, R6 = DM({k['ONE']}, I4)", "0.33f * ..")
    a("F4 = F4 - F6", "- 1.0f")
    a.label("_hq_end")
    a("DM(I0, M6) = R4")
    a.label("_hq_done")
    for i in range(6):
        a(f"DM({i}, I1) = {ph[i]}")
    a.epilogue()
    return a.text()


# hat_osc_kernel(HatOscillator *o, const float *k, float *out, int size, const float *t1): Oscillator::Render's loop,
# square (t1 = 0: out[n] = the square) or saw (out[n] += t1[n] * the saw, RingModNoise's product); k: KH.
KH = {"FINC": 0, "WINC": 1, "ONE": 2, "HALF": 3, "MHALF": 4, "TWO": 5, "ZERO": 6}
HO = {"phase": 0, "next": 1, "high": 4, "freq": 5, "pw": 6}


def blep(a: Asm, t: str, k: dict[str, int]) -> None:
    """F11 = ThisBlepSample(t) = 0.5f * t * t, F12 = NextBlepSample(t) = -0.5f * (1 - t) * (1 - t). Uses R8."""
    a(f"R8 = DM({k['HALF']}, I4)")
    a(f"F11 = F8 * {t}, R8 = DM({k['ONE']}, I4)", "ThisBlepSample: 0.5f * t")
    a(f"F11 = F11 * {t}", "* t")
    a(f"F12 = F8 - {t}, R8 = DM({k['MHALF']}, I4)", "NextBlepSample: t = 1.0f - t")
    a("F8 = F8 * F12", "-0.5f * t")
    a("F12 = F8 * F12", "* t")


def hat_osc(saw: bool) -> str:
    name = "hat_saw_kernel" if saw else "hat_square_kernel"
    tag = "_hw" if saw else "_hs"
    a = Asm(name)
    k, o = KH, HO
    a.raw("")
    a.raw(
        f"// ---- {name}(HatOscillator *o, const float *k, float *out, int size, const float *t1):"
    )
    a.raw(
        "// R0 phase_, R1 next_sample, R2 high_, R3/R13 frequency value/increment, R14/R15 pw value/increment;"
    )
    a.raw(
        "// I1 = o, I4 = k, I0 = out"
        + (", I2 = t1 (out[n] += t1[n] * saw)" if saw else "")
    )
    a.prologue()
    a("I1 = R4")
    a("I4 = R8")
    a("I0 = R12")
    if saw:
        a("I2 = DM(2, I6)", "t1")
    a(f"R0 = DM({o['phase']}, I1)")
    a(f"R1 = DM({o['next']}, I1)")
    a(f"R2 = DM({o['high']}, I1)")
    a(f"R3 = DM({o['freq']}, I1)")
    a(f"R13 = DM({k['FINC']}, I4)")
    a(f"R14 = DM({o['pw']}, I1)")
    a(f"R15 = DM({k['WINC']}, I4)")
    a("R4 = DM(1, I6)", "size")
    a("R5 = 0")
    a("COMP(R4, R5)")
    a(f"IF LE JUMP {tag}_done")
    a(f"LCNTR = R4, DO {tag}_end UNTIL LCE")
    a(
        f"R4 = PASS R1, R1 = DM({k['ZERO']}, I4)",
        "this_sample = next_sample; next_sample = 0.0f",
    )
    a("F3 = F3 + F13", "f = next(&fm)")
    a("F14 = F14 + F15", "w = next(&pwm)")
    a("F0 = F0 + F3", "phase_ += f")
    if not saw:
        a("R5 = 0")
        a("R6 = 1")
        a("COMP(F0, F14)", "phase_ >= w")
        a("IF GE R5 = R6 OR R6")
        a("COMP(R5, R2)", "high_ ^ (..) == 0 ?")
        a(f"IF EQ JUMP {tag}_noedge")
        a("R2 = PASS R5", "high_ = phase_ >= w")
        a(f"F9 = F0 - F14, R10 = DM({k['TWO']}, I4)", "phase_ - w")
        a.div("F9", "F9", "F3", "F6", "F7", "F10", "t = (phase_ - w) / f")
        blep(a, "F9", k)
        a(f"R8 = DM({k['ONE']}, I4)")
        a("F11 = F11 * F8", "ThisBlepSample(t) * 1.0f")
        a("F4 = F4 + F11", "this_sample +=")
        a("F12 = F12 * F8", "NextBlepSample(t) * 1.0f")
        a("F1 = F1 + F12", "next_sample +=")
        a.label(f"{tag}_noedge")
    a(f"R8 = DM({k['ONE']}, I4)")
    a("COMP(F0, F8)", "phase_ >= 1.0f ?")
    a(f"IF LT JUMP {tag}_nowrap")
    a(f"F0 = F0 - F8, R10 = DM({k['TWO']}, I4)", "phase_ -= 1.0f")
    a.div("F9", "F0", "F3", "F6", "F7", "F10", "t = phase_ / f")
    blep(a, "F9", k)
    a("F4 = F4 - F11", "this_sample -= ThisBlepSample(t)")
    a("F1 = F1 - F12", "next_sample -= NextBlepSample(t)")
    if not saw:
        a("R2 = 0", "high_ = 0")
    a.label(f"{tag}_nowrap")
    if saw:
        a("F1 = F1 + F0", "next_sample += phase_")
    else:
        a(f"R5 = DM({k['ZERO']}, I4)")
        a(f"R6 = DM({k['ONE']}, I4)")
        a("COMP(F0, F14)", "phase_ < w ? 0.0f : 1.0f")
        a("IF GE R5 = R6 OR R6")
        a("F1 = F1 + F5", "next_sample +=")
    a(f"R5 = DM({k['TWO']}, I4)")
    a(f"F4 = F5 * F4, R5 = DM({k['ONE']}, I4)", "2.0f * this_sample")
    a("F4 = F4 - F5", "- 1.0f")
    if saw:
        a("R5 = DM(I2, M6)", "temp_1[n]")
        a("F4 = F5 * F4, R5 = DM(0, I0)", "temp_1[n] * temp_2[n]; out[n]")
        a("F4 = F5 + F4", "out[n] +=")
    a.label(f"{tag}_end")
    a("DM(I0, M6) = R4")
    a.label(f"{tag}_done")
    a(f"DM({o['phase']}, I1) = R0")
    a(f"DM({o['next']}, I1) = R1")
    if not saw:
        a(f"DM({o['high']}, I1) = R2")
    a(f"DM({o['freq']}, I1) = R3", "commit(&fm)")
    a(f"DM({o['pw']}, I1) = R14", "commit(&pwm)")
    a.epilogue()
    return a.text()


# hat_tail_kernel{0,1}(HiHat *s, float *k, float *out, int size, unsigned int *rng): hat_render after the metallic
# noise, fused per sample (each stage keeps its own state, so the per-value operations are the loops'): the coloration
# band pass, the noise mix, the VCA (SwingVCA for kind 0, LinearVCA for kind 1) and the high pass.
KT = {
    n: i
    for i, n in enumerate(
        (
            "NOISEF",
            "ONE",
            "HALF",
            "NOISINESS",
            "ENVDECAY",
            "CUTDECAY",
            "ZERO",
            "FOUR",
            "C01",
            "TWO",
            "BIG",
            "C65536",
            "Y32",
            "D32",
        )
    )
}
HT = {"env": 0, "clock": 1, "ns": 2, "col": 3, "hpf": 8}


def hat_tail(kind: int) -> str:
    name = f"hat_tail_kernel{kind}"
    tag = f"_ht{kind}"
    a = Asm(name)
    k, s = KT, HT
    a.raw("")
    a.raw(
        f"// ---- {name}(HiHat *s, float *k, float *out, int size, unsigned int *rng): hat_render's loops after"
    )
    a.raw(
        "// the noise source, fused per sample. R13 rng, R14 envelope_, R15 noise_clock_, R0 noise_sample_, R1/R2"
    )
    a.raw(
        "// the coloration states, R3/R11 the high pass states; I1 = s, I4 = k, I0 = out"
    )
    a.prologue()
    a("I1 = R4")
    a("I4 = R8")
    a("I0 = R12")
    recip_const(a, k, "D32", "Y32")
    a("I2 = DM(2, I6)", "rng")
    a("R13 = DM(0, I2)")
    a(f"R14 = DM({s['env']}, I1)")
    a(f"R15 = DM({s['clock']}, I1)")
    a(f"R0 = DM({s['ns']}, I1)")
    c, h = s["col"], s["hpf"]
    a(f"R1 = DM({c + 3}, I1)")
    a(f"R2 = DM({c + 4}, I1)")
    a(f"R3 = DM({h + 3}, I1)")
    a(f"R11 = DM({h + 4}, I1)")
    a("R4 = DM(1, I6)", "size")
    a("R5 = 0")
    a("COMP(R4, R5)")
    a(f"IF LE JUMP {tag}_done")
    a(f"LCNTR = R4, DO {tag}_end UNTIL LCE")
    # svf_band_pass(&noise_coloration_svf_, out, out)
    a(f"R5 = DM({c + 1}, I1)", "band pass: r")
    a("F9 = F5 * F1, R4 = DM(0, I0)", "r * state_1; in")
    a(f"F10 = F4 - F9, R6 = DM({c}, I1)", "; g")
    a(f"F9 = F6 * F1, R7 = DM({c + 2}, I1)", "g * state_1; h")
    a("F10 = F10 - F9")
    a("F10 = F10 - F2")
    a("F10 = F10 * F7", "hp")
    a("F9 = F6 * F10", "g * hp")
    a("F12 = F9 + F1", "bp: out[n]")
    a("F1 = F9 + F12", "state_1")
    a("F9 = F6 * F12", "g * bp")
    a("F8 = F9 + F2", "lp")
    a(f"F2 = F9 + F8, R5 = DM({k['NOISEF']}, I4)", "state_2")
    # noise
    a(f"F15 = F15 + F5, R5 = DM({k['ONE']}, I4)", "noise_clock_ += noise_f")
    a("COMP(F15, F5)", "noise_clock_ >= 1.0f ?")
    a(f"IF LT JUMP {tag}_noclk")
    a("F15 = F15 - F5")
    rng_float(a, "F9", k)
    a(f"R10 = DM({k['HALF']}, I4)")
    a("F0 = F9 - F10", "noise_sample_ = rng_float - 0.5f")
    a.label(f"{tag}_noclk")
    a(f"F5 = F0 - F12, R6 = DM({k['NOISINESS']}, I4)", "noise_sample_ - out[n]")
    a("F5 = F6 * F5")
    a(f"F12 = F12 + F5, R5 = DM({k['ENVDECAY']}, I4)", "out[n] += noisiness * (..)")
    # VCA
    if kind == 1:
        a(f"R6 = DM({k['HALF']}, I4)")
        a(f"R7 = DM({k['CUTDECAY']}, I4)")
        a("COMP(F14, F6)", "envelope_ > 0.5f ?")
        a("IF LE R5 = R7 OR R7")
        a("F14 = F14 * F5", "envelope_ *=")
        a("F12 = F12 * F14", "LinearVCA")
    else:
        a(f"F14 = F14 * F5, R5 = DM({k['C01']}, I4)", "envelope_ *= envelope_decay")
        a(f"R6 = DM({k['FOUR']}, I4)")
        a(f"R10 = DM({k['ZERO']}, I4)")
        a("COMP(F12, F10)", "v > 0.0f ? 4.0f : 0.1f")
        a("IF GT R5 = R6 OR R6")
        a("F12 = F12 * F5")
        absf(a, "F7", "F12", "F10")
        a(f"R5 = DM({k['ONE']}, I4)")
        a(f"F7 = F5 + F7, R10 = DM({k['TWO']}, I4)", "1.0f + absf(v)")
        a.div("F12", "F12", "F7", "F5", "F6", "F10", "v / (..)")
        a(f"R5 = DM({k['C01']}, I4)")
        a("F12 = F12 + F5", "v + 0.1f")
        a("F12 = F12 * F14", "* envelope_")
    # svf_high_pass(&hpf_, out, out)
    a(f"R5 = DM({h + 1}, I1)", "high pass: r")
    a(f"F9 = F5 * F3, R6 = DM({h}, I1)", "r * state_1; g")
    a(f"F10 = F12 - F9, R7 = DM({h + 2}, I1)", "; h")
    a("F9 = F6 * F3", "g * state_1")
    a("F10 = F10 - F9")
    a("F10 = F10 - F11")
    a("F10 = F10 * F7", "hp: out[n]")
    a("F9 = F6 * F10", "g * hp")
    a("F12 = F9 + F3", "bp")
    a("F3 = F9 + F12", "state_1")
    a("F9 = F6 * F12", "g * bp")
    a("F8 = F9 + F11", "lp")
    a("F11 = F9 + F8", "state_2")
    a.label(f"{tag}_end")
    a("DM(I0, M6) = R10")
    a.label(f"{tag}_done")
    a(f"DM({s['env']}, I1) = R14")
    a(f"DM({s['clock']}, I1) = R15")
    a(f"DM({s['ns']}, I1) = R0")
    a(f"DM({c + 3}, I1) = R1")
    a(f"DM({c + 4}, I1) = R2")
    a(f"DM({h + 3}, I1) = R3")
    a(f"DM({h + 4}, I1) = R11")
    a("I2 = DM(2, I6)")
    a("DM(0, I2) = R13")
    a.epilogue()
    return a.text()


def build() -> str:
    head = [
        "// machines/plaits: the drum engines' sample-loop kernels (engines/kick.c, snare.c, hihat.c),",
        "// GENERATED by lib/gen_drum_kernels.py; edit the generator.",
        "//",
        "// SISD only (no SIMD mode), bit-identical to selcc's code of the C loops (-DPL_DRUMS_C). State on return:",
        "// R0-R8, R12, I4, I12, LCNTR and the flags change; every other register is as on entry (R9-R11, R13-R15",
        "// saved above I7; I0, I1, I2, M6, L0, L1, L2, L4 pushed through R0, M6 = 1 and L = 0 set here). Assumes",
        "// MODE1.RND32 = 1 (the hook's).",
        "",
        ".SECTION/PM seg_pmco;",
        ".COMPRESS;",
    ]
    return "\n".join(
        head
        + [
            kick_analog(),
            kick_synth(),
            kick_overdrive(),
            snare_analog(),
            snare_synth(),
            hat_square_noise(),
            hat_osc(False),
            hat_osc(True),
            hat_tail(0),
            hat_tail(1),
        ]
    )


def main(argv: list[str]) -> int:
    text = build() + "\n"
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT} is stale: run {Path(__file__).name}")
            return 1
        return 0
    OUT.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
