#!/usr/bin/env python3
"""Measure the p-code of the generated SHARC+ language, and compare two runs.

The generated language (tools/sharc_dev/sharcspec/ghidra/gen_sleigh.py) gains p-code
stage by stage (HANDOVER-2026-09-16-machine-to-dsp.md). This tool records
what a change does to the p-code, to Ghidra's analysis and to the
decompiler, so a change that breaks decoding, bloats the p-code or slows
analysis shows up as a difference between two runs.

measure writes OUT/lint.json and OUT/<image>.json:
  lint    the sleigh compiler's diagnostics on the in-tree slaspec, and the
          compile time. Uses Ghidra's compiler when GHIDRA_INSTALL_DIR has
          one, else the one bundled with pypcode.
  lift    pypcode over each image's main program, at the instructions our
          decoder aligns (tools/sharc_dev/sharcflow.py aligned()): how many the
          language decodes, where its length differs from our decoder, p-code
          ops per form, conditional control flow (cond is not TRUE) lifted
          with no fall-through, and lifting speed.
  ghidra  with --ghidra: import and analyse each image into a throwaway
          project under OUT, run the sharcflow pass, then record analysis
          time, function sizes, Error bookmarks, decompile time, decompiler
          warnings and the probes on known functions. Needs the language
          installed by tools/coldfire/ghidra/install-sharc.sh, and refuses an install
          that differs from the slaspec.

  Each image also gets OUT/<image>.sqlite: our decoder's view of the main
  program (every decodable offset, with form, fields, depth, aligned flag,
  computed target and the pypcode lift), and with --ghidra Ghidra's view of
  the whole program (instructions, references, bookmarks, functions,
  decompiled C and decompiler warnings). Addresses are short words (SW);
  lengths are bytes. Compare two runs with sqlite3's ATTACH.

compare OLD NEW prints what changed and exits 1 on a regression. Timings
compare only between runs on the same machine, measured back to back.

  tools/coldfire/ghidra/install-sharc.sh
  uv run python -m tools.sharc_dev.sharcpcode measure --out out/sharcpcode/before --ghidra
  (change the generator, run tools/coldfire/ghidra/install-sharc.sh again)
  uv run python -m tools.sharc_dev.sharcpcode measure --out out/sharcpcode/after --ghidra
  uv run python -m tools.sharc_dev.sharcpcode compare out/sharcpcode/before out/sharcpcode/after
"""

import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TOOLS))

from tools import python_env  # noqa: E402
from tools.sharc_dev import (  # noqa: E402
    sharcflow,  # noqa: E402
    sharcimm,  # noqa: E402
)

LANG_DIR = os.path.join(TOOLS, "sharcspec", "ghidra", "SHARC_VISA", "data", "languages")
LANGUAGE_FILES = ("sharc_visa.ldefs", "sharc_visa.pspec", "sharc_visa.cspec")
DEFAULT_GHIDRA = "/opt/homebrew/Cellar/ghidra/12.1.3/libexec"
PROJECT_NAME = "sharcpcode"
COND_TRUE = 0x1F  # PGR Table 10-4
FLOW_OPS = ("BRANCH", "BRANCHIND", "CALL", "CALLIND", "RETURN")
MAX_INSN_BYTES = 32
MIN_SECONDS = 0.5  # smaller time differences are noise

IMAGES = {
    "dt2-1.15C": {
        "blob": "out/sections/dt2-1.15C/section_7_BLOB.bin",
        "region": "out/sharc/dt2-1.15C-main.bin",
        "base_sw": 0x1C1338,
        # DM 0x2d7148 is imported at unified-code SW 0x16b8a4.
        "label_tables": ["16b8a4:4"],
        "probes": [],
    },
    "dt2-1.16": {
        "blob": "out/sections/dt2-1.16/section_7_BLOB.bin",
        "region": "out/sharc/dt2-1.16-main.bin",
        "base_sw": 0x1C1338,
        # DM 0x2d7158 is imported at unified-code SW 0x16b8ac.
        "label_tables": ["16b8ac:4"],
        "probes": [
            # R0 is set in the delay slot of the return
            # (docs/findings/05-sharc-isa-and-decoding.md).
            {
                "name": "returns 2748",
                "kind": "decompiles_to",
                "sw": [0x1C136A],
                "pattern": r"\b(2748|0xabc)\b",
            },
            # The dispatcher's first piece ends in the 9b jump at 0x1c3c3c.
            {
                "name": "RPC dispatcher is one function",
                "kind": "same_function",
                "sw": [0x1C3BEF, 0x1C3C3C],
            },
        ],
    },
    "dn2-1.11": {
        "blob": "out/sections/dn2-1.11/section_7_BLOB.bin",
        "region": "out/sharc/dn2-1.11-main.bin",
        "base_sw": 0x1C12E2,
        # DM 0x2dd3d0 is imported at unified-code SW 0x16e9e8.
        "label_tables": ["16e9e8:4"],
        "probes": [],
    },
}

# Diagnostic kinds, matched in order against one lowercased compiler line.
LINT_KINDS = (
    ("nop", r"\bnop\b"),
    ("unused_field", r"defined but never used"),
    ("unnecessary", r"unnecessary"),
    ("dead_temp", r"dead temporar|written but not read"),
    ("collision", r"collision|collide"),
    ("pattern_conflict", r"conflict"),
    ("error", r"\berror\b"),
)
REGRESSING_LINT = (
    "unnecessary",
    "dead_temp",
    "collision",
    "pattern_conflict",
    "error",
    "other",
)
SUMMARY_LINE = re.compile(r"^(warn\s+)?\d+ |use -\w switch")
DECOMPILER_WARNING = re.compile(r"WARNING: (.*?)(\*/|$)", re.M)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.write("\n")


