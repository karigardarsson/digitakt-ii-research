"""Cycles per DSP block of the SHARC+ code the firmware runs, from the manual's stall rules.

    uv run python -m tools.sharc_dev.sharc_cpi memspec IMAGE            # DAG base registers per PC for the run
    uv run python -m tools.sharc_dev.sharc_cpi run DEVICE IMAGE NAME:SECONDS... [--patch FILE]
    uv run python -m tools.sharc_dev.sharc_cpi report --out out/clock/sharc.json [--md out/clock/SHARC-REPORT.md] \\
        --image dn2=dn2-1.11 --image dt2=dt2-1.16 [--pair BASE=TEST]... FILE.tsv...

Counting: a `coupled-run` built with the engine's SHARC_CYCMIX diagnostic (packages/emulator/sharc/src/cycmix.rs;
interpreter only, so the run uses SHARC_AOT=0 SHARC_FAST_REGIONS= SHARC_BLOCKTRANS=) writes every executed PC with its
count, every executed (previous PC, PC) pair, the address bucket of the data accesses of each PC and, for L2 and
external data, how often the 64-byte line changed. The idle loop is skipped analytically by the engine and not counted.
The decoded instruction (registers read and written, memory access, branch kind) comes from the program database
`out/sharcdb/IMAGE.sqlite` (`tools/sharc_dev/sharcdb`), which is derived from the firmware and stays under out/.

The rules and their page references are in tools/sharc_dev/sharc_timing.py. Every cost is a (low, nominal, high)
triple. Low has no assumed memory latency at all; nominal and high use the assumed L2 and DDR fill times of
sharc_timing.py (the manual gives none).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict

from tools import python_env
from tools.sharc_dev import sharc_timing as T

ROOT = pathlib.Path(__file__).resolve().parents[2]
SHARCDB = ROOT / "out" / "sharcdb"
LOOP = re.compile(r"^DO 0x([0-9a-f]+) UNTIL (\w+)")
PM = re.compile(r"\bPM\(")


# ---------------------------------------------------------------------------------------------------------------
# The profile written by the engine


class Profile:
    def __init__(self, path: pathlib.Path, blocks: int | None = None):
        self.path = path
        self.counts: dict[int, int] = {}
        self.pairs: dict[tuple[int, int], int] = {}
        self.mem: dict[int, list[list[int]]] = defaultdict(lambda: [[0] * 8, [0] * 8])
        self.same: dict[int, int] = {}
        self.lines: dict[int, int] = defaultdict(int)  # pc -> line changes (both slots)
        self.far: Counter = Counter()
        self.unknown: Counter = Counter()
        self.idle = (0, 0, 0)
        self.cache: dict[tuple[str, int], tuple[int, int]] = {}
        self.btb: dict[int, list[int]] = {}
        for raw in path.read_text().splitlines():
            f = raw.split("\t")
            tag = f[0]
            if tag == "P":
                self.counts[int(f[1], 16)] = int(f[2])
            elif tag == "T":
                self.pairs[(int(f[1], 16), int(f[2], 16))] = int(f[3])
            elif tag == "M":
                self.mem[int(f[1], 16)][int(f[2])] = [int(x) for x in f[3:11]]
            elif tag == "S":
                self.same[int(f[1], 16)] = int(f[2])
            elif tag == "L":
                self.lines[int(f[1], 16)] += int(f[3])
            elif tag == "R":
                self.far[int(f[1], 16)] += int(f[2])
            elif tag == "U":
                self.unknown[int(f[2], 16)] += int(f[3])
            elif tag == "C":
                self.cache[(f[1], int(f[2]))] = (int(f[3]), int(f[4]))
            elif tag == "B":
                self.btb[int(f[1], 16)] = [int(x) for x in f[2:10]]
            elif tag == "I":
                self.idle = (int(f[1], 16), int(f[2], 16), int(f[3], 16))
        self.blocks = blocks

    def is_idle(self, pc: int) -> bool:
        lo, hi, _ = self.idle
        return lo <= pc <= hi


# ---------------------------------------------------------------------------------------------------------------
# The decoded instructions


def load_instructions(image: str, pcs: set[int]) -> dict[int, T.Ins]:
    db = sqlite3.connect(f"file:{SHARCDB / (image + '.sqlite')}?mode=ro", uri=True)
    db.execute("create temp table want(sw integer primary key)")
    db.executemany("insert into want values (?)", [(p,) for p in pcs])
    rows = db.execute(
        "select i.sw, i.width, i.mnemonic, i.cond from insn i join want w on w.sw = i.sw where i.image = ?",
        (image,),
    ).fetchall()
    defs: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for sw, reg, kind in db.execute(
        "select r.sw, r.reg, r.kind from regdef r join want w on w.sw = r.sw where r.image = ?",
        (image,),
    ):
        defs[sw].append((reg, kind))
    uses: dict[int, set[str]] = defaultdict(set)
    for sw, reg in db.execute(
        "select r.sw, r.reg from reguse r join want w on w.sw = r.sw where r.image = ?",
        (image,),
    ):
        uses[sw].add(reg)
    mem: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for sw, space, base in db.execute(
        "select m.sw, m.space, m.base_reg from mem_access m join want w on w.sw = m.sw where m.image = ?",
        (image,),
    ):
        mem[sw].append((space or "", base or ""))
    edges: dict[int, list[tuple[int, str, int]]] = defaultdict(list)
    for frm, to, kind, delayed in db.execute(
        "select e.from_sw, e.to_sw, e.kind, e.delayed from edges e join want w on w.sw = e.from_sw where e.image = ?",
        (image,),
    ):
        edges[frm].append((to, kind, delayed or 0))
    out: dict[int, T.Ins] = {}
    for sw, width, text, cond in rows:
        text = text or ""
        kinds = {k for _, k, _ in edges.get(sw, [])}
        branch = ""
        for k in ("cond_jump", "call", "return", "indirect", "jump"):
            if k in kinds:
                branch = k
                break
        delayed = any(d for _, k, d in edges.get(sw, []) if k != "fallthrough")
        targets = tuple(
            t
            for t, k, _ in edges.get(sw, [])
            if k not in ("fallthrough",) and t is not None
        )
        loop = LOOP.match(text)
        loop_end = 0
        counter = False
        if loop:
            branch = "loop"
            loop_end = int(loop.group(1), 16)
            counter = loop.group(2) == "LCE"
        out[sw] = T.Ins(
            pc=sw,
            nbytes=width or 2,
            text=text,
            always=(cond in (None, 31)) and not text.startswith("IF "),
            defs=tuple(defs.get(sw, [])),
            uses=frozenset(uses.get(sw, set())),
            mem=tuple(mem.get(sw, [])),
            branch=branch,
            delayed=delayed,
            targets=targets,
            loop_end=loop_end,
            loop_counter=counter,
        )
    db.close()
    return out


# ---------------------------------------------------------------------------------------------------------------
# Cost of one profile


def cost_profile(prof: Profile, image: str, blocks: int) -> dict:
    pcs = (
        {p for p in prof.counts if not prof.is_idle(p)}
        | {a for a, _ in prof.pairs}
        | {b for _, b in prof.pairs}
    )
    insns = load_instructions(image, pcs)
    busy_pcs = {p for p in prof.counts if not prof.is_idle(p)}
    n_busy = sum(prof.counts[p] for p in busy_pcs)
    missing = sum(
        prof.counts[p] for p in busy_pcs if p not in insns or not insns[p].text
    )
    res: dict = {
        "blocks": blocks,
        "busy_instructions": n_busy,
        "undecoded_instructions": missing,
    }
    parts: dict[str, T.Cost] = {}

    def acc(name: str, c: T.Cost) -> None:
        parts[name] = T.add(parts.get(name, T.ZERO), c)

    # adjacent-pair rules
    for (a, b), n in prof.pairs.items():
        ia, ib = insns.get(a), insns.get(b)
        if ia is None or ib is None or prof.is_idle(a) or prof.is_idle(b):
            continue
        acc("dag_load_to_use", T.scale(T.dag_use(ia, ib), n))
        acc("float_forward", T.scale(T.float_forward(ia, ib), n))
        acc("float_to_move", T.scale(T.float_to_move(ia, ib), n))
    # branches against the simulated BTB (engine side: 128 sets x 2 ways, 2-bit counters)
    branch_pcs = [p for p in prof.btb if p in insns]
    res["btb"] = {
        "branches": len(branch_pcs),
        "executions": sum(prof.btb[p][0] for p in branch_pcs),
        "hit_fraction": (
            sum(
                prof.btb[p][2] + prof.btb[p][3] + prof.btb[p][4] + prof.btb[p][5]
                for p in branch_pcs
            )
            / max(1, sum(prof.btb[p][0] for p in branch_pcs))
        ),
        "mispredicted_fraction_of_conditional": 0.0,
    }
    ncond = nmis = 0
    for p in branch_pcs:
        ins = insns[p]
        if ins.is_rti:  # Table 4-40 footnote **: 7 cycles of flush, no BTB
            acc("branch_uncond", (float(T.RTI_FLUSH * prof.btb[p][1]),) * 3)
            continue
        acc(
            "branch_cond"
            if ins.conditional or ins.branch == "cond_jump"
            else "branch_uncond",
            T.branch_cost_sim(ins, prof.btb[p]),
        )
        if ins.conditional or ins.branch == "cond_jump":
            ncond += prof.btb[p][0]
            nmis += prof.btb[p][4] + prof.btb[p][5]
    res["btb"]["mispredicted_fraction_of_conditional"] = nmis / max(1, ncond)
    sorted_pcs = sorted(insns)
    for p in busy_pcs:
        loop_ins = insns.get(p)
        if loop_ins is None or loop_ins.branch != "loop":
            continue
        ins = loop_ins
        end = ins.loop_end
        body = [q for q in sorted_pcs if ins.next_pc <= q <= end]
        tail = body[-11:]
        cof = any(
            insns[q].branch in ("jump", "call", "return", "indirect", "cond_jump")
            or insns[q].is_idle
            for q in tail
        )
        executions = prof.counts[p]
        iters = prof.counts.get(end, 0) / executions if executions else 0
        acc("loop_exit", T.loop_exit_cost(ins, len(body), cof, iters, executions))
    # data bus structure: PM access conflicts (code in L1) and two accesses in one L1 block
    pm_pcs = [
        p
        for p in busy_pcs
        if p in insns
        and any(s == "PM" for s, _ in insns[p].mem)
        and T.region_of_pc(p) == "l1"
    ]
    pm_exec = sum(prof.counts[p] for p in pm_pcs)
    acc("pm_conflict_cache", T.conflict_cache_misses(len(pm_pcs), pm_exec))
    same = sum(v for p, v in prof.same.items() if p in busy_pcs)
    acc("same_block", T.scale(T.SAME_BLOCK, same))
    # instruction fetch from L2 through the I-cache (engine side: 2 ways, 64-byte lines)
    ic = {kb: m for (k, kb), m in prof.cache.items() if k == "i"}
    res["icache"] = {
        "accesses_per_block": {kb: a / blocks for kb, (a, _) in ic.items()},
        "misses_per_block": {kb: m / blocks for kb, (_, m) in ic.items()},
    }
    misses = ic.get(16, (0, 0))[1]
    acc("l2_code_fill", T.scale(T.L2_FILL, misses))
    # data in L2 / external memory through the data cache (engine side: 2 ways, 64-byte lines); system MMRs
    far = {"l2": 0, "ext": 0, "mmr": 0}
    for p, slots in prof.mem.items():
        if p not in busy_pcs:
            continue
        for slot in slots:
            far["l2"] += slot[5]
            far["ext"] += slot[6]
            far["mmr"] += slot[7]
    # two data caches per core, DM ("d") and PM ("p"), 16 KB each assumed
    dc: dict[int, tuple[int, int]] = {}
    for (k, kb), (a, m) in prof.cache.items():
        if k in ("d", "p"):
            da, dm = dc.get(kb, (0, 0))
            dc[kb] = (da + a, dm + m)
    d_acc, d_miss = dc.get(16, (0, 0))
    share_ext = far["ext"] / max(1, far["l2"] + far["ext"])
    far["accesses"] = d_acc
    far["misses_16kb"] = d_miss
    res["dcache"] = {
        "accesses_per_block": {kb: a / blocks for kb, (a, _) in dc.items()},
        "misses_per_block": {kb: m / blocks for kb, (_, m) in dc.items()},
        "misses_16kb_dm": prof.cache.get(("d", 16), (0, 0))[1] / blocks,
        "misses_16kb_pm": prof.cache.get(("p", 16), (0, 0))[1] / blocks,
    }
    # high: each miss may also write a dirty line back (two line transfers)
    acc(
        "l2_data_fill",
        (
            T.L2_FILL[0] * d_miss * (1 - share_ext),
            T.L2_FILL[1] * d_miss * (1 - share_ext),
            2 * T.L2_FILL[2] * d_miss * (1 - share_ext),
        ),
    )
    acc(
        "ext_data_fill",
        (
            T.EXT_FILL[0] * d_miss * share_ext,
            T.EXT_FILL[1] * d_miss * share_ext,
            2 * T.EXT_FILL[2] * d_miss * share_ext,
        ),
    )
    acc("mmr_access", T.scale(T.MMR_ACCESS, far["mmr"]))
    res["data"] = far
    res["data_by_bucket"] = {
        name: sum(s[i] for slots in prof.mem.values() for s in slots)
        for i, name in enumerate(T.BUCKETS)
    }
    res["code_by_region"] = {
        r: sum(
            c
            for p, c in prof.counts.items()
            if not prof.is_idle(p) and T.region_of_pc(p) == r
        )
        for r in ("l1", "l2", "other")
    }
    res["conditional_branch_executions"] = ncond
    # per block
    per_block = {k: [x / blocks for x in v] for k, v in parts.items()}
    stalls = [
        sum(v[i] for k, v in per_block.items() if k in STALL_RULES) for i in range(3)
    ]
    memory = [
        sum(v[i] for k, v in per_block.items() if k in MEMORY_RULES) for i in range(3)
    ]
    base = n_busy / blocks
    res["per_block"] = per_block
    res["base_cycles_per_block"] = base
    res["stall_cycles_per_block"] = stalls
    res["memory_cycles_per_block"] = memory
    res["cycles_per_block"] = [base + stalls[i] + memory[i] for i in range(3)]
    # sensitivity of the nominal total to the I-cache size (SHL1C_CFG.ICASIZ, not observed) and to the clock
    sens = {}
    for kb, (_, m) in sorted((kb, v) for (k, kb), v in prof.cache.items() if k == "i"):
        sens[f"icache_{kb}KB"] = (
            res["cycles_per_block"][1]
            - per_block["l2_code_fill"][1]
            + m * T.L2_FILL[1] / blocks
        )
    res["nominal_cycles_by_icache"] = sens
    res["load_1ghz"] = [c / T.PERIOD_INSNS for c in res["cycles_per_block"]]
    res["load_800mhz"] = [c / (T.PERIOD_INSNS * 0.8) for c in res["cycles_per_block"]]
    res["emulator_load"] = base / T.PERIOD_INSNS
    return res


STALL_RULES = {
    "dag_load_to_use", "float_forward", "float_to_move", "branch_cond", "branch_uncond", "loop_exit",
    "pm_conflict_cache", "same_block",
}  # fmt: skip
MEMORY_RULES = {"l2_code_fill", "l2_data_fill", "ext_data_fill", "mmr_access"}


# ---------------------------------------------------------------------------------------------------------------
# Commands


def memspec_path(image: str) -> pathlib.Path:
    """The memspec file of an image. `memspec-ea-` since the slots carry the pre-modify (effective address); the old
    `memspec-` files hold bare I numbers and are still read by older engines."""
    return ROOT / "out" / "clock" / "sharc" / f"memspec-ea-{image}.txt"


def access_scale(width: str) -> int:
    """Bytes by which a byte-space modifier is scaled for an access of this width (PRM p. 6-9, Table 6-2)."""
    if width.startswith("byte"):
        return 1
    if width.startswith("short-word"):
        return 2
    return 4


def memspec_slot(
    base: str, modifier: str, u: int | None, form: str, width: str, text: str
) -> str | None:
    """One data access as a memspec slot: `I` (post-modify), `I+mM/S` or `I+K/S` (pre-modify), or None when the
    base is not an I register."""
    if not re.fullmatch(r"I\d+", base):
        return None
    reg = base[1:]
    if form == "i+imm":
        pre = "post-modify" not in text
    elif form == "direct":
        pre = True
    elif form == "i,m-mod":
        pre = u == 0
    else:  # dual moves and the shift forms are post-modify only
        pre = False
    if not pre or not modifier:
        return reg
    s = access_scale(width or "")
    if re.fullmatch(r"M\d+", modifier):
        return f"{reg}+m{modifier[1:]}/{s}"
    try:
        k = int(modifier, 0)
    except ValueError:
        return reg
    return f"{reg}+{k}/{s}"


def memspec_slots(image: str) -> dict[int, list[str]]:
    """Per PC the memspec slots of its data accesses, from the program database."""
    db = sqlite3.connect(f"file:{SHARCDB / (image + '.sqlite')}?mode=ro", uri=True)
    out: dict[int, list[str]] = defaultdict(list)
    for sw, base, mod, u, form, width, text in db.execute(
        "select m.sw, m.base_reg, m.modifier, m.u, m.form, m.width, i.mnemonic from mem_access m "
        "join insn i on i.image = m.image and i.sw = m.sw where m.image = ? order by m.rowid",
        (image,),
    ):
        slot = memspec_slot(
            base or "", str(mod or ""), u, form or "", width or "", text or ""
        )
        if slot is not None:
            out[sw].append(slot)
    db.close()
    return out


def cmd_memspec(args) -> int:
    """The per-PC file the engine's profile reads: `pc nbytes branch delayed [slots of the data accesses]` (see `memspec_slot`)."""
    db = sqlite3.connect(f"file:{SHARCDB / (args.image + '.sqlite')}?mode=ro", uri=True)
    pcs = {
        r[0]
        for r in db.execute(
            "select sw from insn where image = ? and mnemonic != ''", (args.image,)
        )
    }
    db.close()
    insns = load_instructions(args.image, pcs)
    slots = memspec_slots(args.image)
    out = pathlib.Path(args.out or memspec_path(args.image))
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for pc in sorted(insns):
        ins = insns[pc]
        branch = 0
        if ins.branch in ("jump", "call", "return", "indirect", "cond_jump"):
            branch = 2 if (ins.conditional or ins.branch == "cond_jump") else 1
        regs = slots.get(pc, [])
        lines.append(
            f"{pc:x} {ins.nbytes} {branch} {int(ins.delayed)} {' '.join(regs)}".rstrip()
        )
    out.write_text("\n".join(lines) + "\n")
    print(out, len(lines))
    return 0


