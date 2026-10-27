"""Encoding gate: refuse added DSP instructions whose encoding shape is not proven.

Patched DSP code assembled by a third-party assembler once stalled the owner's unit: one instruction
encoded differently from the vendor toolchain's output, and the emulator, which reads the encoding
the assembler's way, could not catch it. The gate checks every instruction in the DSP code a patch
adds (and in the hook sites it rewrites) against two sets of encoding shapes: the shapes that occur
in real stock 1.17 DSP code, and the shapes listed in encoding_proven.toml (executed in an image that
passed on the unit).

    uv run python -m tools.patchkit.pk_encoding_gate OUT.syx PLAN.json      # exit 1 on violations

A shape is the decoded form name plus the instruction bits that are not register numbers, immediates,
addresses, condition codes or index/modify selectors. Compute fields keep their operation bits
(compute[22:12]; 2c keeps [11:8]); 6b_shiftimm keeps the shift op (bits 22:16), the sign of the 8-bit
amount (bit 15) and data_hi (bits 30:27).

Indirect jumps and calls (Types 9a and 9b: the target is PM(Ic, Md)) must match a stock word exactly, operands
included, or a word listed in encoding_proven.toml (`word = "..."`): their shape masks the I/M registers, and two
hazards pass at shape level. Selache's 48-bit `JUMP (M13, I12)` under .NOCOMPRESS is `JUMP (M13, I8) (DB)` by the PRM
layout yet has the shape of stock `JUMP (M14, I12) (DB)`; and `JUMP (M13, I13)` (083f683f), with which every custom
routine that stalled the owner's unit returned, never occurs in stock code (docs/findings/20-patch-toolchains.md).

Code versus data: a plan lists, for every object placed in L2 ("place" writes), the byte ranges that
are code ("code"); the rest of the object (tables, buffers) is data and is not decoded. Hook sites
(`hooks[]` with cpu "sharc") are code too. Registries and sections are data. An object place write that lists no
code range although its module's object has a text section (or the object cannot be read) is refused: the gate
would otherwise decode nothing of it.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from tools.sharc_dev import sharc_disasm
from tools.sharc_dev.sharc_visa_tables import get_type
from tools.sharc_dev.sharcldr import LoadedMemory

ROOT = Path(__file__).resolve().parents[2]
PROVEN = Path(__file__).with_name("encoding_proven.toml")
L2_BYTE = 0x20000000
L2_SW = 0xB80000
L1_BYTE = 0x28000000

# Field names whose bits are register numbers, immediates, addresses, conditions or index/modify selectors.
MASKED = {
    "addr",
    "reladdr",
    "data",
    "imm",
    "shiftimm",
    "dataex",
    "cond",
    "term",
    "dreg",
    "ureg",
    "dstureg",
    "srcureghigh",
    "srcureglow",
    "sreg",
    "cdreg",
    "breg",
    "dmdreg",
    "pmdreg",
    "dmi",
    "dmm",
    "pmi",
    "pmm",
    "i",
    "m",
    "idis",
    "is",
    "operand",
    "by",
    "toby",
}


# Forms whose fields name a PM I/M register as a jump/call target: matched by the whole word, not by shape.
EXACT_FORMS = {"9a_abs", "9b_abs"}


@dataclass(frozen=True)
class Violation:
    pc: int  # short-word address
    raw: str  # the instruction in listing order (most significant parcel first)
    shape: str


def _span(hi: int, lo: int) -> int:
    return ((1 << (hi - lo + 1)) - 1) << lo


_keep_cache: dict[str, int] = {}


def _keep_mask(type_name: str) -> int:
    """The bits of a form that stay in its shape."""
    cached = _keep_cache.get(type_name)
    if cached is not None:
        return cached
    entry = get_type(type_name)
    if entry is None:
        raise KeyError(type_name)
    keep = (1 << entry["bits"]) - 1
    for label, (hi, lo) in entry["fields"].items():
        base = label.split("[")[0]
        if type_name == "6b_shiftimm":
            # shift op = shiftimm[22:16]; the amount's sign is bit 15; data_hi = dataex.
            if base == "cond":
                keep &= ~_span(hi, lo)
            elif label == "shiftimm[15:0]":
                keep &= ~_span(14, 0)
            continue
        if base == "compute":
            op_lo = 8 if hi == 11 else 12  # 2c: op nibble [11:8]; else compute[22:12]
            if op_lo > lo:
                keep &= ~_span(min(hi, op_lo - 1), lo)
        elif base in MASKED:
            keep &= ~_span(hi, lo)
    _keep_cache[type_name] = keep
    return keep


def shape_of(type_name: str, raw: int) -> str:
    return f"{type_name}:{raw & _keep_mask(type_name):x}"


def shape(ins: sharc_disasm.Instruction) -> str:
    assert ins.type_name is not None and ins.raw is not None
    return shape_of(ins.type_name, ins.raw)


def raw_hex(ins: sharc_disasm.Instruction) -> str:
    assert ins.raw is not None and ins.length_bytes is not None
    return f"{ins.raw:0{2 * ins.length_bytes}x}"


def stock_shapes(db: Path) -> set[str]:
    """Shapes of the stock instructions that belong to real code.

    The sweep database lists a decode at every short-word offset. Rows with aligned = 1 are the ones
    reached by the instruction chain from known code, and confidence = 'confident' drops forms the
    decode table marks unconfirmed. (function_sw IS NOT NULL would drop 269 aligned, confident rows
    not attributed to a function yet.)
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT DISTINCT form, raw FROM insn WHERE aligned = 1 AND confidence = 'confident'"
        ).fetchall()
    finally:
        con.close()
    out: set[str] = set()
    for form, raw in rows:
        value = int(raw if isinstance(raw, str) else raw.hex(), 16)
        out.add(shape_of(form, value))
    return out


