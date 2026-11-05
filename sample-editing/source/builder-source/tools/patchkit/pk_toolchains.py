#!/usr/bin/env python3
"""Fetch and build the patchkit toolchains into out/tools (gitignored, never vendored).

    uv run python -m tools.patchkit.pk_toolchains m68k      # binutils + gcc for m68k-elf, from pinned GNU sources
    uv run python -m tools.patchkit.pk_toolchains selache   # public SHARC+ assembler/compiler at the pinned commit
    uv run python -m tools.patchkit.pk_toolchains status    # what is installed (JSON)

The ColdFire toolchain installs to out/tools/m68k-elf/ (bin/m68k-elf-gcc, ...). Selache builds in
out/tools/selache/ (target/release/selas, seldump, selcc, ...). Both are plain user-space builds; nothing
is installed globally. Paths are resolved by `m68k_bin()` / `selache_dir()` for the tools and tests, which
skip when a toolchain is absent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
# The main tree holds out/ (lanes link to it); resolve through the symlink.
TOOLS = (ROOT / "out" / "tools").resolve()
DL = TOOLS / "dl"

BINUTILS = ("2.47", "https://ftp.gnu.org/gnu/binutils/binutils-2.47.tar.xz")
GCC = ("16.2.0", "https://ftp.gnu.org/gnu/gcc/gcc-16.2.0/gcc-16.2.0.tar.xz")
SHA256 = {
    "binutils-2.47.tar.xz": "154ab23b60070e8f27013c22977f1129425d67d1e8acd6e13010e617811e4cff",
    "gcc-16.2.0.tar.xz": "e6738e29597f733270731aa90600f37ffdc045079dfc27ec7e8192cc81085c3e",
}
M68K_PREFIX = TOOLS / "m68k-elf"
SELACHE_URL = "https://github.com/js216/selache"
SELACHE_REV = "2b26d3b75c53063575bc5c820fa0d38879335187"
SELACHE_DIR = TOOLS / "selache"
# Patches applied (sorted) on top of SELACHE_REV before the build; see tools/patchkit/selache_patches/.
SELACHE_PATCHES = sorted(
    (pathlib.Path(__file__).resolve().parent / "selache_patches").glob("*.patch")
)


def m68k_bin(name: str = "gcc") -> pathlib.Path | None:
    p = M68K_PREFIX / "bin" / f"m68k-elf-{name}"
    return p if p.exists() else None


def selache_dir() -> pathlib.Path | None:
    return (
        SELACHE_DIR if (SELACHE_DIR / "target" / "release" / "selas").exists() else None
    )


def run(
    cmd: list[str], cwd: pathlib.Path | None = None, env: dict[str, str] | None = None
) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True, env=env)


def fetch(url: str) -> pathlib.Path:
    DL.mkdir(parents=True, exist_ok=True)
    dest = DL / url.rsplit("/", 1)[1]
    if (
        not dest.exists()
        or SHA256.get(dest.name) != hashlib.sha256(dest.read_bytes()).hexdigest()
    ):
        run(["curl", "-fL", "-C", "-", "--retry", "5", "-o", str(dest), url])
    want = SHA256.get(dest.name)
    got = hashlib.sha256(dest.read_bytes()).hexdigest()
    if want and got != want:
        sys.exit(f"{dest.name}: sha256 {got} != pinned {want}")
    if not want:
        print(f"UNPINNED {dest.name} sha256 {got}")
    return dest


def unpack(archive: pathlib.Path) -> pathlib.Path:
    out = TOOLS / "src" / archive.name.removesuffix(".tar.xz")
    if not out.exists():
        (TOOLS / "src").mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as t:
            t.extractall(TOOLS / "src", filter="data")
    return out


def brew_prefix(formula: str) -> str:
    return subprocess.run(
        ["brew", "--prefix", formula], capture_output=True, text=True, check=True
    ).stdout.strip()


def build_m68k() -> None:
    jobs = str(os.cpu_count() or 4)
    env = dict(os.environ)
    deps = {f: brew_prefix(f) for f in ("gmp", "mpfr", "libmpc", "isl", "zstd")}
    # binutils
    b_src = unpack(fetch(BINUTILS[1]))
    b_obj = TOOLS / "build" / "binutils"
    b_obj.mkdir(parents=True, exist_ok=True)
    if not (M68K_PREFIX / "bin" / "m68k-elf-as").exists():
        run(
            [
                str(b_src / "configure"),
                f"--prefix={M68K_PREFIX}",
                "--target=m68k-elf",
                "--disable-nls",
                "--disable-werror",
                "--disable-gdb",
                "--disable-sim",
                "--disable-gprof",
                f"--with-zstd={deps['zstd']}",
            ],
            cwd=b_obj,
            env=env,
        )
        run(["make", "-j", jobs], cwd=b_obj, env=env)
        run(["make", "install"], cwd=b_obj, env=env)
    # gcc (C only, no libc/libgcc multilib: freestanding modules)
    g_src = unpack(fetch(GCC[1]))
    g_obj = TOOLS / "build" / "gcc"
    g_obj.mkdir(parents=True, exist_ok=True)
    env["PATH"] = f"{M68K_PREFIX / 'bin'}:{env['PATH']}"
    if not (M68K_PREFIX / "bin" / "m68k-elf-gcc").exists():
        run(
            [
                str(g_src / "configure"),
                f"--prefix={M68K_PREFIX}",
                "--target=m68k-elf",
                "--enable-languages=c",
                "--disable-nls",
                "--disable-multilib",
                "--disable-shared",
                "--disable-threads",
                "--disable-libssp",
                "--disable-libgomp",
                "--disable-libquadmath",
                "--disable-libatomic",
                "--without-headers",
                "--with-newlib",
                "--disable-bootstrap",
                f"--with-gmp={deps['gmp']}",
                f"--with-mpfr={deps['mpfr']}",
                f"--with-mpc={deps['libmpc']}",
                f"--with-isl={deps['isl']}",
                f"--with-zstd={deps['zstd']}",
            ],
            cwd=g_obj,
            env=env,
        )
        run(["make", "-j", jobs, "all-gcc", "all-target-libgcc"], cwd=g_obj, env=env)
        run(["make", "install-gcc", "install-target-libgcc"], cwd=g_obj, env=env)


def build_selache() -> None:
    if not (SELACHE_DIR / ".git").exists():
        run(["git", "clone", SELACHE_URL, str(SELACHE_DIR)])
    run(["git", "checkout", "-q", SELACHE_REV], cwd=SELACHE_DIR)
    run(["git", "checkout", "--", "."], cwd=SELACHE_DIR)
    for patch in SELACHE_PATCHES:
        run(["git", "apply", str(patch)], cwd=SELACHE_DIR)
    run(["cargo", "build", "--release"], cwd=SELACHE_DIR)


def status() -> dict[str, object]:
    out: dict[str, object] = {"m68k_elf_gcc": None, "selache": None}
    gcc = m68k_bin("gcc")
    if gcc:
        out["m68k_elf_gcc"] = subprocess.run(
            [str(gcc), "--version"], capture_output=True, text=True
        ).stdout.splitlines()[0]
        ld = m68k_bin("ld")
        if ld:
            out["m68k_elf_ld"] = subprocess.run(
                [str(ld), "--version"], capture_output=True, text=True
            ).stdout.splitlines()[0]
    sel = selache_dir()
    if sel:
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=sel, capture_output=True, text=True
        ).stdout.strip()
        out["selache"] = rev
        out["selache_patches"] = [
            hashlib.sha256(p.read_bytes()).hexdigest() for p in SELACHE_PATCHES
        ]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("what", choices=["m68k", "selache", "status"])
    args = ap.parse_args()
    if args.what == "m68k":
        build_m68k()
    elif args.what == "selache":
        build_selache()
    print(json.dumps(status(), indent=1))


if __name__ == "__main__":
    main()
