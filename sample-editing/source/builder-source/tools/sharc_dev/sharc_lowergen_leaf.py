"""Print the leaves of a form that satisfy field pins: lg_leaf.py FORM label=value ..."""

import sys

from tools.sharc_dev import sharc_lowergen as g
from tools.sharc_dev import sharc_lowergen_rs as r

name = sys.argv[1]
pins = dict(a.split("=") for a in sys.argv[2:])
ls = g.trace_form(name, r.type_names()[name])
ids = r.Ids()
for lf in ls:
    ok = True
    for e in lf.path:
        if e[0] == "d":
            p = e[1]
            for k, v in pins.items():
                if (
                    p.a == ("f", k)
                    and p.op == "!="
                    and p.b == ("c", 0)
                    and (v != "0") != e[2]
                ):
                    ok = False
                if (
                    p.a == ("f", k)
                    and p.op == "=="
                    and p.b[0] == "c"
                    and (int(v) == p.b[1]) != e[2]
                ):
                    ok = False
        else:
            for label, v in e[1]:
                if label in pins and int(pins[label]) != v:
                    ok = False
    if ok:
        print(
            [e[1:] if e[0] == "e" else (repr(e[1]), e[2]) for e in lf.path], lf.refused
        )
        for it in r.leaf_items(lf, ids):
            print("   ", it)
