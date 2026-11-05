"""Run the native SHARC+ core against the Python core.

The native core (packages/emulator/sharc, built from tools/sharc_dev/sharc_transpile/'s
translation of tools/sharc_dev/sharc_core and tools/sharc_dev/sharc_rsgen.py's block code)
implements tools/sharc_dev/sharc_diff.py's C ABI. This module adds what running it
for real needs:

- ``pack_image``: the image blob ``sharc_native_create`` takes (the loader
  image's bytes, and the MMR addresses sharcimm names).
- ``NativeCore``: sharc_diff.NativeEngine plus the extra calls (options,
  host pokes, fresh calls, one fabricated instruction, counters).
- ``to_native``/``to_python``: whole-state transfer, overlay included.
- ``HybridEngine``: the native core with the Python core as the fallback
  for every instruction the native side traps on (a fork, a stop, an
  unmodelled MMR, a PC with no native code). It follows the Engine
  protocol, so sharc_diff.run_lockstep can check it against a
  PythonEngine instruction by instruction.
- ``run_corpus``: the random-operand compute corpus (sharc_diff's, plus
  the tables it leaves out), each case replayed natively.
- ``run_frames``: capture frames through the native core, checked frame by
  frame against the Python replay.

    uv run python -m tools.sharc_dev.sharc_transpile_run corpus [--cases-per-op 8]
    uv run python -m tools.sharc_dev.sharc_transpile_run lockstep dt2-1.16 --steps 2000
    uv run python -m tools.sharc_dev.sharc_transpile_run frames dt2-1.16 CAPTURE --frames 0-3
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import random
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

from tools.sharc_dev import sharc_diff as sd  # noqa: E402
from tools.sharc_dev import sharc_run as sr  # noqa: E402
from tools.sharc_dev import sharc_trace as st  # noqa: E402
from tools.sharc_dev.sharcldr import LoadedMemory  # noqa: E402

# The optimised build (native-opt): regenerate it after any sharc_core change,
# or it runs the old semantics (see docs/plans/native-emulator.md).
# Order: SHARC_NATIVE_LIB, the firmware-free shared-core build (`mise run
# sharc:native-lib`, rebuilt from the current core), else the image-specific
# build (kept for perf/AOT work; it may be stale, and an old one that lacks
# symbols makes the tests skip). With neither, the shared-core path, so the
# skip message points at what `mise run sharc:native-lib` builds.
_LIB_NAME = (
    "sharc_native.dll"  # cargo names a Windows cdylib without the lib prefix
    if sys.platform == "win32"
    else "libsharc_native." + ("dylib" if sys.platform == "darwin" else "so")
)
_IMAGE_LIB = os.path.join(
    ROOT, "out", "native", "opt", "target-final", "release", _LIB_NAME
)
_SHARED_LIB = os.path.join(
    ROOT, "out", "native", "shared-core", "target", "release", _LIB_NAME
)
DEFAULT_LIB = os.environ.get(
    "SHARC_NATIVE_LIB",
    _IMAGE_LIB
    if os.path.exists(_IMAGE_LIB) and not os.path.exists(_SHARED_LIB)
    else _SHARED_LIB,
)
SPECIAL_SLOTS = sd.SPECIAL_SLOTS
NATIVE_TRAP = "native-trap:"

# ---------------------------------------------------------------------------
# Image and instruction blobs
# ---------------------------------------------------------------------------


def pack_image(data: LoadedMemory | None) -> bytes:
    """The ``sharc_native_create`` image: magic "SHIM", version 1; the
    loader image as (address, bytes) segments; the addresses
    ``sharcimm.name_address`` names, exactly and as [lo, hi) ranges; and
    ``encoding.CORE_MMR_RESET_VALUES``' addresses (both make an address a
    fixed-width MMR in ``memory._dm_read``)."""
    import struct

    from tools.sharc_dev import sharcimm

    out = bytearray(b"SHIM")
    out += struct.pack("<I", 1)
    segments: list[tuple[int, bytes]] = []
    if data is not None:
        starts, ends, _owners = data._segments
        for lo, hi in zip(starts, ends, strict=True):
            raw = data.read(lo, hi - lo)
            if raw is not None:
                segments.append((lo, raw))
    out += struct.pack("<I", len(segments))
    for lo, raw in segments:
        out += struct.pack("<II", lo, len(raw)) + raw
    named = sorted(sharcimm.CORE_MMR_REGS)
    out += struct.pack("<I", len(named))
    for a in named:
        out += struct.pack("<I", a)
    ranges = [(0x310C9000, 0x310CA000), (0x310CA000, 0x310CB000)]
    ranges += [(base, base + size) for base, size, _name, _regs in sharcimm.BLOCKS]
    out += struct.pack("<I", len(ranges))
    for lo, hi in ranges:
        out += struct.pack("<II", lo, hi)
    reset = sorted(st.CORE_MMR_RESET_VALUES)
    out += struct.pack("<I", len(reset))
    for a in reset:
        out += struct.pack("<I", a)
    return bytes(out)


def _pstr(s: str) -> bytes:
    import struct

    raw = s.encode("utf-8")
    return struct.pack("<H", len(raw)) + raw


def pack_insn(
    type_name: str, length_bytes: int | None, kind: str, fields: Mapping[str, int]
) -> bytes:
    """``sharc_native_exec_insn``'s instruction blob (native canon.rs
    parse_insn)."""
    import struct

    out = bytearray(b"SHIN")
    out += _pstr(type_name)
    out += struct.pack("<i", -1 if length_bytes is None else length_bytes)
    out += _pstr(kind)
    out += struct.pack("<H", len(fields))
    for key, value in fields.items():
        out += _pstr(key) + struct.pack("<q", value)
    return bytes(out)


# ---------------------------------------------------------------------------
# The library
# ---------------------------------------------------------------------------

_EXTRA_FUNCTIONS = (
    (
        "sharc_native_set_option",
        [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int64],
        ctypes.c_int32,
    ),
    (
        "sharc_native_set_provisional",
        [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_char_p,
            ctypes.c_size_t,
        ],
        ctypes.c_int32,
    ),
    (
        "sharc_native_exec_insn",
        [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t],
        ctypes.c_int32,
    ),
    (
        "sharc_native_stats",
        [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t],
        ctypes.c_int32,
    ),
    ("sharc_native_info", [ctypes.c_char_p, ctypes.c_size_t], ctypes.c_int32),
    (
        "sharc_native_profile",
        [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t],
        ctypes.c_int32,
    ),
    (
        "sharc_native_set_reg",
        [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ],
        ctypes.c_int32,
    ),
    ("sharc_native_get_reg", [ctypes.c_void_p, ctypes.c_uint32], ctypes.c_uint64),
    (
        "sharc_native_poke",
        [
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_uint32,
        ],
        ctypes.c_int32,
    ),
    (
        "sharc_native_peek",
        [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint32],
        ctypes.c_int64,
    ),
    (
        "sharc_native_fresh_call",
        [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int64],
        ctypes.c_int32,
    ),
)

# Peripheral host events (packages/emulator/sharc/src/lib.rs); bound when present so an
# older library still reaches the build-info check.
_PERIPHERAL_FUNCTIONS = (
    ("sharc_native_sec_raise", [ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int32),
    ("sharc_native_dma_start", [ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int64),
    (
        "sharc_native_dma_done",
        [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32],
        ctypes.c_int32,
    ),
    (
        "sharc_native_spi2_exchange",
        [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p],
        ctypes.c_int64,
    ),
    (
        "sharc_native_sport_block",
        [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_char_p,
            ctypes.c_size_t,
        ],
        ctypes.c_int64,
    ),
)

# sharc_native_set_option keys (native lib.rs / canon.rs).
OPT_BLOCKS = 1
OPT_EXPORT_RANGES = 2
OPT_SPECIAL_PRESENT = 3
OPT_RUNTIME_DECODE = 4
OPT_INSTRUCTION_CLOCK = 5
OPT_INSTRUCTION_CLOCK_BASE = 6
OPT_STOP_SOFTWARE_INTERRUPT = 7
OPT_EXPLICIT_MEMORY_MODEL = 10
OPT_APPROX_RECIPS = 11
OPT_ASSUME_NW32 = 12
OPT_FOLLOW_LOADED_CALLS = 13
OPT_MAX_CALL_DEPTH = 14
OPT_CONTINUE_EXTERNAL_CALLS = 15
OPT_DATA_MEMORY_TAINTED = 16
OPT_HAS_CONCRETE = 17
OPT_DOSSIER_BYTES = 18
OPT_BANK_MODEL = 19
OPT_STACK_MODEL = 20
OPT_STOP_PC = 8
OPT_SOFTWARE_INTERRUPTS = 9
OPT_CORE_TIMER = 21
OPT_PERIPHERAL_MODEL = 22
# Idle-loop skip (native only): loop head PC (-1 off) and the inclusive PC
# range every loop instruction must lie in. Exact; see findings 07.
OPT_PROFILE = 26
OPT_IDLE_HEAD = 23
OPT_IDLE_LO = 24
OPT_IDLE_HI = 25
DN2_IDLE_HEAD = 0xB88AAB
DN2_IDLE_RANGE = (0xB88A49, 0xB88ABB)


# How to rebuild DEFAULT_LIB (native-opt's inputs, all under out/native/opt).
REGENERATE_HINT = (
    "regenerate and rebuild it: uv run python -m tools.sharc_dev.sharc_rsgen dt2-1.16 "
    "--coverage out/native/opt/drive3m.cov --entries out/native/opt/drive3e.entries "
    "--transitions out/native/opt/drive3b.trans --out out/native/opt/gen-final && "
    "SHARC_GEN_DIR=$PWD/out/native/opt/gen-final CARGO_TARGET_DIR=out/native/opt/target-final "
    "cargo build --release --manifest-path packages/emulator/sharc/Cargo.toml"
)
# Set to 1 to load a stale library anyway (with a warning), e.g. to time an
# old build.
ALLOW_STALE_ENV = "SHARC_NATIVE_ALLOW_STALE"


class NativeLibraryUnusable(RuntimeError):
    """The native library cannot serve this tree: it is missing symbols this
    tree's tools call (built from older packages/emulator/sharc sources), or it is stale.
    Tests that need the library skip on this (tests/conftest.py)."""


class StaleNativeLibrary(NativeLibraryUnusable):
    """A native library generated from other sharc_core sources or by
    another generator version than this tree's."""


