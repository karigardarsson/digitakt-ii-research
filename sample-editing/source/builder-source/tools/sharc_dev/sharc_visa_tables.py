"""SHARC+ instruction forms for tools/sharc_dev/sharc_disasm.py, from tools/sharc_dev/sharcspec/decode_table.json.

decode_table.json is built from the public ADI manuals: the SHARC+ Core
Programming Reference Rev 1.5, checked against the classic SHARC Programming
Reference Rev 2.4 (tools/sharc_dev/sharcspec/README.md, docs/sharc/SOURCES.md). It
replaces this file's earlier transcription of Rev 1.4, whose `15b` entry
fixed 3 bits where the manual fixes 7.

Memory holds 16-bit little-endian words; a 32- or 48-bit instruction stores
its most significant word first. decode_table.json gives each form's mask and
value MSB-aligned in a 48-bit frame, frame = (w0 << 32) | (w1 << 16) | w2.
TYPES renumbers them onto each form's own width, so bit width-1 is the MSB of
the first word. decode() picks a form as tools/sharc_dev/sharcspec/sharc_decode.py
does: among the matching forms, the longest run of fixed bits from bit 47
wins, then the most fixed bits; a tie is ambiguous.

TYPES entries: name (the form name without "Type", e.g. "15b", "8a_abs",
"5b_move"), bits, opcode_mask and opcode_value (own width), frame_mask and
frame_value (48-bit frame), fields {label: (hi, lo)} (own width), lead (fixed
bits from the top of the frame), fixed_bits, uncertain (the table marks some
fixed bits unconfirmed) and source.
"""

import os
from collections.abc import Iterable, Sequence

from tools.sharc_dev.sharc_isa import (
    LegacyForm,
    form_id,
    load_instruction_set,
)
from tools.sharc_dev.sharc_isa import (
    frame_of as _frame_of,
)

TABLE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "sharcspec", "decode_table.json"
)


def form_name(name):
    """'Type5b (move)' -> '5b_move'; 'Type8a_abs' -> '8a_abs'."""
    return form_id(name)


def load(path: str = TABLE, mode: str = "visa") -> list[LegacyForm]:
    """-> TYPES for the VISA forms (mode 'visa') or the 48-bit ISA forms ('isa')."""
    return [form.legacy_dict() for form in load_instruction_set(path, mode).forms]


TYPES: list[LegacyForm] = load()
_BY_NAME = {t["name"]: t for t in TYPES}
_ISA = load_instruction_set(TABLE, "visa")


def get_type(name: str) -> LegacyForm | None:
    """-> the TYPES entry of that name, or None."""
    return _BY_NAME.get(name)


def frame_of(words: Iterable[int]) -> int:
    """-> the 48-bit frame of up to three words, most significant first, zero-padded."""
    return _frame_of(words)


def decode(
    words: Iterable[int], types: Sequence[LegacyForm] | None = None
) -> tuple[LegacyForm | None, list[str]]:
    """-> (entry or None, [candidate names]) for the instruction starting at words[0].

    The candidates are the forms that tie for the best match; the entry is None
    when no form matches or several tie."""
    frame = frame_of(words)
    if types is None:
        selection = _ISA.select_frame(frame)
        selected = None if selection.form is None else _BY_NAME[selection.form.id]
        return selected, [form.id for form in selection.candidates]
    hits = [t for t in types if frame & t["frame_mask"] == t["frame_value"]]
    if not hits:
        return None, []
    best = max(t["lead"] for t in hits)
    hits = [t for t in hits if t["lead"] == best]
    most = max(t["fixed_bits"] for t in hits)
    top = [t for t in hits if t["fixed_bits"] == most]
    return (top[0] if len(top) == 1 else None), [t["name"] for t in top]
