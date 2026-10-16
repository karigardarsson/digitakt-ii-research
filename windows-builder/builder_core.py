"""Fail-closed DT2 OS 1.16 Déjà Vu V5.1 builder core."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

STOCK_SHA256 = "278541e466edcd77d6b3e018a91fb90185932d3c7de224dd3e68294dddf3a9ec"
STOCK_MAIN_SHA256 = "57bb4dfa8df07d846adc72fdb4fb0d3cd3c5680c524bf498338460207e008e7d"
OVERLAY_SHA256 = "b600e5ae92cced79c14311a5fa4f74e301f68b6d5454f837936fdb7a7ae46d4e"
MANIFEST_SHA256 = "d9084149c424485f8d8017ee6df986a1ba12791ed919f1eacf171a1c80711a14"
PATCHED_MAIN_SHA256 = "c3c2e54996d4b9f643cb621e7841e1e4ebe805dfacdfea7aae167ebb8581aecd"
OUTPUT_SHA256 = "d9fe67b3778ce5d31aaaa807e6e43ac1929d0f36a10c393adc0a8c590c547835"
OUTPUT_SIZE = 1_848_352
MAIN_BASE = 0x40000400
CAVE_START = 0x403117C4
EXPECTED_SECTION_FILES = {
    "section_2_bootstrap.bin",
    "section_3_MAIN_OS.bin",
    "section_4_updater.raw",
    "section_5_meta.raw",
    "section_7_blob.bin",
    "section_8__.bin",
}


class BuildError(Exception):
    """An expected, user-facing build failure."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def resource_path(relative: str) -> Path:
    return _resource_root() / relative


def default_output_path(stock_path: Path) -> Path:
    return stock_path.with_name("DT2_Deja_Vu_v0.1.0-beta_OS1.16.syx")


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _run_tool(tool: Path, *args: str) -> str:
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            [str(tool), *map(str, args)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
            check=False,
        )
    except OSError as exc:
        raise BuildError("The bundled firmware tool could not be started.") from exc
    if result.returncode != 0:
        raise BuildError("The firmware package could not be processed. No output was written.")
    return result.stdout


