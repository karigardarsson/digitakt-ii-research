"""Generate the tables of the SPEECH model (7) from upstream Plaits (MIT, Emilie Gillet):

    lib/speech_tables.h, lib/speech_tables.c

Run from the repo root (needs the upstream checkout, ../eurorack by default):
    uv run python modules/machines/plaits/src/dsp/lib/gen_speech_tables.py [path/to/eurorack]

Sources (plaits/dsp/speech/ and plaits/resources.cc):
  - naive_speech_synth.cc       NaiveSpeechSynth::phonemes_ (5 phonemes x 5 registers x 5 formants: frequency, amplitude)
  - sam_speech_synth.cc         SAMSpeechSynth::phonemes_ (17 + 1 guard, 3 formants) and formant_amplitude_lut
  - lpc_speech_synth_phonemes.cc LPCSpeechSynthController::phonemes_ (5 vowels, 10 consonants)
  - lpc_speech_synth_controller.cc  the LPC10 code tables (energy, period, k0..k9)
  - lpc_speech_synth_words.cc   the five word banks (LPC10 bit streams from TI ROMs)
  - resources.cc                lut_lpc_excitation_pulse

As the other generators: every float is the IEEE single bit pattern of the value upstream's code computes before it
uses it, in a plain `unsigned int` array read through `PL_F(table)` (Selache's C compiler writes negative floats in
initialised tables with wrong bits; docs/findings/20-patch-toolchains.md). The port does no int-to-float conversion
of a table entry:
  - naive phonemes: the uint8 frequency and amplitude as floats (upstream's differences of two entries are exact in
    float, so float(a) + (float(b) - float(a)) * x is upstream's a + (b - a) * x);
  - SAM phonemes: the frequency as a float and the amplitude already looked up in formant_amplitude_lut;
  - LPC frames: 1 / period (0 for an unvoiced frame, period 0), energy / 256, k0 / 32768, k1 / 32768, k2..k9 / 128,
    each the float upstream's PlayFrame and BlendCoefficient compute (all exact but 1 / period, which is float32's
    correctly rounded quotient, as upstream's);
  - the excitation pulse: int8 s as s / 128 (exact).

Word banks. Upstream decodes a bank's bit stream into frames in its arena (LPCSpeechSynthWordBank::Load, up to 1024
frames of 14 bytes and 32 word boundaries) whenever the bank changes. The decoding depends on the bank only, so it is
done here, by the same algorithm (BitStream, LoadNextWord, Load): each decoded frame is stored as the 12 indices of
its fields into the code tables (2 words per frame), each code table extended by one entry 0.0 for the zero a
frame starts from (LoadNextWord zeroes every field before a word, and a repeated, unvoiced or silent frame keeps
fields from before). The word boundaries are stored per bank. tests/plaits/test_speech_host.py compares every
decoded frame and boundary with upstream's Load.
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DEFAULT = HERE.parents[5].parent / "eurorack"

NUM_BANKS = 5


def f32(x: float) -> np.float32:
    return np.float32(x)


def f32_bits(x: float | np.float32) -> int:
    return int(struct.unpack("<I", struct.pack("<f", float(np.float32(x))))[0])


def strip(src: str) -> str:
    return re.sub(r"//[^\n]*", "", src)


def array_body(src: str, decl: str) -> list[str]:
    m = re.search(rf"{decl}\s*=\s*\{{(.*?)\}};", src, re.S)
    assert m, decl
    return [t for t in re.split(r"[\s,{}]+", strip(m.group(1))) if t]


def ints(toks: list[str]) -> list[int]:
    return [int(t, 0) for t in toks]


def hex_rows(vals: list[int], per: int = 4) -> list[str]:
    return [
        "  " + ", ".join(f"0x{v:08x}u" for v in vals[i : i + per]) + ","
        for i in range(0, len(vals), per)
    ]


def int_rows(vals: list[int], per: int = 8) -> list[str]:
    return [
        "  " + ", ".join(str(v) for v in vals[i : i + per]) + ","
        for i in range(0, len(vals), per)
    ]


# ---- the LPC10 code tables and the word banks


CODE_TABLES = [
    # (name in lpc_speech_synth_controller.cc, C type, entries, scale)
    ("energy_lut_", "uint8_t", 16, 256.0),
    ("period_lut_", "uint8_t", 64, 0.0),  # 1 / period, 0 for period 0
    ("k0_lut_", "int16_t", 32, 32768.0),
    ("k1_lut_", "int16_t", 32, 32768.0),
    ("k2_lut_", "int8_t", 16, 128.0),
    ("k3_lut_", "int8_t", 16, 128.0),
    ("k4_lut_", "int8_t", 16, 128.0),
    ("k5_lut_", "int8_t", 16, 128.0),
    ("k6_lut_", "int8_t", 16, 128.0),
    ("k7_lut_", "int8_t", 8, 128.0),
    ("k8_lut_", "int8_t", 8, 128.0),
    ("k9_lut_", "int8_t", 8, 128.0),
]
# bits of each field's index in a packed frame: energy and period have 0 in their tables (index 0); k0..k9 get one
# more entry (the zero) after upstream's
FIELD_BITS = [4, 6, 6, 6, 5, 5, 5, 5, 5, 4, 4, 4]


def code_tables(root: Path) -> list[list[int]]:
    src = (
        root / "plaits" / "dsp" / "speech" / "lpc_speech_synth_controller.cc"
    ).read_text()
    out = []
    for name, ctype, n, _ in CODE_TABLES:
        vals = ints(
            array_body(src, rf"const {ctype} LPCSpeechSynthWordBank::{name}\[{n}\]")
        )
        assert len(vals) == n, name
        out.append(vals)
    assert out[0][0] == 0 and out[1][0] == 0
    return out


def word_banks(root: Path) -> list[list[int]]:
    src = (root / "plaits" / "dsp" / "speech" / "lpc_speech_synth_words.cc").read_text()
    hdr = (root / "plaits" / "dsp" / "speech" / "lpc_speech_synth_words.h").read_text()
    assert f"#define LPC_SPEECH_SYNTH_NUM_WORD_BANKS {NUM_BANKS}" in hdr
    banks = []
    for b in range(NUM_BANKS):
        vals = ints(array_body(src, rf"const uint8_t bank_{b}\[\]"))
        size = int(re.search(rf"\{{ bank_{b}, (\d+) \}}", src).group(1))  # type: ignore[union-attr]
        assert len(vals) == size, (b, len(vals), size)
        banks.append(vals)
    return banks


def reverse(b: int) -> int:
    b = ((b >> 4) | (b << 4)) & 0xFF
    b = ((b & 0xCC) >> 2) | ((b & 0x33) << 2)
    b = ((b & 0xAA) >> 1) | ((b & 0x55) << 1)
    return b & 0xFF


class BitStream:
    """plaits::BitStream (lpc_speech_synth_controller.h): bits_ is a uint16_t."""

    def __init__(self, data: list[int], p: int) -> None:
        self.data, self.p, self.available, self.bits = data, p, 0, 0

    def get(self, n: int) -> int:
        shift = n
        if n > self.available:
            self.bits = (self.bits << self.available) & 0xFFFF
            shift -= self.available
            self.bits |= reverse(self.data[self.p])
            self.p += 1
            self.available += 8
        self.bits = (self.bits << shift) & 0xFFFF
        result = (self.bits >> 8) & 0xFF
        self.bits &= 0xFF
        self.available -= n
        return result

    def flush(self) -> None:
        while self.available:
            self.get(1)


ZERO = [
    0,
    0,
    32,
    32,
    16,
    16,
    16,
    16,
    16,
    8,
    8,
    8,
]  # the index of 0 in each extended code table


def load_bank(data: list[int]) -> tuple[list[list[int]], list[int]]:
    """LPCSpeechSynthWordBank::Load: the frames (as code-table indices) and the word boundaries (num_words + 1)."""
    frames: list[list[int]] = []
    bounds: list[int] = []
    p = 0
    while p < len(data):
        bounds.append(len(frames))
        bs = BitStream(data, p)
        frame = list(ZERO)  # LoadNextWord: every field 0
        while True:
            energy = bs.get(4)
            if energy == 0:
                frame[0] = 0
            elif energy == 0xF:
                bs.flush()
                break
            else:
                frame[0] = energy
                repeat = bs.get(1)
                frame[1] = bs.get(6)
                if not repeat:
                    frame[2] = bs.get(5)
                    frame[3] = bs.get(5)
                    frame[4] = bs.get(4)
                    frame[5] = bs.get(4)
                    if frame[1]:  # period_lut_[i] != 0 exactly when i != 0
                        frame[6] = bs.get(4)
                        frame[7] = bs.get(4)
                        frame[8] = bs.get(4)
                        frame[9] = bs.get(3)
                        frame[10] = bs.get(3)
                        frame[11] = bs.get(3)
            frames.append(list(frame))
        p = bs.p
    bounds.append(len(frames))
    assert (
        len(frames) <= 1024 and len(bounds) <= 32
    )  # kLPCSpeechSynthMaxFrames, kLPCSpeechSynthMaxWords
    return frames, bounds


def pack(frame: list[int]) -> tuple[int, int]:
    words = [0, 0]
    shift, w = 0, 0
    for idx, bits in zip(frame, FIELD_BITS, strict=True):
        assert 0 <= idx < (1 << bits)
        if shift + bits > 32:
            w, shift = w + 1, 0
        words[w] |= idx << shift
        shift += bits
    return words[0], words[1]


def lut_floats(tables: list[list[int]]) -> tuple[list[np.float32], list[int]]:
    """The code tables as the floats PlayFrame uses, each k table followed by its 0 entry; and each table's offset."""
    out: list[np.float32] = []
    offsets = []
    for k, ((_, _, _, scale), vals) in enumerate(zip(CODE_TABLES, tables, strict=True)):
        offsets.append(len(out))
        if k == 1:
            out += [f32(0.0) if v == 0 else f32(1.0) / f32(v) for v in vals]
        else:
            out += [f32(v) / f32(scale) for v in vals]
            if k >= 2:
                out.append(f32(0.0))
    return out, offsets


