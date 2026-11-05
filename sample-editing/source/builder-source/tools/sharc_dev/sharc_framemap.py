#!/usr/bin/env python3
"""Map ColdFire DSPI2 TX frame byte offsets to SHARC reader PCs and (for a
small, deliberately fingerprinted subset) destination addresses, from one
real render_frame run.

    uv run python -m tools.sharc_dev.sharc_framemap dt2-1.16 \
        [--capture out/captures/dt2-1.16-idle.dt2cap] [--frame-index N] \
        [--no-fingerprint] [--max-steps N] [--json OUT.json]

**Where the frame lands (confirmed by execution, not just cited).**
docs/findings/06-sharc-engine-and-startup.md, "The ColdFire frame is mapped
into SHARC DM at 0x2558dc" already establishes FRAME_BASE below from static
literals alone (eleven independent per-track scalar bases -- FUN_1c24e9's
own literal plus FUN_001c2b24's four -- match `0x2558dc + <ColdFire TX byte
offset>` exactly, with no exceptions). This tool adds the execution half:
it pokes a captured TX frame at FRAME_BASE, byte for byte (the same
convention tools/sharc_dev/sharc_replay.py's own ``_write_bytes`` already uses for its
two now-superseded RX-buffer candidates, `CANDIDATE_RX_BUFFERS`
-- 0x261bac/0x261aa4, docs/findings/04's own still-open guess, which this
finding's literal match already supersedes), then runs the real
block_handler -> command_dispatch_fn -> cmd_handler_3 -> render_frame call
chain (tools/sharc_dev/sharc_harness/'s setup_frame()/FRAME_PATCH_TABLE -- the same
chain tools/sharc_dev/sharc_replay.py drives) with a non-stopping
tools/sharc_dev/sharc_run.py Watchpoint logging every read/write across the SHARC
workspace band tools/sharc_dev/sharc_inputs.py's own DYNAMIC_DM_RANGE already uses
(0x200000-0x300000). Every read this run logs inside [FRAME_BASE,
FRAME_BASE+FRAME_LEN) is direct execution evidence that the byte at that
offset really is read, and by which pc -- independent of whether its value
can be traced any further.

**Following a loaded value to its store.** A true forward taint would need
sharc_core's concrete integers to carry a tag; short of that, this tool uses
the standard "poison value" trick: FINGERPRINT_FIELDS names the eleven
per-track scalar bases docs/findings/06's own literal match already
resolved with confidence (0x02, 0x34, 0x54, 0x74, 0x94, 0xb4, 0x73c, 0x75c,
0x77c, 0x79c, 0x7bc -- NOT the 0x60-stride parameter page at frame offset
0xda.. FUN_1c24e9 also reads, since that page can carry sample-length/loop
bounds a large synthetic value could turn into a runaway loop). Each of the
11 x 16 (field, track) slots gets a distinct small marker
(`marker_value()`, always < 0x2000, so no sign-extension/zero-extension
changes it at any access width); every OTHER frame byte keeps the real
captured value (mostly zero on an idle capture), so the parameter page and
everything this tool has not vetted as safe stays exactly as a real capture
left it. A write elsewhere in the watched band whose own logged value
equals one field's marker is reported as a candidate destination for that
field -- a same-run value correlation, `[D]`-strength, not a `[V]` proof:
a shift/mask/sign-flip between load and store breaks the match (several are
already documented -- docs/findings/06's "ASHIFT R2 by -8" -- and reported
as reads with no destination rather than guessed at), and an unrelated
write that happens to carry the same small integer would false-positive.
Matches are grouped against the known destination structures this run's own
address falls in: the 32 voice records (`0x2412cc + k*0x1d8`,
tools/sharc_dev/sharc_harness.{VOICE_RECORD_STRIDE,VOICE_RECORD_COUNT}), the four DMA output rings
(tools/sharc_dev/sharc_inputs.RING_BUFFERS), or a bare workspace address.

Setting the machine-type field (offset 0x94) to a nonzero marker
deliberately forces the NOT-EQUAL side of FUN_001c2b24's own change-test at
0x1c33df (docs/findings/04's "The SHARC reads and change-tests the 0x94+2i
machine word": an idle/zero frame always compares EQUAL against the
zero-initialized cache) -- this run is not a faithful idle replay for that
one field; pass ``--no-fingerprint`` for a plain, unmodified-value run (the
same read-log confirmation, without exercising that branch or attempting
any destination correlation).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc_capture  # noqa: E402
from tools.sharc_dev import sharc_harness as h  # noqa: E402
from tools.sharc_dev import sharc_inputs as si  # noqa: E402
from tools.sharc_dev import sharc_run as sr  # noqa: E402
from tools.sharc_dev import sharc_survey as sv  # noqa: E402
from tools.sharc_dev.sharcldr import SW_ALIAS_BASE  # noqa: E402

# docs/findings/06, "The ColdFire frame is mapped into SHARC DM at 0x2558dc".
FRAME_BASE = 0x2558DC
FRAME_LEN = 0x802  # docs/findings/04: the ColdFire's own TX payload length.

# tools/sharc_dev/sharc_inputs.DYNAMIC_DM_RANGE: the same SHARC-visible workspace band
# sharc_inputs.py's own dynamic_view() already watches.
WORKSPACE_RANGE = si.DYNAMIC_DM_RANGE

# docs/findings/06's eleven independent per-track scalar bases (the ones the
# literal match resolved with no exceptions) -- see the module docstring for
# why the 0x60-stride parameter page (frame offset 0xda..) is deliberately
# excluded from fingerprinting.
FINGERPRINT_FIELDS: tuple[int, ...] = (
    0x02,
    0x34,
    0x54,
    0x74,
    0x94,
    0xB4,
    0x73C,
    0x75C,
    0x77C,
    0x79C,
    0x7BC,
)
TRACK_COUNT = 16

# Known destination structures a match's address is checked against, in
# this order -- see tools/sharc_dev/sharc_harness/ (voice records) and
# tools/sharc_dev/sharc_inputs.py (rings).
VOICE_RECORDS_BASE = 0x2412CC
VOICE_RECORD_STRIDE = h.VOICE_RECORD_STRIDE
VOICE_RECORD_COUNT = h.VOICE_RECORD_COUNT
# tools/sharc_dev/sharc_harness.FRAME_PATCH_TABLE's own hand-patched
# "per-track mixer/gain parameter mirror" (0x2560b8) sits inside this span --
# named here as a structure, not re-derived: this tool did not identify its
# exact bounds, it reuses the ones already recorded (0x255fb6-0x2560d0).
GAIN_TABLE_RANGE = (0x255FB6, 0x2560D0)


def _unalias(addr: int) -> int:
    """The plain application DM pointer for ADDR, undoing the loader's
    SW_ALIAS_BASE-relative mirror if ADDR is one (sharc_core.memory.
    _canonical_dm_address()'s own docstring: "Application code uses
    unaliased DM pointers ... whereas the boot stream is keyed at
    SW_ALIAS_BASE + <address>" -- a fresh WRITE to a not-yet-present low
    address always lands at the alias, so a Watchpoint's own WatchEvent.
    address is aliased for anything this run itself first *wrote* rather
    than inherited from the loader, e.g. this tool's own frame pokes;
    tools/sharc_dev/sharc_inputs.py's dynamic_view() un-aliases its own read log the
    same way)."""
    return addr - SW_ALIAS_BASE if addr >= SW_ALIAS_BASE else addr


def marker_value(field_index: int, track: int) -> int:
    """A small, distinct value for (field_index into FINGERPRINT_FIELDS,
    track), always < 0x2000 so no width's sign/zero extension changes it."""
    return 0x1000 + field_index * 0x40 + track