def check_build_info(info: Mapping[str, Any], library_path: str) -> None:
    """Refuse a library (its ``sharc_native_info``) whose generated code does
    not come from the current tools/sharc_dev/sharc_core sources and generator
    version: it would run the old semantics. ALLOW_STALE_ENV=1 turns the
    error into a warning."""
    from tools.sharc_dev import sharc_transpile

    problems = []
    core = info.get("core_sha256")
    if core != sharc_transpile.core_hash():
        problems.append(
            "its tools/sharc_dev/sharc_core hash is %s, the sources' is %s"
            % (core, sharc_transpile.core_hash())
        )
    gen = info.get("generator_version", 0)
    if gen != sharc_transpile.GENERATOR_VERSION:
        problems.append(
            "it was generated by generator version %s, this tree's is %d%s"
            % (
                gen,
                sharc_transpile.GENERATOR_VERSION,
                " (it predates the version stamp)" if not gen else "",
            )
        )
    if not problems:
        return
    msg = "stale native SHARC library %s: %s; %s" % (
        library_path,
        "; ".join(problems),
        REGENERATE_HINT,
    )
    if os.environ.get(ALLOW_STALE_ENV) == "1":
        print("warning: " + msg, file=sys.stderr)
        return
    raise StaleNativeLibrary(msg)


class NativeCore(sd.NativeEngine):
    """sharc_diff.NativeEngine with the native core's extra calls. Refuses
    a stale library (check_build_info)."""

    def __init__(self, image: bytes, library_path: str = DEFAULT_LIB) -> None:
        try:
            self._load(image, library_path)
        except AttributeError as exc:  # ctypes: the symbol is not in the library
            raise NativeLibraryUnusable(
                f"{library_path} lacks a symbol this tree needs ({exc}); "
                f"build a current library: {REGENERATE_HINT}"
            ) from exc

    def _load(self, image: bytes, library_path: str) -> None:
        super().__init__(library_path, image)
        for name, argtypes, restype in _EXTRA_FUNCTIONS:
            fn = getattr(self._lib, name)
            fn.argtypes = argtypes
            fn.restype = restype
        self._get_pc = getattr(self._lib, "sharc_native_get_pc", None)
        if self._get_pc is not None:
            self._get_pc.argtypes = [ctypes.c_void_p]
            self._get_pc.restype = ctypes.c_int64
        for name, argtypes, restype in _PERIPHERAL_FUNCTIONS:
            fn = getattr(self._lib, name, None)
            if fn is not None:
                fn.argtypes = argtypes
                fn.restype = restype
        check_build_info(self.info(), library_path)

    def export_state(self, **_kwargs: Any) -> dict:
        """sharc_diff.NativeEngine.export_state, for a state of any size
        (the whole overlay when OPT_EXPORT_RANGES is on)."""
        cap = 1 << 20
        while True:
            buf = ctypes.create_string_buffer(cap)
            n = self._lib.sharc_native_export_state(self._handle, buf, cap)
            if n >= 0:
                return sd.unpack_state(buf.raw[:n])
            cap = -n

    def set_option(self, key: int, value: int) -> None:
        rc = self._lib.sharc_native_set_option(self._handle, key, int(value))
        if rc != 0:
            raise ValueError("sharc_native_set_option(%d) failed (%d)" % (key, rc))
        if key == OPT_CORE_TIMER:
            self._core_timer_enabled = bool(value)
        if key == OPT_PERIPHERAL_MODEL:
            self._peripheral_enabled = bool(value)

    def set_provisional(self, name: str, mode: str) -> None:
        n, m = name.encode(), mode.encode()
        rc = self._lib.sharc_native_set_provisional(self._handle, n, len(n), m, len(m))
        if rc != 0:
            raise ValueError(
                "sharc_native_set_provisional(%s) failed (%d)" % (name, rc)
            )

    def exec_insn(self, blob: bytes) -> bool:
        rc = self._lib.sharc_native_exec_insn(self._handle, blob, len(blob))
        if rc < 0:
            raise ValueError("malformed instruction blob (%d)" % rc)
        if rc == 0:
            buf = ctypes.create_string_buffer(512)
            n = self._lib.sharc_native_halt_reason(self._handle, buf, 512)
            self._halted_reason = buf.raw[:n].decode("utf-8", "replace")
        return rc == 1

    def stats(self) -> dict[str, int]:
        arr = (ctypes.c_uint64 * 16)()
        n = self._lib.sharc_native_stats(self._handle, arr, 16)
        names = (
            "instructions",
            "block_entries",
            "block_instructions",
            "single_steps",
            "traps",
            "blocks",
            "special_present",
            "idle_instructions",
            "block_traps",
            "model_gated",
            "model_unsafe",
            "model_irq_deferred",
            "model_timer_gated",
            "model_mmr_exits",
            "model_code_mismatch",
        )
        return {names[i]: int(arr[i]) for i in range(min(n, len(names)))}

    def profile(self, kind: int) -> str:
        """Interpreter profile text (OPT_PROFILE): 0 coverage, 1 entries,
        2 block transitions, 3 block exits (see native sharc_native_profile)."""
        cap = 1 << 20
        while True:
            buf = ctypes.create_string_buffer(cap)
            n = self._lib.sharc_native_profile(self._handle, kind, buf, cap)
            if n >= 0:
                return buf.raw[:n].decode()
            if n == -1:
                raise ValueError("no profile of kind %d" % kind)
            cap = -n

    def info(self) -> dict:
        buf = ctypes.create_string_buffer(4096)
        n = self._lib.sharc_native_info(buf, 4096)
        return json.loads(buf.raw[:n].decode())

    def set_reg(self, code: int, value: st.Value) -> None:
        if isinstance(value, st.Const):
            args = (1, value.value, 0xFFFFFFFF)
        elif isinstance(value, st.PartialConst):
            args = (2, value.bits, value.mask)
        else:
            args = (0, 0, 0)
        self._lib.sharc_native_set_reg(self._handle, code, *args)

    def get_reg(self, code: int) -> st.Value:
        raw = self._lib.sharc_native_get_reg(self._handle, code)
        bits, mask = raw & 0xFFFFFFFF, raw >> 32
        if mask == 0xFFFFFFFF:
            return st.Const(bits)
        if mask == 0:
            return st.Unknown("native")
        return st.PartialConst(mask, bits)

    def pc(self) -> int:
        """The native engine's next architectural ``pc_sw``.

        Libraries built before ``sharc_native_get_pc`` remain loadable, but
        this diagnostic accessor deliberately does not fall back to a full
        canonical-state export per instruction.
        """
        if self._get_pc is None:
            raise RuntimeError(
                "native library lacks sharc_native_get_pc; no full-state fallback"
            )
        pc = self._get_pc(self._handle)
        if pc < 0:
            raise RuntimeError("sharc_native_get_pc rejected the native handle")
        return int(pc)

    def poke(self, address: int, data: bytes, width: int = 1) -> int:
        return self._lib.sharc_native_poke(
            self._handle, address, data, len(data), width
        )

    def peek(self, address: int, width: int = 4) -> int | None:
        raw = self._lib.sharc_native_peek(self._handle, address, width)
        if raw < 0:
            raise sr.UnmodeledMMR(address, None)
        return raw & 0xFFFFFFFF if raw >> 32 else None

    def sec_raise(self, sid: int) -> None:
        if self._lib.sharc_native_sec_raise(self._handle, sid) != 0:
            raise ValueError("SEC source %d rejected" % sid)

    def dma_start(self, base: int) -> int:
        address = self._lib.sharc_native_dma_start(self._handle, base)
        if address < 0:
            raise ValueError("DMA channel %#x rejected" % base)
        return address

    def dma_done(self, base: int, sid: int) -> None:
        if self._lib.sharc_native_dma_done(self._handle, base, sid) != 0:
            raise ValueError("DMA channel %#x completion rejected" % base)

    def spi2_exchange(self, frame: bytes) -> bytes:
        out = ctypes.create_string_buffer(len(frame))
        n = self._lib.sharc_native_spi2_exchange(self._handle, frame, len(frame), out)
        if n < 0:
            raise ValueError("SPI2 exchange rejected")
        return out.raw[:n]

    def sport_block(
        self, block: bytes | None = None, capacity: int = 4096
    ) -> bytes | None:
        """One SPORT4 audio block; None while the SPORTs are not running."""
        out = ctypes.create_string_buffer(capacity)
        n = self._lib.sharc_native_sport_block(
            self._handle, block, 0 if block is None else len(block), out, capacity
        )
        if n == -2:
            return None
        if n < 0:
            raise ValueError("SPORT block rejected")
        return out.raw[:n]

    def fresh_call(self, pc: int, return_address: int | None = None) -> None:
        self._lib.sharc_native_fresh_call(
            self._handle, pc, -1 if return_address is None else return_address
        )
        self._halted_reason = None

    def run(self, max_steps: int) -> int:
        """Step until a trap or MAX_STEPS; the instructions executed."""
        rc = self._lib.sharc_native_step(self._handle, max_steps)
        if rc < 0:
            raise RuntimeError("sharc_native_step hard error (%d)" % rc)
        # A peripheral event can fail after the last requested instruction
        # completed. Timer-enabled runs must inspect that boundary too.
        if (
            rc < max_steps
            or getattr(self, "_core_timer_enabled", False)
            or getattr(self, "_peripheral_enabled", False)
        ):
            buf = ctypes.create_string_buffer(512)
            n = self._lib.sharc_native_halt_reason(self._handle, buf, 512)
            self._halted_reason = buf.raw[:n].decode("utf-8", "replace") if n else None
        return rc


