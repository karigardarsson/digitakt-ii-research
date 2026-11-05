#!/usr/bin/env python3
"""The memory input contract for a SHARC+ root function: which DM addresses
its call/jump reach set reads without writing itself, and who writes them
(a function outside the reach set, a loader-filled data block, a loader-
filled fill/zero block, or nobody this pass can find).

    uv run python -m tools.sharc_dev.sharc_contract dt2-1.16 0x1c2b24
    uv run python -m tools.sharc_dev.sharc_contract dt2-1.16 0x1c7671 --json out.json

    from tools.sharc_dev import sharc, sharc_contract
    img = sharc.load("dt2-1.16")
    contract = sharc_contract.build(img, 0x1c2b24)

This is a purely static contract, built on top of tools/sharc_dev/sharcdb/'s `ptr`
table (constant-pointer propagation over aligned instructions only -- see
its own module note for what is and is not modelled). It is not proof an
address is genuinely live at runtime, and a resolved hit is only as good as
that propagation pass: an indexed I,M access whose M modifier is not a
tracked constant shows up separately as a 'base_only' hit (base register
value known, exact offset not), never silently folded into a hard range.

`mem_access.abs_address` is deliberately NOT used here: for a form 15a
access (`DM(I3 - 0x2a8)`, I-register-relative), that column holds the raw
negative OFFSET reinterpreted as if it were an absolute address (a large
0xffffxxxx value) -- a base-relative operand, not one. `ptr.address` is the
column built to give the real effective address (base + offset, from this
same constant-propagation pass), so every address in this contract comes
from there instead.

Reach is call+jump edges from the root's own function entry (every `edges`
row with a resolved `to_function`, of any kind -- call, jump, cond_jump, a
resolved indirect), not the narrower CALL-only `reach` table tools/sharc_dev/sharcdb/
already fills: that table is seeded from `roots` alone, so an arbitrary
function used as a root here (FUN_1c2b24 is not itself in `roots`) has no
rows there. See docs/findings/06's own "Reach from 0x1c2b24 ... 154
functions" figure -- this is the same walk.
"""

from __future__ import annotations

import argparse
import json
import os

_here = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc  # noqa: E402

# System MMR (SHARC+ HRM ch.2: peripheral registers) and core MMR (interrupt
# controller, timers, DAG/PM/DM control) windows. A concrete (non-symbolic)
# runner will not model either at first, so any init-function access in
# these ranges needs a stub or a skip, not a real memory poke.
SYSTEM_MMR_RANGE = (0x31000000, 0x31100000)
CORE_MMR_RANGE = (0x00030000, 0x00032000)

# tools/sharc_dev/sharcdb/'s `ptr` constant-propagation pass resolves some non-
# address values (a float bit pattern, a loop count, anything else that
# happens to sit in an I register at the wrong moment) into implausible
# "addresses" -- e.g. 0x40000000 (the ColdFire MAIN load address, not
# anything SHARC+ DM space would use) or 0xc1eed80. Every DM address this
# repo has confirmed real so far (docs/findings/06) falls in one of these
# bands: the SHARC-visible workspace/table/ring region, the two external
# float payload blocks, or the MMR windows above. An object outside all of
# these is reported separately as low-confidence, not folded in as if it
# were as solid as a known-structure hit.
_PLAUSIBLE_DM_BANDS = (
    (0x00200000, 0x00300000),  # workspace, voice records, rings, mix/per-track tables
    (0x8045A000, 0x8045B000),  # boot-loaded float payload block A
    (0x8055C000, 0x8055E000),  # boot-loaded float payload block B (cos/sin + tail)
    SYSTEM_MMR_RANGE,
    CORE_MMR_RANGE,
)


def _is_plausible(addr):
    return any(_is_in(addr, band) for band in _PLAUSIBLE_DM_BANDS)


