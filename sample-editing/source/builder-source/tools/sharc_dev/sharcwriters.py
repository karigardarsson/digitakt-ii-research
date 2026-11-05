#!/usr/bin/env python3
"""Which SHARC+ instructions can write a given DM byte address.

    uv run python -m tools.sharc_dev.sharcwriters TARGET [--image dt2-1.16]
        [--width N] [--stack LO:HI] [--json OUT]
        [--max-steps N] [--max-states N] [--jobs N] [--min-depth N]

TARGET is a DM byte address (e.g. 0x252658). The tool:

1. **Census** -- decodes every aligned instruction in the image's code
   blocks (tools/sharc_dev/sharcinv.py CODE_BLOCKS, sharcflow.aligned/merge_fields)
   and, from decoded fields alone (no SLEIGH, no execution), finds every
   instruction that can store to DM: forms 15a/15b/3a/3b/3c/3d/4a/4b/4d/
   6a_mem/14a/14d store when their merged d==1 and g==0 (per
   tools/sharc_dev/sharcspec/decode_table.json's own field list for each form --
   Type3c's `d` is a real variable bit, not fixed; sharcinv.py's own
   MEM_FORMS omits Type3c even though it stores exactly like the others,
   so this module keeps its own form table rather than reusing that one);
   16a/16b always store, DM when g==0 (no d bit); dual-memory 1a/1b store
   on the DM side when dmd==1, independent of the PM side's own pmd bit.

2. **Resolve** -- groups the census by owning function (tools/sharc_dev/sharcinv.py's
   own function recovery, via tools/sharc_dev/sharcfn/'s load_context/
   build_inventory: return-delimited spans split at interior call
   targets), then runs tools/sharc_dev/sharc_trace.py's forward abstract interpreter
   once per function from its entry, with every I/M/B/L register seeded as
   its own named symbol (`@I3e` etc, so an unresolved address survives as
   e.g. `Affine(I6e - 12)` instead of collapsing to Unknown) and
   concrete_memory=True (so loads from loader-initialised data resolve to
   a Const). Every census store's resolved address is pulled from the
   matching 'store' event(s) in the returned states' traces at that PC.

3. **Classify** -- HIT / EXCLUDED-CONST / EXCLUDED-STACK / STACK-RELATIVE /
   LOADED-POINTER / ENTRY-RELATIVE / UNRESOLVED, from the resolved address,
   the store's width and the target (see classify_store_address). Every
   census DM store gets exactly one class; the class totals always sum to
   the census total (checked at the end of every run).

Stack bounds (Phase 1, docs/findings/06-sharc-engine-and-startup.md, "The
SHARC code is not one block" section and the reset-stack-setup evidence
this tool's own header records): DEFAULT_STACK_LO/HI. Pass --stack LO:HI
to override; omit bounds entirely (not supported via CLI, only
programmatically as stack_lo=None) to fall back every otherwise-excludable
stack-relative store to STACK-RELATIVE instead of EXCLUDED-STACK.

The interpreter-independent logic (the census form rules, the width
tables, and the address classifier) is pure and unit-tested in
tests/sharc/analysis/test_sharcwriters.py with synthetic decoded fields and synthetic
trace events -- no firmware required. Running the CLI needs the DT2 1.16
SHARC loader (out/sections/dt2-1.16/section_7_BLOB.bin), which is not
committed (see CLAUDE.md).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from hashlib import sha256
from typing import Any, cast

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc_trace as trace_mod  # noqa: E402
from tools.sharc_dev import (  # noqa: E402
    sharcfn,  # noqa: E402
    sharcinv,  # noqa: E402
)

REPO = os.path.dirname(os.path.dirname(HERE))

IMAGES = {
    "dt2-1.16": "out/sections/dt2-1.16/section_7_BLOB.bin",
    "dt2-1.15C": "out/sections/dt2-1.15C/section_7_BLOB.bin",
    "dn2-1.11": "out/sections/dn2-1.11/section_7_BLOB.bin",
}

# Phase 1 evidence -- the loader's own layout, not an invented bound. Block
# 39 (target 0x2826db7c, 5252 B) ends at target 0x2826f000; the next block
# (40) starts at 0x282c0000. Nothing in the loader -- no real block, no
# FILL block -- covers the 0x51000-byte span between them, so the image
# never asserts a value for it: it is available RAM, not the tail of any
# initialised structure. Unaliased (subtract SW_ALIAS_BASE=0x28000000),
# that span is DM [0x26f000, 0x2c0000), which is where the tool's default
# stack bounds come from.
#
# Corroborating, and tighter: the startup routine blk88@0x1c0f24 (base_sw
# 0x1c02a4) sets B7=0x26f000, I7=0x26f7f0 and L7=0x1fd (509 normal words
# = 0x7f4 bytes) at sw 0x1c0f64/0x1c0f67/0x1c0f6a, mirrors them into
# B6/I6/L6, and then sets MODE1.CBUFEN at sw 0x1c0f82
# (`MODE1 = set(MODE1, 0x1011800)`). No BIT CLR in the image touches
# CBUFEN and no immediate or memory load writes MODE1 itself. With CBUFEN
# set, post-modify accesses through I7/I6 wrap inside [B7, B7+L7*4) =
# [0x26f000, 0x26f7f4) (PRM p.6-25), and MODIFY wraps whenever L != 0
# regardless of CBUFEN (PRM p.6-7). The gap bound above is kept as the
# default because it needs no assumption about B7, which blk93 recomputes
# from I7 at runtime (sw 0x1c1463/0x1c1472). docs/findings/06-sharc-engine-
# and-startup.md records I6=0x26f7e0 in one traced run, inside both bounds.
DEFAULT_STACK_LO = 0x26F000
DEFAULT_STACK_HI = 0x2C0000

# --- census: which forms store to DM, from decoded fields alone -----------

# tools/sharc_dev/sharcspec/decode_table.json's own field list for "Type<form>"
# confirms a real d (direction) and g (space) bit for every one of these
# (cross-checked 2026-09-21): store when d==1, DM when g==0.
SIMPLE_STORE_FORMS = (
    "15a",
    "15b",
    "3a",
    "3b",
    "3c",
    "3d",
    "4a",
    "4b",
    "4d",
    "6a_mem",
    "14a",
    "14d",
)
# 16a/16b store an immediate unconditionally (no d bit); DM when g==0.
IMMEDIATE_STORE_FORMS = ("16a", "16b")
# Dual-memory: the DM side stores when dmd==1, independent of the PM side's
# own pmd bit; there is no g bit (space is fixed DM+PM by construction).
DUAL_STORE_FORMS = ("1a", "1b")

ALL_STORE_FORMS = SIMPLE_STORE_FORMS + IMMEDIATE_STORE_FORMS + DUAL_STORE_FORMS

# Strict tracing remains the default.  The named continuation policy is
# intentionally narrow: Type14d's decoder semantics are useful for reaching
# later stores, but its unconfirmed encoding is not evidence for an address
# claim.  Facts and cache requests carry this versioned name.
STRICT_TRACE_POLICY = "strict/v1"
TYPE14D_CONTINUATION_POLICY = "type14d-continuation/v1"
WRITER_TRACE_POLICIES = {
    STRICT_TRACE_POLICY: (),
    TYPE14D_CONTINUATION_POLICY: ("14d",),
}
# Individual handler changes can bump only their form's revision.  The index
# compares this metadata against each fact's executed/stopped form set.
TRACE_HANDLER_REVISIONS = {
    "default": "trace-handler/v1",
    # Type7d now carries opaque symbolic ACONV results through writer traces.
    # Facts that executed or stopped on it must be recomputed.
    "7d": "trace-handler/7d-v2",
    # facts that executed or stopped on Type7a must be recomputed.
    "7a": "trace-handler/7a-v2",
}


def handler_revision(form: str) -> str:
    return TRACE_HANDLER_REVISIONS.get(form, TRACE_HANDLER_REVISIONS["default"])


def provisional_forms_for_policy(policy: str) -> tuple[str, ...]:
    try:
        return WRITER_TRACE_POLICIES[policy]
    except KeyError as error:
        raise ValueError("unknown writer trace policy: %r" % (policy,)) from error


def store_space_and_direction(form: str, fields: dict) -> tuple[bool, bool]:
    """-> (is_store, is_dm_store) for one decoded, merged-field instruction,
    purely from its form name and fields -- no execution. (False, False)
    for a form this module does not treat as a memory store at all, or a
    store-capable form instruction that is actually a load."""
    if form in SIMPLE_STORE_FORMS:
        is_store = fields.get("d") == 1
        is_dm = fields.get("g", 0) == 0
        return is_store, is_store and is_dm
    if form in IMMEDIATE_STORE_FORMS:
        is_dm = fields.get("g", 0) == 0
        return True, is_dm
    if form in DUAL_STORE_FORMS:
        is_store = fields.get("dmd") == 1
        return is_store, is_store
    return False, False


def census_instructions(sw_insns) -> list[dict]:
    """sw_insns: [(sw, Instruction)], e.g. from tools/sharc_dev/sharcinv.py's
    instructions_in(). -> a row per store-capable instruction (any space),
    with merged fields, statically-derived width and an is_dm flag. Pure
    decode-only function: no firmware access beyond what the caller already
    decoded, so it is directly unit-testable with synthetic Instructions."""
    rows = []
    for sw, insn in sw_insns:
        form = insn.type_name
        if form not in ALL_STORE_FORMS:
            continue
        fields = sharcinv.merge_fields(insn.fields)
        is_store, is_dm = store_space_and_direction(form, fields)
        if not is_store:
            continue
        rows.append(
            {
                "pc": sw,
                "form": form,
                "fields": fields,
                "width": static_store_width(form, fields),
                "is_dm": is_dm,
            }
        )
    return rows


# --- width -----------------------------------------------------------------

ACCESS_WIDTH_BYTES = {
    "byte": 1,
    "byte-sign-extended": 1,
    "short-word": 2,
    "short-word-sign-extended": 2,
    "normal-word": 4,
    "long-word": 8,
}

# 3d mirrors 3b's own (l, x, w) -> access-width table (same field names,
# plus 3d's own 'ex' exclusive-access bit, which does not affect width);
# 4d mirrors 4b's. Neither form is implemented in tools/sharc_dev/sharc_trace.py
# (see the module docstring there), so this is the decode_table's field
# shape carried over from the nearest documented sibling, not a traced
# fact -- flagged in the docstring above and in docs/TOOLS.md.
_LXW_LIKE_3B = {
    (0, 1, 1): 4,
    (0, 0, 0): 1,
    (0, 1, 0): 1,
    (1, 0, 0): 2,
    (1, 1, 0): 2,
    (1, 1, 1): 8,
}
_LXW_LIKE_4B = {(1, 1, 1): 4, (0, 0, 0): 1, (1, 0, 0): 2, (0, 1, 0): 1, (1, 1, 0): 2}


def _lxw_key(fields: dict[str, Any]) -> tuple[int, int, int] | None:
    values = (fields.get("l"), fields.get("x"), fields.get("w"))
    if all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return cast(tuple[int, int, int], values)
    return None


def static_store_width(form: str, fields: dict):
    """-> byte width of one store from its decoded fields alone, or None
    for a field combination tools/sharc_dev/sharc_trace.py itself refuses (e.g. an
    undocumented l/x/w combination) -- the caller then falls back to
    --width."""
    if form in ("14a", "15a", "15b"):
        return 8 if fields.get("l") else 4
    if form in ("3a", "4a", "6a_mem", "3c", "16a", "16b", "1a", "1b"):
        return 4
    if form == "14d":
        return 2 if fields.get("l") else 1
    if form in ("3b", "3d"):
        key = _lxw_key(fields)
        return _LXW_LIKE_3B.get(key) if key is not None else None
    if form in ("4b", "4d"):
        key = _lxw_key(fields)
        return _LXW_LIKE_4B.get(key) if key is not None else None
    return None


def event_store_width(form: str, event: dict):
    """-> byte width from a resolved tools/sharc_dev/sharc_trace.py 'store' event
    (ground truth of what actually executed), or None if the event does
    not carry enough to tell -- the caller then falls back to the static,
    field-derived width."""
    access_width = event.get("access_width")
    if access_width is not None:
        return ACCESS_WIDTH_BYTES.get(access_width)
    if form == "15b":
        return 8 if event.get("long_word") else 4
    if form in ("3a", "4a", "6a_mem", "3c", "16a", "16b", "14a"):
        return 4
    return None


# --- resolve: seeding and event selection -----------------------------------

# Every I/M/B register gets its own entry-time symbol, e.g. seeding I6
# with "@I6e" so an unresolved address survives as Affine(I6e - 12, ...)
# instead of collapsing to Unknown. R registers are deliberately left
# unseeded (Unknown until something computes them).
#
# L registers are the one deliberate deviation from "seed every I/M/B/L
# register symbolically": tools/sharc_dev/sharc_trace.py's Type19a_scaled MODIFY
# handler (its own circular-buffering model) only takes the cheap linear
# path when the destination's L register is a *Const* zero; a merely
# symbolic L (this tool's own @L*e, which could stand for any value
# including nonzero) makes it refuse and return Unknown("scaled circular
# modify I%d"), which then poisons every later use of that I register --
# observed directly: on a full run this was the single largest UNRESOLVED
# reason (the poisoned expression itself, e.g. "I7 + M7 * 4", with no
# "memory-address " prefix so it is not even recognized as
# LOADED-POINTER). L is 0 (circular buffering off) for every DAG register
# at reset (SHARC+ PRM, "Circular Buffering Mode") and this tool's own
# Phase 1 scan of every literal ureg load in the image (see
# DEFAULT_STACK_LO/HI's docstring) found exactly one register the image
# ever loads a nonzero L into: L7 (0x1fd, for the stack). Seeding every L
# with the real, evidence-backed reset default therefore recovers the
# same "Affine, not Unknown" outcome the symbolic I/M/B seeding is for,
# instead of silently defeating it.
ENTRY_SEED_NAMES = {
    "%s%de" % (group, n): "%s%d" % (group, n)
    for group in ("I", "M", "B")
    for n in range(16)
}
STACK_SYMBOLS = ("I6e", "I7e")

# Every EXCLUDED-STACK classification rests on one unproven, global
# assumption: that I6/I7 are within S (or, for a circ_ term, within S
# widened by CIRC_WRAP_SLACK) at the ENTRY of every function this tool
# traces -- since each function is seeded fresh from its own entry
# (resolve_function), not by propagating an actual runtime value from its
# caller. out/sharcwriters/stack-invariant.md's writer census closes most
# of this (CJUMP's implicit I6=I7, RFRAME, the M7-constant push/pop
# family, boot immediates) but leaves it open for I6/I7/B6/B7 flowing
# through the interrupt/context-switch machinery in blk69@0xb88200 and
# blk69@0xb8853a: B6/B7 have a verified PM(0x59)/PM(0x5a) save/restore
# round-trip (sw 0xb88353/0xb88356 -> sw 0xb885cf/0xb885d2, unchanged
# value), but I6/I7 chain through `I6 = DM(I7+2)` (sw 0xb8823b),
# `I7 = PM(I4+5)` (sw 0xb88255) and further I4-relative context-block
# slots whose own origin (I4 is a parameter from this function's caller,
# blk69@0xb88cb6) was not independently walked. Recorded explicitly, not
# hidden, per this module's own classify_store_address(): every
# EXCLUDED-STACK detail dict carries this string so a reader (or a
# stricter future run) can find and count every store that depends on it
# without re-deriving which classifications are affected.
ENTRY_SEED_ASSUMPTION = (
    "assumes I6/I7 (and, for a circ_ term, the register a circular MODIFY "
    "read from) are within S at this store's owning function's own "
    "entry; verified for CJUMP/RFRAME/the M7 push-pop family/boot, NOT "
    "independently verified for I6/I7 propagation through the "
    "interrupt/context-switch machinery in blk69@0xb88200/0xb8853a -- see "
    'out/sharcwriters/stack-invariant.md, "Item 2"'
)

# tools/sharc_dev/sharc_trace.py's Type19a_scaled handler mints a FRESH symbol (never
# reusing I6e/I7e themselves, so two different modify sites, or a modify
# chained onto an earlier modify's own result, are never asserted equal --
# see that module's CIRC_SYMBOL_PREFIX/_stack_bounded_symbol) for the
# result of a circular MODIFY whose input was already a stack-bounded
# symbol (I6e/I7e themselves, or an earlier such fresh symbol) offset by
# less than one buffer length. This module does not track the specific
# value such a symbol denotes -- only that PRM p.6-23's single
# +-byte_length wrap correction confines it to within one buffer length of
# wherever its input's own bound placed it. CIRC_WRAP_SLACK is that one
# buffer length in bytes: L7's proven-constant value (0x1fd, from the
# blk88 startup evidence and this module's own GLOBAL_CONSTANT_SEEDS)
# times the normal-word scale (4) -- the same L7/scale the tracer itself
# uses when it decides whether an input offset still qualifies. A
# circ_-tagged term therefore ranges over S widened by CIRC_WRAP_SLACK on
# both sides, not S itself -- see combined_affine_range().
CIRC_WRAP_SLACK = 0x1FD * 4  # 0x7f4 = 2036 bytes


def is_circ_symbol(name: str) -> bool:
    return name.startswith(trace_mod.CIRC_SYMBOL_PREFIX)


# Task 1 evidence (2026-09-21, DT2 1.16, image_sha256
# 0f514a12a2255f5c081e292c47f1f29462003177658da4bbae0a22fd737fffa2):
# a decode-only census (no execution) of every instruction in every
# tools/sharc_dev/sharcinv.py CODE_BLOCKS block that can write a ureg-space register
# -- 17a/17b immediates, 5a_move/5b_move ureg-to-ureg copies,
# 3a/3b/3d/14a/15a/15b memory loads with d==0 (the register is the
# destination), and 7d ACONV (B/I only) -- found every writer PC for every
# M0-15/B0-15/L0-15 register (I6/I7 checked too, as a sanity control; both
# fail below). Forms 3c/4a/4b/4d/14d/16a/16b/6a were checked against
# tools/sharc_dev/sharcspec/decode_table.json's own field list and excluded: they
# carry a 'dreg'/'data' field (R-register or no register operand at all),
# never 'ureg', so they cannot write M/B/L.
#
# A single startup routine, blk88's reset code (base_sw 0x1c02a4, entry
# 0x1c0f24, "has_no_static_caller"), sets every qualifying register by a
# 17a/17b immediate inside its own DO-loop (trip count 2, body
# [0x1c0f3a, 0x1c0f7c) -- both passes write the same literal, so the loop
# does not affect the value):
#   M15=0xffff(-1) @0x1c0f3a  M7=0xffff(-1) @0x1c0f3c  M14=1 @0x1c0f3e
#   M6=1 @0x1c0f40  M13=0 @0x1c0f42  M5=0 @0x1c0f44
#   L0,L1,L2,L3,L4,L5,L8..L15=0 @0x1c0f46-0x1c0f61
#   B7=0x26f000 @0x1c0f64  I7=0x26f7f0 @0x1c0f67  L7=0x1fd @0x1c0f6a
#   B6=B7 (5a_move) @0x1c0f6d  I6=I7 (5a_move) @0x1c0f73
#   L6=L7 (5a_move) @0x1c0f76
#
# Every other writer of M5/M6/M7/M13/M14/M15/L7 in the whole image is a
# single 15a "reg-indexed-load" in blk69 (sw 0xb885e7-0xb885ed for
# M7/M6/M5, and its mirror for M13-M15/L7), immediately preceded in the
# same function by a matching 15a store of the same register (blk69
# sw 0xb88200 area: "PM(0x48)=M5", "PM(0x49)=M6", "PM(0x4a)=M7", ... a
# textbook interrupt-entry register-bank save followed by an interrupt-
# exit restore from the same slots) -- a save/restore pair, not an
# independent writer, and since nothing else in the image ever changes
# these registers the restore can only ever put back the one constant
# established at startup. L6 is the same shape, one step removed: its only
# non-restore writer is the 0x1c0f76 move from L7, itself proven constant.
# A throwaway decode-only scanner (whole-image, per-register writer
# census; not committed -- see CLAUDE.md) built for this evidence pass is
# the source of the writer-PC lists above; the same query over
# sharcinv.CODE_BLOCKS/analyze_block reproduces them.
#
# L0-L2/L5/L8-L14 have the same shape (one blk88 startup immediate of 0,
# plus save/restore-paired loads in blk1/blk56/blk88/blk69 that never
# disagree with 0) and are already covered by seed_sets()'s existing
# unconditional L=0 default below, so they need no entry here.
#
# L3, L4 and L15 do NOT qualify despite the same blk88 startup zero:
# blk93 (the main program, reachable at runtime, not just startup) moves
# L3 and L4 from M4 (sw 0x1c508e/0x1c52a3 and 0x1c50c0/0x1c52d5) -- M4 is
# the single most-written register in the image (364 writer sites, dozens
# of distinct immediates), so L3/L4 do not stay 0 -- and loads L15 from
# two fixed DM addresses via Type14a (sw 0x1cc194, 0x1cc478) that are
# nowhere near any RAM region this tool knows about.
#
# B7 does NOT qualify despite its own clean blk88 startup immediate
# (0x26f000, the same value the module's DEFAULT_STACK_LO evidence above
# relies on for the *stack bounds*, a different and unrelated use):
# blk93 recomputes it from I7 via 7d ACONV at sw 0x1c1463/0x1c1472, so it
# does not hold 0x26f000 for the life of the program. Every other B
# register fails the same way (moves or ACONV writes in blk93 or blk56)
# or has multiple, unreconciled reg-indexed-load writers with no proof
# they agree. I6 and I7 -- the sanity check -- both fail hard: I6 has 60
# writer sites (it is the frame pointer), I7 has 20 beyond its own startup
# immediate, including ACONV and moves in blk93.
GLOBAL_CONSTANT_SEEDS = {
    "M5": 0,
    "M6": 1,
    "M7": 0xFFFFFFFF,
    "M13": 0,
    "M14": 1,
    "M15": 0xFFFFFFFF,
    "L6": 0x1FD,
    "L7": 0x1FD,
}


def seed_sets(seed_global_constants: bool = True) -> dict:
    """seed_global_constants=True (the default) additionally seeds the
    registers in GLOBAL_CONSTANT_SEEDS with their Task-1-evidenced Const
    value instead of a symbol (M5/M6/M7/M13/M14/M15) or the pragmatic L=0
    default below (L6/L7, whose real reset-time value is 0x1fd, not 0).
    Every other register -- including every L register GLOBAL_CONSTANT_
    SEEDS does not mention -- is unaffected either way: this option only
    ever narrows a symbol or corrects an already-concrete L default to a
    value proven equal to it everywhere in the image; it never makes a
    previously-symbolic register concrete for one this evidence pass did
    not clear (L3/L4/L15 keep today's L=0 default, not because it is
    proven, but because turning it off was not asked for and would
    reopen the scaled-MODIFY poisoning the module docstring describes)."""
    sets: dict[str, int | str] = {
        reg: "@" + symbol for symbol, reg in ENTRY_SEED_NAMES.items()
    }
    sets.update({"L%d" % n: 0 for n in range(16)})
    if seed_global_constants:
        sets.update(GLOBAL_CONSTANT_SEEDS)
    return sets


def choose_store_event(events: list):
    """Deterministic pick among possibly several resolved 'store' events at
    the same PC (predicate forks, a loop revisiting it, ...): prefer a
    fully-Const address (most informative), then Affine, then an
    unresolved-but-load-derived Unknown, then anything else; ties broken by
    a stable JSON ordering so the result never depends on state discovery
    order. A Type7a-uncertain path taints the selected event even if another
    path has a more concrete address. -> the chosen event dict, or None for
    an empty list."""
    if not events:
        return None

    def rank(event):
        addr = event.get("address")
        if isinstance(addr, bool):
            kind = 4
        elif isinstance(addr, int):
            kind = 0
        elif isinstance(addr, dict) and "affine" in addr:
            kind = 1
        elif (
            isinstance(addr, dict)
            and "unknown" in addr
            and str(addr["unknown"]).startswith("memory-address ")
        ):
            kind = 2
        else:
            kind = 3
        return (kind, json.dumps(addr, sort_keys=True))

    chosen = sorted(events, key=rank)[0]
    if any(event.get("conditional_type7a_uncertain") for event in events):
        return {**chosen, "conditional_type7a_uncertain": True}
    return chosen


# --- classify ----------------------------------------------------------------


def _signed32(value: int) -> int:
    return value if value < 0x80000000 else value - 0x100000000


def _affine_term_range(coefficient: int, lo: int, hi: int) -> tuple[int, int]:
    """-> (min, max) contribution of one affine term whose variable ranges
    over the closed-open interval [lo, hi), for a coefficient stored mod
    2**32 (tools/sharc_dev/sharc_trace.py's Affine convention)."""
    signed = _signed32(coefficient)
    a, b = signed * lo, signed * (hi - 1)
    return (a, b) if a <= b else (b, a)


def _affine_range_with(constant: int, terms, bound_for) -> tuple[int, int]:
    """-> (min, max) address the affine expression `constant + sum(coef *
    term)` can take when each named term in `terms` ranges over whatever
    closed-open interval `bound_for(name)` returns. Plain (unmasked) Python
    ints: the stack region this is used for sits far below 2**32, so
    wraparound cannot occur for the small coefficients real address
    arithmetic produces."""
    total_lo = total_hi = constant
    for name, coefficient in terms:
        lo, hi = bound_for(name)
        term_lo, term_hi = _affine_term_range(coefficient, lo, hi)
        total_lo += term_lo
        total_hi += term_hi
    # `constant` is already the Affine dataclass's own unsigned mod-2**32
    # representation (a small negative offset such as -12 is stored as
    # 0xFFFFFFF4), so the running sum must be reduced mod 2**32 too, or a
    # negative offset added to a small `lo`/`hi` would land billions too
    # high instead of wrapping back down to the intended small result.
    total_lo %= 0x100000000
    total_hi %= 0x100000000
    if total_lo > total_hi:
        # The interval straddles the 32-bit wraparound boundary -- not
        # expected for the small, realistic offsets real address arithmetic
        # produces. Widen rather than silently misorder it.
        return 0, 0xFFFFFFFF
    return total_lo, total_hi


def affine_range(constant: int, terms, lo: int, hi: int) -> tuple[int, int]:
    """-> (min, max) address the affine expression `constant + sum(coef *
    term)` can take when every named term in `terms` ranges over [lo, hi)."""
    return _affine_range_with(constant, terms, lambda _name: (lo, hi))


def combined_affine_range(
    constant: int, terms, stack_lo: int, stack_hi: int, circ_lo: int, circ_hi: int
) -> tuple[int, int]:
    """Like affine_range, but a term whose name is a
    tools/sharc_dev/sharc_trace.py CIRC_SYMBOL_PREFIX-tagged symbol (a fresh
    circular-MODIFY result -- see that module's _stack_bounded_symbol and
    its Type19a_scaled handler) ranges over [circ_lo, circ_hi) instead of
    [stack_lo, stack_hi). Every entry in `terms` must be one or the other
    -- the caller partitions by is_circ_symbol() before calling this."""

    def bound_for(name):
        return (circ_lo, circ_hi) if is_circ_symbol(name) else (stack_lo, stack_hi)

    return _affine_range_with(constant, terms, bound_for)


def ranges_overlap(
    range_lo: int, range_hi: int, width: int, target: int, target_width: int = 1
) -> bool:
    """True if a possible store overlaps requested [target,target+width)."""
    return range_lo < target + target_width and target < range_hi + width


def _format_affine(constant: int, terms) -> str:
    parts = []
    if constant:
        parts.append(hex(_signed32(constant)))
    for name, coefficient in terms:
        signed = _signed32(coefficient)
        parts.append(name if signed == 1 else "%d*%s" % (signed, name))
    return " + ".join(parts) if parts else "0x0"


def classify_store_address(
    addr, width, target: int, stack_lo, stack_hi, target_width: int = 1
):
    """Pure classifier. `addr` is the JSON-rendered resolved address exactly
    as tools/sharc_dev/sharc_trace.py's summarize()/_json_value() produce it: an int
    (Const), {'affine': {'constant': int, 'terms': [[name, coef], ...]}},
    {'unknown': reason}, {'partial': {'known_mask', 'known_bits'}}, or None
    when the store was never reached (or its width could not be
    determined). `width` is the store's own byte width; may be None.
    -> (class: str, detail: dict). Every input lands in exactly one of
    HIT / EXCLUDED-CONST / EXCLUDED-STACK / STACK-RELATIVE /
    LOADED-POINTER / ENTRY-RELATIVE / UNRESOLVED."""
    if addr is None:
        return "UNRESOLVED", {"reason": "store not reached by the tracer"}
    if width is None:
        return "UNRESOLVED", {"reason": "store width could not be determined"}
    if isinstance(addr, bool):
        return "UNRESOLVED", {
            "reason": "unrecognized address representation: %r" % (addr,)
        }
    if isinstance(addr, int):
        hit = addr < target + target_width and target < addr + width
        return ("HIT" if hit else "EXCLUDED-CONST"), {"address": addr}
    if not isinstance(addr, dict):
        return "UNRESOLVED", {
            "reason": "unrecognized address representation: %r" % (addr,)
        }

    if "unknown" in addr:
        reason = addr["unknown"]
        if isinstance(reason, str) and reason.startswith("memory-address "):
            return "LOADED-POINTER", {
                "load_expression": reason[len("memory-address ") :]
            }
        return "UNRESOLVED", {"reason": reason}

    if "partial" in addr:
        partial = addr["partial"]
        return "UNRESOLVED", {
            "reason": "address only partially known",
            "known_mask": partial.get("known_mask"),
            "known_bits": partial.get("known_bits"),
        }

    if "affine" in addr:
        affine = addr["affine"]
        constant = affine["constant"]
        terms = [tuple(term) for term in affine["terms"]]
        if not terms:
            hit = constant < target + target_width and target < constant + width
            return ("HIT" if hit else "EXCLUDED-CONST"), {"address": constant}

        def is_stack_term(name):
            return name in STACK_SYMBOLS or is_circ_symbol(name)

        stack_terms = [(name, coeff) for name, coeff in terms if is_stack_term(name)]
        other_terms = [
            (name, coeff) for name, coeff in terms if not is_stack_term(name)
        ]
        expression = _format_affine(constant, terms)

        if other_terms:
            # List every register the expression actually depends on, not
            # just the non-stack ones: a mixed term such as I7e + 8*M7e (the
            # ordinary DM(I7,M7) push/pop idiom) is still driven by the
            # frame/stack pointer, and reporting 'M7' alone there would
            # wrongly suggest it had nothing to do with the stack.
            register_names: set[str] = set()
            for name, _ in terms:
                display = ENTRY_SEED_NAMES.get(name)
                register_names.add(display if isinstance(display, str) else str(name))
            registers = sorted(register_names)
            return "ENTRY-RELATIVE", {"registers": registers, "expression": expression}

        if stack_lo is None or stack_hi is None:
            return "STACK-RELATIVE", {"expression": expression}

        circ_lo, circ_hi = stack_lo - CIRC_WRAP_SLACK, stack_hi + CIRC_WRAP_SLACK
        range_lo, range_hi = combined_affine_range(
            constant, stack_terms, stack_lo, stack_hi, circ_lo, circ_hi
        )
        detail = {"range": [range_lo, range_hi], "expression": expression}
        if any(is_circ_symbol(name) for name, _ in stack_terms):
            # Record that this classification leans on the circular-MODIFY
            # bound (PRM p.6-23's single +-byte_length wrap correction,
            # CIRC_WRAP_SLACK = L7*scale), not just the plain entry-seed
            # bound, so a reader (or a future stricter run) can find every
            # store that depends on it without re-parsing `expression`.
            detail["via_circular_modify"] = True
        if ranges_overlap(range_lo, range_hi, width, target, target_width):
            return "UNRESOLVED", {
                "reason": (
                    "stack-relative address range overlaps the target; "
                    "Phase 1 bounds do not exclude it"
                ),
                **detail,
            }
        detail["assumption"] = ENTRY_SEED_ASSUMPTION
        return "EXCLUDED-STACK", detail

    return "UNRESOLVED", {"reason": "unrecognized address representation: %r" % (addr,)}


# --- orchestration (needs the firmware) -------------------------------------


def full_project_census(ctx):
    """-> (by_function: {fn_id: [rows]}, orphan: [rows]). orphan rows are
    store-capable instructions inside a return-delimited span with nothing
    decodable before the block's first real instruction (see
    tools/sharc_dev/sharcinv.py's module docstring: such a span is dropped rather
    than emitted as a function) -- expected to be empty or near-empty."""
    by_function = {}
    owned_pcs = set()
    for fn in ctx["functions"]:
        block = ctx["analyzed"][fn["block"]]
        sw_insns = sharcinv.instructions_in(block, fn["entry"], fn["exit"])
        rows = census_instructions(sw_insns)
        by_function[fn["id"]] = rows
        owned_pcs.update(row["pc"] for row in rows)
    orphan = []
    for block in ctx["analyzed"].values():
        sw_insns = [(block["base_sw"] + off // 2, insn) for off, insn in block["insns"]]
        for row in census_instructions(sw_insns):
            if row["pc"] not in owned_pcs:
                orphan.append(row)
    return by_function, orphan


def resolve_function(
    ctx,
    fn,
    dm_rows,
    max_steps,
    max_states,
    seed_global_constants=True,
    provisional_forms=(),
):
    """Run tools/sharc_dev/sharc_trace.py's trace() once from `fn`'s entry, seeded
    per seed_sets(seed_global_constants), and pull the resolved 'store'
    event at each of `dm_rows`' PCs.  A selected provisional form permits
    continuation only; every store at or after it records that dependency.
    -> ({pc: event_or_None}, stop_reasons: set, retained_path_forms)."""
    if not dm_rows:
        return {}, set(), ()
    wanted = {row["pc"] for row in dm_rows}
    states = trace_mod.trace(
        ctx["mem"],
        None,
        fn["entry"],
        sets=seed_sets(seed_global_constants),
        max_steps=max_steps,
        max_states=max_states,
        concrete_memory=True,
        assume_nw32=True,
        follow_loaded_calls=True,
        continue_external_calls=True,
        provisional_forms=tuple(provisional_forms),
    )
    events_by_pc = {pc: [] for pc in wanted}
    stop_reasons = set()
    retained_path_forms = set()
    provisional_form_set = set(provisional_forms)
    for state in states:
        retained_path_forms.add(tuple(getattr(state, "provisional_used", ())))
        if state.trace:
            last = state.trace[-1]
            if last.get("action") == "stop":
                stop_reasons.add(last.get("reason") or "stopped")
        used_before_event = set()
        conditional_type7a_uncertain = False
        for event in state.trace:
            # _execute records a provisional form's event after admitting it,
            # so include that form in the event's own dependency.
            if event.get("form") in provisional_form_set:
                used_before_event.add(event["form"])
            if event.get("action") == "i-modify-uncertain":
                conditional_type7a_uncertain = True
            if event.get("action") == "store" and event.get("pc_sw") in wanted:
                annotated = dict(event)
                if used_before_event:
                    annotated["provisional_forms_used"] = sorted(used_before_event)
                if conditional_type7a_uncertain:
                    annotated["conditional_type7a_uncertain"] = True
                events_by_pc[event["pc_sw"]].append(annotated)
    chosen = {pc: choose_store_event(events) for pc, events in events_by_pc.items()}
    return chosen, stop_reasons, tuple(sorted(retained_path_forms))


def classify_row(
    row,
    chosen_event,
    stop_reasons,
    target,
    fallback_width,
    stack_lo,
    stack_hi,
    target_width=1,
):
    width = row["width"] if row["width"] is not None else fallback_width
    if chosen_event is None:
        reasons = sorted(reason for reason in stop_reasons if reason)
        reason = (
            ("not reached: " + "; ".join(reasons))
            if reasons
            else "not reached by the tracer"
        )
        return "UNRESOLVED", {"reason": reason}, width
    if chosen_event.get("conditional_type7a_uncertain"):
        return (
            "UNRESOLVED",
            {
                "reason": "store may depend on conditional Type7a modify",
            },
            width,
        )
    provisional_forms_used = chosen_event.get("provisional_forms_used", ())
    if provisional_forms_used:
        return (
            "UNRESOLVED",
            {
                "reason": "store trace depends on provisional form(s)",
                "provisional_forms_used": list(provisional_forms_used),
            },
            width,
        )
    event_width = event_store_width(row["form"], chosen_event)
    if event_width is not None:
        width = event_width
    cls, detail = classify_store_address(
        chosen_event.get("address"), width, target, stack_lo, stack_hi, target_width
    )
    return cls, detail, width


_WORKER = {}


def _init_worker(blob_path, block_idxs, min_depth):
    _WORKER["ctx"] = sharcfn.load_context(blob_path, block_idxs, min_depth)


def _process_function_batch(
    fn_id, max_steps, max_states, seed_global_constants=True, provisional_forms=()
):
    """Spawn-safe unit: trace one function once and return plain records."""
    ctx = _WORKER["ctx"]
    fn = ctx["by_id"][fn_id]
    block = ctx["analyzed"][fn["block"]]
    dm_rows = [
        row
        for row in census_instructions(
            sharcinv.instructions_in(block, fn["entry"], fn["exit"])
        )
        if row["is_dm"]
    ]
    chosen, stop_reasons, retained_path_forms = resolve_function(
        ctx,
        fn,
        dm_rows,
        max_steps,
        max_states,
        seed_global_constants,
        provisional_forms,
    )
    return (
        fn_id,
        fn["entry"],
        dm_rows,
        chosen,
        sorted(stop_reasons),
        retained_path_forms,
    )


def _process_function(
    fn_id,
    target,
    max_steps,
    max_states,
    fallback_width,
    stack_lo,
    stack_hi,
    seed_global_constants=True,
    provisional_forms=(),
):
    ctx = _WORKER["ctx"]
    fn = ctx["by_id"][fn_id]
    block = ctx["analyzed"][fn["block"]]
    sw_insns = sharcinv.instructions_in(block, fn["entry"], fn["exit"])
    dm_rows = [row for row in census_instructions(sw_insns) if row["is_dm"]]
    chosen, stop_reasons, _retained_path_forms = resolve_function(
        ctx,
        fn,
        dm_rows,
        max_steps,
        max_states,
        seed_global_constants,
        provisional_forms,
    )
    out = []
    for row in dm_rows:
        cls, detail, width = classify_row(
            row,
            chosen.get(row["pc"]),
            stop_reasons,
            target,
            fallback_width,
            stack_lo,
            stack_hi,
        )
        out.append(
            {
                "pc": row["pc"],
                "form": row["form"],
                "function_entry": fn["entry"],
                "function_id": fn_id,
                "width": width,
                "class": cls,
                **detail,
            }
        )
    return fn_id, out


def _function_fact(
    fn_id,
    function_entry,
    function_ordinal,
    dm_rows,
    chosen,
    stops,
    retained_path_forms,
    trace_policy,
):
    """Canonical, independently cacheable result of one recovered function."""
    stores = [{"row": row, "event": chosen.get(row["pc"])} for row in dm_rows]
    # Store forms are deliberately included as a conservative dependency: a
    # semantic change to a form at a retained store must never reuse its trace.
    forms = {str(row["form"]) for row in dm_rows}
    for event in chosen.values():
        if isinstance(event, dict) and isinstance(event.get("form"), str):
            forms.add(event["form"])
    blockers = set()
    for reason in stops:
        text = str(reason)
        blockers.update(re.findall(r"(?:unsupported|unknown)\s+([0-9]+[a-z_]*)", text))
        # Some conservative semantic stops name their typed form first (for
        # example ``Type7d B2W(B7) source is not concrete``).  They are just
        # as revision-dependent as an ``unsupported 11a`` stop.
        blockers.update(re.findall(r"\bType([0-9]+[a-z_]*)\b", text))
    shape = sha256(_canonical_json(dm_rows)).hexdigest()
    return {
        "function_id": fn_id,
        "function_entry": function_entry,
        "function_ordinal": function_ordinal,
        "store_shape_sha256": shape,
        "complete": True,
        "trace_policy": trace_policy,
        "dependencies": {
            "forms": sorted(forms),
            "blockers": sorted(blockers),
            "handler_revisions": {
                form: handler_revision(form) for form in sorted(forms | blockers)
            },
        },
        "stop_reasons": sorted(stops),
        "retained_path_provisional_forms": [
            list(forms) for forms in retained_path_forms
        ],
        "stores": stores,
    }


def _canonical_json(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def collect_trace_facts(
    blob_path,
    block_idxs,
    min_depth,
    max_steps,
    max_states,
    jobs=1,
    seed_global_constants=True,
    trace_policy=STRICT_TRACE_POLICY,
    cached_functions=None,
):
    """Trace each owning function once and return target-independent facts.

    ``trace_policy`` is an explicit, versioned continuation allowance.  It
    never upgrades evidence: stores after an admitted provisional form are
    retained solely as UNRESOLVED facts during classification.
    """
    provisional_forms = provisional_forms_for_policy(trace_policy)
    if jobs <= 0:
        raise ValueError("jobs must be positive")
    try:
        ctx = sharcfn.load_context(blob_path, block_idxs, min_depth)
    except Exception as error:
        raise RuntimeError(
            f"cannot build writer census context for {blob_path}"
        ) from error
    by_function, orphan = full_project_census(ctx)

    census = Counter()
    for rows in by_function.values():
        census.update(row["form"] for row in rows if row["is_dm"])
    census.update(row["form"] for row in orphan if row["is_dm"])

    ordered_work = sorted(
        (
            fn_id
            for fn_id, rows in by_function.items()
            if any(row["is_dm"] for row in rows)
        )
    )
    ordinals = {fn_id: ordinal for ordinal, fn_id in enumerate(ordered_work)}
    cached_functions = cached_functions or {}
    reusable = {}
    for fn_id in ordered_work:
        cached = cached_functions.get(fn_id)
        rows = [row for row in by_function[fn_id] if row["is_dm"]]
        expected_shape = sha256(_canonical_json(rows)).hexdigest()
        # Shape and ordinal bind a recovered ID to this exact static census,
        # so a rebuilt inventory cannot silently inherit a fact.
        if (
            isinstance(cached, dict)
            and isinstance(cached.get("complete"), bool)
            and cached["complete"]
            and cached.get("function_entry") == ctx["by_id"][fn_id]["entry"]
            and cached.get("function_ordinal") == ordinals[fn_id]
            and isinstance(cached.get("store_shape_sha256"), str)
            and cached.get("store_shape_sha256") == expected_shape
        ):
            reusable[fn_id] = cached
    work = [fn_id for fn_id in ordered_work if fn_id not in reusable]

    if jobs <= 1:
        _init_worker(blob_path, block_idxs, min_depth)
        batches = [
            _process_function_batch(
                fn_id, max_steps, max_states, seed_global_constants, provisional_forms
            )
            for fn_id in work
        ]
    else:
        with ProcessPoolExecutor(
            max_workers=jobs,
            initializer=_init_worker,
            initargs=(blob_path, block_idxs, min_depth),
        ) as pool:
            futures = [
                pool.submit(
                    _process_function_batch,
                    fn_id,
                    max_steps,
                    max_states,
                    seed_global_constants,
                    provisional_forms,
                )
                for fn_id in work
            ]
            batches = [future.result() for future in as_completed(futures)]
    functions_by_id = dict(reusable)
    for fn_id, function_entry, dm_rows, chosen, stops, retained_path_forms in batches:
        functions_by_id[fn_id] = _function_fact(
            fn_id,
            function_entry,
            ordinals[fn_id],
            dm_rows,
            chosen,
            stops,
            retained_path_forms,
            trace_policy,
        )
    functions = [functions_by_id[fn_id] for fn_id in ordered_work]
    return {
        "contract": "sharc-writer-trace-facts/v2",
        "image_sha256": ctx["sha256"],
        "seed_global_constants": seed_global_constants,
        "trace_policy": trace_policy,
        "provisional_forms": list(provisional_forms),
        "census": dict(sorted(census.items())),
        "census_total": sum(census.values()),
        "functions": functions,
        "orphan_stores": [row for row in orphan if row["is_dm"]],
    }


def classify_trace_facts(facts, targets, fallback_width, stack_lo, stack_hi):
    """Classify cached raw facts for targets without executing firmware code."""
    try:
        normalized_targets = {(int(address), int(width)) for address, width in targets}
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            "writer targets must be (integer address, integer width) pairs"
        ) from error
    targets = tuple(sorted(normalized_targets))
    if not targets:
        return {}
    if facts.get("contract") != "sharc-writer-trace-facts/v2":
        raise ValueError("incompatible writer trace facts")

    all_rows = []
    for function in facts["functions"]:
        fn_id = function["function_id"]
        function_entry = function["function_entry"]
        stops = set(function["stop_reasons"])
        for target, target_width in targets:
            for store in function["stores"]:
                row, event = store["row"], store["event"]
                cls, detail, width = classify_row(
                    row,
                    event,
                    stops,
                    target,
                    fallback_width,
                    stack_lo,
                    stack_hi,
                    target_width,
                )
                all_rows.append(
                    {
                        "target": target,
                        "target_width": target_width,
                        "pc": row["pc"],
                        "form": row["form"],
                        "function_entry": function_entry,
                        "function_id": fn_id,
                        "width": width,
                        "class": cls,
                        **detail,
                    }
                )

    for row in facts["orphan_stores"]:
        width = row["width"] if row["width"] is not None else fallback_width
        for target, _target_width in targets:
            all_rows.append(
                {
                    "target": target,
                    "target_width": _target_width,
                    "pc": row["pc"],
                    "form": row["form"],
                    "function_entry": None,
                    "function_id": None,
                    "width": width,
                    "class": "UNRESOLVED",
                    "reason": "instruction has no recovered owning function to trace from",
                }
            )

    all_rows.sort(key=lambda row: (row["target"], row["target_width"], row["pc"]))
    census_total = facts["census_total"]
    results = {}
    for target, target_width in targets:
        rows = [
            dict(row)
            for row in all_rows
            if row["target"] == target and row["target_width"] == target_width
        ]
        totals = Counter(row["class"] for row in rows)
        if sum(totals.values()) != census_total:
            raise RuntimeError("class totals do not sum to census total")
        excluded = [row for row in rows if row["class"] == "EXCLUDED-STACK"]
        results[(target, target_width)] = {
            "target": target,
            "target_width": target_width,
            "width": target_width,
            "image_sha256": facts["image_sha256"],
            "stack": [stack_lo, stack_hi] if stack_lo is not None else None,
            "seed_global_constants": facts["seed_global_constants"],
            "trace_policy": facts["trace_policy"],
            "provisional_forms": facts["provisional_forms"],
            "census": facts["census"],
            "census_total": census_total,
            "class_totals": dict(sorted(totals.items())),
            "excluded_stack_depends_on_unproven_entry_assumption": len(excluded),
            "excluded_stack_via_circular_modify": sum(
                1 for row in excluded if row.get("via_circular_modify")
            ),
            "stores": rows,
            "coverage": "incomplete" if totals.get("UNRESOLVED", 0) else "complete",
            "evidence_class": "strict-trace"
            if not totals.get("UNRESOLVED", 0)
            else "unknown",
        }
    return dict(sorted(results.items()))


def run_many(
    blob_path,
    block_idxs,
    min_depth,
    targets,
    max_steps,
    max_states,
    fallback_width,
    stack_lo,
    stack_hi,
    jobs=1,
    seed_global_constants=True,
    trace_policy=STRICT_TRACE_POLICY,
):
    """Classify targets from one target-independent trace-fact collection."""
    targets = tuple(targets)
    if not targets:
        return {}
    facts = collect_trace_facts(
        blob_path,
        block_idxs,
        min_depth,
        max_steps,
        max_states,
        jobs=jobs,
        seed_global_constants=seed_global_constants,
        trace_policy=trace_policy,
    )
    return classify_trace_facts(facts, targets, fallback_width, stack_lo, stack_hi)


def run(
    blob_path,
    block_idxs,
    min_depth,
    target,
    max_steps,
    max_states,
    fallback_width,
    stack_lo,
    stack_hi,
    jobs=1,
    seed_global_constants=True,
    trace_policy=STRICT_TRACE_POLICY,
):
    """Backward-compatible single-target façade."""
    return run_many(
        blob_path,
        block_idxs,
        min_depth,
        [(target, fallback_width)],
        max_steps,
        max_states,
        fallback_width,
        stack_lo,
        stack_hi,
        jobs=jobs,
        seed_global_constants=seed_global_constants,
        trace_policy=trace_policy,
    )[(target, fallback_width)]


def _parse_stack(text):
    if text is None:
        return DEFAULT_STACK_LO, DEFAULT_STACK_HI
    if text.lower() == "none":
        return None, None
    lo_text, hi_text = text.split(":")
    return int(lo_text, 0), int(hi_text, 0)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("target", type=lambda x: int(x, 0))
    parser.add_argument("--image", default="dt2-1.16", choices=sorted(IMAGES))
    parser.add_argument(
        "--width",
        type=int,
        default=4,
        help="fallback byte width for a store whose width cannot be "
        "statically determined (default: 4)",
    )
    parser.add_argument(
        "--stack",
        default=None,
        help="LO:HI stack bounds (default: Phase 1 evidence, "
        '%#x:%#x); "none" disables stack exclusion entirely'
        % (DEFAULT_STACK_LO, DEFAULT_STACK_HI),
    )
    parser.add_argument("--json")
    parser.add_argument("--max-steps", type=int, default=4000)
    parser.add_argument("--max-states", type=int, default=128)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--min-depth", type=int, default=8)
    parser.add_argument(
        "--blocks",
        default=None,
        help="comma-separated block indices (default: sharcinv.CODE_BLOCKS)",
    )
    parser.add_argument(
        "--no-seed-global-constants",
        dest="seed_global_constants",
        action="store_false",
        help="do not seed M5/M6/M7/M13/M14/M15/L6/L7 with their Task-1 "
        "evidenced constant at function entry (see GLOBAL_CONSTANT_SEEDS "
        "docstring); on by default",
    )
    args = parser.parse_args(argv)

    blob_path = os.path.join(REPO, IMAGES[args.image])
    block_idxs = (
        tuple(int(x, 0) for x in args.blocks.split(","))
        if args.blocks
        else sharcinv.CODE_BLOCKS
    )
    stack_lo, stack_hi = _parse_stack(args.stack)

    result = run(
        blob_path,
        block_idxs,
        args.min_depth,
        args.target,
        args.max_steps,
        args.max_states,
        args.width,
        stack_lo,
        stack_hi,
        jobs=args.jobs,
        seed_global_constants=args.seed_global_constants,
    )

    text = json.dumps(result, indent=1, sort_keys=True)
    if args.json:
        try:
            os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
            with open(args.json, "w") as fh:
                fh.write(text)
        except OSError as error:
            raise RuntimeError(f"cannot write writer report {args.json}") from error
        print(
            "wrote %s (%d stores, %d hits)"
            % (args.json, result["census_total"], result["class_totals"].get("HIT", 0))
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
