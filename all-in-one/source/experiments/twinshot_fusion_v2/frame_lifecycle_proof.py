"""Actual native DSP init/block/AMP route with reconstructed CF frames.

No panel, RTOS or physical transport claim. STOP mask bytes must match the
separate native CF STOP packing proof. Never override gain/ACTIVE/latch/copy
gates to force a result. Phases permit a bounded native-init checkpoint.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

from native_dual_proof import Native

from tools.emulator.snapread import Snapshot
from tools.patchkit.port_resolve import sections_of
from tools.sharc_dev import sharc, sharc_symbols
from tools.sharc_dev import sharc_harness as h
from tools.sharc_dev import sharc_run as sr
from tools.sharc_dev.sharc_harness import layout
from tools.sharc_dev.sharcldr import LoadedMemory


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("phase", choices=("init", "frames"))
    ap.add_argument("--mode", type=int, choices=(0,2,3,4), default=0)
    for name in ("image", "plan", "out"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    memory = LoadedMemory.from_stream(sections_of(a.image)[7])
    profile = sharc_symbols.resolve(sharc.load("dt2-1.17"), device="dt2", reference=sharc.load("dt2-1.17"))
    layout._PROFILE_CACHE["dt2-1.17"] = profile
    state = a.out / "native-init.snapshot"
    if a.phase == "init":
        init = h.run_init(memory, "dt2-1.17", max_steps=2_000_000)
        assert init.ran, init.error
        sr.save_snapshot(init.runner, str(state))
        report = {"status": "PASS", "image_sha256": hashlib.sha256(a.image.read_bytes()).hexdigest(),
                  "instructions": init.runner.instructions, "state": str(state)}
        (a.out / "native-init.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report), flush=True)
        return
    seed = sr.load_snapshot(str(state), memory)
    snapshot = Snapshot("out/runs/patchkit/run-plaits-lfo-probe/cards/f5c765a4acfc/ready/digi-audio-m5.snap")
    source = snapshot.read(0x80005348, 2748)
    assert source[:2] == b"\0\3"
    native = Native(memory, approx_recips=True)
    native.runner = seed
    native.load_slots()
    runner = native.runner
    runner.state.approx_recips = True
    rows = []
    for event in ("trigger", "hold", "hold", "release", "hold", "hold", "stop", "idle", "stop", "retrigger", "hold"):
        tx = bytearray(source)
        def put(p, value, tx=tx):
            struct.pack_into(">H", tx, p, value)
        trig = event in ("trigger", "retrigger")
        stop = event == "stop"
        release = event == "release"
        for offset, value in ((0x22, int(trig)), (0x24, 65535 if stop else int(release)),
                              (0x26, int(trig)), (0x28, 65535 if stop else int(release)),
                              (0x2A, 65535 if stop else 0), (2, 60 << 8),
                              (0x34, 100 << 8), (0x94, 14)):
            put(offset, value)
        for off, raw in enumerate((0x4000, 0, a.mode << 8, 7, 0, 0, 0x4000, 0x7F00, 31, 0x4000)):
            put(0xDA + 2 * off, raw)
        h.write_dma_transfer(runner.state, "dt2-1.17", bytes(tx))
        runner = h.drive_dma_completion(runner, "dt2-1.17")
        runner = runner.fresh_call(profile.block_handler)
        runner.breakpoints = frozenset()
        result = runner.run(300000)
        assert result.halt.reason == "return without followed call", result.halt.to_json()
        native.runner = runner
        plan = json.loads(a.plan.read_text())
        p = next(v for k, v in plan["symbols"].items() if k.endswith(":tf_states."))
        xs = struct.unpack("<32f", native.data(native.get(0x24EF2C), 128))
        rows.append({"event": event, "peak_after_amp": max(map(abs, xs)),
                     "ready": native.get(p + 944), "a_active": native.get(p + 0x1B8, 1),
                     "b_active": native.get(p + 472 + 0x1B8, 1),
                     "release_latch": native.get(p + 1240),
                     "working_frame_masks": native.data(0x2558DC + 0x22, 10).hex()})
        print(rows[-1], flush=True)
    assert any(r["peak_after_amp"] > 0 for r in rows[:3]), rows
    assert rows[4]["release_latch"] == rows[5]["release_latch"] == 1, rows
    assert all(r["a_active"] == r["b_active"] == 0 for r in rows if r["event"] in ("stop", "idle")), rows
    assert rows[-1]["ready"] == 1 and rows[-1]["peak_after_amp"] > 0, rows
    report = {"status": "PASS", "sha256": hashlib.sha256(a.image.read_bytes()).hexdigest(),
              "mode":a.mode,
              "rows": rows, "tier": "native DSP init/frame/AMP, reconstructed CF events; not natural transport or hardware"}
    filename="frame-lifecycle.json" if a.mode==0 else f"frame-lifecycle-mode{a.mode}.json"
    (a.out / filename).write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
