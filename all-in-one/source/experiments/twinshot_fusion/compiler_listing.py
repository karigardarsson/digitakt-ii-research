"""Save this project's generated assembly for a bounded compiler diagnosis."""
import argparse
from pathlib import Path

from tools.patchkit import pk_sharc

ap = argparse.ArgumentParser(__doc__)
ap.add_argument("--out", type=Path, required=True)
a = ap.parse_args()
original = pk_sharc._object


def capture(*args, **kwargs):
    a.out.write_text(kwargs["compiled_asm"])
    return original(*args, **kwargs)


pk_sharc._object = capture
pk_sharc.compile_c('#include "adapter.c"', include_dirs=[Path(__file__).resolve().parent])
