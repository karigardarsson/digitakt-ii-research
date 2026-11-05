#!/usr/bin/env python3
"""One small Python API over a tools/sharc_dev/sharcdb/ database: build/analyze once,
then query in-process instead of a fresh CLI round trip per question.

    from tools.sharc_dev import sharc
    img = sharc.load("dt2-1.16")
    img.func(0x1c642a)
    img.reach(0x1c642a, 0x1c7053)

Ad hoc SQL from the shell, without writing a script:

    uv run python -m tools.sharc_dev.sharc dt2-1.16 "SELECT kind, count(*) FROM roots GROUP BY kind"
"""

from __future__ import annotations

import bisect
import collections
import json
import os
import sqlite3
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))

import networkx as nx  # noqa: E402

from tools.sharc_dev import (  # noqa: E402
    sharc_trace,  # noqa: E402
    sharcdb,  # noqa: E402
    sharcfn,  # noqa: E402
    sharcinv,  # noqa: E402
    sharcldr,  # noqa: E402
)

SECTIONS_DIR = "out/sections"
DB_DIR = "out/sharcdb"

# The firmware's own hardware DAG-modify reset values (docs/findings/06),
# seeded by default so a trace() caller doesn't have to remember them.
_DEFAULT_TRACE_REGS: dict[str | int, int] = {
    "M5": 0,
    "M6": 1,
    "M7": -1,
    "M13": 0,
    "M14": 1,
    "M15": -1,
}

# tools/sharc_dev/sharcdb.sql's "last writer of REG before sw S" recursive CTE, kept
# here verbatim rather than re-derived: walk basic blocks backwards over
# succ, stopping expansion at the first block on each path that already has
# a regdef of REG before the search's upper bound there.
#
# The recursive step's upper_sw for a predecessor block must be that
# predecessor's OWN end_sw, not the block it is being entered from: a
# predecessor executes in full before control reaches the current block, so
# every def in [predecessor.start_sw, predecessor.end_sw) is "before" the
# original sw, and nothing in the current block should be re-admitted. Using
# the current block's end_sw here (as an earlier version of this query did)
# widens the search range for a contiguous predecessor (whose end_sw equals
# the current block's start_sw) out past the current block's own end,
# re-admitting that block's later defs -- including, when sw itself defines
# reg, a "last def of reg before sw" that is sw itself.
_LAST_DEF_SQL = """
WITH RECURSIVE walk(block_sw, upper_sw) AS (
  SELECT b0.start_sw, ?
  FROM bblocks b0 WHERE b0.image = ? AND b0.start_sw <= ? AND b0.end_sw > ?
  UNION
  SELECT s.from_block, pb.end_sw
  FROM walk w
  JOIN bblocks b ON b.image = ? AND b.start_sw = w.block_sw
  JOIN succ s ON s.image = ? AND s.to_block = w.block_sw
  JOIN bblocks pb ON pb.image = ? AND pb.start_sw = s.from_block
  WHERE NOT EXISTS (
    SELECT 1 FROM regdef d
    WHERE d.image = ? AND d.reg = ? AND d.sw >= b.start_sw AND d.sw < w.upper_sw
  )
)
SELECT DISTINCT writer_sw FROM (
  SELECT MAX(d.sw) AS writer_sw
  FROM walk w
  JOIN bblocks b ON b.image = ? AND b.start_sw = w.block_sw
  JOIN regdef d ON d.image = ? AND d.reg = ? AND d.sw >= b.start_sw AND d.sw < w.upper_sw
  GROUP BY w.block_sw, w.upper_sw
)"""

_FUNC_COLUMNS = (
    "entry_sw",
    "end_sw",
    "name",
    "n_insns",
    "block",
    "label",
    "entry_kind",
    "has_static_caller",
    "is_leaf",
)

# Known DM structures, addresses in this repo's own bare convention (the same
# one literals.value/dataref.value/ptr.address already use -- see
# tools/sharc_dev/sharcdb/'s DB_VERSION v7 comment for how that was pinned down
# against IVT_DISPATCH_TABLES). Used by Image.card() to label a resolved
# memory access by structure instead of a bare hex address. Strided arrays
# first (a fixed-size record repeated N times), then flat ranges; the first
# match wins. Track count is a generous upper bound, not a verified count --
# an index past the real number of tracks is still reported (as a high
# index), not silently misclassified as "elsewhere".
_KNOWN_STRIDED = (
    # docs/findings/06: the per-track record array and its "array #2" twin.
    (0x2506EC, 0xDC, 32, "per-track record"),
    (0x2412C8 + 0xD604, 0x1D8, 32, "per-track array#2"),
)
_KNOWN_RANGES = (
    (0x2412C8, 0x2412C8 + 0x10000, "per-frame workspace"),
    (0x261CC8, 0x264200, "rings"),
    (0x252D78, 0x252D78 + 4, "mix table base"),
    # tools/sharc_dev/sharcdb/'s IVT_DISPATCH_TABLES, in the same address convention;
    # both are boot-time FILL blocks on every image checked so far (DB_VERSION
    # v7's comment) -- labelled here anyway so a resolved access there (e.g.
    # once startup code is traced) still gets a name instead of a bare hex.
    (0x240948, 0x240948 + 392, "SECI dispatch table"),
    (0x240AD4, 0x240AD4 + 1740, "generic interrupt dispatch table"),
)

# tools/sharc_dev/sharc.py's Image.card() pairs an image with its cross-image partner
# (same func_hash convention, see Image.match()) for the "cross-image match"
# section -- the two shipping SHARC+ firmwares this repo builds.
_CROSS_IMAGE_PARTNER = {
    "dt2-1.16": "dn2-1.11",
    "dt2-1.15C": "dn2-1.10E",
    "dn2-1.11": "dt2-1.16",
    "dn2-1.10E": "dt2-1.15C",
}


def _describe_address(addr):
    """A known-structure label for a DM address (see _KNOWN_STRIDED/
    _KNOWN_RANGES above), or a bare hex string when nothing matches."""
    if addr is None:
        return "unresolved"
    for base, stride, count, label in _KNOWN_STRIDED:
        if base <= addr < base + stride * count:
            idx, off = divmod(addr - base, stride)
            return "%s[%d]%s" % (label, idx, ("+0x%x" % off) if off else "")
    for lo, hi, label in _KNOWN_RANGES:
        if lo <= addr < hi:
            off = addr - lo
            return "%s%s" % (label, ("+0x%x" % off) if off else "")
    return "0x%x" % addr


def _hex(value):
    return "0x%x" % value if isinstance(value, int) else value


# --- ASTATX/ASTATY bit-group modelling for defuse()/slice() ----------------
#
# PRM ch.4 (REGF_ASTATX/REGF_ASTATY) and tools/sharc_dev/sharcspec/compute_table.json's
# opcode tables split ASTATx into disjoint per-compute-unit bit groups --
# ALU (AZ/AV/AN/AC/AS/AI/AF), MULT (MN/MV/MU/MI), SHIFT (SV/SZ/SS) -- plus
# BTF, which only the system bit-test/xor-test op (Type18a, bop 4/5) sets,
# an entirely separate mechanism from any compute unit. tools/sharc_dev/sharc_core/
# flags.py already encodes these exact groups (ALU_FLAGS_MASK,
# MULT_FLAGS_MASK, SHIFT_FLAGS_MASK, BTF_BIT) for the concrete/symbolic
# tracers; this reuses those same bit numbers (via sharc_trace's re-export)
# rather than re-deriving them, so a slice()/defuse() bit group can never
# disagree with what tools/sharc_dev/sharc_run.py's Halt actually reads.
_ASTAT_GROUPS = ("ALU", "MULT", "SHIFT", "BTF")
# "ASTAT.ALU" etc. -- the per-group pseudo-register names defuse()/slice()
# def/use (see Image._ASTAT's docstring); built once here so Image doesn't
# need a class-body dict comprehension (which cannot see a sibling class
# attribute -- comprehensions get their own scope).
_ASTAT_GROUP_REG = {group: "ASTAT.%s" % group for group in _ASTAT_GROUPS}
_ASTAT_GROUP_BIT = {
    sharc_trace.AZ_BIT: "ALU",
    sharc_trace.AV_BIT: "ALU",
    sharc_trace.AN_BIT: "ALU",
    sharc_trace.AC_BIT: "ALU",
    sharc_trace.AS_BIT: "ALU",
    sharc_trace.AI_BIT: "ALU",
    sharc_trace.AF_BIT: "ALU",
    sharc_trace.MN_BIT: "MULT",
    sharc_trace.MV_BIT: "MULT",
    sharc_trace.MU_BIT: "MULT",
    sharc_trace.MI_BIT: "MULT",
    sharc_trace.SV_BIT: "SHIFT",
    sharc_trace.SZ_BIT: "SHIFT",
    sharc_trace.SS_BIT: "SHIFT",
    sharc_trace.BTF_BIT: "BTF",
}