def _load_resources() -> tuple[Path, bytes, dict]:
    tool = resource_path("native/elektron-firmware-tool.exe")
    overlay_path = resource_path("resources/dv-v51-overlay.bin")
    manifest_path = resource_path("resources/dv-v51-overlay-manifest.json")
    if not tool.is_file() or not overlay_path.is_file() or not manifest_path.is_file():
        raise BuildError("The builder installation is incomplete.")
    overlay = overlay_path.read_bytes()
    manifest_raw = manifest_path.read_bytes()
    if hashlib.sha256(overlay).hexdigest() != OVERLAY_SHA256:
        raise BuildError("The bundled patch failed its integrity check.")
    if hashlib.sha256(manifest_raw).hexdigest() != MANIFEST_SHA256:
        raise BuildError("The bundled patch manifest failed its integrity check.")
    try:
        manifest = json.loads(manifest_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError("The bundled patch manifest is invalid.") from exc
    return tool, overlay, manifest


def _apply_patch(stock_main: bytes, overlay: bytes, manifest: dict) -> bytes:
    if hashlib.sha256(stock_main).hexdigest() != STOCK_MAIN_SHA256:
        raise BuildError("The selected firmware does not contain the expected OS 1.16 image.")
    if manifest.get("device") != "Elektron Digitakt II" or manifest.get("os") != "1.16":
        raise BuildError("The bundled patch is for a different device or OS version.")
    if manifest.get("stock_syx_sha256") != STOCK_SHA256:
        raise BuildError("The bundled patch prerequisites are invalid.")
    if manifest.get("stock_main_sha256") != STOCK_MAIN_SHA256:
        raise BuildError("The bundled MAIN prerequisite is invalid.")
    if manifest.get("total_static_patch_bytes") != 2082:
        raise BuildError("The bundled patch coverage is invalid.")

    cave = manifest.get("cave", {})
    if cave.get("bundle_sha256") != OVERLAY_SHA256:
        raise BuildError("The bundled patch identity does not match its manifest.")
    cave_start = int(cave.get("start", "0"), 16)
    cave_end = int(cave.get("end", "0"), 16)
    payload_end = int(cave.get("payload_end", "0"), 16)
    pointer = int(cave.get("pointer_word", "0"), 16)
    if (cave_start, cave_end, payload_end, pointer) != (
        0x403117C4, 0x40312000, 0x40311FB2, 0x40311FF0
    ):
        raise BuildError("The bundled patch layout is invalid.")
    if len(overlay) != cave.get("payload_bytes") or cave_start + len(overlay) != payload_end:
        raise BuildError("The bundled patch size is invalid.")

    patched = bytearray(stock_main)
    cave_offset = cave_start - MAIN_BASE
    if patched[cave_offset:cave_offset + len(overlay)] != bytes(len(overlay)):
        raise BuildError("The stock firmware patch area is not pristine.")
    ranges = [(cave_offset, cave_offset + len(overlay), "overlay")]
    patched[cave_offset:cave_offset + len(overlay)] = overlay

    patches = manifest.get("patches", [])
    if len(patches) != 10:
        raise BuildError("The bundled patch-site count is invalid.")
    for item in patches:
        address = int(item["address"], 16)
        offset = address - MAIN_BASE
        old = bytes.fromhex(item["stock"])
        new = bytes.fromhex(item["overlay"])
        if len(old) != item["bytes"] or len(new) != len(old):
            raise BuildError("A bundled patch-site size is invalid.")
        if offset < 0 or offset + len(old) > len(patched):
            raise BuildError("A bundled patch site is outside the firmware image.")
        if bytes(patched[offset:offset + len(old)]) != old:
            raise BuildError("The selected firmware does not match a required OS 1.16 patch site.")
        ranges.append((offset, offset + len(new), item["name"]))
        patched[offset:offset + len(new)] = new

    for index, left in enumerate(ranges):
        for right in ranges[index + 1:]:
            if not (left[1] <= right[0] or right[1] <= left[0]):
                raise BuildError("The bundled patch contains overlapping regions.")
    if sum(end - start for start, end, _name in ranges) != 2082:
        raise BuildError("The bundled patch coverage check failed.")

    result = bytes(patched)
    if len(result) != len(stock_main) or hashlib.sha256(result).hexdigest() != PATCHED_MAIN_SHA256:
        raise BuildError("The patched firmware image did not match the verified V5.1 image.")
    return result


def _extract_all(tool: Path, syx: Path, out_dir: Path) -> dict[str, Path]:
    _run_tool(tool, "-i", syx, "-o", out_dir)
    files = {path.name: path for path in out_dir.iterdir() if path.is_file()}
    if set(files) != EXPECTED_SECTION_FILES:
        raise BuildError("The firmware did not contain the expected six Digitakt II sections.")
    return files


def _verify_sections(stock: dict[str, Path], candidate: dict[str, Path]) -> None:
    for name in EXPECTED_SECTION_FILES:
        if name == "section_3_MAIN_OS.bin":
            expected = PATCHED_MAIN_SHA256
        else:
            expected = sha256_file(stock[name])
        if sha256_file(candidate[name]) != expected:
            raise BuildError("A firmware section changed unexpectedly.")


def _atomic_copy(source: Path, destination: Path) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp"
        ) as handle:
            temporary = Path(handle.name)
            with source.open("rb") as source_handle:
                shutil.copyfileobj(source_handle, handle, 1 << 20)
            handle.flush()
            os.fsync(handle.fileno())
        if sha256_file(temporary) != OUTPUT_SHA256:
            raise BuildError("The final output failed its integrity check.")
        os.rename(temporary, destination)  # Atomic on Windows; refuses an existing destination.
        temporary = None
    except FileExistsError as exc:
        raise BuildError("The output file already exists. Choose a new filename.") from exc
    except OSError as exc:
        raise BuildError("The output file could not be written. Choose another location.") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def build_firmware(stock_path: str | Path, output_path: str | Path, progress=None) -> dict:
    stock = Path(stock_path)
    output = Path(output_path)
    notify = progress or (lambda _message: None)

    if not stock.is_file():
        raise BuildError("Select an official Digitakt II OS 1.16 .syx file.")
    if stock.suffix.lower() != ".syx":
        raise BuildError("The selected input must be an Elektron .syx firmware file.")
    if _same_path(stock, output):
        raise BuildError("The output must use a different filename from the stock firmware.")
    if not output.parent.is_dir():
        raise BuildError("The selected output folder does not exist.")
    if output.exists():
        raise BuildError("The output file already exists. Choose a new filename.")

    notify("Checking official OS 1.16 firmware…")
    try:
        stock_hash = sha256_file(stock)
    except OSError as exc:
        raise BuildError("The selected firmware file could not be read.") from exc
    if stock_hash != STOCK_SHA256:
        raise BuildError("This is not the verified official Digitakt II OS 1.16 firmware.")

    tool, overlay, manifest = _load_resources()
    try:
        temporary_work = tempfile.TemporaryDirectory(
            prefix=".dt2-dejavu-builder-", dir=output.parent
        )
    except OSError as exc:
        raise BuildError("The output folder is not writable. Choose another location.") from exc
    with temporary_work as temp_name:
        temp = Path(temp_name)
        stock_sections_dir = temp / "stock-sections"
        candidate_sections_dir = temp / "candidate-sections"

        notify("Reading firmware sections…")
        stock_sections = _extract_all(tool, stock, stock_sections_dir)
        patched_main = _apply_patch(
            stock_sections["section_3_MAIN_OS.bin"].read_bytes(), overlay, manifest
        )
        patched_main_path = temp / "patched-main.bin"
        patched_main_path.write_bytes(patched_main)

        notify("Building Déjà Vu firmware…")
        candidate = temp / "DT2_Deja_Vu_v0.1.0-beta_OS1.16.syx"
        _run_tool(
            tool, "-i", stock, "-c", "3", patched_main_path,
            "-l", "0", "-o", candidate,
        )
        if not candidate.is_file() or candidate.stat().st_size != OUTPUT_SIZE:
            raise BuildError("The generated firmware size did not match verified V5.1.")
        if sha256_file(candidate) != OUTPUT_SHA256:
            raise BuildError("The generated firmware did not match verified V5.1.")

        notify("Verifying all six sections and checksums…")
        summary = _run_tool(tool, "-i", candidate)
        required_summary = (
            "device    : Digitakt II (0x14)",
            "version   : 1.16",
            "container : ELE3",
            "checksums : ok",
        )
        if not all(text in summary for text in required_summary):
            raise BuildError("The generated firmware failed its container integrity check.")
        candidate_sections = _extract_all(tool, candidate, candidate_sections_dir)
        _verify_sections(stock_sections, candidate_sections)

        notify("Writing verified output…")
        _atomic_copy(candidate, output)

    return {
        "path": str(output.resolve()),
        "sha256": OUTPUT_SHA256,
        "size": OUTPUT_SIZE,
        "sections": 6,
    }
