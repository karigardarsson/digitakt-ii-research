"""Bounded caller-stage snapshots; diagnostic only, no firmware output."""
import json
import struct
from pathlib import Path

from patchkit import test_all_in_one25 as u

from tools.patchkit import pk_cfcall, port_resolve
from tools.patchkit import pk_integrate_dv_zoom as d

p = json.loads(Path("../../../../../../dv-offline-v1/out/twinshot-admission/module-seed-v4.plan.json").read_text())
pc = next(v for k, v in p["symbols"].items() if k.endswith(":twinshot_widget"))
main = port_resolve.sections_of(u.CANDIDATE)[3]
off = d.source_for_loaded_address(main, pc) - d.MAIN_BASE
body = main[off:off + 512]
stops = [(name, pc + body.index(bytes.fromhex(op))) for name, op in
         (("before-stock", "4eb947ea0000"), ("before-fill", "4eb9401131ae"),
          ("before-text", "4eb940113dc8"))]
stops.append(("after-text", 0x47FDF000))
pk_cfcall._built = True
for name, stop in stops:
    pokes, page, sound, _ = u.fixture.__wrapped__()
    pokes[sound + 0xA2] = bytes([12])
    pokes[0x401EDC98 + 45 * 4] = struct.pack(">I", 0x47EA0000)
    results = pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx").run(pokes, [u.clear(), u.page_call(page),
        {"pc": pc, "args": [page, u.BMP, 103, 27, 282, 7, 0, 0, 0, 0, 0],
         "ret": stop, "reads": [(u.PIXELS - 16, 1056)], "max": 200000}])
    assert results[-1].ok, results[-1].error
    u.save_bitmap(u.bits(results[-1]), name)
    print(name, sum(x.bit_count() for x in u.bits(results[-1])), flush=True)
