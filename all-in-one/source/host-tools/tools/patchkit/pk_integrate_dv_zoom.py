#!/usr/bin/env python3
"""Combine the verified Plaits image with Musical WAVE V4 and Zoom V11.

This is deliberately a source-image integration, not a merge of patched SYX
files.  It keeps the patchkit reset loader already present in the Plaits
image, applies the guarded V5.1 overlay, and appends the V11 payload as one
more reset-loader segment at an unoccupied expansion address.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
MAIN_BASE = 0x40000400
STOCK_MAIN_END = 0x4031FF60
V11_ORIGINAL_EXP = 0x47E8B000
V11_EXP = 0x47E91000
V11_EXP_LIMIT = 0x47FE0000
V11_SOURCE_SHA256 = "4191be97fd9c2f93c1fd3dae7f189ace28664acafcff51edf7a04af49f4ecd0e"
V11_ORIGINAL_PAYLOAD_SHA256 = "320ea0b5904a3345ce39b8f2429b845939895784e42891fda75958fe3086b2bf"
V11_PAYLOAD_BYTES = 1196
MUSICAL_SOURCE_NAME = "musical_deja_vu_v1.s"
MUSICAL_SOURCE_SHA256 = "ba868b3d766c87ac9746c41eb444541fddac11f532b44ef6a8a1b786bc1983b5"
MUSICAL_PAYLOAD_SHA256 = "9ff8fd1e889df6d2115b0404e218d015bf0466eeba314ad5f1912890465146c9"
MUSICAL_PAYLOAD_BYTES = 3160
MUSICAL_V4_CANDIDATE_SHA256 = "850ad57a7035dbb93a001737aedfe302c8bb1c4fbcd0d75a297b826ac8e73122"
MUSICAL_V4_MAIN_SHA256 = "3b4bdc1f9abf8ae17ce84d18ee88fc7a3d5fc85725e4c68b9f7feb0b8354e43a"
MUSICAL_V4_MAIN_BYTES = 3278792
MUSICAL_V4_PAYLOAD_SOURCE = 0x4031FF60
MUSICAL_V4_ORIGINAL_EXP = 0x47E8B000
MUSICAL_V4_SYMBOLS = {
    "source_zoom_hook": 0x47E8B000,
    "source_zoom_rotation_hook": 0x47E8B09C,
    "oneshot_zoom_wrapper": 0x47E8B18A,
    "zoom_factory_hook": 0x47E8B3DA,
    "waveform_view_init_hook": 0x47E8B414,
    "musical_dv_dispatch": 0x47E8B4AC,
    "musical_dv_gate": 0x47E8B53A,
    "musical_dv_clamp": 0x47E8B5AC,
    "musical_dv_post_apply": 0x47E8B616,
    "musical_wave_formatter": 0x47E8B7D0,
    "musical_hirajoshi_map": 0x47E8B9D6,
    "musical_whole_tone_map": 0x47E8BA56,
    "musical_dorian_map": 0x47E8BAD6,
    "musical_lydian_map": 0x47E8BB56,
    "musical_octatonic_map": 0x47E8BBD6,
}
PLAIT_FULLRANGE_SHA256 = "6e0ddbf485c339bfa14da0032cd9f972372093c22fce63c3e9b2ad8f31d659ee"
PK_LOADER_SOURCE = 0x403117C4
PK_LOADER_DEST = 0x40311C92
PK_LOADER_BYTES = 54
PK_LOADER_SHA256 = "fb53486c3a94ee5373ef62d8f22941d825aba679cdc51d6ae09a5ce5e82c5445"
PK_RESET_HOOK = 0x4000053E
PK_RESET_OLD = bytes.fromhex("4eb9403117c44e71")
PF_TABLE = 0x47E8C64C
LFO_WAVE_ROWS = (79, 89, 99)
INTRO_SCENES = 0x402A1B64
PLAIT_SPLASH_SCENES = bytes.fromhex(
    "47e909a80000002d0000000000000000" * 5
)
STOCK_INTRO_SCENES = bytes.fromhex(
    "400d1512000000af0000000000000000"
    "400d0dce000000b40000000000000000"
    "400d117c000000b40000000000000000"
    "400d11f0000000b40000000000000000"
    "400d120c000000aa0000000000000000"
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def at(main: bytes | bytearray, address: int, size: int) -> bytes:
    offset = address - MAIN_BASE
    if offset < 0 or offset + size > len(main):
        raise RuntimeError(f"MAIN range outside image: {address:#x}+{size:#x}")
    return bytes(main[offset : offset + size])


def put(main: bytearray, address: int, data: bytes) -> None:
    offset = address - MAIN_BASE
    if offset < 0 or offset + len(data) > len(main):
        raise RuntimeError(f"MAIN write outside image: {address:#x}+{len(data):#x}")
    main[offset : offset + len(data)] = data


def load_v11(dv_root: Path, *, musical: bool):
    source_dir = dv_root / "experiments" / "dt2_dv_offline"
    source_name = MUSICAL_SOURCE_NAME if musical else "oneshot_zoom_functional_v11.s"
    source_hash = MUSICAL_SOURCE_SHA256 if musical else V11_SOURCE_SHA256
    source = source_dir / source_name
    if not source.is_file() or sha(source.read_bytes()) != source_hash:
        raise RuntimeError("frozen DV/Zoom assembly source hash gate failed")
    sys.path.insert(0, str(source_dir))
    try:
        pack = importlib.import_module("package_dv_os117")
    finally:
        sys.path.pop(0)
    if sha(pack.OVERLAY.read_bytes()) != pack.OVERLAY_SHA256:
        raise RuntimeError("frozen V5.1 OS 1.17 overlay hash gate failed")
    if sha(pack.MANIFEST.read_bytes()) != pack.MANIFEST_SHA256:
        raise RuntimeError("frozen V5.1 OS 1.17 manifest hash gate failed")
    manifest = json.loads(pack.MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("device") != "Elektron Digitakt II" or manifest.get("os") != "1.17":
        raise RuntimeError("V5.1 manifest is not the expected DT2 OS 1.17 overlay")
    return pack, manifest, source


def assemble(source: Path, tools: Path, address: int, work: Path, *, musical: bool) -> tuple[bytes, dict[str, int]]:
    names = {
        "source_zoom_hook",
        "source_zoom_rotation_hook",
        "oneshot_zoom_wrapper",
        "zoom_factory_hook",
        "waveform_view_init_hook",
    }
    if musical:
        names.update({
            "musical_dv_dispatch",
            "musical_dv_gate",
            "musical_dv_clamp",
            "musical_dv_post_apply",
            "musical_wave_formatter",
            "musical_hirajoshi_map",
            "musical_whole_tone_map",
            "musical_dorian_map",
            "musical_lydian_map",
            "musical_octatonic_map",
        })
    obj = work / f"zoom-{address:x}.o"
    elf = work / f"zoom-{address:x}.elf"
    binary = work / f"zoom-{address:x}.bin"
    for name in ("m68k-linux-gnu-as", "m68k-linux-gnu-ld", "m68k-linux-gnu-objcopy", "m68k-linux-gnu-nm"):
        if not (tools / name).is_file():
            raise RuntimeError(f"missing required ColdFire tool: {tools / name}")
    subprocess.run([str(tools / "m68k-linux-gnu-as"), "-mcpu=5475", str(source), "-o", str(obj)], check=True)
    subprocess.run([str(tools / "m68k-linux-gnu-ld"), f"-Ttext={address:#x}", "-e", "source_zoom_hook", "-o", str(elf), str(obj)], check=True)
    subprocess.run([str(tools / "m68k-linux-gnu-objcopy"), "-O", "binary", "-j", ".text", str(elf), str(binary)], check=True)
    symbols: dict[str, int] = {}
    output = subprocess.check_output([str(tools / "m68k-linux-gnu-nm"), "-n", str(elf)], text=True)
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2] in names:
            symbols[fields[2]] = int(fields[0], 16)
    if set(symbols) != names:
        raise RuntimeError("incomplete V11 symbol table")
    code = binary.read_bytes()
    if musical:
        padding = (-len(code)) & 3
        if padding != 2:
            raise RuntimeError("unexpected Musical Deja Vu payload alignment")
        code += bytes.fromhex("4e71")
    expected_size = MUSICAL_PAYLOAD_BYTES if musical else V11_PAYLOAD_BYTES
    if len(code) != expected_size or len(code) % 4:
        raise RuntimeError("DV/Zoom payload size/alignment gate failed")
    return code, symbols


def extract_musical_v4_payload(candidate: Path, pack) -> tuple[bytes, dict[str, int]]:
    """Load only the hash-gated V4 expansion segment, never its patched MAIN."""
    if not candidate.is_file() or sha(candidate.read_bytes()) != MUSICAL_V4_CANDIDATE_SHA256:
        raise RuntimeError("hardware-proven Musical WAVE V4 candidate hash gate failed")
    _container, _entries, sections = pack.decode_all(candidate)
    main = sections[3]["plain"]
    if len(main) != MUSICAL_V4_MAIN_BYTES or sha(main) != MUSICAL_V4_MAIN_SHA256:
        raise RuntimeError("Musical WAVE V4 decoded MAIN provenance gate failed")
    header = at(main, MUSICAL_V4_PAYLOAD_SOURCE, 12)
    expected_header = struct.pack(">III", MUSICAL_PAYLOAD_BYTES, MUSICAL_V4_ORIGINAL_EXP, 0)
    if header != expected_header:
        raise RuntimeError("Musical WAVE V4 payload segment header gate failed")
    code = at(main, MUSICAL_V4_PAYLOAD_SOURCE + 12, MUSICAL_PAYLOAD_BYTES)
    if sha(code) != MUSICAL_PAYLOAD_SHA256:
        raise RuntimeError("Musical WAVE V4 payload hash gate failed")
    if any(code[offset : offset + 2] == struct.pack(">H", MUSICAL_V4_ORIGINAL_EXP >> 16) for offset in range(len(code) - 1)):
        raise RuntimeError("Musical WAVE V4 payload contains an absolute self-address")
    if at(main, MUSICAL_V4_PAYLOAD_SOURCE + 12 + MUSICAL_PAYLOAD_BYTES, 4) != struct.pack(">I", 0xFFFFFFFF):
        raise RuntimeError("Musical WAVE V4 payload terminator gate failed")
    relocation = V11_EXP - MUSICAL_V4_ORIGINAL_EXP
    return code, {name: address + relocation for name, address in MUSICAL_V4_SYMBOLS.items()}


def apply_v51(main: bytes, pack, manifest: dict, pk_loader: bytes) -> tuple[bytearray, list[dict[str, object]]]:
    """Apply V5.1 to a Plaits MAIN, with current-image guards instead of stock hash."""
    cave = manifest["cave"]
    cave_start = int(cave["start"], 16)
    payload = pack.OVERLAY.read_bytes()
    if sha(payload) != pack.OVERLAY_SHA256 or len(payload) != cave["payload_bytes"]:
        raise RuntimeError("V5.1 overlay payload gate failed")
    mutable = bytearray(main)
    cave_before = at(mutable, cave_start, len(payload))
    expected_cave = bytearray(len(payload))
    loader_offset = PK_LOADER_SOURCE - cave_start
    expected_cave[loader_offset : loader_offset + PK_LOADER_BYTES] = pk_loader
    if cave_before != bytes(expected_cave):
        raise RuntimeError("V5.1 cave contains bytes other than the verified Plaits loader")
    pointer = int(cave["pointer_word"], 16)
    if at(mutable, pointer, 4) != bytes(4):
        raise RuntimeError("V5.1 runtime pointer word is not pristine in the Plaits MAIN")
    for value in manifest["diagnostic"]["latches"].values():
        address = int(value, 16)
        if at(mutable, address, 4) != bytes(4):
            raise RuntimeError(f"V5.1 latch is not pristine at {address:#x}")

    writes: list[dict[str, object]] = [{"name": "dv_v51_cave", "address": f"{cave_start:#010x}", "bytes": len(payload)}]
    put(mutable, cave_start, payload)
    for item in manifest["patches"]:
        address = int(item["address"], 16)
        old = bytes.fromhex(item["stock"])
        new = bytes.fromhex(item["overlay"])
        if at(mutable, address, len(old)) != old:
            raise RuntimeError(f"Plaits/V5.1 guard mismatch at {item['name']} ({address:#x})")
        put(mutable, address, new)
        writes.append({"name": f"dv_v51_{item['name']}", "address": f"{address:#010x}", "bytes": len(new)})
    return mutable, writes


def relocate_pk_loader(main: bytearray, manifest: dict, loader: bytes) -> dict[str, object]:
    """Move patchkit's loader into V11's deliberately inactive formatter tail.

    V11's own builder already uses 0x40311c92 for its loader.  The frozen
    V5.1 manifest labels that span unreachable, and V11 preserves it only to
    minimize its standalone delta.  Relocating the existing patchkit loader
    there lets one reset loader copy both payload segments.
    """
    if len(loader) != PK_LOADER_BYTES or sha(loader) != PK_LOADER_SHA256:
        raise RuntimeError("unexpected Plaits reset-loader bytes")
    inactive = manifest["parts"]["retained_inactive_v4_formatter_bytes"]
    inactive_start = int(inactive["start"], 16)
    inactive_end = int(inactive["end"], 16)
    if not (inactive_start <= PK_LOADER_DEST and PK_LOADER_DEST + len(loader) <= inactive_end):
        raise RuntimeError("V11 inactive formatter tail cannot contain the patchkit loader")
    observed = at(main, PK_RESET_HOOK, len(PK_RESET_OLD))
    if observed != PK_RESET_OLD:
        raise RuntimeError(
            "Plaits reset-loader hook changed before integration: "
            f"expected {PK_RESET_OLD.hex()}, observed {observed.hex()}"
        )
    put(main, PK_LOADER_DEST, loader)
    put(main, PK_RESET_HOOK, b"\x4e\xb9" + struct.pack(">I", PK_LOADER_DEST) + b"\x4e\x71")
    return {
        "name": "relocate_patchkit_loader",
        "source": f"{PK_LOADER_SOURCE:#010x}",
        "destination": f"{PK_LOADER_DEST:#010x}",
        "bytes": len(loader),
        "reset_hook": f"{PK_RESET_HOOK:#010x}",
    }


def apply_musical_v4(main: bytearray, symbols: dict[str, int]) -> list[dict[str, object]]:
    """Apply the hardware-proven Musical WAVE V4 delta after V5.1.

    These are the exact guards and hooks used by the V4 builder. They retain
    WAVE 7/8 and extend the three LFO WAVE descriptors through WAVE 15.
    """
    hooks = (
        ("musical_dispatch", 0x40311BB0, "0c8300000007", b"\x4e\xf9" + struct.pack(">I", symbols["musical_dv_dispatch"])),
        ("musical_gate", 0x40311C00, "7e07be836700", b"\x4e\xf9" + struct.pack(">I", symbols["musical_dv_gate"])),
        ("musical_clamp", 0x40311C30, "7e07be836714", b"\x4e\xf9" + struct.pack(">I", symbols["musical_dv_clamp"])),
        ("musical_post_apply", 0x401398F8, "4a12670642aafffc", b"\x4e\xb9" + struct.pack(">I", symbols["musical_dv_post_apply"]) + b"\x4e\x71"),
    )
    writes: list[dict[str, object]] = []
    for name, address, old_hex, new in hooks:
        old = bytes.fromhex(old_hex)
        if at(main, address, len(old)) != old:
            raise RuntimeError(f"Plaits/DV-Q hook guard mismatch at {name} ({address:#x})")
        put(main, address, new)
        writes.append({"name": name, "address": f"{address:#010x}", "bytes": len(new), "target": f"{struct.unpack_from('>I', new, 2)[0]:#010x}"})
    for index, descriptor in enumerate((0x40210820, 0x40210A78, 0x40210CD0), 1):
        max_address = descriptor + 0x0C
        formatter_address = descriptor + 0x34
        if at(main, max_address, 4) != bytes.fromhex("00000800"):
            raise RuntimeError(f"Plaits/DV-Q wave maximum guard mismatch at LFO {index}")
        if at(main, formatter_address, 4) != struct.pack(">I", 0x40311C50):
            raise RuntimeError(f"Plaits/DV-Q formatter guard mismatch at LFO {index}")
        put(main, max_address, bytes.fromhex("00000f00"))
        put(main, formatter_address, struct.pack(">I", symbols["musical_wave_formatter"]))
        writes.extend((
            {"name": f"lfo{index}_wave_max_musical", "address": f"{max_address:#010x}", "bytes": 4},
            {"name": f"lfo{index}_wave_formatter_musical", "address": f"{formatter_address:#010x}", "bytes": 4, "target": f"{symbols['musical_wave_formatter']:#010x}"},
        ))
    return writes


def parsed_segments(main: bytes) -> tuple[list[tuple[int, int, int, int]], int]:
    offset = STOCK_MAIN_END - MAIN_BASE
    if offset < 0 or offset + 4 > len(main):
        raise RuntimeError("Plaits MAIN lacks the reset-loader segment chain")
    segments: list[tuple[int, int, int, int]] = []
    while True:
        if offset + 4 > len(main):
            raise RuntimeError("unterminated reset-loader segment chain")
        length = struct.unpack_from(">I", main, offset)[0]
        if length == 0xFFFFFFFF:
            return segments, offset
        if length == 0 or length % 4 or offset + 12 + length > len(main):
            raise RuntimeError("invalid existing reset-loader segment")
        dest, bss = struct.unpack_from(">II", main, offset + 4)
        segments.append((offset + MAIN_BASE, length, dest, bss))
        offset += 12 + length


def source_for_loaded_address(main: bytes, address: int) -> int:
    """Return the flash source of a byte owned by a reset-loader segment."""
    segments, _terminator = parsed_segments(main)
    for header, length, destination, _bss in segments:
        if destination <= address < destination + length:
            return header + 12 + address - destination
    raise RuntimeError(f"no reset-loader payload owns active address {address:#x}")


def apply_active_musical_wave_descriptors(main: bytearray, symbols: dict[str, int]) -> list[dict[str, object]]:
    """Patch the core/params relocated rows actually read by the LFO UI.

    core/params redirects every stock descriptor reference to pf_table.  The
    three original MAIN rows patched by V5.1 are therefore inactive in a
    Plaits image; these matching rows are the bytes copied into expansion at
    reset and used by the real selector/formatter path.
    """
    writes: list[dict[str, object]] = []
    for index, row in enumerate(LFO_WAVE_ROWS, 1):
        active = PF_TABLE + row * 60
        source = source_for_loaded_address(main, active)
        maximum = source + 0x0C
        formatter = source + 0x34
        if at(main, maximum, 4) != bytes.fromhex("00000600"):
            raise RuntimeError(f"active LFO {index} WAVE maximum is not the expected stock value")
        if at(main, formatter, 4) != struct.pack(">I", 0x400E1B68):
            raise RuntimeError(f"active LFO {index} WAVE formatter is not the expected stock value")
        put(main, maximum, bytes.fromhex("00000f00"))
        put(main, formatter, struct.pack(">I", symbols["musical_wave_formatter"]))
        if at(main, maximum, 4) != bytes.fromhex("00000f00") or at(main, formatter, 4) != struct.pack(">I", symbols["musical_wave_formatter"]):
            raise RuntimeError(f"active LFO {index} WAVE descriptor write did not persist")
        writes.extend((
            {"name": f"active_lfo{index}_wave_max_musical", "active": f"{active + 0x0C:#010x}", "source": f"{maximum:#010x}", "bytes": 4},
            {"name": f"active_lfo{index}_wave_formatter_musical", "active": f"{active + 0x34:#010x}", "source": f"{formatter:#010x}", "bytes": 4, "target": f"{symbols['musical_wave_formatter']:#010x}"},
        ))
    return writes


def restore_stock_intro(main: bytearray) -> dict[str, object]:
    """Remove the Plaits base image's custom splash without touching its code."""
    if at(main, INTRO_SCENES, len(PLAIT_SPLASH_SCENES)) != PLAIT_SPLASH_SCENES:
        raise RuntimeError("Plaits base intro scene table is not the expected custom splash")
    put(main, INTRO_SCENES, STOCK_INTRO_SCENES)
    if at(main, INTRO_SCENES, len(STOCK_INTRO_SCENES)) != STOCK_INTRO_SCENES:
        raise RuntimeError("stock intro scene table restore did not persist")
    return {
        "name": "restore_stock_elektron_intro",
        "address": f"{INTRO_SCENES:#010x}",
        "bytes": len(STOCK_INTRO_SCENES),
    }


