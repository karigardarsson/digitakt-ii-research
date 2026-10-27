"""Diagnostic direct native SourcePageView callbacks (key10 is YES, SRC is 2).

Button key/flags at +12/+16 are the contract of native 40114984/401149dc.
No external I/O or user Project is accessed by the host fixture.
Direct YES faults also occur on stock Slice: use panel_probe.py --cold for
scheduled UI evidence, not these context-incomplete calls.
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
    ap.add_argument("--key", type=int)
    a = ap.parse_args()
    pk_cfcall._built = True
    pokes, page, sound, _ = ui.fixture.__wrapped__()
    pokes[sound + 0xA2] = bytes([a.type])
    event = 0x47F08000
    rows = []
    for p in (0, 1):
        for key in ((a.key,) if a.key is not None else (10, 2, 41, 42, 43)):
            edits = {**pokes, event:struct.pack(">5I",0,0,0,key,0x11)}
            calls = [{"pc":0x400691E2,"args":[page,p],"max":200000},
                     {"pc":0x400181FE,"args":[page,event],"max":300000,
                      "reads":[(page+0x90,4),(sound,0x450),(0x47FDFC00,1024)]}]
            res = pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", timeout=90).run(edits,calls)
            r = res[-1]
            row = {"page_before":p,"key":key,"ok":r.ok,"pc":hex(r.pc),
                   "error":r.error,"clobbered":r.clobbered,"instructions":r.insns,
                   "page_after":int.from_bytes(r.reads[0],"big"),"d":r.d,"a":r.a}
            row["live_stack"] = r.reads[2][max(0,r.a[7]-0x47FDFC00):][:128].hex()
            rows.append(row)
            print(json.dumps(row),flush=True)
    a.report.write_text(json.dumps({"image_sha256":hashlib.sha256(ui.CANDIDATE.read_bytes()).hexdigest(),
                                   "rows":rows},indent=2)+"\n")


if __name__ == "__main__":
    main()
