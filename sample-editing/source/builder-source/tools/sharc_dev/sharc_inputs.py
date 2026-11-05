#!/usr/bin/env python3
"""Input sorter for a SHARC+ root: tools/sharc_dev/sharc_contract.py answers "what does
ROOT's reach set read but never write itself"; this labels each such address
with where it plausibly comes from, so a lane chasing a stop does not have
to re-derive the same four questions by hand for every address in a
contract's read set.

Static labels, checked in this order (the first that applies wins):

    (a) init      -- FUN_1c15e3 (docs/findings/06) wrote it: present in a
                      real post-init Runner's own write overlay (see
                      run_init_state()). Reported with whether the written
                      value is zero or not.
    (b) coldfire   -- the address falls inside one of the four DMA output
                      ring's own two half-buffers (docs/findings/06, "Audio
                      output buffers" -- RING_BUFFERS below cites it
                      directly). Core writes rings A/C itself and only
                      reads B/D (fed from the ColdFire link -- the same
                      finding's "[C][V] Core code writes rings A and C and
                      only reads rings B and D"); a read landing on A/C
                      here is still labelled from this same table rather
                      than falling through to "no writer found". The same
                      bucket also covers the ColdFire's 0x802-byte DSPI2 TX
                      frame itself, mapped into SHARC DM at 0x2558dc
                      (docs/findings/06, "The ColdFire frame is mapped into
                      SHARC DM at 0x2558dc" -- FRAME_REGION below): no SHARC
                      code writes this range, so every address in it that a
                      root reads but never writes is a ColdFire input, not
                      an unresolved internal one. 52 of the 142 addresses
                      this labeller used to report as `no_writer` for root
                      0x1c2b24 land in this range, including the per-track
                      gain/mixer mirror 0x255fb6-0x2560d0 FRAME_PATCH_TABLE
                      (tools/sharc_dev/sharc_harness/) already had to hand-patch
                      around (0x2560b8, "a per-track mixer/gain parameter
                      mirror this lane's synthetic frame never populated").
    (c) sharc_root -- some OTHER SHARC+ function (outside ROOT's own reach
                      set) writes it: tools/sharc_dev/sharc_contract.py's own
                      classify_external_writers(), named by function and
                      root classification (boot/init, audio task, RPC
                      task, ISR, ...).
    (d) no_writer  -- no writer this pass can find anywhere in the image.
                      Confirmed as cheaply as tools/coldfire/refscan.py confirms a
                      ColdFire "no caller" claim on the raw image (CLAUDE.md's
                      Ghidra rule) -- but the SHARC-side equivalent of that
                      raw scan is already sitting in the program database:
                      tools/sharc_dev/sharcdb/'s own exhaustive per-instruction
                      decode already built img.refs() (every literal
                      reference to a value, anywhere) and img.writers() /
                      img.readers() (resolved AND base-only/unresolved-index
                      hits), so this checks those rather than re-scanning
                      the blob's bytes by hand.

Usage:

    uv run python -m tools.sharc_dev.sharc_inputs dt2-1.16 0x1c2b24
    uv run python -m tools.sharc_dev.sharc_inputs dt2-1.16 0x1c74cd --json out.json

    # Cross-check against a real frame run's own read log (see
    # dynamic_view()'s docstring) instead of only the static labels:
    uv run python -m tools.sharc_dev.sharc_inputs dt2-1.16 0x1c2b24 --dynamic

    # Same cross-check, but replaying a real capture's own frames through
    # the real per-frame command dispatch (see dynamic_view_capture()'s
    # docstring) instead of one bare command-3 frame:
    uv run python -m tools.sharc_dev.sharc_inputs dt2-1.16 0x1c2b24 \
        --capture out/captures/dt2-1.16-play-pretracks.dt2cap --capture-frames 3

    from tools.sharc_dev import sharc, sharc_inputs
    img = sharc.load("dt2-1.16")
    report = sharc_inputs.build(img, 0x1c2b24)
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field

_here = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc  # noqa: E402
from tools.sharc_dev import sharc_contract as C  # noqa: E402
from tools.sharc_dev import sharc_run as sr  # noqa: E402
from tools.sharc_dev import sharc_survey as sv  # noqa: E402
from tools.sharc_dev import sharc_trace as st  # noqa: E402
from tools.sharc_dev.sharcldr import SW_ALIAS_BASE  # noqa: E402

# --- (b) ColdFire-supplied ring buffers --------------------------------------
#
# docs/findings/06-sharc-engine-and-startup.md, "Audio output buffers
# (OBSERVATION)": four DMA descriptor rings, each two half-buffers (a
# ping-pong pair), sizes and addresses **[V]**; direction (core writes A/C,
# only reads B/D) **[C][V]**. Each half-buffer's own end is the next half's
# start (256 B apart for A/B, 2048 B apart for C/D) -- this table names both
# halves of all four rings, not just the two direction-confirmed input ones
# (B/D), so a stray read landing on the *output* rings (A/C) is still
# labelled "coldfire" (the DMA ring region) rather than falling through to
# "no writer found".
RING_FINDING = 'docs/findings/06 ("Audio output buffers")'
RING_BUFFERS: tuple[tuple[str, int, int], ...] = (
    ("ring A buf0", 0x261CC8, 0x261CC8 + 0x100),
    ("ring A buf1", 0x261DC8, 0x261DC8 + 0x100),
    ("ring B buf0", 0x261EC8, 0x261EC8 + 0x100),
    ("ring B buf1", 0x261FC8, 0x261FC8 + 0x100),
    ("ring C buf0", 0x262138, 0x262138 + 0x800),
    ("ring C buf1", 0x262938, 0x262938 + 0x800),
    ("ring D buf0", 0x263138, 0x263138 + 0x800),
    ("ring D buf1", 0x263938, 0x263938 + 0x800),
)

# docs/findings/06-sharc-engine-and-startup.md, "The ColdFire frame is
# mapped into SHARC DM at 0x2558dc": the ColdFire's own 0x802-byte DSPI2 TX
# frame (docs/findings/04's per-track TX frame map), laid out at SHARC DM
# addresses `0x2558dc + <ColdFire TX byte offset>` -- eleven independent
# per-track scalar literals (FUN_1c24e9/FUN_001c2b24) match this base
# exactly, and tools/sharc_dev/sharc_framemap.py's own frame-poke run (this lane, see
# its --json output) exercises reads inside it during a real render_frame
# call. This is the whole frame, not just the fields a static literal scan
# already resolved: no SHARC code is known to write into this range, so
# every address inside it is a ColdFire input, the same way a RING_BUFFERS
# half is.
FRAME_FINDING = (
    'docs/findings/06 ("The ColdFire frame is mapped into SHARC DM at 0x2558dc")'
)
FRAME_REGION: tuple[str, int, int] = ("coldfire TX frame", 0x2558DC, 0x2558DC + 0x802)

_LABEL_ORDER = {"init": 0, "coldfire": 1, "sharc_root": 2, "no_writer": 3}


def ring_label(addr: int) -> str | None:
    """The RING_BUFFERS half-buffer name covering ADDR, or None. Does not
    cover FRAME_REGION -- see frame_label() for that -- so this keeps its
    original, narrower meaning for any other caller/test."""
    for name, lo, hi in RING_BUFFERS:
        if lo <= addr < hi:
            return name
    return None


def frame_label(addr: int) -> str | None:
    """'coldfire TX frame +0xNN'-style text for ADDR if it falls inside
    FRAME_REGION (the ColdFire's own 0x802-byte DSPI2 TX frame, mapped into
    SHARC DM at 0x2558dc), or None."""
    name, lo, hi = FRAME_REGION
    if lo <= addr < hi:
        return "%s +0x%x" % (name, addr - lo)
    return None


def coldfire_region(addr: int) -> tuple[str, str] | None:
    """(name, finding) for whichever of RING_BUFFERS/FRAME_REGION covers
    ADDR, or None -- the single lookup classify_address() uses for label
    (b)."""
    ring = ring_label(addr)
    if ring is not None:
        return ring, RING_FINDING
    frame = frame_label(addr)
    if frame is not None:
        return frame, FRAME_FINDING
    return None


def run_init_state(image: str):
    """A real post-init State (tools/sharc_dev/sharc_harness.run_init(), lazily
    imported -- only a caller that wants label (a) checked against a live
    overlay needs the harness at all) to check ADDR's own write-overlay
    membership against. Raises RuntimeError if init does not reach its
    return (see run_init()'s own docstring)."""
    from tools.sharc_dev import (
        sharc_harness as h,  # lazy: only this path needs the harness
    )

    memory = sr._load_image_memory(image)
    init = h.run_init(memory, image)
    if not init.ran or init.runner is None:
        raise RuntimeError("run_init did not complete: %s" % init.error)
    return init.runner.state


def init_write_info(state, addr: int, width: int = 4) -> tuple[bool, int | None]:
    """(written, value) for ADDR against STATE's own write overlay: True
    only when every byte in [addr, addr+width) is a byte this concrete run
    itself stored (sharc_core.memory._dm_read()'s own overlay-membership
    check, replicated here rather than inferred from a read -- a read
    alone cannot tell "init wrote 0" from "nothing here ever wrote
    anything and this reads as 0 anyway", which is exactly the distinction
    label (a) needs)."""
    canonical = sr._canonical_dm_address(state, addr, width)
    if canonical is None:
        return False, None
    if not all((canonical + i) in state.overlay for i in range(width)):
        return False, None
    value = st._dm_read(state, addr, width)
    return True, (value.value if isinstance(value, st.Const) else None)


@dataclass
class InputLabel:
    """One address from sharc_contract's "reads but does not write" set,
    labelled (a)/(b)/(c)/(d) -- see the module docstring for what each
    means and the order they are tried in."""

    address: int
    label: str
    detail: str
    extra: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        out = {
            "address": "0x%x" % self.address,
            "label": self.label,
            "detail": self.detail,
        }
        out.update(self.extra)
        return out


def classify_address(
    img: sharc.Image,
    root_index: C._RootIndex,
    reach_set: set[int],
    addr: int,
    *,
    init_state=None,
) -> InputLabel:
    """One InputLabel for ADDR, trying (a)/(b)/(c)/(d) in that order (see
    the module docstring)."""
    if init_state is not None:
        written, value = init_write_info(init_state, addr)
        if written:
            nz = "nonzero" if value else "zero"
            return InputLabel(
                addr,
                "init",
                "written by run_init (%s, value=%#x)" % (nz, value or 0),
                extra={"nonzero": bool(value)},
            )

    coldfire = coldfire_region(addr)
    if coldfire is not None:
        name, finding = coldfire
        return InputLabel(addr, "coldfire", "%s (%s)" % (name, finding))

    writers = C.classify_external_writers(img, root_index, addr, addr + 4, reach_set)
    if writers:
        names = "; ".join(
            "%s%s [%s]"
            % (w["function"], (" " + w["name"]) if w["name"] else "", w["class"])
            for w in writers
        )
        return InputLabel(addr, "sharc_root", names, extra={"writers": writers})

    kind, block_idx = C.block_kind_for(img, addr)
    fallback = C._fallback_class(img, addr)
    refs = img.refs(addr)
    nearby = img.writers(addr)
    detail = fallback
    if refs or nearby:
        detail += "; %d literal ref(s), %d nearby writer hit(s) (see extra)" % (
            len(refs),
            len(nearby),
        )
    return InputLabel(
        addr,
        "no_writer",
        detail,
        extra={"block_kind": kind, "refs": refs, "nearby_writers": nearby},
    )


def _is_plausible(addr: int) -> bool:
    """Same test tools/sharc_dev/sharc_contract.py's own build() applies before
    trusting an unlabelled bucket (see its _PLAUSIBLE_DM_BANDS docstring):
    a known-structure address (sharc.py's _KNOWN_STRIDED/_KNOWN_RANGES) is
    always trusted regardless of where it falls; anything else needs to
    land in one of the plausible DM bands. Filtered out here rather than
    labelled -- a `ptr`-pass false positive (a float bit pattern or loop
    count that happened to sit in an I register, resolved as if it were an
    address) is not "no writer found", it is not an address at all."""
    return C._label_and_span(addr)[0] is not None or C._is_plausible(addr)


@dataclass
class InputsReport:
    image: str
    root: int
    n_inputs: int
    labels: list[InputLabel]
    init_checked: bool
    low_confidence: list[int]

    @property
    def counts(self) -> dict[str, int]:
        out = {name: 0 for name in _LABEL_ORDER}
        for lbl in self.labels:
            out[lbl.label] += 1
        return out

    def to_json(self) -> dict:
        return {
            "image": self.image,
            "root": "0x%x" % self.root,
            "n_inputs": self.n_inputs,
            "init_checked": self.init_checked,
            "counts": self.counts,
            "labels": [lbl.to_json() for lbl in self.labels],
            "low_confidence": ["0x%x" % a for a in self.low_confidence],
        }


def build(img: sharc.Image, root: int, *, init_state=None) -> InputsReport:
    """Every address ROOT's own reach set reads but never writes itself
    (sharc_contract.read_write_addresses()), each labelled by
    classify_address(), sorted by label ((a) before (b) before (c) before
    (d), address ascending within a label -- "a checklist table sorted by
    label"). An address outside every plausible DM band (see
    _is_plausible()) is reported separately, in low_confidence, instead of
    being labelled at all. INIT_STATE, if given (run_init_state()'s
    return), is checked for label (a); without it every address falls
    through straight to (b)/(c)/(d) and INIT_CHECKED is False in the
    result, so a caller can tell "no address was init-written" from "label
    (a) was never checked"."""
    reach = C.reach_functions(img, root)
    read_addrs, write_addrs = C.read_write_addresses(img, root)
    candidates = sorted(read_addrs - write_addrs)
    inputs = [a for a in candidates if _is_plausible(a)]
    low_confidence = [a for a in candidates if not _is_plausible(a)]
    root_index = C._RootIndex(img)
    labels = [
        classify_address(img, root_index, reach, addr, init_state=init_state)
        for addr in inputs
    ]
    labels.sort(key=lambda lbl: (_LABEL_ORDER[lbl.label], lbl.address))
    return InputsReport(
        image=img.name,
        root=root,
        n_inputs=len(inputs),
        labels=labels,
        init_checked=init_state is not None,
        low_confidence=low_confidence,
    )


# --- dynamic view -------------------------------------------------------------
#
# The static labels above are a purely static answer (sharc_contract's own
# `ptr` constant-propagation pass); this instead runs the real firmware path
# once (setup_voice() + setup_frame() + fresh_call(block_handler), the same
# call tools/sharc_dev/sharc_harness.call_frame() makes) with a non-stopping Watchpoint
# over the whole SHARC-visible workspace, and reports every address actually
# read that this same run never wrote and that run_init() did not write
# either -- a live cross-check for the static "no writer found" bucket that a
# missed indexed store (an untracked DAG modifier -- see sharc_contract's own
# module docstring on `ptr`'s blind spots) could otherwise hide.

# tools/sharc_dev/sharc_contract.py's own first _PLAUSIBLE_DM_BANDS entry: the
# SHARC-visible workspace/table/ring region every address this module deals
# with falls in.
DYNAMIC_DM_RANGE = (0x00200000, 0x00300000)


@dataclass
class DynamicView:
    instructions: int
    halt: dict
    n_read_addrs: int
    n_written_addrs: int
    unexplained_reads: list[int]

    def to_json(self) -> dict:
        return {
            "instructions": self.instructions,
            "halt": self.halt,
            "n_read_addrs": self.n_read_addrs,
            "n_written_addrs": self.n_written_addrs,
            "unexplained_reads": ["0x%x" % a for a in self.unexplained_reads],
        }


def dynamic_view(
    image: str,
    *,
    patch_table: sv.PatchTable | None = None,
    max_steps: int = 4_000_000,
    dm_range: tuple[int, int] = DYNAMIC_DM_RANGE,
    voice: int = 0,
) -> DynamicView:
    """Run one real frame (command 3, docs/findings/06's "Task loop") from a
    fresh run_init(), with one voice set up (tools/sharc_dev/sharc_harness.setup_voice())
    so render_frame finds at least one active voice, and a log-only
    Watchpoint over DM_RANGE attached to the fresh_call(block_handler)
    Runner. Returns every byte address read during that run that the SAME
    run never wrote and that run_init() did not write either -- see the
    module note above for why this is worth checking beyond the static
    labels."""
    from tools.sharc_dev import (
        sharc_harness as h,  # lazy: only this path needs the harness
    )

    memory = sr._load_image_memory(image)
    init = h.run_init(memory, image)
    if not init.ran or init.runner is None:
        raise RuntimeError("run_init did not complete: %s" % init.error)
    init_overlay = set(init.runner.state.overlay)

    h.setup_voice(init.runner.state, image, voice, sample_len=4096)
    block_handler = h.setup_frame(init.runner.state, image)

    runner = init.runner.fresh_call(block_handler, diagnose_unknown=True)
    watchpoint = sr.Watchpoint(
        dm_range[0],
        dm_range[1],
        on_read=True,
        on_write=True,
        stop=False,
        label="dynamic-view",
    )
    runner.attach_watchpoints([watchpoint])

    halt = sv.run_with_patches(runner, patch_table or {}, max_steps)

    read_addrs: set[int] = set()
    written_addrs: set[int] = set()
    for event in runner.watch_log:
        if event.access == "read":
            read_addrs.add(event.address)  # note_read() logs one byte at a time
        else:
            written_addrs.update(range(event.address, event.address + event.width))

    # Canonical addresses in this DM range are the loader's SW_ALIAS_BASE-
    # relative alias (see sharc_core.memory._canonical_dm_address()'s own
    # docstring); un-aliased back to the plain application DM pointer
    # (e.g. 0x241cc, not 0x2824... ) to match every other address in this
    # report (sharc_contract's own `ptr.address` column is never aliased).
    unexplained = sorted(
        (a - SW_ALIAS_BASE if a >= SW_ALIAS_BASE else a)
        for a in (read_addrs - written_addrs - init_overlay)
    )
    return DynamicView(
        instructions=runner.instructions,
        halt=halt.to_json(),
        n_read_addrs=len(read_addrs),
        n_written_addrs=len(written_addrs),
        unexplained_reads=unexplained,
    )


def dynamic_view_capture(
    image: str,
    capture_path: str,
    *,
    n_frames: int | None = None,
    patch_table: sv.PatchTable | None = None,
    max_steps: int = 4_000_000,
    dm_range: tuple[int, int] = DYNAMIC_DM_RANGE,
    voice: int = 0,
    sample_len: int = 4096,
) -> DynamicView:
    """Like `dynamic_view()`, but drives N frames of a real
    unicorn-reference-final:tools/sharc_capture_run.py capture through the real per-frame command
    dispatch (`tools/sharc_dev/sharc_replay.py`'s own `RX_BASE`/`frame_command()`
    convention -- lane C2, 2026-09-25) on one continuous Runner, instead of
    one bare default-command-3 frame with no captured frame content at all.

    A caller asking "how many no-writer inputs remain after a replay of the
    capture" wants THIS, not `dynamic_view()`: `build()`'s own static
    `no_writer` count never changes from any runtime replay (it is a purely
    static classification of the image), but a real multi-frame,
    real-command replay can retire entries from it by showing a concrete
    writer `build()`'s static `ptr`-pass missed (an untracked indexed
    store -- see that pass's own module docstring on its blind spots), the
    same live cross-check `dynamic_view()` already does for one bare frame.

    Every byte this replay writes -- `run_init()`'s own overlay, each
    frame's `RX_BASE`-mapped captured bytes (docs/findings/06's "The
    ColdFire frame is mapped into SHARC DM at 0x2558dc" -- already a
    ColdFire input, not a "no writer" address, in `build()`'s own
    `frame_label()` bucket), and `command_word`/`command_word_shift_src` --
    is folded into "explained" up front, so a genuinely no-writer read is
    never miscounted just because this tool, not SHARC code, supplied the
    byte.
    """
    from tools.sharc_dev import sharc_capture
    from tools.sharc_dev import (
        sharc_harness as h,  # lazy: only this path needs the harness
    )
    from tools.sharc_dev import (
        sharc_replay as rp,  # lazy: only this path needs the capture format
    )

    memory = sr._load_image_memory(image)
    init = h.run_init(memory, image)
    if not init.ran or init.runner is None:
        raise RuntimeError("run_init did not complete: %s" % init.error)
    explained = set(init.runner.state.overlay)

    h.setup_voice(init.runner.state, image, voice, sample_len=sample_len)
    p = h.profile(image)

    cap = sharc_capture.load(capture_path)
    frames = cap.dspi2_frames if n_frames is None else cap.dspi2_frames[:n_frames]

    frame_patch_table = patch_table if patch_table is not None else h.FRAME_PATCH_TABLE

    runner = init.runner
    total_instructions = 0
    read_addrs: set[int] = set()
    written_addrs: set[int] = set()
    halt_json: dict = {}
    for frame in frames:
        rp._write_bytes(runner.state, rp.RX_BASE, frame.tx)
        h._poke(runner.state, p.command_word_shift_src, 0)
        cmd = rp.frame_command(frame.tx)
        h._poke(runner.state, p.command_word, cmd)
        # Fold in whatever canonical (possibly SW_ALIAS_BASE-aliased --
        # see sharc_core.memory._canonical_dm_address()) keys these pokes
        # actually landed on, read back off the overlay itself rather than
        # hand-computed from RX_BASE/command_word: a raw, unaliased range
        # would silently under-count against watch_log's own canonical
        # addresses if this region needs the low-memory alias, exactly the
        # mistake `init_overlay` above avoids by reading `state.overlay`.
        explained.update(runner.state.overlay)

        watchpoint = sr.Watchpoint(
            dm_range[0],
            dm_range[1],
            on_read=True,
            on_write=True,
            stop=False,
            label="dynamic-view-capture",
        )
        # sharc_harness.call_frame_collect_all() (this lane's own
        # tools/sharc_dev/sharc_replay.py uses it too, the same way): continues
        # through every resolvable fork with FRAME_PATCH_TABLE and the
        # documented not-taken default instead of `run_with_patches()`'s
        # own "stop at the first unpatched fork" (which `dynamic_view()`'s
        # own bare-frame call also does, but a bare bare-command-3 frame
        # never reaches a real per-track fork the way a real capture's
        # frame 1 does).
        call_runner, result = h.call_frame_collect_all(
            runner,
            image,
            patch_table=frame_patch_table,
            max_steps=max_steps,
            watchpoints=[watchpoint],
        )
        halt_json = result.terminal.to_json()
        for event in call_runner.watch_log:
            if event.access == "read":
                read_addrs.add(event.address)
            else:
                written_addrs.update(range(event.address, event.address + event.width))
        total_instructions += call_runner.instructions
        runner = call_runner

    unexplained = sorted(
        (a - SW_ALIAS_BASE if a >= SW_ALIAS_BASE else a)
        for a in (read_addrs - written_addrs - explained)
    )
    return DynamicView(
        instructions=total_instructions,
        halt=halt_json,
        n_read_addrs=len(read_addrs),
        n_written_addrs=len(written_addrs),
        unexplained_reads=unexplained,
    )


# --- CLI ----------------------------------------------------------------------


def _print_text(report: InputsReport) -> None:
    print(
        "%s root=0x%x: %d input address(es) (%s)"
        % (
            report.image,
            report.root,
            report.n_inputs,
            ", ".join("%s=%d" % (k, v) for k, v in report.counts.items()),
        )
    )
    if not report.init_checked:
        print("(label 'init' not checked: no --run-init post-init state given)")
    last_label = None
    for lbl in report.labels:
        if lbl.label != last_label:
            print("\n-- %s --" % lbl.label)
            last_label = lbl.label
        print("  0x%-8x %s" % (lbl.address, lbl.detail))
    if report.low_confidence:
        print(
            "\n%d low-confidence address(es) outside any plausible DM band (likely "
            "`ptr`-pass false positives; see sharc_inputs._is_plausible()): %s"
            % (
                len(report.low_confidence),
                ", ".join("0x%x" % a for a in report.low_confidence[:20]),
            )
        )


def _print_dynamic(view: DynamicView) -> None:
    print(
        "dynamic view: %d instructions, halt=%s"
        % (view.instructions, view.halt.get("reason"))
    )
    print(
        "  %d distinct byte(s) read, %d distinct byte(s) written"
        % (view.n_read_addrs, view.n_written_addrs)
    )
    print(
        "  %d read address(es) this run neither wrote nor found in the init overlay:"
        % len(view.unexplained_reads)
    )
    for addr in view.unexplained_reads[:60]:
        print("    0x%x" % addr)
    if len(view.unexplained_reads) > 60:
        print("    ... +%d more" % (len(view.unexplained_reads) - 60))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("image")
    p.add_argument(
        "root", help="hex or decimal short-word address of the root function"
    )
    p.add_argument(
        "--no-run-init",
        dest="run_init",
        action="store_false",
        default=True,
        help="skip checking label (a) against a live run_init() overlay",
    )
    p.add_argument(
        "--dynamic",
        action="store_true",
        help="also run the real frame call once and report the dynamic view",
    )
    p.add_argument(
        "--capture",
        help="cross-check against a real unicorn-reference-final:tools/sharc_capture_run.py capture "
        "replayed through the real per-frame command dispatch "
        "(dynamic_view_capture()) instead of one bare command-3 frame",
    )
    p.add_argument(
        "--capture-frames",
        type=int,
        default=None,
        help="--capture only: replay at most this many frames (default: all)",
    )
    p.add_argument("--max-steps", type=int, default=4_000_000)
    p.add_argument(
        "--json", help="write the full report (+dynamic view, if asked) as JSON here"
    )
    args = p.parse_args(argv)

    img = sharc.load(args.image)
    root = int(args.root, 0)

    init_state = run_init_state(args.image) if args.run_init else None
    report = build(img, root, init_state=init_state)
    _print_text(report)

    payload = report.to_json()
    if args.dynamic:
        print()
        view = dynamic_view(args.image, max_steps=args.max_steps)
        _print_dynamic(view)
        payload["dynamic_view"] = view.to_json()
    if args.capture:
        print()
        capture_view = dynamic_view_capture(
            args.image,
            args.capture,
            n_frames=args.capture_frames,
            max_steps=args.max_steps,
        )
        _print_dynamic(capture_view)
        payload["dynamic_view_capture"] = capture_view.to_json()

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print("\nwrote %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
