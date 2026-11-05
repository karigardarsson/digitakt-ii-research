"""Linear disassembler for SHARC+ VISA code, built on tools/sharc_dev/sharc_visa_tables.py.

    uv run python -m tools.sharc_dev.sharc_disasm REGION.bin [START_OFFSET]

Walks a byte buffer forward instruction by instruction and stops at the first
word it cannot classify: no form matches, several forms tie, or the buffer
ends inside the instruction. It never advances past such a word, because a
wrong length silently desyncs every instruction after it.

Memory holds 16-bit little-endian words, instructions start at even offsets,
and a 32- or 48-bit instruction stores its most significant word first:
insn = (w0 << 32) | (w1 << 16) | w2 for 48 bits, (w0 << 16) | w1 for 32.
A form whose table entry marks some fixed bits unconfirmed yields kind
"uncertain", and the walk continues.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from tools.sharc_dev.sharc_isa import frame_of, load_instruction_set
from tools.sharc_dev.sharc_visa_tables import TYPES, get_type

ISA = load_instruction_set()
ISA48 = load_instruction_set(mode="isa")


def decode_isa48(data: bytes) -> Instruction:
    """One fixed-width ISA word stored as a little-endian 48-bit value."""
    if len(data) != 6:
        return Instruction(0, None, "unknown", kind="unknown", note="unmapped ISA word")
    raw = int.from_bytes(data, "little")
    selection = ISA48.select_frame(raw)
    form = selection.form
    if form is None:
        return Instruction(
            0, None, "unknown", kind="unknown", note="unresolved ISA word"
        )
    return Instruction(
        0,
        6,
        form.id,
        {item.field.label: item.value for item in form.extract_fields(raw)},
        raw=raw,
        kind="uncertain" if form.uncertain else "confident",
    )


class Desync(Exception):
    """disassemble(..., on_unknown="raise") met a word it cannot classify."""

    def __init__(self, offset: int, reason: str, word0: int, hypotheses: list[str]):
        self.offset = offset
        self.reason = reason
        self.word0 = word0
        self.hypotheses = hypotheses
        super().__init__(f"desync at offset {offset:#x}: {reason} (word0={word0:#06x})")


@dataclass
class Instruction:
    """One decoded or undecodable unit from disassemble().

    offset       : byte offset in the buffer
    length_bytes : 2, 4 or 6; None when kind == "unknown"
    type_name    : the TYPES name (e.g. "15b"), or "unknown"
    fields       : {label: value} from the form's fields; empty when unknown
    raw          : the instruction in its own width; for "unknown" the first
                   word, or None when not even one word was left
    kind         : "confident" (all fixed bits confirmed), "uncertain" (the
                   table marks some unconfirmed), or "unknown" (always the
                   last record)
    note         : why the walk stopped, or the source of an uncertain form
    """

    offset: int
    length_bytes: int | None
    type_name: str | None
    fields: dict[str, int] = field(default_factory=dict)
    raw: int | None = None
    kind: str = "confident"
    note: str = ""


def _read_u16(data: bytes, offset: int) -> int | None:
    if offset + 2 > len(data):
        return None
    return struct.unpack_from("<H", data, offset)[0]


def identify(insn: int, bits: int, include_uncertain: bool = False) -> list[str]:
    """Every form of that width whose mask matches insn, most fixed bits first;
    forms with unconfirmed bits only with include_uncertain."""
    names = [
        t["name"]
        for t in TYPES
        if t["bits"] == bits
        and (include_uncertain or not t["uncertain"])
        and insn & t["opcode_mask"] == t["opcode_value"]
    ]

    def fixed_bits(name: str) -> int:
        entry = get_type(name)
        assert entry is not None
        return entry["fixed_bits"]

    return sorted(names, key=lambda n: -fixed_bits(n))


def disassemble(
    data: bytes,
    start_offset: int = 0,
    count: int | None = None,
    on_unknown: str = "yield",
) -> Iterator[Instruction]:
    """Walk data from start_offset, one Instruction per unit, up to count
    instructions (None: until an unknown word or the end of the buffer).

    on_unknown: "yield" yields a final kind="unknown" record and stops;
    "raise" raises Desync instead.
    """
    offset = start_offset
    yielded = 0
    while count is None or yielded < count:
        if offset == len(data):
            return
        words = []
        for i in range(3):
            word = _read_u16(data, offset + 2 * i)
            if word is None:
                break
            words.append(word)
        result = ISA.decode_words(words)
        decoded = result.instruction
        hypotheses = [form.id for form in result.candidates]
        entry = get_type(decoded.form.id) if decoded is not None else None
        if not words:
            reason = f"only {len(data) - offset} byte(s) remain, not enough for a 16-bit word"
        elif entry is None and hypotheses:
            if result.truncated and len(hypotheses) == 1:
                form = result.candidates[0]
                reason = (
                    f"matches {form.id} ({form.extent_bits} bits) but only "
                    f"{len(data) - offset} bytes remain"
                )
            else:
                reason = f"forms tie: {hypotheses}"
        elif entry is None:
            reason = "no form matches"
        else:
            reason = None
        if reason is not None:
            word0 = words[0] if words else 0
            if on_unknown == "raise":
                raise Desync(offset, reason, word0, hypotheses)
            yield Instruction(
                offset=offset,
                length_bytes=None,
                type_name="unknown",
                fields={},
                raw=words[0] if words else None,
                kind="unknown",
                note=reason,
            )
            return
        assert entry is not None
        assert decoded is not None
        yield Instruction(
            offset=offset,
            length_bytes=entry["bits"] // 8,
            type_name=entry["name"],
            fields=decoded.field_dict(),
            raw=decoded.raw,
            kind="uncertain" if entry["uncertain"] else "confident",
            note=f"source: {entry['source']}" if entry["uncertain"] else "",
        )
        offset += entry["bits"] // 8
        yielded += 1


@dataclass
class WalkReport:
    start_offset: int
    end_offset: int
    bytes_total: int
    bytes_decoded: int
    instructions: int
    confident: int
    uncertain: int
    stopped_reason: str
    last_instruction: Instruction | None

    @property
    def fraction_decoded(self) -> float:
        return self.bytes_decoded / self.bytes_total if self.bytes_total else 0.0


class ShortWordReader(Protocol):
    """Minimal loader-memory interface for exact-PC decoding."""

    def read_sw(self, pc_sw: int, size: int) -> bytes | None:
        return None


def decode_loaded_at(reader: ShortWordReader, pc_sw: int) -> Instruction:
    """Decode exactly at a loader-backed short-word PC.

    Decode from the largest contiguous mapped window (6, 4, then 2 bytes),
    rather than sweeping across loader blocks or gaps.
    """
    for size in (6, 4, 2):
        window = reader.read_sw(pc_sw, size)
        if window is not None:
            return next(disassemble(window, count=1))
    return Instruction(
        0, None, "unknown", kind="unknown", note="PC unmapped in loader memory"
    )


# --- Successor-confidence width correction -----------------------------
#
# tools/sharc_dev/sharc_isa.py's InstructionSet.select_frame() ranks matching forms by
# leading fixed bits, so a wider form whose header shares a narrower form's
# prefix always wins the tie even when every one of its confirmed bits sits
# inside the first word(s) -- it has verified nothing a narrower reading
# didn't already verify, and its "extra" word(s) are free bits that decode
# into *something* almost any time (DT2 1.16 sw 0x1c4b99: Type2b, 32 bits
# cut down to leading_fixed_bits=9, fixed_bits=9, outranks Type2c,
# fixed_bits=4, on the same word, even though the wider reading's own 6-byte
# gap only decodes as a NEVER_ALIGNED_FORMS trap; sw 0x1c4e10: the wider
# reading's extra word is provably free -- a narrower reading rejoins one of
# its own successor offsets a few instructions later).
#
# resolve_confident_width() is the one function that decides this, so a
# single-PC decode (decode_confident()/decode_confident_loaded() below, used
# by tools/sharc_dev/sharc_core/sequencer.decode_at()) and a whole-image walk
# (tools/sharc_dev/sharcimm.py's decode_all(), which calls resolve_confident_width()
# directly with its own memoized table as raw_at, for speed over a whole
# image) make the identical choice -- see docs/findings/05-sharc-isa-and-
# decoding.md, "One decode path".
WIDTH_LOOKAHEAD = 8
# Ambiguous short computes can remain out of phase for more than eight
# instructions. Search farther only for an otherwise unresolved 2b/2c pair.
WIDTH_REJOIN_LOOKAHEAD = 16

# tools/sharc_dev/sharcfn/'s module docstring: Type10a_rel (and, as measured there,
# Type10a_abs too) is absent from tools/sharc_dev/sharc_visa_tables.py's VISA form set
# entirely, so any real occurrence is a disassembler desync landing on a
# coincidental bit-pattern match, not a genuine instruction -- confirmed on
# DT2 1.16 (only 4 of 4,808 raw Type10a_rel matches in the whole image ever
# reach "aligned" status, all four raw field dumps with no real semantics,
# tools/sharc_dev/sharcimm.py's decode_all() v. history). Never treated as a real
# decoded instruction anywhere in this project.
NEVER_ALIGNED_FORMS = {"10a_rel", "10a_abs"}


def _narrow_candidates_from_words(
    words: list[int], offset: int, max_bits: int
) -> list[Instruction]:
    """Confident/uncertain Instructions decodable from WORDS (as read at
    OFFSET) from forms narrower than MAX_BITS, using tools/sharc_dev/sharc_isa.py's own
    form list and matches()/extract_fields() directly -- bypassing
    select_frame()'s width-priority tie-break so a narrower form it
    discarded is visible here too."""
    if not words:
        return []
    frame = frame_of(words)
    widths = sorted({f.extent_bits for f in ISA.forms if f.extent_bits < max_bits})
    out = []
    for width in widths:
        if len(words) < width // 16:
            continue
        forms = [f for f in ISA.forms if f.extent_bits == width and f.matches(frame)]
        if not forms:
            continue
        best = max(f.fixed_bits for f in forms)
        ties = [f for f in forms if f.fixed_bits == best]
        if len(ties) != 1:
            continue
        form = ties[0]
        field_values = {
            item.field.label: item.value for item in form.extract_fields(frame)
        }
        out.append(
            Instruction(
                offset=offset,
                length_bytes=width // 8,
                type_name=form.id,
                fields=field_values,
                raw=frame >> form.width_shift,
                kind="uncertain" if form.uncertain else "confident",
                note="",
            )
        )
    return out


def _narrow_candidates(data: bytes, offset: int, max_bits: int) -> list[Instruction]:
    return _narrow_candidates_from_words(_read_words(data, offset), offset, max_bits)


def _read_words(data: bytes, offset: int) -> list[int]:
    words = []
    for i in range(3):
        word = _read_u16(data, offset + 2 * i)
        if word is None:
            break
        words.append(word)
    return words


def _resolved_reach_clean(pos, first, raw_at, narrow_at, lookahead):
    """True when FIRST at POS and its LOOKAHEAD successors are all confident,
    each successor width-resolved by resolve_confident_width()."""
    cur, p = first, pos
    for _ in range(lookahead):
        if cur is None or cur.kind != "confident":
            return False
        p += cur.length_bytes
        cur = resolve_confident_width(
            p, raw_at(p), raw_at, narrow_at, lookahead, _resolved_successors=False
        )
    return True


def resolve_confident_width(
    pos, insn, raw_at, narrow_at, lookahead=WIDTH_LOOKAHEAD, _resolved_successors=True
):
    """The one width/form choice both tools/sharc_dev/sharcimm.py's decode_all() and
    tools/sharc_dev/sharc_core/sequencer.decode_at() make: prefer a narrower form over
    a wider one at the same position when the narrower form's own successor
    chain decodes confidently for LOOKAHEAD instructions where the wider
    form's does not, or independently rejoins one of the wider form's own
    successor boundaries -- proof its extra word(s) added no information a
    narrower reading lacked (see this module's WIDTH_LOOKAHEAD comment).

    pos is the instruction's own position, in whatever unit insn.length_bytes
    advances it by and raw_at/narrow_at accept (a byte offset into a flat
    buffer for decode_confident(); twice a short-word PC for
    decode_confident_loaded()). raw_at(pos) -> Instruction | None is the
    *uncorrected* single-form decode at pos (already excluding
    NEVER_ALIGNED_FORMS); narrow_at(pos, max_bits) -> list[Instruction] is
    _narrow_candidates()/_narrow_candidates_from_words() or equivalent.

    _resolved_successors is internal: the 2b/2c fallback below re-walks a
    chain with every successor itself width-resolved (False for those inner
    calls, so the fallback does not recurse).
    """
    if insn is None or insn.length_bytes is None or insn.length_bytes <= 2:
        return insn

    def confident_reach(pos0, insn0, limit=lookahead):
        boundaries = {pos0}
        cur, p = insn0, pos0
        for _ in range(limit):
            if cur is None or cur.kind != "confident":
                return boundaries, False
            p += cur.length_bytes
            boundaries.add(p)
            cur = raw_at(p)
        return boundaries, True

    wide_boundaries, wide_clean = confident_reach(pos, insn)
    candidates = narrow_at(pos, insn.length_bytes * 8)
    if not candidates:
        return insn
    wide_targets = wide_boundaries - {pos}
    for cand in candidates:
        if cand.kind != "confident":
            continue
        cand_boundaries, cand_clean = confident_reach(pos, cand)
        if not cand_clean:
            # A short compute followed by shifts and further short computes:
            # the raw chain reads the next 2c's word pair as a 2b and loses
            # phase one instruction later, although that successor resolves
            # to 2c itself. DN2 1.11 sw 0x1c4f4a.
            if (
                _resolved_successors
                and not wide_clean
                and insn.type_name == "2b"
                and cand.type_name == "2c"
                and _resolved_reach_clean(pos, cand, raw_at, narrow_at, lookahead)
                and not _resolved_reach_clean(pos, insn, raw_at, narrow_at, lookahead)
            ):
                return cand
            continue
        if not wide_clean or (cand_boundaries & wide_targets):
            return cand
        if (
            lookahead == WIDTH_LOOKAHEAD
            and insn.type_name == "2b"
            and cand.type_name == "2c"
        ):
            extended_wide, _ = confident_reach(pos, insn, WIDTH_REJOIN_LOOKAHEAD)
            extended_narrow, _ = confident_reach(pos, cand, WIDTH_REJOIN_LOOKAHEAD)
            # Both prefixes are confident through each returned boundary.
            # Once they rejoin, later uncertainty is shared and cannot
            # justify consuming the short instruction's successor word.
            if (extended_narrow - {pos}) & (extended_wide - {pos}):
                return cand
    return insn


def _raw_at_flat(data: bytes, offset: int) -> Instruction | None:
    """The uncorrected single-form decode at OFFSET in a flat buffer, or
    None where disassemble() cannot decode there at all or only matches a
    NEVER_ALIGNED_FORMS decode trap."""
    insn = next(disassemble(data, offset, count=1), None)
    if insn is None or insn.kind == "unknown" or insn.type_name in NEVER_ALIGNED_FORMS:
        return None
    return insn


def decode_confident(data: bytes, offset: int) -> Instruction:
    """Single-instruction decode at OFFSET in a flat buffer, with
    resolve_confident_width() applied -- the same correction
    tools/sharc_dev/sharcimm.py's decode_all() makes over a whole image. Fails closed
    exactly as disassemble(..., count=1) does when nothing decodes at OFFSET
    at all. Unlike decode_all()'s whole-image table, this does not exclude a
    NEVER_ALIGNED_FORMS decode landed on directly (decode_all() never records
    one as an "aligned" real instruction anywhere, so the database gives no
    verdict on a PC only reachable this way, and excluding it here would
    change decode_confident()'s own contract for a caller that lands there on
    purpose -- see tests/sharc/trace/test_sharc_trace_float.py's PhaseAOpcodeRegressionTest for
    Type10a_rel's own field layout). raw_at() below still excludes it from a
    *successor* chain, exactly as decode_all()'s table does, so the width
    correction remains identical to the database's."""
    insn = next(disassemble(data, offset, count=1), None)
    if insn is None:
        return Instruction(offset, None, "unknown", kind="unknown", note="no data")
    if insn.kind == "unknown":
        return insn
    return resolve_confident_width(
        offset,
        insn,
        lambda p: _raw_at_flat(data, p),
        lambda p, mb: _narrow_candidates(data, p, mb),
    )


def _loaded_words(reader: ShortWordReader, pc_sw: int) -> list[int]:
    """Up to 3 16-bit words at PC_SW, from the largest contiguous mapped
    window (6, 4, then 2 bytes) -- the same window strategy
    decode_loaded_at() uses, so a partial mapping recovers exactly the words
    a real decode there would see."""
    for size in (6, 4, 2):
        window = reader.read_sw(pc_sw, size)
        if window is not None:
            return list(struct.unpack("<%dH" % (size // 2), window))
    return []


def _raw_at_loaded(reader: ShortWordReader, pos: int) -> Instruction | None:
    pc_sw, remainder = divmod(pos, 2)
    if remainder:
        return None
    insn = decode_loaded_at(reader, pc_sw)
    if insn.kind == "unknown" or insn.type_name in NEVER_ALIGNED_FORMS:
        return None
    return insn


def decode_confident_loaded(reader: ShortWordReader, pc_sw: int) -> Instruction:
    """Same correction as decode_confident(), for loader-backed memory
    addressed by a short-word PC -- tools/sharc_dev/sharc_core/sequencer.decode_at()'s
    LoadedMemory path, the tracer's and concrete runner's only decode entry.
    Fails closed exactly as decode_loaded_at() does; see decode_confident()'s
    docstring for why a NEVER_ALIGNED_FORMS decode landed on directly is not
    excluded here either (only from a successor chain, via raw_at())."""
    insn = decode_loaded_at(reader, pc_sw)
    if insn.kind == "unknown":
        return insn
    pos = 2 * pc_sw
    return resolve_confident_width(
        pos,
        insn,
        lambda p: _raw_at_loaded(reader, p),
        lambda p, mb: _narrow_candidates_from_words(
            _loaded_words(reader, p // 2), p, mb
        ),
    )


def walk_and_report(data: bytes, start_offset: int = 0) -> WalkReport:
    """Run disassemble() until it stops, and report how far it got and why."""
    offset = start_offset
    n_conf = n_unc = 0
    last: Instruction | None = None
    stopped_reason = "reached end of buffer cleanly"
    for rec in disassemble(data, start_offset=start_offset, on_unknown="yield"):
        last = rec
        if rec.kind == "unknown":
            stopped_reason = rec.note
            break
        if rec.kind == "confident":
            n_conf += 1
        else:
            n_unc += 1
        assert rec.length_bytes is not None
        offset = rec.offset + rec.length_bytes
    return WalkReport(
        start_offset=start_offset,
        end_offset=offset,
        bytes_total=len(data) - start_offset,
        bytes_decoded=offset - start_offset,
        instructions=n_conf + n_unc,
        confident=n_conf,
        uncertain=n_unc,
        stopped_reason=stopped_reason,
        last_instruction=last,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    path = sys.argv[1]
    start = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0
    try:
        blob = Path(path).read_bytes()
    except OSError as exc:
        raise SystemExit(str(exc)) from exc
    report = walk_and_report(blob, start)
    print(f"Walked {path} from offset {start:#x}:")
    print(f"  buffer size:      {len(blob)} bytes")
    print(
        f"  decoded:          {report.bytes_decoded} / {report.bytes_total} bytes "
        f"({report.fraction_decoded:.1%})"
    )
    print(
        f"  instructions:     {report.instructions} "
        f"(confident={report.confident}, uncertain={report.uncertain})"
    )
    print(f"  stopped at:       offset {report.end_offset:#x}")
    print(f"  stopped because:  {report.stopped_reason}")