def load_json(path):
    with open(path) as f:
        return json.load(f)


def ghidra_dir():
    return os.environ.get("GHIDRA_INSTALL_DIR", DEFAULT_GHIDRA)


def ghidra_version(ghidra):
    for line in (
        read_text(os.path.join(ghidra, "Ghidra", "application.properties")) or ""
    ).splitlines():
        if line.startswith("application.version="):
            return line.split("=", 1)[1].strip()
    return None


def installed_sla(ghidra):
    return os.path.join(
        ghidra,
        "Ghidra",
        "Processors",
        "SHARC_VISA",
        "data",
        "languages",
        "sharc_visa.sla",
    )


def git(args):
    proc = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def meta():
    import pypcode

    ghidra = ghidra_dir()
    return {
        "time": datetime.datetime.now().isoformat(timespec="seconds"),
        "host": platform.node(),
        "machine": platform.machine(),
        "cpus": os.cpu_count(),
        "python": platform.python_version(),
        "pypcode": pypcode.__version__,
        "ghidra": ghidra_version(ghidra),
        "git_head": git(["rev-parse", "HEAD"]),
        "sharcspec_dirty": bool(
            git(["status", "--porcelain", "--", "tools/sharc_dev/sharcspec"])
        ),
        "slaspec_sha256": sha256_file(os.path.join(LANG_DIR, "sharc_visa.slaspec")),
        "decode_table_sha256": sha256_file(
            os.path.join(TOOLS, "sharcspec", "decode_table.json")
        ),
    }


# --- lint -------------------------------------------------------------------


def find_sleigh(prefer_ghidra=True):
    """-> (compiler name, path): Ghidra's sleigh when installed and preferred, else pypcode's."""
    path = os.path.join(ghidra_dir(), "support", "sleigh")
    if prefer_ghidra and os.path.exists(path):
        return "ghidra", path
    import pypcode

    return "pypcode", os.path.join(os.path.dirname(pypcode.__file__), "bin", "sleigh")


def classify_diagnostic(line):
    """-> the LINT_KINDS kind of one compiler output line, 'other' for an unknown warning, or None."""
    low = line.strip().lower()
    if not low or SUMMARY_LINE.search(low):
        return None
    for kind, pattern in LINT_KINDS:
        if re.search(pattern, low):
            return kind
    return "other" if re.search(r"\bwarn(ing)?\b", low) else None


def run_sleigh(slaspec, sla_out, compiler, flags):
    """Compile slaspec to sla_out -> {returncode, seconds, counts, examples}."""
    t0 = time.perf_counter()
    proc = subprocess.run(
        [compiler[1], *flags, slaspec, sla_out], capture_output=True, text=True
    )
    seconds = time.perf_counter() - t0
    counts, examples = Counter(), {}
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        kind = classify_diagnostic(line)
        if kind:
            counts[kind] += 1
            if len(examples.setdefault(kind, [])) < 3:
                examples[kind].append(line.strip())
    return {
        "returncode": proc.returncode,
        "seconds": round(seconds, 3),
        "counts": dict(counts),
        "examples": examples,
    }


def build_language(src_dir, dst_dir, compiler):
    """Compile src_dir's slaspec into dst_dir beside copies of its language files.

    Compiles twice: plainly, as tools/coldfire/ghidra/install-sharc.sh does, to
    dst_dir/sharc_visa.sla, and with every diagnostic on to a separate file.
    -> (lint record, path of dst_dir's .ldefs)."""
    os.makedirs(dst_dir, exist_ok=True)
    for name in LANGUAGE_FILES:
        shutil.copy(os.path.join(src_dir, name), dst_dir)
    slaspec = os.path.join(src_dir, "sharc_visa.slaspec")
    sla = os.path.join(dst_dir, "sharc_visa.sla")
    plain = run_sleigh(slaspec, sla, compiler, [])
    flags = ["-u", "-l", "-n", "-t", "-e", "-c"] + (
        ["-f"] if compiler[0] == "ghidra" else []
    )
    lint = run_sleigh(
        slaspec, os.path.join(dst_dir, "diagnostics.sla"), compiler, flags
    )
    lint.update(
        {
            "compiler": compiler[0],
            "flags": flags,
            "compile": {"returncode": plain["returncode"], "seconds": plain["seconds"]},
            "slaspec_sha256": sha256_file(slaspec),
            "sla_sha256": sha256_file(sla)
            if plain["returncode"] == 0 and os.path.exists(sla)
            else None,
        }
    )
    return lint, os.path.join(dst_dir, "sharc_visa.ldefs")


def regenerate_slaspec(dst):
    """Run gen_sleigh.py over a copy of tools/sharc_dev/sharcspec in dst -> the slaspec.

    The copy leaves out the generated SHARC_VISA tree, so what comes back is
    what the current decode_table.json and gen_sleigh.py produce with nothing
    inherited from the output already on disk. Same approach as
    tests/sharc/generation/test_sharc_pcode_language.py's GeneratedLanguage."""
    spec = os.path.join(dst, "sharcspec")
    shutil.copytree(
        os.path.join(TOOLS, "sharcspec"),
        spec,
        ignore=shutil.ignore_patterns("SHARC_VISA", "__pycache__"),
    )
    subprocess.run(
        [sys.executable, os.path.join(spec, "ghidra", "gen_sleigh.py")],
        check=True,
        capture_output=True,
    )
    return os.path.join(
        spec, "ghidra", "SHARC_VISA", "data", "languages", "sharc_visa.slaspec"
    )


