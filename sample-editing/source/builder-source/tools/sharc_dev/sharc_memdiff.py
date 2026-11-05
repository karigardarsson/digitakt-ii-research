"""Replay two unicorn-reference-final:tools/sharc_capture_run.py captures in lockstep from the same
post-init state (tools/sharc_dev/sharc_replay.py's own setup convention: one hand-set-up
voice, real DMA delivery) and diff the two runners' DM state after every
frame.

    uv run python -m tools.sharc_dev.sharc_memdiff dt2-1.16 CAPTURE_A.dt2cap \
        CAPTURE_B.dt2cap [--frames A:B] [--only-first-divergence] [--json]

Both captures are replayed continuously from frame 0 (no start_frame
shortcut: this tool always executes every frame up to the top of --frames,
or the whole capture if --frames is not given -- only *reporting* is
windowed), through the exact same setup tools/sharc_dev/sharc_replay.replay() uses
(sharc_harness.run_init() -> sharc_harness.new_runner() -> one hand-set-up
voice 0 with a --freq Hz sine -> sharc_harness.setup_frame_dma()) and the
exact same per-frame delivery (sharc_harness.write_dma_transfer()/
drive_dma_completion(), then tools/sharc_dev/sharc_replay.call_frame_collect_all_with_hits()
under sharc_harness.FRAME_PATCH_TABLE) -- two independent Runners, one per
capture, advanced one frame at a time so a diff at frame N compares state
built from exactly N+1 frames of each capture's own real TX content on top
of an otherwise identical start.

After each frame this tool cares about (see --frames/--only-first-divergence
below), it diffs:

- `state.overlay` (a dict of byte address -> value): the union of every
  address either run has ever written, un-aliased the same way
  tools/sharc_dev/sharc_inputs.py's dynamic_view() does (an address below
  sharcldr.SW_ALIAS_BASE may be stored at that raw address or at
  SW_ALIAS_BASE+address depending on which was already "present" at write
  time -- sharc_core.memory._canonical_dm_address()'s own docstring; both
  resolve to the same un-aliased address here), read back through
  sharc_trace._dm_read() (which already knows this same resolution) rather
  than compared as raw dict values, so a byte only one side ever wrote is
  still compared correctly against the other side's loader-backed default.
  Differing addresses are coalesced into contiguous ranges, and each range
  is labelled by whichever known structure (voice records, the RX frame
  copy, the command/TX ring pages, the master mix, ring A, the per-track
  mix buffers, or the guard arrays -- see REGIONS below) it falls inside,
  or "unlabeled" if it falls outside all of them.
- `state.mmrs` (a dict of MMR address -> Value): compared by rendered value
  (sharcimm.name_address() for a friendly label when one exists), not
  address presence alone.

This tool never edits sharc_core, sharc_harness.FRAME_PATCH_TABLE, or any
other lane's file: it only drives tools/sharc_dev/sharc_replay.py's existing replay
primitives with a second, independent Runner alongside the first.
"""

from __future__ import annotations

import sys


def _maybe_reexec_under_pypy() -> None:
    """Re-exec this CLI under PyPy 3.11 (tests/devtools/test_pypy.py's own `uv run
    --no-project --python pypy3.11 ... python -m pytest ...` invocation,
    with a direct script path in place of `-m pytest ARGS`), unless
    --cpython was given, this is already running under PyPy, or `uv`/PyPy
    are not available -- in which case it silently falls back to whatever
    interpreter launched it. Only sharc_memdiff's own two dependencies
    beyond the stdlib (tools/sharc_dev/sharc.py's `networkx`, and sqlite3, which is
    stdlib) are installed into the ephemeral PyPy environment."""
    if "--cpython" in sys.argv[1:]:
        return
    if sys.implementation.name == "pypy":
        return
    import os
    import shutil

    if shutil.which("uv") is None:
        return
    from tools import python_env

    cmd = [
        "uv",
        "run",
        "--no-project",
        "--python",
        "pypy3.11",
        "--with",
        "networkx",
        "python",
        "-m",
        "tools.sharc_dev.sharc_memdiff",
        *sys.argv[1:],
    ]
    try:
        os.execvpe(cmd[0], cmd, python_env())
    except OSError as exc:
        print(
            "sharc_memdiff: PyPy re-exec failed (%s), running as-is" % exc,
            file=sys.stderr,
        )


