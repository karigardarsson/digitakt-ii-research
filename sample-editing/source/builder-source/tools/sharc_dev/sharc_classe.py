#!/usr/bin/env python3
"""Why are the hottest interpreted PCs interpreted?

Reads a fastcov diagnostic run (`--diag only`: the `hist:` lines and the
`cov refused/deny/stop` lines in X.diag.err) and the program database, and for
the top interpreted PCs says: the form, whether some region built from any root
contained the PC, and the refusal, denial or stop recorded at it.

    uv run python -m tools.sharc_dev.sharc_classe out/native/fastcov/STAMP/CONFIG.diag.err [--top 40] [--image dn2-1.11]
"""

import argparse
import collections
import re
import sqlite3

ap = argparse.ArgumentParser()
ap.add_argument("err")
ap.add_argument("--top", type=int, default=40)
ap.add_argument("--image", default="dn2-1.11")
a = ap.parse_args()

hist: dict[int, int] = {}
roots: dict[int, str] = {}
runs: dict[int, tuple] = {}
stops: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
for line in open(a.err, errors="replace"):  # noqa: SIM115
    if line.startswith("hist: 0x"):
        _, pc, n = line.split()
        hist[int(pc, 16)] = int(n)
    elif line.startswith("cov run root="):
        m = re.match(
            r"cov run root=(0x[0-9a-f]+) built=(\d+) refused=(\d+) calls=(\d+) insns=(\d+) declined=\[(.*?)\] exits=(.*?) declwhy=(.*)",
            line,
        )
        if m:
            runs[int(m[1], 16)] = (int(m[4]), int(m[5]), m[6], m[8][:60])
    elif line.startswith("cov refused root="):
        m = re.match(r"cov refused root=(0x[0-9a-f]+) why=(.*)", line)
        if m:
            roots[int(m[1], 16)] = m[2]
    elif line.startswith(("cov deny ", "cov stop ")):
        m = re.match(
            r"cov (deny|stop) root=0x[0-9a-f]+ at=(0x[0-9a-f]+) form=(\S+) why=(.*)",
            line,
        )
        if m:
            stops[int(m[2], 16)][f"{m[1]} {m[4][:70]}"] += 1

db = sqlite3.connect(f"file:out/sharcdb/{a.image}.sqlite?mode=ro", uri=True)
total = sum(hist.values())
print("interpreted insns in the table:", total)
cat: collections.Counter = collections.Counter()
rows = []
for pc, n in sorted(hist.items(), key=lambda x: -x[1]):
    r = db.execute(
        "select form, mnemonic from insn where image=? and sw=?", (a.image, pc)
    ).fetchone()
    form = r[0] if r else "?"
    why = roots.get(pc) or (
        stops[pc].most_common(1)[0][0] if stops.get(pc) else "no record"
    )
    key = (
        "refused root: " + re.sub(r"0x[0-9a-f]+|\d+", "#", why)[:60]
        if pc in roots
        else (re.sub(r"0x[0-9a-f]+|\d+", "#", why)[:70])
    )
    cat[(form, key)] += n
    if len(rows) < a.top:
        rows.append((pc, n, form, why, (r[1] or "")[:40] if r else ""))
for pc, n, form, why, mn in rows:
    ru = runs.get(pc)
    extra = (
        f"  run: calls={ru[0]} insns={ru[1]} declined=[{ru[2]}] {ru[3]}" if ru else ""
    )
    print(f"{n:9d} {pc:#x} {form:10s} {mn:30s} {why[:60]}{extra}")
print("--- by (form, reason)")
for (form, key), n in cat.most_common(30):
    print(f"{100 * n / total:5.1f}% {form:10s} {key}")

print("--- by decline class of the entry at the interpreted PC")
names = ["shape", "req", "window", "budget", "exit0"]
agg: collections.Counter = collections.Counter()
for pc, n in hist.items():
    if pc in roots:
        agg[
            "entry refused (" + re.sub(r"0x[0-9a-f]+|\d+", "#", roots[pc])[26:70] + ")"
        ] += n
        continue
    ru = runs.get(pc)
    if ru is None or ru[0] == 0:
        agg["no call made at this PC (no region run or built)"] += n
        continue
    d = [int(x) for x in ru[2].split(",")]
    if sum(d) < 0.5 * n:
        why = stops[pc].most_common(1)[0][0] if stops.get(pc) else "a region exit"
        agg[
            "not covered: after a stop/exit ("
            + re.sub(r"0x[0-9a-f]+|\d+", "#", why)[:60]
            + ")"
        ] += n
    else:
        agg["declined: " + names[d.index(max(d))]] += n
for k, n in agg.most_common(25):
    print(f"{100 * n / total:5.1f}% {k}")
