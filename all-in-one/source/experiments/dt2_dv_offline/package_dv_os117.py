#!/usr/bin/env python3
"""Build the quarantined DT2 OS 1.17 DV V5.1 port candidate.

This is an offline packaging and verification pipeline.  It has no MIDI,
USB, Elektron Transfer, or device code.  The final .syx is published only
after every hash, six-section roundtrip, integrity, size, and bootstrap
depacker gate passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from dt2 import build
from dt2.authcode import DEVICE_BY_ID, DEVICES, compute_trailer, derive_key
from dt2.container import COUNT_OFF, decode_syx, sections
from dt2.elz import depack_section
from emu.oracle import depack as bootstrap_depack


STOCK_SHA256 = "26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5"
OVERLAY_SHA256 = "2c29de1de21907148d3148ec15829aa9105240b4632675ef4620d3807fd78b4f"
MANIFEST_SHA256 = "5ecfe2a06d1321ee4b197cb465b329f69bf66fe14ad3e0b9bd0b74ac4989da2c"
STOCK_MAIN_SHA256 = "a1e7b657b705eba1a19d81c33c1e11ba9c409816447ad74005d7bbf36da6d964"
PATCHED_MAIN_SHA256 = "c41dacf2f0a9c2eec88f8b857d72f69660d2ecac0d2f2ff6c92db2028defd4c8"
VENDOR_TREE_SHA256 = "4d77a5379a7a40b099f4e3ded5b35d5cf41ecae732b93717f1c5ac46ebc3d695"
VENDOR_COMMIT = "a5bce9a6af644386d900924082993a805e812874"
EXPECTED_IDS = [5, 2, 3, 4, 7, 8]
PACKED_IDS = {2, 3, 7, 8}
MAIN_BASE = 0x40000400

OVERLAY = REPO / "out/dt2-dv-offline/dv-v51-os117-overlay.bin"
MANIFEST = REPO / "out/dt2-dv-offline/dv-v51-os117-overlay-manifest.json"
QUARANTINE = REPO / "out/dt2-dv-offline/quarantine"
CANDIDATE_NAME = "DT2_DV_V5.1_OS1.17_DO_NOT_FLASH.syx"
REPORT_NAME = "DT2_DV_V5.1_OS1.17_DO_NOT_FLASH_packaging_report.json"
VENDOR = HERE / "vendor/elektron-firmware-tool"
VENDOR_FILES = [
    "LICENSE", "Makefile", "PROVENANCE.md", "README.md",
    "aplib.c", "aplib.h", "container.c", "container.h",
    "device.c", "device.h", "firmware.c", "firmware.h",
    "integrity.c", "integrity.h", "main.c", "report.c", "report.h",
    "transport.c", "transport.h", "util.h", "PINNED_COMMIT.txt",
]
TOOL_SOURCES = [
    "aplib.c", "container.c", "device.c", "firmware.c", "integrity.c",
    "main.c", "report.c", "transport.c",
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def vendor_digest() -> str:
    h = hashlib.sha256()
    for name in VENDOR_FILES:
        path = VENDOR / name
        if not path.is_file():
            raise RuntimeError(f"missing pinned encoder source: {path}")
        h.update(name.encode("utf-8") + b"\0" + path.read_bytes())
    return h.hexdigest()


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, **kwargs)
    if result.returncode:
        raise RuntimeError("command failed (%d): %s\n%s" %
                           (result.returncode, " ".join(cmd), result.stdout))
    return result


def classify(raw: bytes) -> tuple[str, bytes]:
    if len(raw) >= 8:
        length, stored_sum = struct.unpack_from(">II", raw, 0)
        if (length + 8 <= len(raw)
                and (sum(raw[8:8 + length]) & 0xFFFFFFFF) == stored_sum):
            return "packed", raw
        if stored_sum == 0:
            return "raw", raw[8:]
    return "raw", raw


def decode_all(path: Path) -> tuple[bytes, list[tuple[int, int, int, int]], dict]:
    container, entries = sections(str(path))
    ids = [sid for sid, _off, _length, _dest in entries]
    if ids != EXPECTED_IDS:
        raise RuntimeError(f"wrong section topology: {ids}, expected {EXPECTED_IDS}")
    decoded = {}
    for sid, off, length, dest in entries:
        raw = bytes(container[off:off + length])
        kind, payload = classify(raw)
        if sid in PACKED_IDS and kind != "packed":
            raise RuntimeError(f"section {sid} is not packed")
        if sid not in PACKED_IDS and kind != "raw":
            raise RuntimeError(f"section {sid} unexpectedly classified as packed")
        plain = depack_section(payload) if kind == "packed" else payload
        decoded[sid] = {
            "plain": plain, "stored": raw, "kind": kind, "dest": dest,
            "offset": off, "stored_size": length,
            "plain_sha256": sha256_bytes(plain),
        }
    return container, entries, decoded


def messages(raw: bytes) -> list[bytes]:
    out = []
    pos = 0
    while pos < len(raw):
        if raw[pos] != 0xF0:
            raise RuntimeError(f"expected F0 at file offset {pos:#x}")
        try:
            end = raw.index(0xF7, pos)
        except ValueError as exc:
            raise RuntimeError("unterminated SysEx message") from exc
        out.append(raw[pos:end + 1])
        pos = end + 1
    return out


def verify_integrity(path: Path) -> dict:
    raw = path.read_bytes()
    decoded = decode_syx(str(path))
    total_len, stored_checksum = struct.unpack_from(">II", decoded, 0)
    container = decoded[8:8 + total_len]
    if len(container) != total_len or total_len % 16:
        raise RuntimeError("invalid decoded container length/alignment")
    if stored_checksum != build.content_checksum(container):
        raise RuntimeError("content checksum mismatch")

    msgs = messages(raw)
    if len(msgs) < 3 or len(msgs[0]) != 16 or len(msgs[-1]) != 16:
        raise RuntimeError("missing 16-byte framing messages")
    data_msgs = msgs[1:-1]
    if not data_msgs or any(len(msg) != 128 for msg in data_msgs):
        raise RuntimeError("non-128-byte data message")
    first_body = msgs[0][1:-1]
    last_body = msgs[-1][1:-1]
    device_id = first_body[3]
    if device_id != 0x14 or first_body[7] != 0x0F:
        raise RuntimeError("wrong Digitakt II transport identity")

    def count(body: bytes) -> int:
        return body[11] * 16384 + body[12] * 128 + body[13]

    if count(first_body) != len(data_msgs) or count(last_body) != len(data_msgs):
        raise RuntimeError("framing message count mismatch")
    transfer_key = first_body[7]
    for index, msg in enumerate(data_msgs):
        body = msg[1:-1]
        if body[3] != 0x14 or body[5] != 0x7E:
            raise RuntimeError(f"wrong data-message identity at {index}")
        if body[125] != build.packet_checksum(body, transfer_key):
            raise RuntimeError(f"packet checksum mismatch at {index}")

    device = DEVICE_BY_ID.get(device_id)
    if device != "dt2":
        raise RuntimeError("HMAC device selection is not Digitakt II")
    trailer = container[-32:]
    computed = compute_trailer(decoded, derive_key(**DEVICES[device]))
    if trailer != computed:
        raise RuntimeError("HMAC-SHA256 trailer mismatch")
    return {
        "file_sha256": sha256_bytes(raw),
        "file_size": len(raw),
        "container_size": total_len,
        "message_count": len(data_msgs),
        "content_checksum": f"0x{stored_checksum:08x}",
        "hmac_sha256": trailer.hex(),
        "packet_checksums": f"{len(data_msgs)}/{len(data_msgs)}",
        "post_container_padding": len(decoded) - 8 - total_len,
    }


def apply_manifest(stock_main: bytes, overlay: bytes, manifest: dict) -> tuple[bytes, dict]:
    if sha256_bytes(stock_main) != STOCK_MAIN_SHA256:
        raise RuntimeError("wrong decompressed stock MAIN hash")
    if manifest.get("stock_main_sha256") != STOCK_MAIN_SHA256:
        raise RuntimeError("manifest stock MAIN prerequisite changed")
    if manifest.get("total_static_patch_bytes") != 2082:
        raise RuntimeError("manifest patch coverage is not 2,082 bytes")

    cave = manifest["cave"]
    if cave.get("bundle_sha256") != OVERLAY_SHA256:
        raise RuntimeError("manifest overlay hash changed")
    cave_start = int(cave["start"], 16)
    cave_end = int(cave["end"], 16)
    payload_end = int(cave["payload_end"], 16)
    pointer = int(cave["pointer_word"], 16)
    latches = manifest["diagnostic"]["latches"]
    latch_words = tuple(int(latches[name], 16) for name in
                        ("hook_seen", "malloc_result", "publish_ok"))
    if (cave_start, cave_end, payload_end, pointer) != (
            0x403117C4, 0x40312000, 0x40311FB2, 0x40311FF0):
        raise RuntimeError("diagnostic cave topology changed")
    if latch_words != (0x40311FE4, 0x40311FE8, 0x40311FEC):
        raise RuntimeError("diagnostic latch topology changed")
    if len(overlay) != cave["payload_bytes"] or cave_start + len(overlay) != payload_end:
        raise RuntimeError("overlay payload length/end mismatch")

    patched = bytearray(stock_main)
    cave_off = cave_start - MAIN_BASE
    if patched[cave_off:cave_off + len(overlay)] != bytes(len(overlay)):
        raise RuntimeError("cave is not zero-filled at the frozen placement")
    if patched[pointer - MAIN_BASE:pointer - MAIN_BASE + 4] != b"\0\0\0\0":
        raise RuntimeError("runtime pointer word is not pristine zero")
    for word_addr in latch_words:
        if patched[word_addr - MAIN_BASE:word_addr - MAIN_BASE + 4] != b"\0\0\0\0":
            raise RuntimeError("diagnostic latch word is not pristine zero")

    ranges = [("cave", cave_off, cave_off + len(overlay))]
    patched[cave_off:cave_off + len(overlay)] = overlay
    patch_records = []
    for item in manifest.get("patches", []):
        name = item["name"]
        address = int(item["address"], 16)
        off = address - MAIN_BASE
        old = bytes.fromhex(item["stock"])
        new = bytes.fromhex(item["overlay"])
        if len(old) != item["bytes"] or len(new) != len(old):
            raise RuntimeError(f"length mismatch at patch {name}")
        if not 0 <= off <= len(patched) - len(old):
            raise RuntimeError(f"patch {name} is outside MAIN")
        if bytes(patched[off:off + len(old)]) != old:
            raise RuntimeError(f"stock byte prerequisite failed at {name}")
        ranges.append((name, off, off + len(old)))
        patched[off:off + len(new)] = new
        patch_records.append({"name": name, "address": f"0x{address:08x}",
                              "bytes": len(new), "file_offset": f"0x{off:x}"})

    if len(patch_records) != 10:
        raise RuntimeError(f"expected 10 patch sites, got {len(patch_records)}")
    for index, left in enumerate(ranges):
        for right in ranges[index + 1:]:
            if not (left[2] <= right[1] or right[2] <= left[1]):
                raise RuntimeError(f"patch collision: {left[0]} with {right[0]}")
    coverage = sum(end - start for _name, start, end in ranges)
    if coverage != 2082:
        raise RuntimeError(f"patch coverage is {coverage}, expected 2082")
    out = bytes(patched)
    if len(out) != len(stock_main) or sha256_bytes(out) != PATCHED_MAIN_SHA256:
        raise RuntimeError("patched MAIN length/hash gate failed")
    return out, {"sites": patch_records, "site_count": len(patch_records),
                 "coverage_bytes": coverage, "collisions": 0,
                 "patched_main_sha256": sha256_bytes(out)}


def compile_tool(build_dir: Path) -> Path:
    if vendor_digest() != VENDOR_TREE_SHA256:
        raise RuntimeError("pinned encoder source tree hash mismatch")
    pin = (VENDOR / "PINNED_COMMIT.txt").read_text()
    if f"commit={VENDOR_COMMIT}" not in pin:
        raise RuntimeError("pinned encoder commit marker mismatch")
    binary = build_dir / "elektron-firmware-tool"
    cmd = ["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-I", str(VENDOR),
           "-o", str(binary)] + [str(VENDOR / name) for name in TOOL_SOURCES]
    run(cmd)
    return binary


def verify_bootstrap(decoded: dict) -> dict:
    bootstrap = decoded[2]["plain"]
    results = {}
    for sid in sorted(PACKED_IDS):
        expected = decoded[sid]["plain"]
        got = bootstrap_depack(
            bootstrap, decoded[sid]["stored"],
            out_cap=max(len(expected) + 0x10000, 0x100000))
        if got != expected:
            raise RuntimeError(f"bootstrap depacker mismatch for section {sid}")
        results[str(sid)] = {
            "stored_size": decoded[sid]["stored_size"],
            "plain_size": len(expected),
            "plain_sha256": sha256_bytes(got),
            "result": "PASS",
        }
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock", type=Path, help="verified stock Digitakt II OS 1.17 .syx")
    args = parser.parse_args(argv)
    started = time.perf_counter()

    stock = args.stock.resolve()
    if not stock.is_file() or sha256_file(stock) != STOCK_SHA256:
        raise SystemExit("wrong or missing stock Digitakt II OS 1.17 file")
    if sha256_file(OVERLAY) != OVERLAY_SHA256:
        raise SystemExit("frozen overlay hash mismatch")
    if sha256_file(MANIFEST) != MANIFEST_SHA256:
        raise SystemExit("frozen manifest hash mismatch")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("device") != "Elektron Digitakt II" or manifest.get("os") != "1.17":
        raise SystemExit("manifest device/OS identity mismatch")
    if manifest.get("stock_syx_sha256") != STOCK_SHA256:
        raise SystemExit("manifest stock .syx prerequisite mismatch")

    stock_container, stock_entries, stock_decoded = decode_all(stock)
    if stock_container[:COUNT_OFF] != b"ELE3\x00\x00\x00+0082        1.17\x00\x00\x00\x00":
        raise SystemExit("stock ELE3 product/version header changed")
    stock_integrity = verify_integrity(stock)
    patched_main, manifest_result = apply_manifest(
        stock_decoded[3]["plain"], OVERLAY.read_bytes(), manifest)

    QUARANTINE.mkdir(parents=True, exist_ok=True)
    candidate_path = QUARANTINE / CANDIDATE_NAME
    report_path = QUARANTINE / REPORT_NAME
    warning_path = QUARANTINE / "README_DV_V5.1_OS1.17_HARDWARE_UNTESTED.txt"

    with tempfile.TemporaryDirectory(prefix="dt2-dv-package-") as temp_name:
        temp = Path(temp_name)
        tool = compile_tool(temp)

        stock_roundtrip = temp / "stock-six-section-roundtrip.syx"
        stock_run = run([str(tool), "-i", str(stock), "-r", "-o", str(stock_roundtrip)])
        if stock_roundtrip.read_bytes() != stock.read_bytes():
            raise RuntimeError("stock six-section repack is not byte-identical")
        _c, _e, stock_roundtrip_decoded = decode_all(stock_roundtrip)
        for sid in EXPECTED_IDS:
            if stock_roundtrip_decoded[sid]["plain"] != stock_decoded[sid]["plain"]:
                raise RuntimeError(f"stock roundtrip section {sid} mismatch")

        patched_main_path = temp / "patched-main.bin"
        patched_main_path.write_bytes(patched_main)
        candidate_temp = temp / CANDIDATE_NAME
        candidate_run = run([
            str(tool), "-i", str(stock), "-c", "3", str(patched_main_path),
            "-l", "0", "-o", str(candidate_temp),
        ])
        if candidate_temp.stat().st_size > stock.stat().st_size:
            raise RuntimeError(
                "size gate failed: candidate %d > stock %d" %
                (candidate_temp.stat().st_size, stock.stat().st_size))

        candidate_container, candidate_entries, candidate_decoded = decode_all(candidate_temp)
        if candidate_container[:COUNT_OFF] != stock_container[:COUNT_OFF]:
            raise RuntimeError("ELE3 metadata/product/version header changed")
        if [(sid, dest) for sid, _off, _length, dest in candidate_entries] != [
                (sid, dest) for sid, _off, _length, dest in stock_entries]:
            raise RuntimeError("section order or destination changed")
        for sid in EXPECTED_IDS:
            expected = patched_main if sid == 3 else stock_decoded[sid]["plain"]
            if candidate_decoded[sid]["plain"] != expected:
                raise RuntimeError(f"candidate section {sid} decompressed mismatch")
            if len(candidate_decoded[sid]["plain"]) != len(stock_decoded[sid]["plain"]):
                raise RuntimeError(f"candidate section {sid} decompressed size changed")

        candidate_integrity = verify_integrity(candidate_temp)
        bootstrap_results = verify_bootstrap(candidate_decoded)
        candidate_sha = sha256_file(candidate_temp)

        existing = candidate_path.read_bytes() if candidate_path.exists() else None
        candidate_bytes = candidate_temp.read_bytes()
        if existing is not None and existing != candidate_bytes:
            raise RuntimeError("existing quarantined candidate differs; refusing overwrite")
        if existing is None:
            staged = QUARANTINE / (CANDIDATE_NAME + ".verified.tmp")
            staged.write_bytes(candidate_bytes)
            os.replace(staged, candidate_path)

    elapsed = time.perf_counter() - started
    self_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    child_peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    report = {
        "status": "PASS",
        "warning": "QUARANTINED OFFLINE CANDIDATE. HARDWARE ACCEPTANCE UNTESTED.",
        "device": "Elektron Digitakt II",
        "os": "1.17",
        "inputs": {
            "stock_sha256": STOCK_SHA256,
            "overlay_sha256": OVERLAY_SHA256,
            "manifest_sha256": MANIFEST_SHA256,
        },
        "encoder": {
            "upstream": "https://github.com/mischa85/elektron-firmware-tool",
            "commit": VENDOR_COMMIT,
            "source_tree_sha256": VENDOR_TREE_SHA256,
            "algorithm": "aPLib-variant LZ77 + interlaced Elias-gamma, bounded match search",
            "compression_level": 0,
        },
        "stock_roundtrip": {
            "result": "PASS", "section_count": 6,
            "byte_identical_syx": True, "sha256": STOCK_SHA256,
            "tool_output": stock_run.stdout.strip(),
        },
        "manifest": manifest_result,
        "stock_integrity": stock_integrity,
        "candidate": {
            "path": str(candidate_path), "sha256": candidate_sha,
            "size": candidate_path.stat().st_size,
            "stock_size": stock.stat().st_size,
            "size_delta": candidate_path.stat().st_size - stock.stat().st_size,
            "size_ratio": candidate_path.stat().st_size / stock.stat().st_size,
            "section_count": 6,
            "section_results": {
                str(sid): {
                    "result": "PASS", "kind": candidate_decoded[sid]["kind"],
                    "stored_size": candidate_decoded[sid]["stored_size"],
                    "plain_size": len(candidate_decoded[sid]["plain"]),
                    "plain_sha256": candidate_decoded[sid]["plain_sha256"],
                } for sid in EXPECTED_IDS
            },
            "tool_output": candidate_run.stdout.strip(),
        },
        "candidate_integrity": candidate_integrity,
        "bootstrap_depacker": bootstrap_results,
        "evidence": {
            "packaging": "HOST",
            "bootstrap_depacker": "SYNTHETIC UNICORN using stock DT2 bootstrap code",
            "hardware_acceptance": "UNTESTED",
            "hardware_recovery": "UNTESTED",
        },
        "elapsed_seconds": elapsed,
        "peak_rss_kib": max(self_peak, child_peak),
        "self_peak_rss_kib": self_peak,
        "child_peak_rss_kib": child_peak,
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    warning_path.write_text(
        "DT2 DV V5.1 OS 1.17 PORT QUARANTINE\n\n"
        "The .syx in this directory passed offline packaging checks only.\n"
        "It is not hardware-safe, is not approved for flashing, and must not\n"
        "be opened in Elektron Transfer or sent over MIDI/USB.\n",
        encoding="utf-8")
    print(json.dumps({
        "status": "PASS", "candidate": str(candidate_path),
        "sha256": candidate_sha, "size": candidate_path.stat().st_size,
        "stock_size": stock.stat().st_size, "sections": "6/6",
        "bootstrap_depacker": "4/4", "elapsed_seconds": elapsed,
        "peak_rss_kib": max(self_peak, child_peak),
        "report": str(report_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
