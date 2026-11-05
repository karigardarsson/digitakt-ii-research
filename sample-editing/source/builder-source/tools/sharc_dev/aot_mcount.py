"""Machine code per SHARC instruction for the generated AOT region functions.

Reads a built binary (a region_bench or coupled-run executable) and the gen
directory it was built from: for each region function r_NAME it counts the
machine instructions of the symbol (disassembly) and the SHARC instruction
sites in the generated source (`ic += 1;` sites in the region body), and prints
bytes and machine instructions per SHARC site, plus branch, call, load/store
and stack-slot shares.

    aot_mcount.py BINARY GEN_DIR [--regions PC,..] [--top N] [--json OUT]

Without --regions every r_* region of the gen is reported. The disassembler is
the rustup toolchain's llvm-objdump (aarch64 only). Nothing here reads
firmware; the gen directory is under out/.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys

BRANCH = re.compile(r"^(b|b\.\w+|cbz|cbnz|tbz|tbnz|br|ret)$")
CALL = re.compile(r"^(bl|blr)$")
LOAD = re.compile(r"^(ldr\w*|ldp|ldur\w*|ldar\w*|ld1\w*)$")
STORE = re.compile(r"^(str\w*|stp|stur\w*|stlr\w*|st1\w*)$")
SP_REL = re.compile(r"\[sp[,\]]")
SYM = re.compile(r"^([0-9a-f]+) [tT] (\S+)$")


def objdump() -> str:
    hits = glob.glob(
        os.path.expanduser("~/.rustup/toolchains/*/lib/rustlib/*/bin/llvm-objdump")
    )
    if not hits:
        sys.exit("llvm-objdump not found (rustup component add llvm-tools)")
    return sorted(hits)[-1]


def region_sites(gen: str) -> dict[str, int]:
    """Region name -> number of `ic += 1;` sites in its generated function."""
    out: dict[str, int] = {}
    for path in sorted(glob.glob(os.path.join(gen, "blocks_*.rs"))):
        with open(path) as fh:
            text = fh.read()
        starts = [m for m in re.finditer(r"^pub fn r_([0-9A-F]+)\(", text, re.M)]
        for i, m in enumerate(starts):
            end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
            body = text[m.start() : end]
            nxt = re.search(r"^pub fn ", body[10:], re.M)
            if nxt:
                body = body[: nxt.start() + 10]
            out[m.group(1)] = body.count("ic += 1;")
    return out


def symbols(binary: str) -> list[tuple[int, str]]:
    txt = subprocess.run(
        ["nm", "-n", binary], capture_output=True, text=True, check=True
    ).stdout
    syms = []
    for line in txt.splitlines():
        m = SYM.match(line)
        if m:
            syms.append((int(m.group(1), 16), m.group(2)))
    return syms


def disasm(binary: str, lo: int, hi: int) -> list[tuple[str, str]]:
    txt = subprocess.run(
        [
            objdump(),
            "-d",
            "--no-show-raw-insn",
            "--start-address=0x%x" % lo,
            "--stop-address=0x%x" % hi,
            binary,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    rows = []
    for line in txt.splitlines():
        m = re.match(r"\s*[0-9a-f]+:\s+(\S+)\s*(.*)$", line)
        if m:
            rows.append((m.group(1), m.group(2)))
    return rows


def analyse(binary: str, gen: str, regions: list[str] | None) -> list[dict]:
    sites = region_sites(gen)
    syms = symbols(binary)
    addr = {n: a for a, n in syms}
    order = [a for a, _ in syms]
    result = []
    for name, n_sites in sorted(sites.items()):
        if regions and name not in regions:
            continue
        sym = next((s for s in addr if s.endswith("r_" + name)), None)
        if sym is None or not n_sites:
            continue
        lo = addr[sym]
        nxt = [a for a in order if a > lo]
        hi = nxt[0] if nxt else lo
        rows = disasm(binary, lo, hi)
        n = len(rows)
        cnt = {"branch": 0, "call": 0, "load": 0, "store": 0, "sp": 0}
        for mn, ops in rows:
            if BRANCH.match(mn):
                cnt["branch"] += 1
            elif CALL.match(mn):
                cnt["call"] += 1
            elif LOAD.match(mn):
                cnt["load"] += 1
            elif STORE.match(mn):
                cnt["store"] += 1
            if SP_REL.search(ops) and (LOAD.match(mn) or STORE.match(mn)):
                cnt["sp"] += 1
        result.append(
            {
                "region": name,
                "bytes": hi - lo,
                "mach_insns": n,
                "sharc_sites": n_sites,
                "bytes_per_site": (hi - lo) / n_sites,
                "mach_per_site": n / n_sites,
                **cnt,
            }
        )
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("binary")
    ap.add_argument("gen")
    ap.add_argument("--regions", default="")
    ap.add_argument("--top", type=int, default=0)
    ap.add_argument("--json")
    args = ap.parse_args()
    regs = [r for r in args.regions.split(",") if r] or None
    rows = analyse(args.binary, args.gen, regs)
    rows.sort(key=lambda r: -r["bytes"])
    if args.top:
        rows = rows[: args.top]
    print(
        "region    bytes  mach  sites  B/site  mach/site  br%   call%  ld%   st%   sp-slot%"
    )
    tot = {"bytes": 0, "mach_insns": 0, "sharc_sites": 0}
    for r in rows:
        m = r["mach_insns"]
        print(
            "%-8s %7d %6d %6d %7.1f %9.1f %5.1f %5.1f %5.1f %5.1f %6.1f"
            % (
                r["region"],
                r["bytes"],
                m,
                r["sharc_sites"],
                r["bytes_per_site"],
                r["mach_per_site"],
                100 * r["branch"] / m,
                100 * r["call"] / m,
                100 * r["load"] / m,
                100 * r["store"] / m,
                100 * r["sp"] / m,
            )
        )
        for k in tot:
            tot[k] += r[k]
    if tot["sharc_sites"]:
        print(
            "total    %7d %6d %6d %7.1f %9.1f"
            % (
                tot["bytes"],
                tot["mach_insns"],
                tot["sharc_sites"],
                tot["bytes"] / tot["sharc_sites"],
                tot["mach_insns"] / tot["sharc_sites"],
            )
        )
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rows, fh, indent=1)


if __name__ == "__main__":
    main()
