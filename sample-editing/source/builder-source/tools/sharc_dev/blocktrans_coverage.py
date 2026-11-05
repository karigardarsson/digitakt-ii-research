#!/usr/bin/env python3
"""What stops the fast-tier cluster translator from covering what AOT runs.

Arms `SHARC_BLOCKTRANS` cluster roots for the top-N AOT entries of a region
cost ranking, runs a short coupled idle scenario with `SHARC_FAST_COV=1`
(coverage lines from `packages/emulator/sharc/src/fast/`), and reports

  (a) a table per root: AOT ms/s at stake, calls/s, translated fraction of
      the dynamic instructions, first stop;
  (b) stop FAMILIES ranked by the AOT ms/s they block (as first stop and as
      any stop), with roots, PCs, forms and decoded instruction text;
  (c) roots that are already fully covered (candidates for the default list).

    uv run python -m tools.sharc_dev.blocktrans_coverage [--n 150] [--seconds 2]
        [--batch 0] [--binary BIN] [--ranked ranked.json] [--out DIR]
        [--reanalyse]   # re-analyse the logs already in --out, no emulator run

Build the binary once, non-PGO (one ~4 min sharc-gen compile):

    SHARC_GEN_DIR=$PWD/out/native/dn2-audio/gen cargo build --release --locked \
      -p digi-coupled --features coupled-audio --bin coupled-run \
      --target-dir out/native/target/cov

Method notes. Roots are armed as clusters only (no `SHARC_FAST_REGIONS`), so
every other entry stays on AOT. Arming many roots at once can perturb the run
(a decline at a PC without an AOT block single-steps the interpreter); the
tool reports the interpreter count against an unarmed baseline run, and
`--batch K` probes the roots K at a time. Region-profile timings in the
ranking carry about 33% overhead: treat ms as relative. The "translated
fraction" is kernel instructions / (kernel instructions + tail), where the
tail is the instruction count between a kernel exit and the next fast-tier
call (capped at 4096 per exit, so it is an upper bound on what AOT still runs
after the exit); an exit at RETURN has no tail.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import pathlib
import re
import sqlite3
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]

RANKED = ROOT / "out/native/runs/20261005-region-cost/ranked.json"
BINARY = ROOT / "out/native/target/cov/release/coupled-run"
DB = ROOT / "out/sharcdb/dn2-1.11.sqlite"
IDLE = (0xB88A49, 0xB88ABB)
GAP_CAP = 4096  # matches COV_GAP_CAP in packages/emulator/sharc/src/fast/mod.rs
DECLINES = ("shape", "req", "window", "budget", "exit0")

# -- log parsing (pure) -------------------------------------------------------

_KV = re.compile(r"(\w+)=")


def parse_kv(line: str, keys: tuple[str, ...]) -> dict[str, str]:
    """Split `cov KIND k=v k=v ...` where the last keys may hold spaces.

    `keys` lists the keys in order; a value runs to the next key.
    """
    out: dict[str, str] = {}
    pos = [(k, line.find(f" {k}=")) for k in keys]
    pos = [(k, p) for k, p in pos if p >= 0]
    pos.sort(key=lambda kp: kp[1])
    for i, (k, p) in enumerate(pos):
        start = p + len(k) + 2
        end = pos[i + 1][1] if i + 1 < len(pos) else len(line)
        out[k] = line[start:end].strip()
    return out


@dataclasses.dataclass
class Root:
    pc: int
    built: bool = False
    refused: str | None = None
    insns: int = 0
    pcs: list[int] = dataclasses.field(default_factory=list)
    stops: list[dict] = dataclasses.field(default_factory=list)
    denies: list[dict] = dataclasses.field(default_factory=list)
    calls: int = 0
    run_insns: int = 0
    declined: dict[str, int] = dataclasses.field(default_factory=dict)
    decl_why: dict[str, int] = dataclasses.field(default_factory=dict)
    exits: list[dict] = dataclasses.field(default_factory=list)


def parse_log(text: str) -> dict[int, Root]:
    """Parse `cov ...` lines (other lines are ignored) into roots by PC.

    A root rebuilt after an entry decline keeps its last build.
    """
    roots: dict[int, Root] = {}

    def get(pc: int) -> Root:
        return roots.setdefault(pc, Root(pc))

    for line in text.splitlines():
        if not line.startswith("cov "):
            continue
        kind, _, rest = line[4:].partition(" ")
        rest = " " + rest
        if kind == "built":
            kv = parse_kv(rest, ("root", "insns", "kernel_ops", "windows", "pcs"))
            r = get(int(kv["root"], 16))
            r.built, r.refused = True, None
            r.insns = int(kv["insns"])
            r.pcs = [int(x, 16) for x in kv.get("pcs", "").split(",") if x]
            r.stops = []  # stops are re-logged with each build
        elif kind in ("stop", "deny"):
            kv = parse_kv(rest, ("root", "at", "form", "why"))
            r = get(int(kv["root"], 16))
            d = {"at": int(kv["at"], 16), "form": kv["form"], "why": kv["why"]}
            (r.stops if kind == "stop" else r.denies).append(d)
        elif kind == "refused":
            kv = parse_kv(rest, ("root", "why"))
            r = get(int(kv["root"], 16))
            r.built, r.refused = False, kv["why"]
        elif kind == "run":
            kv = parse_kv(
                rest,
                (
                    "root",
                    "built",
                    "refused",
                    "calls",
                    "insns",
                    "declined",
                    "exits",
                    "declwhy",
                ),
            )
            r = get(int(kv["root"], 16))
            r.calls = int(kv["calls"])
            r.run_insns = int(kv["insns"])
            dec = [int(x) for x in re.findall(r"\d+", kv.get("declined", ""))]
            r.declined = dict(zip(DECLINES, dec, strict=False))
            r.exits = []
            for item in filter(None, kv.get("exits", "").split(";")):
                at, k, n, ins, gap = item.split(":")
                r.exits.append(
                    {
                        "at": int(at, 16),
                        "kind": k,
                        "n": int(n),
                        "insns": int(ins),
                        "gap": int(gap),
                    }
                )
            for item in filter(None, kv.get("declwhy", "").split(";")):
                n, _, why = item.partition("x ")
                r.decl_why[why] = int(n)
    return roots


# -- reason classes (pure) ----------------------------------------------------

_PC_PREFIX = re.compile(r"^(?:0x[0-9a-f]+: )+")
_INSN = re.compile(r"insn \d+ \((\w+)\): (.*)")

# (regex on the message, family) in priority order.
_RULES: list[tuple[str, str]] = [
    (r"^form (\S+)", "unsupported form {0}"),
    (r"^conditional (\S+)", "conditional execution of form {0}"),
    (r"^call$", "CALL not lowered"),
    (r"address range unknown", "address range unknown"),
    (r"too many windows", "too many windows"),
    (r"outside R/I/M", "register outside R/I/M"),
    (
        r"forgets|flag-forgetting|kernel flags|too many flag sources",
        "flag-forgetting op",
    ),
    (r"TRUNCATE", "MODE1.TRUNCATE set"),
    (r"L != 0|MODIFY", "L!=0 / circular MODIFY"),
    (r"circular|index length register|CBUFEN", "circular buffer constraints"),
    (r"compute|opcode", "unsupported compute"),
    (r"shiftimm", "unsupported shift"),
    (r"MR data move", "MR data move"),
    (r"long word", "long-word access (14a/15a/15b)"),
    (r"access width", "3b/4b access width (not 32-bit normal word)"),
    (r"loop abort|CI modifier", "loop abort / CI modifier"),
    (r"jump condition|jump without", "unsupported jump condition"),
    (r"jump target", "jump target not constant"),
    (r"delay slot|branch into|branch at", "delay slot / branch shape"),
    (r"zero-count DO|DO count register", "DO count form"),
    (r"register step", "loop shape: register step unstable"),
    (r"end of an outer|end is not|body not decoded|end inside", "loop shape"),
    (r"region size", "region size limit"),
    (r"undecodable|not decoded", "undecodable instruction"),
    (r"MODE1 unknown", "MODE1 unknown at build"),
    (r"path too deep|too many", "region too large"),
    (r"falls off", "region falls off its end"),
    (r"no generated core", "no generated core"),
]


def classify(why: str) -> tuple[str, str, str | None]:
    """Reason text -> (family, detail, form).

    The form is the instruction form named in the reason, if any. Reasons the
    translator wraps (`loop at ..: <why> inside`, `the entry instruction cannot
    run here: <why>`) are classified by the inner reason.
    """
    s = _PC_PREFIX.sub("", why.strip())
    s = re.sub(r"^the entry instruction cannot run here: ", "", s)
    m = re.match(r"^loop at 0x[0-9a-f]+: (.*)$", s)
    if m and not m.group(1).endswith(" inside"):
        # The loop's own structure (not an instruction inside it).
        return (
            "loop shape: " + re.sub(r"0x[0-9a-f]+|\d+", "#", m.group(1)),
            m.group(1),
            None,
        )
    s = re.sub(r"^loop at 0x[0-9a-f]+: ", "", s)
    s = re.sub(r" inside$", "", s)
    s = _PC_PREFIX.sub("", s)
    form = None
    m = _INSN.search(s)
    if m:
        form, s = m.group(1), m.group(2)
    if s == "cannot be lowered in this region":
        return "denied, reason not logged", s, form
    for pat, fam in _RULES:
        mm = re.search(pat, s)
        if mm:
            return fam.format(*mm.groups()), s, form
    if "undecodable" in why:
        return "undecodable instruction", s, form
    return "other: " + re.sub(r"0x[0-9a-f]+|\d+", "#", s)[:50], s, form


def decline_family(why: str) -> str:
    """An entry decline reason (`SlotCov::decl_why`) -> family."""
    w = re.sub(r"^req: ", "", why)
    if why.startswith("req: "):
        if w.startswith("known"):
            return "entry decline: register unknown at entry (known rN)"
        if w.startswith("eq"):
            return "entry decline: baked register constant changed (eq rN)"
        if w.startswith("misc"):
            return "entry decline: MODE1 / flags requirement (misc)"
        if w.startswith("within"):
            return "entry decline: circular index outside its range"
        if w.startswith("nw-plain"):
            return "entry decline: address not normal-word plain"
    if why.startswith("window"):
        return "entry decline: " + re.sub(r"window \d+", "window", why)
    return "entry decline: " + why


def inner_pc(why: str, default: int) -> int:
    """The PC a deny reason names (`0xPC: insn ...`), else DEFAULT."""
    m = re.match(r"^(0x[0-9a-f]+): ", why)
    return int(m.group(1), 16) if m else default


# -- program database ---------------------------------------------------------


class Db:
    """Read-only view of out/sharcdb/<image>.sqlite."""

    def __init__(self, path: pathlib.Path):
        self.c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self._insn: dict[int, tuple[str, str]] = {}

    def insn(self, sw: int) -> tuple[str, str]:
        if sw not in self._insn:
            row = self.c.execute(
                "SELECT form, mnemonic FROM insn WHERE sw=?", (sw,)
            ).fetchone()
            self._insn[sw] = row or ("?", "?")
        return self._insn[sw]

    def text(self, sw: int) -> str:
        return self.insn(sw)[1]

    def is_return(self, sw: int) -> bool:
        return self.text(sw).startswith(("RETURN", "RTI", "RTS"))

    def func(self, sw: int) -> dict | None:
        row = self.c.execute(
            "SELECT entry_sw, end_sw, n_insns FROM functions WHERE entry_sw<=? AND end_sw>? "
            "ORDER BY entry_sw DESC",
            (sw, sw),
        ).fetchone()
        return dict(zip(("entry", "end", "n_insns"), row, strict=True)) if row else None

    def block(self, sw: int) -> dict | None:
        row = self.c.execute(
            "SELECT start_sw, end_sw, n_insns FROM bblocks WHERE start_sw=?", (sw,)
        ).fetchone()
        return dict(zip(("start", "end", "n_insns"), row, strict=True)) if row else None


# -- analysis -----------------------------------------------------------------


def root_stops(r: Root) -> list[dict]:
    """Every stop of a root as {at, culprit, form, family, detail, kind}.

    A stop denied during the build carries the reason of its deny line; a
    deny inside a DO loop names the culprit PC in the reason.
    """
    deny_by_at = {d["at"]: d for d in r.denies}
    out = []
    for st in r.stops:
        why, form, culprit = st["why"], st["form"], st["at"]
        d = deny_by_at.get(st["at"])
        if d is not None and "cannot be lowered in this region" in why:
            why, culprit = d["why"], inner_pc(d["why"], st["at"])
            form = d["form"] if culprit == st["at"] else form
        fam, detail, rform = classify(why)
        out.append(
            {
                "at": st["at"],
                "culprit": culprit,
                "form": rform or form,
                "family": fam,
                "detail": detail,
                "source": "stop",
            }
        )
    seen = {s["at"] for s in out}
    for d in r.denies:
        if d["at"] in seen:
            continue
        fam, detail, rform = classify(d["why"])
        out.append(
            {
                "at": d["at"],
                "culprit": inner_pc(d["why"], d["at"]),
                "form": rform or d["form"],
                "family": fam,
                "detail": detail,
                "source": "deny",
            }
        )
    return out


def analyse_root(r: Root, ms: float, calls_s: float, secs: float, db: Db) -> dict:
    """One row of table (a) plus the data the family ranking needs."""
    ok_calls = r.calls - sum(r.declined.values())
    decl_frac = sum(r.declined.values()) / r.calls if r.calls else 0.0
    kernel = sum(e["insns"] for e in r.exits)
    tail = 0
    terminal_calls = 0
    exits = []
    for e in r.exits:
        term = db.is_return(e["at"])
        if term:
            terminal_calls += e["n"]
        else:
            tail += e["gap"]
        exits.append(
            {
                **e,
                "terminal": term,
                "text": db.text(e["at"]),
                "form": db.insn(e["at"])[0],
            }
        )
    frac = kernel / (kernel + tail) if (kernel + tail) else 0.0
    stops = root_stops(r)
    for s in stops:
        s["text"] = db.text(s["culprit"])
        s["stop_text"] = db.text(s["at"])
    entry: dict = {
        "pc": r.pc,
        "ms_per_s": ms,
        "calls_per_s_aot": calls_s,
        "built": r.built,
        "refused": r.refused,
        "calls": r.calls,
        "calls_per_s": r.calls / secs if secs else 0.0,
        "ok_calls": ok_calls,
        "declined": r.declined,
        "decl_why": r.decl_why,
        "decline_fraction": decl_frac,
        "kernel_insns_per_call": kernel / ok_calls if ok_calls else 0.0,
        "tail_insns_per_call": tail / ok_calls if ok_calls else 0.0,
        "translated_fraction": frac,
        "terminal_exit_fraction": terminal_calls / ok_calls if ok_calls else 0.0,
        "static_insns": r.insns,
        "exits": sorted(exits, key=lambda e: -e["n"]),
        "stops": stops,
        "block": db.block(r.pc),
        "func": db.func(r.pc),
    }
    fn = entry["func"]
    if fn:
        entry["static_in_function"] = sum(
            1 for p in r.pcs if fn["entry"] <= p < fn["end"]
        )
    # Share of AOT time still on AOT.
    if not r.built:
        remaining = 1.0
    elif r.calls == 0:
        remaining = 1.0  # never entered in this run
    else:
        remaining = decl_frac + (1 - decl_frac) * (1 - frac)
    entry["remaining_fraction"] = remaining
    entry["blocked_ms"] = ms * remaining
    # Entry declines by family (share of the declined calls).
    dtot = sum(r.decl_why.values())
    entry["decline_families"] = {}
    for why, n in r.decl_why.items():
        f = decline_family(why)
        entry["decline_families"][f] = entry["decline_families"].get(f, 0.0) + n / dtot
    # First stop: the dominant entry decline when most calls decline, else the
    # most taken non-terminal exit, else the lowest-PC stop.
    first = None
    live = [e for e in entry["exits"] if not e["terminal"]]
    if r.built and r.calls and decl_frac > 0.5 and entry["decline_families"]:
        fam = max(entry["decline_families"], key=entry["decline_families"].get)
        first = {
            "at": r.pc,
            "culprit": r.pc,
            "form": "-",
            "family": fam,
            "detail": max(r.decl_why, key=r.decl_why.get),
            "text": db.text(r.pc),
            "stop_text": db.text(r.pc),
        }
    elif r.built and live:
        e0 = live[0]
        first = next((s for s in stops if s["at"] == e0["at"]), None)
        if first is None:
            first = {
                "at": e0["at"],
                "culprit": e0["at"],
                "form": e0["form"],
                "family": f"guard/budget exit ({e0['kind']})",
                "detail": e0["kind"],
                "text": e0["text"],
                "stop_text": e0["text"],
            }
    elif stops:
        first = min(stops, key=lambda s: s["at"])
    if first is None and not r.built and r.refused:
        fam, detail, form = classify(r.refused)
        first = {
            "at": r.pc,
            "culprit": inner_pc(r.refused, r.pc),
            "form": form or "?",
            "family": fam,
            "detail": detail,
            "text": db.text(r.pc),
            "stop_text": db.text(r.pc),
        }
    entry["first_stop"] = first
    return entry


def fully_covered(e: dict) -> bool:
    """Built, entered, (almost) never declined, and every exit is a RETURN."""
    return bool(
        e["built"]
        and e["calls"] > 0
        and e["decline_fraction"] <= 0.01
        and e["exits"]
        and all(x["terminal"] for x in e["exits"])
    )


def families(rows: list[dict]) -> dict:
    """Family -> ranked blocked ms, as first stop and as any stop."""
    fam: dict[str, dict] = {}

    def slot(name: str) -> dict:
        return fam.setdefault(
            name,
            {
                "family": name,
                "first_ms": 0.0,
                "any_ms": 0.0,
                "first_roots": set(),
                "any_roots": set(),
                "pcs": collections.Counter(),
                "forms": collections.Counter(),
                "examples": {},
                "details": collections.Counter(),
            },
        )

    def note(f: dict, root: int, s: dict) -> None:
        f["pcs"][s["culprit"]] += 1
        f["forms"][s["form"]] += 1
        f["details"][s["detail"]] += 1
        f["examples"].setdefault(s["culprit"], s["text"])

    for e in rows:
        if e["blocked_ms"] <= 0:
            continue
        fs = e["first_stop"]
        if fs is not None:
            f = slot(fs["family"])
            f["first_ms"] += e["blocked_ms"]
            f["first_roots"].add(e["pc"])
            note(f, e["pc"], fs)
        seen = set()
        for s in e["stops"]:
            if s["family"] in seen or db_terminal(s):
                continue
            seen.add(s["family"])
            f = slot(s["family"])
            f["any_ms"] += e["blocked_ms"]
            f["any_roots"].add(e["pc"])
            note(f, e["pc"], s)
        if (
            fs is not None
            and fs["family"] not in seen
            and not fs["family"].startswith("entry decline")
        ):
            f = slot(fs["family"])
            f["any_ms"] += e["blocked_ms"]
            f["any_roots"].add(e["pc"])
        if e["decline_fraction"] > 0.01:
            for name, share in e["decline_families"].items():
                f = slot(name)
                f["any_ms"] += e["blocked_ms"] * share
                f["any_roots"].add(e["pc"])
    out = []
    for f in fam.values():
        out.append(
            {
                "family": f["family"],
                "first_stop_ms": round(f["first_ms"], 3),
                "any_stop_ms": round(f["any_ms"], 3),
                "first_stop_roots": sorted(f["first_roots"]),
                "any_stop_roots": sorted(f["any_roots"]),
                "pcs": [
                    {"pc": p, "n": n, "text": f["examples"].get(p, "")}
                    for p, n in f["pcs"].most_common(12)
                ],
                "forms": dict(f["forms"].most_common()),
                "details": dict(f["details"].most_common(8)),
            }
        )
    out.sort(key=lambda f: (-f["first_stop_ms"], -f["any_stop_ms"]))
    return {
        "by_first_stop": out,
        "by_any_stop": sorted(
            out, key=lambda f: (-f["any_stop_ms"], -f["first_stop_ms"])
        ),
    }


def db_terminal(s: dict) -> bool:
    """A stop that is just the RETURN ending a covered function."""
    return s.get("stop_text", "").startswith("RETURN")


# -- running ------------------------------------------------------------------


def select_roots(ranked: list[dict], n: int) -> list[dict]:
    rows = []
    for r in ranked:
        pc = int(r["pc"], 16)
        if r.get("tier") != "aot" or IDLE[0] <= pc <= IDLE[1]:
            continue
        rows.append(
            {"pc": pc, "ms": r["ms_per_audio_s"], "calls": r["calls_per_audio_s"]}
        )
    rows.sort(key=lambda r: -r["ms"])
    return rows[:n]


def run_once(
    binary, roots, seconds, out: pathlib.Path, tag: str, fast_log: bool
) -> dict:
    from tools.emulator import coupled_matrix as matrix

    env = matrix.config_env(matrix.CONFIGS["prod"], matrix.DEFAULT_SYX)
    for k in list(env):
        if k.startswith(("SHARC_FAST_", "SHARC_CONCRETISE", "SHARC_BLOCKTRANS")):
            env.pop(k)
    if roots:
        env["SHARC_BLOCKTRANS"] = ",".join(f"{p:#x}" for p in roots)
        env["SHARC_FAST_COV"] = "1"
    if fast_log:
        env["SHARC_FAST_LOG"] = "1"
    ready = matrix.READY
    res = out / f"{tag}.json"
    cmd = [
        str(binary),
        "--syx", str(matrix.DEFAULT_SYX),
        "--dsp-image", str(ready / "digi-audio-dn2-image.bin"),
        "--dsp-state", str(ready / "digi-audio-m5.snap.dsp"),
        "--cf-snapshot", str(ready / "digi-audio-m5.snap"),
        "--scenario", "idle",
        "--seconds", str(seconds),
        "--out", str(res),
    ]  # fmt: skip
    t0 = time.time()
    p = subprocess.run(
        cmd, cwd=ROOT, env=env, text=True, capture_output=True, timeout=1800
    )
    (out / f"{tag}.log").write_text(p.stderr)
    info: dict = {"tag": tag, "rc": p.returncode, "wall_s": round(time.time() - t0, 1)}
    try:
        s = json.loads(res.read_text())["scenarios"][0]
        info.update(
            audio_seconds=s["audio_seconds"],
            gates_ok=s["gates"]["ok"],
            pcm=s["pcm"]["sha256"][:16],
            dsp=s["dsp_instructions"],
        )
    except (OSError, KeyError, ValueError, IndexError):
        info["error"] = p.stderr[-400:]
    return info


def merge(parts: list[dict[int, Root]]) -> dict[int, Root]:
    out: dict[int, Root] = {}
    for p in parts:
        out.update(p)
    return out


# -- report -------------------------------------------------------------------


def fmt_pc(pc: int) -> str:
    return f"{pc:#x}"


def markdown(rep: dict) -> str:
    L: list[str] = []
    a = L.append
    a("# Block-translator coverage")
    a("")
    m = rep["meta"]
    a(
        f"{m['roots']} roots armed (top by AOT ms/s, idle range excluded), "
        f"{m['audio_seconds']:.2f} audio s, binary `{m['binary']}`, batches: {m['batches']}."
    )
    a("")
    a("Interpreter instructions (perturbation check):")
    a("")
    a("| run | interpreter | fast tier | aot block | built | refused | pcm |")
    a("|---|---:|---:|---:|---:|---:|---|")
    shown = rep["runs"]
    note = 0
    if len(shown) > 12:
        base = [r for r in shown if r["tag"] == "baseline"]
        rest = sorted(
            (r for r in shown if r["tag"] != "baseline"),
            key=lambda r: r.get("dsp", {}).get("interpreter", 0),
        )
        shown = base + [rest[0], rest[len(rest) // 2], rest[-1]]
        note = len(rest)
    for r in shown:
        d = r.get("dsp", {})
        a(
            f"| {r['tag']} | {d.get('interpreter', '?')} | {d.get('fast_tier', '?')} | "
            f"{d.get('aot_block', '?')} | {d.get('fast_built_total', '?')} | "
            f"{d.get('fast_refused_total', '?')} | {r.get('pcm', '?')} |"
        )
    if len(rep["runs"]) > 12:
        a(
            f"| ({note} batch runs: min, median, max by interpreter count; "
            "all in runs.json) | | | | | | |"
        )
    a("")
    a("AOT ms/s come from a region-profile build (about 33% overhead): relative only.")
    a("")
    a("## (b) Stop families, ranked by AOT ms/s blocked")
    a("")
    a(
        "Blocked ms = root AOT ms/s x share of the root still on AOT (entry declines + "
        "tail after exits). First-stop credit is not shared between families; any-stop "
        "credit counts the whole root for every family it hits, so it does not add up."
    )
    a("")
    a(
        "| # | family | first-stop ms/s | any-stop ms/s | roots (first/any) | forms | example PCs |"
    )
    a("|---:|---|---:|---:|---:|---|---|")
    for i, f in enumerate(rep["families"]["by_first_stop"][:25], 1):
        ex = "; ".join(f"{fmt_pc(p['pc'])} `{p['text'][:40]}`" for p in f["pcs"][:3])
        forms = ", ".join(f"{k}x{v}" for k, v in list(f["forms"].items())[:4])
        a(
            f"| {i} | {f['family']} | {f['first_stop_ms']:.1f} | {f['any_stop_ms']:.1f} | "
            f"{len(f['first_stop_roots'])}/{len(f['any_stop_roots'])} | {forms} | {ex} |"
        )
    a("")
    a("### Ranked by any stop")
    a("")
    a("| # | family | any-stop ms/s | first-stop ms/s | roots |")
    a("|---:|---|---:|---:|---:|")
    for i, f in enumerate(rep["families"]["by_any_stop"][:25], 1):
        a(
            f"| {i} | {f['family']} | {f['any_stop_ms']:.1f} | {f['first_stop_ms']:.1f} | "
            f"{len(f['any_stop_roots'])} |"
        )
    a("")
    a("### Family detail")
    for f in rep["families"]["by_first_stop"][:15]:
        a("")
        a(
            f"**{f['family']}** (first {f['first_stop_ms']:.1f}, any {f['any_stop_ms']:.1f} ms/s)"
        )
        a("")
        a("- roots: " + ", ".join(fmt_pc(p) for p in f["any_stop_roots"][:20]))
        for p in f["pcs"][:8]:
            a(f"- `{fmt_pc(p['pc'])}` x{p['n']}: `{p['text']}`")
        for d, n in list(f["details"].items())[:4]:
            a(f"- reason: `{d}` x{n}")
    a("")
    a("## (c) Fully covered roots (default-list candidates)")
    a("")
    a("| root | AOT ms/s | calls/s | insns/call | exit | function |")
    a("|---|---:|---:|---:|---|---|")
    for e in rep["fully_covered"]:
        fn = e["func"]
        ex = e["exits"][0]
        a(
            f"| {fmt_pc(e['pc'])} | {e['ms_per_s']:.1f} | {e['calls_per_s']:.0f} | "
            f"{e['kernel_insns_per_call']:.0f} | {fmt_pc(ex['at'])} {ex['text']} | "
            f"{fmt_pc(fn['entry']) if fn else '?'} |"
        )
    a("")
    a("## (a) Per root")
    a("")
    a(
        "| root | AOT ms/s | AOT calls/s | calls/s | insns/call | translated | decl | "
        "static in func | first stop (pc, form, family) |"
    )
    a("|---|---:|---:|---:|---:|---:|---:|---|---|")
    for e in rep["roots"]:
        fs = e["first_stop"]
        stop = (
            f"{fmt_pc(fs['culprit'])} {fs['form']} {fs['family']} `{fs['text'][:40]}`"
            if fs
            else "-"
        )
        fn = e["func"]
        st = (
            f"{e.get('static_in_function', 0)}/{fn['n_insns']}"
            if fn and e["built"]
            else ("refused" if e["refused"] else "-")
        )
        a(
            f"| {fmt_pc(e['pc'])} | {e['ms_per_s']:.1f} | {e['calls_per_s_aot']:.0f} | "
            f"{e['calls_per_s']:.0f} | {e['kernel_insns_per_call']:.0f} | "
            f"{100 * e['translated_fraction']:.0f}% | {100 * e['decline_fraction']:.0f}% | "
            f"{st} | {stop} |"
        )
    a("")
    return "\n".join(L)


def build_report(roots: dict[int, Root], sel: list[dict], runs, meta, db: Db) -> dict:
    secs = meta["audio_seconds"]
    rows = []
    for s in sel:
        r = roots.get(s["pc"], Root(s["pc"]))
        rows.append(analyse_root(r, s["ms"], s["calls"], secs, db))
    rows.sort(key=lambda e: -e["ms_per_s"])
    return {
        "meta": meta,
        "runs": runs,
        "roots": rows,
        "families": families(rows),
        "fully_covered": [e for e in rows if fully_covered(e)],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--n", type=int, default=150, help="top N AOT entries (default 150)"
    )
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument(
        "--batch", type=int, default=10, help="probe roots K at a time (0: all)"
    )
    ap.add_argument("--binary", type=pathlib.Path, default=BINARY)
    ap.add_argument("--ranked", type=pathlib.Path, default=RANKED)
    ap.add_argument("--db", type=pathlib.Path, default=DB)
    ap.add_argument("--out", type=pathlib.Path)
    ap.add_argument("--no-baseline", action="store_true")
    ap.add_argument(
        "--fast-log", action="store_true", help="also SHARC_FAST_LOG=1 (large)"
    )
    ap.add_argument(
        "--reanalyse", action="store_true", help="re-read the logs in --out, no run"
    )
    args = ap.parse_args(argv)

    sel = select_roots(json.loads(args.ranked.read_text()), args.n)
    out = (
        args.out
        or ROOT / "out/native/runs" / f"{time.strftime('%Y%m%d')}-blocktrans-coverage"
    )
    out.mkdir(parents=True, exist_ok=True)
    runs: list[dict] = []
    if args.reanalyse:
        runs = json.loads((out / "runs.json").read_text())
        parts = [
            parse_log(p.read_text())
            for p in sorted(out.glob("*.log"))
            if p.stem.startswith(("batch", "armed"))
        ]
        armed = [r for r in runs if r["tag"] != "baseline" and "audio_seconds" in r]
        meta_secs = armed[0]["audio_seconds"] if armed else args.seconds
    else:
        if not args.binary.exists():
            sys.exit(f"missing {args.binary}: build it (see --help)")
        sha = hashlib.sha256(args.binary.read_bytes()).hexdigest()[:16]
        print(f"binary {args.binary} sha {sha}", flush=True)
        if not args.no_baseline:
            runs.append(run_once(args.binary, [], args.seconds, out, "baseline", False))
            print(runs[-1], flush=True)
        pcs = [s["pc"] for s in sel]
        k = args.batch or len(pcs)
        parts = []
        for i in range(0, len(pcs), k):
            tag = "armed" if k >= len(pcs) else f"batch{i // k:02d}"
            runs.append(
                run_once(
                    args.binary, pcs[i : i + k], args.seconds, out, tag, args.fast_log
                )
            )
            print(runs[-1], flush=True)
            parts.append(parse_log((out / f"{tag}.log").read_text()))
        armed = [r for r in runs if r["tag"] != "baseline" and "audio_seconds" in r]
        meta_secs = armed[0]["audio_seconds"] if armed else args.seconds
    (out / "runs.json").write_text(json.dumps(runs, indent=1))
    meta = {
        "roots": len(sel),
        "audio_seconds": meta_secs,
        "binary": str(args.binary),
        "batches": len(parts),
        "idle_range": [fmt_pc(IDLE[0]), fmt_pc(IDLE[1])],
    }
    rep = build_report(merge(parts), sel, runs, meta, Db(args.db))
    (out / "coverage.json").write_text(json.dumps(rep, indent=1, default=list))
    (out / "coverage.md").write_text(markdown(rep))
    print(f"wrote {out}/coverage.json and coverage.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