# ---------------------------------------------------------------------------
# State transfer
# ---------------------------------------------------------------------------


def _overlay_ranges(overlay: Mapping[int, int]) -> dict[int, dict]:
    ranges: dict[int, dict] = {}
    start = prev = None
    buf = bytearray()
    for addr in sorted(overlay):
        if prev is not None and addr == prev + 1:
            buf.append(overlay[addr] & 0xFF)
        else:
            if start is not None:
                ranges[start] = {"length": len(buf), "data": buf.hex()}
            start, buf = addr, bytearray([overlay[addr] & 0xFF])
        prev = addr
    if start is not None:
        ranges[start] = {"length": len(buf), "data": buf.hex()}
    return ranges


def to_native(core: NativeCore, state: st.State) -> None:
    """Load Python STATE into CORE: registers, stacks, MMRs, the whole
    overlay, which special registers exist, and the run configuration."""
    fields = sd.export_state(state, page_hash=False)
    fields["memory_ranges"] = _overlay_ranges(state.overlay)
    core.load_state(fields)
    present = 0
    for i, name in enumerate(SPECIAL_SLOTS):
        if name in state.special:
            present |= 1 << i
    core.set_option(OPT_SPECIAL_PRESENT, present)
    core.set_option(OPT_EXPLICIT_MEMORY_MODEL, state.explicit_memory_model)
    core.set_option(OPT_APPROX_RECIPS, state.approx_recips)
    core.set_option(OPT_ASSUME_NW32, state.assume_nw32)
    core.set_option(OPT_FOLLOW_LOADED_CALLS, state.follow_loaded_calls)
    core.set_option(OPT_MAX_CALL_DEPTH, state.max_call_depth)
    core.set_option(OPT_CONTINUE_EXTERNAL_CALLS, state.continue_external_calls)
    core.set_option(OPT_DATA_MEMORY_TAINTED, state.data_memory_tainted)
    core.set_option(OPT_HAS_CONCRETE, state.concrete is not None)
    core.set_option(OPT_DOSSIER_BYTES, state.dossier_bytes)
    core.set_option(OPT_BANK_MODEL, state.bank_model)
    core.set_option(OPT_STACK_MODEL, state.stack_model)
    core.set_option(OPT_SOFTWARE_INTERRUPTS, state.software_interrupts)
    core.set_option(OPT_CORE_TIMER, state.core_timer)
    core.set_option(OPT_PERIPHERAL_MODEL, state.peripheral_model)
    if state.provisional_forms:
        raise ValueError("provisional_forms are not supported natively")
    for name, mode in state.provisional_interpretations.items():
        core.set_provisional(name, mode)


def to_python(core: NativeCore, template: st.State) -> st.State:
    """CORE's state as a Python State, with TEMPLATE's configuration (and
    its loader image)."""
    import dataclasses

    core.set_option(OPT_EXPORT_RANGES, 1)
    try:
        fields = core.export_state()
    finally:
        core.set_option(OPT_EXPORT_RANGES, 0)
    overlay: dict[int, int] = {}
    for address, r in fields["memory_ranges"].items():
        data = bytes.fromhex(r["data"])
        for i, b in enumerate(data):
            overlay[int(address) + i] = b
    fields = dict(fields)
    fields["memory_ranges"] = {}
    state = sd.import_state(fields, data=None)
    present = core.stats()["special_present"]
    special = {
        name: state.special[name]
        for i, name in enumerate(SPECIAL_SLOTS)
        if present & (1 << i)
    }
    return dataclasses.replace(
        template,
        pc_sw=state.pc_sw,
        uregs=state.uregs,
        special=special,
        mmrs=state.mmrs,
        pending=state.pending,
        loops=state.loops,
        call_stack=state.call_stack,
        stack_model=state.stack_model,
        pc_stack=state.pc_stack,
        loop_depth=state.loop_depth,
        loop_slots=state.loop_slots,
        pc_stack_pending=state.pc_stack_pending,
        pc_stack_requested=state.pc_stack_requested,
        status_stack=state.status_stack,
        overlay=overlay,
        stopped=None,
        trace=[],
        bank_model=state.bank_model,
        bank_active_mask=state.bank_active_mask,
        bank_pending_mask=state.bank_pending_mask,
        bank_requested_mask=state.bank_requested_mask,
        bank_alt=state.bank_alt,
    )


# ---------------------------------------------------------------------------
# Hybrid engine: native, with the Python core for what the native side traps on
# ---------------------------------------------------------------------------


