"""Typed SHARC+ instruction forms and deterministic VISA decode selection.

This module is the project-owned seam over ``sharcspec/decode_table.json``.
The table remains the public-manual-derived encoding source; callers receive
immutable forms, normalized operands, decoded instructions, and claim-level
evidence references instead of depending on the JSON layout.

It deliberately models encoding only.  Architectural effects, rendering,
assembly, and image-specific findings belong behind separate adapters.
"""

from __future__ import annotations

import json
import re
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TypedDict, cast

HERE = Path(__file__).resolve().parent
TABLE_PATH = HERE / "sharcspec" / "decode_table.json"
_RANGED_LABEL = re.compile(r"^(\w+)\[(\d+):(\d+)\]$")


class LegacyForm(TypedDict):
    """Compatibility shape historically exported by ``sharc_visa_tables``."""

    name: str
    bits: int
    opcode_mask: int
    opcode_value: int
    frame_mask: int
    frame_value: int
    fields: dict[str, tuple[int, int]]
    lead: int
    fixed_bits: int
    uncertain: bool
    source: str


class _TableField(TypedDict):
    label: str
    hi: int
    lo: int


class _TableForm(TypedDict):
    name: str
    width: int
    visa: bool
    isa: bool
    mask: str
    value: str
    fixed_bits: int
    fields: list[_TableField]
    source: str
    classic_keys: list[str]
    unconfirmed_bits: int


class OperandKind(str, Enum):  # noqa: UP042
    """Conservative structural classification, not an execution claim."""

    ADDRESS = "address"
    COMPUTE = "compute"
    CONDITION = "condition"
    IMMEDIATE = "immediate"
    REGISTER = "register"
    SELECTOR = "selector"


class EvidenceStatus(str, Enum):  # noqa: UP042
    """Status of one machine-readable encoding claim."""

    DOCUMENTED = "documented"
    UNCONFIRMED = "unconfirmed"
    PROVISIONAL = "provisional"


@dataclass(frozen=True)
class EvidenceRef:
    """Reference and status for one claim represented by the model."""

    claim_id: str
    status: EvidenceStatus
    source: str


@dataclass(frozen=True)
class EncodedField:
    """One physical field fragment in the 48-bit MSB-aligned frame."""

    label: str
    frame_hi: int
    frame_lo: int
    operand_hi: int
    operand_lo: int

    @property
    def width(self) -> int:
        return self.frame_hi - self.frame_lo + 1

    def extract(self, frame: int) -> int:
        return (frame >> self.frame_lo) & ((1 << self.width) - 1)


@dataclass(frozen=True)
class OperandSpec:
    """A logical operand assembled from one or more encoded fragments."""

    name: str
    kind: OperandKind
    fragments: tuple[EncodedField, ...]

    def extract(self, fields: Mapping[str, int]) -> int:
        value = 0
        for fragment in self.fragments:
            value |= fields[fragment.label] << fragment.operand_lo
        return value


@dataclass(frozen=True)
class FieldValue:
    field: EncodedField
    value: int


@dataclass(frozen=True)
class OperandValue:
    operand: OperandSpec
    value: int


@dataclass(frozen=True)
class InstructionForm:
    """One immutable instruction encoding form."""

    id: str
    table_name: str
    extent_bits: int
    visa: bool
    isa: bool
    frame_mask: int
    frame_value: int
    fixed_bits: int
    leading_fixed_bits: int
    fields: tuple[EncodedField, ...]
    operands: tuple[OperandSpec, ...]
    source: str
    classic_keys: tuple[str, ...]
    unconfirmed_bits: int
    evidence: tuple[EvidenceRef, ...]

    @property
    def extent_bytes(self) -> int:
        return self.extent_bits // 8

    @property
    def extent_words(self) -> int:
        return self.extent_bits // 16

    @property
    def uncertain(self) -> bool:
        return bool(self.unconfirmed_bits)

    @property
    def width_shift(self) -> int:
        return 48 - self.extent_bits

    @property
    def opcode_mask(self) -> int:
        return self.frame_mask >> self.width_shift

    @property
    def opcode_value(self) -> int:
        return self.frame_value >> self.width_shift

    def matches(self, frame: int) -> bool:
        return frame & self.frame_mask == self.frame_value

    def operand(self, name: str) -> OperandSpec:
        for operand in self.operands:
            if operand.name == name:
                return operand
        raise KeyError(name)

    def extract_fields(self, frame: int) -> tuple[FieldValue, ...]:
        return tuple(FieldValue(field, field.extract(frame)) for field in self.fields)

    def legacy_dict(self) -> LegacyForm:
        """Compatibility view used by existing tools; callers should not mutate it."""
        shift = self.width_shift
        return {
            "name": self.id,
            "bits": self.extent_bits,
            "opcode_mask": self.opcode_mask,
            "opcode_value": self.opcode_value,
            "frame_mask": self.frame_mask,
            "frame_value": self.frame_value,
            "fields": {
                field.label: (field.frame_hi - shift, field.frame_lo - shift)
                for field in self.fields
            },
            "lead": self.leading_fixed_bits,
            "fixed_bits": self.fixed_bits,
            "uncertain": self.uncertain,
            "source": self.source,
        }

    def table_dict(self) -> dict[str, object]:
        """Compatibility view matching one ``decode_table.json`` form row."""
        return {
            "name": self.table_name,
            "width": self.extent_bits,
            "visa": self.visa,
            "isa": self.isa,
            "mask": f"0x{self.frame_mask:012x}",
            "value": f"0x{self.frame_value:012x}",
            "fixed_bits": self.fixed_bits,
            "fields": [
                {"label": field.label, "hi": field.frame_hi, "lo": field.frame_lo}
                for field in self.fields
            ],
            "source": self.source,
            "classic_keys": list(self.classic_keys),
            "unconfirmed_bits": self.unconfirmed_bits,
        }