def _astat_groups_written(t, f):
    """Which of _ASTAT_GROUPS aligned instruction (type_name t, its own
    merged decode fields f -- tools/sharc_dev/sharcinv.py's merge_fields() over the
    `insn.fields` this row's build already stored, never mnemonic text, per
    tools/sharc_dev/sharcdb/'s own Type3c rule) writes, cross-checked against
    tools/sharc_dev/sharc_core/{flags,compute_alu,compute_mult,
    compute_shift,compute_multi,forms_system}.py, which is where each
    of these actually gets applied:

      - A COMPUTE_FORMS instruction's parallel compute field: classified
        exactly the way tools/sharc_dev/sharcdb/'s own `_compute_regdef_reguse`
        does, through the same tools/sharc_dev/sharcinv.classify_compute() (PRM Table
        18-5/18-9/18-18/18-19 by the field's own cu/opcode bits) --
        'ALU'/'MULT'/'SHIFT' map directly; 'MULTIFN' (a MUL+ALU or
        MUL+dual-add/subtract multifunction op) always touches both ALU
        (the ALU sub-op's own flags, PRM's "flags from the ALU op") and
        MULT (compute_multi.py always also forgets MN/MV/MU/MI, since the
        multiplier result format is unmodelled for a multifunction op too)
        -- including a MULTIFN row tools/sharc_dev/sharcdb/ itself leaves as
        `unknown: multifn_alu_compute` for its DATA effect (the ALU sub-op's
        variable-width operand table isn't modelled there), since the flags
        effect is unconditional regardless of whether the data effect could
        be named.
      - Type2c's 12-bit short-compute field (never in COMPUTE_FORMS):
        opcode 0x7/0xF (mul_ssi/fmul) is MULT; every other opcode there
        (add/sub/pass/comp/not/inc/dec/fadd/fsub/float/fcomp) is ALU.
      - Type6a_mem's ShiftImm sub-instruction (PRM Table 18-9): always
        SHIFT for a recognised opcode (tools/sharc_dev/sharcfn._SHIFTIMM_MNEMONICS),
        including btst (register bit-test sets SZ, PRM p.513 -- an entirely
        different flag from Type18a's BTF below) and the "[status only]"
        case tools/sharc_dev/sharcdb/'s own `_shiftimm_regdef_reguse` leaves with no
        data regdef row at all.
      - Type18a's system bit-test/xor-test (bop 4/5, forms_system.py's
        `_type_18a`): BTF. tools/sharc_dev/sharcdb/'s `register_effects` records no
        regdef row for this case either (it only reads its source register
        into BTF, never writes one) -- the gap the frame-render survey
        found: a slice for a BTF-reading branch used to walk back through
        the block's last *unrelated* compute (any kind='compute' regdef, on
        the old whole-ASTAT rule) instead of the real Type18a bit-test.

    Returns () for a compute field this file/tools/sharc_dev/sharcdb/ doesn't model
    at all (field absent/zero, or an sharcinv.classify_compute() cu this
    table has no PRM-sourced group for -- 'CU3' -- or a Type6a_mem opcode
    outside _SHIFTIMM_MNEMONICS): left unmodelled, not guessed, exactly like
    tools/sharc_dev/sharcdb/'s own regdef/reguse `unknown` tags.
    """
    if t in sharcinv.COMPUTE_FORMS:
        field23 = f.get("compute")
        if not field23:
            return ()
        cu, _detail = sharcinv.classify_compute(field23)
        if cu == "ALU":
            return ("ALU",)
        if cu == "MULT":
            return ("MULT",)
        if cu == "SHIFT":
            return ("SHIFT",)
        if cu == "MULTIFN":
            return ("ALU", "MULT")
        return ()
    if t == "2c":
        field12 = f.get("compute")
        if field12 is None:
            return ()
        opcode = (field12 >> 8) & 0xF
        return ("MULT",) if opcode in (0x7, 0xF) else ("ALU",)
    if t == "6a_mem":
        field = f.get("shiftimm", 0) & 0x7FFFFF
        opcode = (field >> 16) & 0x3F
        if opcode not in sharcfn._SHIFTIMM_MNEMONICS:
            return ()
        return ("SHIFT",)
    if t == "18a":
        return ("BTF",) if f.get("bop") in (4, 5) else ()
    return ()


def _cond_astat_groups(cond):
    """Which _ASTAT_GROUPS a Type2a-family IF-cond field (PGR Table 10-4)
    reads: the same cond encoding, and the same PRM p.4-53 LT/GE/LE/GT
    special case, tools/sharc_dev/sharc_run.py's _fork_diagnosis() reads for a Halt
    message -- this returns group names for defuse()/slice() instead of the
    register/flag names _fork_diagnosis reports. MODE1 (also read by
    EQ/NE's PEYEN check and LT/GE/LE/GT's ALUSAT term) is a plain UREG, not
    an ASTATx bit group, so it is out of scope here -- defuse()/slice()
    already track a real UREG like MODE1 through its own ordinary reguse
    rows when one exists, same as before this function existed."""
    if cond is None or cond == sharcfn.ALWAYS_TRUE_COND:
        return ()
    if cond in (0x00, 0x10):  # EQ / NE: AZ
        return ("ALU",)
    if cond in (0x01, 0x02, 0x11, 0x12):  # LT / GE / LE / GT: AF, AN, AZ, AV
        return ("ALU",)
    bits = sharc_trace.SIMPLE_COND_BITS.get(cond)
    if bits is not None:
        bit, _negate = bits
        group = _ASTAT_GROUP_BIT.get(bit)
        if group is not None:
            return (group,)
    return ()


def load(
    name,
    sections_dir=SECTIONS_DIR,
    db_dir=DB_DIR,
    min_depth=8,
    blocks=None,
    force=False,
):
    """Open `db_dir`/<name>.sqlite, building (or rebuilding, if the blob's
    sha256 or tools/sharc_dev/sharcdb/'s DB_VERSION has moved on) from
    `sections_dir`/<name>/section_7_BLOB.bin first when needed. If the blob
    is gone but an up-to-date database is already on disk, that's fine --
    only a build or rebuild requires the blob."""
    blob_path = os.path.join(sections_dir, name, "section_7_BLOB.bin")
    out_path = os.path.join(db_dir, name + ".sqlite")

    stale = force or not os.path.exists(out_path)
    if not stale:
        meta = sharcdb.read_meta(out_path)
        if (
            meta.get("db_version") != str(sharcdb.DB_VERSION)
            or os.path.exists(blob_path)
            and meta.get("image_sha256") != sharcfn.sha256_of(blob_path)
        ):
            stale = True

    if stale:
        if not os.path.exists(blob_path):
            raise FileNotFoundError(
                "sharc.load(%r): %s is missing or stale and %s does not exist to rebuild it"
                % (name, out_path, blob_path)
            )
        sharcdb.build_database(
            blob_path,
            out_path,
            name=name,
            min_depth=min_depth,
            blocks=blocks,
            force=True,
        )

    return Image(name, out_path, blob_path)