if __name__ == "__main__":
    _maybe_reexec_under_pypy()

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import (  # noqa: E402
    sharc_capture,
    sharc_dac,
)
from tools.sharc_dev import sharc_harness as h  # noqa: E402
from tools.sharc_dev import sharc_replay as replay  # noqa: E402
from tools.sharc_dev import sharc_run as sr  # noqa: E402
from tools.sharc_dev import sharc_trace as st  # noqa: E402
from tools.sharc_dev.sharcldr import SW_ALIAS_BASE  # noqa: E402

# --- Known DM structure, exactly as given by this lane's brief -------------
# Each entry is (lo, hi, label): lo <= address < hi. Built lazily per image
# (voice_records/command_word/command_record_table/ring_a all come from
# tools/sharc_dev/sharc_symbols.py's Profile, not hardcoded, so a different image's
# resolved addresses still label correctly; the ones with no Profile entry
# -- the two special-voice records, the guard arrays -- are this lane's own
# literal addresses, dt2-1.16-specific, same as the task brief gives them).
SPECIAL_VOICE_BASES = (0x252730, 0x252908)  # tools/sharc_dev/sharc_armpath.py's
# CALL_252730/CALL_252908 targets: FUN_1c2ac9's own two FUN_1c4eaf call
# sites, arming something in the render_frame master-bus/dynamics scratch
# tables (0x2524xx-0x2529xx) with the SAME +0x1b8/+0x1ba record layout a
# real voice record uses -- not a voice, but shaped like one.
RING_A_STRIDE = 0x100
GUARD_LO = 0x24F0D8
GUARD_HI = 0x24F138  # exclusive; the brief's own 0x24f137 is the last byte


def build_regions(image: str) -> list[tuple[int, int, str]]:
    """The labelled DM ranges this tool knows about for IMAGE, sorted by
    address. See the module docstring's list (voice records, special
    voices, the RX frame copy, command/TX ring pages, the master mix, ring
    A, per-track mix buffers, the guard arrays)."""
    p = h.profile(image)
    regions: list[tuple[int, int, str]] = []
    for i in range(h.VOICE_RECORD_COUNT):
        lo = p.voice_records + i * h.VOICE_RECORD_STRIDE
        regions.append((lo, lo + h.VOICE_RECORD_STRIDE, "voice%d" % i))
    for i, base in enumerate(SPECIAL_VOICE_BASES):
        regions.append((base, base + h.VOICE_RECORD_STRIDE, "special_voice%d" % i))
    regions.append(
        (replay.RX_BASE, replay.RX_BASE + replay.TX_PAYLOAD_BYTES, "rx_frame")
    )
    for i, base in enumerate((p.command_word, p.command_word + h.RING_SIZE_BYTES)):
        regions.append((base, base + h.RING_SIZE_BYTES, "command_page%d" % i))
    for i, base in enumerate(
        (p.command_record_table, p.command_record_table + h.RING_SIZE_BYTES)
    ):
        regions.append((base, base + h.RING_SIZE_BYTES, "tx_page%d" % i))
    mix_span = sharc_dac.MASTER_MIX_CHANNEL_WORDS * 4
    regions.append(
        (
            sharc_dac.MASTER_MIX_BASE,
            sharc_dac.MASTER_MIX_BASE + mix_span,
            "master_mix_L",
        )
    )
    regions.append(
        (
            sharc_dac.MASTER_MIX_BASE + mix_span,
            sharc_dac.MASTER_MIX_BASE + 2 * mix_span,
            "master_mix_R",
        )
    )
    for i, base in enumerate((p.ring_a, p.ring_a + RING_A_STRIDE)):
        regions.append((base, base + RING_A_STRIDE, "ring_a%d" % i))
    for t in range(16):
        base = h.TRACK_MIX_BASE + t * h.TRACK_MIX_STRIDE
        regions.append((base, base + h.TRACK_MIX_STRIDE, "track%d" % t))
    regions.append((GUARD_LO, GUARD_HI, "guard"))
    regions.sort()
    return regions


def _canon(addr: int) -> int:
    """Un-alias ADDR the same way tools/sharc_dev/sharc_inputs.py's dynamic_view()
    does: an address at or past SW_ALIAS_BASE is the loader's own mirror of
    a plain application DM pointer below it."""
    return addr - SW_ALIAS_BASE if addr >= SW_ALIAS_BASE else addr


