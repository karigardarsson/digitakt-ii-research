"""Bounded native SourcePageView encoder events, not direct parameter writes.

Event layout is read from 0x401119b8 / 0x40111a12 / 0x40111aaa:
vtable, time words, encoder 1..8 at +12, signed delta +16, pressed +20.
Runs the actual source-page input callback 0x400184ea and its native setters.
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
    ap.add_argument("--delta", type=int, default=1)
    ap.add_argument("--pressed", type=int, default=0)
    ap.add_argument("--stop", type=lambda s: int(s, 0))
    ap.add_argument("--knob", type=int)
    ap.add_argument("--page", type=int)
    ap.add_argument("--break-at", type=lambda s: int(s, 0))
    ap.add_argument("--burst", type=int, default=1)
    a = ap.parse_args()
    pk_cfcall._built = True
    pokes, page, sound, _ = ui.fixture.__wrapped__()
    pokes[sound + 0xA2] = bytes([a.type])
    event = 0x47F08000
    data = 0x47F09000
    rows = []
    for p in ((a.page,) if a.page is not None else (0, 1)):
        for k in ((a.knob,) if a.knob is not None else range(8)):
            edits = {**pokes, page + 0x90: struct.pack(">I", p),
                     event: struct.pack(">5IB", 0x401F10C4, 0, 0, k + 1, a.delta & 0xffffffff, a.pressed)}
            if a.break_at:
                edits[a.break_at] = bytes.fromhex("4ef947fdf000")
            code = ([248, 292, 293, 294, 295, 296, 297, 298][k] if p == 0
                    else [299, 300, 298, 0, 0, 0, 0, 0][k])
            event_call = {"pc": 0x400184EA, "args": [page, event], "max": 300000,
                          **({"ret": a.stop} if a.stop else {}),
                          "reads": [(sound, 0x450), (data, 0x450), (page, 0x268), (event, 24),
                                    (0x47FDFC00, 1024)]}
            calls = [
                {"pc": 0x4006978E, "args": [page, code, -1]},
                {"pc": 0x4013AB84, "args": [data, sound, 0x450]},
                *[event_call for _ in range(a.burst)],
                {"pc": 0x4006978E, "args": [page, code, -1]},
            ]
            results = pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", timeout=60).run(edits, calls)
            r = results[-2]
            diff = ([i for i, (x, y) in enumerate(zip(r.reads[0], r.reads[1])) if x != y]
                    if len(r.reads) >= 2 else [])
            row = {"page": p, "knob": k, "ok": r.ok, "pc": hex(r.pc),
                   "error": r.error, "clobbered": r.clobbered,
                   "instructions": r.insns, "sound_changed_bytes": diff,
                   "value_before": results[0].d0, "value_after": results[-1].d0,
                   "event": r.reads[3].hex(),
                   "live_stack": r.reads[4][max(0, r.a[7] - 0x47FDFC00):][:64].hex(),
                   "regs_d": r.d, "regs_a": r.a}
            rows.append(row)
            print(json.dumps(row), flush=True)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps({"image_sha256": hashlib.sha256(ui.CANDIDATE.read_bytes()).hexdigest(),
                                   "type": a.type, "rows": rows}, indent=2) + "\n")


if __name__ == "__main__":
    main()