class Image:
    """A queryable image: a handle on out/sharcdb/<name>.sqlite plus,
    lazily, the LoadedMemory needed for trace()/xref_table()."""

    def __init__(self, name, db_path, blob_path):
        self.name = name
        self.db_path = db_path
        self.blob_path = blob_path
        self.db = sqlite3.connect(db_path)
        self.meta = dict(
            self.db.execute(
                "SELECT key, value FROM meta WHERE image=?", (name,)
            ).fetchall()
        )
        self._mem_cache = None
        self._succ_cache = None
        self._notes_attached = False
        self._cross_cache = {}
        self._cfg_cache = {}
        self._callgraph_cache = None
        self._defuse_cache = {}

    def close(self):
        for other in self._cross_cache.values():
            if other is not None:
                other.close()
        self.db.close()

    # --- raw SQL ------------------------------------------------------------

    def sql(self, query, *args):
        return self.db.execute(query, args).fetchall()

    # --- functions and listings ----------------------------------------------

    def func(self, sw):
        """The function whose [entry_sw, end_sw) span contains sw, or None."""
        row = self.db.execute(
            "SELECT %s FROM functions WHERE image=? AND entry_sw<=? AND end_sw>?"
            % ",".join(_FUNC_COLUMNS),
            (self.name, sw, sw),
        ).fetchone()
        if row is None:
            return None
        d = dict(zip(_FUNC_COLUMNS, row, strict=True))
        d["entry_sw"], d["end_sw"] = _hex(d["entry_sw"]), _hex(d["end_sw"])
        return d

    def listing(self, sw_or_func, n=None):
        """Aligned (sw, mnemonic) pairs from sw_or_func to the end of its
        function, capped at n when given."""
        fn = self.func(sw_or_func)
        if fn is None:
            return []
        rows = self.db.execute(
            "SELECT sw, mnemonic FROM insn WHERE image=? AND aligned=1 AND sw>=? AND sw<? ORDER BY sw",
            (self.name, sw_or_func, int(fn["end_sw"], 16)),
        ).fetchall()
        if n is not None:
            rows = rows[:n]
        return [(_hex(sw), mnemonic) for sw, mnemonic in rows]

    # --- call graph -----------------------------------------------------------

    def callers(self, sw, kinds=None):
        """Every edge (of any kind, or only `kinds`) targeting sw -- a CALL,
        JUMP or COND_JUMP into the middle of a function all count, not just
        CALL (see tools/sharc_dev/sharcdb/'s module docstring)."""
        query = "SELECT kind, from_sw, from_function, cond, delayed, note FROM edges WHERE image=? AND to_sw=?"
        args = [self.name, sw]
        if kinds:
            query += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args += list(kinds)
        rows = self.db.execute(query, args).fetchall()
        return [
            {
                "kind": kind,
                "from_sw": _hex(from_sw),
                "from_function": _hex(from_function)
                if from_function is not None
                else None,
                "cond": cond,
                "delayed": delayed,
                "note": note,
            }
            for kind, from_sw, from_function, cond, delayed, note in rows
        ]

    def callees(self, func):
        rows = self.db.execute(
            "SELECT DISTINCT to_function FROM edges WHERE image=? AND from_function=? "
            "AND kind='call' AND to_function IS NOT NULL",
            (self.name, func),
        ).fetchall()
        return [_hex(r[0]) for r in rows]

    def roots(self):
        rows = self.db.execute(
            "SELECT sw, kind, note FROM roots WHERE image=? ORDER BY kind, sw",
            (self.name,),
        ).fetchall()
        return [{"sw": _hex(sw), "kind": kind, "note": note} for sw, kind, note in rows]

    # --- basic-block reachability ----------------------------------------------

    def _succ_graph(self):
        if self._succ_cache is None:
            g = nx.DiGraph()
            g.add_edges_from(
                self.db.execute(
                    "SELECT from_block, to_block FROM succ WHERE image=? AND to_block IS NOT NULL",
                    (self.name,),
                ).fetchall()
            )
            self._succ_cache = g
        return self._succ_cache

    def _block_at(self, sw):
        row = self.db.execute(
            "SELECT start_sw FROM bblocks WHERE image=? AND start_sw<=? AND end_sw>?",
            (self.name, sw, sw),
        ).fetchone()
        return row[0] if row else None

    def reach(self, src, dst):
        """(bool, path) over succ alone (bblocks/succ, no call-crossing --
        see sharcdb.sql's "across calls" query for that superset), path a
        list of hex block-start addresses or None."""
        g = self._succ_graph()
        b1, b2 = self._block_at(src), self._block_at(dst)
        if b1 is None or b2 is None or not nx.has_path(g, b1, b2):
            return False, None
        return True, [_hex(b) for b in nx.shortest_path(g, b1, b2)]

    # --- graphs (networkx layer) -------------------------------------------------
    #
    # cfg()/callgraph()/defuse() build small networkx graphs in-process from
    # tables tools/sharc_dev/sharcdb/ already fills (bblocks/succ/edges/regdef/
    # reguse/insn/mem_access/ptr/literals), the same reasoning that file's
    # own _detect_callgraph/_detect_dominators_and_loops already apply
    # internally at build time -- exposed here, cached per Image, so an
    # analysis question is a networkx call on an already-open image instead
    # of a fresh ad hoc SQL query or a throwaway script every time. Nothing
    # here is persisted: sharcdb.py's schema and DB_VERSION are untouched.

    def _bblocks_for(self, func):
        """[(start_sw, end_sw, n_insns), ...] for one function, sorted."""
        return self.db.execute(
            "SELECT start_sw, end_sw, n_insns FROM bblocks WHERE image=? AND function_sw=? ORDER BY start_sw",
            (self.name, func),
        ).fetchall()

    def cfg(self, func):
        """The basic-block CFG of one function: bblocks/succ restricted to
        it. Nodes are block start sws (attrs start/end/n_insns as hex/int,
        is_loop_header from `loops`); edges carry succ's own `kind`
        (fallthrough/jump/cond_taken/cond_not_taken/call_return/loop_back/
        loop_exit/return/indirect -- see sharcdb.py's SCHEMA comment on
        `succ`). Cached per function entry sw."""
        if func in self._cfg_cache:
            return self._cfg_cache[func]
        g = nx.DiGraph()
        for start, end, n_insns in self._bblocks_for(func):
            g.add_node(
                start,
                start=_hex(start),
                end=_hex(end),
                n_insns=n_insns,
                is_loop_header=False,
            )
        for from_block, to_block, kind in self.db.execute(
            "SELECT s.from_block, s.to_block, s.kind FROM succ s "
            "JOIN bblocks b ON b.image = s.image AND b.start_sw = s.from_block "
            "WHERE s.image=? AND b.function_sw=?",
            (self.name, func),
        ).fetchall():
            if to_block is not None and to_block in g.nodes:
                g.add_edge(from_block, to_block, kind=kind)
        for (header,) in self.db.execute(
            "SELECT header_block FROM loops WHERE image=? AND function_sw=?",
            (self.name, func),
        ).fetchall():
            if header in g.nodes:
                g.nodes[header]["is_loop_header"] = True
        self._cfg_cache[func] = g
        return g

    def callgraph(self):
        """The whole image's function-level CALL graph (edges.kind='call'),
        built once and cached -- the same construction cards() and
        tools/sharc_dev/sharcdb/'s own _detect_callgraph use, now shared instead of
        each building its own copy."""
        if self._callgraph_cache is not None:
            return self._callgraph_cache
        g = nx.DiGraph()
        g.add_nodes_from(
            r[0]
            for r in self.db.execute(
                "SELECT entry_sw FROM functions WHERE image=?", (self.name,)
            ).fetchall()
        )
        g.add_edges_from(
            self.db.execute(
                "SELECT DISTINCT from_function, to_function FROM edges WHERE image=? AND kind='call' "
                "AND from_function IS NOT NULL AND to_function IS NOT NULL",
                (self.name,),
            ).fetchall()
        )
        self._callgraph_cache = g
        return g

    # A family of pseudo-registers standing in for ASTATX/ASTATY: SCHEMA's
    # `regdef` has no flags entry at all (see sharcdb.py's own comment on
    # regdef.kind -- compute/mem_load/literal/move/swap/dag_modify/unknown,
    # never a condition-code register), because tools/sharc_dev/sharcdb/'s
    # register_effects never models one. But PGR Table 10-4's IF-cond field
    # (insn.cond, see sharcfn.COND_NAMES) reads ASTATX/ASTATY, and PRM:
    # every ALU/MULT/SHIFT compute updates its own group of those bits as a
    # side effect (see the module-level _astat_groups_written()/
    # _cond_astat_groups() docstrings above for exactly which). `_ASTAT`
    # ("ASTAT") is the old whole-register pseudo-register -- kept, and still
    # def'd/used alongside the per-group ones below, so a caller that wants
    # "anything that could have touched any flag" (e.g. a quick survey, or
    # code written before the per-group split) still gets that. `_ASTAT_GROUPS`
    # ("ALU"/"MULT"/"SHIFT"/"BTF", pseudo-register names "ASTAT.ALU" etc. --
    # see _ASTAT_GROUP_REG) are the precise ones: defuse()/slice() def each
    # group a compute/system-bit-test instruction actually writes (per
    # _astat_groups_written()), and use only the group(s) a conditional
    # instruction's own cond actually reads (per _cond_astat_groups()) --
    # so a slice for a BTF-reading branch (reg="ASTAT.BTF") walks back only
    # through a real Type18a bit-test/xor-test, never an unrelated ALU/MULT/
    # SHIFT compute that is architecturally unable to touch BTF (PRM: "BTF
    # is unaffected" by every ALU op -- see flags.py's _astatx_btst
    # docstring). An explicit move into ASTATX/ASTATY (Type5a_move/17a's
    # dstureg == ASTATX/ASTATY, already a real regdef row for that named
    # ureg) overwrites every bit at once, so it is treated as a def of every
    # group plus the whole-register pseudo-register, not just one.
    # Known gap: a compute this file's classifier does not recognise (an
    # sharcinv.classify_compute() cu of 'CU3', or a Type6a_mem opcode
    # outside tools/sharc_dev/sharcfn._SHIFTIMM_MNEMONICS) is not seen as any ASTAT
    # source; not silently declared correct, just not modelled -- see the
    # module docstring's "never from mnemonic text" rule, which rules out
    # falling back to scanning the rendered mnemonic instead.
    _ASTAT = "ASTAT"
    _ASTAT_GROUPS = _ASTAT_GROUPS
    _ASTAT_GROUP_REG = _ASTAT_GROUP_REG

    def _defuse_inputs(self, entry, end):
        """Bulk-fetch everything one defuse() build needs for [entry, end):
        ordered (sw, cond) per instruction, reg lists per sw for regdef/
        reguse, mem_access/literals rows for node attrs on a mem_load/
        literal def, and the ASTAT bit group(s) (see _astat_groups_written())
        each sw's own decoded form/fields write -- a handful of range-scan
        queries instead of one query per instruction. `insn`'s already-
        stored `form`/`fields` columns (the same typed decode
        tools/sharc_dev/sharcdb/'s own register_effects() used to build regdef/
        reguse) are reused here rather than re-decoding the image bytes."""
        insn_rows = self.db.execute(
            "SELECT sw, cond, mnemonic, form, fields FROM insn "
            "WHERE image=? AND function_sw=? AND aligned=1 ORDER BY sw",
            (self.name, entry),
        ).fetchall()
        insns = [
            (sw, cond, mnemonic) for sw, cond, mnemonic, _form, _fields in insn_rows
        ]
        astat_groups_by_sw = {}
        for sw, _cond, _mnemonic, form, fields_json in insn_rows:
            if form is None or fields_json is None:
                continue
            f = sharcinv.merge_fields(json.loads(fields_json))
            groups = _astat_groups_written(form, f)
            if groups:
                astat_groups_by_sw[sw] = groups
        regdef_by_sw = collections.defaultdict(list)
        for sw, reg, kind in self.db.execute(
            "SELECT sw, reg, kind FROM regdef WHERE image=? AND sw>=? AND sw<? ORDER BY sw",
            (self.name, entry, end),
        ).fetchall():
            regdef_by_sw[sw].append((reg, kind))
        reguse_by_sw = collections.defaultdict(list)
        for sw, reg in self.db.execute(
            "SELECT sw, reg FROM reguse WHERE image=? AND sw>=? AND sw<? ORDER BY sw",
            (self.name, entry, end),
        ).fetchall():
            reguse_by_sw[sw].append(reg)
        mem_by_sw = {}
        for (
            sw,
            space,
            direction,
            base_reg,
            modifier,
            width,
            abs_address,
        ) in self.db.execute(
            "SELECT sw, space, direction, base_reg, modifier, width, abs_address FROM mem_access "
            "WHERE image=? AND sw>=? AND sw<?",
            (self.name, entry, end),
        ).fetchall():
            mem_by_sw[sw] = {
                "space": space,
                "direction": direction,
                "base_reg": base_reg,
                "modifier": modifier,
                "width": width,
                "abs_address": _hex(abs_address) if abs_address is not None else None,
            }
        ptr_by_sw = {}
        for sw, base_reg, base_value, address, width in self.db.execute(
            "SELECT sw, base_reg, base_value, address, width FROM ptr WHERE image=? AND sw>=? AND sw<?",
            (self.name, entry, end),
        ).fetchall():
            ptr_by_sw[sw] = {
                "base_reg": base_reg,
                "base_value": _hex(base_value) if base_value is not None else None,
                "address": _hex(address) if address is not None else None,
                "width": width,
            }
        lit_by_sw = {}
        for sw, value, form in self.db.execute(
            "SELECT sw, value, form FROM literals WHERE image=? AND sw>=? AND sw<?",
            (self.name, entry, end),
        ).fetchall():
            lit_by_sw[sw] = {"value": _hex(value), "form": form}
        return (
            insns,
            regdef_by_sw,
            reguse_by_sw,
            mem_by_sw,
            ptr_by_sw,
            lit_by_sw,
            astat_groups_by_sw,
        )

    def _mem_label(self, sw, mem_by_sw, ptr_by_sw):
        """'record+0x1bc'-style text for a mem_load def at sw, from `ptr`
        when this pass resolved a concrete address, else the raw base/
        modifier `mem_access` recorded (see writers()/readers()'s own
        'resolved' vs 'base_only' distinction)."""
        ptr = ptr_by_sw.get(sw)
        if ptr and ptr["address"] is not None:
            return "%s @ %s" % (
                _describe_address(int(ptr["address"], 16)),
                ptr["address"],
            )
        mem = mem_by_sw.get(sw)
        if mem is None:
            return None
        if mem["abs_address"] is not None:
            return _describe_address(int(mem["abs_address"], 16))
        base = (
            ptr["base_value"]
            if ptr and ptr["base_value"] is not None
            else mem["base_reg"]
        )
        if mem["modifier"] is not None:
            return "%s(%s,%s)" % (mem["space"] or "DM", base, mem["modifier"])
        return "%s(%s)" % (mem["space"] or "DM", base)

    def defuse(self, func):
        """Intra-procedural reaching-definitions over one function's cfg():
        an nx.DiGraph whose nodes are instruction sws (attrs mnemonic, cond,
        def_kinds: the regdef kinds this sw sets) and whose edges are
        def -> use, labelled reg=<register, _ASTAT, or one of _ASTAT_GROUPS'
        "ASTAT.ALU"/"ASTAT.MULT"/"ASTAT.SHIFT"/"ASTAT.BTF">. Built as a
        standard forward reaching-definitions dataflow (gen/kill per
        instruction, meet=union, iterated to a fixpoint over cfg()'s cycles
        -- a hardware DO loop's loop_back edge is just another predecessor
        at that point, no special-casing needed) with two SHARC-specific
        gen/kill rules:

          - A conditionally-executed def (insn.cond real, not
            sharcfn.ALWAYS_TRUE_COND -- a Type2a-family IF-cond compute, a
            conditional memory form, or a hardware-loop DAG modify) is a
            may-def: it ADDS itself to the reaching set for that register
            rather than replacing it (an unconditional def replaces/kills).
            The same insn.cond also generates a use of the whole-register
            _ASTAT pseudo-register AND of the specific _ASTAT_GROUPS
            pseudo-register(s) that cond's own bit(s) read (see
            _cond_astat_groups()) at that sw, so a conditional branch's or
            store's condition source shows up in the graph like any other
            input -- and a slice by the precise group (reg="ASTAT.BTF")
            never drags in an unrelated compute's ALU/MULT/SHIFT flags, or
            vice versa.
          - Any instruction whose own decoded form/fields write one of
            _ASTAT_GROUPS (see _astat_groups_written(): every COMPUTE_FORMS
            compute, Type2c's short compute, Type6a_mem's ShiftImm, and
            Type18a's system bit-test/xor-test -- independent of whether
            regdef itself has a row there, which it does not for a
            "[status only]" ShiftImm or a bit-test/xor-test, both of which
            read a source into a flag but write no data register) is a def
            of that group's pseudo-register AND of _ASTAT. An explicit move
            into ASTATX/ASTATY (already an ordinary regdef row for that
            named ureg, kind 'move'/'literal') is a def of every group at
            once, since it overwrites the whole register.
          - A CALL site (edges.kind='call') has no regdef row of its own
            (register_effects() models no register effect for a call), so
            it is never a kill here either -- definitions from before a
            call reach after it unless this file's own decode later adds a
            call regdef; there is no general caller/callee-saved convention
            recorded for this ABI to apply instead (docs/findings/06 only
            pins down R4-as-argument and explicit save/restore spills, both
            already visible as ordinary regdef/reguse rows).

        A mem_load def's node also carries a `mem` attr (see _mem_label) and
        a literal def's a `literal` attr (its value), so a slice ending at
        one prints an address/value, not just a bare sw. Delayed-branch slot
        instructions need no special handling: sharcdb.py's bblocks already
        include them as ordinary sequential instructions of the block they
        end (see its SCHEMA comment on `bblocks`).

        Cached per function entry sw."""
        if func in self._defuse_cache:
            return self._defuse_cache[func]
        fn = self.func(func)
        if fn is None or int(fn["entry_sw"], 16) != func:
            raise ValueError("defuse(): 0x%x is not a function entry" % func)
        entry, end = func, int(fn["end_sw"], 16)
        cfg = self.cfg(entry)
        (
            insns,
            regdef_by_sw,
            reguse_by_sw,
            mem_by_sw,
            ptr_by_sw,
            lit_by_sw,
            astat_groups_by_sw,
        ) = self._defuse_inputs(entry, end)
        starts = sorted(b for b in cfg.nodes)
        insns_by_block = collections.defaultdict(list)
        for sw, cond, _m in insns:
            idx = bisect.bisect_right(starts, sw) - 1
            block = starts[idx] if idx >= 0 else starts[0]
            insns_by_block[block].append((sw, cond))

        def is_conditional(cond):
            return cond is not None and cond != sharcfn.ALWAYS_TRUE_COND

        def transfer(block, in_state):
            """in_state/out_state: {reg: frozenset(def_sw)}. Also returns
            the def -> use edges this block generates given in_state (a
            pure function of (block, in_state), recomputed fresh -- never
            mutated across fixpoint iterations)."""
            state = dict(in_state)
            edges = []
            unresolved = collections.defaultdict(set)
            for sw, cond in insns_by_block.get(block, ()):
                cond_here = is_conditional(cond)
                used = list(reguse_by_sw.get(sw, ()))
                if cond_here:
                    used = used + [self._ASTAT]
                    used = used + [
                        self._ASTAT_GROUP_REG[group]
                        for group in _cond_astat_groups(cond)
                    ]
                for reg in used:
                    writers = state.get(reg)
                    if writers:
                        for w in writers:
                            edges.append((w, sw, reg))
                    else:
                        unresolved[sw].add(reg)
                defined = list(regdef_by_sw.get(sw, ()))
                groups = set(astat_groups_by_sw.get(sw, ()))
                if any(reg in ("ASTATX", "ASTATY") for reg, _kind in defined):
                    groups |= set(self._ASTAT_GROUPS)
                if groups:
                    defined = defined + [(self._ASTAT, "flags")]
                    defined = defined + [
                        (self._ASTAT_GROUP_REG[group], "flags")
                        for group in sorted(groups)
                    ]
                for reg, _kind in defined:
                    if cond_here:
                        state[reg] = state.get(reg, frozenset()) | {sw}
                    else:
                        state[reg] = frozenset((sw,))
            return state, edges, unresolved

        # Standard worklist fixpoint: IN[b] = union of OUT[pred] per reg;
        # monotonic (gen/kill fixed per instruction, meet=union) over a
        # finite domain (this function's own instruction sws), so this
        # always terminates.
        RegState = dict[str, frozenset]
        IN: dict[int, RegState] = {b: {} for b in cfg.nodes}
        OUT: dict[int, RegState] = {b: {} for b in cfg.nodes}
        order = list(cfg.nodes)
        worklist = collections.deque(order)
        in_queue = set(order)
        guard = 0
        max_iters = 200 * (len(order) + 1)
        while worklist:
            guard += 1
            if guard > max_iters:
                raise RuntimeError(
                    "defuse(): fixpoint did not converge for 0x%x after %d iterations"
                    % (func, guard)
                )
            b = worklist.popleft()
            in_queue.discard(b)
            preds = list(cfg.predecessors(b))
            new_in: RegState = {}
            for p in preds:
                for reg, sws in OUT[p].items():
                    new_in[reg] = new_in.get(reg, frozenset()) | sws
            if new_in != IN[b]:
                IN[b] = new_in
            new_out, _edges, _unresolved = transfer(b, IN[b])
            if new_out != OUT[b]:
                OUT[b] = new_out
                for s in cfg.successors(b):
                    if s not in in_queue:
                        in_queue.add(s)
                        worklist.append(s)

        g = nx.DiGraph()
        for sw, cond, mnemonic in insns:
            g.add_node(sw, mnemonic=mnemonic, cond=cond, def_kinds=[])
        for block in order:
            _out, edges, unresolved = transfer(block, IN[block])
            for w, sw, reg in edges:
                if g.has_edge(w, sw):
                    if reg not in g.edges[w, sw]["regs"]:
                        g.edges[w, sw]["regs"].append(reg)
                else:
                    g.add_edge(w, sw, regs=[reg])
            for sw, regs in unresolved.items():
                g.nodes[sw].setdefault("unresolved", set()).update(regs)
            for sw, _cond in insns_by_block.get(block, ()):
                for reg, kind in regdef_by_sw.get(sw, ()):
                    g.nodes[sw]["def_kinds"].append((reg, kind))
                    if kind == "mem_load":
                        label = self._mem_label(sw, mem_by_sw, ptr_by_sw)
                        if label:
                            g.nodes[sw]["mem"] = label
                    elif kind == "literal" and sw in lit_by_sw:
                        g.nodes[sw]["literal"] = lit_by_sw[sw]["value"]

        self._defuse_cache[func] = g
        return g

    def slice(self, sw, reg=None, depth=None):
        """Backward data/flag slice ending at sw: the sub-nx.DiGraph of
        defuse() reachable backward from sw, seeded by sw's own def -> use
        in-edges (all of them, or only `reg`'s -- pass reg=self._ASTAT, or
        just reg='ASTAT', for the old whole-register slice; reg="ASTAT.ALU"/
        "ASTAT.MULT"/"ASTAT.SHIFT"/"ASTAT.BTF" (self._ASTAT_GROUP_REG['ALU'/
        'MULT'/'SHIFT'/'BTF']) for the precise group the branch's own cond
        actually reads -- e.g. a BTF-reading branch's slice(reg="ASTAT.BTF")
        walks back only to a real Type18a bit-test/xor-test, not the last
        unrelated ALU/MULT/SHIFT compute in the block, which is
        architecturally unable to touch BTF), then closed over every
        ancestor's OWN inputs regardless of register (once you are at the
        instruction that produced a value, everything that fed it matters,
        not just the one edge that led there). `depth` bounds the number of
        def-edge hops walked backward from the seed frontier (None:
        unbounded, i.e. the whole reaching slice)."""
        fn = self.func(sw)
        if fn is None:
            raise ValueError("slice(): no function contains sw=0x%x" % sw)
        entry = int(fn["entry_sw"], 16)
        g = self.defuse(entry)
        if sw not in g:
            raise ValueError(
                "slice(): sw=0x%x has no defuse node (not an aligned instruction?)" % sw
            )
        seed_edges = [(u, d) for u, _v, d in g.in_edges(sw, data=True)]
        if reg is not None:
            seed_edges = [(u, d) for u, d in seed_edges if reg in d.get("regs", ())]
        frontier = {u for u, _d in seed_edges}
        visited = set(frontier) | {sw}
        q = collections.deque((u, 1) for u in frontier)
        while q:
            node, hop = q.popleft()
            if depth is not None and hop >= depth:
                continue
            for u, _v, _d in g.in_edges(node, data=True):
                if u not in visited:
                    visited.add(u)
                    q.append((u, hop + 1))
        return g.subgraph(visited).copy()

    def print_slice(self, sw, reg=None, depth=None):
        """Text render of slice(sw, reg, depth): one line per instruction
        (sw, mnemonic, a mem/literal address suffix when the defuse node
        carries one, an 'unresolved: REG' note for a use with no reaching
        def in this function -- a parameter or an external value), each
        line indented under the def(s) that feed it, walked depth-first
        from sw. A node already printed in full is shown again (dependents
        can share an ancestor) but not re-expanded, marked '(...)'."""
        g = self.slice(sw, reg=reg, depth=depth)
        lines = []
        expanded = set()

        def line_for(n):
            d = g.nodes[n]
            bits = ["0x%x" % n, d.get("mnemonic") or "?"]
            if d.get("mem"):
                bits.append("[%s]" % d["mem"])
            if d.get("literal") is not None:
                bits.append("[literal=%s]" % d["literal"])
            if d.get("unresolved"):
                bits.append("[unresolved: %s]" % ",".join(sorted(d["unresolved"])))
            return "  ".join(bits)

        def visit(n, indent, via_reg):
            prefix = "  " * indent + ("<- [%s] " % via_reg if via_reg else "")
            if n in expanded:
                lines.append(prefix + line_for(n) + "  (...)")
                return
            expanded.add(n)
            lines.append(prefix + line_for(n))
            preds = sorted(
                (
                    (u, r)
                    for u, _v, d in g.in_edges(n, data=True)
                    for r in d.get("regs", ())
                ),
                key=lambda t: (-t[0], t[1]),
            )
            for u, r in preds:
                visit(u, indent + 1, r)

        visit(sw, 0, None)
        text = "\n".join(lines)
        print(text)
        return text

    # --- registers and data references ------------------------------------------

    def last_def(self, reg, sw):
        """The last writer(s) of reg before sw. A bare int when there is one
        unambiguous writer, a list when several distinct blocks disagree,
        None when there is none.

        A cheaper, less precise sibling of defuse(): a block-granularity
        "does any regdef of reg exist between here and there" walk, not a
        flow-sensitive dataflow -- it has no notion of a conditional def
        (see defuse()'s docstring on may-defs), so a block containing one is
        still treated as a hard stop that kills every earlier reaching def
        on that path. Cross-checked against defuse() over every (use sw,
        reg) pair in FUN_1c4f81/FUN_1c642a/FUN_1c2b24 (2526 pairs, 2026-09):
        95% agree exactly; all 129 disagreements are this gap, split
        ~roughly 60/40 into last_def missing an older def a may-def kept
        alive for defuse (its result a subset of defuse's reaching set) and
        last_def keeping a block-level candidate defuse's per-instruction
        analysis determines is shadowed before it actually reaches sw (a
        superset, or overlapping-but-different, of defuse's set) -- never a
        case where last_def found nothing defuse could. For a precise
        answer, or when reg's conditionality matters, use defuse()/slice()
        instead."""
        rows = self.db.execute(
            _LAST_DEF_SQL,
            (
                sw,
                self.name,
                sw,
                sw,
                self.name,
                self.name,
                self.name,
                self.name,
                reg,
                self.name,
                self.name,
                reg,
            ),
        ).fetchall()
        writers = sorted(r[0] for r in rows if r[0] is not None)
        if not writers:
            return None
        return writers[0] if len(writers) == 1 else writers

    def uses(self, reg, func):
        fn = self.func(func)
        if fn is None:
            return []
        rows = self.db.execute(
            "SELECT sw FROM reguse WHERE image=? AND reg=? AND sw>=? AND sw<? ORDER BY sw",
            (self.name, reg, int(fn["entry_sw"], 16), int(fn["end_sw"], 16)),
        ).fetchall()
        return [_hex(r[0]) for r in rows]

    def refs(self, addr):
        rows = self.db.execute(
            "SELECT sw, form, role FROM dataref WHERE image=? AND value=? ORDER BY sw",
            (self.name, addr),
        ).fetchall()
        return [{"sw": _hex(sw), "form": form, "role": role} for sw, form, role in rows]

    def _ptr_hits(self, addr, size, direction):
        """Every tools/sharc_dev/sharcdb/ `ptr` row for `direction` whose resolved
        [address, address+width) range overlaps [addr, addr+size) ('kind':
        'resolved'), plus every row whose base is known but whose address
        isn't (an indexed I,M form with an untracked modifier) and whose
        base sits within 0x10000 bytes below addr ('kind': 'base_only') --
        a plausible hit for a runtime-indexed struct/array write this static
        pass can't fully resolve, not a proof. See tools/sharc_dev/sharcdb/'s
        _build_ptr_rows module note for what the `ptr` table does and does
        not cover."""
        hi = addr + size
        rows = self.db.execute(
            "SELECT sw, base_reg, base_value, address, width, "
            "  CASE WHEN address IS NOT NULL THEN 'resolved' ELSE 'base_only' END kind "
            "FROM ptr WHERE image=? AND direction=? AND ("
            "  (address IS NOT NULL AND address < ? AND address + (CASE width WHEN 'long' THEN 8 ELSE 4 END) > ?)"
            "  OR (address IS NULL AND base_value IS NOT NULL AND base_value <= ? AND ? - base_value < 0x10000)"
            ") ORDER BY sw",
            (self.name, direction, hi, addr, addr, addr),
        ).fetchall()
        return [
            {
                "sw": _hex(sw),
                "base_reg": base_reg,
                "base_value": _hex(base_value) if base_value is not None else None,
                "address": _hex(address) if address is not None else None,
                "width": width,
                "kind": kind,
            }
            for sw, base_reg, base_value, address, width, kind in rows
        ]

    def writers(self, addr, size=4):
        """Every store tools/sharc_dev/sharcdb/'s constant-pointer propagation (the
        `ptr` table) can place at or partly inside [addr, addr+size)."""
        return self._ptr_hits(addr, size, "store")

    def readers(self, addr, size=4):
        """The same, for a load."""
        return self._ptr_hits(addr, size, "load")

    def xref_table(self, addr, n):
        """Read n consecutive 32-bit little-endian loader words from addr as
        code pointers (the pattern behind roots.kind='code_pointer_array'),
        resolving each to its owning function when there is one."""
        mem = self._mem()
        out = []
        for i in range(n):
            raw = mem.read(addr + i * 4, 4)
            if raw is None:
                break
            value = int.from_bytes(raw, "little")
            fn = self.func(value)
            out.append(
                {
                    "index": i,
                    "addr": _hex(addr + i * 4),
                    "value": _hex(value),
                    "function": fn["entry_sw"] if fn else None,
                }
            )
        return out

    # --- notes ----------------------------------------------------------------
    #
    # A human/agent-written note per function, kept in a sidecar database
    # (out/sharcdb/<name>.notes.sqlite, ATTACHed here on first use) rather
    # than in the main .sqlite: tools/sharc_dev/sharcdb/'s build_database() always
    # replaces out_path wholesale (see its own "build into a temp file" note),
    # so a note living there would be wiped by the next `sharcdb build
    # --force`. This file never touches the sidecar's path.

    def _ensure_notes_db(self):
        if self._notes_attached:
            return
        notes_path = os.path.join(
            os.path.dirname(os.path.abspath(self.db_path)), self.name + ".notes.sqlite"
        )
        self.db.execute("ATTACH DATABASE ? AS notesdb", (notes_path,))
        self.db.execute("""CREATE TABLE IF NOT EXISTS notesdb.notes (
            image TEXT, function_sw INTEGER, role TEXT, summary TEXT, reads TEXT,
            writes TEXT, confidence TEXT, evidence TEXT, author TEXT, created TEXT,
            PRIMARY KEY (image, function_sw))""")
        self._notes_attached = True

    _NOTE_FIELDS = (
        "role",
        "summary",
        "reads",
        "writes",
        "confidence",
        "evidence",
        "author",
    )

    def note(self, fn, **fields):
        """Upsert this image's note for function `fn` in the sidecar
        database. fields: any of role/summary/reads/writes/confidence/
        evidence/author; a field left out keeps its previous value (or NULL,
        for a brand new row). `created` is set to now on first write and left
        alone on an update, unless passed explicitly."""
        self._ensure_notes_db()
        existing = self.notes(fn)
        prior = existing[0] if existing else {}
        row = {f: fields.get(f, prior.get(f)) for f in self._NOTE_FIELDS}
        created = (
            fields.get("created")
            or prior.get("created")
            or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        )
        self.db.execute(
            "INSERT INTO notesdb.notes (image, function_sw, role, summary, reads, writes, confidence, "
            "evidence, author, created) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(image, function_sw) DO UPDATE SET role=excluded.role, summary=excluded.summary, "
            "reads=excluded.reads, writes=excluded.writes, confidence=excluded.confidence, "
            "evidence=excluded.evidence, author=excluded.author, created=excluded.created",
            (
                self.name,
                fn,
                row["role"],
                row["summary"],
                row["reads"],
                row["writes"],
                row["confidence"],
                row["evidence"],
                row["author"],
                created,
            ),
        )
        self.db.commit()

    def notes(self, fn=None):
        """This image's note for `fn` (a single-element list, or [] when
        none exists), or every note in the image when fn is None."""
        self._ensure_notes_db()
        cols = (
            "function_sw",
            "role",
            "summary",
            "reads",
            "writes",
            "confidence",
            "evidence",
            "author",
            "created",
        )
        query = "SELECT %s FROM notesdb.notes WHERE image=?" % ",".join(cols)
        args = [self.name]
        if fn is not None:
            query += " AND function_sw=?"
            args.append(fn)
        query += " ORDER BY function_sw"
        rows = self.db.execute(query, args).fetchall()
        out = [dict(zip(cols, r, strict=True)) for r in rows]
        for d in out:
            d["function_sw"] = _hex(d["function_sw"])
        return out

    # --- function cards ---------------------------------------------------------

    def _cross_partner(self):
        """The Image for this one's cross-image partner (see
        _CROSS_IMAGE_PARTNER), lazily loaded and cached, or None when there
        is no known partner or its database isn't built."""
        if self.name in self._cross_cache:
            return self._cross_cache[self.name]
        other_name = _CROSS_IMAGE_PARTNER.get(self.name)
        other = None
        if other_name:
            try:
                other = load(other_name)
            except Exception:
                other = None
        self._cross_cache[self.name] = other
        return other

    def card(self, fn, listing_n=8):
        """A deterministic, compact text card for one function: header,
        roots reaching it, callers/callees (with any note's role/summary),
        literals and datarefs, resolved memory reads/writes grouped by
        known structure (see _describe_address), loops, float-compute
        density, a cross-image match, and a listing (full when short,
        otherwise the first/last `listing_n` instructions plus every
        branch/call/return/indirect/store line in between)."""
        f = self.func(fn)
        if f is None:
            return "no function at 0x%x" % fn
        entry, end = int(f["entry_sw"], 16), int(f["end_sw"], 16)
        L = []
        L.append(
            "FUN_%06x  entry=0x%x end=0x%x  n_insns=%d  bytes=%d"
            % (entry, entry, end, f["n_insns"], (end - entry) * 2)
        )
        bits = ["block=%s" % f["block"], "leaf" if f["is_leaf"] else "non-leaf"]
        if f["name"] and f["name"] != "FUN_%06x" % entry:
            bits.append("name=%s" % f["name"])
        if f["label"]:
            bits.append("label=%s" % f["label"])
        if f["entry_kind"]:
            bits.append("entry_kind=%s" % f["entry_kind"])
        L.append("  " + " ".join(bits))

        own_note = self.notes(entry)
        if own_note:
            n = own_note[0]
            L.append("note: role=%s  %s" % (n.get("role"), n.get("summary") or ""))

        root_rows = self.db.execute(
            "SELECT r.kind, MIN(rc.depth) FROM reach rc JOIN roots r "
            "ON r.image = rc.image AND r.sw = rc.root_sw "
            "WHERE rc.image=? AND rc.function_sw=? GROUP BY r.kind ORDER BY 2",
            (self.name, entry),
        ).fetchall()
        L.append(
            "roots: "
            + (
                ", ".join("%s(depth=%s)" % (k, d) for k, d in root_rows)
                if root_rows
                else "none reach this function"
            )
        )

        def _role_suffix(fn_hex):
            if fn_hex is None:
                return ""
            rows = self.notes(int(fn_hex, 16))
            if not rows or not rows[0].get("summary"):
                return ""
            return " -- %s: %s" % (rows[0].get("role") or "?", rows[0]["summary"])

        callers = self.callers(entry)
        seen, caller_bits = set(), []
        for c in callers:
            key = (c["from_function"], c["kind"])
            if key in seen:
                continue
            seen.add(key)
            caller_bits.append(
                "%s(%s)%s"
                % (
                    c["from_function"] or c["from_sw"],
                    c["kind"],
                    _role_suffix(c["from_function"]),
                )
            )
        L.append(
            "callers (%d): %s" % (len(caller_bits), "; ".join(caller_bits) or "none")
        )

        callees = self.callees(entry)
        L.append(
            "callees (%d): %s"
            % (
                len(callees),
                "; ".join("%s%s" % (c, _role_suffix(c)) for c in callees) or "none",
            )
        )

        lit_rows = self.db.execute(
            "SELECT DISTINCT l.value, l.form, l.dest_reg FROM literals l JOIN insn i "
            "ON i.image = l.image AND i.sw = l.sw WHERE l.image=? AND i.function_sw=? ORDER BY l.value",
            (self.name, entry),
        ).fetchall()
        if lit_rows:
            shown = [
                "0x%x(%s%s)" % (v, form, "->" + dr if dr else "")
                for v, form, dr in lit_rows[:20]
            ]
            more = "" if len(lit_rows) <= 20 else " ... +%d more" % (len(lit_rows) - 20)
            L.append("literals (%d): %s%s" % (len(lit_rows), ", ".join(shown), more))

        dataref_rows = self.db.execute(
            "SELECT DISTINCT d.value, d.role FROM dataref d WHERE d.image=? AND d.sw>=? AND d.sw<? ORDER BY d.value",
            (self.name, entry, end),
        ).fetchall()
        if dataref_rows:
            L.append(
                "datarefs: "
                + ", ".join(
                    "%s(%s)" % (_describe_address(v), role)
                    for v, role in dataref_rows[:20]
                )
            )

        mem_rows = self.db.execute(
            "SELECT direction, address, base_value FROM ptr WHERE image=? AND sw>=? AND sw<?",
            (self.name, entry, end),
        ).fetchall()
        grouped: collections.defaultdict[str, list[int]] = collections.defaultdict(
            lambda: [0, 0]
        )
        for direction, address, base_value in mem_rows:
            label = _describe_address(address if address is not None else base_value)
            grouped[label][0 if direction == "load" else 1] += 1
        if grouped:
            L.append(
                "memory: "
                + "; ".join(
                    "%s(r=%d,w=%d)" % (label, r, w)
                    for label, (r, w) in sorted(grouped.items())
                )
            )

        loop_rows = self.db.execute(
            "SELECT header_block, n_blocks, kind, depth FROM loops WHERE image=? AND function_sw=? ORDER BY header_block",
            (self.name, entry),
        ).fetchall()
        if loop_rows:
            L.append(
                "loops: "
                + "; ".join(
                    "0x%x(%s,n_blocks=%d,depth=%d)" % (h, k, n, d)
                    for h, n, k, d in loop_rows
                )
            )

        total = self.db.execute(
            "SELECT COUNT(*) FROM regdef WHERE image=? AND sw>=? AND sw<? AND kind='compute'",
            (self.name, entry, end),
        ).fetchone()[0]
        if total:
            floaty = self.db.execute(
                "SELECT COUNT(*) FROM regdef WHERE image=? AND sw>=? AND sw<? AND kind='compute' AND reg LIKE 'F%'",
                (self.name, entry, end),
            ).fetchone()[0]
            L.append(
                "float-compute density: %.0f%% (%d/%d compute regdefs)"
                % (100.0 * floaty / total, floaty, total)
            )

        other = self._cross_partner()
        if other is not None:
            matches = self.match(other, entry)
            L.append(
                "cross-image (%s): %s"
                % (
                    other.name,
                    ", ".join(
                        "%s%s"
                        % (m["entry_sw"], "" if m["exact_match"] else "(reloc-only)")
                        for m in matches
                    )
                    if matches
                    else "no match",
                )
            )

        all_rows = self.db.execute(
            "SELECT sw, mnemonic FROM insn WHERE image=? AND function_sw=? AND aligned=1 ORDER BY sw",
            (self.name, entry),
        ).fetchall()
        n = listing_n
        L.append(
            "listing (%d insns%s):"
            % (len(all_rows), "" if len(all_rows) <= 2 * n + 4 else ", truncated")
        )
        if len(all_rows) <= 2 * n + 4:
            L += ["  0x%x  %s" % (sw, m) for sw, m in all_rows]
        else:
            # Branch/call/return/indirect sites are kept in full (there are
            # rarely more than a handful); store sites are capped so one
            # store-heavy dispatcher (e.g. FUN_1c642a) doesn't blow the
            # card's whole size budget -- see the module note on card()'s
            # 1-3k token target.
            control_flow = set(
                r[0]
                for r in self.db.execute(
                    "SELECT DISTINCT from_sw FROM edges WHERE image=? AND from_sw>=? AND from_sw<? "
                    "AND kind IN ('call','jump','cond_jump','indirect','return')",
                    (self.name, entry, end),
                ).fetchall()
            )
            stores = set(
                r[0]
                for r in self.db.execute(
                    "SELECT DISTINCT sw FROM mem_access WHERE image=? AND sw>=? AND sw<? AND direction='store'",
                    (self.name, entry, end),
                ).fetchall()
            )
            head, tail = all_rows[:n], all_rows[-n:]
            edge_sws = {sw for sw, _ in head} | {sw for sw, _ in tail}
            store_cap = 40
            mid_stores = [
                (sw, m) for sw, m in all_rows if sw not in edge_sws and sw in stores
            ]
            omitted_stores = max(0, len(mid_stores) - store_cap)
            mid_stores = mid_stores[:store_cap]
            mid = sorted(
                {
                    (sw, m)
                    for sw, m in all_rows
                    if sw not in edge_sws and sw in control_flow
                }
                | set(mid_stores)
            )
            L += ["  0x%x  %s" % (sw, m) for sw, m in head]
            L.append("  ...")
            L += ["  0x%x  %s" % (sw, m) for sw, m in mid]
            if omitted_stores:
                L.append("  ... (+%d more store lines omitted)" % omitted_stores)
            L.append("  ...")
            L += ["  0x%x  %s" % (sw, m) for sw, m in tail]

        return "\n".join(L)

    def cards(self, order="bottom_up", root=None, limit=None):
        """Yield card() text for every function in the image (or, with
        `root`, only those in `reach` from that root sw or sws), in
        callee-first ('bottom_up', the default) or caller-first
        ('top_down') topological order over the CALL-edge graph (SCC-
        condensed, same construction as tools/sharc_dev/sharcdb/'s
        _detect_callgraph, so a recursive cluster is yielded as one group)."""
        if order not in ("bottom_up", "top_down"):
            raise ValueError(
                "cards(): order must be 'bottom_up' or 'top_down', got %r" % order
            )
        G = self.callgraph()
        if root is not None:
            roots = root if isinstance(root, (list, tuple, set)) else [root]
            keep = set()
            for r in roots:
                keep |= {
                    row[0]
                    for row in self.db.execute(
                        "SELECT function_sw FROM reach WHERE image=? AND root_sw=?",
                        (self.name, r),
                    ).fetchall()
                }
            G = G.subgraph(keep).copy()

        C = nx.condensation(G)
        topo = list(nx.topological_sort(C))
        if order == "bottom_up":
            topo.reverse()
        count = 0
        for c in topo:
            for entry in sorted(C.nodes[c]["members"]):
                if limit is not None and count >= limit:
                    return
                yield self.card(entry)
                count += 1

    # --- cross-image ------------------------------------------------------------

    def match(self, other_img, func):
        """Functions in other_img matching func by relocation-tolerant
        hash, with whether the match is exact-byte too."""
        row = self.db.execute(
            "SELECT exact_hash, reloc_hash FROM func_hash WHERE image=? AND entry_sw=?",
            (self.name, func),
        ).fetchone()
        if row is None:
            return []
        exact_hash, reloc_hash = row
        rows = other_img.db.execute(
            "SELECT entry_sw, exact_hash FROM func_hash WHERE image=? AND reloc_hash=?",
            (other_img.name, reloc_hash),
        ).fetchall()
        return [
            {"entry_sw": _hex(entry_sw), "exact_match": exact_hash == other_exact}
            for entry_sw, other_exact in rows
        ]

    # --- symbolic trace -----------------------------------------------------------

    def _mem(self):
        if self._mem_cache is None:
            with open(self.blob_path, "rb") as fh:
                data = fh.read()
            blocks = sharcldr.parse_blocks(data)
            self._mem_cache = sharcldr.LoadedMemory.from_stream(data, blocks)
        return self._mem_cache

    def trace(
        self,
        start,
        max_steps=100,
        max_states=32,
        pokes=None,
        provisional_forms=(),
        **regs,
    ):
        """tools/sharc_dev/sharc_trace.py's trace(), in-process against this image's
        loaded memory: firmware DAG-modify constants seeded by default
        (override any of them, or add more, via **regs), concrete memory
        and 32-bit-normal-word addressing on by default. provisional_forms
        is passed straight through (e.g. ("14d",) to trace through Type14d).
        Raises NotImplementedError for a ColdFire image (see sharc.load's
        module docstring): sharc_trace only symbolically executes SHARC+."""
        if self.meta.get("cpu") == "coldfire":
            raise NotImplementedError(
                "Image.trace(): %r is a ColdFire image; sharc_trace only symbolically "
                "executes SHARC+ code, not m68k/ColdFire" % self.name
            )
        sets = dict(_DEFAULT_TRACE_REGS)
        sets.update(regs)
        return sharc_trace.trace(
            self._mem(),
            None,
            start,
            sets,
            max_steps,
            max_states,
            concrete_memory=True,
            assume_nw32=True,
            pokes=pokes,
            provisional_forms=provisional_forms,
        )


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) >= 2 and argv[1] == "--slice":
        if len(argv) < 3:
            print(
                "usage: sharc.py IMAGE --slice SW [--reg REG] [--depth N]",
                file=sys.stderr,
            )
            return 1
        img = load(argv[0])
        sw = int(argv[2], 0)
        reg, depth = None, None
        rest = argv[3:]
        i = 0
        while i < len(rest):
            if rest[i] == "--reg" and i + 1 < len(rest):
                reg = rest[i + 1]
                i += 2
            elif rest[i] == "--depth" and i + 1 < len(rest):
                depth = int(rest[i + 1])
                i += 2
            else:
                print(
                    "usage: sharc.py IMAGE --slice SW [--reg REG] [--depth N]",
                    file=sys.stderr,
                )
                return 1
        img.print_slice(sw, reg=reg, depth=depth)
        return 0
    if len(argv) != 2:
        print('usage: sharc.py IMAGE "SQL"', file=sys.stderr)
        print(
            "       sharc.py IMAGE --slice SW [--reg REG] [--depth N]", file=sys.stderr
        )
        return 1
    img = load(argv[0])
    for row in img.sql(argv[1]):
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
