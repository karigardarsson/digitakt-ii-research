"""Machine code of one generated region function, attributed to source functions.

Needs a binary built with line tables (REGIONBENCH_DEBUG=1 with
tools/devtools/sharc_regionbench.py; copy the target's region_bench.dSYM next to the binary) and the gen directory it was built from. For each
instruction of the region function `llvm-objdump -l` gives the innermost source
line (inlined code carries the line of the inlined function); the line is mapped
to its enclosing `fn` in the Rust sources of sharc-rt and of the gen dir. The
report is bytes per function (static) and, with `--exec FILE` (the dump of
`aot_dyntrace.py --dump`, one call), the bytes executed in that call.

    aot_attrib.py BINARY GEN_DIR REGION [--exec FILE] [--top N]

Nothing here reads firmware beyond the gen dir under out/.
"""

from __future__ import annotations

import argparse
import bisect
import glob
import os
import re
import subprocess
import sys
from collections import Counter

OBJDUMP = glob.glob(
    os.path.expanduser("~/.rustup/toolchains/*/lib/rustlib/*/bin/llvm-objdump")
)[-1]
FN = re.compile(r"^\s*(?:pub(?:\([a-z]+\))? )?(?:unsafe )?(?:const )?fn (\w+)")
INSN = re.compile(r"^\s*([0-9a-f]+):\s+(\S+)")
LOC = re.compile(r"^;\s+(/\S+\.rs):(\d+)")
_index: dict[str, tuple[list[int], list[str]]] = {}


def fn_at(path: str, line: int) -> str:
    if path not in _index:
        starts: list[int] = []
        names: list[str] = []
        try:
            with open(path) as fh:
                for i, text in enumerate(fh, 1):
                    m = FN.match(text)
                    if m:
                        starts.append(i)
                        names.append(m.group(1))
        except OSError:
            pass
        _index[path] = (starts, names)
    starts, names = _index[path]
    i = bisect.bisect_right(starts, line) - 1
    base = os.path.basename(path)
    if i < 0:
        return "%s:?" % base
    name = names[i]
    # generated variants: _type_3a__v12 -> _type_3a
    name = re.sub(r"__v\d+$", "", name)
    return "%s::%s" % (re.sub(r"_\d+\.rs$", ".rs", base), name)


_KINDS = [
    (r"let sv\d+ =", "undo copy (svN)"),
    (r"rf\.r\[\d+\] = sv\d+", "undo restore"),
    (r"rf\.o\[\d+\] = rf\.r", "snapshot copy (rf.o)"),
    (r"s\.trap = Some", "trap exit"),
    (r"rf\.pc = 0x|rf\.pending = ", "trap exit"),
    (r"EXIT_CHAIN", "chain exit arm"),
    (r"chain_stops_at", "chain stop test"),
    (r"short &&|s\.icount|s\.limit", "budget check"),
    (r"ic \+= 1", "ic += 1"),
    (r"rf\.pc !=|match rf\.pc", "successor route"),
    (r"EXIT_NEXT|EXIT_BUDGET", "exit"),
    (r"\.m\b", "known-mask test"),
    (r"V \{ b: s\.r", "register load"),
    (r"s\.r\[\d+\]", "register write-back"),
    (r"let ld|let bk|let pd0|loop_depth|bank_requested", "loop/bank/pending copy"),
    (r"if let Err\(t\)", "call + Err test"),
]
_lines: dict[str, list[str]] = {}


