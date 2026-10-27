"""Generate lib/dx_tables.{c,h} and lib/dx7_banks.{c,h} (the DX7 models, 18 to 20 and 24) from upstream Plaits and the
owner's bank (MIT, Emilie Gillet, for the upstream parts).

Run from the repo root (needs the upstream checkout, ../eurorack by default):
    uv run python modules/machines/plaits/src/dsp/lib/gen_dx_tables.py [path/to/eurorack]

Inputs
  - plaits/dsp/fm/dx_units.cc: lut_coarse, lut_amp_mod_sensitivity, lut_pitch_mod_sensitivity, lut_cube_root (17 entries
    and one guard: upstream's Interpolate reads index 17 at velocity 1.0, multiplied by a fraction of 0);
  - plaits/dsp/fm/dx_units.h: OperatorLevel, PitchEnvelopeLevel, OperatorEnvelopeIncrement, PitchEnvelopeIncrement,
    LFOFrequency and LFODelay, evaluated here for the 100 values of a 0..99 patch field in float32 with the C++
    operation order (numpy float32 rounds every operation, as `-ffp-contract=off`), so the port does not divide or
    multiply integers on the DSP (the encoding gate refuses most integer multiplies, and the DSP's divide is not
    correctly rounded). tests/plaits/test_six_op_host.py compares every entry with upstream's inline functions;
  - plaits/dsp/fm/algorithms.cc: Algorithms<6>::opcodes_ and `Compile`, evaluated here (the macros of the file are
    evaluated, Compile is transcribed) into a table of render calls, and the per-algorithm mask of modulator operators;
    the test compares the table with upstream's compiled Algorithms<6> through the oracle;
  - plaits/resources.cc: syx_bank_0..2 (models 18, 19, 20) and data/tyler-favorites.syx (model 24, the owner's bank: a
    DX7 32-voice bulk dump). The .syx must be exactly 4,104 bytes, start F0 43 0n 09 20 00, carry the 7-bit two's
    complement checksum of the 4,096 data bytes and end F7: the generator fails otherwise.

The patch quantiser (owner decision, 2026-10-23, docs/design/2026-10-22-plaits-dx7-design.md "Patch-name display"): the
DX7 models pick the patch from HARMONICS without memory, so the name on the display is the patch that plays. The
patch of a HARMONICS word w is upstream's HysteresisQuantizer2(32, hysteresis, false) with the hysteresis 0 (the
nominal thresholds), evaluated here in float32 in upstream's operation order (`patch_of_word`); the table holds, for
k = 1..31, the smallest word whose patch is k or more (`pl_dx7_patch_word`) and the same boundary as the float
`unit(word)` the wrapper gives the engine (`pl_dx7_patch_thr`, the DSP's compares). The same words are the ColdFire's
(src/cf/dx7_names.s, with the 4 x 32 names, the bytes 118 to 127 of each voice, trailing spaces trimmed), so the display
and the DSP read one table. tests/plaits/test_six_op_host.py checks every word against upstream's quantiser.

Every float is the IEEE single bit pattern in a `const unsigned int` array read through `PL_F` (drift 2 in PORTING.md).
A bank is 1,024 words, byte j in word j / 4 at bit 8 (j % 4) (the DSP reads a byte with a shift and a mask).
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PLAITS = HERE.parents[2]  # modules/machines/plaits
DEFAULT = HERE.parents[5].parent / "eurorack"
OWNER_SYX = PLAITS / "data" / "tyler-favorites.syx"

F = np.float32


def f32_bits(x: float) -> int:
    return int(struct.unpack("<I", struct.pack("<f", x))[0])


def bits(x: np.float32) -> int:
    return int(np.asarray(x, dtype=np.float32).view(np.uint32))


def dec_f32(text: str) -> int:
    """Bits of the float a C++ compiler makes of the decimal literal `text` (with or without the f suffix): the nearest
    float to the exact decimal value, ties to even (not a double first)."""
    from fractions import Fraction

    exact = Fraction(text.rstrip("fF"))
    x = np.float32(float(exact))
    cands = [x, np.nextafter(x, F(np.inf)), np.nextafter(x, F(-np.inf))]
    best = min(
        cands,
        key=lambda c: (abs(Fraction(float(c)) - exact), int(np.asarray(c, dtype=np.float32).view(np.uint32)) & 1),
    )
    return bits(best)


def body(src: str, ctype: str, name: str) -> list[str]:
    m = re.search(rf"const {ctype} {name}\[\d*\] = \{{(.*?)\}};", src, re.S)
    assert m, name
    return [t for t in re.split(r"[\s,]+", m.group(1)) if t]


def read_syx(path: Path) -> bytes:
    """The 4,096 data bytes of a DX7 32-voice bulk dump; fails closed on a bad header, length or checksum."""
    d = path.read_bytes()
    assert len(d) == 4104, f"{path}: {len(d)} bytes, want 4104"
    assert d[0] == 0xF0 and d[1] == 0x43 and d[2] >> 4 == 0, f"{path}: header {d[:3].hex()}"
    assert d[3:6] == bytes([0x09, 0x20, 0x00]), f"{path}: format/size bytes {d[3:6].hex()}"
    assert d[-1] == 0xF7, f"{path}: no F7"
    data = d[6:4102]
    assert all(b < 0x80 for b in data), f"{path}: a data byte has bit 7 set"
    assert d[4102] == (-sum(data)) & 0x7F, f"{path}: checksum {d[4102]:#x}, want {(-sum(data)) & 0x7F:#x}"
    return data


# ---- dx_units.h, evaluated in float32 in the C++ operation order


def operator_level(level: int) -> int:
    tlc = level
    if level < 20:
        tlc = (tlc * (36 - tlc)) >> 3 if tlc < 15 else 27 + tlc
    else:
        tlc += 28
    return tlc


def pitch_envelope_level(level: int) -> np.float32:
    lv = F(F(level) - F(50.0)) / F(32.0)
    tail = F(abs(F(lv + F(0.02))) - F(1.0))
    tail = tail if not (tail < F(0.0)) else F(0.0)  # std::max(x, 0.0f) = (x < 0) ? 0 : x
    return F(lv * F(F(1.0) + F(F(tail * tail) * F(5.3056))))


def operator_envelope_increment(rate: int) -> np.float32:
    rate_scaled = (rate * 41) >> 6
    mantissa = 4 + (rate_scaled & 3)
    exponent = 2 + (rate_scaled >> 2)
    return F(F(mantissa << exponent) / F(1 << 24))


def pitch_envelope_increment(rate: int) -> np.float32:
    r = F(F(rate) * F(0.01))
    r4 = F(F(F(r * r) * r) * r)
    num = F(F(1.0) + F(F(F(192.0) * r) * F(r4 + F(0.3333))))
    return F(num / F(F(21.3) * F(44100.0)))


KMIN_LFO = F(0.005865)


def lfo_frequency(rate: int) -> np.float32:
    rate_scaled = 1 if rate == 0 else (rate * 165) >> 6
    rate_scaled *= 11 if rate_scaled < 160 else (11 + ((rate_scaled - 160) >> 4))
    return F(F(rate_scaled) * KMIN_LFO)


def lfo_delay(delay: int) -> tuple[np.float32, np.float32]:
    if delay == 0:
        return F(100000.0), F(100000.0)
    d = 99 - delay
    d = (16 + (d & 15)) << (1 + (d >> 4))
    return F(F(d) * KMIN_LFO), F(F(max(0x80, d & 0xFF80)) * KMIN_LFO)


# ---- algorithms.cc

DESTINATION_MASK, SOURCE_MASK, SOURCE_FEEDBACK = 0x03, 0x30, 0x30
ADDITIVE_FLAG, FEEDBACK_SOURCE_FLAG = 0x04, 0x40
# renderers_ of Algorithms<6>, in upstream's order: (n, modulation_source, additive)
RENDERERS = [
    (1, -2, False),
    (1, -2, True),
    (1, -1, False),
    (1, -1, True),
    (1, 0, False),
    (1, 0, True),
    (3, 2, True),
    (2, 1, True),
]


def opcodes(src: str) -> list[list[int]]:
    m = re.search(r"Algorithms<6>::opcodes_\[32\]\[6\] = \{(.*?)\n\};", src, re.S)
    assert m
    env = {
        "MOD": lambda n: n << 4,
        "ADD": lambda n: n | ADDITIVE_FLAG,
        "OUT": lambda n: n,
        "FB_SRC": FEEDBACK_SOURCE_FLAG,
        "FB_DST": 3 << 4,
        "NO_MOD": 0,
        "OUTPUT": ADDITIVE_FLAG,
    }
    env["FB"] = env["FB_SRC"] | env["FB_DST"]
    out = []
    for block in re.findall(r"\{\s*// Algorithm \d+\n(.*?)\n  \}", m.group(1), re.S):
        ops = []
        for line in block.strip().splitlines():
            expr = line.split("//")[0].strip().rstrip(",")
            ops.append(int(eval(expr, {"__builtins__": {}}, env)))
        assert len(ops) == 6, block
        out.append(ops)
    assert len(out) == 32
    return out


def compile_algorithm(ops: list[int]) -> dict[int, tuple[int, int, int, int]]:
    """Algorithms<6>::Compile: chain start op -> (renderer index, n, input_index, output_index)."""
    calls: dict[int, tuple[int, int, int, int]] = {}
    i = 0
    while i < 6:
        opcode = ops[i]
        n = 1
        while i + n < 6:
            frm = ops[i + n - 1]
            to = (ops[i + n] & SOURCE_MASK) >> 4
            has_additive = bool(frm & ADDITIVE_FLAG)
            broken = (frm & DESTINATION_MASK) != to
            if has_additive or broken:
                if to == (opcode & DESTINATION_MASK):
                    n = 1
                break
            n += 1
        found = False
        for _attempt in range(2):
            out_opcode = ops[i + n - 1]
            additive = bool(out_opcode & ADDITIVE_FLAG)
            ms = -3
            if not (opcode & SOURCE_MASK):
                ms = -1
            elif (opcode & SOURCE_MASK) != SOURCE_FEEDBACK:
                ms = -2
            else:
                for j in range(n):
                    if ops[i + j] & FEEDBACK_SOURCE_FLAG:
                        ms = j
            if (n, ms, additive) in RENDERERS:
                calls[i] = (
                    RENDERERS.index((n, ms, additive)),
                    n,
                    (opcode & SOURCE_MASK) >> 4,
                    out_opcode & DESTINATION_MASK,
                )
                found = True
                break
            if n != 1:
                n = 1
        assert found, (ops, i)
        i += n
    return calls


# ---- output


def notice(root: Path) -> str:
    return (root / "plaits" / "resources.cc").read_text().split("// ----")[0].rstrip() + "\n"


def words_c(name: str, vals: list[int], comment: str = "") -> list[str]:
    out = [f"const unsigned int {name}[{len(vals)}] = {{"] if not comment else [comment, f"const unsigned int {name}[{len(vals)}] = {{"]
    for i in range(0, len(vals), 4):
        out.append("  " + ", ".join(f"0x{v:08x}u" for v in vals[i : i + 4]) + ",")
    out.append("};")
    out.append("")
    return out


INV_FULL = np.uint32(dec_f32("3.0757874e-05f")).view(np.float32)  # plaits.c PL_INV_FULL: 1 / 0x7f00
FULL = 0x7F00


def unit_of_word(w: int) -> np.float32:
    """plaits.c unit(word): word * PL_INV_FULL clamped to [0, 1] (float32 operations)."""
    x = F(F(w) * INV_FULL)
    return F(min(max(x, F(0.0)), F(1.0)))


def patch_of_word(w: int) -> int:
    """The patch of HARMONICS word w: SixOpEngine::Render's `patch_index_quantizer_.Process(harmonics * 1.02f)` with the
    hysteresis 0 (stmlib HysteresisQuantizer2::Process, 32 steps, not symmetric: scale 32, offset -0.5)."""
    v = F(unit_of_word(w) * np.uint32(dec_f32("1.02f")).view(np.float32))
    v = F(v * F(32.0))
    v = F(v + F(-0.5))
    v = F(v + F(0.0))  # + base
    v = F(v + F(0.0))  # + hysteresis_sign * 0
    q = int(F(v + F(0.5)))  # static_cast<int>: truncates toward zero
    return min(max(q, 0), 31)


def patch_words() -> list[int]:
    """thr[k] for k = 0..31: the smallest word whose patch is >= k (thr[0] = 0)."""
    patches = [patch_of_word(w) for w in range(FULL + 1)]
    assert all(a <= b for a, b in zip(patches, patches[1:])), "the quantiser is not monotone in the word"
    assert patches[0] == 0 and patches[FULL] == 31
    thr = [0] + [next(w for w in range(FULL + 1) if patches[w] >= k) for k in range(1, 32)]
    assert thr == sorted(set(thr)), "a patch with no word"
    return thr


def name_rows(banks: list[bytes]) -> list[list[str]]:
    """The 32 names of each bank: bytes 118..127 of voice v (128 bytes each), trailing spaces trimmed."""
    rows = []
    for d in banks:
        names = []
        for v in range(32):
            raw = d[v * 128 + 118 : v * 128 + 128]
            assert all(0x20 <= c < 0x7F and c not in (0x22, 0x5C) for c in raw), raw
            names.append(raw.decode("ascii").rstrip(" "))
        rows.append(names)
    return rows


LABEL_WIDTH = 6  # characters of a value label under a knob on the SRC page (SQUARE, MORPH fit; 7 would touch the neighbours)


def short_name(name: str, width: int = LABEL_WIDTH) -> str:
    """The name for the narrow label: the whole name when it fits, else the first characters, keeping a trailing number
    (up to two digits: `BASS    3` -> `BASS3`, `*PPG*Vol.3` -> `*PPG*3`, `ANALOG  4` -> `ANALO4`) so that patches of a bank
    stay apart; trailing spaces trimmed."""
    import re

    t = name.strip()
    if len(t) <= width:
        return t
    m = re.search(r"\d+$", t)
    k = min(len(m.group()), 2) if m else 0
    return t[: width - k].rstrip() + t[len(t) - k :] if k else t[:width].rstrip()


def short_rows(names: list[list[str]]) -> list[list[str]]:
    rows = [[short_name(n) for n in bank] for bank in names]
    for r in rows:
        assert len(set(r)) == 32, "two patches of a bank share a short name"
    return rows


def names_asm(thr: list[int], names: list[list[str]]) -> str:
    out = [
        "| GENERATED by modules/machines/plaits/src/dsp/lib/gen_dx_tables.py (the owner's patch-name display); do not edit.",
        "|",
        "| The DX7 patch quantiser and the patch names, from the tables the DSP reads (lib/dx7_banks.c: pl_dx7_patch_word)",
        "| and the banks' voice names. dx7_thr[k - 1], k = 1..31 (32-bit words): the smallest HARMONICS word whose patch is k or",
        "| more (a word below dx7_thr[0] is patch 0). dx7_names: bank b (0..3), patch p at dx7_names + 12 * (32 * b + p), a NUL",
        "| terminated name of at most 10 characters (trailing spaces trimmed), padded to 12 bytes.",
        "",
        "    .section .rodata",
        "    .balign 4",
        "    .globl dx7_thr",
        "dx7_thr:",
    ]
    for k in range(1, 32, 8):
        out.append("    .long " + ", ".join(f"0x{thr[j]:04x}" for j in range(k, min(k + 8, 32))))
    out += ["    .globl dx7_names", "dx7_names:"]
    for bank in names:
        for n in bank:
            out.append(f'    .ascii "{n}"')
            out.append(f"    .space {12 - len(n)}, 0")
    out += [
        "| dx7_short: the same patches for the narrow label under a knob (at most 6 characters, see short_name): bank b, patch p at",
        "| dx7_short + 8 * (32 * b + p), NUL terminated, padded to 8 bytes.",
        "    .globl dx7_short",
        "dx7_short:",
    ]
    for bank in short_rows(names):
        for n in bank:
            out.append(f'    .ascii "{n}"')
            out.append(f"    .space {8 - len(n)}, 0")
    out.append("")
    return "\n".join(out)


def pack_bank(data: bytes) -> list[int]:
    assert len(data) == 4096
    return [int.from_bytes(data[k : k + 4], "little") for k in range(0, 4096, 4)]


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    units = (root / "plaits" / "dsp" / "fm" / "dx_units.cc").read_text()
    algs = (root / "plaits" / "dsp" / "fm" / "algorithms.cc").read_text()
    res = (root / "plaits" / "resources.cc").read_text()
    head = [
        notice(root),
        "// -----------------------------------------------------------------------------",
        "//",
        "// GENERATED by lib/gen_dx_tables.py from upstream plaits (dsp/fm/dx_units.{h,cc}, dsp/fm/algorithms.cc,",
        "// resources.cc) and the owner's bank (data/tyler-favorites.syx) - do not edit. See the generator.",
        "",
    ]

    # dx_tables
    coarse = [dec_f32(t) for t in body(units, "float", "lut_coarse")[:32]]
    amp = [dec_f32(t) for t in body(units, "float", "lut_amp_mod_sensitivity")[:4]]
    pitch = [dec_f32(t) for t in body(units, "float", "lut_pitch_mod_sensitivity")[:8]]
    cube = [dec_f32(t) for t in body(units, "float", "lut_cube_root")[:17]] + [f32_bits(1.0)]
    assert len(coarse) == 32 and len(amp) == 4 and len(pitch) == 8 and len(cube) == 18
    ops = opcodes(algs)
    calls = [compile_algorithm(a) for a in ops]
    call_words = [
        (c[0] | (c[1] << 8) | (c[2] << 16) | (c[3] << 24)) if (c := calls[a].get(i)) else 0
        for a in range(32)
        for i in range(6)
    ]
    modmask = [sum((1 << i) for i in range(6) if ops[a][i] & DESTINATION_MASK) for a in range(32)]
    op_level = [operator_level(v) for v in range(100)]
    pitch_level = [bits(pitch_envelope_level(v)) for v in range(100)]
    op_inc = [bits(operator_envelope_increment(v)) for v in range(100)]
    pitch_inc = [bits(pitch_envelope_increment(v)) for v in range(100)]
    lfo_f = [bits(lfo_frequency(v)) for v in range(100)]
    delays = [lfo_delay(v) for v in range(100)]
    lfo_d0 = [bits(d[0]) for d in delays]
    lfo_d1 = [bits(d[1]) for d in delays]

    tabs = [
        ("pl_dx_lut_coarse", coarse, "lut_coarse (dx_units.cc): semitones of the coarse ratio"),
        ("pl_dx_lut_amp_mod", amp, "lut_amp_mod_sensitivity"),
        ("pl_dx_lut_pitch_mod", pitch, "lut_pitch_mod_sensitivity"),
        ("pl_dx_lut_cube_root", cube, "lut_cube_root (17 entries) and a guard entry 1.0f (upstream reads index 17 at velocity 1.0)"),
        ("pl_dx_op_level", op_level, "OperatorLevel(0..99), integers"),
        ("pl_dx_pitch_level", pitch_level, "PitchEnvelopeLevel(0..99)"),
        ("pl_dx_op_increment", op_inc, "OperatorEnvelopeIncrement(0..99)"),
        ("pl_dx_pitch_increment", pitch_inc, "PitchEnvelopeIncrement(0..99)"),
        ("pl_dx_lfo_frequency", lfo_f, "LFOFrequency(0..99)"),
        ("pl_dx_lfo_delay0", lfo_d0, "LFODelay(0..99), increments[0]"),
        ("pl_dx_lfo_delay1", lfo_d1, "LFODelay(0..99), increments[1]"),
        ("pl_dx_calls", call_words, "Algorithms<6>::render_call_[algorithm][op]: renderer | n << 8 | input << 16 | output << 24 (0 inside a chain); renderers in upstream's order"),
        ("pl_dx_modmask", modmask, "bit op of word algorithm: is_modulator(algorithm, op)"),
    ]
    h = [
        *head,
        "#ifndef PLAITS_LIB_DX_TABLES_H",
        "#define PLAITS_LIB_DX_TABLES_H",
        '#include "tables.h"',
        "",
    ]
    c = [*head, '#include "dx_tables.h"', ""]
    for name, vals, comment in tabs:
        h.append(f"/* {comment} */")
        h.append(f"extern const unsigned int {name}[{len(vals)}];")
        c += words_c(name, vals, f"/* {comment} */")
    h += ["", "int pl_dx_tables_bytes(void);", "", "#endif", ""]
    c += [f"int pl_dx_tables_bytes(void) {{ return {4 * sum(len(v) for _, v, _ in tabs)}; }}", ""]
    (HERE / "dx_tables.h").write_text("\n".join(h))
    (HERE / "dx_tables.c").write_text("\n".join(c))

    # banks: models 18, 19, 20 = upstream's syx_bank_0..2, model 24 = the owner's bank
    banks = []
    for b in range(3):
        data = bytes(int(t) for t in body(res, "uint8_t", f"syx_bank_{b}"))
        assert len(data) == 4096 and all(v < 0x80 for v in data), (b, len(data))
        banks.append(data)
    banks.append(read_syx(OWNER_SYX))
    bh = [
        *head,
        "#ifndef PLAITS_LIB_DX7_BANKS_H",
        "#define PLAITS_LIB_DX7_BANKS_H",
        "",
        "/* Four banks of 32 voices, 128 bytes each in the DX7 32-voice dump format (upstream's Patch::Unpack input): bank 0, 1, 2 =",
        " * upstream's three (PLAITS models 18, 19, 20), bank 3 = the owner's (model 24). Byte j of bank b is",
        " * (pl_dx7_banks[b * 1024 + j / 4] >> (8 * (j % 4))) & 0xff. */",
        "#define PL_DX7_BANKS 4",
        "#define PL_DX7_BANK_WORDS 1024",
        "extern const unsigned int pl_dx7_banks[4096];",
        "",
        "/* The memoryless patch quantiser of HARMONICS (gen_dx_tables.py, `patch_of_word`): pl_dx7_patch_word[k] is the smallest",
        " * HARMONICS word (0 .. 0x7f00) whose patch is k or more and pl_dx7_patch_thr[k] the float bits of unit(word), the DSP's",
        " * compare value (entry 0 is 0: every word has patch 0 or more). The ColdFire names read the same words. */",
        "extern const unsigned int pl_dx7_patch_word[32];",
        "extern const unsigned int pl_dx7_patch_thr[32];",
        "",
        "int pl_dx7_banks_bytes(void);",
        "",
        "#endif",
        "",
    ]
    bc = [*head, '#include "dx7_banks.h"', ""]
    bc += words_c("pl_dx7_banks", [w for d in banks for w in pack_bank(d)])
    thr = patch_words()
    bc += words_c("pl_dx7_patch_word", thr, "/* smallest HARMONICS word of patch >= k (k = 0..31) */")
    bc += words_c(
        "pl_dx7_patch_thr",
        [bits(unit_of_word(w)) for w in thr],
        "/* the float bits of unit(pl_dx7_patch_word[k]): the engine's compare values */",
    )
    bc += ["int pl_dx7_banks_bytes(void) { return 16384 + 256; }", ""]
    (PLAITS / "src" / "cf" / "dx7_names.s").write_text(names_asm(thr, name_rows(banks)))
    (HERE / "dx7_banks.h").write_text("\n".join(bh))
    (HERE / "dx7_banks.c").write_text("\n".join(bc))


if __name__ == "__main__":
    main()