def stock_control_words(db: Path) -> set[str]:
    """The stock indirect jump/call words (EXACT_FORMS), in listing order, from real code (as stock_shapes)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        marks = ",".join("?" * len(EXACT_FORMS))
        rows = con.execute(
            "SELECT DISTINCT width, raw FROM insn WHERE aligned = 1 AND confidence = 'confident' "
            f"AND form IN ({marks})",
            sorted(EXACT_FORMS),
        ).fetchall()
    finally:
        con.close()
    out: set[str] = set()
    for width, raw in rows:
        value = int(raw if isinstance(raw, str) else raw.hex(), 16)
        out.add(f"{value:0{2 * width}x}")
    return out


def _proven(path: Path) -> list[dict]:
    doc = tomllib.loads(path.read_text())
    entries = doc.get("proven", [])
    for e in entries:
        if bool(e.get("shape")) == bool(e.get("word")):
            raise SystemExit(
                f"{path}: a [[proven]] entry needs one of 'shape' or 'word'"
            )
        for key in ("evidence", "note"):
            if not e.get(key):
                raise SystemExit(f"{path}: a [[proven]] entry lacks {key!r}")
    return entries


def proven_shapes(path: Path = PROVEN) -> set[str]:
    return {e["shape"] for e in _proven(path) if e.get("shape")}


def proven_words(path: Path = PROVEN) -> set[str]:
    """Whole instruction words (listing order, lower-case hex) executed in an image that passed on the unit."""
    return {e["word"].lower() for e in _proven(path) if e.get("word")}


def check_code(
    mem: sharc_disasm.ShortWordReader,
    ranges: list[tuple[int, int]],
    allowed: set[str],
    exact: set[str] | frozenset[str] = frozenset(),
) -> list[Violation]:
    """Decode each [start_sw, end_sw) range and report instructions whose shape is not allowed, and indirect
    jumps/calls (EXACT_FORMS) whose whole word is not in `exact`."""
    out: list[Violation] = []
    for start, end in ranges:
        pc = start
        while pc < end:
            ins = sharc_disasm.decode_confident_loaded(mem, pc)
            if ins.kind == "unknown" or ins.length_bytes is None:
                head = mem.read_sw(pc, 2)
                raw = f"{int.from_bytes(head, 'little'):04x}" if head else ""
                out.append(Violation(pc, raw, "unknown"))
                pc += 1
                continue
            s = shape(ins)
            if ins.type_name in EXACT_FORMS:
                if raw_hex(ins) not in exact:
                    out.append(
                        Violation(pc, raw_hex(ins), f"{s} (exact word required)")
                    )
            elif s not in allowed:
                out.append(Violation(pc, raw_hex(ins), s))
            pc += ins.length_bytes // 2
    return out


# Forms that carry a DM/PM data transfer (their SIMD-mode behaviour adds an implicit PEy access, PRM Table 6-10).
MEMORY_FORMS = frozenset(
    {
        "1a",
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
        "15a",
        "15b",
        "16a",
        "16b",
    }
)
PEYEN = 0x200000
MODE1_SREG = 2  # Type 18 sreg code of MODE1


def check_simd_memory(
    mem: sharc_disasm.ShortWordReader, ranges: list[tuple[int, int]]
) -> list[Violation]:
    """Data transfers that patched code makes in SIMD mode (MODE1.PEYEN set by a `BIT SET MODE1` earlier in the same
    range, scanning in address order; a range starts in SISD, as every PLAITS routine is entered).

    Every SIMD-mode data access in stock 1.17 code addresses L1 normal-word space (0x24f0ac..0x2641e0; stores also reach
    DDR). The PRM documents SIMD normal-word access in byte space (p. 7-6: PEy at explicit + 4, 4-byte aligned), but
    no image has done it on the unit yet and H-opt1, which did (together with illegal same-DAG stores, check_same_dag),
    hung. A precaution, not a manual rule: every transfer under PEYEN is refused until probe-simd-l2-* pass on the unit
    (OPTIMIZING.md). `BIT TGL` of PEYEN, or a MODE1
    write from a register, counts as SIMD on."""
    out: list[Violation] = []
    for start, end in ranges:
        pc, simd = start, False
        while pc < end:
            ins = sharc_disasm.decode_confident_loaded(mem, pc)
            if ins.kind == "unknown" or ins.length_bytes is None:
                pc += 1
                continue
            f = ins.fields
            if ins.type_name == "18a" and f.get("sreg[3:0]") == MODE1_SREG:
                data = (f.get("data[31:16]", 0) << 16) | f.get("data[15:0]", 0)
                if data & PEYEN:
                    bop = f.get("bop[2:0]")
                    simd = (
                        bop != 1 if bop in (0, 1) else True
                    )  # 0 SET, 1 CLR, else TGL/TST: assume on
            elif simd and ins.type_name in MEMORY_FORMS:
                out.append(
                    Violation(
                        pc, raw_hex(ins), f"{shape(ins)} (data transfer in SIMD mode)"
                    )
                )
            pc += ins.length_bytes // 2
    return out


# Ureg <-> memory forms whose ureg field may name a DAG register (Types 3 and 15).
UREG_MEMORY_FORMS = frozenset({"3a", "3b", "3d", "15a", "15b"})


def _field_value(f: dict[str, int], stem: str) -> int | None:
    for k, v in f.items():
        if k.split("[")[0] == stem:
            return v
    return None


def check_same_dag(
    mem: sharc_disasm.ShortWordReader, ranges: list[tuple[int, int]]
) -> list[Violation]:
    """Ureg <-> memory transfers whose ureg is a register of the DAG that generates the address (I/M/L/B 0-7 with a DM
    access, 8-15 with a PM access). The SHARC+ Core Programming Reference forbids them for Types 3 and 15 ("the Ureg
    may not be from the same DAG (that is, DAG1 or DAG2) as Ia/Mb or Ic/Md", pp. 14-12, 14-15, 16-7, 16-10). Stock
    1.17 code loads such registers often but never stores one (every DAG1-register store in it goes through a data
    register or is a DAG2 register); the PLAITS shim stores I6/I7/L6/L7 through R0. H-opt1's RINGS kernel pushed M4,
    I0, I1, M6, L0, L1, L4 with `DM(I7, M7) = ...` and the DSP hung (2026-10-20). Loads are left alone: stock code and
    core/dsp-inline (`I2 = DM(I6 - 7)`, on the unit since H1) load DAG1 registers through DAG1 thousands of times."""
    out: list[Violation] = []
    for start, end in ranges:
        pc = start
        while pc < end:
            ins = sharc_disasm.decode_confident_loaded(mem, pc)
            if ins.kind == "unknown" or ins.length_bytes is None:
                pc += 1
                continue
            if (
                ins.type_name in UREG_MEMORY_FORMS
                and _field_value(ins.fields, "d") == 1
            ):
                ureg = _field_value(ins.fields, "ureg")
                g = _field_value(ins.fields, "g") or 0
                if ureg is not None and 0x10 <= ureg <= 0x4F and (ureg & 0xF) // 8 == g:
                    out.append(
                        Violation(
                            pc,
                            raw_hex(ins),
                            f"{shape(ins)} (DAG{g + 1} register through DAG{g + 1})",
                        )
                    )
            pc += ins.length_bytes // 2
    return out


# Flow-control forms (jumps, calls, returns, DO loops, CJUMP) and the forms that may branch by their bits.
FLOW_FORMS = frozenset(
    {
        "8a_abs",
        "8a_rel",
        "9a_abs",
        "9a_rel",
        "9b_abs",
        "9b_rel",
        "10a_rel",
        "11a",
        "11c",
        "12a_imm",
        "12a_ureg",
        "13a",
        "25a_direct",
        "25a_pcrel",
        "8p_undoc48",
        "21p_undoc16",
        "22p_undoc48",
        "26p_undoc48",
    }
)
# Forms with a delay bit `j` (set: delayed branch); CJUMP (25a) is always delayed.
DELAYABLE_FORMS = frozenset(
    {"8a_abs", "8a_rel", "9a_abs", "9a_rel", "9b_abs", "9b_rel", "11a", "11c"}
)
ALWAYS_DELAYED = frozenset({"25a_direct", "25a_pcrel"})
COND_TRUE = 31
PC_STACK_UREGS = frozenset({0x64, 0x65})  # PCSTK, PCSTKP


def _field_full(f: dict[str, int], stem: str) -> int | None:
    """A field split over several entries (`pmi[2:2]`, `pmi[1:0]`) put together; a plain entry as it is."""
    total, seen = 0, False
    for k, v in f.items():
        name, _, bits = k.partition("[")
        if name != stem:
            continue
        seen = True
        lo = int(bits.rstrip("]").split(":")[1]) if bits else 0
        total |= v << lo
    return total if seen else None


def _ureg_written(ins: sharc_disasm.Instruction) -> int | None:
    """The ureg an instruction writes by a move, an immediate load or a load from memory (None if none)."""
    f, t = ins.fields, ins.type_name
    if t in ("17a", "17b"):
        return _field_value(f, "ureg")
    if t in ("5a_move", "5b_move"):
        return _field_value(f, "dstureg")
    if t in ("3a", "3b", "3d", "15a", "15b", "14a") and _field_value(f, "d") == 0:
        return _field_value(f, "ureg")
    return None


def _writes_dag2(ins: sharc_disasm.Instruction, i: int, m: int) -> bool:
    """Whether an instruction writes I(8 + i) or M(8 + m): a ureg write, a MODIFY of that I, or a post-modify access
    through it (conservative: every DAG2 access through that I counts unless it is a pre-modify)."""
    ureg = _ureg_written(ins)
    if ureg in (0x18 + i, 0x28 + m):
        return True
    f, t = ins.fields, ins.type_name
    g = _field_value(f, "g")
    if t in ("7a", "7b", "19a", "19a_scaled", "19a_bitrev", "7d"):
        return g == 1 and i in (_field_full(f, "is"), _field_full(f, "idis"))
    if t in ("1a", "1b"):
        return _field_full(f, "pmi") == i
    if t in ("3a", "3b", "3d", "4a", "4b", "4d"):
        return g == 1 and _field_full(f, "i") == i and _field_value(f, "u") == 1
    if t in ("6a_mem", "16a", "16b"):
        return g == 1 and _field_full(f, "i") == i
    return False


def _sets_flags(ins: sharc_disasm.Instruction) -> bool:
    """Whether an instruction may change the condition flags: a compute (non-zero compute field), a shift with an
    immediate, or a system-register bit operation."""
    t = ins.type_name
    if t in ("6a_mem", "6a_nomem", "6b_shiftimm", "18a"):
        return True
    return any(k.startswith("compute") and v for k, v in ins.fields.items())


def check_delay_slots(
    mem: sharc_disasm.ShortWordReader, ranges: list[tuple[int, int]]
) -> list[Violation]:
    """The two instructions after a delayed branch (PRM, "Restrictions When Using Delayed Branches", pp. 4-18..4-20):
    no jump, call, return or DO loop (the PRM's exception for mutually exclusive conditions is not used), no PC stack
    push, pop or write and no IDLE; after an indirect branch through PM(Md, Ic), no write of that I or M; after a
    conditional one, no instruction that may change the flags (a precaution beyond the PRM's text: the condition is
    the branch's own, but nothing in patched code needs the slots for a compute there)."""
    out: list[Violation] = []
    for start, end in ranges:
        pc = start
        while pc < end:
            ins = sharc_disasm.decode_confident_loaded(mem, pc)
            if ins.kind == "unknown" or ins.length_bytes is None:
                pc += 1
                continue
            t, f = ins.type_name, ins.fields
            delayed = t in ALWAYS_DELAYED or (
                t in DELAYABLE_FORMS and _field_value(f, "j") == 1
            )
            if delayed:
                cond = _field_value(f, "cond")
                conditional = cond is not None and cond != COND_TRUE
                dag2 = None
                if t in ("9a_abs", "9b_abs"):
                    dag2 = (_field_full(f, "pmi") or 0, _field_full(f, "pmm") or 0)
                slot = pc + ins.length_bytes // 2
                for _ in range(2):
                    if slot >= end:
                        break
                    s_ins = sharc_disasm.decode_confident_loaded(mem, slot)
                    if s_ins.kind == "unknown" or s_ins.length_bytes is None:
                        break
                    st, sf = s_ins.type_name, s_ins.fields
                    why = None
                    if st in FLOW_FORMS:
                        why = "branch in a delay slot"
                    elif st in ("22a", "22c"):
                        why = "IDLE in a delay slot"
                    elif st == "20a" and (
                        _field_value(sf, "ppu") or _field_value(sf, "ppo")
                    ):
                        why = "PC stack push/pop in a delay slot"
                    elif _ureg_written(s_ins) in PC_STACK_UREGS:
                        why = "PC stack write in a delay slot"
                    elif dag2 is not None and _writes_dag2(s_ins, *dag2):
                        why = f"write of the branch's I{8 + dag2[0]}/M{8 + dag2[1]} in a delay slot"
                    elif conditional and _sets_flags(s_ins):
                        why = "flag writer in the delay slot of a conditional branch"
                    if why:
                        out.append(
                            Violation(
                                slot,
                                raw_hex(s_ins),
                                f"{shape(s_ins)} ({why}, branch at {pc:#x})",
                            )
                        )
                    slot += s_ins.length_bytes // 2
            pc += ins.length_bytes // 2
    return out


def check_loop_ends(
    mem: sharc_disasm.ShortWordReader, ranges: list[tuple[int, int]]
) -> list[Violation]:
    """The last 11 instructions of every DO loop (the whole body when it is shorter) must be 48-bit ISA encodings,
    not VISA short forms (PRM, "VISA-Related Restrictions on Hardware Loops"), and the loop end must be an
    instruction of the range."""
    out: list[Violation] = []
    for start, end in ranges:
        pc = start
        while pc < end:
            ins = sharc_disasm.decode_confident_loaded(mem, pc)
            if ins.kind == "unknown" or ins.length_bytes is None:
                pc += 1
                continue
            if ins.type_name in ("12a_imm", "12a_ureg", "13a"):
                f = ins.fields
                rel = _field_full(f, "reladdr") or 0
                last = pc + rel
                body: list[tuple[int, sharc_disasm.Instruction]] = []
                q = pc + ins.length_bytes // 2
                while q <= last and q < end:
                    b = sharc_disasm.decode_confident_loaded(mem, q)
                    if b.kind == "unknown" or b.length_bytes is None:
                        break
                    body.append((q, b))
                    q += b.length_bytes // 2
                if not body or body[-1][0] != last:
                    out.append(
                        Violation(
                            pc,
                            raw_hex(ins),
                            f"{shape(ins)} (loop end {last:#x} is not an instruction)",
                        )
                    )
                else:
                    for q, b in body[-11:]:
                        if b.length_bytes != 6:
                            out.append(
                                Violation(
                                    q,
                                    raw_hex(b),
                                    f"{shape(b)} (VISA form among a loop's last 11, DO at {pc:#x})",
                                )
                            )
            pc += ins.length_bytes // 2
    return out


def decode_listing(listing_hex: str) -> sharc_disasm.Instruction:
    """Decode one instruction written in listing order (most significant parcel first)."""

    class _Words:
        data = b"".join(
            bytes.fromhex(listing_hex[i : i + 4])[::-1]
            for i in range(0, len(listing_hex), 4)
        )

        def read_sw(self, pc_sw: int, size: int = 6) -> bytes | None:
            lo = 2 * pc_sw
            return self.data[lo : lo + size] if lo + size <= len(self.data) else None

    return sharc_disasm.decode_confident_loaded(_Words(), 0)


def byte_to_sw(addr: int) -> int:
    if addr >= L1_BYTE:
        return (addr - L1_BYTE) // 2
    return L2_SW + (addr - L2_BYTE) // 2


def code_ranges(plan: dict) -> list[tuple[int, int]]:
    """Short-word ranges of the DSP code a patch adds: object code ranges and hook sites."""
    out: list[tuple[int, int]] = []
    for w in plan["writes"]:
        if w["cpu"] == "sharc" and w["kind"] == "place":
            for lo, hi in w.get("code") or []:
                out.append((byte_to_sw(lo), byte_to_sw(hi)))
    for h in plan.get("hooks", []):
        if h["cpu"] == "sharc":
            lo = byte_to_sw(h["addr"])
            out.append((lo, lo + h["site_len"] // 2))
    return out


OBJECT_IN = "object in "


def _has_text(moddir: Path, region: str, device: str) -> bool | None:
    """Whether the module in `moddir` places a SHARC object with a non-empty text section in `region` (None: the
    module or its object cannot be read)."""
    manifest = moddir / "module.toml"
    if not manifest.exists():
        return None
    found = False
    for o in tomllib.loads(manifest.read_text()).get("object", []):
        if o.get("place") != region:
            continue
        devices = o.get("devices")
        if devices and device not in devices:
            continue
        path = moddir / o.get("path", "")
        if not path.is_file():
            return None
        doc = json.loads(path.read_text())
        if doc.get("arch") != "sharc":
            continue
        found = True
        if any(s.get("kind") == "text" and s.get("size") for s in doc["sections"]):
            return True
    return False if found else None


def missing_code_ranges(plan: dict, modules: Path | None = None) -> list[Violation]:
    """SHARC object place writes that list no code range although the object has code (or cannot be told): the
    gate would decode nothing of them, so they are refused instead of passing unchecked."""
    modules = modules or ROOT / "modules"
    device = plan.get("base", {}).get("device", "")
    out: list[Violation] = []
    for w in plan["writes"]:
        if w["cpu"] != "sharc" or w["kind"] != "place" or w.get("code"):
            continue
        what = w.get("what", "")
        if not what.startswith(OBJECT_IN):
            continue
        owner = w.get("owner", "").rsplit("@", 1)[0]
        has = _has_text(modules / owner, what[len(OBJECT_IN) :], device)
        if has is False:
            continue  # a data-only object
        why = "has code" if has else "cannot be read to tell whether it has code"
        out.append(
            Violation(
                byte_to_sw(w["addr"]),
                "",
                f"{owner}: {what} lists no code range but {why} (refused: nothing of it would be checked)",
            )
        )
    return out


def default_db(plan: dict) -> Path:
    base = plan["base"]
    return ROOT / "out" / "sharcdb" / f"{base['device']}-{base['version']}.sqlite"


def check_image(syx: Path, plan: Path, db: Path | None = None) -> list[Violation]:
    """Violations in the DSP code of a patched image (empty when the patch adds no DSP code)."""
    from tools.patchkit import dsp_patch

    doc = json.loads(plan.read_text())
    missing = missing_code_ranges(doc)
    ranges = code_ranges(doc)
    if not ranges:
        return missing
    db = db or default_db(doc)
    if not db.exists():
        raise SystemExit(
            f"encoding gate needs the stock instruction database {db}; build it with "
            "`uv run python -m tools.sharc_dev.sharcdb build out/sections/<image>/section_7_BLOB.bin`"
        )
    mem = LoadedMemory.from_stream(dsp_patch.section_blob(syx, 7))
    return (
        missing
        + check_code(
            mem,
            ranges,
            stock_shapes(db) | proven_shapes(),
            stock_control_words(db) | proven_words(),
        )
        + check_simd_memory(mem, ranges)
        + check_same_dag(mem, ranges)
        + check_delay_slots(mem, ranges)
        + check_loop_ends(mem, ranges)
    )


def format_violation(v: Violation) -> str:
    return f"  pc {v.pc:#08x}  {v.raw}  {v.shape}"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    found = check_image(Path(argv[0]), Path(argv[1]))
    for v in found:
        print(format_violation(v))
    print(f"{len(found)} violation(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