@dataclass(frozen=True)
class DecodedInstruction:
    form: InstructionForm
    frame: int
    raw: int
    fields: tuple[FieldValue, ...]
    operands: tuple[OperandValue, ...]

    @property
    def extent_bytes(self) -> int:
        return self.form.extent_bytes

    def field_dict(self) -> dict[str, int]:
        return {item.field.label: item.value for item in self.fields}

    def operand_dict(self) -> dict[str, int]:
        return {item.operand.name: item.value for item in self.operands}


@dataclass(frozen=True)
class Selection:
    form: InstructionForm | None
    candidates: tuple[InstructionForm, ...]


@dataclass(frozen=True)
class DecodeResult:
    instruction: DecodedInstruction | None
    candidates: tuple[InstructionForm, ...]
    truncated: bool = False


def form_id(table_name: str) -> str:
    """Return the stable project id for a table name such as ``Type5b (move)``."""
    name = re.sub(r"^Type", "", table_name)
    return re.sub(r"\s*\((\w+)\)", r"_\1", name)


def frame_of(words: Iterable[int]) -> int:
    """Build the 48-bit MSB-aligned frame from up to three 16-bit parcels."""
    frame = 0
    for index, word in enumerate(tuple(words)[:3]):
        frame |= (word & 0xFFFF) << (32 - 16 * index)
    return frame


def _leading_fixed_bits(mask: int) -> int:
    count = 0
    for bit in range(47, -1, -1):
        if not (mask >> bit) & 1:
            break
        count += 1
    return count


def _operand_kind(name: str) -> OperandKind:
    if name in {"addr", "reladdr"}:
        return OperandKind.ADDRESS
    if name in {"compute", "shiftimm"}:
        return OperandKind.COMPUTE
    if name in {"cond", "term"}:
        return OperandKind.CONDITION
    if name == "data" or name.startswith("data"):
        return OperandKind.IMMEDIATE
    if name in {
        "ureg",
        "srcureg",
        "dstureg",
        "cureg",
        "dreg",
        "cdreg",
        "sreg",
        "i",
        "m",
        "is",
        "idis",
        "dmi",
        "dmm",
        "pmi",
        "pmm",
        "breg",
        "dmdreg",
        "pmdreg",
    }:
        return OperandKind.REGISTER
    return OperandKind.SELECTOR


class InstructionTableError(ValueError):
    pass


def _integer(value: object, field: str, base: int = 10) -> int:
    try:
        return (
            int(cast(str | bytes | bytearray, value), base)
            if isinstance(value, (str, bytes, bytearray))
            else int(cast(int, value))
        )
    except (TypeError, ValueError) as exc:
        raise InstructionTableError(f"invalid integer for {field}: {value!r}") from exc


def _fields(row: _TableForm) -> tuple[EncodedField, ...]:
    result = []
    for raw in row["fields"]:
        label = str(raw["label"])
        frame_hi = _integer(raw["hi"], f"{row['name']}.{label}.hi")
        frame_lo = _integer(raw["lo"], f"{row['name']}.{label}.lo")
        match = _RANGED_LABEL.match(label)
        if match:
            operand_hi = _integer(match.group(2), f"{row['name']}.{label}.operand_hi")
            operand_lo = _integer(match.group(3), f"{row['name']}.{label}.operand_lo")
        else:
            operand_hi, operand_lo = frame_hi - frame_lo, 0
        result.append(EncodedField(label, frame_hi, frame_lo, operand_hi, operand_lo))
    return tuple(result)