def append_v11_segment(main: bytearray, code: bytes) -> tuple[bytearray, dict[str, object]]:
    segments, terminator = parsed_segments(main)
    v11_end = V11_EXP + len(code)
    if v11_end > V11_EXP_LIMIT:
        raise RuntimeError("V11 expansion payload exceeds the permitted expansion region")
    for _source, length, dest, bss in segments:
        occupied_end = dest + max(length, bss)
        if not (v11_end <= dest or occupied_end <= V11_EXP):
            raise RuntimeError(f"V11 expansion collision with {dest:#x}..{occupied_end:#x}")
    segment = struct.pack(">III", len(code), V11_EXP, 0) + code + struct.pack(">I", 0xFFFFFFFF)
    combined = bytearray(main[:terminator])
    combined.extend(segment)
    return combined, {
        "name": "v11_zoom_segment",
        "source": f"{terminator + MAIN_BASE:#010x}",
        "bytes": len(segment),
        "payload_bytes": len(code),
        "destination": f"{V11_EXP:#010x}",
        "end": f"{v11_end:#010x}",
        "existing_segments": len(segments),
    }


def apply_v11_hooks(main: bytearray, symbols: dict[str, int]) -> list[dict[str, object]]:
    hooks = (
        ("zoom_input", 0x400181FE, "4e56ffcc48d71cfc", b"\x4e\xf9" + struct.pack(">I", symbols["source_zoom_hook"]) + b"\x4e\x71"),
        ("zoom_rotation", 0x400184EA, "4e56ffcc48d73cfc", b"\x4e\xf9" + struct.pack(">I", symbols["source_zoom_rotation_hook"]) + b"\x4e\x71"),
        ("zoom_draw", 0x4001706A, "4eb94010ee64", b"\x4e\xb9" + struct.pack(">I", symbols["oneshot_zoom_wrapper"])),
        ("zoom_factory", 0x4010EE84, "4eb9401a04e4", b"\x4e\xb9" + struct.pack(">I", symbols["zoom_factory_hook"])),
        ("zoom_view_init", 0x4010ECD0, "42002f0a246f0008", b"\x4e\xf9" + struct.pack(">I", symbols["waveform_view_init_hook"]) + b"\x4e\x71"),
    )
    writes: list[dict[str, object]] = []
    for name, address, old_hex, new in hooks:
        old = bytes.fromhex(old_hex)
        if at(main, address, len(old)) != old:
            raise RuntimeError(f"Plaits/V11 hook guard mismatch at {name} ({address:#x})")
        put(main, address, new)
        writes.append({"name": name, "address": f"{address:#010x}", "bytes": len(new), "target": f"{struct.unpack_from('>I', new, 2)[0]:#010x}"})
    return writes