def _bucket(
    addr: int, regions: list[tuple[int, int, str]]
) -> tuple[str | None, int | None]:
    for lo, hi, name in regions:
        if lo <= addr < hi:
            return name, lo
    return None, None


def _coalesce(addrs: list[int], regions: list[tuple[int, int, str]]) -> list[dict]:
    """Sorted, deduplicated ADDRS coalesced into maximal contiguous runs
    that also stay inside one _bucket() label -- a run splits wherever the
    label changes, even if the addresses themselves stay contiguous, so a
    range never straddles e.g. two voice records."""
    addrs = sorted(set(addrs))
    ranges: list[dict] = []
    i = 0
    n = len(addrs)
    while i < n:
        name0, base0 = _bucket(addrs[i], regions)
        j = i
        while j + 1 < n and addrs[j + 1] == addrs[j] + 1:
            name1, _base1 = _bucket(addrs[j + 1], regions)
            if name1 != name0:
                break
            j += 1
        ranges.append(
            {"lo": addrs[i], "hi": addrs[j] + 1, "label": name0, "base": base0}
        )
        i = j + 1
    return ranges


def _byte_or_none(state, addr: int) -> int | None:
    value = st._dm_read(state, addr, 1)
    return value.value & 0xFF if value is not None else None


def _byte(state, addr: int) -> int:
    value = _byte_or_none(state, addr)
    return 0 if value is None else value


def _format_range(r: dict, state_a, state_b, max_bytes: int = 32) -> dict:
    lo, hi = r["lo"], r["hi"]
    if r["label"] is None:
        text = "unlabeled[%#x:%#x]" % (lo, hi)
    else:
        text = "%s[%#x:%#x]" % (r["label"], lo - r["base"], hi - r["base"])
    shown_hi = min(hi, lo + max_bytes)
    a_bytes = bytes(_byte(state_a, a) for a in range(lo, shown_hi))
    b_bytes = bytes(_byte(state_b, a) for a in range(lo, shown_hi))
    return {
        "label": text,
        "lo": "%#x" % lo,
        "hi": "%#x" % hi,
        "bytes": hi - lo,
        "a": a_bytes.hex(),
        "b": b_bytes.hex(),
        "truncated": (hi - lo) > max_bytes,
    }


def _mmr_render(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, st.Const):
        return "%#x" % (value.value & 0xFFFFFFFF)
    return "unknown:%s" % getattr(value, "reason", "?")


def _mmr_label(addr: int) -> str:
    name = st.name_address(addr)
    return "%s(%#x)" % (name, addr) if name else "%#x" % addr


def diff_states(
    state_a,
    state_b,
    candidate_keys,
    regions: list[tuple[int, int, str]],
    top: int,
) -> dict:
    """One frame's own diff: state_a/state_b are the two runners' State
    objects right after that frame's own render call. CANDIDATE_KEYS is
    every (already un-aliased) address either runner has EVER written up to
    and including this frame (see _WATCH's own module note above
    _advance_one_frame() -- state.overlay itself is never scanned in full:
    a post-init overlay already holds millions of entries from run_init()'s
    own zero-fill, so a full-dict union/scan is the difference between a
    diff finishing in milliseconds and one taking tens of seconds *per
    frame*). Any address NEITHER side has ever written is guaranteed equal
    between the two runners regardless (both start from the identical
    deterministic post-init/post-setup state), so restricting the scan to
    CANDIDATE_KEYS never misses a real difference.

    Returns overlay_total_ranges/overlay_total_bytes (over EVERY differing
    range, not just the ones shown) plus overlay_ranges_top (the TOP ranges
    by byte width, at most `top`) and mmr_changes (every differing MMR, not
    capped -- MMR writes are rare enough not to need it)."""
    changed = [
        addr
        for addr in candidate_keys
        if _byte_or_none(state_a, addr) != _byte_or_none(state_b, addr)
    ]
    ranges = _coalesce(changed, regions)
    formatted = [_format_range(r, state_a, state_b) for r in ranges]
    formatted.sort(key=lambda d: d["bytes"], reverse=True)

    mmr_keys = set(state_a.mmrs) | set(state_b.mmrs)
    mmr_changes = []
    for addr in sorted(mmr_keys):
        ra = _mmr_render(state_a.mmrs.get(addr))
        rb = _mmr_render(state_b.mmrs.get(addr))
        if ra != rb:
            mmr_changes.append({"address": _mmr_label(addr), "a": ra, "b": rb})

    return {
        "overlay_total_ranges": len(formatted),
        "overlay_total_bytes": sum(f["bytes"] for f in formatted),
        "overlay_ranges_top": formatted[:top],
        "mmr_changes": mmr_changes,
    }


