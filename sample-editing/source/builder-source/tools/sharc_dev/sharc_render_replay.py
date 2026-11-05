#!/usr/bin/env python3
"""Bounded independent Python-SHARC replay of logged post-queue DMA bytes.

The ignored native rendered-input log supplies exact bytes/ordinals, not a
new queue policy. Import the SHRD start state from the locally checked state
pack; compare its packed image to the independently loaded local section.
Watch the suspect DM word during each Python frame handler; a clean run is
NOT evidence of a writer on an intermittent native stop. No Device claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from tools.sharc_dev import sharc_diff as sd  # noqa: E402
from tools.sharc_dev import sharc_harness as h  # noqa: E402
from tools.sharc_dev import sharc_run as sr  # noqa: E402
from tools.sharc_dev import sharc_trace as st  # noqa: E402
from tools.sharc_dev import sharc_transpile_run as tr  # noqa: E402
from tools.sharc_dev.sharcldr import LoadedMemory  # noqa: E402

MAX_RECORDS = 256
MAX_STEPS_PER_FRAME = 4_000_000


def logged_inputs(path: Path, count: int) -> list[tuple[bytes, str]]:
    if not 1 <= count <= MAX_RECORDS or path.stat().st_size > 4_000_000:
        raise ValueError("unbounded SHARC rendered-input artifact")
    records = path.read_text().splitlines()
    if len(records) < count or len(records) > MAX_RECORDS:
        raise ValueError("missing/unbounded rendered-input records")
    result = []
    for ordinal, text in enumerate(records[:count]):
        record = json.loads(text)
        data = bytes.fromhex(record["bytes_hex"])
        if (
            record["ordinal"] != ordinal
            or record["byte_len"] != len(data)
            or not 0 < len(data) <= 4096
            or record["sha256"] != hashlib.sha256(data).hexdigest()
            or record["source"] not in ("taken", "repeat")
        ):
            raise ValueError(f"rendered-input mismatch at ordinal {ordinal}")
        result.append((data, record["source"]))
    return result


def pack_start(pack: bytes, data: LoadedMemory) -> st.State:
    """Use the pack's state, never rerun SHARC init or invent an overlay."""
    if pack[:8] != b"SHFP\x01\0\0\0":
        raise ValueError("not a v1 SHFP state pack")
    offset = 8
    size = struct.unpack_from("<I", pack, offset)[0]
    offset += 4
    if size > 80_000_000 or offset + size + 4 > len(pack):
        raise ValueError("SHFP image exceeds bound")
    image = pack[offset : offset + size]
    offset += size
    if image != tr.pack_image(data):
        raise ValueError("state pack image differs from checked SHARC section")
    size = struct.unpack_from("<I", pack, offset)[0]
    offset += 4
    if size > 80_000_000 or offset + size > len(pack):
        raise ValueError("SHRD state exceeds bound")
    fields = sd.unpack_state(pack[offset : offset + size])
    # SHRD maps range address -> {length, data}; import_state expects
    # `address` also inside each value. Restore it losslessly from the key.
    fields["memory_ranges"] = {
        address: dict(item, address=address)
        for address, item in fields["memory_ranges"].items()
    }
    return sd.import_state(fields, data=data)


def native_agreement(
    observations: list[dict], native: list[dict], stopped: str | None
) -> str:
    mismatch = next(
        (
            f"frame {a['ordinal']}: Python {a['terminal']}/{a['dma_instructions'] + a['handler_instructions']} "
            f"versus native {b['frame_end']}/{b['instructions']}"
            for a, b in zip(observations, native, strict=False)
            if a["ordinal"] != b["ordinal"]
            or a["source"] != b["source"]
            or a["sha256"] != b["sha256"]
            or a["terminal"] != "frame-returned"
            or b["frame_end"] != "clean"
            or a["dma_instructions"] + a["handler_instructions"] != b["instructions"]
        ),
        None,
    )
    return mismatch or (
        "full" if len(observations) == len(native) and stopped is None else "incomplete"
    )


