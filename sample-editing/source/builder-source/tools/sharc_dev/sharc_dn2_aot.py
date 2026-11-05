"""Build the DN2 native SHARC+ block library from scratch, reproducibly.

One command runs the whole profile-guided pipeline and writes a manifest:

1. ``tools/sharc_dev/sharc_transpile/ --strict`` into ``OUT/gen-c0`` (the core only,
   no image) and a cargo build of it.
2. ``--cycles`` profiling cycles (default 3). Cycle 0 replays the capture
   interpreter-only with the gen-c0 library; cycle i > 0 runs
   ``tools/sharc_dev/sharc_rsgen.py`` on the profiles of cycles 0..i-1, builds that
   library and replays with blocks on. Each replay writes the native
   profile (``tools/sharc_dev/sharc_dn2_replay.py --profile``).
3. The final ``rsgen`` on the profiles of every cycle, into ``OUT/gen``, and
   its build (``OUT/lib/``); optionally the wasm32 ``--features sharc`` core.
4. The replay gate (``tools/sharc_dev/sharc_dn2_replay.py``): blocks on/off times idle
   skip on/off plus a repeat; every state hash and the PCM hash must agree.
   Optionally the coupled ``packages/emulator/boot`` ``sharc_live`` run (threaded and
   ColdFire-only), which prints its digests.

The profiles given to rsgen are the cycles' profiles merged: coverage and
transition counts summed per key, entries summed per PC, each written sorted
(``OUT/prof/merged-cN.*``). ``OUT/manifest.json`` records the sha256 of every
input, the arguments, the source revision and core hash, every profile file,
every rsgen command, the gen tree hash (``*.rs`` and ``*.bin``; the JSON
reports hold timings) and the library hash (the build remaps the gen path and,
on macOS, fixes the install name, so the bytes do not depend on OUT).
``--compare OLD/manifest.json`` checks that a run reproduced an earlier one
(``--expect`` checks result hashes); either failing makes the exit status 1.

Everything under OUT is firmware-derived (profiles, generated code, libraries):
OUT must lie outside the repository.

    uv run python -m tools.sharc_dev.sharc_dn2_aot --out out/dn2-aot \\
        --state STATE.bin --capture NOTE.dt2cap \\
        [--syx firmware/Digitone_II_OS1.11.syx --cf-snapshot M5.snap \\
         --dsp-image DN2-IMAGE.bin] [--wasm] [--compare OLD/manifest.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tools import python_env

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TOOL = "sharc_dn2_aot"
MANIFEST_VERSION = 1
MARKER = ".sharc_dn2_aot"
# The replay gate: (name, extra sharc_dn2_replay.py arguments).
GATE_RUNS = (
    ("blocks+skip", ()),
    ("blocks+skip#2", ()),
    ("blocks", ("--no-skip",)),
    ("interp+skip", ("--no-blocks",)),
    ("interp", ("--no-blocks", "--no-skip")),
)
GATE_CHECK = "4700,5000,5399,5400,5500,5599,5600"
# Profile kinds as sharc_dn2_replay.py writes them: suffix -> key columns.
PROFILE_KINDS = {"": 3, ".entries": 1, ".trans": 2, ".exits": 3}
# Manifest fields that two runs from the same inputs must reproduce.
REPRO_KEYS = (
    "inputs",
    "settings",
    "source",
    "cycles",
    "final",
    "final_lib",
    "wasm",
    "gate_hashes",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", required=True, help="output directory (outside the repo)")
    p.add_argument("--state", required=True, help="canonical DSP state to replay from")
    p.add_argument("--capture", required=True, help=".dt2cap with DSPI2 frames")
    p.add_argument("--image", default="dn2-1.11", help="program database image")
    p.add_argument("--cycles", type=int, default=3, help="profiling cycles (>= 1)")
    p.add_argument("--profile-start", type=int, default=4600)
    p.add_argument("--profile-end", type=int, default=5600)
    p.add_argument("--gate-start", type=int, default=4600)
    p.add_argument("--gate-end", type=int, default=5600)
    p.add_argument("--gate-check", default=GATE_CHECK, help="frames to hash state at")
    p.add_argument(
        "--gate",
        choices=("full", "blocks", "none"),
        default="full",
        help="full: the five-way gate; blocks: blocks+skip twice; none",
    )
    p.add_argument("--meas", default="5400,5600", help="timed frame range [a,b)")
    p.add_argument(
        "--chain", action="store_true", help="pass --chain to rsgen (generator >= 10)"
    )
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="rsgen --exclude LO:HI (repeatable). A DN2 library used with "
        "SharcPeer needs 0xb88a49:0xb88abc (the idle loop) together with --chain",
    )
    p.add_argument("--region-insns", type=int, default=120)
    p.add_argument("--region-regs", type=int, default=36)
    p.add_argument(
        "--unknown-fallbacks",
        default="",
        help="comma-separated hex block starts to compile with unknown-value fallbacks",
    )
    p.add_argument("--wasm", action="store_true", help="also build the wasm32 core")
    p.add_argument("--syx", help="DN2 .syx (coupled sharc_live run)")
    p.add_argument(
        "--cf-snapshot", help="ColdFire ready snapshot (and .dsp) to run from"
    )
    p.add_argument("--dsp-image", help="packed DN2 loader image for sharc_live")
    p.add_argument(
        "--coupled-extra",
        type=int,
        default=100_000_000,
        help="ColdFire instr after ready",
    )
    p.add_argument("--note-events", default="trig")
    p.add_argument(
        "--toolchain",
        help="Rust toolchain directory (default: ~/.rustup/toolchains/<mise.toml "
        "rust version>-<host>)",
    )
    p.add_argument(
        "--target-dir",
        help="cargo target directory for the native library (default OUT/target)",
    )
    p.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="KEY=HEXPREFIX",
        help="require a result hash to start with HEXPREFIX; KEY is pcm, "
        "state:FRAME, coupled_cf, coupled_dsp, coupled_pcm or cfonly_cf",
    )
    p.add_argument("--compare", help="an earlier manifest.json this run must reproduce")
    a = p.parse_args(argv)
    if a.cycles < 1:
        p.error("--cycles must be at least 1")
    coupled = [a.syx, a.cf_snapshot, a.dsp_image]
    if any(coupled) and not all(coupled):
        p.error("the coupled run needs --syx, --cf-snapshot and --dsp-image together")
    for item in a.expect:
        try:
            parse_expect(item)
        except ValueError as exc:
            p.error(str(exc))
    if inside_work_tree(Path(a.out)):
        p.error(
            "--out must lie outside the repository (its contents are firmware-derived)"
        )
    return a


def inside_work_tree(path: Path) -> bool:
    """Whether PATH, as given or with links resolved, lies in this repository
    or in any other version-controlled tree (a parent holds ``.git``)."""
    for p in (path.absolute(), path.resolve()):
        if p == ROOT or ROOT in p.parents:
            return True
        if any((d / ".git").exists() for d in (p, *p.parents)):
            return True
    return False


def parse_expect(item: str) -> tuple[str, str]:
    key, sep, prefix = item.partition("=")
    if not sep or not re.fullmatch(r"[0-9a-f]{4,64}", prefix):
        raise ValueError("--expect wants KEY=HEXPREFIX, got %r" % item)
    simple = ("pcm", "coupled_cf", "coupled_dsp", "coupled_pcm", "cfonly_cf")
    if key not in simple and not re.fullmatch(r"state:\d+", key):
        raise ValueError("unknown --expect key %r" % key)
    return key, prefix


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(directory: Path) -> tuple[str, dict[str, str]]:
    """The gen tree's hash: every ``*.rs`` and ``*.bin`` file (what the build
    reads), by name, in name order. The JSON reports are left out (timings)."""
    files = {
        p.name: sha256_file(p)
        for p in sorted(directory.iterdir())
        if p.is_file() and p.suffix in (".rs", ".bin")
    }
    h = hashlib.sha256()
    for name, digest in files.items():
        h.update(("%s %s\n" % (name, digest)).encode())
    return h.hexdigest(), files


def format_row(suffix: str, key: tuple[int, ...], count: int) -> str:
    """One merged profile line in the native core's own format."""
    if suffix == "":
        cols = ["%#x" % key[0], "%#x" % key[1], str(key[2])]
    elif suffix == ".entries":
        cols = ["%#x" % key[0]]
    elif suffix == ".trans":
        cols = ["%#x" % key[0], "%#x" % key[1]]
    else:
        cols = ["%#x" % key[0], str(key[1]), "%#x" % key[2]]
    return " ".join(cols + [str(count)]) + "\n"