def _operands(fields: Sequence[EncodedField]) -> tuple[OperandSpec, ...]:
    groups: dict[str, list[EncodedField]] = {}
    order: list[str] = []
    for field in fields:
        match = _RANGED_LABEL.match(field.label)
        name = match.group(1) if match else field.label
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(field)
    return tuple(
        OperandSpec(
            name,
            _operand_kind(name),
            tuple(sorted(groups[name], key=lambda fragment: -fragment.operand_hi)),
        )
        for name in order
    )


def _evidence(form: str, source: str, unconfirmed_bits: int) -> tuple[EvidenceRef, ...]:
    if "undoc" in form.lower() or "firmware only" in source.lower():
        status = EvidenceStatus.PROVISIONAL
    elif unconfirmed_bits:
        status = EvidenceStatus.UNCONFIRMED
    else:
        status = EvidenceStatus.DOCUMENTED
    return (EvidenceRef(f"isa.form.{form}.encoding", status, source),)


class InstructionSet:
    """Deep encoding module: table loading, selection, and field extraction."""

    def __init__(self, forms: Sequence[InstructionForm]):
        self.forms = tuple(forms)
        self._by_id = {form.id: form for form in self.forms}
        if len(self._by_id) != len(self.forms):
            raise ValueError("duplicate SHARC instruction form id")

    @classmethod
    def from_json(
        cls, table: str | Path = TABLE_PATH, mode: str = "visa"
    ) -> InstructionSet:
        if mode not in {"visa", "isa"}:
            raise ValueError(f"unsupported SHARC decode mode {mode!r}")
        try:
            payload = json.loads(Path(table).read_text())
            rows = cast(list[_TableForm], payload["forms"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise InstructionTableError(
                f"cannot load SHARC instruction table {table}"
            ) from exc
        forms = []
        for row in rows:
            if mode == "visa" and not row["visa"]:
                continue
            if mode == "isa" and row["width"] != 48:
                continue
            ident = form_id(row["name"])
            fields = _fields(row)
            mask = _integer(row["mask"], f"{ident}.mask", 16)
            source = str(row.get("source") or "")
            unconfirmed = _integer(
                row.get("unconfirmed_bits") or 0, f"{ident}.unconfirmed_bits"
            )
            forms.append(
                InstructionForm(
                    id=ident,
                    table_name=row["name"],
                    extent_bits=_integer(row["width"], f"{ident}.width"),
                    visa=bool(row["visa"]),
                    isa=bool(row["isa"]),
                    frame_mask=mask,
                    frame_value=_integer(row["value"], f"{ident}.value", 16),
                    fixed_bits=_integer(row["fixed_bits"], f"{ident}.fixed_bits"),
                    leading_fixed_bits=_leading_fixed_bits(mask),
                    fields=fields,
                    operands=_operands(fields),
                    source=source,
                    classic_keys=tuple(row.get("classic_keys") or ()),
                    unconfirmed_bits=unconfirmed,
                    evidence=_evidence(ident, source, unconfirmed),
                )
            )
        return cls(forms)

    def form(self, ident: str) -> InstructionForm:
        return self._by_id[ident]

    def select_frame(self, frame: int) -> Selection:
        hits = [form for form in self.forms if form.matches(frame)]
        if not hits:
            return Selection(None, ())
        leading = max(form.leading_fixed_bits for form in hits)
        hits = [form for form in hits if form.leading_fixed_bits == leading]
        fixed = max(form.fixed_bits for form in hits)
        candidates = tuple(form for form in hits if form.fixed_bits == fixed)
        return Selection(candidates[0] if len(candidates) == 1 else None, candidates)

    def decode_words(self, words: Sequence[int]) -> DecodeResult:
        actual = tuple(words[:3])
        selection = self.select_frame(frame_of(actual))
        if selection.form is None:
            return DecodeResult(None, selection.candidates)
        form = selection.form
        if len(actual) < form.extent_words:
            return DecodeResult(None, selection.candidates, truncated=True)
        frame = frame_of(actual)
        fields = form.extract_fields(frame)
        field_values = {item.field.label: item.value for item in fields}
        operands = tuple(
            OperandValue(operand, operand.extract(field_values))
            for operand in form.operands
        )
        raw = frame >> form.width_shift
        return DecodeResult(
            DecodedInstruction(form, frame, raw, fields, operands),
            selection.candidates,
        )

    def decode_bytes(self, data: bytes) -> DecodeResult:
        words = tuple(
            struct.unpack_from("<H", data, offset)[0]
            for offset in range(0, min(len(data), 6) - 1, 2)
        )
        return self.decode_words(words)


def load_instruction_set(
    table: str | Path = TABLE_PATH, mode: str = "visa"
) -> InstructionSet:
    """Load one immutable instruction set from the current table bytes."""
    return InstructionSet.from_json(Path(table).resolve(), mode)