class HybridEngine:
    """The native core with Python fallback, as a sharc_diff Engine.

    A native trap leaves the native state exactly at the trapping
    instruction; the state moves to a Python Runner, the Python core runs
    that one instruction (halting there if it stops), and the state moves
    back. ``fallbacks`` counts them per trap reason."""

    def __init__(self, core: NativeCore, data: LoadedMemory) -> None:
        self.core = core
        self.data = data
        self.template: st.State | None = None
        self._halt: str | None = None
        self.fallbacks: dict[str, int] = {}
        self.fallback_seconds = 0.0

    def load_state(self, fields: Mapping[str, Any]) -> None:
        state = sd.import_state(fields, data=self.data)
        self.load_python(state)

    def load_python(self, state: st.State) -> None:
        self.template = state
        to_native(self.core, state)
        self._halt = None

    def _python_step(self) -> str | None:
        """One instruction through the Python core; the halt reason, if it
        halted."""
        assert self.template is not None
        t0 = time.perf_counter()
        state = to_python(self.core, self.template)
        runner = _runner_for(state, self.data)
        halt = None
        try:
            runner.step()
        except sr.Halt as exc:
            halt = exc.reason
        self.template = runner.state
        to_native(self.core, runner.state)
        self.fallback_seconds += time.perf_counter() - t0
        return halt

    def step(self, n: int = 1) -> None:
        if self._halt is not None:
            return
        done = 0
        while done < n:
            got = self.core.run(n - done)
            done += got
            if done >= n:
                break
            reason = self.core.halt_reason or "halted"
            key = reason.split(" at ")[0]
            self.fallbacks[key] = self.fallbacks.get(key, 0) + 1
            halt = self._python_step()
            if halt is not None:
                self._halt = halt
                return
            done += 1

    def export_state(self, **_kwargs: Any) -> dict:
        fields = self.core.export_state()
        if self._halt is not None:
            fields["stopped"] = self._halt
        return fields

    @property
    def halted(self) -> bool:
        return self._halt is not None

    @property
    def halt_reason(self) -> str | None:
        return self._halt


def _runner_for(state: st.State, data: LoadedMemory) -> sr.Runner:
    import collections

    runner = sr.Runner.__new__(sr.Runner)
    runner.data = data
    runner.state = state
    runner._watch = None
    runner.diagnose_unknown = False
    runner._last_writer = {}
    runner._diagnose_codes = ()
    runner.breakpoints = frozenset()
    runner.instructions = 0
    runner.form_counts = collections.Counter()
    runner.max_call_depth_reached = 0
    runner._cache = {}
    return runner


# ---------------------------------------------------------------------------
# Compute corpus
# ---------------------------------------------------------------------------

# Register values that make float and fixed-point corner cases likely.
_EDGE_VALUES = (
    0x00000000,  # +0
    0x80000000,  # -0 / most negative
    0x7F800000,  # +inf
    0xFF800000,  # -inf
    0x7FC00000,  # quiet NaN
    0x7F800001,  # signalling NaN
    0x00000001,  # smallest denormal
    0x807FFFFF,  # largest negative denormal
    0x00800000,  # smallest normal
    0x00C00000,  # 1.5 * smallest normal (a difference with it is a denormal)
    0x0D800000,  # 2**-100 (a square is a denormal or an underflow)
    0x1A800000,  # 2**-63
    0x7F7FFFFF,  # largest finite
    0x3F800000,  # 1.0
    0xBF800000,  # -1.0
    0x4F000000,  # 2**31
    0xCF000000,  # -2**31
    0x7FFFFFFF,
    0xFFFFFFFF,
    0x00000020,
    0x0000001F,
    0xFFFFFFE0,
)


def _edge_state(rng: random.Random) -> st.State:
    state = sd._random_state(rng, seed_special=True)
    for code in range(16):
        if rng.random() < 0.6:
            state.uregs[code] = st.Const(rng.choice(_EDGE_VALUES))
        if rng.random() < 0.6:
            state.uregs[80 + code] = st.Const(rng.choice(_EDGE_VALUES))
    mode1 = st.UREG_CODES["MODE1"]
    bits = rng.getrandbits(32) & ~(1 << 21)
    if rng.random() < 0.3:
        bits |= 1 << 21  # SIMD
    state.uregs[mode1] = st.Const(bits)
    astat = st.UREG_CODES["ASTATX"]
    if rng.random() < 0.3:
        state.uregs[astat] = st.PartialConst(
            rng.getrandbits(32) | 1, rng.getrandbits(32)
        )
    return state


def _case(
    table: str,
    opcode: int,
    form: str,
    fields: dict[str, int],
    rng: random.Random,
    edge: bool,
) -> sd.ComputeCase:
    if not edge:
        return sd._run_one_compute_case(table, opcode, form, fields, rng)
    # sharc_diff._run_one_compute_case with an edge-value state.
    length_bytes = {"2a": 6, "2a_short": 4, "2c": 2}[form]
    state = _edge_state(rng)
    insn = st.Instruction(
        offset=0,
        length_bytes=length_bytes,
        type_name=form,
        fields=fields,
        kind="confident",
    )
    before = sd.export_state(state, page_hash=False)
    present = [name for name in SPECIAL_SLOTS if name in state.special]
    try:
        out = st._execute(state, insn)
    except Exception as exc:  # noqa: BLE001 -- recorded
        case = sd.ComputeCase(
            table,
            opcode,
            form,
            fields,
            before,
            None,
            "%s: %s" % (type(exc).__name__, exc),
        )
        case.present = present  # type: ignore[attr-defined]
        return case
    if len(out) != 1:
        case = sd.ComputeCase(table, opcode, form, fields, before, None, "forked")
        case.present = present  # type: ignore[attr-defined]
        return case
    case = sd.ComputeCase(
        table,
        opcode,
        form,
        fields,
        before,
        sd.export_state(out[0], page_hash=False),
        None,
    )
    case.present = present  # type: ignore[attr-defined]
    return case


def corpus_cases(seed: int, cases_per_op: int) -> list[sd.ComputeCase]:
    """sharc_diff's compute corpus (ALU/MULT/SHIFT function tables and the
    short ops) plus what it leaves out: the fixed-point multiplier specs
    (MULT_FIXED_OPS), the multifunction rows, dual add/subtract, the MR
    data moves and multiply-accumulate patterns; each op half with
    sharc_diff's random registers and half with float/fixed edge values."""
    from tools.sharc_dev.sharc_core.compute_alu import ALU_OPS
    from tools.sharc_dev.sharc_core.compute_mult import (
        MR_DATAMOVE_REGISTERS,
        MULT_FIXED_OPS,
        MULT_OPS,
    )
    from tools.sharc_dev.sharc_core.compute_multi import MULTIFN_MUL_ALU_OPS, SHORT_OPS
    from tools.sharc_dev.sharc_core.compute_shift import SHIFT_OPS

    rng = random.Random(seed)
    cases: list[sd.ComputeCase] = []

    def full(cu: int, opcode: int) -> dict[str, int]:
        rn, rx, ry = rng.randrange(16), rng.randrange(16), rng.randrange(16)
        return sd._full_compute_fields(cu, opcode, rn, rx, ry)

    def raw(field: int) -> dict[str, int]:
        return {
            "cond[4:0]": 0x1F,
            "compute[22:16]": (field >> 16) & 0x7F,
            "compute[15:0]": field & 0xFFFF,
        }

    for table, cu, ops in (
        ("alu", 0, ALU_OPS),
        ("mult", 1, MULT_OPS),
        ("multfixed", 1, MULT_FIXED_OPS),
        ("shift", 2, SHIFT_OPS),
    ):
        for opcode in sorted(ops):
            for i in range(cases_per_op):
                cases.append(
                    _case(table, opcode, "2a", full(cu, opcode), rng, i % 2 == 1)
                )
    for opcode in sorted(SHORT_OPS):
        for i in range(cases_per_op):
            f = sd._short_compute_fields(opcode, rng.randrange(16), rng.randrange(16))
            cases.append(_case("short", opcode, "2c", f, rng, i % 2 == 1))
    # Dual add/subtract: cu=0, opcode top nibble 0x7 (fixed) / 0xF (float).
    for top in (0x7, 0xF):
        for i in range(cases_per_op * 4):
            opcode = (top << 4) | rng.randrange(16)
            cases.append(_case("dual", opcode, "2a", full(0, opcode), rng, i % 2 == 1))
    # Multifunction (mf=1): bit 22, category bits 21:16.
    for category in sorted(MULTIFN_MUL_ALU_OPS) + [0x30 + k for k in range(16)]:
        for i in range(cases_per_op):
            field = (1 << 22) | ((category & 0x3F) << 16) | rng.getrandbits(16)
            cases.append(_case("multifn", category, "2a", raw(field), rng, i % 2 == 1))
    # MR data moves: bits 22:17 = 100000, direction bit 16, opcode 15:12.
    for opcode in sorted(MR_DATAMOVE_REGISTERS):
        for i in range(cases_per_op):
            field = (
                (0b100000 << 17)
                | (rng.randrange(2) << 16)
                | (opcode << 12)
                | (rng.randrange(16) << 8)
            )
            cases.append(_case("mrmove", opcode, "2a", raw(field), rng, i % 2 == 1))
    # Multiply-accumulate into MRF (cu=1 opcodes 0xB4/0xB0, any mf).
    for opcode in (0xB4, 0xB0):
        for i in range(cases_per_op):
            f = full(1, opcode)
            if i % 3 == 2:
                f = raw(((f["compute[22:16]"] << 16) | f["compute[15:0]"]) | (1 << 22))
            cases.append(_case("mac", opcode, "2a", f, rng, i % 2 == 1))
    return cases


