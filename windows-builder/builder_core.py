"""Fail-closed dual-version DT2 Deja Vu V5.1 builder core."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

MAIN_BASE = 0x40000400
EXPECTED_SECTION_FILES = {
    "section_2_bootstrap.bin",
    "section_3_MAIN_OS.bin",
    "section_4_updater.raw",
    "section_5_meta.raw",
    "section_7_blob.bin",
    "section_8__.bin",
}


@dataclass(frozen=True)
class BuildProfile:
    os_version: str
    stock_sha256: str
    stock_main_sha256: str
    overlay_sha256: str
    manifest_sha256: str
    patched_main_sha256: str
    output_sha256: str
    output_size: int
    overlay_resource: str
    manifest_resource: str

    @property
    def output_name(self) -> str:
        return f"DT2_Deja_Vu_v0.1.1-beta_OS{self.os_version}.syx"


PROFILES = {
    "1.16": BuildProfile(
        os_version="1.16",
        stock_sha256="278541e466edcd77d6b3e018a91fb90185932d3c7de224dd3e68294dddf3a9ec",
        stock_main_sha256="57bb4dfa8df07d846adc72fdb4fb0d3cd3c5680c524bf498338460207e008e7d",
        overlay_sha256="b600e5ae92cced79c14311a5fa4f74e301f68b6d5454f837936fdb7a7ae46d4e",
        manifest_sha256="d9084149c424485f8d8017ee6df986a1ba12791ed919f1eacf171a1c80711a14",
        patched_main_sha256="c3c2e54996d4b9f643cb621e7841e1e4ebe805dfacdfea7aae167ebb8581aecd",
        output_sha256="d9fe67b3778ce5d31aaaa807e6e43ac1929d0f36a10c393adc0a8c590c547835",
        output_size=1_848_352,
        overlay_resource="resources/dv-v51-overlay.bin",
        manifest_resource="resources/dv-v51-overlay-manifest.json",
    ),
    "1.17": BuildProfile(
        os_version="1.17",
        stock_sha256="26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5",
        stock_main_sha256="a1e7b657b705eba1a19d81c33c1e11ba9c409816447ad74005d7bbf36da6d964",
        overlay_sha256="2c29de1de21907148d3148ec15829aa9105240b4632675ef4620d3807fd78b4f",
        manifest_sha256="5ecfe2a06d1321ee4b197cb465b329f69bf66fe14ad3e0b9bd0b74ac4989da2c",
        patched_main_sha256="c41dacf2f0a9c2eec88f8b857d72f69660d2ecac0d2f2ff6c92db2028defd4c8",
        output_sha256="cd553ef6e84d60401b8ac3abc3492b61343ba8a6ac720cf019e15b613cd67b2e",
        output_size=1_849_632,
        overlay_resource="resources/dv-v51-os117-overlay.bin",
        manifest_resource="resources/dv-v51-os117-overlay-manifest.json",
    ),
}
PROFILES_BY_STOCK_HASH = {profile.stock_sha256: profile for profile in PROFILES.values()}


class BuildError(Exception):
    """An expected, user-facing build failure."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_profile(stock_path: str | Path) -> BuildProfile | None:
    stock = Path(stock_path)
    if not stock.is_file() or stock.suffix.lower() != ".syx":
        return None
    try:
        return PROFILES_BY_STOCK_HASH.get(sha256_file(stock))
    except OSError:
        return None


def _resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def resource_path(relative: str) -> Path:
    return _resource_root() / relative


def default_output_path(stock_path: Path, profile: BuildProfile | None = None) -> Path:
    selected = profile or detect_profile(stock_path)
    name = selected.output_name if selected else "DT2_Deja_Vu_v0.1.1-beta.syx"
    return stock_path.with_name(name)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _run_tool(tool: Path, *args: str) -> str:
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [str(tool), *map(str, args)], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", creationflags=flags, check=False,
        )
    except OSError as exc:
        raise BuildError("The bundled firmware tool could not be started.") from exc
    if result.returncode != 0:
        raise BuildError("The firmware package could not be processed. No output was written.")
    return result.stdout


