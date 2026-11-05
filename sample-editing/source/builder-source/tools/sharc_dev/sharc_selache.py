"""Optional adapter for the pinned public Selache SHARC+ toolchain.

Selache is an independent GPLv3 project and remains external: this module
invokes its command-line programs, transforms its big-endian VISA parcels to
the boot stream's little-endian parcel representation, and compares the result
with :mod:`sharc_isa`.  Selache output is corroboration, never authority.
"""

from __future__ import annotations

import hashlib
import re
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tools.sharc_dev.sharc_isa import DecodeResult, InstructionSet, load_instruction_set

PINNED_REVISION = "2b26d3b75c53063575bc5c820fa0d38879335187"
DEFAULT_PROCESSOR = "ADSP-21569"
DEFAULT_SECTION = "seg_pmco"
_LISTING_ROW = re.compile(
    r"^\s*([0-9a-fA-F]{8})\s+([0-9a-fA-F]{4}(?:[0-9a-fA-F]{4}){0,2})\s+(.+?)\s*$"
)


class SelacheError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExternalInstruction:
    parcel_address: int
    raw: bytes
    text: str

    @property
    def extent_bytes(self) -> int:
        return len(self.raw)


@dataclass(frozen=True)
class ComparedInstruction:
    external: ExternalInstruction
    boot_bytes: bytes
    native_form_id: str | None
    native_candidates: tuple[str, ...]
    native_fields: tuple[tuple[str, int], ...]
    native_extent_bytes: int | None
    extent_agrees: bool


@dataclass(frozen=True)
class Comparison:
    instructions: tuple[ComparedInstruction, ...]

    @property
    def all_extents_agree(self) -> bool:
        return all(instruction.extent_agrees for instruction in self.instructions)


@dataclass(frozen=True)
class OracleRun:
    revision: str
    tool_fingerprints: tuple[ToolFingerprint, ...]
    section: str
    external_bytes: bytes
    boot_bytes: bytes
    listing: str
    comparison: Comparison


@dataclass(frozen=True)
class KnownDisagreement:
    id: str
    external_bytes: bytes
    expected_native_forms: tuple[str, ...]
    kind: str


@dataclass(frozen=True)
class ToolFingerprint:
    name: str
    sha256: str
    size: int


# These are regression inputs already documented in
# docs/findings/05-sharc-isa-and-decoding.md.  They assert only our byte-backed
# decode; they do not import Selache's interpretation as an ISA fact.
KNOWN_DISAGREEMENTS = (
    KnownDisagreement(
        "adjacent-type3c-width",
        bytes.fromhex("90349015"),
        ("3c", "3c"),
        "external-width",
    ),
    KnownDisagreement(
        "type4a-width",
        bytes.fromhex("6abe340298b0"),
        ("4a",),
        "external-width",
    ),
)


def swap_parcels(data: bytes) -> bytes:
    """Swap bytes within every complete 16-bit parcel; preserve a trailing byte."""
    result = bytearray(data)
    for offset in range(0, len(result) - 1, 2):
        result[offset], result[offset + 1] = result[offset + 1], result[offset]
    return bytes(result)


def parse_listing(text: str) -> tuple[ExternalInstruction, ...]:
    """Parse only instruction rows from ``seldump -ns`` output."""
    rows = []
    for line in text.splitlines():
        match = _LISTING_ROW.match(line)
        if match:
            rows.append(
                ExternalInstruction(
                    parcel_address=int(match.group(1), 16),
                    raw=bytes.fromhex(match.group(2)),
                    text=match.group(3),
                )
            )
    return tuple(rows)


def _compared(
    external: ExternalInstruction, isa: InstructionSet
) -> ComparedInstruction:
    boot = swap_parcels(external.raw)
    result: DecodeResult = isa.decode_bytes(boot)
    instruction = result.instruction
    return ComparedInstruction(
        external=external,
        boot_bytes=boot,
        native_form_id=None if instruction is None else instruction.form.id,
        native_candidates=tuple(form.id for form in result.candidates),
        native_fields=()
        if instruction is None
        else tuple(instruction.field_dict().items()),
        native_extent_bytes=None if instruction is None else instruction.extent_bytes,
        extent_agrees=(
            instruction is not None
            and instruction.extent_bytes == external.extent_bytes
        ),
    )


def compare_listing(text: str, isa: InstructionSet | None = None) -> Comparison:
    """Compare each externally selected instruction extent with native decode."""
    instruction_set = isa or load_instruction_set()
    return Comparison(
        tuple(_compared(row, instruction_set) for row in parse_listing(text))
    )


def compare_rows(
    rows: tuple[ExternalInstruction, ...] | list[ExternalInstruction],
    isa: InstructionSet | None = None,
) -> Comparison:
    """Compare externally selected instruction extents (raw bytes in Selache's parcel order) with native decode."""
    instruction_set = isa or load_instruction_set()
    return Comparison(tuple(_compared(row, instruction_set) for row in rows))


