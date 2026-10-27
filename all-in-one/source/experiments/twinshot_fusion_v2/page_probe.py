"""Bounded native second-SRC-page observation, not a storage allocation."""
import json
import struct

from patchkit import test_all_in_one25 as ui

from tools.patchkit import pk_cfcall


def run():
    pk_cfcall._built = True
    pokes, page, sound, _ = ui.fixture.__wrapped__()
    pokes[sound + 0xA2] = bytes([14])
    m = pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", timeout=60)
    result = []
    for index in (0, 1):
        p = {**pokes, page + 0x90: struct.pack(">I", index)}
        calls = [ui.clear(), ui.page_call(page)]
        from tools.patchkit.port_resolve import sections_of
        main = sections_of(ui.CANDIDATE)[3]
        mapper = struct.unpack_from(">I", main, 0x401EDD54 - 0x40000400)[0]
        calls += [{"pc": mapper, "args": [page, knob]} for knob in range(8)]
        rows = ui.checked(m.run(p, calls))
        ui.save_bitmap(ui.bits(rows[1]), f"v2-native-src-page-{index}")
        result.append({"page": index, "codes": [r.d0 for r in rows[2:]], "instructions": rows[1].insns})
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    run()