def check_language_current():
    """Fail unless LANG_DIR's slaspec is what gen_sleigh.py produces right now.

    Nothing regenerates LANG_DIR on its own and it is git-ignored, so editing
    decode_table.json or gen_sleigh.py leaves it behind without a trace. The
    installed-language check in cmd_measure cannot catch that: a stale slaspec
    compiles to the stale .sla that is installed, the two agree, and the run
    silently measures the old language. That happened on 2026-09-16 -- four
    runs recorded the same slaspec hash across two new decode forms."""
    on_disk = os.path.join(LANG_DIR, "sharc_visa.slaspec")
    if not os.path.exists(on_disk):
        raise SystemExit(
            "no slaspec in %s; run tools/coldfire/ghidra/install-sharc.sh" % LANG_DIR
        )
    tmp = tempfile.mkdtemp(prefix="sharcpcode-gen-")
    try:
        if sha256_file(regenerate_slaspec(tmp)) != sha256_file(on_disk):
            raise SystemExit(
                "the slaspec in %s is not what gen_sleigh.py makes from the current "
                "decode_table.json; run tools/coldfire/ghidra/install-sharc.sh"
                % LANG_DIR
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- lift -------------------------------------------------------------------


def load_context(ldefs):
    import pypcode

    arch = pypcode.Arch("SHARC_VISA", ldefs)
    return pypcode.Context(arch.languages[0])


def lift_one(ctx, code, address):
    """-> (length in bytes, [p-code ops other than IMARK]) of the instruction
    at the start of code, placed at Ghidra address `address` (2 * short word);
    (None, []) when the language does not decode it."""
    import pypcode

    try:
        tr = ctx.translate(code, base_address=address, max_instructions=1)
    except (pypcode.BadDataError, pypcode.UnimplError):
        return None, []
    length, ops = None, []
    for op in tr.ops:
        if op.opcode == pypcode.OpCode.IMARK:
            if length is None:
                length = op.inputs[0].size
        else:
            ops.append(op)
    return length, ops


def lifts_without_fallthrough(names):
    """True when p-code op names leave the instruction unconditionally (no CBRANCH)."""
    return "CBRANCH" not in names and any(n in FLOW_OPS for n in names)


def _example(table, form, sw):
    rec = table.setdefault(form, {"count": 0, "examples": []})
    rec["count"] += 1
    if len(rec["examples"]) < 5:
        rec["examples"].append(hex(sw))


def _total(table):
    return sum(rec["count"] for rec in table.values())


def lift_region(ctx, data, base_sw, min_depth=8, repeat=1):
    """Lift every aligned instruction of a main program -> lift record."""
    rows = sharcflow.aligned(data, min_depth)
    forms, undecoded, mismatch, no_fallthrough, opcodes = (
        {},
        Counter(),
        {},
        {},
        Counter(),
    )
    decoded = ops_total = 0
    for off, insn in rows:
        sw = base_sw + off // 2
        form = insn.type_name
        length, ops = lift_one(ctx, data[off : off + MAX_INSN_BYTES], 2 * sw)
        if length is None:
            undecoded[form] += 1
            continue
        decoded += 1
        names = [op.opcode.name for op in ops]
        rec = forms.setdefault(form, {"n": 0, "ops": 0, "max_ops": 0, "no_ops": 0})
        rec["n"] += 1
        rec["ops"] += len(ops)
        rec["max_ops"] = max(rec["max_ops"], len(ops))
        rec["no_ops"] += not ops
        ops_total += len(ops)
        opcodes.update(names)
        if length != insn.length_bytes:
            _example(mismatch, form, sw)
        cond = insn.fields.get("cond[4:0]")
        if cond is not None and cond != COND_TRUE and lifts_without_fallthrough(names):
            _example(no_fallthrough, form, sw)
    seconds = min(_time_lift(ctx, data, rows, base_sw) for _ in range(max(1, repeat)))
    return {
        "aligned": len(rows),
        "decoded": decoded,
        "undecoded": dict(undecoded),
        "length_mismatch": mismatch,
        "conditional_without_fallthrough": no_fallthrough,
        "forms": forms,
        "opcodes": dict(opcodes),
        "ops_total": ops_total,
        "seconds": round(seconds, 4),
        "instructions_per_second": round(len(rows) / seconds) if seconds else None,
    }


def _time_lift(ctx, data, rows, base_sw):
    t0 = time.perf_counter()
    for off, _ in rows:
        lift_one(ctx, data[off : off + MAX_INSN_BYTES], 2 * (base_sw + off // 2))
    return time.perf_counter() - t0


# --- sqlite dump ------------------------------------------------------------

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
-- our decoder (tools/sharc_dev/sharc_disasm.py) at every offset of the main program that decodes
CREATE TABLE decoder (
    sw INTEGER PRIMARY KEY, form TEXT, length INTEGER, kind TEXT, raw TEXT,
    fields TEXT, depth INTEGER, sweep INTEGER, aligned INTEGER,
    b INTEGER, j INTEGER, cond INTEGER,
    target_sw INTEGER,          -- addr, or sw + signed reladdr
    target_aligned INTEGER,
    sleigh_length INTEGER, pcode TEXT);   -- pypcode lift, aligned rows only
-- Ghidra, whole program
CREATE TABLE insn (
    sw INTEGER PRIMARY KEY, length INTEGER, mnemonic TEXT, raw TEXT, flow TEXT,
    fallthrough_sw INTEGER, pcode TEXT, function_sw INTEGER, in_main INTEGER);
CREATE TABLE refs (from_sw INTEGER, to_sw INTEGER, type TEXT, op_index INTEGER);
CREATE TABLE bookmarks (
    sw INTEGER, type TEXT, category TEXT, text TEXT,
    at_sw INTEGER, flow_from_sw INTEGER);   -- parsed from text
CREATE TABLE functions (sw INTEGER PRIMARY KEY, name TEXT, instructions INTEGER, in_main INTEGER);
CREATE TABLE decompiled (function_sw INTEGER PRIMARY KEY, seconds REAL, completed INTEGER, error TEXT, c TEXT);
CREATE TABLE warnings (
    function_sw INTEGER, message TEXT, normalised TEXT,
    addr1 INTEGER, addr2 INTEGER);          -- first two 0x numbers, as printed
CREATE INDEX refs_to ON refs(to_sw);
CREATE INDEX refs_from ON refs(from_sw);
CREATE INDEX bookmarks_sw ON bookmarks(sw);
CREATE INDEX insn_function ON insn(function_sw);
"""

LABEL = re.compile(r"^(\w+)(?:\[(\d+):(\d+)\])?$")


def open_db(path):
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def field_value(fields, base, signed=False):
    """-> field `base` assembled from its labelled chunks ('reladdr[23:16]',
    'reladdr[15:0]'), sign-extended when signed; None when the form has none."""
    value = width = 0
    found = False
    for label, v in fields.items():
        m = LABEL.match(label)
        if not m or m.group(1) != base:
            continue
        found = True
        hi = int(m.group(2)) if m.group(2) else 0
        lo = int(m.group(3)) if m.group(3) else 0
        value |= v << lo
        width = max(width, hi + 1)
    if not found:
        return None
    if signed and value & (1 << (width - 1)):
        value -= 1 << width
    return value


def write_decoder(db, ctx, data, base_sw, min_depth=8):
    """Fill the decoder table for a main program; ctx None skips the lift."""
    table = sharcimm.decode_all(data)
    depth = sharcimm.depths(table, len(data))
    sweep = sharcimm.sweep_offsets(table, len(data))
    aligned = {base_sw + off // 2 for off in sweep if depth.get(off, 0) >= min_depth}
    rows = []
    for off in sorted(table):
        insn = table[off]
        sw = base_sw + off // 2
        f = insn.fields
        target = field_value(f, "addr")
        if target is None:
            rel = field_value(f, "reladdr", signed=True)
            target = None if rel is None else sw + rel
        sleigh_length = pcode = None
        if ctx is not None and sw in aligned:
            sleigh_length, ops = lift_one(ctx, data[off : off + MAX_INSN_BYTES], 2 * sw)
            pcode = " ".join(op.opcode.name for op in ops)
        rows.append(
            (
                sw,
                insn.type_name,
                insn.length_bytes,
                insn.kind,
                None if insn.raw is None else "%x" % insn.raw,
                json.dumps(f, sort_keys=True),
                depth.get(off),
                int(off in sweep),
                int(sw in aligned),
                f.get("b"),
                f.get("j"),
                field_value(f, "cond"),
                target,
                None if target is None else int(target in aligned),
                sleigh_length,
                pcode,
            )
        )
    db.executemany("INSERT INTO decoder VALUES (%s)" % ",".join("?" * 16), rows)


def write_program(db, program, lo_sw, hi_sw):
    """Fill insn, refs, bookmarks and functions from an open Ghidra program."""
    listing, fm = program.getListing(), program.getFunctionManager()

    def sw_of(address):
        return address.getOffset() // 2

    def in_main(sw):
        return int(lo_sw <= sw < hi_sw)

    insns, refs = [], []
    for ins in listing.getInstructions(True):
        sw = sw_of(ins.getAddress())
        ft = ins.getFallThrough()
        f = fm.getFunctionContaining(ins.getAddress())
        insns.append(
            (
                sw,
                ins.getLength(),
                str(ins.getMnemonicString()),
                "".join("%02x" % (b & 0xFF) for b in ins.getBytes()),
                str(ins.getFlowType()),
                None if ft is None else sw_of(ft),
                " ".join(str(op.getMnemonic()) for op in ins.getPcode()),
                None if f is None else sw_of(f.getEntryPoint()),
                in_main(sw),
            )
        )
        for ref in ins.getReferencesFrom():
            to = ref.getToAddress()
            refs.append(
                (
                    sw,
                    sw_of(to) if to.isMemoryAddress() else None,
                    str(ref.getReferenceType()),
                    ref.getOperandIndex(),
                )
            )
    db.executemany("INSERT INTO insn VALUES (?,?,?,?,?,?,?,?,?)", insns)
    db.executemany("INSERT INTO refs VALUES (?,?,?,?)", refs)

    marks = []
    for mark in program.getBookmarkManager().getBookmarksIterator():
        text = str(mark.getComment() or "")
        at = re.search(r"\bat ([0-9a-fA-F]{6,})", text)
        src = re.search(r"flow from ([0-9a-fA-F]{6,})", text)
        marks.append(
            (
                sw_of(mark.getAddress()),
                str(mark.getTypeString()),
                str(mark.getCategory()),
                text,
                int(at.group(1), 16) if at else None,
                int(src.group(1), 16) if src else None,
            )
        )
    db.executemany("INSERT INTO bookmarks VALUES (?,?,?,?,?,?)", marks)

    funcs = []
    for f in fm.getFunctions(True):
        sw = sw_of(f.getEntryPoint())
        n = sum(1 for _ in listing.getInstructions(f.getBody(), True))
        funcs.append((sw, str(f.getName()), n, in_main(sw)))
    db.executemany("INSERT INTO functions VALUES (?,?,?,?)", funcs)


# --- ghidra -----------------------------------------------------------------


def start_pyghidra():
    """Start the JVM once -> the pyghidra module."""
    # Run as a module (`python -m tools.sharc_dev.sharcpcode`), not by script path: tools/coldfire
    # then stays off sys.path and its plain `ghidra/` directory cannot shadow PyGhidra's `ghidra`.
    os.environ.setdefault("GHIDRA_INSTALL_DIR", DEFAULT_GHIDRA)
    import pyghidra

    if not pyghidra.started():
        pyghidra.start(verbose=False)
    return pyghidra


def timed_run(argv):
    t0 = time.perf_counter()
    proc = subprocess.run(
        argv, capture_output=True, text=True, cwd=REPO, env=python_env()
    )
    return proc, round(time.perf_counter() - t0, 2)


def sharc_import_args(blob, name, project, spec):
    """Build sharc_import.py arguments for one image's configured seed tables."""
    argv = [
        sys.executable,
        "-m",
        "tools.sharc_dev.sharc_import",
        blob,
        "--name",
        name,
        "--project",
        project,
        "--project-name",
        PROJECT_NAME,
    ]
    for label_table in spec.get("label_tables", []):
        argv.extend(["--label-table", label_table])
    return argv + ["--seed-calls", "--analyze", "--overwrite"]


def measure_ghidra(image, spec, out_dir, timeout, max_functions, db=None):
    """Import, analyse and run the sharcflow pass in a throwaway project -> ghidra record."""
    project = os.path.join(out_dir, "ghidra-project")
    os.makedirs(project, exist_ok=True)
    name = image + "_SHARC"
    blob = os.path.join(REPO, spec["blob"])
    region = os.path.join(REPO, spec["region"])
    imp, import_seconds = timed_run(sharc_import_args(blob, name, project, spec))
    if imp.returncode:
        raise SystemExit("sharc_import.py failed:\n" + imp.stdout + imp.stderr)
    flow_json = os.path.join(out_dir, image + "-sharcflow.json")
    flow, flow_seconds = timed_run(
        [
            sys.executable,
            "-m",
            "tools.sharc_dev.sharcflow",
            region,
            "--base-sw",
            hex(spec["base_sw"]),
            "--program",
            "/" + name,
            "--project",
            project,
            "--project-name",
            PROJECT_NAME,
            "--cover",
            "--analyze",
            "--save",
            "--json",
            flow_json,
        ]
    )
    if flow.returncode:
        raise SystemExit("sharcflow.py failed:\n" + flow.stdout + flow.stderr)
    lo_sw = spec["base_sw"]
    hi_sw = lo_sw + os.path.getsize(region) // 2
    record = ghidra_metrics(
        project,
        PROJECT_NAME,
        "/" + name,
        lo_sw,
        hi_sw,
        spec["probes"],
        timeout,
        max_functions,
        db,
    )
    record.update(
        {
            "import_analyze_seconds": import_seconds,
            "flow_pass_seconds": flow_seconds,
            "sharcflow": load_json(flow_json).get("ghidra"),
        }
    )
    return record


def ghidra_metrics(
    project_dir,
    project_name,
    program_path,
    lo_sw,
    hi_sw,
    probes,
    timeout,
    max_functions,
    db=None,
):
    pyghidra = start_pyghidra()
    project = pyghidra.open_project(project_dir, project_name, create=False)
    try:
        with pyghidra.program_context(project, program_path) as program:
            return collect(program, lo_sw, hi_sw, probes, timeout, max_functions, db)
    finally:
        project.close()


def size_bucket(n):
    return "1" if n == 1 else "2-5" if n <= 5 else "6-20" if n <= 20 else "21+"


def normalise_message(text):
    text = re.sub(r"0x[0-9a-fA-F]+|\b[0-9a-fA-F]{8}\b", "ADDR", str(text or ""))
    return " ".join(text.split())[:120]


def collect(program, lo_sw, hi_sw, probes, timeout, max_functions, db=None):
    """Function, p-code and decompiler metrics over [lo_sw, hi_sw) of an open program."""
    from ghidra.app.decompiler import DecompInterface
    from ghidra.program.model.address import AddressSet
    from ghidra.util.task import TaskMonitor

    space = program.getAddressFactory().getDefaultAddressSpace()

    def addr(sw):
        return space.getAddress(2 * sw)

    main = AddressSet(addr(lo_sw), addr(hi_sw).subtract(1))
    listing, fm = program.getListing(), program.getFunctionManager()

    def count_instructions(body):
        return sum(1 for _ in listing.getInstructions(body, True))

    instructions = pcode_ops = 0
    for ins in listing.getInstructions(main, True):
        instructions += 1
        pcode_ops += len(ins.getPcode())

    functions, sizes = [], Counter()
    for f in fm.getFunctions(main, True):
        n = count_instructions(f.getBody())
        functions.append(f)
        sizes[size_bucket(n)] += 1

    if db is not None:
        write_program(db, program, lo_sw, hi_sw)
    decompiled_rows, warning_rows = [], []

    ifc = DecompInterface()
    ifc.openProgram(program)

    def decompile(f):
        t0 = time.perf_counter()
        res = ifc.decompileFunction(f, timeout, TaskMonitor.DUMMY)
        seconds = time.perf_counter() - t0
        c = res.getDecompiledFunction().getC() if res.decompileCompleted() else None
        return res, seconds, c

    todo = functions[:max_functions] if max_functions else functions
    times, slowest, failed, warnings = [], [], Counter(), Counter()
    decompiled, timeouts, baddata, high_ops = {}, 0, 0, 0
    try:
        for f in todo:
            sw = f.getEntryPoint().getOffset() // 2
            res, seconds, c = decompile(f)
            times.append(seconds)
            slowest.append((seconds, sw))
            if db is not None:
                decompiled_rows.append(
                    (
                        sw,
                        seconds,
                        int(c is not None),
                        None if c is not None else str(res.getErrorMessage() or ""),
                        c,
                    )
                )
                for m in DECOMPILER_WARNING.finditer(c or ""):
                    nums = [
                        int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]+)", m.group(1))
                    ]
                    warning_rows.append(
                        (
                            sw,
                            m.group(1).strip(),
                            normalise_message(m.group(1)),
                            nums[0] if nums else None,
                            nums[1] if len(nums) > 1 else None,
                        )
                    )
            if c is None:
                timeouts += bool(res.isTimedOut())
                failed[normalise_message(res.getErrorMessage())] += 1
                continue
            decompiled[sw] = c
            baddata += "halt_baddata" in c
            for m in DECOMPILER_WARNING.finditer(c):
                warnings[normalise_message(m.group(1))] += 1
            hf = res.getHighFunction()
            if hf is not None:
                high_ops += sum(1 for _ in hf.getPcodeOps())

        if db is not None:
            db.executemany("INSERT INTO decompiled VALUES (?,?,?,?,?)", decompiled_rows)
            db.executemany("INSERT INTO warnings VALUES (?,?,?,?,?)", warning_rows)

        probe_results = []
        for p in probes:
            funcs = [fm.getFunctionContaining(addr(sw)) for sw in p["sw"]]
            entries = [
                None if f is None else f.getEntryPoint().getOffset() // 2 for f in funcs
            ]
            rec = {
                "name": p["name"],
                "kind": p["kind"],
                "entries": [None if e is None else hex(e) for e in entries],
            }
            if funcs[0] is not None:
                rec["instructions"] = count_instructions(funcs[0].getBody())
            if p["kind"] == "same_function":
                rec["ok"] = None not in entries and len(set(entries)) == 1
            elif p["kind"] == "decompiles_to":
                c = None
                if funcs[0] is not None:
                    c = decompiled.get(entries[0])
                    if c is None:
                        c = decompile(funcs[0])[2]
                rec["ok"] = bool(c and re.search(p["pattern"], c, re.I))
                rec["c"] = c
            probe_results.append(rec)
    finally:
        ifc.dispose()

    ordered = sorted(times)
    return {
        "main_instructions": instructions,
        "main_pcode_ops": pcode_ops,
        "main_functions": len(functions),
        "function_sizes": dict(sizes),
        "error_bookmarks": program.getBookmarkManager().getBookmarkCount("Error"),
        "decompiled": len(todo),
        "decompile_failed": dict(failed),
        "decompile_timeouts": timeouts,
        "halt_baddata": baddata,
        "decompiler_warnings": dict(warnings),
        "high_pcode_ops": high_ops,
        "decompile_seconds": {
            "total": round(sum(times), 3),
            "median": round(statistics.median(times), 4) if times else None,
            "p95": round(ordered[int(0.95 * (len(ordered) - 1))], 4) if times else None,
            "max": round(ordered[-1], 4) if times else None,
        },
        "slowest": [
            [hex(sw), round(s, 3)] for s, sw in sorted(slowest, reverse=True)[:5]
        ],
        "probes": probe_results,
    }


# --- compare ----------------------------------------------------------------


def _timing(reg, notes, label, a, b, tolerance, higher_is_better=False):
    if not a or b is None:
        return
    change = (b - a) / a
    if abs(change) < 0.02:
        return
    worse = -change if higher_is_better else change
    line = "%s %.4g -> %.4g (%+.0f%%)" % (label, a, b, 100 * change)
    if worse > tolerance and (higher_is_better or b - a >= MIN_SECONDS):
        reg.append(line)
    else:
        notes.append(line)


def _count_change(reg, notes, label, a, b, regress_up=True):
    if a == b:
        return
    (reg if (b > a) == regress_up else notes).append("%s %s -> %s" % (label, a, b))


def compare_lint(old, new, tolerance):
    reg, notes = [], []
    if old.get("compiler") != new.get("compiler"):
        notes.append(
            "compiler %s -> %s: diagnostics may not compare"
            % (old.get("compiler"), new.get("compiler"))
        )
    if (
        new.get("compile", {}).get("returncode", new["returncode"]) != 0
        or new["returncode"] != 0
    ):
        reg.append("the slaspec does not compile")
    oc, nc = old.get("counts", {}), new.get("counts", {})
    for kind in sorted(set(oc) | set(nc)):
        a, b = oc.get(kind, 0), nc.get(kind, 0)
        if a != b:
            (reg if b > a and kind in REGRESSING_LINT else notes).append(
                "%s %d -> %d" % (kind, a, b)
            )
    if old.get("slaspec_sha256") == new.get("slaspec_sha256"):
        notes.append("slaspec unchanged")
    return reg, notes


def compare_image(old, new, tolerance):
    reg, notes = [], []
    same = old.get("region_sha256") == new.get("region_sha256")
    bucket = reg if same else notes
    if not same:
        notes.append(
            "the main program differs between the runs: counts are not compared as regressions"
        )

    lo, ln = old["lift"], new["lift"]
    _count_change(
        bucket,
        notes,
        "decoded instructions (of %d aligned)" % ln["aligned"],
        lo["decoded"],
        ln["decoded"],
        regress_up=False,
    )
    for key, label in (
        ("length_mismatch", "length differs from our decoder"),
        ("conditional_without_fallthrough", "conditional flow without fall-through"),
    ):
        _count_change(bucket, notes, label, _total(lo[key]), _total(ln[key]))
        for form in sorted(set(lo[key]) | set(ln[key])):
            a = lo[key].get(form, {}).get("count", 0)
            b = ln[key].get(form, {}).get("count", 0)
            if a != b:
                notes.append("  %s %s %d -> %d" % (label, form, a, b))
    for form in sorted(set(lo["undecoded"]) | set(ln["undecoded"])):
        _count_change(
            notes,
            notes,
            "undecoded %s" % form,
            lo["undecoded"].get(form, 0),
            ln["undecoded"].get(form, 0),
        )
    _count_change(notes, notes, "p-code ops", lo["ops_total"], ln["ops_total"])
    for form in sorted(set(lo["forms"]) | set(ln["forms"])):
        a, b = lo["forms"].get(form), ln["forms"].get(form)
        if a and b and (a["max_ops"], a["ops"]) != (b["max_ops"], b["ops"]):
            notes.append(
                "form %s ops/instruction mean %.2f -> %.2f, max %d -> %d"
                % (
                    form,
                    a["ops"] / a["n"],
                    b["ops"] / b["n"],
                    a["max_ops"],
                    b["max_ops"],
                )
            )
    _timing(
        reg,
        notes,
        "lift instructions/s",
        lo["instructions_per_second"],
        ln["instructions_per_second"],
        tolerance,
        higher_is_better=True,
    )

    go, gn = old.get("ghidra"), new.get("ghidra")
    if go and gn:
        for key in ("decompile_timeouts", "halt_baddata", "error_bookmarks"):
            _count_change(bucket, notes, key, go.get(key, 0), gn.get(key, 0))
        _count_change(
            bucket,
            notes,
            "decompile failures",
            sum(go.get("decompile_failed", {}).values()),
            sum(gn.get("decompile_failed", {}).values()),
        )
        for key in (
            "main_functions",
            "main_instructions",
            "main_pcode_ops",
            "high_pcode_ops",
        ):
            _count_change(notes, notes, key, go.get(key), gn.get(key))
        if go.get("function_sizes") != gn.get("function_sizes"):
            notes.append(
                "function sizes %s -> %s"
                % (go.get("function_sizes"), gn.get("function_sizes"))
            )
        ow, nw = go.get("decompiler_warnings", {}), gn.get("decompiler_warnings", {})
        for msg in sorted(set(ow) | set(nw)):
            _count_change(
                notes,
                notes,
                'decompiler warning "%s"' % msg,
                ow.get(msg, 0),
                nw.get(msg, 0),
            )
        old_probes = {p["name"]: p for p in go.get("probes", [])}
        for p in gn.get("probes", []):
            q = old_probes.get(p["name"])
            if q is None:
                notes.append(
                    'probe "%s" new: %s' % (p["name"], "ok" if p["ok"] else "fails")
                )
            elif q["ok"] and not p["ok"]:
                bucket.append('probe "%s" stopped passing' % p["name"])
            elif p["ok"] and not q["ok"]:
                notes.append('probe "%s" now passes' % p["name"])
            if q and q.get("instructions") != p.get("instructions"):
                notes.append(
                    'probe "%s" function instructions %s -> %s'
                    % (p["name"], q.get("instructions"), p.get("instructions"))
                )
        _timing(
            reg,
            notes,
            "import and analysis seconds",
            go.get("import_analyze_seconds"),
            gn.get("import_analyze_seconds"),
            tolerance,
        )
        _timing(
            reg,
            notes,
            "sharcflow pass seconds",
            go.get("flow_pass_seconds"),
            gn.get("flow_pass_seconds"),
            tolerance,
        )
        for key in ("total", "p95"):
            _timing(
                reg,
                notes,
                "decompile seconds %s" % key,
                go.get("decompile_seconds", {}).get(key),
                gn.get("decompile_seconds", {}).get(key),
                tolerance,
            )
    elif go or gn:
        notes.append("Ghidra metrics are in only one run")
    return reg, notes


def compare_dirs(old_dir, new_dir, tolerance):
    reg, notes = [], []
    for name in sorted(os.listdir(new_dir)):
        if not name.endswith(".json") or name.endswith("-sharcflow.json"):
            continue
        old_path = os.path.join(old_dir, name)
        if not os.path.exists(old_path):
            notes.append("%s: not in %s" % (name, old_dir))
            continue
        compare = compare_lint if name == "lint.json" else compare_image
        r, n = compare(
            load_json(old_path), load_json(os.path.join(new_dir, name)), tolerance
        )
        reg += ["%s: %s" % (name, x) for x in r]
        notes += ["%s: %s" % (name, x) for x in n]
    return reg, notes


# --- CLI --------------------------------------------------------------------


def cmd_measure(args):
    args.out = os.path.abspath(args.out)  # Ghidra rejects a relative project path
    os.makedirs(args.out, exist_ok=True)
    compiler = find_sleigh(prefer_ghidra=True)
    if args.ghidra and compiler[0] != "ghidra":
        raise SystemExit(
            "--ghidra needs Ghidra at GHIDRA_INSTALL_DIR (%s)" % ghidra_dir()
        )
    check_language_current()
    lint, ldefs = build_language(LANG_DIR, os.path.join(args.out, "lang"), compiler)
    lint["meta"] = meta()
    write_json(os.path.join(args.out, "lint.json"), lint)
    print(
        "lint (%s): compile %.2fs, %s"
        % (lint["compiler"], lint["compile"]["seconds"], lint["counts"])
    )
    if lint["compile"]["returncode"] != 0:
        raise SystemExit("the slaspec does not compile; see %s/lint.json" % args.out)
    if args.ghidra:
        installed = installed_sla(ghidra_dir())
        if (
            not os.path.exists(installed)
            or sha256_file(installed) != lint["sla_sha256"]
        ):
            raise SystemExit(
                "the installed SHARC_VISA language is not the slaspec measured here; "
                "run tools/coldfire/ghidra/install-sharc.sh"
            )
    ctx = load_context(ldefs)
    for image in args.image or list(IMAGES):
        spec = IMAGES[image]
        region = os.path.join(REPO, spec["region"])
        with open(region, "rb") as f:
            data = f.read()
        record = {
            "image": image,
            "meta": meta(),
            "region_sha256": sha256_bytes(data),
            "blob_sha256": sha256_file(os.path.join(REPO, spec["blob"])),
            "source_sha256": read_text(
                os.path.join(REPO, os.path.dirname(spec["blob"]), ".source-sha256")
            ),
            "lift": lift_region(ctx, data, spec["base_sw"], repeat=args.repeat),
        }
        lift = record["lift"]
        print(
            "%s: %d of %d aligned instructions decode; length differs %d; conditional flow "
            "without fall-through %d; %d p-code ops; %s instructions/s"
            % (
                image,
                lift["decoded"],
                lift["aligned"],
                _total(lift["length_mismatch"]),
                _total(lift["conditional_without_fallthrough"]),
                lift["ops_total"],
                lift["instructions_per_second"],
            )
        )
        db_path = os.path.join(args.out, image + ".sqlite")
        db = open_db(db_path)
        db.executemany(
            "INSERT INTO meta VALUES (?,?)",
            [
                ("image", image),
                ("base_sw", hex(spec["base_sw"])),
                ("region_sha256", record["region_sha256"]),
                ("sla_sha256", lint["sla_sha256"]),
                ("slaspec_sha256", lint["slaspec_sha256"]),
                ("git_head", record["meta"]["git_head"]),
            ],
        )
        write_decoder(db, ctx, data, spec["base_sw"])
        db.commit()
        if args.ghidra:
            g = record["ghidra"] = measure_ghidra(
                image, spec, args.out, args.timeout, args.max_functions, db
            )
            print(
                "%s: import+analysis %.1fs, sharcflow pass %.1fs, %d functions %s, decompile %.1fs "
                "(%d failed, %d timeouts, %d halt_baddata), probes %s"
                % (
                    image,
                    g["import_analyze_seconds"],
                    g["flow_pass_seconds"],
                    g["main_functions"],
                    g["function_sizes"],
                    g["decompile_seconds"]["total"],
                    sum(g["decompile_failed"].values()),
                    g["decompile_timeouts"],
                    g["halt_baddata"],
                    {p["name"]: p["ok"] for p in g["probes"]},
                )
            )
        db.commit()
        db.close()
        print("%s: wrote %s" % (image, db_path))
        write_json(os.path.join(args.out, image + ".json"), record)
    return 0


def cmd_compare(args):
    reg, notes = compare_dirs(args.old, args.new, args.tolerance)
    for line in notes:
        print("  " + line)
    for line in reg:
        print("REGRESSION " + line)
    print("%d regression%s" % (len(reg), "" if len(reg) == 1 else "s"))
    return 1 if reg else 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="command", required=True)
    m = sub.add_parser(
        "measure", help="lint, lift and optionally Ghidra metrics into OUT"
    )
    m.add_argument("--out", required=True)
    m.add_argument(
        "--image",
        action="append",
        choices=sorted(IMAGES),
        help="image to measure (repeatable; default all)",
    )
    m.add_argument(
        "--ghidra", action="store_true", help="also import, analyse and decompile"
    )
    m.add_argument(
        "--repeat", type=int, default=3, help="lift timing runs; the fastest counts"
    )
    m.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="decompile timeout per function, seconds",
    )
    m.add_argument(
        "--max-functions",
        type=int,
        default=0,
        help="decompile at most N functions (0: all)",
    )
    m.set_defaults(func=cmd_measure)
    c = sub.add_parser("compare", help="compare two measure outputs")
    c.add_argument("old")
    c.add_argument("new")
    c.add_argument(
        "--tolerance",
        type=float,
        default=0.25,
        help="relative slowdown counted as a regression (default 0.25)",
    )
    c.set_defaults(func=cmd_compare)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
