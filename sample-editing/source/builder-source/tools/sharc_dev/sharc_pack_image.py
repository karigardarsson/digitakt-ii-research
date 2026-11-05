"""Write the native engine image blob for a SHARC+ image (the loader image
`sharc_transpile_run.pack_image` builds), for hosts that have no Python.

    uv run python -m tools.sharc_dev.sharc_pack_image dn2-1.11 OUT.bin

The output is derived from firmware: keep it outside the repository.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tools.sharc_dev import sharc_run, sharc_transpile_run


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    blob = sharc_transpile_run.pack_image(sharc_run._load_image_memory(argv[1]))
    Path(argv[2]).write_bytes(blob)
    print(f"{argv[2]}: {len(blob)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
