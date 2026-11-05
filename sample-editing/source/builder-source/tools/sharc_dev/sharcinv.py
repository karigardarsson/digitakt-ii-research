"""Classify every function in the SHARC+ DSP code: boundaries, call graph and
a compute-op feature vector, with a conservative heuristic label.

    uv run python -m tools.sharc_dev.sharcinv SECTION7.bin
        [--blocks 56,69,76,78,80,88,91,93] [--min-depth N]
        [--json OUT] [--top N] [--ground-truth]

SECTION7.bin is the raw SHARC DSP boot stream (out/sections/dt2-1.16/
section_7_BLOB.bin). It is parsed with tools/sharc_dev/sharcldr.py; --blocks defaults
to the code blocks identified in docs/findings/06-sharc-engine-and-startup.md
("The SHARC code is not one block"): blk69 (0x20000000, 109436 B) and blk93
(0x283827cc, 104500 B) are the two big code regions, blk88 (0x28380548,
8484 B) and 56/76/78/80/91 are small code fragments. blk1 (0x282403f0,
10312 B) was in this list through DB_VERSION v12 but is dead in the final
loaded image (see CODE_BLOCKS above and docs/findings/05-sharc-isa-and-
decoding.md, "One decode path") and is no longer scanned as code. The rest
of the blob (17/19/21/27/35/37/39/40/99/101) is data and is not scanned.

Boundaries: tools/sharc_dev/sharcflow.py finds returns (a 9b_abs jump through M5/I4,
raw 0x083F343F) and direct calls (25a_direct/25a_pcrel with a push+store in
their delay slots) over the depth-filtered aligned instruction stream
(tools/sharc_dev/sharcimm.py). A function runs from just after one return to the
next -- but a callee can sit physically inside another routine's span with
no return of its own immediately before it: verified on blk93, 0x1c1338's
frame reader calls 0x1c24e9 (a 396-instruction parameter converter) from a
call site itself embedded inside a bigger return-delimited span, with no
intervening return. So every direct-call target that falls strictly inside
a return-delimited span also opens a new function there. This two-pass
scheme reproduces all ten hand-read ground-truth entries' boundaries
exactly except one (0x1c71ec: 235 instructions found vs. 251 hand-counted,
because 0x1c7442, embedded 16 instructions before its old end, is itself an
external call target and gets split out -- see --ground-truth). Every such
split is recorded on both halves (fn['entry_kind'] == 'interior_call_target'
on the carved-out callee, fn['boundary_note'] on both sides) so a reader
comparing n_insns against a hand count sees why they differ instead of just
a mismatched number. A return-delimited span that decodes zero instructions
(e.g. the header gap before a block's first real instruction) is dropped
rather than emitted as a 0-instruction function.

Feature vectors are counted from the decoded, merged instruction fields
(e.g. data[31:16]/data[15:0] -> one 'data' value), classified with the
opcode tables in tools/sharc_dev/sharcspec/compute_table.json rather than re-derived:
aluop_32_40bit (PRM Table 18-5, cross-checked against PGR Table 12-3/12-4,
0 conflicts), mulop_32_40bit (PRM Table 18-7 / PGR Table 12-5),
shiftop_shiftimm (PRM Table 18-9 / PGR Table 12-11), shortcompute (PRM
Table 18-2 / PGR "Short Compute Opcodes", identical), and dual_add_subtract
(PRM Table 18-10 p.433: "RA = RX + RY, RS = RX - RY", cu=0,
opcode[19:16]=0111 fixed / 1111 float) plus its multifunction form
multifn_mul_dual_addsub (PGR Table 12-1: compute-field bits[22:20] = 110
fixed / 111 float, a multiply done in parallel with the same dual
add/subtract).

dual_add_subtract is a paired-sum-and-difference idiom, not an FFT tell by
itself: it is the natural shape for a coefficient combine, a block average
or a one-shot rotation, and 13 functions in the image carry 25 of them with
no co-occurring FFT signal anywhere (see docs/findings/06, "The FFT /
phase-vocoder reading was wrong"). label_function() only escalates the
label toward an FFT claim when dual_add_sub is paired with at least two of
four corroborating tells also carried in the vector: bitrev_addr (an actual
bit-reversed address modify), loop_pow2_uniform (every literal hardware-loop
trip count in the function is a power of two -- one non-power-of-two count,
like the 15 in blk93@0x1c5615's [32, 15, 32], disqualifies it), a
table-space literal touch (a named table or an 0x80xxxxxx literal), and
nested_loops (one hardware loop's span strictly containing another's).

The label heuristic is deliberately conservative: it only fires on a small
set of vector shapes seen in the ten ground-truth reads (see
label_function()), and every function's full vector is always emitted
alongside its label so a wrong label stays visible instead of load-bearing.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import struct
import sys
from collections import Counter, defaultdict

_here = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import (  # noqa: E402
    sharcflow,  # noqa: E402
    sharcimm,  # noqa: E402
    sharcldr,  # noqa: E402
)

# --- block table -----------------------------------------------------------

# docs/findings/06-sharc-engine-and-startup.md, "The SHARC code is not one
# block". Everything else in the boot stream is data (float tables etc).
# Block 1 (0x282403f0, 10312 B), included here until DB_VERSION v13, is NOT
# code in the final loaded image: ~99.92% of its own declared byte range
# (10304 of 10312 bytes, identically across all four images) is overwritten
# by later blocks in the same boot stream -- mostly one large zero FILL --
# before LoadedMemory's last-write-wins resolution is reached, so it is
# never resident in the image the emulator actually boots from. See
# docs/findings/05-sharc-isa-and-decoding.md, "One decode path".
CODE_BLOCKS = (56, 69, 76, 78, 80, 88, 91, 93)

SW_ALIAS_BASE = sharcldr.SW_ALIAS_BASE
L2_BYTE_BASE = sharcldr.L2_BYTE_BASE
L2_BYTE_LIMIT = sharcldr.L2_BYTE_LIMIT
L2_SW_BASE = sharcldr.L2_SW_BASE

RETURN_JUMP = sharcflow.RETURN_JUMP


def sw_base_for_target(target: int):
    """Short-word address of a code block's first word, from its loader byte
    target_address. Block targets at or above 0x28000000 use sharcldr's
    ordinary short-word alias; blk69 (0x20000000) sits in the raw L2 byte
    window instead (see sharcldr.LoadedMemory.read_sw's fallback)."""
    if target >= SW_ALIAS_BASE:
        return (target - SW_ALIAS_BASE) // 2
    if L2_BYTE_BASE <= target < L2_BYTE_LIMIT:
        return L2_SW_BASE + (target - L2_BYTE_BASE) // 2
    return None


# --- field merging -----------------------------------------------------

_FIELD_RE = re.compile(r"^(\w+)\[(\d+):(\d+)\]$")


def merge_fields(fields: dict) -> dict:
    """{base_name: value} from an Instruction's split fields, e.g.
    data[31:16]/data[15:0] -> one 'data'. Same convention as the scratch
    block scanners (sweep2.py, mscan.py) this tool's block walk is based on."""
    out = {}
    for k, v in fields.items():
        m = _FIELD_RE.match(k)
        if m:
            base, lo = m.group(1), int(m.group(3))
            out[base] = out.get(base, 0) | (v << lo)
        else:
            out[k] = v
    return out


def float32(bits32: int) -> float:
    return struct.unpack(">f", struct.pack(">I", bits32 & 0xFFFFFFFF))[0]


def sign_extend(value: int, bits: int) -> int:
    if value & (1 << (bits - 1)):
        return value - (1 << bits)
    return value


def _plausible_float(f: float) -> bool:
    return f == f and 1e-6 < abs(f) < 1e6  # not NaN, not degenerate


# --- opcode tables, from tools/sharc_dev/sharcspec/compute_table.json ----------------

# aluop_32_40bit: PRM Table 18-5 p.425-427 (PGR Table 12-3/12-4, 0 conflicts).
ALU_OPS = {
    0x01: "add",
    0x02: "sub",
    0x05: "add_c",
    0x06: "sub_c",
    0x09: "avg",
    0x0A: "comp",
    0x0B: "compu",
    0x21: "pass",
    0x22: "neg",
    0x25: "inc_c",
    0x26: "dec_c",
    0x29: "inc",
    0x2A: "dec",
    0x30: "abs",
    0x40: "and",
    0x41: "or",
    0x42: "xor",
    0x43: "not",
    0x61: "min",
    0x62: "max",
    0x63: "clip",
    0x81: "fadd",
    0x82: "fsub",
    0x89: "favg",
    0x8A: "fcomp",
    0x91: "fabs_add",
    0x92: "fabs_sub",
    0xA1: "fpass",
    0xA2: "fneg",
    0xA5: "frnd",
    0xAD: "mant",
    0xB0: "fabs",
    0xBD: "scalb",
    0xC1: "logb",
    0xC4: "recips",
    0xC5: "rsqrts",
    0xC9: "fix",
    0xCA: "float",
    0xCD: "trunc",
    0xD9: "fix_by",
    0xDA: "float_by",
    0xDD: "trunc_by",
    0xE0: "copysign",
    0xE1: "fmin",
    0xE2: "fmax",
    0xE3: "fclip",
}
FLOAT_ALU_MIN = 0x80  # ALU_OPS keys at/above this are the float half of Table 18-5

# shiftop_shiftimm: PRM Table 18-9 p.431-433 (PGR Table 12-11).
SHIFT_OPS = {
    0x00: "lshift",
    0x04: "ashift",
    0x08: "rot",
    0x20: "or_lshift",
    0x24: "or_ashift",
    0x40: "fext",
    0x44: "fdep",
    0x48: "fext_se",
    0x4C: "fdep_se",
    0x50: "bitext",
    0x58: "bitext_nu",
    0x64: "or_fdep",
    0x6C: "or_fdep_se",
    0x70: "bffwrp_rd",
    0x7C: "bffwrp_wr",
    0x80: "exp",
    0x84: "exp_ex",
    0x88: "leftz",
    0x8C: "lefto",
    0x90: "fpack",
    0x94: "funpack",
    0xC0: "bset",
    0xC4: "bclr",
    0xC8: "btgl",
    0xCC: "btst",
}

# shortcompute: PRM Table 18-2 p.423-425 (PGR "Short Compute Opcodes",
# identical). Used by the 2c form's 12-bit compute field.
SHORT_OPS = {
    0x0: "add",
    0x1: "sub",
    0x2: "pass",
    0x3: "comp",
    0x4: "not",
    0x5: "inc",
    0x6: "dec",
    0x7: "mul_ssi",
    0x8: "fadd",
    0x9: "fsub",
    0xA: "float",
    0xB: "fcomp",
    0xC: "and",
    0xD: "or",
    0xE: "xor",
    0xF: "fmul",
}
FLOAT_SHORT_OPS = {0x8, 0x9, 0xA, 0xB, 0xF}


def classify_compute(field23: int):
    """(cu, detail) for a 23-bit compute field (bits 22:0 of a form's
    compute[22:16]/compute[15:0], or its already-merged 'compute' field).

    cu is 'ALU', 'MULT', 'SHIFT', 'MULTIFN' (a multifunction MUL+ALU or
    MUL+dual-add/subtract op) or None for an all-zero field (no parallel
    compute -- e.g. a memory-only instruction that still carries the slot).
    detail carries is_float, is_mac, is_plain_mul, is_dual_addsub and the
    raw opcode, as available for that cu.
    """
    if field23 == 0:
        return None, {}
    top3 = (field23 >> 20) & 7
    opcode = (field23 >> 12) & 0xFF
    if top3 >= 4:
        # Multifunction (mf=1): PGR Table 12-1 packs mf and cu into these 3
        # bits -- 100/101 = MUL + single ALU, 110/111 = MUL + dual
        # add/subtract (multifn_mul_dual_addsub: a twiddle multiply done in
        # parallel with an FFT butterfly).
        return "MULTIFN", {
            "is_float": bool(top3 & 1),
            "is_mac": True,
            "is_dual_addsub": top3 in (6, 7),
            "opcode": opcode,
        }
    cu = top3
    if cu == 0:
        top_nibble = opcode >> 4
        if top_nibble in (7, 0xF):
            # dual_add_subtract: cu=0, opcode[19:16]=0111/1111; opcode[15:12]
            # is the second result register (Rs/Fs), not an ALU_OPS opcode.
            return "ALU", {
                "is_float": top_nibble == 0xF,
                "is_dual_addsub": True,
                "opcode": opcode,
            }
        return "ALU", {
            "is_float": opcode >= FLOAT_ALU_MIN,
            "opcode": opcode,
            "known": opcode in ALU_OPS,
        }
    if cu == 1:
        if opcode == 0x30:
            return "MULT", {"is_float": True, "is_plain_mul": True, "opcode": opcode}
        top2 = (opcode >> 6) & 3
        if top2 == 0:
            return "MULT", {"housekeeping": True, "opcode": opcode}
        return "MULT", {
            "is_float": bool((opcode >> 3) & 1),
            "is_mac": top2 in (2, 3),
            "is_plain_mul": top2 == 1,
            "opcode": opcode,
        }
    if cu == 2:
        return "SHIFT", {"opcode": opcode, "known": opcode in SHIFT_OPS}
    return "CU3", {"opcode": opcode}


# forms whose merged fields include a full 23-bit (or already-merged 48-bit
# instruction's) parallel compute field alongside their own op
COMPUTE_FORMS = {
    "1a",
    "2a",
    "2a_short",
    "2b",
    "3a",
    "4a",
    "5a_move",
    "5a_swap",
    "7a",
    "9a_abs",
    "9a_rel",
    "11a",
}

# forms that read or write data memory, and how to read space (g: 0=DM,1=PM)
# and direction (d: 0=load,1=store; '-' when the form has no direction bit,
# e.g. a direct-address load-only form).
MEM_FORMS = {
    "15a": "direct",
    "15b": "i+imm",
    "3a": "i,m-mod",
    "3b": "i,m-mod",
    "3d": "i,m-mod",
    "4a": "i+imm",
    "4b": "i+imm",
    "4d": "i+imm",
    "6a_mem": "i,m-mod+shift",
    "14a": "direct",
    "14d": "direct",
}
DUAL_MEM_FORMS = {"1a", "1b"}  # simultaneous DM+PM reference (multifunction)

LOOP_LITERAL_FORM = "12a_imm"
LOOP_REGISTER_FORM = "12a_ureg"

# literal-carrying forms and the merged field holding the constant
LITERAL_FORMS = {
    "17a": ("data", 32, True),  # direct ureg load, float-decodable
    "17b": ("data", 16, False),
    "16a": ("data", 32, False),  # I-register modify by immediate
    "16b": ("data", 16, False),
    "19a": ("data", 32, False),  # I-register modify (raw-byte)
    "19a_scaled": ("data", 32, False),
    "19a_bitrev": ("data", 32, False),  # bit-reversed addressing -- FFT tell
    "15a": ("addr", 32, False),  # direct DM/PM address
    "14a": ("addr", 32, False),
    "18a": ("data", 32, False),
}

# tools/sharc_dev/sharcimm.py's peripheral-register namer, reused rather than
# reimplemented (Appendix A register lists, ADSP-2156x SHARC+ Processor
# Hardware Reference).
PERIPHERAL_LO, PERIPHERAL_HI = sharcimm.PERIPHERAL_SPACE

FRAME_LO, FRAME_HI = 0x2558DC, 0x2560DE
RING_ADDRS = {0x262138, 0x262938, 0x263138, 0x263938}
NAMED_TABLES = {
    0x8055C440: "cosine_a",
    0x8055C640: "cosine_b",
    0x8055C890: "pair1024_a",
    0x8055D890: "pair1024_b",
    0x8045A6C8: "exp829",
    0x8045C3C0: "table32",
    0x26BB68: "stage5_index_table",
}


def classify_literal(value: int) -> str:
    """Region bucket for a merged literal 'data'/'addr' value, for the
    per-function literal-address histogram."""
    if value in NAMED_TABLES:
        return "named:" + NAMED_TABLES[value]
    if FRAME_LO <= value <= FRAME_HI:
        return "param_frame"
    if value in RING_ADDRS:
        return "audio_ring"
    if (value >> 24) == 0x80:
        return "external_0x80xxxxxx"
    if PERIPHERAL_LO <= value <= PERIPHERAL_HI:
        return "peripheral"
    if 0x200000 <= value < 0x300000:
        return "dm_0x2xxxxx"
    return "other"


# --- block-level decode ----------------------------------------------------


def load_blocks(blob_path):
    with open(blob_path, "rb") as f:
        data = f.read()
    blocks = {b["index"]: b for b in sharcldr.parse_blocks(data)}
    return data, blocks


def analyze_block(mem, blocks: dict, idx: int, min_depth: int = 8):
    """-> dict with base_sw, sites (sharcflow.find_sites), insns (aligned
    (offset, Instruction) pairs) and raw_len for code block `idx`.

    `mem` is a tools/sharc_dev/sharcldr.py LoadedMemory over the whole boot stream:
    decoding reads the block's own declared address range through it (the
    final, last-write-wins bytes the emulator actually boots from), not the
    block's own raw stream payload -- a code block a LATER block in the
    same stream fully overwrites (docs/findings/05-sharc-isa-and-decoding.md,
    "One decode path", DB_VERSION v13 -- e.g. loader block 1) is never
    resident in that image, so its own stream bytes are not the program."""
    b = blocks[idx]
    if b["fill"] or not b["byte_count"]:
        return None
    payload = mem.read(b["target_address"], b["byte_count"])
    if payload is None:
        return None
    base_sw = sw_base_for_target(b["target_address"])
    if base_sw is None:
        return None
    sites = sharcflow.find_sites(payload, base_sw, min_depth)
    insns = sharcflow.aligned(payload, min_depth)
    return {
        "idx": idx,
        "target": b["target_address"],
        "base_sw": base_sw,
        "payload_len": b["payload_len"],
        "sites": sites,
        "insns": insns,
    }


def function_bounds(block: dict):
    """[(entry_sw, exit_sw, entry_kind)] for a block: return-delimited spans,
    each further split at any direct-call target that falls strictly inside
    it (see the module docstring; validated against the ten ground-truth
    functions in blk93). entry_kind is 'return_boundary' for a span's first
    piece (it starts just after a return) and 'interior_call_target' for any
    later piece carved out of the same span at a call site -- the visible
    marker for the 0x1c71ec/0x1c7442 case in the module docstring."""
    sites = block["sites"]
    base_sw = block["base_sw"]
    rets = sorted(
        (r for r in sites["returns"] if r["after"] is not None), key=lambda r: r["sw"]
    )
    if not rets:
        return []
    bounds = [base_sw] + [r["after"] for r in rets]
    spans = [(bounds[i], rets[i]["after"]) for i in range(len(rets))]
    call_targets = sorted({c["target"] for c in sites["calls"]})
    out = []
    for entry, exit_ in spans:
        lo = bisect.bisect_right(call_targets, entry)
        hi = bisect.bisect_left(call_targets, exit_)
        cur = entry
        kind = "return_boundary"
        for t in call_targets[lo:hi]:
            if t > cur:
                out.append((cur, t, kind))
                cur = t
                kind = "interior_call_target"
        out.append((cur, exit_, kind))
    return out


def instructions_in(block: dict, entry: int, exit_: int):
    """[(sw, Instruction)] for one function, sliced from the block's aligned
    instruction stream by bisecting on short-word address."""
    insns = block["insns"]
    base_sw = block["base_sw"]  # noqa: F841
    sws = block["_insn_sw"]
    lo = bisect.bisect_left(sws, entry)
    hi = bisect.bisect_left(sws, exit_)
    return [(sws[i], insns[i][1]) for i in range(lo, hi)]


# --- feature vector ----------------------------------------------------


def empty_vector():
    return {
        "int_alu": 0,
        "float_alu": 0,
        "float_mul": 0,
        "mac": 0,
        "plain_mul": 0,
        "shifter": 0,
        "dual_add_sub": 0,
        "multifn": 0,
        "bitrev_addr": 0,
        "loop_literal": 0,
        "loop_register": 0,
        "loop_literal_values": [],
        "loop_spans": [],
        "mem_dm_load": 0,
        "mem_dm_store": 0,
        "mem_pm_load": 0,
        "mem_pm_store": 0,
        "mem_dual": 0,
        "mem_by_form": Counter(),
        "float_immediates": [],
        "literal_regions": Counter(),
        "named_tables_touched": set(),
        "calls": 0,
        "indirect_calls": 0,
    }


def compute_vector(func_insns, sites_calls_by_sw, sites_indirect_by_sw):
    v = empty_vector()
    for sw, insn in func_insns:
        f = merge_fields(insn.fields)
        t = insn.type_name

        # calls (own call sites, for leaf detection -- resolved separately
        # against the global call list, this just counts them)
        if sw in sites_calls_by_sw:
            v["calls"] += 1
        if sw in sites_indirect_by_sw:
            v["indirect_calls"] += 1

        # parallel compute
        field23 = None
        if t in COMPUTE_FORMS:
            field23 = f.get("compute")
        elif t == "2c":
            field23 = None  # short form, handled below
        if field23:
            cu, d = classify_compute(field23)
            if cu == "ALU":
                if d.get("is_dual_addsub"):
                    v["dual_add_sub"] += 1
                elif d.get("is_float"):
                    v["float_alu"] += 1
                else:
                    v["int_alu"] += 1
            elif cu == "MULT":
                if d.get("is_mac"):
                    v["mac"] += 1
                elif d.get("is_plain_mul"):
                    v["plain_mul"] += 1 if not d.get("is_float") else 0
                    if d.get("is_float"):
                        v["float_mul"] += 1
                elif d.get("is_float") and not d.get("housekeeping"):
                    v["float_mul"] += 1
            elif cu == "SHIFT":
                v["shifter"] += 1
            elif cu == "MULTIFN":
                v["mac"] += 1
                if d.get("is_dual_addsub"):
                    v["dual_add_sub"] += 1
                if d.get("is_float"):
                    v["float_mul"] += 1
        if t == "2c":
            field12 = f.get("compute", 0)
            opcode = (field12 >> 8) & 0xF
            if opcode in FLOAT_SHORT_OPS:
                if opcode == 0xF:
                    v["float_mul"] += 1
                else:
                    v["float_alu"] += 1
            elif opcode == 0x7:
                v["plain_mul"] += 1
            else:
                v["int_alu"] += 1
        elif t == "6a_mem":
            v["shifter"] += 1  # Type-6 immediate-shift form; op identity not resolved
        elif t == "6b_shiftimm":
            v["shifter"] += 1

        # memory
        if t in MEM_FORMS:
            space = "pm" if f.get("g") else "dm"
            direction = "store" if f.get("d") else "load"
            v[f"mem_{space}_{direction}"] += 1
            v["mem_by_form"][t] += 1
        elif t in DUAL_MEM_FORMS:
            v["mem_dual"] += 1
            v["mem_by_form"][t] += 1

        # loops
        if t == LOOP_LITERAL_FORM:
            val = f.get("data", 0)
            v["loop_literal"] += 1
            v["loop_literal_values"].append(val)
        elif t == LOOP_REGISTER_FORM:
            v["loop_register"] += 1
        if t in (LOOP_LITERAL_FORM, LOOP_REGISTER_FORM):
            # Type12a's reladdr is the loop-end offset from this instruction
            # (PC-relative, 23 bits signed); the loop body runs from just
            # after this instruction to there. Same formula as
            # tools/sharc_dev/sharc_trace.py's _start_counted_loop, computed statically
            # here (no execution) so nested_loops can be a feature-vector
            # count rather than only visible under the tracer.
            reladdr = f.get("reladdr")
            if reladdr is not None and insn.length_bytes:
                start_sw = sw + insn.length_bytes // 2
                end_sw = sw + sign_extend(reladdr, 23)
                v["loop_spans"].append((start_sw, end_sw))

        # addressing mode tells
        if t == "19a_bitrev":
            v["bitrev_addr"] += 1

        # literal constants
        if t in LITERAL_FORMS:
            field, bits, is_float_capable = LITERAL_FORMS[t]
            val = f.get(field)
            if val is None:
                continue
            if is_float_capable:
                fl = float32(val)
                v["float_immediates"].append(
                    {"bits": val, "float": fl, "plausible": _plausible_float(fl)}
                )
            region = classify_literal(val)
            v["literal_regions"][region] += 1
            if region.startswith("named:"):
                v["named_tables_touched"].add(region[6:])
    return v


def _count_nested_loops(spans):
    """How many of a function's hardware-loop spans sit strictly inside
    another one of its own hardware-loop spans -- a real corroborating tell
    for staged (e.g. FFT) loop nests, as opposed to N flat, sequential
    loops (see blk69@0xb8063e in the module docstring: 17 loops, all
    flat)."""
    nested = 0
    norm = [(min(s, e), max(s, e)) for s, e in spans]
    for i, (lo1, hi1) in enumerate(norm):
        for j, (lo2, hi2) in enumerate(norm):
            if i != j and lo1 < lo2 and hi2 <= hi1:
                nested += 1
                break
    return nested


def finalize_vector(v):
    """JSON-safe copy: sets->sorted lists, Counters->dicts. Also derives the
    FFT corroborator features that need more than one instruction to see:
    loop_pow2_uniform (every literal loop trip count in the function is a
    power of two -- one non-power-of-two count disqualifies the whole
    function, see blk93@0x1c5615's [32, 15, 32] in the module docstring) and
    nested_loops (see _count_nested_loops)."""
    out = dict(v)
    out["mem_by_form"] = dict(v["mem_by_form"])
    out["literal_regions"] = dict(v["literal_regions"])
    out["named_tables_touched"] = sorted(v["named_tables_touched"])
    out["mem_load"] = v["mem_dm_load"] + v["mem_pm_load"]
    out["mem_store"] = v["mem_dm_store"] + v["mem_pm_store"]
    out["compute_total"] = (
        v["int_alu"]
        + v["float_alu"]
        + v["float_mul"]
        + v["mac"]
        + v["plain_mul"]
        + v["shifter"]
        + v["dual_add_sub"]
    )
    values = v["loop_literal_values"]
    out["loop_pow2_uniform"] = int(
        bool(values) and all(x > 0 and (x & (x - 1)) == 0 for x in values)
    )
    out["nested_loops"] = _count_nested_loops(v["loop_spans"])
    del out["loop_spans"]
    return out


# --- labeling ------------------------------------------------------------


def label_function(
    fv: dict, n_insns: int, n_callers: int, n_callees: int, is_leaf: bool
):
    """(label, confidence 0-1, reasons). Deliberately conservative: only
    fires on vector shapes actually seen among the ten hand-read ground
    truth functions (see the module docstring and --ground-truth); anything
    else falls through to 'unclassified/mixed' rather than guessing.
    fv['dual_add_sub'] and fv['bitrev_addr'] are reported regardless of the
    label chosen here -- a single incidental dual add/subtract in an
    otherwise memory-gather-shaped function should not by itself relabel it
    (see 0x1cd286 in --ground-truth). When dual add/subtract does dominate a
    function's compute, the default reading is 'paired sum/difference
    (coefficient combine)' -- a plain sum-and-difference idiom, also used
    for block averages and one-shot rotations -- and only escalates toward
    an FFT claim when at least two of four corroborating tells also show up
    in the vector (bitrev_addr, loop_pow2_uniform, a table-space literal
    touch, nested_loops); see the module docstring."""
    reasons = []
    compute = fv["compute_total"]
    mem_lo, mem_st = fv["mem_load"], fv["mem_store"]

    if n_insns <= 10 and compute == 0 and mem_lo + mem_st <= 2:
        return "glue/trampoline", 0.6, ["tiny, almost no compute or memory traffic"]

    if n_callees >= 5:
        reasons.append(f"{n_callees} distinct callees")
        return "orchestrator/dispatcher", 0.45, reasons

    ring_hits = fv["literal_regions"].get("audio_ring", 0) + fv["literal_regions"].get(
        "dm_0x2xxxxx", 0
    )
    no_float = fv["float_alu"] == 0 and fv["float_mul"] == 0 and fv["mac"] == 0
    if no_float and mem_st >= mem_lo * 1.3 and mem_st >= 5 and ring_hits >= 1:
        reasons.append(
            "no floating-point ops, write-dominant, touches ring/DM addresses"
        )
        return "DMA/descriptor construction", 0.4, reasons

    peripheral_hits = fv["literal_regions"].get("peripheral", 0)
    if peripheral_hits >= 1 and compute <= peripheral_hits + 4:
        reasons.append(
            f"{peripheral_hits} peripheral-register literal(s), little compute"
        )
        return "driver/peripheral", 0.5, reasons

    if fv["dual_add_sub"] >= 2 or (fv["dual_add_sub"] >= 1 and compute <= 20):
        tells = []
        if fv["bitrev_addr"] > 0:
            tells.append(f"{fv['bitrev_addr']} bit-reversed address modify(s)")
        if fv.get("loop_pow2_uniform"):
            tells.append(
                f"power-of-two hardware-loop trip count(s) {fv['loop_literal_values']}"
            )
        table_hits = len(fv["named_tables_touched"]) + fv["literal_regions"].get(
            "external_0x80xxxxxx", 0
        )
        if table_hits > 0:
            tells.append(f"{table_hits} table-space literal touch(es)")
        if fv.get("nested_loops"):
            tells.append(f"{fv['nested_loops']} nested hardware loop(s)")
        if len(tells) >= 2:
            reasons.append(
                f"{fv['dual_add_sub']} dual add/subtract op(s) plus {len(tells)} "
                f"corroborating spectral tells: " + "; ".join(tells)
            )
            return "FFT-like (dual add/subtract + spectral tell)", 0.7, reasons
        reasons.append(
            f"{fv['dual_add_sub']} dual add/subtract op(s) dominate a small compute budget"
            + (
                f"; one uncorroborated tell present ({tells[0]}) -- not enough alone, worth a look"
                if tells
                else "; no corroborating spectral tell"
            )
        )
        return "paired sum/difference (coefficient combine)", 0.55, reasons

    if fv["bitrev_addr"] > 0:
        reasons.append(f"{fv['bitrev_addr']} bit-reversed address modify(s)")
        return "FFT-like (bit-reversed addressing)", 0.5, reasons

    param_hits = fv["literal_regions"].get("param_frame", 0)
    if fv["shifter"] >= 30 and param_hits >= 1:
        reasons.append(
            f"{fv['shifter']} shifter ops (bitfield pack/unpack) + parameter-frame literal(s)"
        )
        return "parameter converter (bitfield unpack)", 0.35, reasons

    has_table = bool(fv["named_tables_touched"])
    external_hits = fv["literal_regions"].get("external_0x80xxxxxx", 0)
    if (has_table or external_hits >= 2) and mem_lo >= 2 and fv["float_mul"] >= 1:
        reasons.append("named/external table address + >=2 loads + a blend multiply")
        return (
            "interpolating table lookup / wavetable oscillator",
            (0.6 if has_table else 0.4),
            reasons,
        )

    if (
        is_leaf
        and n_insns <= 110
        and fv["dual_add_sub"] == 0
        and fv["mac"] + fv["float_mul"] >= 3
        and fv["float_alu"] <= fv["mac"] + fv["float_mul"]
        and mem_st >= 1
    ):
        reasons.append(
            "leaf, a few MAC/multiplies with few surrounding adds, writes state back"
        )
        conf = 0.5 + (0.1 if n_callers >= 2 else 0)
        return "IIR or recurrence", min(conf, 0.65), reasons

    if (
        fv["float_alu"] >= 8
        and fv["mac"] + fv["float_mul"] >= 2
        and n_insns <= 200
        and mem_lo <= 40
    ):
        reasons.append(
            "float ALU ops dominate a moderate multiply count -- polynomial/ramp shape"
        )
        return "envelope or gain", 0.4, reasons

    if (
        mem_lo >= 15
        and fv["float_mul"] >= 1
        and fv["shifter"] >= 5
        and fv["int_alu"] >= 5
    ):
        reasons.append(
            "heavy DM gather + shifter/mask ops (phase math) + a blend multiply, no literal table"
        )
        return "wavetable/indexed lookup (pointer table)", 0.35, reasons

    if (
        mem_lo >= 1
        and mem_st >= 1
        and abs(mem_lo - mem_st) <= max(1, mem_lo // 4)
        and compute <= mem_lo + mem_st
    ):
        reasons.append("loads roughly match stores, little compute between them")
        return "block copy / move", 0.4, reasons

    return "unclassified/mixed", 0.2, ["no rule matched"]


# --- orchestration ---------------------------------------------------------


def build_inventory(blob_path, block_idxs, min_depth=8):
    data, blocks = load_blocks(blob_path)
    mem = sharcldr.LoadedMemory.from_stream(data, list(blocks.values()))
    analyzed = {}
    for idx in block_idxs:
        r = analyze_block(mem, blocks, idx, min_depth)
        if r is None:
            continue
        r["_insn_sw"] = [r["base_sw"] + off // 2 for off, _ in r["insns"]]
        analyzed[idx] = r

    functions = []
    entry_to_func = {}
    for idx, block in analyzed.items():
        for entry, exit_, entry_kind in function_bounds(block):
            if not instructions_in(block, entry, exit_):
                # Header/padding gap before the block's first decodable
                # instruction (or some other span with nothing in it) --
                # not a real function; see blk1@0x1201f8 in the module
                # docstring.
                continue
            fid = f"blk{idx}@{entry:#x}"
            fn = {
                "id": fid,
                "block": idx,
                "entry": entry,
                "exit": exit_,
                "entry_kind": entry_kind,
            }
            functions.append(fn)
            entry_to_func.setdefault(entry, []).append(fid)
    by_id = {fn["id"]: fn for fn in functions}

    # Cross-reference interior-call-target splits so both halves carry a
    # human-readable note (the module docstring's 0x1c71ec/0x1c7442 case):
    # the carved-out callee names the routine it was split from, and that
    # routine names the callee its tail was split into.
    exit_index = {(fn["block"], fn["exit"]): fn for fn in functions}
    for fn in functions:
        if fn["entry_kind"] != "interior_call_target":
            continue
        prev = exit_index.get((fn["block"], fn["entry"]))
        if prev is None:
            continue
        fn["split_from"] = prev["id"]
        fn["boundary_note"] = (
            f"entry is an interior call target split out of {prev['id']}'s "
            f"return-delimited span"
        )
        prev["tail_split_into"] = fn["id"]
        prev["boundary_note"] = (
            f"tail split off as {fn['id']} (a shared, independently "
            f"callable routine) -- n_insns here is short by that amount"
        )

    # owning function for every call/indirect-call site, by (block, sw)
    def owner_of(idx, sw):
        block = analyzed[idx]  # noqa: F841
        fns = [
            fn
            for fn in functions
            if fn["block"] == idx and fn["entry"] <= sw < fn["exit"]
        ]
        return fns[0]["id"] if fns else None

    all_calls = []  # (owner_id, target_sw, kind)
    for idx, block in analyzed.items():
        calls_by_sw = {c["sw"]: c for c in block["sites"]["calls"]}
        indirect_by_sw = {c["sw"]: c for c in block["sites"]["indirect_calls"]}
        for fn in [f for f in functions if f["block"] == idx]:
            func_insns = instructions_in(block, fn["entry"], fn["exit"])
            fv = compute_vector(func_insns, calls_by_sw, indirect_by_sw)
            fn["n_insns"] = len(func_insns)
            fn["vector"] = fv
            for c in block["sites"]["calls"]:
                if fn["entry"] <= c["sw"] < fn["exit"]:
                    all_calls.append((fn["id"], c["target"], "direct"))

    callers = defaultdict(list)
    for owner_id, target, _kind in all_calls:
        for callee_id in entry_to_func.get(target, []):
            callers[callee_id].append(owner_id)

    callees = defaultdict(list)
    for owner_id, target, _kind in all_calls:
        resolved = entry_to_func.get(target)
        callees[owner_id].append({"target": target, "resolved": resolved})

    for fn in functions:
        fn["callers"] = sorted(set(callers.get(fn["id"], [])))
        raw_callees = callees.get(fn["id"], [])
        fn["callees"] = sorted({r for c in raw_callees for r in (c["resolved"] or [])})
        fn["unresolved_callees"] = sorted(
            {c["target"] for c in raw_callees if not c["resolved"]}
        )
        fn["is_leaf"] = (
            fn["vector"]["calls"] == 0 and fn["vector"]["indirect_calls"] == 0
        )
        fn["has_no_static_caller"] = len(fn["callers"]) == 0
        fv = finalize_vector(fn["vector"])
        fn["vector"] = fv
        label, conf, reasons = label_function(
            fv, fn["n_insns"], len(fn["callers"]), len(fn["callees"]), fn["is_leaf"]
        )
        fn["label"] = label
        fn["confidence"] = conf
        fn["label_reasons"] = reasons

    return functions, by_id


# --- ground truth ----------------------------------------------------------

GROUND_TRUTH = {
    0x1C71EC: {
        "n_insns": 251,
        "note": "orchestrator: cosine-table interpolation, nine calls",
    },
    0x1CCBD8: {"n_insns": 59, "note": "two-state linear recurrence, streaming (leaf)"},
    0x1CDECB: {"n_insns": 51, "note": "gated block copy / linear interpolation"},
    0x1CB3D8: {
        "n_insns": 98,
        "note": "polynomial envelope, applied along a ramp; four callers",
    },
    0x1CD286: {"n_insns": 148, "note": "field-pair gather + data-dependent MAC loop"},
    0x1CC79E: {"n_insns": 173, "note": "interpolated resampler; table at 0x26bb68"},
    0x1CBF07: {
        "n_insns": 133,
        "note": "two-tap wavetable lookup; 2^32/8192.0, 0x1fff mask",
    },
    0x1C24E9: {"n_insns": 396, "note": "per-track filter/amp/FX parameter converter"},
    0x1C2B24: {"n_insns": None, "note": "frame reader; calls 0x1c24e9 32 times"},
    0x1C75D8: {"n_insns": None, "note": "DMA descriptor ring construction"},
}


def check_ground_truth(functions):
    by_entry = defaultdict(list)
    for fn in functions:
        by_entry[fn["entry"]].append(fn)
    rows = []
    for addr, gt in GROUND_TRUTH.items():
        fns = by_entry.get(addr, [])
        if not fns:
            rows.append({"addr": addr, "found": False, **gt})
            continue
        fn = fns[0]
        rows.append(
            {
                "addr": addr,
                "found": True,
                "n_insns": fn["n_insns"],
                "expected": gt["n_insns"],
                "label": fn["label"],
                "n_callers": len(fn["callers"]),
                "n_callees": len(fn["callees"]),
                "note": gt["note"],
                "boundary_note": fn.get("boundary_note"),
            }
        )
    return rows


# --- CLI ---------------------------------------------------------------


def _int_list(text):
    return tuple(int(x, 0) for x in text.split(","))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("blob")
    ap.add_argument("--blocks", type=_int_list, default=CODE_BLOCKS)
    ap.add_argument("--min-depth", type=int, default=8)
    ap.add_argument("--json")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--ground-truth", action="store_true")
    args = ap.parse_args(argv)

    functions, by_id = build_inventory(args.blob, args.blocks, args.min_depth)

    by_block = Counter(fn["block"] for fn in functions)
    by_label = Counter(fn["label"] for fn in functions)
    dual_total = sum(fn["vector"]["dual_add_sub"] for fn in functions)

    print(f"{len(functions)} functions across blocks {list(args.blocks)}")
    for idx in args.blocks:
        print(f"  blk{idx}: {by_block.get(idx, 0)} functions")
    print("\nlabel distribution:")
    for label, n in by_label.most_common():
        print(f"  {label:<40} {n}")
    print(f"\ndual add/subtract instructions across all functions: {dual_total}")

    if args.ground_truth:
        print("\n--- ground truth ---")
        for row in check_ground_truth(functions):
            if not row["found"]:
                print(f"  {row['addr']:#08x}  NOT FOUND AS ENTRY  ({row['note']})")
                continue
            match = "=" if row["n_insns"] == row["expected"] else "!="
            exp = row["expected"] if row["expected"] is not None else "?"
            line = (
                f"  {row['addr']:#08x}  n_insns={row['n_insns']} {match} expected={exp}"
                f"  label={row['label']}  callers={row['n_callers']} callees={row['n_callees']}"
                f"  -- {row['note']}"
            )
            if row.get("boundary_note"):
                line += f"  [{row['boundary_note']}]"
            print(line)

    if args.top:

        def score(fn):
            v = fn["vector"]
            s = (
                2 * v["float_mul"]
                + 2 * v["mac"]
                + v["float_alu"]
                + v["shifter"] * 0.2
                + 3 * len(v["named_tables_touched"])
                + 2 * v["literal_regions"].get("external_0x80xxxxxx", 0)
                + (
                    2
                    if fn["label"] != "glue/trampoline"
                    and fn["label"] != "driver/peripheral"
                    else 0
                )
            )
            if fn["label"] in (
                "driver/peripheral",
                "glue/trampoline",
                "block copy / move",
            ):
                s *= 0.2
            return s

        ranked = sorted(functions, key=score, reverse=True)[: args.top]
        print(f"\n--- top {len(ranked)} unread-function shortlist ---")
        for fn in ranked:
            v = fn["vector"]
            print(
                f"  {fn['id']:<22} n={fn['n_insns']:<4} label={fn['label']:<32} "
                f"float_mul={v['float_mul']} mac={v['mac']} float_alu={v['float_alu']} "
                f"tables={v['named_tables_touched']} callers={len(fn['callers'])} callees={len(fn['callees'])}"
            )

    if args.json:
        with open(args.json, "w") as f:
            json.dump(
                {
                    "blocks": args.blocks,
                    "min_depth": args.min_depth,
                    "functions": functions,
                },
                f,
                indent=1,
            )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
