"""Differential test harness for the SHARC+ core: Python (tools/sharc_dev/sharc_core,
driven concretely through tools/sharc_dev/sharc_run.Runner) against a future native
implementation (a Rust crate, ``packages/emulator/sharc/``, built as a C-ABI cdylib and
loaded with ctypes -- see scratchpad/rt-native-core-design.md section 4).

The native core does not exist yet in this tree. This module builds the
Python side of the contract it will be checked against:

1. A canonical, engine-independent machine-state format (``export_state``/
   ``import_state`` for the structured dict form, ``pack_state``/
   ``unpack_state`` for the binary wire form a Rust side mirrors).
2. An ``Engine`` protocol with a ``PythonEngine`` adapter (wraps
   ``sharc_run.Runner``) and a ``NativeEngine`` ctypes stub (raises if the
   library is absent -- there is nothing to load yet).
3. Lockstep comparison: run two engines from the same imported state, step
   them in tandem, and stop at the first state divergence with a readable
   diff. ``run_frame_lockstep`` does the same at frame granularity, reusing
   tools/sharc_dev/sharc_harness/'s/tools/sharc_dev/sharc_replay.py's own frame-call
   primitives (``write_dma_transfer`` / ``drive_dma_completion`` /
   ``call_frame_collect_all``) instead of re-implementing frame delivery.
4. A self-test: two independent PythonEngine instances must agree with each
   other (proving the export/import/compare plumbing is a closed loop), and
   a deliberately mutated copy must be caught with a clear diff.
5. A random-operand corpus generator over sharc_core's compute op tables
   (ALU_OPS, MULT_OPS, SHIFT_OPS, SHORT_OPS), producing (state, instruction)
   cases a native core can replay and check against the recorded Python
   result -- see ``generate_compute_corpus``.

--------------------------------------------------------------------------
Canonical state format (the contract)
--------------------------------------------------------------------------

The *structured* form (what ``export_state``/``compare_states`` work with)
is a plain nested dict of ints/strs -- easy to diff and to serialise as
JSON for the corpus. The *binary* form (``pack_state``/``unpack_state``) is
what a Rust core mirrors byte-for-byte; every multi-byte field is
little-endian, and the layout is fixed and versioned (``STATE_FORMAT_
VERSION``, the first four bytes of every blob):

    offset  size  field
    0       4     magic b"SHRD"
    4       4     format version (uint32)
    8       4     pc_sw (uint32)
    12      1     stopped_present (0/1)
    13      N     stopped reason, only if stopped_present (uint16 length +
                   UTF-8 bytes)
    ...     ...   uregs: UREG_COUNT (128) entries, each 9 bytes --
                   kind:uint8 (0=Unknown,1=Const,2=PartialConst), then
                   value:uint32, mask:uint32 (mask is 0xFFFFFFFF for a
                   Const, the known-bit mask for a PartialConst, 0 for
                   Unknown). Register N's UREG code is N (see
                   sharc_core.encoding.UREG_NAMES -- code == array index,
                   0..127: R0-15, I0-15, M0-15, L0-15, B0-15, S0-15, then
                   the named system registers up to USTAT4).
    ...     ...   special: len(SPECIAL_SLOTS) (7) entries in SPECIAL_SLOTS
                   order (MRF, MRB, MSF, MSB, BFFWRP, BFF_HI, BFF_LO), each
                   21 bytes -- kind:uint8 (0=absent/Unknown, 1=Const,
                   2=MR), value:10 bytes LE (80-bit), mask:10 bytes LE
                   (80-bit; a Const's mask is the low 4 bytes 0xFFFFFFFF,
                   rest zero).
    ...     4     mmr_count (uint32)
    ...     ...   mmr_count entries, each 13 bytes -- address:uint32,
                   then a 9-byte Value (kind/value/mask as uregs above),
                   sorted by address.
    ...     1     pending_present (0/1)
    ...     13    pending, only if present -- target_present:uint8,
                   target:uint32 (valid only if target_present), call:
                   uint8, slots:uint8, return_from_call:uint8,
                   return_sw_present:uint8, return_sw:int32 (valid only if
                   return_sw_present; AFTER_DELAY_SLOTS, -1, is a real
                   value here, distinct from "absent")
    ...     2     loop_count (uint16)
    ...     ...   loop_count entries, each 16 bytes -- start_sw:uint32,
                   end_sw:uint32, remaining:uint32, mode:uint32
    ...     2     call_stack_count (uint16)
    ...     ...   call_stack_count uint32 entries
    ...     2     status_stack_count (uint16)
    ...     ...   status_stack_count entries, each 27 bytes: three 9-byte
                   Values back to back (the PUSH STS triple)
    ...     4     memory_range_count (uint32)
    ...     ...   memory_range_count entries -- address:uint32,
                   length:uint32, then LENGTH bytes (a byte with no known
                   value, i.e. ``_dm_read`` returned None, is *not*
                   representable here: ``export_state`` only ever includes
                   a range if every byte in it read as a concrete Const;
                   callers wanting partial coverage must request narrower
                   ranges)
    ...     4     page_hash_count (uint32)
    ...     ...   page_hash_count entries -- page_address:uint32 (PAGE_
                   SIZE=4096-aligned), sha256:32 bytes -- see
                   ``_page_hashes`` for exactly what is hashed (the
                   overlay's own dirty bytes in that page, sorted by
                   address, as (address:uint64 LE, value:uint8) pairs).
    ...     1     v2 bank_model (0/1)
    ...     12    v2 bank_active_mask, bank_pending_mask, bank_requested_mask:
                   three int32 values; -1 means no pending/requested change.
    ...     864   v2 inactive bank: UREG codes 0..95, each a 9-byte Value.
    ...     1     v3 stack_model (0/1)
    ...     8     v3 pc_stack_pending, pc_stack_requested: two int32 values;
                   -1 means no pending/requested truncation.
    ...     2     v3 physical pc_stack_count (uint16)
    ...     ...   v3 physical PC stack: count uint32 entries.

    ...     1     v4 physical loop_depth (0..6)
    ...   108     v4 six retained (LADDR, CURLCNTR) Value32 pairs

Readers accept v1 with banking disabled and unknown inactive registers.
Readers accept v1/v2 with physical stacks disabled and no entries.
Readers accept v3 with empty physical loop resources.
Writers emit v4, preserving inactive registers, independent physical PC
entries, both selector/pointer pipelines, and all six physical loop slots. Software IRQ delivery is an
explicit run option, not a wire-format flag.

Deliberately out of the canonical format: the trace/event log
(``State.trace``, observability only), the calibration/provisional
bookkeeping fields, and PEx/PEy SIMD state -- SIMD has no separate register
file in sharc_core (``MODE1.PEYEN`` plus the S0-15 register bank, both
already covered by the uregs above and MODE1 being an ordinary UREG).
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import random
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc_harness as h  # noqa: E402
from tools.sharc_dev import sharc_run as sr  # noqa: E402
from tools.sharc_dev import sharc_trace as st  # noqa: E402  (re-exports sharc_core)
from tools.sharc_dev.sharc_core.values import (  # noqa: E402
    Operand,  # noqa: E402  (not re-exported by sharc_trace)
)
from tools.sharc_dev.sharcldr import LoadedMemory  # noqa: E402

# ---------------------------------------------------------------------------
# 1. Canonical state layout
# ---------------------------------------------------------------------------

STATE_FORMAT_VERSION = 4
_MAGIC = b"SHRD"

# sharc_core.encoding.UREG_NAMES is a 128-entry tuple; a UREG's code IS its
# index (see sharc_core/state.py's own _ureg_raw). Fixed here so a change to
# UREG_NAMES's *length* is caught (an ordering change inside it is already
# everyone's problem, not just this module's).
UREG_COUNT = 128
assert len(st.UREG_NAMES) == UREG_COUNT, (
    "sharc_core.encoding.UREG_NAMES grew or shrank (%d, expected %d) -- "
    "bump STATE_FORMAT_VERSION and this constant together"
) % (len(st.UREG_NAMES), UREG_COUNT)

# state.special's known string keys (state.py/compute_mult.py/
# compute_shift.py), in the fixed order the binary layout uses.
SPECIAL_SLOTS: tuple[str, ...] = (
    "MRF",
    "MRB",
    "MSF",
    "MSB",
    "BFFWRP",
    "BFF_HI",
    "BFF_LO",
)

PAGE_SIZE = 4096

_VK_UNKNOWN, _VK_CONST, _VK_PARTIAL = 0, 1, 2
_SK_ABSENT, _SK_CONST, _SK_MR = 0, 1, 2


def _export_value32(v: st.Value) -> dict:
    """A uregs-file entry (Const/Unknown/PartialConst only -- an Affine
    means this state came from the symbolic tracer, not a concrete Runner,
    which this harness does not support; see the module docstring)."""
    if isinstance(v, st.Const):
        return {"kind": _VK_CONST, "value": v.value, "mask": 0xFFFFFFFF}
    if isinstance(v, st.PartialConst):
        return {"kind": _VK_PARTIAL, "value": v.bits, "mask": v.mask}
    if isinstance(v, st.Unknown):
        return {"kind": _VK_UNKNOWN, "value": 0, "mask": 0}
    raise TypeError(
        "cannot export a non-concrete register value %r (Affine values "
        "only ever come from the symbolic tracer, not sharc_run.Runner)" % (v,)
    )


def _import_value32(d: Mapping[str, int]) -> st.Value:
    kind = d["kind"]
    if kind == _VK_CONST:
        return st.Const(d["value"])
    if kind == _VK_PARTIAL:
        return st.PartialConst(d["mask"], d["value"])
    return st.Unknown("uninitialized (imported)")


def _export_special(v: Operand | st.MR | None) -> dict:
    if v is None or isinstance(v, st.Unknown):
        return {"kind": _SK_ABSENT, "value": 0, "mask": 0}
    if isinstance(v, st.MR):
        return {"kind": _SK_MR, "value": v.bits, "mask": v.mask}
    if isinstance(v, st.Const):
        return {"kind": _SK_CONST, "value": v.value, "mask": 0xFFFFFFFF}
    raise TypeError("unexpected special-slot value %r" % (v,))


def _import_special(d: Mapping[str, int]) -> Operand | st.MR:
    kind = d["kind"]
    if kind == _SK_MR:
        return st.MR(d["mask"], d["value"])
    if kind == _SK_CONST:
        return st.Const(d["value"])
    return st.Unknown("uninitialized (imported)")


def _export_pending(p: st.Pending | None) -> dict | None:
    if p is None:
        return None
    return {
        "target": p.target,
        "call": p.call,
        "slots": p.slots,
        "return_from_call": p.return_from_call,
        "return_sw": p.return_sw,
    }


def _import_pending(d: Mapping[str, Any] | None) -> st.Pending | None:
    if d is None:
        return None
    return st.Pending(
        target=d["target"],
        call=d["call"],
        slots=d["slots"],
        return_from_call=d["return_from_call"],
        return_sw=d["return_sw"],
    )


def _export_loop(loop: st.Loop) -> dict:
    return {
        "start_sw": loop.start_sw,
        "end_sw": loop.end_sw,
        "remaining": loop.remaining,
        "mode": loop.mode,
    }


def _import_loop(d: Mapping[str, int]) -> st.Loop:
    return st.Loop(d["start_sw"], d["end_sw"], d["remaining"], d["mode"])


def _read_bytes(state: st.State, address: int, length: int) -> bytes | None:
    """LENGTH concrete bytes from ADDRESS (overlay-then-loader, exactly
    ``sharc_core.memory._dm_read``'s own precedence, read one byte at a
    time through that same function so this never re-implements it) --
    None if any byte in the range is not a concrete Const (Unknown,
    unmapped, or an unmodeled MMR)."""
    out = bytearray(length)
    for i in range(length):
        value = st._dm_read(state, address + i, 1)
        if not isinstance(value, st.Const):
            return None
        out[i] = value.value & 0xFF
    return bytes(out)


def _write_bytes(state: st.State, address: int, data: bytes) -> None:
    for i, byte in enumerate(data):
        if not st._dm_write(state, address + i, 1, st.Const(byte)):
            raise ValueError(
                "import_state: byte write at %#x did not take effect "
                "(outside a mapped DM region)" % (address + i)
            )


def _page_hashes(overlay: Mapping[int, int]) -> dict[int, str]:
    """sha256 per PAGE_SIZE-aligned page of OVERLAY's own dirty bytes
    (sorted (address:uint64-LE, value:uint8) pairs) -- the design doc's
    "hash of dirty memory pages" (scratchpad/rt-native-core-design.md
    section 4.2), over what this engine's own run wrote, not the whole
    address space. Two engines that wrote the same bytes to the same
    addresses hash equal regardless of unrelated memory (the loader
    image, or an earlier run's leftovers in a shared LoadedMemory) neither
    ever touched."""
    pages: dict[int, dict[int, int]] = {}
    for address, value in overlay.items():
        page = address - (address % PAGE_SIZE)
        pages.setdefault(page, {})[address] = value & 0xFF
    hashes: dict[int, str] = {}
    for page, byte_map in pages.items():
        digest = hashlib.sha256()
        for address in sorted(byte_map):
            digest.update(struct.pack("<QB", address, byte_map[address]))
        hashes[page] = digest.hexdigest()
    return hashes


def export_state(
    state: st.State,
    *,
    memory_ranges: Sequence[tuple[int, int]] = (),
    page_hash: bool = True,
) -> dict:
    """The canonical structured view of STATE (see the module docstring).

    ``memory_ranges``, ``[(address, length), ...]``, are the only raw bytes
    included (a full-image dump is neither necessary nor affordable -- see
    CLAUDE.md's RAM-budget rule); a range with any non-concrete byte is
    reported with its bytes as ``None`` rather than silently dropped, so a
    caller who asked for one and does not get it back knows to look.
    ``page_hash=True`` (the default) adds a page-hash summary of every
    dirty overlay page (see ``_page_hashes``) -- cheap even for a large
    overlay, and the first thing a lockstep diff should compare before
    paying for any explicit range.
    """
    uregs = {
        code: _export_value32(st._ureg_raw(state.uregs, code))
        for code in range(UREG_COUNT)
    }
    special = {name: _export_special(state.special.get(name)) for name in SPECIAL_SLOTS}
    mmrs = {
        address: _export_value32(value) for address, value in sorted(state.mmrs.items())
    }
    ranges = {}
    for address, length in memory_ranges:
        data = _read_bytes(state, address, length)
        ranges[address] = {
            "length": length,
            "data": data.hex() if data is not None else None,
        }
    result = {
        "format_version": STATE_FORMAT_VERSION,
        "pc_sw": state.pc_sw,
        "stopped": state.stopped,
        "uregs": uregs,
        "special": special,
        "mmrs": mmrs,
        "pending": _export_pending(state.pending),
        "loops": [_export_loop(loop) for loop in state.loops],
        "call_stack": list(state.call_stack),
        "stack_model": state.stack_model,
        "pc_stack": list(state.pc_stack),
        "loop_depth": state.loop_depth,
        "loop_slots": [
            [_export_value32(a), _export_value32(c)] for a, c in state.loop_slots
        ],
        "pc_stack_pending": state.pc_stack_pending,
        "pc_stack_requested": state.pc_stack_requested,
        "status_stack": [
            [_export_value32(v) for v in triple] for triple in state.status_stack
        ],
        "memory_ranges": ranges,
        "bank_model": state.bank_model,
        "bank_active_mask": state.bank_active_mask,
        "bank_pending_mask": state.bank_pending_mask,
        "bank_requested_mask": state.bank_requested_mask,
        "bank_alt": {
            code: _export_value32(
                state.bank_alt.get(code, st.Unknown("uninitialized alternate register"))
            )
            for code in range(96)
        },
    }
    if page_hash:
        result["page_hashes"] = _page_hashes(state.overlay)
    return result


def import_state(
    fields: Mapping[str, Any],
    *,
    data: LoadedMemory | None = None,
    explicit_memory_model: bool = True,
    approx_recips: bool = True,
    assume_nw32: bool = True,
) -> st.State:
    """The inverse of ``export_state``: a fresh, concrete ``State`` seeded
    from FIELDS, ready to hand to a ``PythonEngine``/``Runner``.

    DATA (a ``LoadedMemory``) backs ``State.concrete``; ``None`` is fine for
    a synthetic, memory-free instruction-level case (the compute corpus:
    see ``generate_compute_corpus``) as long as the instruction(s) run never
    touch DM/PM. ``explicit_memory_model``/``approx_recips``/``assume_nw32``
    default True/True/True, matching ``sharc_harness._make_runner``'s own
    always-on options (not ``sharc_run.make_state``'s bare defaults) since
    this harness's callers -- the self-test and the corpus generator -- both
    want a fully-concrete, non-forking run.
    """
    uregs = {
        code: _import_value32(
            fields["uregs"][str(code)]
            if str(code) in fields["uregs"]
            else fields["uregs"][code]
        )
        for code in range(UREG_COUNT)
    }
    special = {name: _import_special(fields["special"][name]) for name in SPECIAL_SLOTS}
    mmrs = {
        int(address): _import_value32(value)
        for address, value in fields["mmrs"].items()
    }
    state = st.State(
        pc_sw=fields["pc_sw"],
        uregs=uregs,
        special=special,
        mmrs=mmrs,
        pending=_import_pending(fields.get("pending")),
        loops=[_import_loop(loop) for loop in fields.get("loops", ())],
        call_stack=list(fields.get("call_stack", ())),
        stack_model=bool(fields.get("stack_model", False)),
        pc_stack=list(fields.get("pc_stack", ())),
        loop_depth=int(fields.get("loop_depth", 0)),
        loop_slots=[
            (_import_value32(pair[0]), _import_value32(pair[1]))
            for pair in fields["loop_slots"]
        ]
        if "loop_slots" in fields
        else [
            (st.Unknown("loop address"), st.Unknown("loop counter")) for _ in range(6)
        ],
        pc_stack_pending=int(fields.get("pc_stack_pending", -1)),
        pc_stack_requested=int(fields.get("pc_stack_requested", -1)),
        status_stack=[
            (
                _import_value32(triple[0]),
                _import_value32(triple[1]),
                _import_value32(triple[2]),
            )
            for triple in fields.get("status_stack", ())
        ],
        concrete=data,
        record_events=False,
        explicit_memory_model=explicit_memory_model,
        approx_recips=approx_recips,
        assume_nw32=assume_nw32,
        follow_loaded_calls=data is not None,
        max_call_depth=64,
        bank_model=bool(fields.get("bank_model", False)),
        bank_active_mask=int(fields.get("bank_active_mask", 0)),
        bank_pending_mask=int(fields.get("bank_pending_mask", -1)),
        bank_requested_mask=int(fields.get("bank_requested_mask", -1)),
        bank_alt={
            code: _import_value32(
                fields.get("bank_alt", {}).get(
                    code,
                    fields.get("bank_alt", {}).get(
                        str(code), {"kind": 0, "value": 0, "mask": 0}
                    ),
                )
            )
            for code in range(96)
        },
    )
    for range_address, range_dict in fields.get("memory_ranges", {}).items():
        if range_dict["data"] is not None:
            _write_bytes(
                state,
                int(range_dict.get("address", range_address)),
                bytes.fromhex(range_dict["data"]),
            )
    return state


# ---------------------------------------------------------------------------
# Binary wire format
# ---------------------------------------------------------------------------


def _pack_value32(d: Mapping[str, int]) -> bytes:
    return struct.pack(
        "<BII", d["kind"], d["value"] & 0xFFFFFFFF, d["mask"] & 0xFFFFFFFF
    )


def _unpack_value32(buf: bytes, off: int) -> tuple[dict, int]:
    kind, value, mask = struct.unpack_from("<BII", buf, off)
    return {"kind": kind, "value": value, "mask": mask}, off + 9


def _int_to_bytes80(value: int) -> bytes:
    return (value & ((1 << 80) - 1)).to_bytes(10, "little")


def _bytes80_to_int(buf: bytes) -> int:
    return int.from_bytes(buf, "little")


def _pack_special(d: Mapping[str, int]) -> bytes:
    return bytes([d["kind"]]) + _int_to_bytes80(d["value"]) + _int_to_bytes80(d["mask"])


def _unpack_special(buf: bytes, off: int) -> tuple[dict, int]:
    kind = buf[off]
    value = _bytes80_to_int(buf[off + 1 : off + 11])
    mask = _bytes80_to_int(buf[off + 11 : off + 21])
    return {"kind": kind, "value": value, "mask": mask}, off + 21


def pack_state(fields: Mapping[str, Any]) -> bytes:
    """FIELDS (an ``export_state()`` dict) as the fixed binary layout the
    module docstring documents -- what a Rust core's own state struct
    mirrors field-for-field."""
    out = bytearray()
    out += _MAGIC
    out += struct.pack("<I", STATE_FORMAT_VERSION)
    out += struct.pack("<I", fields["pc_sw"] & 0xFFFFFFFF)
    stopped = fields.get("stopped")
    if stopped is None:
        out += bytes([0])
    else:
        encoded = stopped.encode("utf-8")
        out += bytes([1]) + struct.pack("<H", len(encoded)) + encoded
    for code in range(UREG_COUNT):
        out += _pack_value32(
            fields["uregs"][code]
            if code in fields["uregs"]
            else fields["uregs"][str(code)]
        )
    for name in SPECIAL_SLOTS:
        out += _pack_special(fields["special"][name])
    mmrs = fields["mmrs"]
    out += struct.pack("<I", len(mmrs))
    for address in sorted(int(a) for a in mmrs):
        out += struct.pack("<I", address)
        out += _pack_value32(mmrs[address] if address in mmrs else mmrs[str(address)])
    pending = fields.get("pending")
    if pending is None:
        out += bytes([0])
    else:
        out += bytes([1])
        target_present = pending["target"] is not None
        out += bytes([1 if target_present else 0])
        out += struct.pack("<I", (pending["target"] or 0) & 0xFFFFFFFF)
        out += bytes([1 if pending["call"] else 0])
        out += bytes([pending["slots"] & 0xFF])
        out += bytes([1 if pending["return_from_call"] else 0])
        return_sw_present = pending["return_sw"] is not None
        out += bytes([1 if return_sw_present else 0])
        out += struct.pack("<i", pending["return_sw"] or 0)
    loops = fields.get("loops", ())
    out += struct.pack("<H", len(loops))
    for loop in loops:
        out += struct.pack(
            "<IIII", loop["start_sw"], loop["end_sw"], loop["remaining"], loop["mode"]
        )
    call_stack = fields.get("call_stack", ())
    out += struct.pack("<H", len(call_stack))
    for value in call_stack:
        out += struct.pack("<I", value & 0xFFFFFFFF)
    status_stack = fields.get("status_stack", ())
    out += struct.pack("<H", len(status_stack))
    for triple in status_stack:
        for v in triple:
            out += _pack_value32(v)
    ranges = fields.get("memory_ranges", {})
    concrete_ranges = [
        (int(address), r) for address, r in ranges.items() if r.get("data") is not None
    ]
    out += struct.pack("<I", len(concrete_ranges))
    for address, r in sorted(concrete_ranges):
        data = bytes.fromhex(r["data"])
        out += struct.pack("<II", address, len(data))
        out += data
    page_hashes = fields.get("page_hashes", {})
    out += struct.pack("<I", len(page_hashes))
    for page in sorted(page_hashes):
        out += struct.pack("<I", page)
        out += bytes.fromhex(page_hashes[page])
    out += bytes([1 if fields.get("bank_model", False) else 0])
    out += struct.pack(
        "<iii",
        int(fields.get("bank_active_mask", 0)),
        int(fields.get("bank_pending_mask", -1)),
        int(fields.get("bank_requested_mask", -1)),
    )
    bank_alt = fields.get("bank_alt", {})
    for code in range(96):
        value = bank_alt.get(
            code, bank_alt.get(str(code), {"kind": 0, "value": 0, "mask": 0})
        )
        out += _pack_value32(value)
    out += struct.pack(
        "<BiiH",
        bool(fields.get("stack_model", False)),
        int(fields.get("pc_stack_pending", -1)),
        int(fields.get("pc_stack_requested", -1)),
        len(fields.get("pc_stack", ())),
    )
    for entry in fields.get("pc_stack", ()):
        out += struct.pack("<I", entry & 0xFFFFFFFF)
    out += bytes([int(fields.get("loop_depth", 0))])
    for address, counter in fields.get(
        "loop_slots", [[{"kind": 0, "value": 0, "mask": 0}] * 2 for _ in range(6)]
    ):
        out += _pack_value32(address) + _pack_value32(counter)
    return bytes(out)


def unpack_state(blob: bytes) -> dict:
    """The inverse of ``pack_state``."""
    if blob[:4] != _MAGIC:
        raise ValueError("not a sharc_diff state blob (bad magic %r)" % (blob[:4],))
    (version,) = struct.unpack_from("<I", blob, 4)
    if version not in (1, 2, 3, STATE_FORMAT_VERSION):
        raise ValueError(
            "sharc_diff state blob is format version %d, this module reads %d"
            % (version, STATE_FORMAT_VERSION)
        )
    off = 8
    (pc_sw,) = struct.unpack_from("<I", blob, off)
    off += 4
    stopped_present = blob[off]
    off += 1
    stopped = None
    if stopped_present:
        (length,) = struct.unpack_from("<H", blob, off)
        off += 2
        stopped = blob[off : off + length].decode("utf-8")
        off += length
    uregs = {}
    for code in range(UREG_COUNT):
        uregs[code], off = _unpack_value32(blob, off)
    special = {}
    for name in SPECIAL_SLOTS:
        special[name], off = _unpack_special(blob, off)
    (mmr_count,) = struct.unpack_from("<I", blob, off)
    off += 4
    mmrs = {}
    for _ in range(mmr_count):
        (address,) = struct.unpack_from("<I", blob, off)
        off += 4
        mmrs[address], off = _unpack_value32(blob, off)
    pending_present = blob[off]
    off += 1
    pending = None
    if pending_present:
        target_present = blob[off]
        off += 1
        (target,) = struct.unpack_from("<I", blob, off)
        off += 4
        call = bool(blob[off])
        off += 1
        slots = blob[off]
        off += 1
        return_from_call = bool(blob[off])
        off += 1
        return_sw_present = blob[off]
        off += 1
        (return_sw,) = struct.unpack_from("<i", blob, off)
        off += 4
        pending = {
            "target": target if target_present else None,
            "call": call,
            "slots": slots,
            "return_from_call": return_from_call,
            "return_sw": return_sw if return_sw_present else None,
        }
    (loop_count,) = struct.unpack_from("<H", blob, off)
    off += 2
    loops = []
    for _ in range(loop_count):
        start_sw, end_sw, remaining, mode = struct.unpack_from("<IIII", blob, off)
        off += 16
        loops.append(
            {
                "start_sw": start_sw,
                "end_sw": end_sw,
                "remaining": remaining,
                "mode": mode,
            }
        )
    (call_stack_count,) = struct.unpack_from("<H", blob, off)
    off += 2
    call_stack = []
    for _ in range(call_stack_count):
        (value,) = struct.unpack_from("<I", blob, off)
        off += 4
        call_stack.append(value)
    (status_stack_count,) = struct.unpack_from("<H", blob, off)
    off += 2
    status_stack = []
    for _ in range(status_stack_count):
        triple = []
        for _ in range(3):
            v, off = _unpack_value32(blob, off)
            triple.append(v)
        status_stack.append(triple)
    (range_count,) = struct.unpack_from("<I", blob, off)
    off += 4
    memory_ranges = {}
    for _ in range(range_count):
        address, length = struct.unpack_from("<II", blob, off)
        off += 8
        data = blob[off : off + length]
        off += length
        memory_ranges[address] = {"length": length, "data": data.hex()}
    (page_hash_count,) = struct.unpack_from("<I", blob, off)
    off += 4
    page_hashes = {}
    for _ in range(page_hash_count):
        (page,) = struct.unpack_from("<I", blob, off)
        off += 4
        page_hashes[page] = blob[off : off + 32].hex()
        off += 32
    bank_model = False
    bank_active_mask = 0
    bank_pending_mask = -1
    bank_requested_mask = -1
    bank_alt = {code: {"kind": 0, "value": 0, "mask": 0} for code in range(96)}
    if version >= 2:
        bank_model = bool(blob[off])
        off += 1
        bank_active_mask, bank_pending_mask, bank_requested_mask = struct.unpack_from(
            "<iii", blob, off
        )
        off += 12
        for code in range(96):
            bank_alt[code], off = _unpack_value32(blob, off)
    stack_model = False
    pc_stack_pending = pc_stack_requested = -1
    pc_stack = []
    if version >= 3:
        stack_model, pc_stack_pending, pc_stack_requested, depth = struct.unpack_from(
            "<BiiH", blob, off
        )
        off += 11
        for _ in range(depth):
            (entry,) = struct.unpack_from("<I", blob, off)
            pc_stack.append(entry)
            off += 4
    loop_depth = 0
    loop_slots = [[{"kind": 0, "value": 0, "mask": 0}] * 2 for _ in range(6)]
    if version >= 4:
        loop_depth = blob[off]
        off += 1
        loop_slots = []
        for _ in range(6):
            address, off = _unpack_value32(blob, off)
            counter, off = _unpack_value32(blob, off)
            loop_slots.append([address, counter])
    return {
        "format_version": version,
        "pc_sw": pc_sw,
        "stopped": stopped,
        "uregs": uregs,
        "special": special,
        "mmrs": mmrs,
        "pending": pending,
        "loops": loops,
        "call_stack": call_stack,
        "stack_model": bool(stack_model),
        "pc_stack": pc_stack,
        "loop_depth": loop_depth,
        "loop_slots": loop_slots,
        "pc_stack_pending": pc_stack_pending,
        "pc_stack_requested": pc_stack_requested,
        "status_stack": status_stack,
        "memory_ranges": memory_ranges,
        "page_hashes": page_hashes,
        "bank_model": bank_model,
        "bank_active_mask": bank_active_mask,
        "bank_pending_mask": bank_pending_mask,
        "bank_requested_mask": bank_requested_mask,
        "bank_alt": bank_alt,
    }


# ---------------------------------------------------------------------------
# 2. Engine protocol and adapters
# ---------------------------------------------------------------------------


class Engine(Protocol):
    """One steppable SHARC+ core, Python or native, driven identically by
    the lockstep comparator below."""

    def load_state(self, fields: Mapping[str, Any]) -> None:
        """Reset this engine and seed it from an ``export_state()`` dict."""
        ...

    def step(self, n: int = 1) -> None:
        """Execute up to N instructions, or fewer if this engine halts
        first (a halt is not an error: ``export_state()`` and ``halted``
        still work afterwards; a caller comparing across a halt should
        check ``halted``/``halt_reason``)."""
        ...

    def export_state(self) -> dict:
        """This engine's current state, in the canonical structured form
        (see the module docstring)."""
        ...

    @property
    def halted(self) -> bool: ...

    @property
    def halt_reason(self) -> str | None: ...


class PythonEngine:
    """Engine adapter over ``sharc_run.Runner`` -- the reference
    implementation every other engine is checked against."""

    def __init__(self, data: LoadedMemory | None = None) -> None:
        self.data = data
        self._runner: sr.Runner | None = None
        self._halt: sr.Halt | None = None

    def load_state(self, fields: Mapping[str, Any]) -> None:
        assert self.data is not None, (
            "PythonEngine(data=None) can only run synthetic, memory-free "
            "instructions directly (see generate_compute_corpus); "
            "load_state()/step() need a real LoadedMemory to decode from"
        )
        state = import_state(fields, data=self.data)
        runner = sr.Runner.__new__(sr.Runner)
        runner.data = self.data
        runner.state = state
        runner._watch = None
        runner.diagnose_unknown = False
        runner._last_writer = {}
        runner._diagnose_codes = ()
        runner.breakpoints = frozenset()
        runner.instructions = 0
        runner.form_counts = __import__("collections").Counter()
        runner.max_call_depth_reached = 0
        runner._cache = {}
        self._runner = runner
        self._halt = None

    def load_runner(self, runner: sr.Runner) -> None:
        """Adopt an already-running Runner directly (no export/import round
        trip) -- used by ``run_frame_lockstep``, which seeds both engines
        from ``sharc_harness.new_runner(..., init=...)`` copies rather than
        from a from-scratch ``import_state``."""
        self.data = runner.data
        self._runner = runner
        self._halt = None

    def step(self, n: int = 1) -> None:
        assert self._runner is not None, "load_state()/load_runner() first"
        if self._halt is not None:
            return
        for _ in range(n):
            try:
                self._runner.step()
            except sr.Halt as exc:
                self._halt = exc
                return

    def export_state(self, **kwargs: Any) -> dict:
        assert self._runner is not None, "load_state()/load_runner() first"
        return export_state(self._runner.state, **kwargs)

    @property
    def halted(self) -> bool:
        return self._halt is not None

    @property
    def halt_reason(self) -> str | None:
        return None if self._halt is None else self._halt.reason

    @property
    def runner(self) -> sr.Runner:
        assert self._runner is not None
        return self._runner


# The native core's C ABI contract (scratchpad/rt-native-core-design.md
# section 3a), as far as this harness needs it: state import/export using
# exactly ``pack_state``/``unpack_state``'s byte layout, and single-
# instruction stepping (not ``call(pc, ...)`` -- the design doc's block-
# call entry point is for the live/real-time path; a differential test
# wants single-instruction granularity). A native implementation exposes:
#
#   void*  sharc_native_create(const uint8_t* image, size_t image_len);
#   void   sharc_native_destroy(void* handle);
#   int32_t sharc_native_import_state(void* handle, const uint8_t* blob, size_t blob_len);
#       -- 0 on success, negative on a malformed/unsupported blob.
#   int32_t sharc_native_export_state(void* handle, uint8_t* out, size_t out_cap);
#       -- number of bytes written, or the required capacity negated if
#          out_cap was too small (caller reallocates and retries).
#   int32_t sharc_native_step(void* handle, uint32_t n);
#       -- number of instructions actually executed (<= n; fewer means a
#          halt); a negative return is a hard error (not a halt).
#   int32_t sharc_native_halt_reason(void* handle, char* out, size_t out_cap);
#       -- UTF-8 halt reason length written (truncated to out_cap), or 0 if
#          not halted.
#
# STATE_FORMAT_VERSION (the first four bytes after the magic in every
# blob) is the version negotiated between the two sides; a native core
# built against a different version must refuse import_state rather than
# silently misinterpret the layout.
_NATIVE_FUNCTIONS = (
    ("sharc_native_create", [ctypes.c_char_p, ctypes.c_size_t], ctypes.c_void_p),
    ("sharc_native_destroy", [ctypes.c_void_p], None),
    (
        "sharc_native_import_state",
        [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t],
        ctypes.c_int32,
    ),
    (
        "sharc_native_export_state",
        [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t],
        ctypes.c_int32,
    ),
    ("sharc_native_step", [ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int32),
    (
        "sharc_native_halt_reason",
        [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t],
        ctypes.c_int32,
    ),
)


class NativeEngine:
    """ctypes adapter for the future ``packages/emulator/sharc/`` cdylib.

    Raises ``FileNotFoundError`` (no library at LIBRARY_PATH) or
    ``OSError``/``AttributeError`` (present but missing an expected export)
    at construction time -- there is nothing to load yet in this tree, and
    a caller (the self-test, a future CI check) should treat either as "no
    native engine available", not crash the whole harness.
    """

    def __init__(self, library_path: str, image: bytes) -> None:
        if not os.path.isfile(library_path):
            raise FileNotFoundError(
                "no native SHARC+ core at %r (packages/emulator/sharc/ has not been "
                "built yet, or LIBRARY_PATH is wrong)" % library_path
            )
        self._lib = ctypes.CDLL(library_path)
        for name, argtypes, restype in _NATIVE_FUNCTIONS:
            try:
                fn = getattr(self._lib, name)
            except AttributeError as exc:
                raise AttributeError(
                    "%r is missing expected export %r -- see this module's "
                    "NativeEngine docstring for the full contract"
                    % (library_path, name)
                ) from exc
            fn.argtypes = argtypes
            fn.restype = restype
        self._image = image
        self._handle = self._lib.sharc_native_create(image, len(image))
        if not self._handle:
            raise OSError("sharc_native_create failed for %r" % library_path)
        self._halted_reason: str | None = None

    def __del__(self) -> None:
        lib = getattr(self, "_lib", None)
        handle = getattr(self, "_handle", None)
        if lib is not None and handle:
            lib.sharc_native_destroy(handle)

    def load_state(self, fields: Mapping[str, Any]) -> None:
        blob = pack_state(fields)
        rc = self._lib.sharc_native_import_state(self._handle, blob, len(blob))
        if rc != 0:
            raise ValueError("sharc_native_import_state failed (rc=%d)" % rc)
        self._halted_reason = None

    def step(self, n: int = 1) -> None:
        rc = self._lib.sharc_native_step(self._handle, n)
        if rc < 0:
            raise RuntimeError("sharc_native_step hard error (rc=%d)" % rc)
        if rc < n:
            buf = ctypes.create_string_buffer(256)
            length = self._lib.sharc_native_halt_reason(self._handle, buf, 256)
            self._halted_reason = (
                buf.raw[:length].decode("utf-8", "replace") if length else "halted"
            )

    def export_state(self, **_kwargs: Any) -> dict:
        cap = 1 << 20
        buf = ctypes.create_string_buffer(cap)
        n = self._lib.sharc_native_export_state(self._handle, buf, cap)
        if n < 0:
            raise ValueError(
                "sharc_native_export_state: buffer too small (need %d)" % -n
            )
        return unpack_state(buf.raw[:n])

    @property
    def halted(self) -> bool:
        return self._halted_reason is not None

    @property
    def halt_reason(self) -> str | None:
        return self._halted_reason


# ---------------------------------------------------------------------------
# 3. Lockstep comparison
# ---------------------------------------------------------------------------


def _value_repr(d: Mapping[str, int]) -> str:
    if d["kind"] == _VK_CONST or d["kind"] == _SK_CONST:
        return "%#x" % d["value"]
    if d["kind"] == _VK_PARTIAL:
        return "partial(known=%#x,bits=%#x)" % (d["mask"], d["value"])
    if d["kind"] == _SK_MR:
        return "mr(%#x/%#x)" % (d["value"], d["mask"])
    return "unknown"


def _symbolic_pc(pc_sw: int, img=None) -> str:
    label = "%#x" % pc_sw
    if img is None:
        return label
    try:
        fn = img.func(pc_sw)
    except Exception:
        fn = None
    if fn:
        label += " (%s+%#x)" % (
            fn.get("name") or fn["entry_sw"],
            pc_sw - int(fn["entry_sw"], 16),
        )
    try:
        rows = img.sql(
            "SELECT mnemonic FROM insn WHERE image=? AND sw=? LIMIT 1", img.name, pc_sw
        )
    except Exception:
        rows = []
    if rows:
        label += ": " + rows[0][0]
    return label


def compare_states(
    a: Mapping[str, Any], b: Mapping[str, Any], *, img=None
) -> list[str]:
    """Human-readable diff lines between two ``export_state()`` dicts,
    empty if they agree on everything this format covers. Cheapest checks
    first (pc, halt reason) so a caller can bail before the O(128) register
    walk."""
    lines: list[str] = []
    if a["pc_sw"] != b["pc_sw"]:
        lines.append(
            "pc_sw: %s != %s"
            % (_symbolic_pc(a["pc_sw"], img), _symbolic_pc(b["pc_sw"], img))
        )
    if a.get("stopped") != b.get("stopped"):
        lines.append("stopped: %r != %r" % (a.get("stopped"), b.get("stopped")))
    for name, default in (
        ("bank_model", False),
        ("bank_active_mask", 0),
        ("bank_pending_mask", -1),
        ("bank_requested_mask", -1),
        ("stack_model", False),
        ("pc_stack_pending", -1),
        ("pc_stack_requested", -1),
        ("loop_depth", 0),
    ):
        va, vb = a.get(name, default), b.get(name, default)
        if va != vb:
            lines.append("%s: %r != %r" % (name, va, vb))
    if a.get("pc_stack", []) != b.get("pc_stack", []):
        lines.append("pc_stack: %r != %r" % (a.get("pc_stack"), b.get("pc_stack")))
    unknown = {"kind": 0, "value": 0, "mask": 0}
    if a.get("loop_slots", [[unknown] * 2 for _ in range(6)]) != b.get(
        "loop_slots", [[unknown] * 2 for _ in range(6)]
    ):
        lines.append("loop_slots: physical resource contents differ")
    aa, ba = a.get("bank_alt", {}), b.get("bank_alt", {})
    for code in range(96):
        va = aa.get(code, aa.get(str(code), unknown))
        vb = ba.get(code, ba.get(str(code), unknown))
        if va != vb:
            lines.append(
                "bank_alt[%s]: %s != %s"
                % (st.UREG_NAMES[code], _value_repr(va), _value_repr(vb))
            )
    for code in range(UREG_COUNT):
        va = a["uregs"][code] if code in a["uregs"] else a["uregs"][str(code)]
        vb = b["uregs"][code] if code in b["uregs"] else b["uregs"][str(code)]
        if va != vb:
            lines.append(
                "%s: %s != %s" % (st.UREG_NAMES[code], _value_repr(va), _value_repr(vb))
            )
    for name in SPECIAL_SLOTS:
        va, vb = a["special"][name], b["special"][name]
        if va != vb:
            lines.append(
                "special[%s]: %s != %s" % (name, _value_repr(va), _value_repr(vb))
            )
    mmr_addrs = {int(x) for x in a["mmrs"]} | {int(x) for x in b["mmrs"]}
    for address in sorted(mmr_addrs):
        va = a["mmrs"].get(address, a["mmrs"].get(str(address)))
        vb = b["mmrs"].get(address, b["mmrs"].get(str(address)))
        if va != vb:
            lines.append(
                "mmr[%#x]: %s != %s"
                % (
                    address,
                    _value_repr(va) if va else "absent",
                    _value_repr(vb) if vb else "absent",
                )
            )
    if a.get("pending") != b.get("pending"):
        lines.append("pending: %r != %r" % (a.get("pending"), b.get("pending")))
    if a.get("loops") != b.get("loops"):
        lines.append("loops: %r != %r" % (a.get("loops"), b.get("loops")))
    if a.get("call_stack") != b.get("call_stack"):
        lines.append(
            "call_stack: %r != %r" % (a.get("call_stack"), b.get("call_stack"))
        )
    if a.get("status_stack") != b.get("status_stack"):
        lines.append(
            "status_stack differs (%d vs %d entries)"
            % (len(a.get("status_stack", ())), len(b.get("status_stack", ())))
        )
    ah, bh = a.get("page_hashes", {}), b.get("page_hashes", {})
    for page in sorted(set(ah) | set(bh)):
        pa, pb = (
            ah.get(page) if page in ah else ah.get(str(page)),
            bh.get(page) if page in bh else bh.get(str(page)),
        )
        if pa != pb:
            lines.append("memory page %#x: hash %s != %s" % (page, pa, pb))
    ar, br = a.get("memory_ranges", {}), b.get("memory_ranges", {})
    # A range with no concrete data (every byte Unknown/unmapped) round-
    # trips through pack_state/unpack_state as an absent entry (the wire
    # format only carries concrete ranges -- see pack_state's
    # concrete_ranges filter), so "present with data=None" and "absent"
    # must compare equal here, or every lockstep against a re-imported
    # blob would spuriously report a difference that was never real.
    for address in sorted(set(ar) | set(br)):
        ra_data = ar[address]["data"] if address in ar else None
        rb_data = br[address]["data"] if address in br else None
        if ra_data != rb_data:
            lines.append("memory[%s]: %r != %r" % (address, ra_data, rb_data))
    return lines


@dataclass
class LockstepResult:
    diverged: bool
    steps_agreed: int
    diff: list[str]
    pc_sw: int | None


def run_lockstep(
    engine_a: Engine,
    engine_b: Engine,
    *,
    max_steps: int,
    compare_every: int = 1,
    img=None,
) -> LockstepResult:
    """Step ENGINE_A and ENGINE_B in tandem, comparing their exported state
    every COMPARE_EVERY instructions (1 = every instruction; a larger value
    is the "block mode" the design doc asks for -- the same lockstep loop,
    just a coarser comparison interval, which still catches a divergence
    inside a block, only later and with a less precise pc). Stops at the
    first state that disagrees, or when either engine halts, or after
    MAX_STEPS total instructions.
    """
    steps_agreed = 0
    while steps_agreed < max_steps:
        chunk = min(compare_every, max_steps - steps_agreed)
        engine_a.step(chunk)
        engine_b.step(chunk)
        steps_agreed += chunk
        state_a, state_b = engine_a.export_state(), engine_b.export_state()
        diff = compare_states(state_a, state_b, img=img)
        if diff:
            return LockstepResult(True, steps_agreed, diff, state_a["pc_sw"])
        if engine_a.halted or engine_b.halted:
            if engine_a.halt_reason != engine_b.halt_reason:
                return LockstepResult(
                    True,
                    steps_agreed,
                    [
                        "halt_reason: %r != %r"
                        % (engine_a.halt_reason, engine_b.halt_reason)
                    ],
                    state_a["pc_sw"],
                )
            return LockstepResult(False, steps_agreed, [], state_a["pc_sw"])
    return LockstepResult(False, steps_agreed, [], None)


def run_frame_lockstep(
    runner_a: sr.Runner,
    runner_b: sr.Runner,
    image: str,
    frames: Sequence[Any],
    *,
    img=None,
) -> LockstepResult:
    """Per-frame lockstep: deliver each of FRAMES (``.tx`` DSPI2 bytes, as
    ``tools.sharc_dev.sharc_capture``'s frame records have) to both runners with
    exactly the primitives ``sharc_replay.replay()``'s own frame loop uses
    (``sharc_harness.write_dma_transfer``/``drive_dma_completion``/
    ``call_frame_collect_all``), then compares full exported state.  Stops
    at the first frame that disagrees; RUNNER_A/RUNNER_B are left
    positioned at that frame's post-call state either way (a caller
    inspecting the halt should read ``runner_a.state``/``runner_b.state``
    directly, this function does not wrap them in an Engine).
    """
    for idx, frame in enumerate(frames):
        h.write_dma_transfer(runner_a.state, image, frame.tx)
        runner_a = h.drive_dma_completion(runner_a, image)
        runner_a, _ = h.call_frame_collect_all(runner_a, image)

        h.write_dma_transfer(runner_b.state, image, frame.tx)
        runner_b = h.drive_dma_completion(runner_b, image)
        runner_b, _ = h.call_frame_collect_all(runner_b, image)

        state_a, state_b = export_state(runner_a.state), export_state(runner_b.state)
        diff = compare_states(state_a, state_b, img=img)
        if diff:
            return LockstepResult(
                True, idx, ["frame %d:" % idx] + diff, state_a["pc_sw"]
            )
    return LockstepResult(False, len(frames), [], None)


# ---------------------------------------------------------------------------
# 4. Self-test
# ---------------------------------------------------------------------------


def self_test_agree(
    image: str, *, start: int = 0x1C4ECF, steps: int = 2000
) -> LockstepResult:
    """Two independent PythonEngine instances, both starting a fresh
    ``sharc_run.Runner`` at START with default reset regs (the same
    ``sharc_run.main()`` CLI would use), must agree for STEPS instructions.
    This is the harness's own closed-loop check: export -> import -> step
    -> export must be lossless, or this diverges immediately at step 0."""
    data = sr._load_image_memory(image)
    engine_a, engine_b = PythonEngine(data), PythonEngine(data)
    seed_state = sr.make_state(data, start)
    fields = export_state(seed_state, page_hash=True)
    engine_a.load_state(fields)
    engine_b.load_state(fields)
    return run_lockstep(engine_a, engine_b, max_steps=steps)


def self_test_mutation(
    image: str, *, start: int = 0x1C4ECF, steps: int = 2000, mutate_after: int = 50
) -> LockstepResult:
    """Like ``self_test_agree``, but ENGINE_B's state is deliberately
    mutated (one ASTATX bit flipped) after MUTATE_AFTER instructions -- the
    harness must catch this with a clear diff, not silently agree."""
    data = sr._load_image_memory(image)
    engine_a, engine_b = PythonEngine(data), PythonEngine(data)
    seed_state = sr.make_state(data, start)
    fields = export_state(seed_state, page_hash=True)
    engine_a.load_state(fields)
    engine_b.load_state(fields)
    engine_a.step(mutate_after)
    engine_b.step(mutate_after)
    astatx_code = st.UREG_CODES["ASTATX"]
    current = st._ureg_raw(engine_b.runner.state.uregs, astatx_code)
    current_value = current.value if isinstance(current, st.Const) else 0
    engine_b.runner.state.uregs[astatx_code] = st.Const(current_value ^ 1)
    result = run_lockstep(engine_a, engine_b, max_steps=steps - mutate_after)
    result.steps_agreed += mutate_after
    return result


# ---------------------------------------------------------------------------
# 5. Random-operand compute corpus
# ---------------------------------------------------------------------------


def _full_compute_fields(
    cu: int, opcode: int, rn: int, rx: int, ry: int
) -> dict[str, int]:
    """Type2a/2a_short/2b field encoding (decode_table.json: "cond[4:0]",
    "compute[22:16]", "compute[15:0]") for a single-function (mf=0) compute
    at compute unit CU, opcode OPCODE, PRM Table 18-1's rn/rx/ry layout."""
    field = (
        ((cu & 3) << 20)
        | ((opcode & 0xFF) << 12)
        | ((rn & 0xF) << 8)
        | ((rx & 0xF) << 4)
        | (ry & 0xF)
    )
    return {
        "cond[4:0]": 0x1F,
        "compute[22:16]": (field >> 16) & 0x7F,
        "compute[15:0]": field & 0xFFFF,
    }


def _short_compute_fields(opcode: int, rn: int, rx: int) -> dict[str, int]:
    """Type2c's "compute[11:0]" field (short compute; _compute's short=True
    path: opcode/rn/rx packed into the low 12 bits)."""
    field = ((opcode & 0xF) << 8) | ((rn & 0xF) << 4) | (rx & 0xF)
    return {"compute[11:0]": field}


def _random_state(rng: random.Random, *, seed_special: bool) -> st.State:
    uregs: dict[int, st.Value] = {
        code: st.Const(rng.getrandbits(32)) for code in range(UREG_COUNT)
    }
    special: dict[str, Operand | st.MR] = {}
    if seed_special:
        for name in ("MRF", "MRB", "MSF", "MSB"):
            if rng.random() < 0.5:
                special[name] = st.MR((1 << 80) - 1, rng.getrandbits(80))
        for name in ("BFFWRP",):
            if rng.random() < 0.5:
                special[name] = st.Const(rng.getrandbits(32))
    return st.State(
        pc_sw=0,
        uregs=uregs,
        special=special,
        mmrs={},
        concrete=None,
        record_events=False,
        explicit_memory_model=True,
        approx_recips=True,
        assume_nw32=True,
    )


@dataclass
class ComputeCase:
    op_table: str
    opcode: int
    form: str
    fields: dict[str, int]
    state_before: dict
    state_after: dict | None
    error: str | None


def _run_one_compute_case(
    op_table: str, opcode: int, form: str, fields: dict[str, int], rng: random.Random
) -> ComputeCase:
    length_bytes = {"2a": 6, "2a_short": 4, "2c": 2}[form]
    state = _random_state(rng, seed_special=True)
    insn = st.Instruction(
        offset=0,
        length_bytes=length_bytes,
        type_name=form,
        fields=fields,
        kind="confident",
    )
    before = export_state(state, page_hash=False)
    try:
        out = st._execute(state, insn)
    except Exception as exc:  # noqa: BLE001 -- recorded, not swallowed
        return ComputeCase(
            op_table,
            opcode,
            form,
            fields,
            before,
            None,
            "%s: %s" % (type(exc).__name__, exc),
        )
    if len(out) != 1:
        return ComputeCase(
            op_table,
            opcode,
            form,
            fields,
            before,
            None,
            "forked into %d successors (cond=0x1F should never fork)" % len(out),
        )
    after = export_state(out[0], page_hash=False)
    return ComputeCase(op_table, opcode, form, fields, before, after, None)


def generate_compute_corpus(
    out_dir: str, *, seed: int = 0, cases_per_op: int = 8
) -> dict:
    """A random-operand corpus over sharc_core's full-compute op tables
    (ALU_OPS/MULT_OPS/SHIFT_OPS, form "2a") and short-compute table
    (SHORT_OPS, form "2c"). Each case is a fabricated (no firmware bytes:
    the fields are synthesized directly from the op's table key, not
    decoded from any image) (state_before, instruction, state_after) triple
    a native core can replay by importing state_before, decoding+executing
    the one instruction FIELDS describes, and comparing against
    state_after.

    Not covered (future extension, same shape): the multifunction table
    (MULTIFN_MUL_ALU_OPS, a different field layout -- see compute.py's
    docstring), the MRDATAMOVE/multiply-accumulate-into-MRF encodings
    (checked by raw field pattern ahead of the mf/cu split), and dual add/
    subtract (cu=0, opcode top nibble 0x7/0xF -- intercepted before ALU_OPS
    is even consulted, so it is not reachable through this generator's
    per-table iteration).

    Returns a summary dict; writes one JSON file per case under OUT_DIR
    plus a ``manifest.json`` listing them (small: a few thousand short
    JSON files, no firmware-derived bytes, safe under out/ like every
    other generated artifact -- see CLAUDE.md's out/ rule).
    """
    from tools.sharc_dev.sharc_core.compute_alu import ALU_OPS
    from tools.sharc_dev.sharc_core.compute_mult import MULT_OPS
    from tools.sharc_dev.sharc_core.compute_multi import SHORT_OPS
    from tools.sharc_dev.sharc_core.compute_shift import SHIFT_OPS

    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(seed)
    manifest = []
    errors = 0
    tables: list[tuple[str, int, Mapping[int, object]]] = [
        ("alu", 0, ALU_OPS),
        ("mult", 1, MULT_OPS),
        ("shift", 2, SHIFT_OPS),
    ]
    for table_name, cu, ops in tables:
        for opcode in sorted(ops):
            for i in range(cases_per_op):
                rn, rx, ry = rng.randrange(16), rng.randrange(16), rng.randrange(16)
                fields = _full_compute_fields(cu, opcode, rn, rx, ry)
                case = _run_one_compute_case(table_name, opcode, "2a", fields, rng)
                fname = "%s_%02x_%03d.json" % (table_name, opcode, i)
                with open(os.path.join(out_dir, fname), "w") as fh:
                    json.dump(case.__dict__, fh, indent=1, sort_keys=True)
                manifest.append(fname)
                if case.error is not None:
                    errors += 1
    for opcode in sorted(SHORT_OPS):
        for i in range(cases_per_op):
            rn, rx = rng.randrange(16), rng.randrange(16)
            fields = _short_compute_fields(opcode, rn, rx)
            case = _run_one_compute_case("short", opcode, "2c", fields, rng)
            fname = "short_%02x_%03d.json" % (opcode, i)
            with open(os.path.join(out_dir, fname), "w") as fh:
                json.dump(case.__dict__, fh, indent=1, sort_keys=True)
            manifest.append(fname)
            if case.error is not None:
                errors += 1
    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w") as fh:
        json.dump(
            {
                "seed": seed,
                "cases_per_op": cases_per_op,
                "op_counts": {
                    "alu": len(ALU_OPS),
                    "mult": len(MULT_OPS),
                    "shift": len(SHIFT_OPS),
                    "short": len(SHORT_OPS),
                },
                "cases": sorted(manifest),
            },
            fh,
            indent=1,
            sort_keys=True,
        )
    return {
        "cases": len(manifest),
        "errors": errors,
        "manifest_path": manifest_path,
        "out_dir": out_dir,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_self = sub.add_parser(
        "self-test", help="Python-vs-Python agreement + mutation-catch check"
    )
    p_self.add_argument("image")
    p_self.add_argument("--steps", type=int, default=2000)

    p_corpus = sub.add_parser(
        "corpus", help="generate the random-operand compute corpus"
    )
    p_corpus.add_argument("out_dir")
    p_corpus.add_argument("--seed", type=int, default=0)
    p_corpus.add_argument("--cases-per-op", type=int, default=8)

    args = parser.parse_args(argv)
    if args.cmd == "self-test":
        agree = self_test_agree(args.image, steps=args.steps)
        print(
            "agree: diverged=%s steps_agreed=%d" % (agree.diverged, agree.steps_agreed)
        )
        for line in agree.diff:
            print("  " + line)
        mutation = self_test_mutation(args.image, steps=args.steps)
        print(
            "mutation-catch: diverged=%s steps_agreed=%d"
            % (mutation.diverged, mutation.steps_agreed)
        )
        for line in mutation.diff:
            print("  " + line)
        return 0 if (not agree.diverged and mutation.diverged) else 1
    if args.cmd == "corpus":
        summary = generate_compute_corpus(
            args.out_dir, seed=args.seed, cases_per_op=args.cases_per_op
        )
        print(json.dumps(summary, indent=1, sort_keys=True))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
