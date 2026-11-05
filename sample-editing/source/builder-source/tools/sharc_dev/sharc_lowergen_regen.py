#!/usr/bin/env python3
"""Regenerate everything derived from tools/sharc_dev/sharc_core for the generated
lowering, in one step (`mise run regen`):

1. the shared transpiled core (`mise run core`: out/native/shared-core/gen),
2. packages/emulator/sharc/src/fast/lower/gen_forms.rs for the forms in its GENERATED
   list (add a form by listing it in FORMS_EXTRA, or on the command line),
3. the fuzz vectors and the test programs.

    uv run python -m tools.sharc_dev.sharc_lowergen_regen [--add FORM,...] [--no-core]
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
GEN = ROOT / "packages/emulator/sharc/src/fast/lower/gen_forms.rs"

from tools import python_env  # noqa: E402
from tools.sharc_dev import sharc_lowergen_rs as r  # noqa: E402

# Forms with a generated helper body (flag-neutral transfers; tools/sharc_dev/sharc_lowergen_helpers.py).
HELPER_FORMS = ["3a", "3b", "3c", "3d", "4a", "4b", "4d", "14a", "15a", "15b"]


def generated_list() -> list[str]:
    text = GEN.read_text()
    m = re.search(r"pub const GENERATED: &\[&str\] = &\[(.*?)\];", text)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


def run(*cmd: str) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, env=python_env(), check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", default="", help="forms to add to the list")
    ap.add_argument("--no-core", action="store_true")
    a = ap.parse_args()
    forms = generated_list()
    for f in filter(None, a.add.split(",")):
        if f not in forms:
            forms.append(f)
    forms = sorted(
        forms,
        key=lambda n: (re.sub(r"\D", "", n) == "", int(re.sub(r"\D.*", "", n) or 0), n),
    )
    if not a.no_core:
        run(
            "uv", "run", "python", "-m", "tools.sharc_dev.sharc_transpile", "--strict",
            "--out", "out/native/shared-core/gen",
            "--work", "out/native/shared-core/work",
        )  # fmt: skip
    GEN.write_text(r.emit_file(forms))
    print("wrote", GEN.relative_to(ROOT), len(forms), "forms")
    run(
        "uv", "run", "python", "-m", "tools.sharc_dev.sharc_lowergen_vectors",
        "--out", "packages/emulator/sharc/src/tests_gen_vectors.rs", "--n", "24", *forms,
    )  # fmt: skip
    run(
        "uv", "run", "python", "-m", "tools.sharc_dev.sharc_lowergen_helpers",
        "packages/emulator/sharc/src/fast/gen_helpers.rs", ",".join(HELPER_FORMS),
    )  # fmt: skip
    run(
        "uv", "run", "python", "-m", "tools.sharc_dev.sharc_lowergen_programs",
        "--out", "packages/emulator/sharc/src/tests_gen_programs.rs",
    )  # fmt: skip


if __name__ == "__main__":
    main()