def compare_external_bytes(
    data: bytes, isa: InstructionSet | None = None
) -> Comparison:
    """Decode an external byte stream using native extents after parcel swapping.

    This is useful for regression fixtures without invoking the external tool.
    It does not claim what extent Selache itself would select.
    """
    instruction_set = isa or load_instruction_set()
    boot = swap_parcels(data)
    rows = []
    offset = 0
    parcel_address = 0
    while offset < len(boot):
        result = instruction_set.decode_bytes(boot[offset : offset + 6])
        if result.instruction is None:
            extent = min(2, len(boot) - offset)
        else:
            extent = result.instruction.extent_bytes
        external_raw = swap_parcels(boot[offset : offset + extent])
        rows.append(ExternalInstruction(parcel_address, external_raw, ""))
        offset += extent
        parcel_address += extent // 2
    return Comparison(tuple(_compared(row, instruction_set) for row in rows))


def find_elf_section(path: str | Path, name: str) -> bytes:
    """Read one ELF32 section by name without depending on dump formatting."""
    data = Path(path).read_bytes()
    if len(data) < 0x34 or data[:4] != b"\x7fELF" or data[4] != 1:
        raise SelacheError(f"not an ELF32 object: {path}")
    endian = "<" if data[5] == 1 else ">"
    section_offset = struct.unpack_from(endian + "I", data, 0x20)[0]
    entry_size, count, names_index = struct.unpack_from(endian + "HHH", data, 0x2E)
    names_header = section_offset + names_index * entry_size
    names_offset, names_size = struct.unpack_from(
        endian + "II", data, names_header + 0x10
    )
    names = data[names_offset : names_offset + names_size]

    def section_name(index: int) -> str:
        try:
            end = names.index(b"\0", index)
        except ValueError as exc:
            raise SelacheError("unterminated ELF section name") from exc
        return names[index:end].decode()

    for index in range(count):
        header = section_offset + index * entry_size
        name_offset = struct.unpack_from(endian + "I", data, header)[0]
        payload_offset, payload_size = struct.unpack_from(
            endian + "II", data, header + 0x10
        )
        if section_name(name_offset) == name:
            return data[payload_offset : payload_offset + payload_size]
    raise SelacheError(f"section {name!r} not found in {path}")


def _run(command: list[str], *, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SelacheError(f"cannot run {command[0]}: {exc}") from exc
    if result.returncode:
        rendered = " ".join(command[:1])
        raise SelacheError(f"{rendered} failed:\n{result.stdout}{result.stderr}")
    return result.stdout


@dataclass(frozen=True)
class SelacheOracle:
    """Pinned external assembler/disassembler adapter."""

    checkout: Path
    revision: str
    selas: Path
    seldump: Path
    tool_fingerprints: tuple[ToolFingerprint, ...]

    @classmethod
    def open(cls, checkout: str | Path) -> SelacheOracle:
        root = Path(checkout).resolve()
        revision = _run(["git", "rev-parse", "HEAD"], cwd=root).strip()
        if revision != PINNED_REVISION:
            raise SelacheError(
                f"Selache revision {revision} is not pinned revision {PINNED_REVISION}"
            )
        if _run(["git", "status", "--porcelain"], cwd=root).strip():
            raise SelacheError(
                "Selache checkout has local changes; oracle must be clean"
            )
        selas = root / "target" / "release" / "selas"
        seldump = root / "target" / "release" / "seldump"
        missing = [str(path) for path in (selas, seldump) if not path.is_file()]
        if missing:
            raise SelacheError(
                "missing built Selache executable(s): " + ", ".join(missing)
            )
        try:
            fingerprints = tuple(
                ToolFingerprint(
                    path.name,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    path.stat().st_size,
                )
                for path in (selas, seldump)
            )
        except OSError as exc:
            raise SelacheError(
                f"cannot fingerprint Selache executables: {exc}"
            ) from exc
        return cls(root, revision, selas, seldump, fingerprints)

    def assemble_text(
        self,
        source: str,
        *,
        processor: str = DEFAULT_PROCESSOR,
        section: str = DEFAULT_SECTION,
    ) -> OracleRun:
        with tempfile.TemporaryDirectory(prefix="sharc-selache-") as directory:
            work = Path(directory)
            source_path = work / "snippet.s"
            object_path = work / "snippet.doj"
            source_path.write_text(source)
            _run(
                [
                    str(self.selas),
                    "-proc",
                    processor,
                    "-o",
                    str(object_path),
                    str(source_path),
                ]
            )
            listing = _run([str(self.seldump), "-ns", section, str(object_path)])
            external = find_elf_section(object_path, section)
            rows = parse_listing(listing)
            listed = b"".join(row.raw for row in rows)
            if listed != external:
                raise SelacheError(
                    "Selache listing bytes do not equal the assembled section bytes"
                )
            return OracleRun(
                revision=self.revision,
                tool_fingerprints=self.tool_fingerprints,
                section=section,
                external_bytes=external,
                boot_bytes=swap_parcels(external),
                listing=listing,
                comparison=compare_listing(listing),
            )