def _setup_runner(image: str, tone_freq: float, sample_len: int) -> sr.Runner:
    """The exact post-init setup tools/sharc_dev/sharc_replay.replay() uses: one
    hand-set-up voice (voice 0) with a --freq Hz sine, so both runners start
    from an otherwise identical state before either capture's own frames
    are delivered."""
    memory = h.load_image_memory(image)
    init = h.run_init(memory, image)
    if not init.ran:
        raise RuntimeError("run_init failed: %s" % init.error)
    runner = h.new_runner(memory, image, init=init)
    state = runner.state
    sample_base = 0x310000
    tone = [
        math.sin(2 * math.pi * (tone_freq / h.SOURCE_SAMPLE_RATE) * i)
        for i in range(sample_len)
    ]
    h._write_samples(state, sample_base, tone, "int16")
    h.setup_voice(state, image, voice=0, sample_len=sample_len, sample_base=sample_base)
    h.setup_frame_dma(state, image, ring_flag=0)
    return runner


# Every DM write in this span (twice sharcldr.SW_ALIAS_BASE, covering both
# a plain application DM pointer and the loader's own alias mirror of it --
# sharc_core.memory._canonical_dm_address()'s own docstring) is logged, at
# no real cost to a normal frame call (measured: a real 95k-instruction
# render frame runs in 1.73s with no watchpoint attached and 1.88s with
# this one, ~9% -- one Watchpoint is a cheap per-access range check, unlike
# scanning the resulting overlay dict, which is what actually needs
# avoiding here; see diff_states()'s own docstring).
WATCH_SPAN = (0, 2 * SW_ALIAS_BASE)


def _advance_one_frame(
    runner: sr.Runner, image: str, tx: bytes, touched: set[int]
) -> tuple[sr.Runner, dict]:
    """Deliver TX and run one frame's own render call, updating TOUCHED (in
    place) with every byte address this frame's own execution wrote,
    canonicalized (_canon()) the same way every other address in this
    module is. TOUCHED is the caller's own running set across the whole
    replay so far -- see diff_states()'s docstring for why this, not
    state.overlay itself, is what a diff scans."""
    watchpoint = sr.Watchpoint(
        *WATCH_SPAN, on_read=False, on_write=True, stop=False, label="memdiff"
    )
    h.write_dma_transfer(runner.state, image, tx)
    runner = h.drive_dma_completion(runner, image)
    runner, result, _hits = replay.call_frame_collect_all_with_hits(
        runner,
        image,
        trace_pcs=frozenset(),
        patch_table=h.FRAME_PATCH_TABLE,
        watchpoints=[watchpoint],
    )
    for event in runner.watch_log:
        base = _canon(event.address)
        touched.update(range(base, base + event.width))
    terminal = result.terminal
    return runner, {
        "category": terminal.category,
        "pc": "%#x" % terminal.pc,
        "instructions": result.instructions,
    }


def memdiff(
    image: str,
    capture_a_path: str,
    capture_b_path: str,
    *,
    frame_lo: int = 0,
    frame_hi: int | None = None,
    only_first_divergence: bool = False,
    tone_freq: float = 1000.0,
    sample_len: int = 4096,
    top: int = 20,
) -> dict:
    """Replay CAPTURE_A_PATH and CAPTURE_B_PATH in lockstep (see the module
    docstring) and diff the two runners' DM state after every frame in
    [frame_lo, frame_hi] (inclusive; frame_hi=None means "the rest of
    whichever capture is shorter"). Both captures are executed continuously
    from frame 0 regardless of frame_lo -- only which frames' diffs are
    computed and kept is windowed, not which frames run. If
    only_first_divergence, execution stops (and only that one frame's own
    diff is returned) as soon as a frame in the window has any overlay or
    MMR difference."""
    cap_a = sharc_capture.load(capture_a_path)
    cap_b = sharc_capture.load(capture_b_path)
    n_avail = min(len(cap_a.dspi2_frames), len(cap_b.dspi2_frames))
    n_exec = n_avail if frame_hi is None else min(n_avail, frame_hi + 1)

    regions = build_regions(image)
    runner_a = _setup_runner(image, tone_freq, sample_len)
    runner_b = _setup_runner(image, tone_freq, sample_len)
    touched_a: set[int] = set()
    touched_b: set[int] = set()

    per_frame = []
    first_divergence = None
    frames_executed = 0
    for idx in range(n_exec):
        runner_a, terminal_a = _advance_one_frame(
            runner_a, image, cap_a.dspi2_frames[idx].tx, touched_a
        )
        runner_b, terminal_b = _advance_one_frame(
            runner_b, image, cap_b.dspi2_frames[idx].tx, touched_b
        )
        frames_executed = idx + 1

        if idx < frame_lo:
            continue

        diff = diff_states(
            runner_a.state, runner_b.state, touched_a | touched_b, regions, top
        )
        has_diff = bool(diff["overlay_total_ranges"]) or bool(diff["mmr_changes"])
        entry = {
            "frame": idx,
            "terminal_a": terminal_a,
            "terminal_b": terminal_b,
            "has_diff": has_diff,
            **diff,
        }
        per_frame.append(entry)
        if has_diff and first_divergence is None:
            first_divergence = idx
        if only_first_divergence and has_diff:
            break

    return {
        "image": image,
        "capture_a": capture_a_path,
        "capture_b": capture_b_path,
        "frame_window": [frame_lo, frame_hi],
        "frames_executed": frames_executed,
        "first_divergence_frame": first_divergence,
        "per_frame": per_frame,
    }