# The two RTOS task roots this repo has already named (tools/sharc_dev/sharcdb/'s
# rtos_task root note carries 'helper=... call_site=...'); told apart here
# by call site rather than re-deriving them, since they're the only two
# roots.kind='rtos_task' rows in dt2-1.16 (docs/findings/06, "Audio Task"
# and the RPC task).
AUDIO_TASK_CALL_SITE = 0x1C7775
RPC_TASK_CALL_SITE = 0x1C3F6A

# roots.kind='interrupt_vector', 'slot 1 RSTI' -- the SHARC+ hardware IVT's
# reset vector, whose target sw equals the loader_entry root's own sw
# (0x1c1338): the reset vector IS the boot entry point on this image, not an
# asynchronous interrupt. See classify_writer_function()'s own note.
RESET_VECTOR_SW = 0x1C1338


def _is_in(addr, rng):
    lo, hi = rng
    return lo <= addr < hi


def is_peripheral(addr):
    """True when addr falls in the system or core MMR window."""
    return _is_in(addr, SYSTEM_MMR_RANGE) or _is_in(addr, CORE_MMR_RANGE)


# --- reach ------------------------------------------------------------------


def reach_functions(img, root):
    """Function entry_sw set reachable from root's own containing function,
    over every edges row with a resolved to_function -- see module docstring
    for why this, not the `reach` table, is used."""
    fn = img.func(root)
    if fn is None:
        raise ValueError("no function contains 0x%x" % root)
    entry = int(fn["entry_sw"], 16)
    seen = {entry}
    frontier = [entry]
    while frontier:
        cur = frontier.pop()
        rows = img.sql(
            "SELECT DISTINCT to_function FROM edges WHERE image=? AND from_function=? AND to_function IS NOT NULL",
            img.name,
            cur,
        )
        for (target,) in rows:
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    return seen


def _use_temp_fn_table(img, funcs):
    """Load `funcs` into a scratch temp table (funcs can run to the
    thousands -- a plain SQL IN(...) list would blow SQLite's default bound-
    parameter limit) and return its name."""
    img.db.execute("CREATE TEMP TABLE IF NOT EXISTS _sc_fn (sw INTEGER PRIMARY KEY)")
    img.db.execute("DELETE FROM _sc_fn")
    img.db.executemany("INSERT OR IGNORE INTO _sc_fn VALUES (?)", [(f,) for f in funcs])
    return "_sc_fn"


def instruction_count(img, funcs):
    if not funcs:
        return 0
    _use_temp_fn_table(img, funcs)
    row = img.db.execute(
        "SELECT COUNT(*) FROM insn i JOIN _sc_fn r ON r.sw = i.function_sw "
        "WHERE i.image=? AND i.aligned=1",
        (img.name,),
    ).fetchone()
    return row[0]


def _ptr_rows_for(img, funcs, direction):
    """Every `ptr` row of `direction` ('load'/'store') whose owning
    instruction's function_sw is in `funcs`."""
    if not funcs:
        return []
    _use_temp_fn_table(img, funcs)
    return img.db.execute(
        "SELECT p.sw, p.base_reg, p.base_value, p.address, p.width "
        "FROM ptr p JOIN insn i ON i.image=? AND i.sw=p.sw "
        "JOIN _sc_fn r ON r.sw=i.function_sw "
        "WHERE p.image=? AND p.direction=?",
        (img.name, img.name, direction),
    ).fetchall()


def read_write_addresses(img, root):
    """(read_addrs, write_addrs): every concrete `ptr`-resolved DM address
    ROOT's own reach set (reach_functions()) loads from / stores to -- the
    same rows build() itself groups into known-structure objects, flattened
    to individual addresses (a base-only hit with no resolved address is
    dropped, same as build()'s own read_addrs/write_addrs). Used by
    tools/sharc_dev/sharc_inputs.py, which labels one address at a time rather than
    one known-structure object."""
    reach = reach_functions(img, root)
    read_addrs = {
        address
        for _sw, _base_reg, _base_value, address, _width in _ptr_rows_for(
            img, reach, "load"
        )
        if address is not None
    }
    write_addrs = {
        address
        for _sw, _base_reg, _base_value, address, _width in _ptr_rows_for(
            img, reach, "store"
        )
        if address is not None
    }
    return read_addrs, write_addrs