def replay(
    pack: Path,
    inputs: Path,
    output: Path,
    count: int,
    limit: int,
    pack_sha256: str,
) -> dict:
    if not 1 <= limit <= count * MAX_STEPS_PER_FRAME:
        raise ValueError("explicit instruction limit outside bounded range")
    out = (ROOT / "out").resolve()
    target = output.resolve()
    if not target.parent.is_relative_to(out):
        raise ValueError("derived report must stay under ignored out/")
    source = logged_inputs(inputs, count)
    packed = pack.read_bytes()
    actual_sha = hashlib.sha256(packed).hexdigest()
    if pack_sha256 != actual_sha:
        raise ValueError("state pack differs from operator-checked SHA-256")
    image = "dt2-1.16"
    data = h.load_image_memory(image)
    state = pack_start(packed, data)
    runner = sr.Runner(data, state.pc_sw, diagnose_unknown=True)
    runner.state = state
    watch = sr.Watchpoint(
        0x254D94,
        0x254D98,
        on_read=True,
        on_write=True,
        stop=False,
        label="suspect-dm-source",
    )
    observations = []
    actual_steps = 0
    stopped = None
    for ordinal, (raw, source_kind) in enumerate(source):
        if actual_steps >= limit:
            stopped = "instruction limit"
            break
        # `raw` is already post-halfword-swap; never swap a second time.
        h.write_dma_transfer(runner.state, image, raw, swap16=False)
        runner = h.drive_dma_completion(runner, image)
        dma_steps = runner.instructions
        allowance = min(MAX_STEPS_PER_FRAME, limit - actual_steps - dma_steps)
        if allowance < 1:
            stopped = "instruction limit before frame handler"
            break
        runner, result = h.call_frame_collect_all(
            runner,
            image,
            patch_table=h.FRAME_PATCH_TABLE,
            max_steps=allowance,
            watchpoints=(watch,),
        )
        steps = dma_steps + result.instructions
        actual_steps += steps
        terminal = result.terminal
        hits = [hit.to_json() for hit in runner.watch_log[:32]]
        observations.append(
            {
                "ordinal": ordinal,
                "source": source_kind,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "dma_instructions": dma_steps,
                "handler_instructions": result.instructions,
                "terminal": terminal.category,
                "pc": terminal.pc,
                "suspect_word_accesses": hits,
                "suspect_word_accesses_truncated": len(runner.watch_log) > len(hits),
            }
        )
        if terminal.category != "frame-returned":
            stopped = f"{terminal.category} at {terminal.pc:#x}"
            break
    report = {
        "format_version": 1,
        "engine": "independent Python SHARC interpreter",
        "pack_sha256": actual_sha,
        "rendered_input_sha256": hashlib.sha256(inputs.read_bytes()).hexdigest(),
        "requested_frames": count,
        "observed_frames": len(observations),
        "instruction_limit": limit,
        "actual_python_instructions": actual_steps,
        "stop": stopped,
        "frames": observations,
    }
    native = [json.loads(line) for line in inputs.read_text().splitlines()[:count]]
    report["native_agreement"] = native_agreement(observations, native, stopped)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pack", required=True, type=Path)
    p.add_argument("--pack-sha256", required=True)
    p.add_argument("--inputs", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--frames", type=int, required=True)
    p.add_argument("--limit", type=int, required=True)
    a = p.parse_args()
    result = replay(a.pack, a.inputs, a.output, a.frames, a.limit, a.pack_sha256)
    print(
        f"Python SHARC: {result['observed_frames']} frames, "
        f"{result['actual_python_instructions']} actual instructions; "
        f"stop={result['stop']}; native agreement={result['native_agreement']}"
    )
    return 0 if result["native_agreement"] == "full" else 1


if __name__ == "__main__":
    raise SystemExit(main())