# ---- the phoneme tables


def naive_phonemes(root: Path) -> list[int]:
    src = (root / "plaits" / "dsp" / "speech" / "naive_speech_synth.cc").read_text()
    vals = ints(
        array_body(
            src,
            r"const NaiveSpeechSynth::Phoneme NaiveSpeechSynth::phonemes_\[\]\[kNaiveSpeechNumRegisters\]",
        )
    )
    assert len(vals) == 5 * 5 * 5 * 2, len(vals)
    assert all(0 <= v <= 255 for v in vals)
    return vals


def sam_phonemes(root: Path) -> tuple[list[int], list[float]]:
    src = (root / "plaits" / "dsp" / "speech" / "sam_speech_synth.cc").read_text()
    vals = ints(
        array_body(src, r"const SAMSpeechSynth::Phoneme SAMSpeechSynth::phonemes_\[\]")
    )
    assert len(vals) == 18 * 3 * 2, len(vals)
    lut = [
        float(t.rstrip("f"))
        for t in array_body(
            src, r"const float SAMSpeechSynth::formant_amplitude_lut\[\]"
        )
    ]
    assert len(lut) == 16
    return vals, lut


def lpc_phonemes(root: Path) -> list[list[int]]:
    src = (
        root / "plaits" / "dsp" / "speech" / "lpc_speech_synth_phonemes.cc"
    ).read_text()
    vals = ints(
        array_body(
            src, r"LPCSpeechSynthController::phonemes_\[kLPCSpeechSynthNumPhonemes\]"
        )
    )
    assert len(vals) == 15 * 12, len(vals)
    return [vals[12 * i : 12 * i + 12] for i in range(15)]