def cmd_run(args) -> int:
    """Run named scenarios interpreter-only with the profile on (about 50 s of wall per audio second)."""
    out_dir = ROOT / "out" / "clock" / "sharc" / "mix"
    out_dir.mkdir(parents=True, exist_ok=True)
    memspec = memspec_path(args.image)
    if not memspec.is_file():
        cmd_memspec(argparse.Namespace(image=args.image, out=None))
    status = 0
    for spec in args.scenarios:
        name, _, seconds = spec.partition(":")
        label = f"{args.label or args.device}-{name}"
        env = {
            **python_env(),
            "SHARC_CYCMIX": "1",
            "SHARC_CYCMIX_OUT": str(out_dir / f"{label}.tsv"),
            "SHARC_CYCMIX_MEM": str(memspec),
            "SHARC_FAST_REGIONS": "",
            "SHARC_BLOCKTRANS": "",
            "SHARC_AOT": "0",
        }
        cmd = [
            sys.executable, "-m", "tools.emulator.coupled", "scenario", "--no-build", "--no-cache",
            "--device", args.device, "--out", str(out_dir / f"{label}.json"),
        ]  # fmt: skip
        if seconds:
            cmd += ["--seconds", seconds]
        cmd += [name]
        if args.patch:
            cmd += ["--", "--dsp-patch", args.patch]
        status |= subprocess.run(cmd, cwd=ROOT, env=env, check=False).returncode
    return status