def run_case(core: NativeCore, case: sd.ComputeCase) -> list[str]:
    """Replay CASE natively; the differences from the Python result."""
    if case.error is not None or case.state_after is None:
        return []
    fields = dict(case.state_before)
    core.load_state(fields)
    core.set_option(OPT_HAS_CONCRETE, 0)
    core.set_option(OPT_FOLLOW_LOADED_CALLS, 0)
    present = getattr(case, "present", None)
    if present is None:
        present = [
            n for n in SPECIAL_SLOTS if case.state_before["special"][n]["kind"] != 0
        ]
    core.set_option(
        OPT_SPECIAL_PRESENT,
        sum(1 << i for i, n in enumerate(SPECIAL_SLOTS) if n in present),
    )
    length = {"2a": 6, "2a_short": 4, "2c": 2}[case.form]
    ok = core.exec_insn(pack_insn(case.form, length, "confident", case.fields))
    if not ok:
        return ["native trapped: %s" % core.halt_reason]
    got = core.export_state()
    want = sd.unpack_state(sd.pack_state(case.state_after))
    got.pop("page_hashes", None)
    want.pop("page_hashes", None)
    return sd.compare_states(want, got)


def run_corpus(
    seed: int, cases_per_op: int, lib: str = DEFAULT_LIB, verbose: bool = True
) -> dict:
    t0 = time.perf_counter()
    cases = corpus_cases(seed, cases_per_op)
    t1 = time.perf_counter()
    core = NativeCore(pack_image(None), lib)
    ok = diverged = traps = errors = 0
    by_op: dict[str, int] = {}
    examples: list[str] = []
    for case in cases:
        if case.error is not None or case.state_after is None:
            errors += 1
            continue
        diff = run_case(core, case)
        key = "%s %#x" % (case.op_table, case.opcode)
        if not diff:
            ok += 1
            continue
        if diff[0].startswith("native trapped"):
            traps += 1
        else:
            diverged += 1
        by_op[key] = by_op.get(key, 0) + 1
        if len(examples) < 40:
            examples.append(
                "%s fields=%s: %s" % (key, case.fields, "; ".join(diff[:4]))
            )
    summary = {
        "cases": len(cases),
        "python_errors_or_forks": errors,
        "match": ok,
        "diverged": diverged,
        "native_traps": traps,
        "by_op": by_op,
        "examples": examples,
        "python_seconds": round(t1 - t0, 2),
        "native_seconds": round(time.perf_counter() - t1, 2),
    }
    if verbose:
        print(
            json.dumps({k: v for k, v in summary.items() if k != "examples"}, indent=1)
        )
        for line in examples:
            print("  " + line)
    return summary


# ---------------------------------------------------------------------------
# Instruction lockstep (sharc_diff.run_lockstep) and frames
# ---------------------------------------------------------------------------


def run_lockstep(
    image: str,
    start: int,
    steps: int,
    every: int,
    lib: str = DEFAULT_LIB,
    blocks: bool = True,
) -> sd.LockstepResult:
    """PythonEngine against HybridEngine from sharc_run.make_state(START),
    compared every EVERY instructions (1: each instruction through the
    one-instruction interpreter; larger: block code runs between
    comparisons)."""
    data = sr._load_image_memory(image)
    seed = sr.make_state(data, start)
    fields = sd.export_state(seed, page_hash=True)
    py = sd.PythonEngine(data)
    py.load_state(fields)
    core = NativeCore(pack_image(data), lib)
    core.set_option(OPT_BLOCKS, int(blocks))
    hy = HybridEngine(core, data)
    hy.load_state(fields)
    result = sd.run_lockstep(py, hy, max_steps=steps, compare_every=every)
    result.fallbacks = dict(hy.fallbacks)  # type: ignore[attr-defined]
    return result


def python_collect_step(
    state: st.State, data: LoadedMemory
) -> tuple[st.State, str | None]:
    """One instruction under sharc_survey.run_collect_all's rules with an
    empty patch table (a two-way fork continues not-taken): the new state,
    and the terminal category when the call ended there."""
    from tools.sharc_dev import sharc_survey as sv

    runner = _runner_for(state, data)
    result = sv.run_collect_all(runner, {}, 1)
    last = result.stops[-1] if result.stops else None
    terminal = None if last is None or last.category == "max-steps" else last.category
    return runner.state, terminal


def hybrid_call(
    core: NativeCore, state: st.State, data: LoadedMemory, max_steps: int
) -> tuple[st.State, str | None, dict]:
    """Run a call natively from Python STATE until it ends (the Python core
    running every instruction the native side traps on); the final state,
    the terminal category, and counters."""
    t0 = time.perf_counter()
    to_native(core, state)
    template = state
    done = 0
    native_seconds = 0.0
    native_instructions = 0
    fallbacks: dict[str, int] = {}
    terminal = None
    while done < max_steps:
        t = time.perf_counter()
        n = core.run(max_steps - done)
        native_seconds += time.perf_counter() - t
        native_instructions += n
        done += n
        if done >= max_steps:
            break
        reason = core.halt_reason or "halted"
        fallbacks[reason] = fallbacks.get(reason, 0) + 1
        py_state = to_python(core, template)
        py_state, terminal = python_collect_step(py_state, data)
        done += 1
        template = py_state
        if terminal is not None:
            break
        to_native(core, py_state)
    if terminal is None:
        template = to_python(core, template)
    return (
        template,
        terminal,
        {
            "instructions": done,
            "native_instructions": native_instructions,
            "native_seconds": native_seconds,
            "seconds": time.perf_counter() - t0,
            "fallbacks": fallbacks,
            "stats": core.stats(),
        },
    )


def frame_start(
    image: str, snapshot: str | None, lp0: str | None
) -> tuple[sr.Runner, LoadedMemory]:
    """sharc_replay's starting point: run_init, a 1 kHz tone on voice 0,
    the DMA replay path, optionally an LP0 feed. Cached in SNAPSHOT."""
    import math

    from tools.sharc_dev import sharc_harness as h

    memory = h.load_image_memory(image)
    if snapshot and os.path.exists(snapshot):
        return sr.load_snapshot(snapshot, memory), memory
    init = h.run_init(memory, image)
    if not init.ran:
        raise SystemExit("run_init failed: %s" % init.error)
    runner = h.new_runner(memory, image, init=init)
    state = runner.state
    sample_base, sample_len = 0x310000, 4096
    tone = [
        math.sin(2 * math.pi * (1000.0 / h.SOURCE_SAMPLE_RATE) * i)
        for i in range(sample_len)
    ]
    h._write_samples(state, sample_base, tone, "int16")
    h.setup_voice(state, image, voice=0, sample_len=sample_len, sample_base=sample_base)
    h.setup_frame_dma(state, image, ring_flag=0)
    if lp0:
        from tools.sharc_dev import sharc_lp0

        runner, _ = sharc_lp0.feed(runner, image, list(sharc_lp0.read_log(lp0)))
    if snapshot:
        sr.save_snapshot(runner, snapshot)
    return runner, memory


def _deliver(runner: sr.Runner, image: str, tx: bytes) -> sr.Runner:
    """The host side of a frame (sharc_replay.replay): the DMA transfer and
    its completion call, then a fresh call of the block handler."""
    from tools.sharc_dev import sharc_harness as h

    h.write_dma_transfer(runner.state, image, tx)
    runner = h.drive_dma_completion(runner, image)
    return runner.fresh_call(h.profile(image).block_handler)


# A stop whose native trap leaves exactly the state the Python core halts
# in: it happens before the instruction changes anything the canonical
# state holds (the handler's first test), so the native side ends the call
# itself instead of handing the state to Python.
CLEAN_CALL_END = (
    "sharc_core.forms_flow._type_9b_abs:",
    "sharc_core.forms_flow._type_9a_abs:",
)
CALL_END_REASON = "return without followed call"