def _global_ptr_rows(img, direction, lo, hi):
    """Every `ptr` row of `direction` in the WHOLE image (no reach filter)
    whose resolved address lands in [lo, hi)."""
    return img.db.execute(
        "SELECT sw, base_reg, base_value, address, width FROM ptr "
        "WHERE image=? AND direction=? AND address IS NOT NULL AND address >= ? AND address < ?",
        (img.name, direction, lo, hi),
    ).fetchall()


# --- grouping into known objects ---------------------------------------------


def _label_and_span(addr):
    """(label, lo, hi) from sharc.py's own _KNOWN_STRIDED/_KNOWN_RANGES when
    addr falls in one (the same span for every index of a strided array, so
    every index groups into one object), else (None, bucket_lo, bucket_hi)
    for an 0x40-byte bucket -- a readability grouping for addresses this
    repo has not named a structure for yet, not a claimed object boundary."""
    for base, stride, count, label in sharc._KNOWN_STRIDED:
        if base <= addr < base + stride * count:
            return label, base, base + stride * count
    for lo, hi, label in sharc._KNOWN_RANGES:
        if lo <= addr < hi:
            return label, lo, hi
    bucket_lo = addr & ~0x3F
    return None, bucket_lo, bucket_lo + 0x40


# --- writer classification --------------------------------------------------


def block_kind_for(img, addr):
    """('data'|'fill'|'code'|None, block_idx|None) for the loader block
    covering addr in the bare-sw convention (blocks.base_sw + byte_count/2,
    see tools/sharc_dev/sharcdb/'s own base_sw column comment)."""
    rows = img.sql(
        "SELECT idx, target_address, byte_count, kind, base_sw FROM blocks WHERE image=? AND base_sw IS NOT NULL",
        img.name,
    )
    for idx, _target, byte_count, kind, base_sw in rows:
        if base_sw <= addr < base_sw + byte_count // 2:
            return kind, idx
    return None, None


class _RootIndex:
    """Cached roots/reach lookups so classifying many writer functions does
    not re-query per call."""

    def __init__(self, img):
        self.img = img
        self.roots_by_sw = {}
        for sw, kind, note in img.sql(
            "SELECT sw, kind, note FROM roots WHERE image=?", img.name
        ):
            self.roots_by_sw.setdefault(sw, []).append((kind, note))
        self.audio_task_root = None
        self.rpc_task_root = None
        for sw, entries in self.roots_by_sw.items():
            for kind, note in entries:
                if kind != "rtos_task" or not note:
                    continue
                if "call_site=0x%x" % AUDIO_TASK_CALL_SITE in note:
                    self.audio_task_root = sw
                elif "call_site=0x%x" % RPC_TASK_CALL_SITE in note:
                    self.rpc_task_root = sw

    def roots_reaching(self, fn_entry):
        rows = self.img.sql(
            "SELECT rc.root_sw, r.kind, rc.depth FROM reach rc JOIN roots r "
            "ON r.image=rc.image AND r.sw=rc.root_sw WHERE rc.image=? AND rc.function_sw=?",
            self.img.name,
            fn_entry,
        )
        return rows

    def classify_writer_function(self, fn_entry):
        """One label for a writer function, from the roots that reach it.
        Priority: the audio task, then boot (loader_entry), then a genuine
        async interrupt_vector, then the RPC task, else whatever root kinds
        do reach it. The SHARC+ hardware IVT's slot 1 (RSTI, reset) is
        excluded from "genuine async interrupt": on this image it targets
        the SAME address as the loader_entry root (both sw=0x1c1338, see
        the roots table), so it is the boot vector, not an asynchronous
        interrupt -- treating it as one would mislabel every boot function
        it reaches (which is most of them) as an ISR."""
        rows = self.roots_reaching(fn_entry)
        root_sws = {r[0] for r in rows}
        true_isr = any(
            kind == "interrupt_vector" and sw != RESET_VECTOR_SW for sw, kind, _ in rows
        )
        if self.audio_task_root is not None and self.audio_task_root in root_sws:
            return "audio task (per block)"
        if RESET_VECTOR_SW in root_sws or any(
            kind == "loader_entry" for _, kind, _ in rows
        ):
            return "boot/init"
        if true_isr:
            return "interrupt handler (ISR)"
        if self.rpc_task_root is not None and self.rpc_task_root in root_sws:
            return "RPC task"
        kinds = {r[1] for r in rows}
        if kinds:
            return (
                "unclassified static entry (%s; needs runtime confirmation)"
                % ", ".join(sorted(kinds))
            )
        return "no static root reaches it (needs runtime confirmation)"