def blocks_of(tsv: pathlib.Path) -> int:
    report = json.loads(tsv.with_suffix(".json").read_text())
    return int(report["scenarios"][0]["frames"])


def cmd_report(args) -> int:
    images = dict(x.split("=", 1) for x in args.image)
    results = {}
    for f in args.files:
        path = pathlib.Path(f)
        device = path.name[:3]  # dn2 or dt2 (the label of `run` starts with the device)
        prof = Profile(path)
        res = cost_profile(prof, images[device], blocks_of(path))
        res["device"] = device
        results[path.stem] = res
    pairs = {}
    for spec in args.pair or []:
        base, test = spec.split("=", 1)
        b, t = results[base], results[test]
        pairs[spec] = {
            "added_instructions_per_block": t["busy_instructions"] / t["blocks"]
            - b["busy_instructions"] / b["blocks"],
            "added_cycles_per_block": [
                t["cycles_per_block"][i] - b["cycles_per_block"][i] for i in range(3)
            ],
            "total_load_1ghz": t["load_1ghz"],
        }
    out = {"results": results, "pairs": pairs}
    pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(args.out).write_text(json.dumps(out, indent=1, sort_keys=True))
    print(args.out)
    if args.md:
        pathlib.Path(args.md).write_text(markdown(out))
        print(args.md)
    return 0