class NativeFrames:
    """Frames through the native core, the state staying native between
    frames (sharc_replay.replay's host steps done through the ABI); the
    Python core runs only what the native side traps on."""

    def __init__(
        self, core: NativeCore, image: str, state: st.State, data: LoadedMemory
    ) -> None:
        from tools.sharc_dev import sharc_harness as h

        self.core = core
        self.image = image
        self.data = data
        self.template = state
        self.profile = h.profile(image)
        self.fallbacks: dict[str, int] = {}
        self.fallback_seconds = 0.0
        self.stopped: str | None = None
        to_native(core, state)

    def _run_call(self, max_steps: int) -> tuple[int, str | None]:
        """Run the current call to its end; (instructions, halt reason)."""
        done = 0
        while done < max_steps:
            n = self.core.run(max_steps - done)
            done += n
            if done >= max_steps:
                return done, "max-steps"
            reason = self.core.halt_reason or "halted"
            if CALL_END_REASON in reason and reason.startswith(
                tuple(NATIVE_TRAP + " " + p for p in CLEAN_CALL_END)
            ):
                return done + 1, CALL_END_REASON
            key = reason.split(" at ")[0]
            self.fallbacks[key] = self.fallbacks.get(key, 0) + 1
            t = time.perf_counter()
            state = to_python(self.core, self.template)
            state, terminal = python_collect_step(state, self.data)
            self.template = state
            done += 1
            if terminal is not None:
                self.fallback_seconds += time.perf_counter() - t
                to_native(self.core, state)
                return done, state.stopped or terminal
            to_native(self.core, state)
            self.fallback_seconds += time.perf_counter() - t
        return done, "max-steps"

    def frame(self, tx: bytes) -> dict:
        """One DSPI2 frame: the DMA transfer, its completion call, and the
        block handler call (sharc_replay.replay)."""
        from tools.sharc_dev import sharc_harness as h

        core, p = self.core, self.profile
        t0 = time.perf_counter()
        shift = (core.peek(p.command_word_shift_src, 4) or 0) & 1
        base = (p.command_word + (1 - shift) * h.RING_SIZE_BYTES) & 0xFFFFFFFF
        data = h._swap16(tx[: h.RING_SIZE_BYTES])
        if core.poke(base, data, 1) != len(data):
            raise RuntimeError("DMA transfer poke did not take effect")
        core.fresh_call(h.DMA_SHIFT_CALLBACK)
        core.set_reg(st.UREG_CODES["R8"], st.Const(h.DMA_SHIFT_CALLBACK_COMPLETE_EVENT))
        n1, why = self._run_call(64)
        if why != CALL_END_REASON:
            raise RuntimeError("DMA completion call did not return: %s" % why)
        core.fresh_call(p.block_handler)
        t1 = time.perf_counter()
        n2, why = self._run_call(4_000_000)
        t2 = time.perf_counter()
        self.stopped = why
        return {
            "instructions": n1 + n2,
            "frame_instructions": n2,
            "terminal": why,
            "seconds": t2 - t0,
            "frame_seconds": t2 - t1,
        }

    def export(self) -> dict:
        fields = self.core.export_state()
        fields["stopped"] = self.stopped
        return fields


def run_frames(
    image: str,
    capture: str,
    frames: range,
    *,
    lib: str = DEFAULT_LIB,
    snapshot: str | None = None,
    lp0: str | None = None,
    compare: bool = True,
    verbose: bool = True,
    save_at: int | None = None,
    save_to: str | None = None,
) -> dict:
    """Frames 0..FRAMES.stop-1 of CAPTURE through the Python replay and
    through the native core from the same start (sharc_replay.replay's
    set-up, cached in SNAPSHOT); the two exported states are compared
    after every frame in FRAMES. SAVE_AT/SAVE_TO keep the Python state
    after that frame (a later run's SNAPSHOT)."""
    from tools.sharc_dev import sharc_capture
    from tools.sharc_dev import sharc_harness as h
    from tools.sharc_dev import sharc_survey as sv

    cap = sharc_capture.load(capture)
    runner, memory = frame_start(image, snapshot, lp0)
    first = 0
    if snapshot and os.path.exists(snapshot + ".frame"):
        with open(snapshot + ".frame") as fh:
            first = int(fh.read())
    ref = runner
    core = NativeCore(pack_image(memory), lib)
    nat = NativeFrames(core, image, h._clone_state(runner.state), memory)
    rows = []
    for idx, frame in enumerate(cap.dspi2_frames[: frames.stop]):
        if idx < first:
            continue
        row: dict[str, Any] = {"frame": idx}
        if compare:
            t = time.perf_counter()
            ref = _deliver(ref, image, frame.tx)
            result = sv.run_collect_all(ref, h.FRAME_PATCH_TABLE, 4_000_000, img=None)
            row["python_seconds"] = round(time.perf_counter() - t, 3)
            row["python_instructions"] = result.instructions
        info = nat.frame(frame.tx)
        row.update(
            native_instructions=info["frame_instructions"],
            native_terminal=info["terminal"],
            native_frame_us=round(info["frame_seconds"] * 1e6, 1),
            fallbacks=dict(nat.fallbacks),
        )
        stats = core.stats()
        row["block_instructions_total"] = stats.get("block_instructions")
        if compare and idx in frames:
            diff = sd.compare_states(sd.export_state(ref.state), nat.export())
            row["diff"] = diff[:12]
        rows.append(row)
        if verbose:
            print(json.dumps(row), flush=True)
        if save_at is not None and idx == save_at and save_to and compare:
            sr.save_snapshot(ref, save_to)
            with open(save_to + ".frame", "w") as fh:
                fh.write(str(idx + 1))
        if compare and row.get("diff"):
            break
    return {"frames": rows}


def _state_options(state: st.State) -> list[tuple[int, int]]:
    """The sharc_native_set_option calls ``to_native`` makes for STATE."""
    present = 0
    for i, name in enumerate(SPECIAL_SLOTS):
        if name in state.special:
            present |= 1 << i
    return [
        (OPT_SPECIAL_PRESENT, present),
        (OPT_EXPLICIT_MEMORY_MODEL, int(state.explicit_memory_model)),
        (OPT_APPROX_RECIPS, int(state.approx_recips)),
        (OPT_ASSUME_NW32, int(state.assume_nw32)),
        (OPT_FOLLOW_LOADED_CALLS, int(state.follow_loaded_calls)),
        (OPT_MAX_CALL_DEPTH, int(state.max_call_depth)),
        (OPT_CONTINUE_EXTERNAL_CALLS, int(state.continue_external_calls)),
        (OPT_DATA_MEMORY_TAINTED, int(state.data_memory_tainted)),
        (OPT_HAS_CONCRETE, int(state.concrete is not None)),
        (OPT_DOSSIER_BYTES, int(state.dossier_bytes)),
        (OPT_BANK_MODEL, int(state.bank_model)),
        (OPT_STACK_MODEL, int(state.stack_model)),
        (OPT_SOFTWARE_INTERRUPTS, int(state.software_interrupts)),
        (OPT_CORE_TIMER, int(state.core_timer)),
        (OPT_PERIPHERAL_MODEL, int(state.peripheral_model)),
    ]


def pack_frames(
    image: str,
    capture: str,
    count: int,
    out_path: str,
    *,
    snapshot: str | None = None,
    lp0: str | None = None,
) -> dict:
    """Write the frame pack packages/emulator/sharc's ``sharc-frames`` replays without
    Python: magic "SHFP", version 1; the image blob; the start state
    (``to_native``'s: SHRD with the whole overlay as ranges) and its
    set_option calls; the host constants of ``NativeFrames.frame``
    (command_word_shift_src, command_word, ring size, DMA callback, R8 code
    and value, block_handler); then COUNT frames' DMA data (byte-swapped,
    one ring). Frames start at the snapshot's own frame."""
    import struct

    from tools.sharc_dev import sharc_capture
    from tools.sharc_dev import sharc_harness as h

    if lp0 and snapshot and not os.path.exists(snapshot):
        raise SystemExit("pack needs an existing --snapshot (see the frames command)")
    cap = sharc_capture.load(capture)
    runner, memory = frame_start(image, snapshot, lp0)
    first = 0
    if snapshot and os.path.exists(snapshot + ".frame"):
        with open(snapshot + ".frame") as fh:
            first = int(fh.read())
    state = h._clone_state(runner.state)
    fields = sd.export_state(state, page_hash=False)
    fields["memory_ranges"] = _overlay_ranges(state.overlay)
    blob = sd.pack_state(fields)
    img = pack_image(memory)
    opts = _state_options(state)
    p = h.profile(image)
    out = bytearray(b"SHFP") + struct.pack("<I", 1)
    out += struct.pack("<I", len(img)) + img
    out += struct.pack("<I", len(blob)) + blob
    out += struct.pack("<I", len(opts))
    for key, value in opts:
        out += struct.pack("<Iq", key, value)
    out += struct.pack(
        "<7I",
        p.command_word_shift_src,
        p.command_word,
        h.RING_SIZE_BYTES,
        h.DMA_SHIFT_CALLBACK,
        st.UREG_CODES["R8"],
        h.DMA_SHIFT_CALLBACK_COMPLETE_EVENT,
        p.block_handler,
    )
    frames = cap.dspi2_frames[first : first + count]
    out += struct.pack("<II", first, len(frames))
    for frame in frames:
        data = h._swap16(frame.tx[: h.RING_SIZE_BYTES])
        out += struct.pack("<I", len(data)) + data
    with open(out_path, "wb") as fh:
        fh.write(out)
    return {"path": out_path, "bytes": len(out), "first": first, "frames": len(frames)}


