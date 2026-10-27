"""Execute native page transition, periodic callback and drawing, bounded.

SourcePageView vfunc11 (0x40017a40) has a page-index-dependent waveform
update that draw-only fixtures did not cover. No real card/user data writes.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

from patchkit import test_all_in_one25 as ui
from tools.patchkit import pk_cfcall


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--type", type=int, default=14)
    ap.add_argument("--filter-depth", type=lambda s: int(s, 0))
    a = ap.parse_args()
    pk_cfcall._built = True
    pokes, page, sound, _ = ui.fixture.__wrapped__()
    pokes[sound + 0xA2] = bytes([a.type])
    if a.filter_depth is not None:
        pokes[sound + 0x14 + 2 * 38] = struct.pack(">H", a.filter_depth & 65535)
    calls, names = [], []
    for p in (0, 1, 0, 1):
        calls.append({"pc": 0x400691E2, "args": [page, p], "max": 200000})
        names.append(f"set-page-{p}")
        for k in range(3):
            calls += [{"pc": 0x40017A40, "args": [page], "max": 300000},
                      ui.clear(), ui.page_call(page)]
            names += [f"tick-{p}-{k}", "clear", f"draw-{p}-{k}"]
    results = pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", timeout=90).run(pokes, calls)
    rows = []
    for name, r in zip(names, results, strict=True):
        row = {"call": name, "ok": r.ok, "pc": hex(r.pc), "error": r.error,
               "clobbered": r.clobbered, "instructions": r.insns,
               "d": r.d, "a": r.a}
        rows.append(row)
        print(json.dumps(row), flush=True)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps({"image_sha256": hashlib.sha256(ui.CANDIDATE.read_bytes()).hexdigest(),
                                   "type": a.type, "rows": rows}, indent=2) + "\n")


if __name__ == "__main__":
    main()