def stmt_kind(path: str, line: int) -> str:
    if path not in _lines:
        try:
            with open(path) as fh:
                _lines[path] = fh.read().split("\n")
        except OSError:
            _lines[path] = []
    text = _lines[path][line - 1] if 0 < line <= len(_lines[path]) else ""
    for pat, kind in _KINDS:
        if re.search(pat, text):
            return kind
    return "other: " + text.strip()[:30]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("binary")
    ap.add_argument("gen")
    ap.add_argument("region", help="region name, or a comma-separated list (summed)")
    ap.add_argument("--exec", dest="exec_file")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument(
        "--lines",
        action="store_true",
        help="split the region function's own bytes by the kind of generated statement",
    )
    ap.add_argument(
        "--src-lines",
        metavar="FN",
        help="also print dynamic instructions per source line of functions matching FN",
    )
    ap.add_argument(
        "--trace-range",
        type=int,
        nargs=2,
        metavar=("START", "N"),
        help="print N executed instructions of the --exec dump from the START-th, with source lines",
    )
    ap.add_argument("--by-dyn", action="store_true", help="sort by dynamic count")
    args = ap.parse_args()
    nm = subprocess.check_output([OBJDUMP, "-t", args.binary], text=True)
    dw = glob.glob(
        os.path.join(args.binary + ".dSYM", "Contents", "Resources", "DWARF", "*")
    )
    extra = ["--dsym=" + dw[0]] if dw else []
    per: list[tuple[int, tuple[str, int]]] = []
    executed: set[int] = set()
    dyn_n: Counter[int] = Counter()
    regions = args.region.split(",")
    if regions == ["all"]:
        regions = sorted(
            {
                m.group(1)
                for ln in nm.splitlines()
                if "F __TEXT" in ln
                and (m := re.search(r"blocks_\d+8r_([0-9A-F]+)$", ln))
            }
        )
    for region in regions:
        cand = [
            ln
            for ln in nm.splitlines()
            if re.search(r"blocks_\d+8r_%s$" % region, ln.rstrip()) and "F __TEXT" in ln
        ]
        if len(cand) != 1:
            sys.exit("expected one symbol r_%s, got %d" % (region, len(cand)))
        sym = cand[0].split()[-1]
        out = subprocess.check_output(
            [
                OBJDUMP,
                "-d",
                "-l",
                "--no-show-raw-insn",
                "--disassemble-symbols=" + sym,
                *extra,
                args.binary,
            ],
            text=True,
        )
        start_static = None
        cur = ("?", 0)
        for ln in out.splitlines():
            m = LOC.match(ln)
            if m:
                cur = (m.group(1), int(m.group(2)))
                continue
            m = INSN.match(ln)
            if m:
                addr = int(m.group(1), 16)
                if start_static is None:
                    start_static = addr
                per.append((addr, cur))
    if args.exec_file:
        slide = None
        with open(args.exec_file) as fh:
            for ln in fh:
                if ln.startswith("# function-start"):
                    slide = int(ln.split()[2], 16) - int(per[0][0])
                    continue
                if ln.startswith("["):
                    continue
                a = int(ln.split()[0], 16) - (slide or 0)
                executed.add(a)
                dyn_n[a] += 1
    static: Counter[str] = Counter()
    ran: Counter[str] = Counter()
    dyn: Counter[str] = Counter()
    for addr, (path, line) in per:
        if args.lines and os.path.basename(path).startswith("blocks_"):
            f = "gen: " + stmt_kind(path, line)
        else:
            f = fn_at(path, line)
        static[f] += 4
        if addr in executed:
            ran[f] += 4
            dyn[f] += dyn_n[addr]
    if args.trace_range:
        where = {addr: loc for addr, loc in per}
        seq = []
        with open(args.exec_file) as fh:
            slide2 = None
            for ln in fh:
                if ln.startswith("# function-start"):
                    slide2 = int(ln.split()[2], 16) - int(per[0][0])
                elif not ln.startswith("["):
                    seq.append(ln.rstrip())
        start, n = args.trace_range
        for ln in seq[start : start + n]:
            a = int(ln.split()[0], 16) - (slide2 or 0)
            loc = where.get(a)
            tag = (
                "outlined"
                if loc is None
                else "%s:%d" % (os.path.basename(loc[0]), loc[1])
            )
            print("%-34s %s" % (ln[:34], tag))
        return
    if args.src_lines:
        per_line: Counter[tuple[str, int]] = Counter()
        for addr, (path, line) in per:
            if args.src_lines in fn_at(path, line):
                per_line[(os.path.basename(path), line)] += dyn_n[addr]
        for (b, ln), n in per_line.most_common(args.top):
            print("  %s:%d  %d" % (b, ln, n))
        return
    total = sum(static.values())
    rt = sum(ran.values())
    nd = sum(dyn.values())
    outside = sum(dyn_n.values()) - nd
    print(
        "region r_%s: %d bytes, %d executed in the call, %d dynamic instructions "
        "(+%d in outlined callees)" % (args.region, total, rt, nd, outside)
    )
    print(
        "%-48s %8s %6s %8s %6s %8s %6s"
        % ("function", "bytes", "%", "executed", "%", "dynamic", "%")
    )
    for f, n in (
        sorted(static.items(), key=lambda kv: (-dyn[kv[0]], -kv[1]))[: args.top]
        if args.by_dyn
        else static.most_common(args.top)
    ):
        print(
            "%-48s %8d %5.1f%% %8d %5.1f%% %8d %5.1f%%"
            % (
                f[:48],
                n,
                100 * n / total,
                ran[f],
                100 * ran[f] / max(rt, 1),
                dyn[f],
                100 * dyn[f] / max(nd, 1),
            )
        )


if __name__ == "__main__":
    main()