def _region_label(addr: int) -> str:
    ring = si.ring_label(addr)
    if ring is not None:
        return "ring:%s" % ring
    if GAIN_TABLE_RANGE[0] <= addr < GAIN_TABLE_RANGE[1]:
        return "gain/mixer table +%#x" % (addr - GAIN_TABLE_RANGE[0])
    if (
        VOICE_RECORDS_BASE
        <= addr
        < VOICE_RECORDS_BASE + VOICE_RECORD_COUNT * VOICE_RECORD_STRIDE
    ):
        off = addr - VOICE_RECORDS_BASE
        k, rem = divmod(off, VOICE_RECORD_STRIDE)
        return "voice record %d +%#x" % (k, rem)
    if FRAME_BASE <= addr < FRAME_BASE + FRAME_LEN:
        return "frame itself +%#x" % (addr - FRAME_BASE)
    return "workspace %#x" % addr


def _load_frame_bytes(capture_path: str | None, frame_index: int) -> bytearray:
    if capture_path is None:
        return bytearray(FRAME_LEN)
    cap = sharc_capture.load(capture_path)
    if not cap.dspi2_frames:
        raise ValueError("%s: no DSPI2 frames captured" % capture_path)
    if frame_index >= len(cap.dspi2_frames):
        raise ValueError(
            "%s: only %d frame(s), asked for index %d"
            % (capture_path, len(cap.dspi2_frames), frame_index)
        )
    tx = bytearray(cap.dspi2_frames[frame_index].tx)
    if len(tx) < FRAME_LEN:
        tx.extend(bytes(FRAME_LEN - len(tx)))
    return tx[:FRAME_LEN]