def _load_resources(profile: BuildProfile) -> tuple[Path, bytes, dict]:
    tool = resource_path("native/elektron-firmware-tool.exe")
    overlay_path = resource_path(profile.overlay_resource)
    manifest_path = resource_path(profile.manifest_resource)
    if not tool.is_file() or not overlay_path.is_file() or not manifest_path.is_file():
        raise BuildError("The builder installation is incomplete.")
    overlay = overlay_path.read_bytes()
    manifest_raw = manifest_path.read_bytes()
    if hashlib.sha256(overlay).hexdigest() != profile.overlay_sha256:
        raise BuildError("The bundled patch failed its integrity check.")
    if hashlib.sha256(manifest_raw).hexdigest() != profile.manifest_sha256:
        raise BuildError("The bundled patch manifest failed its integrity check.")
    try:
        manifest = json.loads(manifest_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError("The bundled patch manifest is invalid.") from exc
    return tool, overlay, manifest


def _apply_patch(stock_main: bytes, overlay: bytes, manifest: dict, profile: BuildProfile) -> bytes:
    if hashlib.sha256(stock_main).hexdigest() != profile.stock_main_sha256:
        raise BuildError(f"The selected firmware does not contain the expected OS {profile.os_version} image.")
    if manifest.get("device") != "Elektron Digitakt II" or manifest.get("os") != profile.os_version:
        raise BuildError("The bundled patch is for a different device or OS version.")
    if manifest.get("stock_syx_sha256") != profile.stock_sha256:
        raise BuildError("The bundled patch prerequisites are invalid.")
    if manifest.get("stock_main_sha256") != profile.stock_main_sha256:
        raise BuildError("The bundled MAIN prerequisite is invalid.")
    if manifest.get("total_static_patch_bytes") != 2082:
        raise BuildError("The bundled patch coverage is invalid.")

    cave = manifest.get("cave", {})
    if cave.get("bundle_sha256") != profile.overlay_sha256:
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
            raise BuildError(f"The selected firmware does not match a required OS {profile.os_version} patch site.")
        ranges.append((offset, offset + len(new), item["name"]))
        patched[offset:offset + len(new)] = new

    for index, left in enumerate(ranges):
        for right in ranges[index + 1:]:
            if not (left[1] <= right[0] or right[1] <= left[0]):
                raise BuildError("The bundled patch contains overlapping regions.")
    if sum(end - start for start, end, _name in ranges) != 2082:
        raise BuildError("The bundled patch coverage check failed.")

    result = bytes(patched)
    if len(result) != len(stock_main) or hashlib.sha256(result).hexdigest() != profile.patched_main_sha256:
        raise BuildError("The patched firmware image did not match the verified V5.1 image.")
    return result


def _extract_all(tool: Path, syx: Path, out_dir: Path) -> dict[str, Path]:
    _run_tool(tool, "-i", syx, "-o", out_dir)
    files = {path.name: path for path in out_dir.iterdir() if path.is_file()}
    if set(files) != EXPECTED_SECTION_FILES:
        raise BuildError("The firmware did not contain the expected six Digitakt II sections.")
    return files


def _verify_sections(stock: dict[str, Path], candidate: dict[str, Path], profile: BuildProfile) -> None:
    for name in EXPECTED_SECTION_FILES:
        expected = profile.patched_main_sha256 if name == "section_3_MAIN_OS.bin" else sha256_file(stock[name])
        if sha256_file(candidate[name]) != expected:
            raise BuildError("A firmware section changed unexpectedly.")


def _atomic_copy(source: Path, destination: Path, profile: BuildProfile) -> None:
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
        if sha256_file(temporary) != profile.output_sha256:
            raise BuildError("The final output failed its integrity check.")
        os.rename(temporary, destination)
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
        raise BuildError("Select an official Digitakt II OS 1.16 or OS 1.17 .syx file.")
    if stock.suffix.lower() != ".syx":
        raise BuildError("The selected input must be an Elektron .syx firmware file.")
    if _same_path(stock, output):
        raise BuildError("The output must use a different filename from the stock firmware.")
    if not output.parent.is_dir():
        raise BuildError("The selected output folder does not exist.")
    if output.exists():
        raise BuildError("The output file already exists. Choose a new filename.")

    notify("Checking official firmware…")
    try:
        stock_hash = sha256_file(stock)
    except OSError as exc:
        raise BuildError("The selected firmware file could not be read.") from exc
    profile = PROFILES_BY_STOCK_HASH.get(stock_hash)
    if profile is None:
        raise BuildError("This is not a verified official Digitakt II OS 1.16 or OS 1.17 firmware file.")

    notify(f"Detected Digitakt II OS {profile.os_version}.")
    tool, overlay, manifest = _load_resources(profile)
    try:
        temporary_work = tempfile.TemporaryDirectory(prefix=".dt2-dejavu-builder-", dir=output.parent)
    except OSError as exc:
        raise BuildError("The output folder is not writable. Choose another location.") from exc
    with temporary_work as temp_name:
        temp = Path(temp_name)
        stock_sections_dir = temp / "stock-sections"
        candidate_sections_dir = temp / "candidate-sections"

        notify("Reading firmware sections…")
        stock_sections = _extract_all(tool, stock, stock_sections_dir)
        patched_main = _apply_patch(
            stock_sections["section_3_MAIN_OS.bin"].read_bytes(), overlay, manifest, profile
        )
        patched_main_path = temp / "patched-main.bin"
        patched_main_path.write_bytes(patched_main)

        notify("Building Déjà Vu firmware…")
        candidate = temp / profile.output_name
        _run_tool(tool, "-i", stock, "-c", "3", patched_main_path, "-l", "0", "-o", candidate)
        if not candidate.is_file() or candidate.stat().st_size != profile.output_size:
            raise BuildError("The generated firmware size did not match the verified V5.1 image.")
        if sha256_file(candidate) != profile.output_sha256:
            raise BuildError("The generated firmware did not match the hardware-tested V5.1 image.")

        notify("Verifying all six sections and checksums…")
        summary = _run_tool(tool, "-i", candidate)
        required_summary = (
            "device    : Digitakt II (0x14)",
            f"version   : {profile.os_version}",
            "container : ELE3",
            "checksums : ok",
        )
        if not all(text in summary for text in required_summary):
            raise BuildError("The generated firmware failed its container integrity check.")
        candidate_sections = _extract_all(tool, candidate, candidate_sections_dir)
        _verify_sections(stock_sections, candidate_sections, profile)

        notify("Writing verified output…")
        _atomic_copy(candidate, output, profile)

    return {
        "path": str(output.resolve()),
        "os_version": profile.os_version,
        "sha256": profile.output_sha256,
        "size": profile.output_size,
        "sections": 6,
    }
