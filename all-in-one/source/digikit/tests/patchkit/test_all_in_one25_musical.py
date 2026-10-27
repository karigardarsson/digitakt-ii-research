"""Reuse the frozen Musical WAVE/Zoom execution fixtures on final MAIN bytes."""

import importlib
import json
import os
import struct
from pathlib import Path

import pytest

from tools.patchkit import pk_all_in_one25 as aio
from tools.patchkit import port_resolve


@pytest.fixture(scope="module")
def musical():
    root = aio.ROOT
    doc = json.loads(
        Path(
            os.environ.get("AIO25_REPORT", root / "out/all-in-one-report.json")
        ).read_text()
    )
    dv_root = Path(doc["dv_source"]["path"]).parents[2]
    assert (
        dv_root / "experiments/dt2_dv_offline/verify_musical_deja_vu_v1.py"
    ).exists()
    # Run with the frozen fixture directory on PYTHONPATH; do not import or
    # copy it under a new package identity or edit the reference checkout.
    v = importlib.import_module("verify_musical_deja_vu_v1")
    main = port_resolve.sections_of(
        Path(
            os.environ.get(
                "AIO25_CANDIDATE", root / "out/dt2-all-in-one-plaits25-ui21.syx"
            )
        )
    )[3]
    stock = port_resolve.sections_of(root / "firmware/Digitakt_II_OS1.17.syx")[3]
    symbols = {k: hex(n) for k, n in doc["dv"]["symbols"].items()}
    segments, _ = aio.dv.parsed_segments(main)
    code = next(
        aio.dv.at(main, src + 12, n)
        for src, n, dest, _ in segments
        if dest == aio.DV_BASE
    )
    old_setup = v.hp.setup

    def setup(*args, **kwargs):
        uc, a, b = old_setup(*args, **kwargs)
        for src, n, dest, bss in segments:
            uc.mem_write(dest, aio.dv.at(main, src + 12, n) + bytes(bss))
        return uc, a, b

    v.hp.setup = setup
    v.b.EXP = aio.DV_BASE
    v.zoom.b.EXP = aio.DV_BASE
    v.b.assemble = lambda: (code, symbols)
    yield v, main, stock, code, symbols, doc
    v.hp.setup = old_setup


def test_stock_waves_dv_and_all_musical_scales(musical):
    v, main, stock, code, symbols, doc = musical
    baseline, _ = v.p.apply_manifest(
        stock, v.p.OVERLAY.read_bytes(), json.loads(v.p.MANIFEST.read_text())
    )
    fields = (
        "apply",
        "output",
        "phase",
        "values",
        "registers",
        "stack_after",
        "emac_after",
    )
    for wave in range(9):
        left = v.run_lfo(
            stock if wave < 7 else baseline, None, wave=wave, dest=2, dep=0x6000
        )
        right = v.run_lfo(main, code, wave=wave, dest=2, dep=0x6000)
        for field in fields:
            assert left[field] == right[field], (wave, field)
        assert right["stack_before"] == right["stack_after"]
    for slot in (0, 0x40000000, 0x7FFFFFFF, 0xC0000000):
        ref = v.run_lfo(main, code, wave=7, dest=2, dep=0x6000, slot=slot)
        for wave, (_name, scale, _sym) in v.SCALES.items():
            q = v.run_lfo(main, code, wave=wave, dest=25, dep=0x7F00, slot=slot)
            assert ((q["apply"] >> 8) - 64) % 12 in scale
            assert 0x0400 <= q["apply"] <= 0x7C00 and q["post_seen"]
            other = v.run_lfo(main, code, wave=wave, dest=2, dep=0x6000, slot=slot)
            for field in ("apply", "output", "phase", "registers", "emac_after"):
                assert ref[field] == other[field], (wave, slot, field)
    assert v.check_new_scale_maps(code, symbols)
    assert v.check_formatter(main, code, symbols) == "PASS"


def test_zoom_stock_fallback_and_three_levels(musical):
    v, main, _stock, code, symbols, _doc = musical
    assert v.check_zoom(main, code, symbols) == "PASS"


def test_sound_serialization_25_models_and_all_waves(musical):
    v, main, _stock, _code, _symbols, _doc = musical
    sp = v.dv.sp
    sp.TO_STORAGE, sp.FROM_STORAGE = 0x400DBA94, 0x400DB3B2
    sp.TO_CONTAINER, sp.FROM_CONTAINER = 0x400DBD44, 0x400DB80C
    old_make = sp.make_uc
    segments, _ = aio.dv.parsed_segments(main)

    def make(raw, *args, **kwargs):
        # The original 1.16 fixture maps only the stock flash window. Loader
        # records appended after that window are installed at their real RAM
        # destinations below, not executed out of the packed MAIN tail.
        uc = old_make(raw[:0x31FC00], *args, **kwargs)
        uc.mem_map(0x44000000, 0x4000000)
        for src, n, dest, bss in segments:
            uc.mem_write(dest, aio.dv.at(main, src + 12, n) + bytes(bss))
        return uc

    sp.make_uc = make
    try:
        canonical = sp.canonical_runtime(main)
        for model in range(25):
            source = bytearray(canonical)
            source[0xA2] = 11
            values = {
                25: 0x4100,
                26: model << 8,
                27: 0x2400,
                31: 0x5100,
                32: 0x6200,
                33: 0x7300,
            }
            values.update({5: 0x900, 13: 0xE00, 21: 0xF00})
            for slot, raw in values.items():
                struct.pack_into(">H", source, 0x14 + 2 * slot, raw)
            _container, _storage, restored, *_ = sp.roundtrip(main, bytes(source))
            assert restored[0xA2] == 11
            for slot, raw in values.items():
                assert struct.unpack_from(">H", restored, 0x14 + 2 * slot)[0] == raw, (
                    model,
                    slot,
                )
        for wave in range(16):
            source = sp.fixture(canonical, 0, wave)
            _container, _storage, restored, *_ = sp.roundtrip(main, source)
            assert sp.get_param(restored, 5) == wave << 8
    finally:
        sp.make_uc = old_make