def _apply_fingerprint(frame: bytearray) -> dict[int, tuple[int, int]]:
    """Overwrite FRAME's FINGERPRINT_FIELDS x TRACK_COUNT slots with
    marker_value()s (big-endian, matching the ColdFire's own per-track TX
    layout -- 04-coldfire-dsp-link.md: "each field is a big-endian word at
    offset + 2i"). Returns {marker_value: (field_offset, track)}."""
    markers: dict[int, tuple[int, int]] = {}
    for field_index, field_offset in enumerate(FINGERPRINT_FIELDS):
        for track in range(TRACK_COUNT):
            offset = field_offset + 2 * track
            if offset + 2 > FRAME_LEN:
                continue
            value = marker_value(field_index, track)
            frame[offset] = (value >> 8) & 0xFF
            frame[offset + 1] = value & 0xFF
            markers[value] = (field_offset, track)
    return markers


def _natural_markers(frame: bytes | bytearray) -> dict[int, tuple[int, int]]:
    """Like _apply_fingerprint(), but reads back whatever value a REAL
    capture already carries at the eleven known scalar bases instead of
    overwriting it -- for a run that wants destination correlation without
    touching a real frame's own content (e.g. unicorn-reference-final:tools/sharc_capture_run.py's
    ``--poke-track-type``, which already puts a genuine, firmware-copied
    nonzero value at frame offset 0x94). Only a NONZERO field is registered
    (zero would match nearly every write in the run and drown any real
    signal), and a value shared by more than one field is dropped rather
    than guessed at -- both mean this is `[D]`-strength at best, weaker
    than a fingerprint's own guaranteed-distinct markers, since small real
    values like 1 or 2 are exactly the kind of number ordinary unrelated
    code also writes."""
    seen: dict[int, tuple[int, int]] = {}
    ambiguous: set[int] = set()
    for field_offset in FINGERPRINT_FIELDS:
        for track in range(TRACK_COUNT):
            offset = field_offset + 2 * track
            if offset + 2 > len(frame):
                continue
            value = (frame[offset] << 8) | frame[offset + 1]
            if value == 0:
                continue
            if value in seen and seen[value] != (field_offset, track):
                ambiguous.add(value)
                continue
            seen[value] = (field_offset, track)
    for value in ambiguous:
        seen.pop(value, None)
    return seen


def _write_frame(state, frame: bytes | bytearray) -> None:
    for i, byte in enumerate(frame):
        h._poke(state, FRAME_BASE + i, byte, width=1)