def frame_floats(f: list[int]) -> list[np.float32]:
    """A Frame {energy, period, k0..k9} as [1 / period (0 if period is 0), energy / 256, k0 / 32768, k1 / 32768,
    k2..k9 / 128]."""
    energy, period, *k = f
    inv = f32(0.0) if period == 0 else f32(1.0) / f32(period)
    return [
        inv,
        f32(energy) / f32(256.0),
        f32(k[0]) / f32(32768.0),
        f32(k[1]) / f32(32768.0),
    ] + [f32(x) / f32(128.0) for x in k[2:]]


def excitation_pulse(root: Path) -> list[int]:
    src = (root / "plaits" / "resources.cc").read_text()
    vals = ints(array_body(src, r"const int8_t lut_lpc_excitation_pulse\[\]"))
    hdr = (root / "plaits" / "resources.h").read_text()
    assert "#define LUT_LPC_EXCITATION_PULSE_SIZE 640" in hdr
    assert len(vals) == 640
    return vals


def notice(root: Path) -> str:
    return (
        root / "plaits" / "dsp" / "speech" / "lpc_speech_synth_words.cc"
    ).read_text().split("// ----")[0].rstrip() + "\n"


def head(root: Path, what: str) -> list[str]:
    return [
        notice(root),
        "// -----------------------------------------------------------------------------",
        "//",
        "// GENERATED by lib/gen_speech_tables.py from upstream Plaits - do not edit.",
        f"// {what}",
        "",
    ]


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    tables = code_tables(root)
    luts, offsets = lut_floats(tables)
    banks = [load_bank(b) for b in word_banks(root)]
    packed: list[int] = []
    bank_frame0: list[int] = []
    bank_frames: list[int] = []
    bank_words: list[int] = []
    bound_off: list[int] = []
    bounds: list[int] = []
    for frames, bnd in banks:
        bank_frame0.append(len(packed) // 2)
        bank_frames.append(len(frames))
        bank_words.append(len(bnd) - 1)
        bound_off.append(len(bounds))
        bounds += bnd
        for fr in frames:
            packed += list(pack(fr))
    naive = naive_phonemes(root)
    sam, sam_lut = sam_phonemes(root)
    sam_f: list[np.float32] = []
    for i in range(0, len(sam), 2):
        sam_f += [f32(sam[i]), f32(sam_lut[sam[i + 1]])]
    lpc_ph: list[np.float32] = []
    for fr in lpc_phonemes(root):
        lpc_ph += frame_floats(fr)
    pulse = excitation_pulse(root)

    sizes = {
        "pl_naive_phonemes": len(naive),
        "pl_sam_phonemes": len(sam_f),
        "pl_lpc_phonemes": len(lpc_ph),
        "pl_lpc_code": len(luts),
        "pl_lpc_word_frames": len(packed),
        "pl_lpc_word_boundaries": len(bounds),
        "pl_lpc_excitation_pulse": len(pulse),
    }
    small = 4 * NUM_BANKS * 4
    total = 4 * sum(sizes.values()) + small

    names = ["ENERGY", "PERIOD", *[f"K{i}" for i in range(10)]]
    h = [
        *head(
            root,
            "The tables of the SPEECH model, as IEEE single bit patterns and code indices (see the generator).",
        ),
        "#ifndef PLAITS_LIB_SPEECH_TABLES_H",
        "#define PLAITS_LIB_SPEECH_TABLES_H",
        '#include "tables.h"',
        "",
        "/* NaiveSpeechSynth::phonemes_[phoneme 5][register 5].formant[5] = {frequency, amplitude}, as floats. */",
        f"extern const unsigned int pl_naive_phonemes[{sizes['pl_naive_phonemes']}];",
        "",
        "/* SAMSpeechSynth::phonemes_[17 + 1 guard].formant[3] = {frequency as a float, formant_amplitude_lut[amplitude]}. */",
        f"extern const unsigned int pl_sam_phonemes[{sizes['pl_sam_phonemes']}];",
        "",
        "/* LPCSpeechSynthController::phonemes_[15] as PL_LPC_FRAME floats each: 1 / period (0: unvoiced), energy / 256,",
        " * k0 / 32768, k1 / 32768, k2..k9 / 128. */",
        "#define PL_LPC_FRAME 12",
        f"extern const unsigned int pl_lpc_phonemes[{sizes['pl_lpc_phonemes']}];",
        "",
        "/* The LPC10 code tables as PlayFrame's floats (energy / 256, 1 / period or 0, k / scale), each k table followed",
        " * by 0.0 (the zero a word starts from); PL_LPC_CODE_<field> is each table's offset. */",
        *[f"#define PL_LPC_CODE_{n} {o}" for n, o in zip(names, offsets, strict=True)],
        f"extern const unsigned int pl_lpc_code[{sizes['pl_lpc_code']}];",
        "",
        "/* The word banks' decoded frames, 2 words each: the indices into the code tables of energy (bits 0-3), period",
        " * (4-9), k0 (10-15), k1 (16-21), k2 (22-26), k3 (27-31); k4 (0-4), k5 (5-9), k6 (10-14), k7 (15-18), k8 (19-22),",
        " * k9 (23-26). Bank b's frames start at frame pl_lpc_bank_frame0[b]; its word boundaries (num_words + 1) at",
        " * pl_lpc_word_boundaries[pl_lpc_bank_boundary0[b]]. */",
        f"#define PL_LPC_NUM_BANKS {NUM_BANKS}",
        f"extern const unsigned int pl_lpc_word_frames[{sizes['pl_lpc_word_frames']}];",
        f"extern const int pl_lpc_word_boundaries[{sizes['pl_lpc_word_boundaries']}];",
        f"extern const int pl_lpc_bank_frame0[{NUM_BANKS}];",
        f"extern const int pl_lpc_bank_num_frames[{NUM_BANKS}];",
        f"extern const int pl_lpc_bank_num_words[{NUM_BANKS}];",
        f"extern const int pl_lpc_bank_boundary0[{NUM_BANKS}];",
        "",
        "/* lut_lpc_excitation_pulse[i] / 128. */",
        f"#define PL_LPC_EXCITATION_PULSE_SIZE {len(pulse)}",
        f"extern const unsigned int pl_lpc_excitation_pulse[{len(pulse)}];",
        "",
        f"/* The bytes of these tables ({total}). */",
        "int pl_speech_tables_bytes(void);",
        "",
        "#endif",
    ]
    c = [
        *head(root, "The tables of the SPEECH model (see the generator)."),
        '#include "speech_tables.h"',
        "",
        f"const unsigned int pl_naive_phonemes[{len(naive)}] = {{",
        *hex_rows([f32_bits(float(v)) for v in naive], 10),
        "};",
        "",
        f"const unsigned int pl_sam_phonemes[{len(sam_f)}] = {{",
        *hex_rows([f32_bits(v) for v in sam_f], 6),
        "};",
        "",
        f"const unsigned int pl_lpc_phonemes[{len(lpc_ph)}] = {{",
        *hex_rows([f32_bits(v) for v in lpc_ph], 6),
        "};",
        "",
        f"const unsigned int pl_lpc_code[{len(luts)}] = {{",
        *hex_rows([f32_bits(v) for v in luts]),
        "};",
        "",
        f"const unsigned int pl_lpc_word_frames[{len(packed)}] = {{",
        *hex_rows(packed),
        "};",
        "",
        f"const int pl_lpc_word_boundaries[{len(bounds)}] = {{",
        *int_rows(bounds, 10),
        "};",
        "",
        f"const int pl_lpc_bank_frame0[{NUM_BANKS}] = {{ {', '.join(map(str, bank_frame0))} }};",
        f"const int pl_lpc_bank_num_frames[{NUM_BANKS}] = {{ {', '.join(map(str, bank_frames))} }};",
        f"const int pl_lpc_bank_num_words[{NUM_BANKS}] = {{ {', '.join(map(str, bank_words))} }};",
        f"const int pl_lpc_bank_boundary0[{NUM_BANKS}] = {{ {', '.join(map(str, bound_off))} }};",
        "",
        f"const unsigned int pl_lpc_excitation_pulse[{len(pulse)}] = {{",
        *hex_rows([f32_bits(f32(v) / f32(128.0)) for v in pulse]),
        "};",
        "",
        f"int pl_speech_tables_bytes(void) {{ return {total}; }}",
        "",
    ]
    (HERE / "speech_tables.h").write_text("\n".join(h) + "\n")
    (HERE / "speech_tables.c").write_text("\n".join(c))
    print("bytes", sizes, "total", total, "frames", bank_frames, "words", bank_words)


if __name__ == "__main__":
    main()
