#!/usr/bin/env python3
"""Build coupled-run with line tables (same code generation) into its own
target dir, for address-level profiles (xctrace + atos). Prints the binary.

    uv run python -m tools.sharc_dev.sharc_build_dbg
"""

import os
import pathlib
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
from tools.emulator import (  # noqa: E402
    coupled,  # noqa: E402
    digi_env,  # noqa: E402
    native_target,  # noqa: E402
)

gen = coupled.generated()
digi_env.check_generated(gen)
target = native_target.target_dir(gen, variant="dbg")
env = {
    **digi_env.rust_env(),
    "SHARC_GEN_DIR": str(gen),
    "CARGO_PROFILE_RELEASE_DEBUG": "line-tables-only",
}
cmd = coupled.cargo(
    [
        "build", "--release", "--locked",
        "--manifest-path", "packages/emulator/coupled/Cargo.toml",
        "--features", coupled.FEATURES,
        "--bin", coupled.BINARY_NAME,
        "--target-dir", str(target),
    ]
)  # fmt: skip
subprocess.run(cmd, cwd=ROOT, env=env, check=True)
print(target / "release" / coupled.BINARY_NAME)
os.sync()
