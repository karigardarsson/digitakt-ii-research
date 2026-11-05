#!/usr/bin/env python3
"""Survey the generator over every form of tools/sharc_dev/sharc_core.

For each form: leaves traced, refusing leaves (and why, grouped), Rust lines
the emitter produces, trace + emit time. A form that raises is listed with the
exception: that is the next intrinsic or lifting to write.

    uv run python -m tools.sharc_dev.sharc_lowergen_survey [FORM ...]
"""

from __future__ import annotations

import collections
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc_lowergen as g  # noqa: E402
from tools.sharc_dev import sharc_lowergen_rs as r  # noqa: E402
from tools.sharc_dev.sharc_core import forms  # noqa: E402


def main() -> None:
    tab = r.type_names()
    names = sys.argv[1:] or sorted(forms.FORMS)
    total = 0
    for name in names:
        if name not in tab:
            print(f"{name:14s} no decode-table entry")
            continue
        t0 = time.time()
        try:
            leaves = g.trace_form(name, tab[name])
            lines = r.emit_form(name, tab[name])
        except Exception as e:  # noqa: BLE001
            print(f"{name:14s} FAILS {type(e).__name__}: {str(e)[:90]}")
            continue
        why = collections.Counter(lf.refused for lf in leaves if lf.refused)
        ok = sum(1 for lf in leaves if not lf.refused)
        total += len(lines)
        top = ", ".join(f"{k[:44]} x{v}" for k, v in why.most_common(2))
        print(
            f"{name:14s} leaves {len(leaves):5d} ok {ok:5d} rust {len(lines):5d} lines {time.time() - t0:5.1f}s  {top}"
        )
    print("total rust lines", total)


if __name__ == "__main__":
    main()