# ---------------------------------------------------------------------------
# Live packs (packages/emulator/live's SHARC frame source)
# ---------------------------------------------------------------------------

LIVE_DIR = os.path.join(ROOT, "out", "native", "live")
LIVE_TRAILER = b"SHLV"
LIVE_VERSION = 1
# The trailer layout written (packages/emulator/live's LivePack reads 1 to 3): version
# 2 appends the card image's SHA-256 after the key, version 3 then the
# tools/sharc_dev/sharc_core hash the start state was built with (packages/emulator/live refuses
# a library generated from another core).
LIVE_TRAILER_VERSION = 3
# Files whose code builds the start state or defines its format: a change
# to any of them makes a cached pack stale. A directory entry stands for
# every *.py file under it.
LIVE_STATE_FILES = (
    "sharc_harness",
    "sharc_lp0.py",
    "sharc_run.py",
    "sharc_trace.py",
    "sharc_diff.py",
    "sharcldr.py",
)


def _file_sha256(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def live_key(
    image_blob: bytes,
    capture: str | None,
    lp0: str | None,
    start_frame: int,
    card_sha256: str | None = None,
) -> str:
    """The cache key of a live pack: the image blob, the capture (None: a
    state pack, no frames), the LP0 log, the Python core
    (sharc_transpile.core_hash) and the files that build the start state,
    the start frame, and the card image the LP0 log came from (when named;
    a capture pack without one keeps its old key)."""
    import hashlib

    from tools.sharc_dev import sharc_transpile

    h = hashlib.sha256()
    h.update(b"live-pack %d\n" % LIVE_VERSION)
    h.update(hashlib.sha256(image_blob).digest())
    h.update(_file_sha256(capture).encode() if capture else b"no capture")
    h.update((_file_sha256(lp0) if lp0 else "-").encode())
    h.update(sharc_transpile.core_hash().encode())
    for name in LIVE_STATE_FILES:
        path = os.path.join(HERE, name)
        if os.path.isdir(path):
            rels = sorted(
                os.path.relpath(os.path.join(dirpath, f), path)
                for dirpath, _dirs, files in os.walk(path)
                for f in files
                if f.endswith(".py")
            )
            for rel in rels:
                h.update(rel.encode())
                h.update(_file_sha256(os.path.join(path, rel)).encode())
        else:
            h.update(_file_sha256(path).encode())
    h.update(b"start %d" % start_frame)
    if card_sha256:
        h.update(b"\ncard %s" % card_sha256.lower().encode())
    return h.hexdigest()[:24]


def armed_start(
    image: str, lp0: str | None, *, init_limit: int = 2_000_000
) -> tuple[sr.Runner, LoadedMemory]:
    """sharc_replay.replay_armed_voice's state before its first frame:
    run_init, the frame DMA set-up (ring flag 0), then the LP0 feed of the
    real FlexBus log. No test tone and no voice set-up (frame_start's
    replay path has both). INIT_LIMIT bounds run_init; LP0 arm and each
    finite-log transfer have separate instruction caps in sharc_lp0."""
    from tools.sharc_dev import sharc_harness as h

    if init_limit <= 0:
        raise ValueError("init instruction limit must be positive")
    memory = h.load_image_memory(image)
    init = h.run_init(memory, image, max_steps=init_limit)
    if not init.ran:
        raise SystemExit("run_init failed: %s" % init.error)
    runner = h.new_runner(memory, image, init=init)
    h.setup_frame_dma(runner.state, image, ring_flag=0)
    if lp0:
        from tools.sharc_dev import sharc_lp0

        runner, _ = sharc_lp0.feed(runner, image, sharc_lp0.read_log(lp0))
    return runner, memory


def live_pack(
    image: str,
    capture: str | None,
    lp0: str | None,
    *,
    start_frame: int = 74,
    card_sha256: str | None = None,
    out_dir: str = LIVE_DIR,
    voices: Sequence[int] = (0, 1),
    rebuild: bool = False,
    init_limit: int = 2_000_000,
) -> dict:
    """The pack packages/emulator/live plays: the SHFP frame pack (pack_frames'
    format, so sharc-frames reads it too) from armed_start's state, with
    CAPTURE's frames START_FRAME.. to the end (none when CAPTURE is None: a
    state pack, for frames that arrive live), then a trailer: "SHLV",
    LIVE_TRAILER_VERSION, the voice count and per voice the record address,
    the work buffer offset and word count (sharc_harness.read_voice_work_
    buffer_decimated's inputs), the cache key, and CARD_SHA256 (the card
    image the LP0 log was recorded from; empty when not named) and the
    tools/sharc_dev/sharc_core hash (sharc_transpile.core_hash, also in the key).
    Cached as OUT_DIR/<key>.pack (state packs: state-<key>.pack); returns
    its path, key and how long building took."""
    import struct

    from tools.sharc_dev import sharc_capture, sharc_transpile
    from tools.sharc_dev import sharc_harness as h

    memory = h.load_image_memory(image)
    img = pack_image(memory)
    card = card_sha256.lower() if card_sha256 else ""
    key = live_key(img, capture, lp0, start_frame, card or None)
    name = ("%s.pack" if capture else "state-%s.pack") % key
    path = os.path.join(out_dir, name)
    if os.path.exists(path) and not rebuild:
        if init_limit != 2_000_000:
            raise ValueError(
                "a cached pack has no init-step receipt: pass rebuild=True for a custom limit"
            )
        return {"path": path, "key": key, "cached": True, "seconds": 0.0}
    t0 = time.perf_counter()
    cap = sharc_capture.load(capture) if capture else None
    runner, _ = armed_start(image, lp0, init_limit=init_limit)
    state = h._clone_state(runner.state)
    fields = sd.export_state(state, page_hash=False)
    fields["memory_ranges"] = _overlay_ranges(state.overlay)
    blob = sd.pack_state(fields)
    opts = _state_options(state)
    p = h.profile(image)
    out = bytearray(b"SHFP") + struct.pack("<I", 1)
    out += struct.pack("<I", len(img)) + img
    out += struct.pack("<I", len(blob)) + blob
    out += struct.pack("<I", len(opts))
    for k, value in opts:
        out += struct.pack("<Iq", k, value)
    out += struct.pack(
        "<7I",
        p.command_word_shift_src,
        p.command_word,
        h.RING_SIZE_BYTES,
        h.DMA_SHIFT_CALLBACK,
        st.UREG_CODES["R8"],
        h.DMA_SHIFT_CALLBACK_COMPLETE_EVENT,
        p.block_handler,
    )
    frames = cap.dspi2_frames[start_frame:] if cap is not None else []
    out += struct.pack("<II", start_frame, len(frames))
    for frame in frames:
        data = h._swap16(frame.tx[: h.RING_SIZE_BYTES])
        out += struct.pack("<I", len(data)) + data
    out += LIVE_TRAILER + struct.pack("<II", LIVE_TRAILER_VERSION, len(voices))
    for v in voices:
        out += struct.pack(
            "<III", h.voice_record_address(image, v), h.FIELD_WORK_BUFFER, 64
        )
    out += struct.pack("<I", len(key)) + key.encode()
    out += struct.pack("<I", len(card)) + card.encode()
    core = sharc_transpile.core_hash().encode()
    out += struct.pack("<I", len(core)) + core
    os.makedirs(out_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(out)
    os.replace(tmp, path)
    seconds = time.perf_counter() - t0
    return {"path": path, "key": key, "cached": False, "seconds": round(seconds, 1)}


def state_pack(
    image: str,
    lp0: str | None,
    card_sha256: str | None,
    *,
    out_dir: str = LIVE_DIR,
    rebuild: bool = False,
    init_limit: int = 2_000_000,
) -> dict:
    """The frameless live pack packages/emulator/live's LiveSource starts from:
    armed_start(IMAGE, LP0) (run_init, the frame DMA set-up, the LP0 feed
    of the FlexBus log), keyed and marked with CARD_SHA256, the card image
    that log was recorded from (packages/emulator/live refuses the pack for another
    card). Built once (about 2 min), then cached; see live_pack."""
    return live_pack(
        image,
        None,
        lp0,
        start_frame=0,
        card_sha256=card_sha256,
        out_dir=out_dir,
        rebuild=rebuild,
        init_limit=init_limit,
    )


def live_reference(
    image: str,
    capture: str,
    lp0: str | None,
    *,
    start_frame: int = 74,
    frames: int = 16,
    out_dir: str = LIVE_DIR,
) -> dict:
    """The Python replay's voice outputs for FRAMES frames from
    START_FRAME (sharc_replay.replay_armed_voice with record_voices 0 and 1,
    the replay behind out/listen/*-voices.wav), cached next to the pack as
    ref-<key>-<frames>.json: {"key", "arm_frame", "voice_outputs": {"0": [...],
    "1": [...]} (from the arm frame on), "frame_stops"}."""
    from tools.sharc_dev import sharc_harness as h
    from tools.sharc_dev import sharc_replay

    key = live_key(pack_image(h.load_image_memory(image)), capture, lp0, start_frame)
    path = os.path.join(out_dir, "ref-%s-%d.json" % (key, frames))
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    t0 = time.perf_counter()
    rep = sharc_replay.replay_armed_voice(
        image,
        capture,
        voice=0,
        extra_frames=0,
        n_frames=frames,
        start_frame=start_frame,
        flexbus_log=lp0,
        record_voices=(0, 1),
    )
    ref = {
        "key": key,
        "start_frame": start_frame,
        "frames": frames,
        "arm_frame": rep.get("arm_frame"),
        "voice_outputs": {str(k): v for k, v in rep.get("voice_outputs", {}).items()},
        "frame_stops": [
            {"frame": s["frame"], "reason": s["reason"], "pc": s["pc"]}
            for s in rep.get("frame_stops", [])
        ],
        "seconds": round(time.perf_counter() - t0, 1),
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(ref, fh)
    return ref


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--lib", default=DEFAULT_LIB)
    sub = p.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser(
        "corpus", help="random-operand compute corpus, native vs Python"
    )
    pc.add_argument("--seed", type=int, default=1)
    pc.add_argument("--cases-per-op", type=int, default=8)
    pl = sub.add_parser(
        "lockstep", help="instruction lockstep from sharc_run.make_state"
    )
    pl.add_argument("image")
    pl.add_argument("--start", type=lambda x: int(x, 0), default=0x1C4ECF)
    pl.add_argument("--steps", type=int, default=2000)
    pl.add_argument("--every", type=int, default=1)
    pl.add_argument("--no-blocks", action="store_true")
    pf = sub.add_parser("frames", help="capture frames, native vs the Python replay")
    pf.add_argument("image")
    pf.add_argument("capture")
    pf.add_argument("--frames", default="0-2", help="compare after these frames (A-B)")
    pf.add_argument("--snapshot", help="cache of the starting state (out/...)")
    pf.add_argument("--lp0", help="FlexBus log to feed over LP0 first")
    pf.add_argument("--no-compare", action="store_true", help="native only (timing)")
    pf.add_argument(
        "--save-at", type=int, help="save the Python state after this frame"
    )
    pf.add_argument("--save-to", help="snapshot path for --save-at")
    pp = sub.add_parser(
        "pack",
        help="write a frame pack for packages/emulator/sharc's sharc-frames (no Python)",
    )
    pp.add_argument("image")
    pp.add_argument("capture")
    pp.add_argument("out", help="pack path (under out/: firmware-derived)")
    pp.add_argument("--count", type=int, default=91)
    pp.add_argument("--snapshot", help="cache of the starting state (out/...)")
    pp.add_argument("--lp0", help="FlexBus log to feed over LP0 first")
    ps = sub.add_parser(
        "state-pack",
        help="build (or find) the frameless pack packages/emulator/live renders live frames from",
    )
    ps.add_argument("image")
    ps.add_argument("--lp0", help="FlexBus log to feed over LP0 first")
    card = ps.add_mutually_exclusive_group(required=True)
    card.add_argument("--card-image", help="the +Drive image the LP0 log came from")
    card.add_argument("--card-sha256", help="its SHA-256 (hex)")
    ps.add_argument("--out-dir", default=LIVE_DIR)
    ps.add_argument("--rebuild", action="store_true")
    ps.add_argument(
        "--limit",
        type=int,
        help="init instruction cap; requires --rebuild (LP0 callbacks have separate limits)",
    )
    for name, help_text in (
        ("live-pack", "build (or find) the cached pack packages/emulator/live plays"),
        ("live-ref", "the Python replay's voice outputs for the live check"),
    ):
        pv = sub.add_parser(name, help=help_text)
        pv.add_argument("image")
        pv.add_argument("capture")
        pv.add_argument("--lp0", help="FlexBus log to feed over LP0 first")
        pv.add_argument("--start-frame", type=int, default=74)
        pv.add_argument("--out-dir", default=LIVE_DIR)
        if name == "live-pack":
            pv.add_argument("--rebuild", action="store_true")
            pv.add_argument(
                "--limit",
                type=int,
                help="init instruction cap; requires --rebuild (LP0 callbacks have separate limits)",
            )
        else:
            pv.add_argument("--frames", type=int, default=16)
    args = p.parse_args(argv)
    if (
        args.cmd in ("state-pack", "live-pack")
        and args.limit is not None
        and not args.rebuild
    ):
        p.error("--limit requires --rebuild: cached packs have no init-step receipt")
    if args.cmd == "state-pack":
        card_sha = args.card_sha256 or _file_sha256(args.card_image)
        info = state_pack(
            args.image,
            args.lp0,
            card_sha,
            out_dir=args.out_dir,
            rebuild=args.rebuild,
            init_limit=args.limit if args.limit is not None else 2_000_000,
        )
        print(json.dumps(info))
        return 0
    if args.cmd == "live-pack":
        info = live_pack(
            args.image,
            args.capture,
            args.lp0,
            start_frame=args.start_frame,
            out_dir=args.out_dir,
            rebuild=args.rebuild,
            init_limit=args.limit if args.limit is not None else 2_000_000,
        )
        print(json.dumps(info))
        return 0
    if args.cmd == "live-ref":
        ref = live_reference(
            args.image,
            args.capture,
            args.lp0,
            start_frame=args.start_frame,
            frames=args.frames,
            out_dir=args.out_dir,
        )
        print(
            json.dumps(
                {k: v for k, v in ref.items() if k != "voice_outputs"}
                | {"samples": {k: len(v) for k, v in ref["voice_outputs"].items()}}
            )
        )
        return 0
    if args.cmd == "pack":
        print(
            json.dumps(
                pack_frames(
                    args.image,
                    args.capture,
                    args.count,
                    args.out,
                    snapshot=args.snapshot,
                    lp0=args.lp0,
                )
            )
        )
        return 0
    if args.cmd == "corpus":
        s = run_corpus(args.seed, args.cases_per_op, args.lib)
        return 0 if s["diverged"] == 0 and s["native_traps"] == 0 else 1
    if args.cmd == "lockstep":
        r = run_lockstep(
            args.image, args.start, args.steps, args.every, args.lib, not args.no_blocks
        )
        print(
            "diverged=%s steps_agreed=%d pc=%s fallbacks=%s"
            % (r.diverged, r.steps_agreed, r.pc_sw, getattr(r, "fallbacks", {}))
        )
        for line in r.diff:
            print("  " + line)
        return 1 if r.diverged else 0
    if args.cmd == "frames":
        lo, _, hi = args.frames.partition("-")
        frames = range(int(lo), int(hi or lo) + 1)
        out = run_frames(
            args.image,
            args.capture,
            frames,
            lib=args.lib,
            snapshot=args.snapshot,
            lp0=args.lp0,
            compare=not args.no_compare,
            save_at=args.save_at,
            save_to=args.save_to,
        )
        return 1 if any(r.get("diff") for r in out["frames"]) else 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