def package(base: Path, output: Path, combined_main: bytes, pack) -> dict[str, object]:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RuntimeError(f"refusing to overwrite existing integration candidate: {output}")
    _container, _entries, base_sections = pack.decode_all(base)
    with tempfile.TemporaryDirectory(prefix="pk-dv-zoom-") as temp_name:
        temp = Path(temp_name)
        tool = pack.compile_tool(temp)
        roundtrip = temp / "plaits-roundtrip.syx"
        pack.run([str(tool), "-i", str(base), "-r", "-o", str(roundtrip)])
        if roundtrip.read_bytes() != base.read_bytes():
            raise RuntimeError("Plaits six-section roundtrip is not byte-identical")
        main_path = temp / "combined-main.bin"
        main_path.write_bytes(combined_main)
        candidate = temp / output.name
        pack.run([str(tool), "-i", str(base), "-c", "3", str(main_path), "-l", "0", "-o", str(candidate)])
        _candidate_container, _candidate_entries, candidate_sections = pack.decode_all(candidate)
        for sid, base_section in base_sections.items():
            expected = combined_main if sid == 3 else base_section["plain"]
            if candidate_sections[sid]["plain"] != expected:
                raise RuntimeError(f"combined section {sid} decompressed mismatch")
        integrity = pack.verify_integrity(candidate)
        bootstrap = pack.verify_bootstrap(candidate_sections)
        data = candidate.read_bytes()
    output.write_bytes(data)
    return {"sha256": sha(data), "bytes": len(data), "integrity": integrity, "bootstrap": bootstrap}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True, help="validated full-range Plaits SYX")
    parser.add_argument("--dv-root", type=Path, required=True, help="read-only dv-offline-v1 checkout")
    parser.add_argument("--m68k-bin", type=Path, required=True, help="directory containing m68k-elf binutils")
    parser.add_argument("--output", type=Path, required=True, help="new output SYX; must not exist")
    parser.add_argument("--report", type=Path, required=True, help="new JSON integration report; must not exist")
    parser.add_argument("--musical-v4", action="store_true", help="include hardware-proven Musical WAVE V4 and its WAVE 7–15 hooks")
    parser.add_argument("--musical-v4-candidate", type=Path, help="exact hardware-proven Musical WAVE V4 SYX")
    args = parser.parse_args(argv)
    base = args.base.resolve()
    if not base.is_file() or sha(base.read_bytes()) != PLAIT_FULLRANGE_SHA256:
        raise SystemExit("full-range Plaits source SHA-256 gate failed")
    if args.output.exists() or args.report.exists():
        raise SystemExit("integration output/report already exists; refusing overwrite")
    if args.musical_v4 and args.musical_v4_candidate is None:
        raise SystemExit("--musical-v4 requires --musical-v4-candidate")

    pack, manifest, source = load_v11(args.dv_root.resolve(), musical=args.musical_v4)
    _container, _entries, sections = pack.decode_all(base)
    main_before = sections[3]["plain"]
    with tempfile.TemporaryDirectory(prefix="pk-dv-assemble-") as temp_name:
        temp = Path(temp_name)
        expected_payload_sha = MUSICAL_PAYLOAD_SHA256 if args.musical_v4 else V11_ORIGINAL_PAYLOAD_SHA256
        if args.musical_v4:
            code, symbols = extract_musical_v4_payload(args.musical_v4_candidate.resolve(), pack)
        else:
            original, _original_symbols = assemble(source, args.m68k_bin.resolve(), V11_ORIGINAL_EXP, temp, musical=False)
            if sha(original) != expected_payload_sha:
                raise RuntimeError("DV/Zoom original-address payload provenance gate failed")
            code, symbols = assemble(source, args.m68k_bin.resolve(), V11_EXP, temp, musical=False)
        if sha(code) != expected_payload_sha:
            raise RuntimeError("DV/Zoom payload byte-identity gate failed")
        pk_loader = at(main_before, PK_LOADER_SOURCE, PK_LOADER_BYTES)
        main, v51_writes = apply_v51(main_before, pack, manifest, pk_loader)
        intro_write = restore_stock_intro(main)
        musical_writes = apply_musical_v4(main, symbols) if args.musical_v4 else []
        loader_write = relocate_pk_loader(main, manifest, pk_loader)
        main, segment = append_v11_segment(main, code)
        active_wave_writes = apply_active_musical_wave_descriptors(main, symbols) if args.musical_v4 else []
        hook_writes = apply_v11_hooks(main, symbols)
        result = package(base, args.output.resolve(), bytes(main), pack)

    report = {
        "status": "PASS",
        "base": {"path": str(base), "sha256": sha(base.read_bytes())},
        "v11": {
            "source": str(source),
            "source_sha256": MUSICAL_SOURCE_SHA256 if args.musical_v4 else V11_SOURCE_SHA256,
            "original_payload_sha256": expected_payload_sha,
            "relocated_payload_sha256": sha(code),
            "original_expansion": f"{V11_ORIGINAL_EXP:#010x}",
            "relocated_expansion": f"{V11_EXP:#010x}",
            "symbols": {key: f"{value:#010x}" for key, value in sorted(symbols.items())},
        },
        "musical_v4_provenance": (
            {"candidate": str(args.musical_v4_candidate.resolve()), "sha256": MUSICAL_V4_CANDIDATE_SHA256}
            if args.musical_v4 else None
        ),
        "writes": v51_writes + [intro_write] + musical_writes + [loader_write, segment] + active_wave_writes + hook_writes,
        "musical_v4": args.musical_v4,
        "main": {"base_bytes": len(main_before), "combined_bytes": len(main), "sha256": sha(bytes(main))},
        "candidate": {"path": str(args.output.resolve()), **result},
        "evidence": {
            "integration": "SOURCE + HOST packaging + synthetic bootstrap decompression",
            "hardware": "UNTESTED combined behavior",
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "candidate": result["sha256"], "main": report["main"], "writes": len(report["writes"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
