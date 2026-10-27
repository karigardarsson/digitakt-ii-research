"""Generate the tables of the WAVETABLE (5) and CHORD (6) models from upstream Plaits (MIT, Emilie Gillet):

    lib/waves.h, lib/waves.c         wav_integrated_waves (192 waves of 132 entries) and the WAVETABLE wave map
    lib/chord_bank_tables.h          ChordBank's ratios and note counts (included by lib/chord_bank.c only)
    engines/chord_tables.h           ChordEngine's registrations, fade points and wave list (by engines/chord.c only)

Run from the repo root (needs the upstream checkout, ../eurorack by default, with stmlib populated):
    uv run python modules/machines/plaits/src/dsp/lib/gen_wave_tables.py [path/to/eurorack]

Sources: plaits/resources.cc (`wav_integrated_waves`: upstream's own build of resources/waves.bin and the waveform
definitions of resources/wavetables.py, which this generator takes as it is rather than re-running numpy's FFT),
plaits/dsp/engine/wavetable_engine.cc (the wave map), plaits/dsp/chords/chord_bank.cc (the chords, without
JON_CHORDS, as upstream builds) and plaits/dsp/engine/chord_engine.cc (registrations, fade points, wave list), and
stmlib/dsp/units.cc (lut_pitch_ratio_high/low, for SemitonesToRatio).

As lib/gen_lib_tables.py: every float is written as the IEEE single bit pattern of the value upstream's build holds, in
a plain `unsigned int` array read through `PL_F(table)`. Selache's C compiler writes negative floats in initialised
tables with wrong bits, and places union members like struct members (docs/findings/20-patch-toolchains.md). An int16
wave entry v becomes the float v (exact): upstream converts each entry with static_cast<float> before it uses it, so
the port reads floats with equal bits and does no int-to-float conversion in its inner loop. That doubles the table
(101,376 bytes against upstream's 50,688 of int16); L2 has room, and an int16 read would need a load, a shift and a
signed conversion that Selache's C cannot do without a FLOAT the encoding gate refuses.

Pointers into the wave table are offsets (entries), not addresses: an initialised table of addresses would need data
relocations. ChordBank's ratios are SemitonesToRatio(chords_[i][j]), computed here in float32 as upstream's
SemitonesToRatio does (one sum, one truncation, one product of two table entries), so they are constants rather than
236 bytes of per-voice state; tests/plaits/test_wavetable_chord_host.py checks them against upstream's ChordBank.
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DEFAULT = HERE.parents[5].parent / "eurorack"

WAVE_SIZE = 132  # kTableSize + 4
NUM_WAVES = 192
NUM_BANKS = 4
WAVES_PER_BANK = 64


def f32_bits(x: float) -> int:
    return int(struct.unpack("<I", struct.pack("<f", x))[0])


def array_body(src: str, decl: str) -> list[str]:
    m = re.search(rf"{decl}\s*=\s*\{{(.*?)\}};", src, re.S)
    assert m, decl
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return [t for t in re.split(r"[\s,{}]+", body) if t]


def floats(toks: list[str]) -> list[float]:
    return [float(t.rstrip("f")) for t in toks]


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


def semitones_to_ratio(root: Path, semitones: np.float32) -> np.float32:
    """stmlib::SemitonesToRatio in float32: pitch = s + 128; high[int(pitch)] * low[int(fraction * 256)]."""
    src = (root / "stmlib" / "dsp" / "units.cc").read_text()
    high = np.array(
        floats(array_body(src, r"const float lut_pitch_ratio_high\[\]")), np.float32
    )
    low = np.array(
        floats(array_body(src, r"const float lut_pitch_ratio_low\[\]")), np.float32
    )
    pitch = np.float32(semitones + np.float32(128.0))
    integral = int(pitch)
    fractional = np.float32(pitch - np.float32(integral))
    return np.float32(
        high[integral] * low[int(np.float32(fractional * np.float32(256.0)))]
    )


def chords(root: Path) -> list[list[str]]:
    """chords_[kChordNumChords][kChordNumNotes] of the build without JON_CHORDS (the #else branch)."""
    src = (root / "plaits" / "dsp" / "chords" / "chord_bank.cc").read_text()
    branch = src.split("#else", 1)[1].split("#endif", 1)[0]
    toks = array_body(
        branch, r"const float ChordBank::chords_\[kChordNumChords\]\[kChordNumNotes\]"
    )
    assert len(toks) == 44, len(toks)
    return [toks[4 * i : 4 * i + 4] for i in range(11)]


def notice(root: Path) -> str:
    return (root / "plaits" / "resources.cc").read_text().split("// ----")[
        0
    ].rstrip() + "\n"


def head(root: Path, what: str) -> list[str]:
    return [
        notice(root),
        "// -----------------------------------------------------------------------------",
        "//",
        "// GENERATED by lib/gen_wave_tables.py from upstream Plaits - do not edit.",
        f"// {what}",
        "",
    ]


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    resources = (root / "plaits" / "resources.cc").read_text()
    waves = [
        int(t) for t in array_body(resources, r"const int16_t wav_integrated_waves\[\]")
    ]
    assert len(waves) == NUM_WAVES * WAVE_SIZE, len(waves)
    assert all(-32768 <= w <= 32767 for w in waves)

    # WavetableEngine::LoadUserData(NULL): bank 3 maps wave i to i * 101 % 192.
    engine_src = (
        root / "plaits" / "dsp" / "engine" / "wavetable_engine.cc"
    ).read_text()
    assert "w = user_data ? user_data[wave] : (w * 101 % kNumWaves);" in engine_src
    wave_map = []
    for bank in range(NUM_BANKS):
        for wave in range(WAVES_PER_BANK):
            w = bank * WAVES_PER_BANK + wave
            if bank == NUM_BANKS - 1:
                w = w * 101 % NUM_WAVES
            wave_map.append(w * WAVE_SIZE)

    h = [
        *head(
            root,
            "The wave table of the WAVETABLE and CHORD models, as IEEE single bit patterns (see the generator).",
        ),
        "#ifndef PLAITS_LIB_WAVES_H",
        "#define PLAITS_LIB_WAVES_H",
        '#include "tables.h"',
        "",
        "/* wav_integrated_waves: 192 integrated waves of 132 entries (128 + 4 for the interpolators), each entry the float",
        " * of upstream's int16; read as PL_F(pl_wav_integrated_waves). */",
        f"#define PL_WAVE_SIZE {WAVE_SIZE}",
        f"extern const unsigned int pl_wav_integrated_waves[{len(waves)}];",
        "",
        "/* WavetableEngine's wave_map_ after LoadUserData(NULL): the offset (in entries) of the wave at",
        " * x + 8 y + 64 z (bank z, row y, column x); bank 3 is wave i * 101 % 192. */",
        "extern const int pl_wavetable_map[256];",
        "",
        "/* The bytes of these tables. */",
        "int pl_waves_bytes(void);",
        "",
        "#endif",
    ]
    c = [
        *head(
            root,
            "The wave table of the WAVETABLE and CHORD models (see the generator).",
        ),
        '#include "waves.h"',
        "",
    ]
    c.append(f"const unsigned int pl_wav_integrated_waves[{len(waves)}] = {{")
    c += hex_rows([f32_bits(float(w)) for w in waves])
    c += ["};", "", "const int pl_wavetable_map[256] = {"]
    c += int_rows(wave_map)
    c += ["};", ""]
    total = 4 * (len(waves) + len(wave_map))
    c += [f"int pl_waves_bytes(void) {{ return {total}; }}", ""]
    (HERE / "waves.h").write_text("\n".join(h) + "\n")
    (HERE / "waves.c").write_text("\n".join(c))

    # ChordBank::Reset: ratios_ = SemitonesToRatio(chords_[i][j]); note_count_ counts the notes that are not one of
    # the doubling intervals 0.01, 7.01, 11.99, 12.00 (compared as float literals).
    table = chords(root)
    ratios, counts = [], []
    doubling = {np.float32(x) for x in (0.01, 7.01, 11.99, 12.00)}
    for row in table:
        vals = [np.float32(float(t.rstrip("f"))) for t in row]
        ratios += [f32_bits(float(semitones_to_ratio(root, v))) for v in vals]
        counts.append(sum(1 for v in vals if v not in doubling))
    cb = [
        *head(
            root,
            "ChordBank's ratios and note counts (see the generator). Included by lib/chord_bank.c only.",
        ),
        "#ifndef PLAITS_LIB_CHORD_BANK_TABLES_H",
        "#define PLAITS_LIB_CHORD_BANK_TABLES_H",
        "",
        "/* SemitonesToRatio(chords_[i][j]), i < 11 chords, j < 4 notes, row by row. */",
        "static const unsigned int chord_ratios[44] = {",
        *hex_rows(ratios),
        "};",
        "",
        "/* note_count_[i]. */",
        "static const int chord_note_count[11] = {",
        *int_rows(counts, 11),
        "};",
        "",
        "#endif",
    ]
    (HERE / "chord_bank_tables.h").write_text("\n".join(cb) + "\n")

    chord_src = (root / "plaits" / "dsp" / "engine" / "chord_engine.cc").read_text()
    fade = floats(array_body(chord_src, r"const float fade_point\[kChordNumVoices\]"))
    regs = floats(
        array_body(
            chord_src,
            r"const float registrations\[kRegistrationTableSize\]\[kChordNumHarmonics \* 2\]",
        )
    )
    assert len(fade) == 5 and len(regs) == 48, (len(fade), len(regs))
    wl = re.findall(
        r"WAVE\((\d+), (\d+), (\d+)\)",
        chord_src.split("wavetable[] = {", 1)[1].split("};", 1)[0],
    )
    assert len(wl) == 15, wl
    wave_list = [(int(b) * 64 + int(r) * 8 + int(col)) * WAVE_SIZE for b, r, col in wl]
    ce = [
        *head(
            root,
            "ChordEngine's registrations, fade points and wave list (see the generator). Included by engines/chord.c only.",
        ),
        "#ifndef PLAITS_CHORD_TABLES_H",
        "#define PLAITS_CHORD_TABLES_H",
        "",
        "/* fade_point[kChordNumVoices]. */",
        "static const unsigned int chord_fade_point[5] = {",
        *hex_rows([f32_bits(x) for x in fade]),
        "};",
        "",
        "/* registrations[kRegistrationTableSize = 8][kChordNumHarmonics * 2 = 6], row by row. */",
        "static const unsigned int chord_registrations[48] = {",
        *hex_rows([f32_bits(x) for x in regs], 6),
        "};",
        "",
        "/* wavetable[]: WAVE(bank, row, column) as the offset (bank * 64 + row * 8 + column) * 132 into",
        " * pl_wav_integrated_waves. */",
        "static const int chord_waves[15] = {",
        *int_rows(wave_list, 5),
        "};",
        "",
        "#endif",
    ]
    (HERE.parent / "engines" / "chord_tables.h").write_text("\n".join(ce) + "\n")

    sm_src = (root / "plaits" / "dsp" / "engine2" / "string_machine_engine.cc").read_text()
    sm_regs = floats(
        array_body(
            sm_src,
            r"const float registrations\[kRegistrationTableSize\]\[kChordNumHarmonics \* 2\]",
        )
    )
    assert len(sm_regs) == 66, len(sm_regs)
    sm = [
        *head(
            root,
            "StringMachineEngine's registrations (see the generator). Included by engines/string_machine.c only.",
        ),
        "#ifndef PLAITS_STRING_MACHINE_TABLES_H",
        "#define PLAITS_STRING_MACHINE_TABLES_H",
        "",
        "/* registrations[kRegistrationTableSize = 11][kChordNumHarmonics * 2 = 6], row by row. */",
        "static const unsigned int string_machine_registrations[66] = {",
        *hex_rows([f32_bits(x) for x in sm_regs], 6),
        "};",
        "",
        "#endif",
    ]
    (HERE.parent / "engines" / "string_machine_tables.h").write_text("\n".join(sm) + "\n")


if __name__ == "__main__":
    main()