def _print_summary(result: dict) -> None:
    print(
        "%s vs %s: %d frame(s) executed, window=%s, first_divergence_frame=%s"
        % (
            result["capture_a"],
            result["capture_b"],
            result["frames_executed"],
            result["frame_window"],
            result["first_divergence_frame"],
        )
    )
    target = result["first_divergence_frame"]
    if target is None:
        print("  no divergence found in the reported window")
        return
    entry = next(f for f in result["per_frame"] if f["frame"] == target)
    print(
        "  frame %d: %d changed range(s), %d byte(s) total, %d mmr change(s)"
        % (
            entry["frame"],
            entry["overlay_total_ranges"],
            entry["overlay_total_bytes"],
            len(entry["mmr_changes"]),
        )
    )
    for r in entry["overlay_ranges_top"]:
        print(
            "    %-28s %5d B  a=%s b=%s%s"
            % (
                r["label"],
                r["bytes"],
                r["a"],
                r["b"],
                " (truncated)" if r["truncated"] else "",
            )
        )
    for m in entry["mmr_changes"][:20]:
        print("    mmr %s: %s -> %s" % (m["address"], m["a"], m["b"]))


def _parse_frames(spec: str | None) -> tuple[int, int | None]:
    if not spec:
        return 0, None
    a_str, _, b_str = spec.partition(":")
    return int(a_str), int(b_str)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("image")
    p.add_argument("capture_a")
    p.add_argument("capture_b")
    p.add_argument(
        "--frames",
        metavar="A:B",
        help="only report frames A..B (inclusive); both captures are still "
        "executed continuously from frame 0 through B",
    )
    p.add_argument(
        "--only-first-divergence",
        action="store_true",
        help="stop as soon as a reported frame has any difference, and "
        "report only that one frame",
    )
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--top", type=int, default=20, help="top N changed ranges to keep per frame"
    )
    p.add_argument("--freq", type=float, default=1000.0)
    p.add_argument("--sample-len", type=int, default=4096)
    p.add_argument("--report", help="write the full report as JSON")
    p.add_argument(
        "--cpython",
        action="store_true",
        help="skip the PyPy re-exec and run under the interpreter that launched this",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    frame_lo, frame_hi = _parse_frames(args.frames)
    start = time.perf_counter()
    result = memdiff(
        args.image,
        args.capture_a,
        args.capture_b,
        frame_lo=frame_lo,
        frame_hi=frame_hi,
        only_first_divergence=args.only_first_divergence,
        tone_freq=args.freq,
        sample_len=args.sample_len,
        top=args.top,
    )
    elapsed = time.perf_counter() - start
    result["elapsed_seconds"] = elapsed
    result["seconds_per_frame"] = (
        elapsed / result["frames_executed"] if result["frames_executed"] else None
    )
    if args.report:
        with open(args.report, "w") as fh:
            json.dump(result, fh, indent=1)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        _print_summary(result)
        print(
            "  %d frame(s), %.3fs total, %.3fs/frame"
            % (
                result["frames_executed"],
                elapsed,
                result["seconds_per_frame"] or 0.0,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