def merge_kind(texts: list[str], suffix: str) -> str:
    keys = PROFILE_KINDS[suffix]
    total: dict[tuple[int, ...], int] = {}
    for text in texts:
        for line in text.splitlines():
            parts = line.split()
            if len(parts) != keys + 1:
                continue
            key = tuple(int(x, 0) for x in parts[:keys])
            total[key] = total.get(key, 0) + int(parts[keys])
    return "".join(format_row(suffix, k, c) for k, c in sorted(total.items()))


def rsgen_command(
    image: str, gen: Path, work: Path, merged: Path, a: argparse.Namespace
) -> list[str]:
    """The rsgen arguments for one generation (MERGED is the profile prefix)."""
    cmd = [
        "-m",
        "tools.sharc_dev.sharc_rsgen",
        image,
        "--coverage",
        str(merged),
        "--entries",
        str(merged) + ".entries",
        "--transitions",
        str(merged) + ".trans",
        "--model-safe",
        "--explicit-memory-model",
        "0",
        "--region-insns",
        str(a.region_insns),
        "--region-regs",
        str(a.region_regs),
    ]
    if a.chain:
        cmd.append("--chain")
    if a.unknown_fallbacks:
        cmd += ["--unknown-fallbacks", a.unknown_fallbacks]
    for rng in a.exclude:
        cmd += ["--exclude", rng]
    return cmd + ["--work", str(work), "--out", str(gen)]