def pct(v: list[float]) -> str:
    return " / ".join(f"{x * 100:.0f}" for x in v)


def markdown(out: dict) -> str:
    lines = [
        "| run | busy instr/block | emulator load | cycles/block lo / nom / hi | load % at 1 GHz lo / nom / hi | at 800 MHz |",
        "|---|---:|---:|---|---|---|",
    ]
    for name, r in sorted(out["results"].items()):
        b = r["blocks"]
        cyc = " / ".join(f"{x:,.0f}" for x in r["cycles_per_block"])
        lines.append(
            f"| {name} | {r['busy_instructions'] / b:,.0f} | {r['emulator_load'] * 100:.0f} % | {cyc} | "
            f"{pct(r['load_1ghz'])} | {pct(r['load_800mhz'])} |"
        )
    rules = sorted({k for r in out["results"].values() for k in r["per_block"]})
    lines += ["", "Cycles per block by rule (nominal; low / high in the JSON):", ""]
    names = sorted(out["results"])
    lines.append("| rule | " + " | ".join(names) + " |")
    lines.append("|---|" + "---:|" * len(names))
    for rule in rules:
        row = " | ".join(
            f"{out['results'][n]['per_block'].get(rule, [0, 0, 0])[1]:,.0f}"
            for n in names
        )
        lines.append(f"| {rule} | {row} |")
    if out["pairs"]:
        lines += ["", "Added by a patch (paired runs of the same scenario):", ""]
        lines.append(
            "| pair | instructions/block | cycles/block lo / nom / hi | total load 1 GHz % lo / nom / hi |"
        )
        lines.append("|---|---:|---|---|")
        for k, v in out["pairs"].items():
            lines.append(
                f"| {k} | {v['added_instructions_per_block']:.1f} | "
                + " / ".join(f"{x:,.0f}" for x in v["added_cycles_per_block"])
                + f" | {pct(v['total_load_1ghz'])} |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("memspec")
    p.add_argument("image")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_memspec)
    p = sub.add_parser("run")
    p.add_argument("device", choices=("dn2", "dt2"))
    p.add_argument("image")
    p.add_argument("scenarios", nargs="+")
    p.add_argument("--patch")
    p.add_argument("--label")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("report")
    p.add_argument("files", nargs="+")
    p.add_argument("--image", action="append", required=True)
    p.add_argument("--pair", action="append")
    p.add_argument("--out", required=True)
    p.add_argument("--md")
    p.set_defaults(fn=cmd_report)
    args = ap.parse_args(argv)
    os.chdir(ROOT)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
