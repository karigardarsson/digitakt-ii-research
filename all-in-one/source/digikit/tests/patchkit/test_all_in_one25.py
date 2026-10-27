"""Execute the final private composition, not a separately linked UI mockup.

The cached stock ColdFire state supplies live UI objects. Final MAIN bytes and
all loader segments replace its code; there is no coupled cold boot here.
"""

import json
import os
import struct
from pathlib import Path

import pytest
from PIL import Image

from tools.emulator.snapread import Snapshot
from tools.patchkit import pk_all_in_one25 as aio
from tools.patchkit import pk_cfcall, port_resolve

ROOT = aio.ROOT
CANDIDATE = Path(
    os.environ.get("AIO25_CANDIDATE", ROOT / "out/dt2-all-in-one-plaits25-ui21.syx")
)
REPORT = Path(os.environ.get("AIO25_REPORT", ROOT / "out/all-in-one-report.json"))
BMP, PIXELS = 0x47F00000, 0x47F01000
OUT = Path(os.environ.get("AIO25_UI_OUT", ROOT / "out/all-in-one25-ui"))


def save_bitmap(bits, name):
    im = Image.new("1", (128, 64))
    for x in range(128):
        for y in range(64):
            w = struct.unpack_from(">I", bits, x * 8 + (y // 32) * 4)[0]
            im.putpixel((x, 63 - y), bool(w & (0x80000000 >> (y % 32))))
    OUT.mkdir(exist_ok=True)
    im.save(OUT / (name + ".png"))
    im.resize((768, 384), Image.Resampling.NEAREST).save(OUT / (name + "-6x.png"))


@pytest.fixture(scope="module")
def fixture():
    report = json.loads(REPORT.read_text())
    main = port_resolve.sections_of(CANDIDATE)[3]
    snap = Snapshot(str(ROOT / "out/cache/pk_cfcall/Digitakt_II_OS1.17.state"))
    pages = [
        a + i
        for a, b in snap._page_cache.items()
        if 0x44000000 <= a < 0x47000000
        for i in range(0, len(b) - 4, 4)
        if b[i : i + 4] == struct.pack(">I", 0x401EDC98)
    ]
    assert len(pages) == 1
    sound = snap.u32(0x8000470C)
    assert sound
    pokes = {
        0x40000400: main[: aio.dv.STOCK_MAIN_END - aio.dv.MAIN_BASE],
        BMP: struct.pack(">7I", 0, 128, 64, 2, PIXELS, 0, 0),
        PIXELS - 16: b"\xa5" * 16 + bytes(1024) + b"\x5a" * 16,
    }
    segments, _ = aio.dv.parsed_segments(main)
    for src, n, dest, bss in segments:
        pokes[dest] = aio.dv.at(main, src + 12, n) + bytes(bss)
    return pokes, pages[0], sound, report


def checked(res):
    for r in res:
        assert r.ok, (hex(r.pc), r.error)
        assert not r.clobbered, r.clobbered
    return res


def clear():
    return {"pc": 0x401131AE, "args": [BMP, 0, 0, 127, 63, 0]}


def page_call(page):
    return {
        "pc": 0x40016EAC,
        "args": [page, BMP],
        "reads": [(PIXELS - 16, 1056)],
        "max": 500000,
    }


def bits(r):
    data = r.reads[0]
    assert data[:16] == b"\xa5" * 16 and data[-16:] == b"\x5a" * 16
    return data[16:-16]


def test_actual_25_model_pages_and_dx7_patch_names(fixture):
    pokes, page, sound, report = fixture
    pokes = dict(pokes)
    pokes[sound + 0xA2] = b"\x0b"
    for slot, raw in {
        25: 0x4000,
        26: 0,
        27: 0x4000,
        31: 0x4000,
        32: 0x4000,
        33: 0x4000,
        34: 0x6400,
    }.items():
        pokes[sound + 0x14 + 2 * slot] = struct.pack(">H", raw)
    cases = [(m, 0x4000) for m in range(25)]
    cases += [(m, h) for m in range(18, 22) for h in (0, 0x7F00)]
    cases += [(0, 0x4000)]
    calls = []
    for k, (model, harm) in enumerate(cases):
        src = 0x47F04000 + 4 * k
        pokes[src] = struct.pack(">2H", model << 8, harm)
        calls += [
            {"pc": 0x4013AB84, "args": [sound + 0x48, src, 4]},
            clear(),
            page_call(page),
        ]
    res = checked(
        pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", timeout=25).run(pokes, calls)
    )
    captures = [bits(r) for r in res[2::3]]
    assert len(set(captures[:25])) == 25
    assert captures[0] == captures[-1], "MODEL return leaves no stale glyphs"
    for i, model in enumerate(range(18, 22)):
        assert captures[25 + 2 * i] != captures[25 + 2 * i + 1], (
            "DX7 patch name",
            model,
        )
    for k, (model, harm) in enumerate(cases[:-1]):
        save_bitmap(captures[k], f"model-{model:02d}-harm-{harm:04x}-executed")
    OUT.mkdir(exist_ok=True)
    (OUT / "ui-report.json").write_text(
        json.dumps(
            {
                "candidate_sha256": report["candidate"]["sha256"],
                "model_pages": 25,
                "dx7_endpoint_pages": 8,
                "callee_registers": "PASS",
                "bitmap_guards": "PASS",
                "max_page_instructions": max(r.insns for r in res[2::3]),
            },
            indent=2,
        )
        + "\n"
    )


def test_rectangles_endpoints_and_repeat_redraw(fixture):
    pokes, page, sound, report = fixture
    pokes = dict(pokes)
    pokes[sound + 0xA2] = b"\x0b"
    pokes[sound + 0x48] = b"\0\0"
    hook = report["ui"]["symbols"]["plaits_ui_widget"]
    calls = []
    for code in range(275, 280):
        calls.append(clear())
        for raw in (0, 0x1800 if code == 275 else 0x7F00, 0):
            calls.append(
                {
                    "pc": hook,
                    "args": [page, BMP, 51, 27, code, raw, 0, 0, 0, 0, 0],
                    "reads": [(PIXELS - 16, 1056)],
                    "max": 200000,
                }
            )
    res = checked(
        pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", timeout=25).run(pokes, calls)
    )
    for k in range(5):
        assert bits(res[4 * k + 1]) == bits(res[4 * k + 3])
        assert bits(res[4 * k + 1]) != bits(res[4 * k + 2])


def test_stock_src_rendering_identical_for_all_stock_machines(fixture):
    pokes, page, sound, _ = fixture
    pokes = dict(pokes)
    calls = []
    for typ in range(7):
        src = 0x47F03000 + typ
        pokes[src] = bytes([typ])
        calls += [
            {"pc": 0x4013AB84, "args": [sound + 0xA2, src, 1]},
            clear(),
            page_call(page),
        ]
    m = pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", timeout=25)
    patched = checked(m.run(pokes, calls))
    original = checked(
        m.run({**pokes, aio.VT_SLOT: struct.pack(">I", aio.STOCK_WIDGET)}, calls)
    )
    assert [bits(r) for r in patched[2::3]] == [bits(r) for r in original[2::3]]


def test_25_short_names_guarded_and_unique(fixture):
    pokes, _page, _sound, report = fixture
    pokes = dict(pokes)
    buf = 0x47F05000
    pokes[buf - 4] = b"GUAR" + bytes(6) + b"DEND"
    pc = report["ui"]["symbols"]["plaits_model_short"]
    expected = [
        "VA",
        "ASYTR",
        "2FM",
        "FORMT",
        "ADDIT",
        "WVTBL",
        "CHORD",
        "SPECH",
        "SWARM",
        "NOISE",
        "DUST",
        "STRNG",
        "RINGS",
        "KICK",
        "SNARE",
        "HIHAT",
        "VCFVA",
        "PHASE",
        "DX7-1",
        "DX7-2",
        "DX7-3",
        "DX7-4",
        "TERRN",
        "STRMC",
        "SQUAR",
    ]
    calls = [
        {"pc": pc, "args": [raw, buf], "reads": [(buf - 4, 14)]}
        for raw in [*(i << 8 for i in range(25)), 25 << 8, 0xFFFFFFFF]
    ]
    res = checked(
        pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", timeout=25).run(pokes, calls)
    )
    for r, name in zip(res, expected + ["ERR", "ERR"], strict=True):
        d = r.reads[0]
        assert d[:4] == b"GUAR" and d[-4:] == b"DEND"
        assert d[4:10].split(b"\0", 1)[0].decode() == name