def settings(a: argparse.Namespace) -> dict[str, Any]:
    """The arguments that decide the result (not paths or reporting)."""
    result = {
        "image": a.image,
        "cycles": a.cycles,
        "profile_frames": [a.profile_start, a.profile_end],
        "gate_frames": [a.gate_start, a.gate_end],
        "gate_check": a.gate_check,
        "gate": a.gate,
        "region_insns": a.region_insns,
        "region_regs": a.region_regs,
        "model_safe": True,
        "explicit_memory_model": 0,
        "chain": a.chain,
        "exclude": list(a.exclude),
        "coupled_extra": a.coupled_extra if a.cf_snapshot else None,
        "note_events": a.note_events if a.cf_snapshot else None,
    }
    if a.unknown_fallbacks:
        result["unknown_fallbacks"] = sorted(
            {int(x, 16) for x in a.unknown_fallbacks.split(",") if x}
        )
    return result


def compare_manifests(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """The reproducibility fields that differ, as 'key.path: old != new'."""
    diffs: list[str] = []

    def walk(path: str, x: Any, y: Any) -> None:
        if isinstance(x, dict) and isinstance(y, dict):
            for k in sorted(set(x) | set(y)):
                walk("%s.%s" % (path, k), x.get(k), y.get(k))
        elif isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
            for i, (u, v) in enumerate(zip(x, y, strict=True)):
                walk("%s[%d]" % (path, i), u, v)
        elif x != y:
            diffs.append("%s: %r != %r" % (path, x, y))

    for key in REPRO_KEYS:
        walk(key, strip_paths(old.get(key)), strip_paths(new.get(key)))
    return diffs


def strip_paths(x: Any) -> Any:
    """Drop the 'path' and 'out' fields, which differ between two output
    directories (commands already name OUT/... instead of the directory)."""
    if isinstance(x, dict):
        return {k: strip_paths(v) for k, v in x.items() if k not in ("path", "out")}
    if isinstance(x, list):
        return [strip_paths(v) for v in x]
    return x


def check_expect(expect: list[str], results: dict[str, str]) -> list[str]:
    bad = []
    for item in expect:
        key, prefix = parse_expect(item)
        got = results.get(key)
        if got is None or not got.startswith(prefix):
            bad.append("%s: expected %s..., got %s" % (key, prefix, got))
    return bad


def toolchain_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser()
    text = (ROOT / "mise.toml").read_text()
    m = re.search(r'^rust\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("%s: no rust version in mise.toml; pass --toolchain" % TOOL)
    arch = {"arm64": "aarch64"}.get(platform.machine(), platform.machine())
    host = {"Darwin": "apple-darwin", "Linux": "unknown-linux-gnu"}[platform.system()]
    return (
        Path.home() / ".rustup" / "toolchains" / ("%s-%s-%s" % (m.group(1), arch, host))
    )


class Pipeline:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.out = Path(a.out).resolve()
        self.logs = self.out / "logs"
        self.timing: dict[str, float] = {}
        self.tc = toolchain_dir(a.toolchain)
        self.target = (
            Path(a.target_dir).resolve() if a.target_dir else self.out / "target"
        )
        if not (self.tc / "bin" / "cargo").is_file():
            raise SystemExit("%s: no cargo in %s" % (TOOL, self.tc))
        self.env = dict(os.environ)
        self.env.update(
            PATH="%s:%s" % (self.tc / "bin", os.environ.get("PATH", "")),
            RUSTC=str(self.tc / "bin" / "rustc"),
            RUSTDOC=str(self.tc / "bin" / "rustdoc"),
            DYLD_LIBRARY_PATH=str(self.tc / "lib"),
        )
        self.env.pop("SHARC_NATIVE_LIB", None)

    # -- helpers ----------------------------------------------------------
    def rel(self, p: Path | str) -> str:
        """A path under OUT as 'OUT/...', for the manifest."""
        s = str(p)
        return "OUT" + s[len(str(self.out)) :] if s.startswith(str(self.out)) else s

    def run(
        self, stage: str, cmd: list[str], *, cwd: Path = ROOT, env: dict | None = None
    ) -> str:
        log = self.logs / (stage + ".log")
        print("[%s] %s" % (stage, " ".join(self.rel(c) for c in cmd)), flush=True)
        t0 = time.perf_counter()
        with open(log, "w") as fh:
            proc = subprocess.run(
                cmd, cwd=cwd, env=env or self.env, stdout=fh, stderr=subprocess.STDOUT
            )
        self.timing[stage] = round(time.perf_counter() - t0, 2)
        if proc.returncode:
            sys.stdout.write(log.read_text()[-3000:])
            raise SystemExit(
                "%s: stage %s failed (exit %d), log %s"
                % (TOOL, stage, proc.returncode, log)
            )
        return log.read_text()

    def python(self, stage: str, args: list[str]) -> str:
        return self.run(stage, [sys.executable, *args], env=python_env(self.env))

    def prepare(self) -> None:
        if self.out.exists() and any(self.out.iterdir()):
            if not (self.out / MARKER).exists():
                raise SystemExit("%s: %s is not empty and not ours" % (TOOL, self.out))
            for child in self.out.iterdir():
                if child.name in (MARKER,) or child == self.target:
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        for d in (self.out, self.logs, self.out / "prof", self.out / "lib"):
            d.mkdir(parents=True, exist_ok=True)
        (self.out / MARKER).write_text("written by tools/sharc_dev/sharc_dn2_aot.py\n")

    def build_lib(self, stage: str, gen: Path, name: str) -> Path:
        """``cargo build --lib`` of packages/emulator/sharc, made independent of where
        GEN and the target directory are: the gen path is remapped in the
        crate's own file names, and on macOS the install name (else the
        target path) is fixed. Two runs then give the same library bytes."""
        # The generated code is its own crate (packages/emulator/sharc-gen), so the remap
        # goes in RUSTFLAGS: `cargo rustc -- ARGS` reaches only the last crate.
        env = dict(
            self.env,
            SHARC_GEN_DIR=str(gen),
            CARGO_TARGET_DIR=str(self.target),
            RUSTFLAGS="--remap-path-prefix=%s=/sharc-gen" % gen,
        )
        cmd = [str(self.tc / "bin" / "cargo"), "rustc", "--release", "--offline",
               "--locked", "--lib"]  # fmt: skip
        ext = ".dylib" if platform.system() == "Darwin" else ".so"
        if ext == ".dylib":
            cmd += ["--", "-Clink-arg=-Wl,-install_name,@rpath/libsharc_native.dylib"]
        self.run(stage, cmd, cwd=ROOT / "packages" / "emulator" / "sharc", env=env)
        lib = self.out / "lib" / (name + ext)
        shutil.copyfile(self.target / "release" / ("libsharc_native" + ext), lib)
        return lib

    def replay(
        self, stage: str, lib: Path, extra: list[str], start: int, end: int, check: str
    ) -> dict:
        res = self.out / "replay" / (stage + ".json")
        res.parent.mkdir(exist_ok=True)
        args = [
            "-m", "tools.sharc_dev.sharc_dn2_replay",
            "--lib", str(lib),
            "--image", self.a.image,
            "--state", str(Path(self.a.state).resolve()),
            "--capture", str(Path(self.a.capture).resolve()),
            "--out", str(res),
            "--start", str(start),
            "--end", str(end),
            "--check", check,
            "--meas", self.a.meas,
            *extra,
        ]  # fmt: skip
        self.python(stage, args)
        return json.loads(res.read_text())

    def merged(self, cycles: list[Path], name: str) -> tuple[Path, dict[str, str]]:
        prefix = self.out / "prof" / name
        hashes = {}
        for suffix in PROFILE_KINDS:
            text = merge_kind(
                [Path(str(c) + suffix).read_text() for c in cycles], suffix
            )
            Path(str(prefix) + suffix).write_text(text)
            hashes["merged" + (suffix or ".cov")] = sha256_file(
                Path(str(prefix) + suffix)
            )
        return prefix, hashes

    def rsgen(self, stage: str, gen: Path, merged: Path) -> tuple[list[str], dict]:
        cmd = rsgen_command(self.a.image, gen, self.out / "work", merged, self.a)
        self.python(stage, cmd)
        rep = json.loads((gen / "rsgen-report.json").read_text())
        keys = ("blocks", "block_instructions", "instructions_in_table", "modules")
        return [self.rel(c) for c in cmd], {k: rep[k] for k in keys}

    # -- stages -----------------------------------------------------------
    def inputs(self) -> dict[str, Any]:
        a = self.a
        files: dict[str, str | None] = {
            "state": a.state,
            "capture": a.capture,
            "image_blob": str(
                ROOT / "out" / "sections" / a.image / "section_7_BLOB.bin"
            ),
            "syx": a.syx,
            "cf_snapshot": a.cf_snapshot,
            "cf_snapshot_dsp": a.cf_snapshot + ".dsp" if a.cf_snapshot else None,
            "dsp_image": a.dsp_image,
        }
        out: dict[str, Any] = {}
        for key, path in files.items():
            if path is None:
                continue
            p = Path(path).resolve()
            if not p.is_file():
                raise SystemExit("%s: input %s missing: %s" % (TOOL, key, p))
            out[key] = {
                "path": str(p),
                "sha256": sha256_file(p),
                "bytes": p.stat().st_size,
            }
        return out

    def source(self) -> dict[str, Any]:
        from tools.sharc_dev import sharc_transpile as tr

        def git(*args: str) -> str:
            return subprocess.run(
                ["git", *args], cwd=ROOT, capture_output=True, text=True
            ).stdout.strip()

        paths = [
            "tools",
            "packages/emulator/sharc",
            "packages/emulator/sharc-rt",
            "packages/emulator/sharc-gen",
            "packages/emulator/boot",
            "emu",
        ]
        rustc = subprocess.run(
            [self.env["RUSTC"], "--version"],
            capture_output=True,
            text=True,
            env=self.env,
        )
        return {
            "core_hash": tr.core_hash(),
            "generator_version": tr.GENERATOR_VERSION,
            "git_head": git("rev-parse", "HEAD"),
            "git_dirty": git("status", "--porcelain", "--", *paths).splitlines(),
            "rustc": rustc.stdout.strip(),
        }

    def coupled(self, gen: Path) -> dict[str, Any]:
        a = self.a
        snap = Path(a.cf_snapshot).resolve()
        if not snap.is_file() or not Path(str(snap) + ".dsp").is_file():
            # sharc_live would boot and *write* the snapshot: never here.
            raise SystemExit("%s: %s(.dsp) missing" % (TOOL, snap))
        target = self.out / "boot-target"
        env = dict(self.env, SHARC_GEN_DIR=str(gen), CARGO_TARGET_DIR=str(target))
        cmd = [str(self.tc / "bin" / "cargo"), "build", "--release", "--offline", "--locked",
               "--features", "sharc", "--example", "sharc_live"]  # fmt: skip
        self.run(
            "build-sharc_live",
            cmd,
            cwd=ROOT / "packages" / "emulator" / "boot",
            env=env,
        )
        binary = target / "release" / "examples" / "sharc_live"
        result: dict[str, Any] = {}
        for name, extra in (
            ("threaded", {"DSP_THREAD": "1"}),
            ("cfonly", {"DSP_PERIOD": "1"}),
        ):
            wav = self.out / "coupled" / (name + ".wav")
            wav.parent.mkdir(exist_ok=True)
            run_env = dict(
                self.env,
                CF_DIGEST="1",
                NOTE_EVENTS=a.note_events,
                CF_SNAPSHOT=str(snap),
                **extra,
            )
            run_cmd = [str(binary), str(Path(a.syx).resolve()), str(Path(a.state).resolve()),
                       str(Path(a.dsp_image).resolve()), str(wav), str(a.coupled_extra)]  # fmt: skip
            log = self.run("coupled-" + name, run_cmd, cwd=self.out, env=run_env)
            fields = dict(re.findall(r"(\w+)=(\S+)", log))
            result[name] = {
                k: fields.get(k)
                for k in ("cf_state_digest", "dsp_export_sha256", "pcm_since_ready_sha256",
                          "samples", "frames", "wall", "real_time_factor")
            }  # fmt: skip
        return result

    def main(self) -> int:
        a = self.a
        self.prepare()
        t_all = time.perf_counter()
        manifest: dict[str, Any] = {
            "tool": TOOL,
            "manifest_version": MANIFEST_VERSION,
            "argv": sys.argv[1:],
            "settings": settings(a),
            "inputs": self.inputs(),
            "source": self.source(),
            "toolchain": str(self.tc),
        }
        work = self.out / "work"
        gen0 = self.out / "gen-c0"
        self.python(
            "transpile",
            [
                "-m",
                "tools.sharc_dev.sharc_transpile",
                "--strict",
                "--out",
                str(gen0),
                "--work",
                str(work),
            ],
        )
        g0, _ = tree_hash(gen0)
        lib = self.build_lib("build-c0", gen0, "c0")
        cycles: list[dict[str, Any]] = []
        profiles: list[Path] = []
        for i in range(a.cycles):
            entry: dict[str, Any] = {"index": i}
            if i == 0:
                entry.update(gen_sha256=g0, blocks=False)
                extra = ["--no-blocks"]
            else:
                merged, mh = self.merged(profiles, "merged-c%d" % i)
                gen = self.out / ("gen-c%d" % i)
                cmd, info = self.rsgen("rsgen-c%d" % i, gen, merged)
                entry.update(
                    profiles_in=mh,
                    rsgen=cmd,
                    rsgen_result=info,
                    gen_sha256=tree_hash(gen)[0],
                    blocks=True,
                )
                lib = self.build_lib("build-c%d" % i, gen, "c%d" % i)
                extra = []
            prof = self.out / "prof" / ("c%d" % i)
            res = self.replay("profile-c%d" % i, lib, [*extra, "--profile", str(prof)],
                              a.profile_start, a.profile_end, str(a.profile_end - 1))  # fmt: skip
            entry["profile"] = {
                (s or ".cov").lstrip("."): sha256_file(Path(str(prof) + s))
                for s in PROFILE_KINDS
            }
            entry["replay"] = {
                "pcm": res["pcm"],
                "states": res["states"],
                "total": res["total"],
                "halt": res["halt"],
            }
            cycles.append(entry)
            profiles.append(prof)
        manifest["cycles"] = cycles
        merged, mh = self.merged(profiles, "merged-final")
        gen = self.out / "gen"
        cmd, info = self.rsgen("rsgen-final", gen, merged)
        gsha, gfiles = tree_hash(gen)
        lib = self.build_lib("build-final", gen, "final")
        final: dict[str, Any] = {
            "profiles_in": mh, "rsgen": cmd, "rsgen_result": info,
            "gen_sha256": gsha, "gen_files": gfiles,
        }  # fmt: skip
        manifest["final"] = final
        manifest["final_lib"] = {"path": str(lib), "sha256": sha256_file(lib)}
        if a.wasm:
            # With --target, RUSTFLAGS reach the wasm crates only (not build
            # scripts); the remaps keep the gen and checkout paths out.
            env = dict(
                self.env,
                SHARC_GEN_DIR=str(gen),
                CARGO_TARGET_DIR=str(self.out / "wasm-target"),
                RUSTFLAGS="--remap-path-prefix=%s=/sharc-gen --remap-path-prefix=%s=/src"
                % (gen, ROOT),
            )
            cmd2 = [str(self.tc / "bin" / "cargo"), "build", "--manifest-path", str(ROOT / "packages/emulator/boot/Cargo.toml"),
                    "--locked", "--offline", "--release", "--lib", "--target", "wasm32-unknown-unknown",
                    "--features", "sharc"]  # fmt: skip
            self.run("build-wasm", cmd2, env=env)
            wasm = (
                self.out
                / "wasm-target/wasm32-unknown-unknown/release/elektron_native_boot.wasm"
            )
            manifest["wasm"] = {"path": str(wasm), "sha256": sha256_file(wasm)}
        gate, hashes, ok = self.gate(lib)
        manifest["gate"] = gate
        manifest["gate_hashes"] = hashes
        results = {"pcm": hashes.get("pcm", "")}
        results.update({"state:%s" % k: v for k, v in hashes.get("states", {}).items()})
        if a.cf_snapshot:
            co = self.coupled(gen)
            manifest["coupled"] = co
            results.update(
                coupled_cf=co["threaded"]["cf_state_digest"] or "",
                coupled_dsp=co["threaded"]["dsp_export_sha256"] or "",
                coupled_pcm=co["threaded"]["pcm_since_ready_sha256"] or "",
                cfonly_cf=co["cfonly"]["cf_state_digest"] or "",
            )
            manifest["gate_hashes"]["coupled"] = {
                k: co["threaded"][k]
                for k in (
                    "cf_state_digest",
                    "dsp_export_sha256",
                    "pcm_since_ready_sha256",
                    "samples",
                )
            } | {"cfonly_cf_state_digest": co["cfonly"]["cf_state_digest"]}
        self.timing["total"] = round(time.perf_counter() - t_all, 2)
        manifest["timing"] = self.timing
        problems = [] if ok else ["replay gate: runs disagree"]
        problems += check_expect(a.expect, results)
        if a.compare:
            old = json.loads(Path(a.compare).read_text())
            diffs = compare_manifests(old, manifest)
            manifest["compare"] = {
                "with": str(Path(a.compare).resolve()),
                "diffs": diffs,
            }
            problems += ["differs from %s: %s" % (a.compare, d) for d in diffs]
        manifest["problems"] = problems
        (self.out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
        self.report(manifest)
        return 1 if problems else 0

    def gate(self, lib: Path) -> tuple[dict[str, Any], dict[str, Any], bool]:
        a = self.a
        runs = {"full": GATE_RUNS, "blocks": GATE_RUNS[:2], "none": ()}[a.gate]
        gate: dict[str, Any] = {}
        for name, extra in runs:
            stage = "gate-" + name.replace("+", "-").replace("#", "-")
            res = self.replay(
                stage, lib, list(extra), a.gate_start, a.gate_end, a.gate_check
            )
            st = res["stats"]
            busy = st["instructions"] - st["idle_instructions"]
            gate[name] = {
                "pcm": res["pcm"], "states": res["states"], "halt": res["halt"],
                "total": res["total"], "wall": round(res["wall"], 3),
                "meas_wall": round(res["meas_wall"], 3), "meas_instr": res["meas_instr"],
                "blocks": st["blocks"], "block_entries": st["block_entries"],
                "block_instructions": st["block_instructions"],
                "idle_instructions": st["idle_instructions"],
                "block_share_of_busy": round(st["block_instructions"] / busy, 4) if busy else 0.0,
                "model_code_mismatch": st["model_code_mismatch"],
            }  # fmt: skip
        keys = {
            (g["pcm"], json.dumps(g["states"], sort_keys=True), json.dumps(g["halt"]))
            for g in gate.values()
        }
        ok = len(keys) <= 1
        first = next(iter(gate.values()), None)
        hashes: dict[str, Any] = {"identical": ok}
        if first is not None:
            hashes.update(
                pcm=first["pcm"],
                states=first["states"],
                halt=first["halt"],
                total=first["total"],
            )
        return gate, hashes, ok

    def report(self, m: dict[str, Any]) -> None:
        print()
        print("manifest  %s" % (self.out / "manifest.json"))
        print("gen       %s  sha256 %s" % (self.out / "gen", m["final"]["gen_sha256"]))
        print(
            "library   %s  sha256 %s"
            % (m["final_lib"]["path"], m["final_lib"]["sha256"])
        )
        for c in m["cycles"]:
            print(
                "cycle %d   gen %s  profile cov %s"
                % (c["index"], c["gen_sha256"][:16], c["profile"]["cov"][:16])
            )
        print(
            "rsgen     %(blocks)d blocks (%(block_instructions)d instructions), "
            "%(instructions_in_table)d instructions in the table, %(modules)d files"
            % m["final"]["rsgen_result"]
        )
        print(
            "gate      %-14s %-12s %8s %9s %7s %8s  states"
            % ("run", "pcm", "wall", "meas_wall", "blocks", "blk/busy")
        )
        for name, g in m["gate"].items():
            states = " ".join("%s:%s" % (k, v[:8]) for k, v in g["states"].items())
            print("          %-14s %-12s %8.2f %9.3f %7d %8.4f  %s" % (
                name, g["pcm"][:12], g["wall"], g["meas_wall"], g["blocks"], g["block_share_of_busy"], states))  # fmt: skip
        print("gate      identical across runs: %s" % m["gate_hashes"]["identical"])
        for name, c in m.get("coupled", {}).items():
            print("coupled   %-9s cf %s dsp %s pcm %s samples %s wall %s" % (
                name, (c["cf_state_digest"] or "")[:8], (c["dsp_export_sha256"] or "")[:8],
                (c["pcm_since_ready_sha256"] or "")[:12], c["samples"], c["wall"]))  # fmt: skip
        print("timing    %s" % " ".join("%s=%s" % kv for kv in m["timing"].items()))
        for p in m["problems"]:
            print("PROBLEM   %s" % p)
        print("result    %s" % ("ok" if not m["problems"] else "FAILED"))


def main(argv: list[str] | None = None) -> int:
    return Pipeline(parse_args(argv)).main()


if __name__ == "__main__":
    raise SystemExit(main())