def run(
    image: str,
    *,
    capture: str | None,
    frame_index: int = 0,
    fingerprint: bool = True,
    natural_markers: bool = False,
    max_steps: int = 4_000_000,
) -> dict:
    frame = _load_frame_bytes(capture, frame_index)
    confidence = "d"
    if fingerprint:
        markers = _apply_fingerprint(frame)
    elif natural_markers:
        markers = _natural_markers(frame)
        confidence = "low (real small-integer value, not a distinct marker)"
    else:
        markers = {}

    memory = h.load_image_memory(image)
    init = h.run_init(memory, image)
    if not init.ran:
        return {"error": "run_init failed: %s" % init.error}

    runner = h.new_runner(memory, image, init=init)
    h.setup_voice(runner.state, image, voice=0, sample_len=4096)
    block_handler = h.setup_frame(runner.state, image, command=3, ring_flag=0)
    _write_frame(runner.state, frame)

    new_runner = runner.fresh_call(block_handler, diagnose_unknown=True)
    watchpoint = sr.Watchpoint(
        WORKSPACE_RANGE[0],
        WORKSPACE_RANGE[1],
        on_read=True,
        on_write=True,
        stop=False,
        label="framemap",
    )
    new_runner.attach_watchpoints([watchpoint])
    halt = sv.run_with_patches(new_runner, h.FRAME_PATCH_TABLE, max_steps)

    reads_by_offset: dict[int, set[int]] = {}
    writes: list[tuple[int, int, int]] = []  # (writer_pc, address, new_value)
    for event in new_runner.watch_log:
        address = _unalias(event.address)
        if event.access == "read":
            if FRAME_BASE <= address < FRAME_BASE + FRAME_LEN:
                offset = address - FRAME_BASE
                reads_by_offset.setdefault(offset, set()).add(event.pc_sw)
        elif event.new_value is not None:
            writes.append((event.pc_sw, address, event.new_value))

    destinations: dict[int, list[dict]] = {}
    for writer_pc, address, value in writes:
        hit = markers.get(value)
        if hit is None:
            continue
        field_offset, track = hit
        offset = field_offset + 2 * track
        destinations.setdefault(offset, []).append(
            {
                "writer_pc": "%#x" % writer_pc,
                "address": "%#x" % address,
                "region": _region_label(address),
                "marker": "%#x" % value,
                "confidence": confidence,
            }
        )

    fields = []
    for offset in sorted(reads_by_offset):
        fields.append(
            {
                "frame_offset": "%#x" % offset,
                "reader_pcs": ["%#x" % pc for pc in sorted(reads_by_offset[offset])],
                "destinations": destinations.get(offset, []),
            }
        )
    # Offsets fingerprinted but never observed as read, for completeness.
    for _value, (field_offset, track) in markers.items():
        offset = field_offset + 2 * track
        if offset not in reads_by_offset and offset not in {
            int(str(f["frame_offset"]), 16) for f in fields
        }:
            fields.append(
                {
                    "frame_offset": "%#x" % offset,
                    "reader_pcs": [],
                    "destinations": destinations.get(offset, []),
                }
            )
    fields.sort(key=lambda f: int(str(f["frame_offset"]), 16))

    n_with_destination = sum(1 for f in fields if f["destinations"])
    return {
        "image": image,
        "capture": capture,
        "frame_index": frame_index,
        "fingerprint": fingerprint,
        "frame_base": "%#x" % FRAME_BASE,
        "frame_len": FRAME_LEN,
        "workspace_range": ["%#x" % WORKSPACE_RANGE[0], "%#x" % WORKSPACE_RANGE[1]],
        "instructions": new_runner.instructions,
        "halt": {"reason": halt.reason, "pc": "%#x" % halt.pc_sw, "form": halt.form},
        "n_offsets_read": len(reads_by_offset),
        "n_offsets_with_destination": n_with_destination,
        "fields": fields,
    }


def _print_text(result: dict) -> None:
    if "error" in result:
        print("error: %s" % result["error"])
        return
    print(
        "%s: %d instructions, halt=%s at %s"
        % (
            result["image"],
            result["instructions"],
            result["halt"]["reason"],
            result["halt"]["pc"],
        )
    )
    print(
        "%d frame offset(s) read, %d with a fingerprint-matched destination"
        % (result["n_offsets_read"], result["n_offsets_with_destination"])
    )
    for f in result["fields"]:
        if not f["reader_pcs"] and not f["destinations"]:
            continue
        print(
            "  frame+%s  read by %s"
            % (f["frame_offset"], ", ".join(f["reader_pcs"]) or "(never read)")
        )
        for d in f["destinations"]:
            print(
                "      -> %s (%s) written by %s"
                % (d["address"], d["region"], d["writer_pc"])
            )


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("image")
    p.add_argument("--capture", default=None)
    p.add_argument("--frame-index", type=int, default=0)
    p.add_argument("--no-fingerprint", dest="fingerprint", action="store_false")
    p.add_argument(
        "--natural-markers",
        action="store_true",
        help="with --no-fingerprint, correlate destinations from the "
        "capture's own real nonzero values instead (see _natural_markers())",
    )
    p.add_argument("--max-steps", type=int, default=4_000_000)
    p.add_argument("--json", help="write the full report as JSON here")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    result = run(
        args.image,
        capture=args.capture,
        frame_index=args.frame_index,
        fingerprint=args.fingerprint,
        natural_markers=args.natural_markers,
        max_steps=args.max_steps,
    )
    _print_text(result)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1)
        print("\nwrote %s" % args.json)
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