def classify_external_writers(img, root_index, lo, hi, reach_set):
    """External writer rows for [lo, hi), grouped by owning function, each
    with its classification; plus the fallback classification for the range
    as a whole when NO writer (internal or external) was found at all."""
    rows = _global_ptr_rows(img, "store", lo, hi)
    by_fn = {}
    for sw, _base_reg, _base_value, _address, _width in rows:
        fn = img.func(sw)
        fn_entry = int(fn["entry_sw"], 16) if fn else None
        if fn_entry is not None and fn_entry in reach_set:
            continue  # self-written -- not an external contract item
        key = fn_entry if fn_entry is not None else sw
        entry = by_fn.setdefault(
            key,
            {
                "function": "0x%x" % fn_entry if fn_entry is not None else None,
                "name": fn["name"] if fn else None,
                "sites": [],
            },
        )
        entry["sites"].append("0x%x" % sw)
    writers = []
    for key, entry in by_fn.items():
        entry["sites"] = sorted(set(entry["sites"]))
        cls = (
            root_index.classify_writer_function(key)
            if isinstance(key, int) and img.func(key)
            else "writer site has no owning function (trampoline/interior code)"
        )
        entry["class"] = cls
        writers.append(entry)
    return sorted(writers, key=lambda w: w["function"] or "")


def _fallback_class(img, lo):
    kind, idx = block_kind_for(img, lo)
    if kind == "data":
        return "loader-initialised constant (block %d, never written at runtime)" % idx
    if kind == "fill":
        return "loader fill/zero block (block %d, never written at runtime)" % idx
    if kind == "code":
        return "loader code block (block %d) -- likely misclassified as data" % idx
    return "unknown (no covering loader block and no writer found)"


# --- building the contract ---------------------------------------------------


def build(img, root):
    reach = reach_functions(img, root)
    n_insns = instruction_count(img, reach)
    root_index = _RootIndex(img)

    load_rows = _ptr_rows_for(img, reach, "load")
    store_rows = _ptr_rows_for(img, reach, "store")

    read_addrs, read_base_only = set(), []
    for sw, base_reg, base_value, address, _width in load_rows:
        if address is not None:
            read_addrs.add(address)
        elif base_value is not None:
            read_base_only.append(
                {
                    "sw": "0x%x" % sw,
                    "base_reg": base_reg,
                    "base_value": "0x%x" % base_value,
                }
            )

    write_addrs, write_base_only = set(), []
    for sw, base_reg, base_value, address, _width in store_rows:
        if address is not None:
            write_addrs.add(address)
        elif base_value is not None:
            write_base_only.append(
                {
                    "sw": "0x%x" % sw,
                    "base_reg": base_reg,
                    "base_value": "0x%x" % base_value,
                }
            )

    groups = {}
    for addr in read_addrs:
        label, lo, hi = _label_and_span(addr)
        key = (label, lo, hi)
        g = groups.setdefault(key, {"label": label, "lo": lo, "hi": hi, "addrs": set()})
        g["addrs"].add(addr)

    objects, low_confidence = [], []
    for (label, lo, hi), g in sorted(groups.items(), key=lambda kv: kv[0][1]):
        addrs = g["addrs"]
        self_written = sorted(a for a in addrs if a in write_addrs)
        unwritten = sorted(a for a in addrs if a not in write_addrs)
        obj = {
            "label": label or "0x%x" % lo,
            "range": "0x%x-0x%x" % (lo, hi),
            "size": hi - lo,
            "n_read_addrs": len(addrs),
            "self_written_addrs": len(self_written),
            "peripheral": is_peripheral(lo) or is_peripheral(hi - 1),
        }
        if unwritten:
            writers = classify_external_writers(img, root_index, lo, hi, reach)
            obj["externally_provided"] = True
            obj["writers"] = writers
            obj["fallback_class"] = _fallback_class(img, lo) if not writers else None
        else:
            obj["externally_provided"] = False
            obj["writers"] = []
            obj["fallback_class"] = None
        # A known-structure label is trusted regardless of address (it was
        # named from real evidence elsewhere); an unlabelled bucket is only
        # kept as a solid object when it falls in a plausible DM band -- see
        # _PLAUSIBLE_DM_BANDS's own note on the `ptr` pass's false positives.
        if label is not None or _is_plausible(lo):
            objects.append(obj)
        else:
            low_confidence.append(obj)

    fn = img.func(root)
    return {
        "image": img.name,
        "root": "0x%x" % root,
        "root_function": fn["entry_sw"] if fn else None,
        "n_functions": len(reach),
        "n_instructions": n_insns,
        "objects": objects,
        "low_confidence_objects": low_confidence,
        "read_base_only": sorted(read_base_only, key=lambda r: r["sw"]),
        "write_base_only": sorted(write_base_only, key=lambda r: r["sw"]),
    }


# --- CLI ----------------------------------------------------------------------


def _print_text(contract):
    print(
        "%s root=%s (function %s): %d functions, %d instructions"
        % (
            contract["image"],
            contract["root"],
            contract["root_function"],
            contract["n_functions"],
            contract["n_instructions"],
        )
    )
    for obj in contract["objects"]:
        flag = " [PERIPHERAL]" if obj["peripheral"] else ""
        print(
            "\n%s  %s  size=0x%x  reads=%d self_written=%d%s"
            % (
                obj["label"],
                obj["range"],
                obj["size"],
                obj["n_read_addrs"],
                obj["self_written_addrs"],
                flag,
            )
        )
        if not obj["externally_provided"]:
            print("  fully self-written within the reach set")
            continue
        if obj["writers"]:
            for w in obj["writers"]:
                print(
                    "  writer %s %s  sites=%s  class=%s"
                    % (
                        w["function"],
                        w["name"] or "",
                        ",".join(w["sites"]),
                        w["class"],
                    )
                )
        else:
            print("  no external writer found: %s" % obj["fallback_class"])
    if contract["low_confidence_objects"]:
        print(
            "\n%d low-confidence bucket(s) outside any plausible DM band (likely `ptr`-pass "
            "false positives -- non-address values resolved as if they were addresses; see "
            "_PLAUSIBLE_DM_BANDS): %s"
            % (
                len(contract["low_confidence_objects"]),
                ", ".join(o["range"] for o in contract["low_confidence_objects"][:20]),
            )
        )
    if contract["read_base_only"]:
        print(
            "\nbase-only reads (base register known, exact offset not, within 0x10000):"
        )
        for r in contract["read_base_only"][:40]:
            print("  ", r)
        if len(contract["read_base_only"]) > 40:
            print("  ... +%d more" % (len(contract["read_base_only"]) - 40))


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("image")
    p.add_argument(
        "root", help="hex or decimal short-word address of the root function"
    )
    p.add_argument("--json", help="write the full contract as JSON to this path")
    args = p.parse_args(argv)

    img = sharc.load(args.image)
    root = int(args.root, 0)
    contract = build(img, root)
    _print_text(contract)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(contract, fh, indent=2)
        print("\nwrote %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
