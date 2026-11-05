"""Replay a unicorn-reference-final:tools/sharc_capture_run.py capture into the real SHARC audio
task, from a post-init state.

    uv run python -m tools.sharc_dev.sharc_replay dt2-1.16 CAPTURE.dt2cap \
        [--frames N] [--out OUT.wav] [--report OUT.json] [--freq HZ] \
        [--sample-len N] [--force-command N]

For each captured DSPI2 frame, in capture order:

1. **Report the frame's own content**, at the per-track offsets
   docs/findings/04-coldfire-dsp-link.md's TX frame map already establishes
   from the ColdFire's own build code (`FUN_4002d438`/the vector-191
   handler): machine type at byte offset `0x94 + 2i` and the two derived
   flags at `0x73c + 2i` / `0x75c + 2i`, for tracks 0-15. This does not
   depend on where -- or whether -- the payload lands on the SHARC side
   (see point 2): it is read straight from the captured bytes, the same way
   `docs/findings/04`'s own `unicorn-reference-final:tools/sharcframe.py` experiment read them.

2. **Delivers the transfer at its real hardware address and lets the
   firmware do the rest (lane G1, 2026-09-26)**, instead of poking a
   confirmed-by-execution but firmware-internal buffer directly. Lane G1
   traced the SPI-slave receive DMA's own descriptor-list ring
   (`tools/sharc_dev/sharc_harness/`'s own module note above
   `dma_landing_address()`: `FUN_1c7bd4` builds it at DM `0x2641b0`/
   `0x2641cc`, ADDRSTART fields pointing at `command_word`'s own two
   ping-pong instances, `0x264220`/`0x265220` on DT2 1.16 -- the SC58x/
   2158x Hardware Reference's own Descriptor-List Mode layout,
   out/refs/adsp-2156x-hwr Table 27-10) and confirmed the SAME address from
   `render_frame`'s (`FUN_1c2b24`) own consumer side: its `M10` (saved from
   its incoming `R12` argument, `command_word + (shift<<12)`) is the SOURCE
   of its own 512-long-word (2,048-byte) copy into `RX_BASE` (`0x2558dc`,
   still the confirmed-by-execution buffer `FUN_1c2b24`'s own per-track
   decode reads -- now understood to be a WORKING COPY render_frame makes
   every frame, not the landing zone itself).

   So this tool now calls `sharc_harness.write_dma_transfer()` (writes the
   captured frame's own TX bytes -- 16-bit-unit byte-swapped, see that
   function's own docstring for why -- at whichever ring `command_word_
   shift_src` is NOT currently pointing at) and `sharc_harness.
   drive_dma_completion()` (calls `FUN_1c77b4`, the SPI service's own
   registered completion callback, with an event code that has its own
   bit 5 set -- the one bit that function's own 9 instructions act on --
   toggling the ping-pong shift onto the buffer just written, and re-arming
   `sharc_harness.COPY_GATE_ADDRESS`, a second, still-open gate lane G1
   found guarding render_frame's own companding-copy block; see that
   module's own section note for exactly what is, and is not, traced about
   it) before every frame's own `call_frame_collect_all()`. It no longer
   pokes `RX_BASE`/`command_word` directly at all -- both are now filled by
   `render_frame`'s own execution, from the transfer this tool delivers at
   the real hardware address.

3. **Drives the real audio-task call chain**, the same one
   tools/sharc_dev/sharc_harness/'s `render_frames()`/`call_frame()` already proved
   reaches render_frame (block_handler -> command_dispatch_fn ->
   cmd_handler_3 -> render_frame -- docs/findings/06's "Frame call path"),
   with `FRAME_PATCH_TABLE` (that module's own hypotheses for the stops an
   almost-empty synthetic frame hits) via
   `sharc_harness.call_frame_collect_all_with_hits()` -- every stop the
   frame call passes is recorded (`per_frame[i]["stops"]`), not only the
   first, so a replay's own report is a full list of what each frame
   depended on; a breakpoint at `0x1c60a2` (`FUN_1c60a2`, the machine-type
   change detector docs/findings/06's Lane E2 found unreached from a
   synthetic frame) is installed on every frame, so `per_frame[i][
   "fun_1c60a2_hits"]` answers, with a real hit count rather than an
   inferred one, whether a real, correctly-delivered transfer ever reaches
   it.

   **The command word itself is no longer a separate poke.** It comes from
   whatever the delivered transfer's own header (TX byte offset 0,
   docs/findings/04's "Frame content: a header written at +0x00") resolves
   to once `render_frame`/`command_dispatch_fn` read it back -- `cmd` in
   this tool's own per-frame report is `frame_command(frame.tx)` (or
   `--force-command N`'s override, now applied to the TRANSFERRED bytes'
   own header before delivery, not to `command_word` after the fact) purely
   for describing what was sent, not a separate write.

   ONE voice is set up via `sharc_harness.setup_voice()`, with a `--freq`
   Hz sine (default 1000 Hz, at `sharc_harness.SOURCE_SAMPLE_RATE`) written
   to its sample buffer -- **not** from the captured frame, because the
   SHARC-side code path that would turn a received DSPI2 frame into an
   active voice record (a written record word +0 / ACTIVE) is still not
   found even with the transfer delivered correctly this time (see this
   lane's own report). This tool's own `voice_activated_by_firmware` is
   therefore still always `False` for voice 0 (the one it sets up by
   hand) -- it names the missing link instead of quietly working around it
   and calling that "firmware-driven". `scan_voice_active` reports whether
   the firmware itself ever marked any *other* voice record ACTIVE
   (`+0x1b8`) while this replay ran, independent of the one this tool sets
   up by hand.

4. **Collects ring A** (DAC output, `0x261cc8 + (flag<<8)`,
   docs/findings/06's "Rings": already Q31, L/R-interleaved, at the final
   output rate -- unlike the single-voice work buffer, this is NOT run
   through `sharc_harness.decimate()`) after each call, and writes an
   L+R-averaged mono WAV from it. **The SHARC's TX reply** -- an actual
   outgoing DSPI2/SPI packet -- is not collected, because no code path that
   builds one is known in either image yet (docs/findings/04: "What happens
   to 2748 after the call is not known... No write to an SPI or DMA
   register has been found yet"); this tool does not invent one.

Stops are reported exactly as `tools/sharc_dev/sharc_survey.py`'s `CollectStop`s give
them (category, pc, form, unknowns), via `FRAME_PATCH_TABLE` the same way
`sharc_harness.render_frames()` applies it. This tool never adds a new
patch-table hypothesis of its own -- one belongs in `FRAME_PATCH_TABLE`
(tools/sharc_dev/sharc_harness/, this lane's own file), not here.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Callable

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc_capture  # noqa: E402
from tools.sharc_dev import sharc_harness as h  # noqa: E402
from tools.sharc_dev import sharc_lp0 as lp0  # noqa: E402
from tools.sharc_dev import sharc_run as sr  # noqa: E402
from tools.sharc_dev import sharc_survey as sv  # noqa: E402
from tools.sharc_dev import sharc_trace as st  # noqa: E402

# docs/findings/06's "The ColdFire frame is mapped into SHARC DM at
# 0x2558dc": the confirmed (by execution) whole-frame mapped range
# [0x2558dc, 0x2560de], 0x802 bytes. **[C] Lane G1 (2026-09-26): this is
# render_frame's (FUN_1c2b24) own WORKING COPY of the real transfer, made by
# a 2,048-byte copy from the real hardware landing zone
# (sharc_harness.dma_landing_address()) every frame it runs -- not
# something this tool writes to any more (see the module docstring, point
# 2, and sharc_harness's own section note above dma_landing_address()).
# Kept here only for describe_frame()'s own per-track byte offsets below,
# which are relative to this same base either way.
RX_BASE = 0x2558DC

# tools/sharc_dev/dspiframe.py's TX_PAYLOAD_BYTES["dt2"]: the real-payload length the
# ColdFire driver copies out of its own TX source buffer before padding the
# rest of the FRAME_BYTES=0xabc (2,748) byte wire frame with tag-only PUSHR
# entries (docs/findings/04-coldfire-dsp-link.md, "TX length ... are not two
# independently sized directions"). Used only by describe_frame() below to
# find a captured frame's own tail (past the real payload) -- lane G1
# delivers the whole transfer (up to sharc_harness.RING_SIZE_BYTES) at the
# real hardware address, not just this prefix.
TX_PAYLOAD_BYTES = 0x802

# docs/findings/04's per-track TX frame map: offsets into the ColdFire's own
# 0x802-byte payload, which is also this tool's captured `tx` bytes (see
# unicorn-reference-final:tools/sharc_capture_run.py: the driver-call hook captures the argument
# length the firmware itself passes, 0x802, not the padded 2748-byte wire
# frame unicorn-reference-final:emu/dspi2.py's eDMA model would see).
MACHINE_TYPE_OFFSET = 0x94
FLAG_A_OFFSET = 0x73C
FLAG_B_OFFSET = 0x75C
TRACK_COUNT = 16

# --frame-override's own per-track base: docs/findings/04-coldfire-dsp-link.md
# ("The mirror index to TX frame map") documents "the four parameter pages in
# a per-track 0x60-byte block at frame byte 0xda + track*0x60" as the
# ColdFire's own per-track parameter area -- the best-documented candidate
# location for a per-track "source field" in the TX payload, even though
# docs/findings/06 ("No read of the frame's per-track parameter block found
# yet") records that no SHARC-side reader of it has been found. TRACK:OFFSET
# in --frame-override addresses a byte inside THIS block (OFFSET=0 is the
# block's own first byte), not an arbitrary raw TX offset -- see
# apply_frame_overrides()'s own docstring for why this, and not some other
# stride, was chosen, and for what this lane's own O1 report found when it
# used this to carry the built-in sample's pointer/length for track 2.
PER_TRACK_PARAM_BLOCK_OFFSET = 0xDA
PER_TRACK_PARAM_BLOCK_STRIDE = 0x60

# docs/findings/06's "Rings": ring A holds 32 L/R-interleaved Q31 sample
# pairs (64 words) at the final output rate.
RING_A_WORDS = 64

VOICE_ACTIVATED_REASON = (
    "No SHARC-side code path from a received DSPI2 frame to a voice "
    "record's own word+0/ACTIVE fields is established yet "
    "(docs/findings/06 'Init writes': 'the firmware writer of word +0 for "
    "a playing voice is not yet found'; docs/findings/04's two candidate "
    "receive buffers are not aliased to the frame reader's runtime I5: "
    "'No static path establishes either frame value'). This replay drives "
    "the render path with a hand-set-up voice "
    "(tools/sharc_dev/sharc_harness.setup_voice), not one the firmware itself "
    "activated from the captured frame."
)


def _u16be(data: bytes, offset: int) -> int | None:
    if offset + 2 > len(data):
        return None
    return (data[offset] << 8) | data[offset + 1]


# The ColdFire's own frame-build code writes a header word at TX byte
# offset 0 (docs/findings/04, "Frame content: a header written at +0x00").
# Every `.dt2cap` capture on hand (idle, idle15M, note-track1,
# machine2-track0, play-pretracks) has this word as 1 on the capture's
# first frame and 3 on every frame after it -- exactly docs/findings/06's
# own command semantics for "clears + stores 0" and "renders". See the
# module docstring's point 4 for what is, and is not, established about
# this being the SAME word `command_dispatch_fn` (0x1c778a) reads at
# `command_word` (0x264220 + (DM(command_word_shift_src=0x261ca4)<<12)).
COMMAND_HEADER_OFFSET = 0x0


def frame_command(tx: bytes) -> int:
    """The command this captured frame's own header asks for -- the u16be
    at `COMMAND_HEADER_OFFSET` -- for driving `replay()`'s per-frame
    dispatch instead of a hardcoded 3. Falls back to 3 (render) only if TX
    is implausibly short to hold a header at all; every real capture has
    one."""
    value = _u16be(tx, COMMAND_HEADER_OFFSET)
    return value if value is not None else 3


def describe_frame(tx: bytes) -> dict:
    """-> per-track machine type / derived-flag words this captured frame
    carries, from the ColdFire's own documented TX layout -- independent of
    where, or whether, it lands on the SHARC side. Also reports the
    transfer's tail (past `TX_PAYLOAD_BYTES`, see that constant's own
    docstring): a capture made before this lane's `unicorn-reference-final:tools/sharc_capture_run.py`
    fix only ever has `tx_len == TX_PAYLOAD_BYTES` (an empty tail, `None`
    fields below), not a claim the tail is absent on real hardware."""
    tracks = [
        {
            "track": i,
            "machine_type": _u16be(tx, MACHINE_TYPE_OFFSET + 2 * i),
            "flag_a": _u16be(tx, FLAG_A_OFFSET + 2 * i),
            "flag_b": _u16be(tx, FLAG_B_OFFSET + 2 * i),
        }
        for i in range(TRACK_COUNT)
    ]
    tail = tx[TX_PAYLOAD_BYTES:]
    return {
        "tx_len": len(tx),
        "tracks": tracks,
        "has_tail": len(tail) > 0,
        "tail_len": len(tail),
        "tail_nonzero": any(tail) if tail else None,
    }


# The TX frame fields the trigger/sample-load path uses (scratchpad report
# sharc-trig-arm.md). ColdFire writer: vector_191_handler (0x4002dd0c),
# which builds the frame at 0x80005348; SHARC reader: FUN_1c24e9 (per track
# and voice, from render_frame's working copy at RX_BASE) and FUN_1c2b24.
# All are big-endian halfwords of the TX payload.
TRIG_MASK_OFFSET = 0x22  # bit t: track t triggered this frame (note on)
RELEASE_MASK_OFFSET = 0x24  # bit t: track t released (note off)
MASK_26_OFFSET = 0x26  # bit t: event flag 0x200 set on track t [O] meaning
MASK_28_OFFSET = 0x28  # bit t: release of the 0x200 event [O] meaning
# +2t: 0x800047fc[t] >> 8, the pitch from FUN_4013a1d0 (note << 8 plus a
# fraction: 0x3c00 = note 60 in running-play frame 131). SHARC: sw 0x1c2554
# reads it, 0x1c255e stores it per voice at 0x25226c + 2v.
PITCH_OFFSET = 0x02
# +2t: 0x800047dc[t] = the trig's velocity byte << 8 (100 = the default
# velocity). SHARC: sw 0x1c2546, then a curve lookup; the gain lands at
# 0x25242c + 4v (sw 0x1c25cd).
VELOCITY_OFFSET = 0x34
# Sample slot: int16 at 0xda + t*0x60 + 6, the track row's halfword 0x2d
# (the row block 0x54..0x67 is copied to 0xda + t*0x60). The SHARC reads it
# at sw 0x1c3338 (landing ring + 0xda + track*0x60, (swse) +3 halfwords).
SLOT_OFFSET_IN_BLOCK = 6


def _s16be(data: bytes, offset: int) -> int | None:
    value = _u16be(data, offset)
    if value is None:
        return None
    return value - 0x10000 if value & 0x8000 else value


def frame_fields(tx: bytes) -> dict:
    """-> the header masks and per-track fields of one TX frame that the
    SHARC trigger and sample-load path reads: trig/release masks (0x22,
    0x24, 0x26, 0x28) and, per track, machine type, sample slot,
    pitch, velocity, flag_a and whether the track's trig/release bit is set.
    Read from the captured bytes; no SHARC state involved."""
    trig = _u16be(tx, TRIG_MASK_OFFSET) or 0
    release = _u16be(tx, RELEASE_MASK_OFFSET) or 0
    tracks = []
    for t in range(TRACK_COUNT):
        velocity = _u16be(tx, VELOCITY_OFFSET + 2 * t)
        tracks.append(
            {
                "track": t,
                "trig": bool(trig >> t & 1),
                "release": bool(release >> t & 1),
                "machine_type": _u16be(tx, MACHINE_TYPE_OFFSET + 2 * t),
                "slot": _s16be(
                    tx,
                    PER_TRACK_PARAM_BLOCK_OFFSET
                    + t * PER_TRACK_PARAM_BLOCK_STRIDE
                    + SLOT_OFFSET_IN_BLOCK,
                ),
                "pitch": _u16be(tx, PITCH_OFFSET + 2 * t),
                "velocity": None if velocity is None else velocity >> 8,
                "flag_a": _u16be(tx, FLAG_A_OFFSET + 2 * t),
            }
        )
    return {
        "command": frame_command(tx),
        "trig_mask": trig,
        "release_mask": release,
        "mask_26": _u16be(tx, MASK_26_OFFSET),
        "mask_28": _u16be(tx, MASK_28_OFFSET),
        "tracks": tracks,
    }


def summarize_frame_fields(fields: dict) -> dict:
    """FRAME_FIELDS (frame_fields()) with only the tracks worth printing:
    those with a trig or release bit, a machine, or a nonzero slot."""
    keep = [
        t
        for t in fields["tracks"]
        if t["trig"] or t["release"] or t["machine_type"] or t["slot"]
    ]
    return {**fields, "tracks": keep}


def capture_fields(capture_path: str, *, only_events: bool = False) -> list[dict]:
    """frame_fields() for every DSPI2 frame of CAPTURE_PATH, summarized
    (summarize_frame_fields()), each with its "frame" index. ONLY_EVENTS
    keeps just the frames whose trig or release mask is nonzero."""
    cap = sharc_capture.load(capture_path)
    out = []
    for idx, frame in enumerate(cap.dspi2_frames):
        fields = frame_fields(frame.tx)
        if only_events and not (fields["trig_mask"] or fields["release_mask"]):
            continue
        out.append({"frame": idx, **summarize_frame_fields(fields)})
    return out


# render_frame's per-voice trigger path (FUN_1c2b24 voice loop, R15 = voice)
# and the calls it makes; replay_armed_voice() logs each hit as an
# "arm_events" entry. See the scratchpad report sharc-trig-arm.md.
ARM_PATH_PCS = {
    0x1C33BC: "trig",  # TX hw 0x22 bit set: trig-pending byte 0x24f0ac + v
    0x1C3329: "load",  # sample-load block: slot -> FUN_1c3f78 -> FUN_1c7442
    0x1C6056: "release",  # FUN_1c6056(R8 = voice): TX hw 0x24 bit (1 frame late)
    0x1C4E70: "set_sample",  # FUN_1c4e70(R4 = record, R8 = ptr, R12 = rate)
    0x1C4EAF: "arm",  # FUN_1c4eaf(R4 = record): ACTIVE / seed pending
}


def _reg_int(state, name: str) -> int | None:
    value = state.uregs.get(st.UREG_CODES[name])
    return value.value & 0xFFFFFFFF if isinstance(value, st.Const) else None


def _make_arm_event_hook(
    image: str, events: list[dict], frame_cell: list[int]
) -> Callable[[sr.Runner], None]:
    """post_step_hook: append one ARM_PATH_PCS event per hit, with the
    voice (R15 in the voice loop, R8 for FUN_1c6056, the record in R4 for
    FUN_1c4e70/FUN_1c4eaf; None for a record outside the 32 voices, such
    as FUN_1c2ac9's preview records)."""
    base = h.profile(image).voice_records

    def record_voice(address: int | None) -> int | None:
        if address is None:
            return None
        index, rem = divmod(address - base, h.VOICE_RECORD_STRIDE)
        return index if rem == 0 and 0 <= index < h.VOICE_RECORD_COUNT else None

    def hook(runner: sr.Runner) -> None:
        state = runner.state
        kind = ARM_PATH_PCS.get(state.pc_sw)
        if kind is None:
            return
        event: dict = {"frame": frame_cell[0], "event": kind}
        if kind in ("trig", "load"):
            event["voice"] = _reg_int(state, "R15")
        elif kind == "release":
            event["voice"] = _reg_int(state, "R8")
        else:
            event["voice"] = record_voice(_reg_int(state, "R4"))
        if kind == "set_sample":
            event["ptr"] = _hex_or_none(_reg_int(state, "R8"))
            event["rate"] = _reg_int(state, "R12")
        events.append(event)

    return hook


def _make_voice_output_hook(
    image: str,
    voices: tuple[int, ...],
    out: dict[int, dict[int, list[float]]],
    frame_cell: list[int],
) -> Callable[[sr.Runner], None]:
    """post_step_hook: when the frame reaches MASTER_STAGE_CALL_PC (after
    every voice has rendered), read each of VOICES' own decimated work
    buffer (`sharc_harness.read_voice_work_buffer_decimated()`, read-only)
    into OUT[voice][frame]. This is each voice's own render output, before
    the (unmodelled) per-track mix; it writes nothing."""
    records = {v: h.voice_record_address(image, v) for v in voices}

    def hook(runner: sr.Runner) -> None:
        if runner.state.pc_sw != h.MASTER_STAGE_CALL_PC:
            return
        for v, record in records.items():
            out[v][frame_cell[0]] = h.read_voice_work_buffer_decimated(
                runner.state, record
            )

    return hook


def _chain_hooks(*hooks):
    live = [x for x in hooks if x is not None]
    if not live:
        return None

    def hook(runner: sr.Runner) -> None:
        for x in live:
            x(runner)

    return hook


def parse_frame_override(spec: str) -> tuple[int, int, int]:
    """Parse one `--frame-override TRACK:OFFSET=VALUE` spec into
    `(track, offset, value)` (all ints; TRACK/OFFSET/VALUE accept `0x...`
    hex or plain decimal). Raises ValueError with the original spec quoted
    on any malformed input -- `main()` lets this propagate as a usage
    error rather than silently ignoring a typo'd override."""
    try:
        track_offset, value_s = spec.split("=", 1)
        track_s, offset_s = track_offset.split(":", 1)
        track = int(track_s, 0)
        offset = int(offset_s, 0)
        value = int(value_s, 0)
    except ValueError as exc:
        raise ValueError(
            "--frame-override: expected TRACK:OFFSET=VALUE, got %r" % spec
        ) from exc
    if not (0 <= track < TRACK_COUNT):
        raise ValueError(
            "--frame-override: track %d out of range [0, %d)" % (track, TRACK_COUNT)
        )
    return track, offset, value


def apply_frame_overrides(
    tx: bytes, overrides: list[tuple[int, int, int]] | None
) -> bytes:
    """Apply every `(track, offset, value)` from `--frame-override` (see
    `parse_frame_override()`) to a COPY of `tx`, returning `tx` itself
    unchanged if `overrides` is empty/None -- the same "mutate a bytearray
    copy, only if there is something to change" convention `replay()`'s own
    `--force-command` handling above uses.

    This is a raw TX-payload poke, HAND STEP, not a firmware-native field:
    it writes `value` as 4 plain big-endian bytes (matching `_u16be()`'s own
    convention for every other documented per-track field in this module,
    widened to 4 bytes since a sample pointer or a sample-count length does
    not fit in the 2-byte fields `MACHINE_TYPE_OFFSET`/`FLAG_A_OFFSET`/
    `FLAG_B_OFFSET` use) at absolute TX offset `PER_TRACK_PARAM_BLOCK_OFFSET
    + track*PER_TRACK_PARAM_BLOCK_STRIDE + offset` -- docs/findings/04's own
    "four parameter pages in a per-track 0x60-byte block at frame byte
    0xda + track*0x60", the one already-documented per-track block wide
    enough to hold a 4-byte value without colliding with a neighbouring
    track's own slot. No SHARC-side reader of this block is established
    (docs/findings/06, "No read of the frame's per-track parameter block
    found yet"), and this lane's own O1 report records what happened when
    it used this override to carry the built-in sample's pointer/length for
    track 2 -- read that before assuming this reaches any particular voice
    field. Values are written pre-swap, into the same `tx` bytes `describe_
    frame()`/`frame_command()` read and `sharc_harness.write_dma_transfer()`
    delivers (that function's own 16-bit-unit byte swap, if any, applies to
    this override exactly as it does to every other TX byte)."""
    if not overrides:
        return tx
    out = bytearray(tx)
    for track, offset, value in overrides:
        base = (
            PER_TRACK_PARAM_BLOCK_OFFSET + track * PER_TRACK_PARAM_BLOCK_STRIDE + offset
        )
        if base + 4 > len(out):
            raise ValueError(
                "--frame-override: track=%d offset=%#x -> TX offset %#x..%#x "
                "past the end of a %d-byte frame"
                % (track, offset, base, base + 4, len(out))
            )
        out[base : base + 4] = (value & 0xFFFFFFFF).to_bytes(4, "big")
    return bytes(out)


def _write_bytes(state, base: int, data: bytes) -> None:
    """Byte-for-byte poke, unswapped -- `replay()` itself no longer uses
    this (see the module docstring, point 2: it delivers a transfer at the
    real hardware address via `sharc_harness.write_dma_transfer()` instead).
    Kept as a small utility for `tools/sharc_dev/sharc_inputs.py`'s own `dynamic_view()`,
    which still models the pre-lane-G1 direct-poke behaviour for its own,
    separate purpose (a static "no writer" audit, not a claim about where a
    real transfer lands)."""
    for i, byte in enumerate(data):
        h._poke(state, base + i, byte, width=1)


def _read_ring_a(state, image: str) -> list[float]:
    p = h.profile(image)
    ring_flag = st._dm_read(state, p.ring_flag, 4)
    flag = (ring_flag.value & 1) if ring_flag is not None else 0
    base = p.ring_a + (flag << 8)
    out = []
    for i in range(RING_A_WORDS):
        raw = st._dm_read(state, base + i * 4, 4)
        value = raw.value & 0xFFFFFFFF if raw is not None else 0
        if value & 0x80000000:
            value -= 1 << 32
        out.append(value / float(1 << 31))
    return out


def _track_buffers_nonzero(state) -> dict[int, bool]:
    """Whether each of the 16 master-stage per-track input buffers
    (`sharc_harness.TRACK_MIX_BASE + t*TRACK_MIX_STRIDE`, docs/findings/06's
    "Master stage": `0x1c207b` sums these into the master mix) holds any
    nonzero float, L or R half, after a frame call -- read the same way
    `sharc_harness.inject_track_buffer()` writes them, but never itself
    written by this tool. Per-track (not just "any"), so a caller can tell
    which of the 16 tracks the render path actually deposited output into,
    keyed by track index 0-15. A float -0.0 (0x80000000) counts as zero:
    the fixed core's mixer writes -0.0 into every idle track buffer."""
    out: dict[int, bool] = {}
    for track in range(16):
        base = h.TRACK_MIX_BASE + track * h.TRACK_MIX_STRIDE
        nonzero = False
        for i in range(2 * h.TRACK_MIX_CHANNEL_WORDS):
            raw = st._dm_read(state, base + i * 4, 4)
            if raw is not None and raw.value & 0x7FFFFFFF:
                nonzero = True
                break
        out[track] = nonzero
    return out


def _mono(ring_a_blocks: list[list[float]]) -> list[float]:
    """L/R-interleaved Q31 pairs -> mono, averaging each pair."""
    out = []
    for block in ring_a_blocks:
        for i in range(0, len(block) - 1, 2):
            out.append(0.5 * (block[i] + block[i + 1]))
    return out


# Lane C2's own item 1 targets: whether a real per-frame command dispatch
# (as opposed to a single hand-forced command=3 for every frame) ever lets
# the firmware's own execution populate these, and by which pc, instead of
# staying zero because a synthetic single-command-3 test never ran the
# commands (0-2) or repeated command-3 calls that would reach them.
#
# `MASTER_BUS_SOURCE` is the raw 26-field mixer/gain table `render_frame`'s
# own 2,048-byte copy (lane G1: from the real DMA landing zone, into
# `RX_BASE`) maps byte-for-byte from the captured TX frame (docs/findings/06,
# "The ColdFire frame is mapped into SHARC DM at 0x2558dc") -- checked
# directly against the captured bytes, not via a watchpoint, since nothing in
# the SHARC image is a
# "writer" of it (it is a ColdFire input, tools/sharc_dev/sharc_inputs.py's own
# `frame_label()` bucket). `MASTER_BUS_DECODED` is `FUN_1c2b24`'s own
# 0x1c2e00-0x1c2fb0 decode of that table into per-channel dynamics
# parameters (this branch's own "Continuity across frames, root-caused"
# note in tools/sharc_dev/sharc_harness/); `MIX_GATE` is `DM(0x252d3c)`
# (docs/findings/06's still-open mix gate, three known writers: init
# 0x1c1643, `FUN_1cb336`'s own reset at 0x1cb33a, and `FUN_1cdbb2`'s
# 0x1cdc23); `SLOT_TYPE_WATCH` covers the 32 voice records
# (`sharc_harness.VOICE_RECORD_STRIDE`-strided from 0x2412cc) at the
# `+0x1b9` byte docs/findings/06's "Where the SRC-page words go" names as
# the per-track flag `FUN_001c60a2` sets ("0x2412c8+4+t*0x1d8+0x1b9" ==
# record base 0x2412cc, `+0x1b9`).
MASTER_BUS_SOURCE = (0x255FB6, 0x2560D0)
MASTER_BUS_DECODED = (0x2524D0, 0x2526E8)
MIX_GATE = (0x252D3C, 0x252D40)
SLOT_TYPE_STRIDE = 0x1D8
SLOT_TYPE_OFFSET = 0x1B9
SLOT_TYPE_WATCH = (0x2412CC, 0x2412CC + 32 * SLOT_TYPE_STRIDE)


def _first_nonzero_write(
    events, lo: int, hi: int, *, stride: int | None = None, offset: int | None = None
) -> dict | None:
    """The first write in EVENTS (an `sr.Runner.watch_log`) landing in
    [lo, hi) -- optionally further restricted to a strided per-record field
    (`(address - lo) % stride == offset`, for `SLOT_TYPE_WATCH`) -- whose
    new value is nonzero: `{"address", "pc", "value"}` (all hex strings), or
    None if this frame's own watch_log never wrote a nonzero value there."""
    for event in events:
        if event.access != "write" or not (lo <= event.address < hi):
            continue
        if stride is not None and (event.address - lo) % stride != offset:
            continue
        if event.new_value:
            return {
                "address": "%#x" % event.address,
                "pc": "%#x" % event.pc_sw,
                "value": "%#x" % event.new_value,
            }
    return None


def call_frame_collect_all_with_hits(
    runner: sr.Runner,
    image: str,
    *,
    trace_pcs,
    patch_table: sv.PatchTable | None = None,
    watchpoints=(),
    max_steps: int = 4_000_000,
    max_hits: int = 200,
    img=None,
    sample_fn=None,
) -> tuple[sr.Runner, sv.CollectAllResult, list[dict]]:
    """Like `sharc_harness.call_frame_collect_all()`, but also answers "does
    control ever reach any of TRACE_PCS during this one frame call", with a
    real pc-hit breakpoint (`Runner.breakpoints`, checked natively inside
    `sharc_survey.run_collect_all()`'s own loop before an instruction
    decodes) rather than inferring reachability from a memory watch on some
    address a function is *believed* to write (lane E2, 2026-09-25/26 --
    see docs/findings/06's "Lane E2" section for why this matters: a
    write-target watch at a candidate offset can read as empty either
    because the function never ran, or because it ran but the watched
    address was wrong -- a pc-hit breakpoint at the function's own entry
    (or any other pc of interest) tells the two apart).

    `Runner.fresh_call()` already carries `breakpoints` forward (`new_runner
    .breakpoints = self.breakpoints`, tools/sharc_dev/sharc_run.py), so TRACE_PCS is
    installed on the fresh_call runner this makes internally -- a caller
    does not need to pre-set `runner.breakpoints` itself. A breakpoint hit
    is not fatal to the frame call: this function clears `breakpoints`
    (saving the set first), executes exactly the one instruction at that pc
    (`Runner.step()`, tools/sharc_dev/sharc_run.py's own single-instruction API,
    unaffected by whatever `sharc_survey.run_collect_all()`'s own inlined
    stepping loop does elsewhere), restores `breakpoints`, and resumes
    `run_collect_all()` -- so a frame with several hits (e.g. a function
    called once per voice/track, in a `DO 32` loop) still runs to its real
    terminal (`frame-returned`, a genuine fork, an MMR trap, ...), with
    every intermediate hit recorded rather than only the first.

    Returns `(runner, result, hits)`: `runner` is the fresh_call Runner,
    positioned at the true terminal stop (not at a hit -- hits are always
    stepped past); `result` is that terminal leg's own `CollectAllResult`
    (earlier legs' own stops, if any, are not merged in -- only the hits
    list accumulates across legs); `hits` is
    `[{"pc": "0x...", "instructions": N}, ...]`, oldest first, `N` being
    `runner.instructions` (this frame's own running count, comparable
    across hits and to `result.instructions`) at the moment of that hit,
    before the pc's own instruction executed. Stops at `max_hits` hits
    (a runaway loop should not build an unbounded list) or `max_steps`
    total instructions, whichever comes first -- either way, `result`
    still reports whatever terminal `run_collect_all()` last reached.

    `sample_fn`, if given, is called as `sample_fn(new_runner, terminal.pc)`
    right when a hit is recorded -- *before* that pc's own instruction has
    executed, so it sees exactly the register/memory state the about-to-run
    instruction (often a conditional jump) will read. Its return value
    (any JSON-able dict) is merged under the hit's own `"sample"` key.
    Never called when `sample_fn` is None (the default), which keeps every
    existing caller's hit-dict shape (and tests/sharc/integration/test_sharc_replay.py's own
    assertions on it) unchanged -- this is a pure opt-in addition, not a
    behaviour change (lane H3, 2026-09-26, tools/sharc_dev/sharc_armpath.py's own
    caller: reading raw register state at a guard jump this way, rather
    than re-deriving it from the disassembly's own displacement units,
    which sharc_core's `modify()` resolves once and correctly already).
    """
    p = h.profile(image)
    new_runner = runner.fresh_call(p.block_handler, diagnose_unknown=True)
    if watchpoints:
        new_runner.attach_watchpoints(list(watchpoints))
    trace_pcs = frozenset(trace_pcs)
    new_runner.breakpoints = frozenset(new_runner.breakpoints) | trace_pcs
    hits: list[dict] = []
    result = None
    remaining = max_steps
    while remaining > 0 and len(hits) < max_hits:
        result = sv.run_collect_all(new_runner, patch_table or {}, remaining, img=img)
        terminal = result.terminal
        if terminal.category != "breakpoint" or terminal.pc not in trace_pcs:
            break
        hit = {"pc": "%#x" % terminal.pc, "instructions": new_runner.instructions}
        if sample_fn is not None:
            hit["sample"] = sample_fn(new_runner, terminal.pc)
        hits.append(hit)
        saved = new_runner.breakpoints
        new_runner.breakpoints = frozenset()
        try:
            new_runner.step()
        except sr.Halt as exc:
            hits[-1]["step_halt"] = str(exc)
            return new_runner, result, hits
        new_runner.breakpoints = saved
        remaining = max_steps - new_runner.instructions
    return new_runner, result, hits


FUN_1C60A2 = 0x1C60A2  # docs/findings/06's Lane E2 machine-type change detector.

# docs/findings/06's Lane F2: the pointer FUN_1c60a2's own gate dereferences
# -- null (via State.explicit_memory_model) everywhere this project has run
# with an unfilled companding record. A real, correctly-delivered transfer
# (lane G1) does not fill DM(0x266220) either (see sharc_harness's own
# section note: the transfer fits inside command_word's own ring, and never
# reaches command_record_table's), so this stays a diagnostic read, not
# something this tool expects to change -- reported per frame either way.
COMPANDING_GATE_POINTER = 0x254D78


def replay(
    image: str,
    capture_path: str,
    *,
    n_frames: int | None = None,
    command: int | None = None,
    ring_flag: int = 0,
    tone_freq: float = 1000.0,
    sample_len: int = 4096,
    provisional_interpretations: dict[str, str] | None = None,
    flexbus_log: str | None = None,
) -> dict:
    """Replay CAPTURE_PATH's DSPI2 frames from a `run_init()` state, one
    voice set up with a `tone_freq` Hz sine (`sharc_harness.SOURCE_SAMPLE_RATE`
    -- matching `sharc_harness.render_frames()`'s own CLI convention) so the
    voice actually has audible input, not the all-zero (unwritten,
    `explicit_memory_model`) PCM an earlier version of this function left at
    `sample_base`.

    **Delivery (lane G1, 2026-09-26): every frame's own captured TX bytes
    are written at the real SPI-slave receive DMA's own landing zone**
    (`sharc_harness.write_dma_transfer()`) and the transfer is "completed"
    the way real hardware would (`sharc_harness.drive_dma_completion()`,
    toggling the ping-pong shift and the companding-copy gate -- see that
    module's own section note) -- not poked into `RX_BASE`/`command_word`
    directly any more. See the module docstring's point 2.

    `command` defaults to None: each frame's own command comes from
    `frame_command(frame.tx)` (that frame's own captured header) -- the
    block handler runs whatever command that frame actually asked for,
    read back from the transfer itself, not a hardcoded 3. Pass an int to
    OVERRIDE that frame's own header bytes (TX offset 0-1, big-endian)
    before delivering it, for an A/B comparison -- this still goes through
    `write_dma_transfer()`/`drive_dma_completion()`, it does not poke
    `command_word` after the fact.

    `provisional_interpretations`, if given, is threaded through
    `sharc_harness.run_init()` to every frame call afterward (`fresh_call()`
    keeps a State's own `provisional_interpretations`/`provisional_
    interpreted` -- see `tools/sharc_dev/sharc_run.py`'s `fresh_call_state()`): an
    opt-in way past an undocumented form this replay would otherwise stop
    at (e.g. `{"21p_undoc16": "nop"}`), reported back per frame in
    `provisional_hits` so a caller can tell a provisional-assisted run from
    a clean one -- label any result built with this **[D]**, not **[V]**.
    """
    cap = sharc_capture.load(capture_path)
    frames = cap.dspi2_frames if n_frames is None else cap.dspi2_frames[:n_frames]

    memory = h.load_image_memory(image)
    init = h.run_init(
        memory,
        image,
        provisional_interpretations=provisional_interpretations,
    )
    if not init.ran:
        return {
            "capture": capture_path,
            "kind": cap.kind,
            "frames_in_capture": len(cap.dspi2_frames),
            "frames_replayed": 0,
            "error": "run_init failed: %s" % init.error,
        }

    runner = h.new_runner(memory, image, init=init)
    state = runner.state
    sample_base = 0x310000
    tone = [
        math.sin(2 * math.pi * (tone_freq / h.SOURCE_SAMPLE_RATE) * i)
        for i in range(sample_len)
    ]
    h._write_samples(state, sample_base, tone, "int16")
    h.setup_voice(state, image, voice=0, sample_len=sample_len, sample_base=sample_base)
    h.setup_frame_dma(state, image, ring_flag=ring_flag)
    lp0_report = None
    if flexbus_log is not None:
        runner, lp0_report = lp0.feed(runner, image, lp0.read_log(flexbus_log))
        state = runner.state

    per_frame = []
    ring_a_blocks = []
    first_stop = None
    other_voices_active: dict[int, int] = {}
    commands_seen: dict[int, int] = {}
    fun_1c60a2_total_hits = 0
    provisional_used: set[str] = set()

    target_lo = min(MASTER_BUS_DECODED[0], MIX_GATE[0], SLOT_TYPE_WATCH[0])
    target_hi = max(MASTER_BUS_DECODED[1], MIX_GATE[1], SLOT_TYPE_WATCH[1])
    target_watch = sr.Watchpoint(
        target_lo,
        target_hi,
        on_read=False,
        on_write=True,
        stop=False,
        label="c2-targets",
    )

    for idx, frame in enumerate(frames):
        info = describe_frame(frame.tx)
        info["index"] = idx
        info["instr_count"] = frame.instr_count

        cmd = frame_command(frame.tx)
        tx = frame.tx
        if command is not None and cmd != command:
            tx = bytearray(tx)
            tx[0] = (command >> 8) & 0xFF
            tx[1] = command & 0xFF
            tx = bytes(tx)
            cmd = command
        info["command"] = cmd
        commands_seen[cmd] = commands_seen.get(cmd, 0) + 1

        landing_base = h.write_dma_transfer(state, image, tx)
        runner = h.drive_dma_completion(runner, image)
        state = runner.state
        info["landing_base"] = "%#x" % landing_base

        src_lo, src_hi = MASTER_BUS_SOURCE
        info["master_bus_source_nonzero"] = any(tx[src_lo - RX_BASE : src_hi - RX_BASE])

        runner, result, hits = call_frame_collect_all_with_hits(
            runner,
            image,
            trace_pcs={FUN_1C60A2},
            patch_table=h.FRAME_PATCH_TABLE,
            watchpoints=[target_watch],
        )
        state = runner.state
        fun_1c60a2_total_hits += len(hits)
        events = runner.watch_log
        ring_a = _read_ring_a(state, image)
        ring_a_blocks.append(ring_a)
        frame_voices_active = h.scan_voice_active(state, image, exclude=(0,))
        other_voices_active.update(frame_voices_active)
        track_buffers = _track_buffers_nonzero(state)
        master_mix = h.read_master_mix(memory, image, runner)
        companding_fields = h.companding_record_fields(state, image)
        if state.provisional_interpreted:
            provisional_used.update(state.provisional_interpreted)

        terminal = result.terminal
        info["handler"] = "block_handler -> command_dispatch_fn -> cmd_handler_%d" % cmd
        info["stops"] = ["%s@%#x" % (s.category, s.pc) for s in result.stops]
        info["stop_reason"] = terminal.category
        info["stop_pc"] = "%#x" % terminal.pc
        info["instructions"] = result.instructions
        info["fun_1c60a2_hits"] = [hit["pc"] for hit in hits]
        info["voice_active_by_firmware"] = frame_voices_active
        info["track_buffers_nonzero"] = track_buffers
        info["any_track_buffer_nonzero"] = any(track_buffers.values())
        info["master_mix_nonzero"] = any(v for v in master_mix)
        info["ring_a_nonzero"] = any(v for v in ring_a)
        info["companding_fields"] = ["%#x" % v for v in companding_fields]
        info["companding_gate_pointer_nonzero"] = bool(companding_fields[0])
        info["mix_gate_write"] = _first_nonzero_write(events, *MIX_GATE)
        info["master_bus_decoded_write"] = _first_nonzero_write(
            events, *MASTER_BUS_DECODED
        )
        info["slot_type_write"] = _first_nonzero_write(
            events,
            *SLOT_TYPE_WATCH,
            stride=SLOT_TYPE_STRIDE,
            offset=SLOT_TYPE_OFFSET,
        )
        per_frame.append(info)

        if first_stop is None and terminal.category != "frame-returned":
            first_stop = {
                "frame": idx,
                "pc": "%#x" % terminal.pc,
                "category": terminal.category,
                "form": terminal.form,
                "instructions": result.instructions,
            }

    return {
        "capture": capture_path,
        "kind": cap.kind,
        "device": cap.device,
        "source_sha256": cap.source_sha256,
        "frames_in_capture": len(cap.dspi2_frames),
        "frames_replayed": len(frames),
        "rx_base": "%#x" % RX_BASE,
        "tx_payload_bytes": "%#x" % TX_PAYLOAD_BYTES,
        "tone_freq_hz": tone_freq,
        "commands_seen": commands_seen,
        "forced_command": command,
        "voice_activated_by_firmware": False,
        "voice_activated_by_firmware_reason": VOICE_ACTIVATED_REASON,
        "other_voices_active_by_firmware": other_voices_active,
        "fun_1c60a2_total_hits": fun_1c60a2_total_hits,
        "companding_gate_pointer": "%#x" % COMPANDING_GATE_POINTER,
        "any_companding_gate_pointer_nonzero": any(
            f["companding_gate_pointer_nonzero"] for f in per_frame
        ),
        "any_signal_without_injection": any(
            f["any_track_buffer_nonzero"] or f["master_mix_nonzero"] for f in per_frame
        ),
        "provisional_interpretations": provisional_interpretations,
        "provisional_used": sorted(provisional_used),
        "per_frame": per_frame,
        "first_stop": first_stop,
        "ring_a_mono": _mono(ring_a_blocks),
        "lp0": lp0_report,
    }


# --- Lane K1 (2026-09-26): a voice record's own word+0 across a real,
# continuous replay, and a demo render of whichever voice the firmware
# itself arms ------------------------------------------------------------
#
# Lane J1 replayed `out/captures/dt2-1.16-running-trig.dt2cap` from a
# `--start-frame 300` shortcut (skipping frames 0-299 without executing
# them) and found voice 4's own FIELD_SAMPLE_PTR (word+0,
# `sharc_harness.FIELD_SAMPLE_PTR`) already null (`0x0`) from the very
# first frame that shortcut fed -- on BOTH the TRIG and the idle capture --
# even though voice 4's record reads a real SDRAM address (`0x8045a6c8`)
# right after `run_init()`. J1's own report flagged this as possibly a
# cold-start artifact of the shortcut itself, not a genuine frame-300
# event, and left it open. This section answers that with a real,
# continuous replay from frame 0 (no `start_frame` shortcut) -- see
# `replay_armed_voice()`.

# One log-only (`stop=False`), write-only 4-byte Watchpoint per voice
# record's own word+0, for every `sharc_harness.VOICE_RECORD_COUNT` voice
# record (`sharc_harness.profile(image).voice_records`,
# `sharc_harness.VOICE_RECORD_STRIDE` apart). Labelled so a WatchEvent's own
# `label` (carried through unchanged by `sharc_run._WatchTracker`, never
# re-derived from its address) identifies which voice wrote, without
# re-deriving the voice index from `(address - base) // stride` -- exact
# even if a canonicalized range ever shifted the reported address.
VOICE_WORD0_LABEL_PREFIX = "voice-word0-"
VOICE_ACTIVE_LABEL_PREFIX = "voice-active-"


def _voice_word0_watchpoints(image: str) -> list[sr.Watchpoint]:
    base = h.profile(image).voice_records
    return [
        sr.Watchpoint(
            base + i * h.VOICE_RECORD_STRIDE + h.FIELD_SAMPLE_PTR,
            base + i * h.VOICE_RECORD_STRIDE + h.FIELD_SAMPLE_PTR + 4,
            on_read=False,
            on_write=True,
            stop=False,
            label="%s%d" % (VOICE_WORD0_LABEL_PREFIX, i),
        )
        for i in range(h.VOICE_RECORD_COUNT)
    ]


def _voice_active_watchpoint(image: str, voice: int) -> sr.Watchpoint:
    """Log-only, 1-byte Watchpoint on VOICE's own FIELD_ACTIVE
    (`+0x1b8`) -- separate from the word+0 watches above so this lane can
    name the exact pc of a deactivating write (docs/findings/06's
    `0x1c5008` past-limit / `0x1c50fe` wrap candidates), not just infer a
    frame-level transition from re-reading the byte after each call."""
    record = h.voice_record_address(image, voice)
    return sr.Watchpoint(
        record + h.FIELD_ACTIVE,
        record + h.FIELD_ACTIVE + 1,
        on_read=False,
        on_write=True,
        stop=False,
        label="%s%d" % (VOICE_ACTIVE_LABEL_PREFIX, voice),
    )


def _voice_index_from_label(label: str, prefix: str) -> int | None:
    if not label.startswith(prefix):
        return None
    return int(label[len(prefix) :])


def _hex_or_none(value: int | None) -> str | None:
    return None if value is None else "%#x" % value


# --- Lane N2 (2026-09-26): a real sample through the firmware's own arm,
# not silence -----------------------------------------------------------
#
# Lane K1 (above) proved the firmware genuinely arms voice 4 from a real
# TRIG (`arm_frame=304`), but that voice's own word+0 (the raw-PCM pointer)
# was already null by then -- zeroed at frame 1 by `FUN_1c4e70`'s own
# generic per-voice "clear" utility (`0x1c4e86` for word+0, `0x1c4e8a` for
# ACTIVE -- see K1's own disassembly), which runs unconditionally, every
# frame, for every "in-service" voice slot, before that same frame's own
# arm/render dispatch. This section drives a REAL sample through that same
# real arm event instead of leaving word+0 null, by reacting to three pcs
# (two already found by K1, one this lane found by execution when the
# first two alone measured a ~15x-too-quiet render -- see
# `_make_voice_rearm_hook()`'s own docstring for that trace):
#
# 1. `VOICE_ARM_PC` (`0x1c4ebb`, inside `FUN_1c4eaf`): the firmware's own
#    real arm write (`ACTIVE=1`). The instant this fires for the voice
#    this replay is watching, the hook only flags `armed=True` -- it does
#    NOT poke the record here (see point 3: a real setup_voice() poke
#    placed here gets clobbered before the render ever sees it).
#
# 2. `REARM_CLEAR_PC` (`0x1c4e86`, word+0) and `REARM_ACTIVE_CLEAR_PC`
#    (`0x1c4e8a`, ACTIVE): K1's own "generic per-frame default-clear"
#    writes (`FUN_1c4e70`'s own two stores, called for this voice slot at
#    the *start* of every frame's dispatch, arm or not). Once armed, the
#    hook re-pokes whichever of these two fields the clear just zeroed,
#    for as many frames as the sample needs to play once through
#    (`_frames_for_sample()`) -- and then deliberately stops, letting the
#    NEXT such clear silence the voice again, so the demo ends in the same
#    natural silence a real one-shot playback would. A DIFFERENT pc
#    clearing ACTIVE while armed (docs/findings/06's own past-limit
#    `0x1c5008` or wrap `0x1c50fe`) is left alone and ends the keep-alive
#    window early -- a real end-of-sample event, not this hook's target.
#
# 3. `profile(image).voice_render`'s own entry (checked against the
#    incoming `R4` -- the record base -- since this entry point is shared
#    by all 32 voices' own per-frame dispatch): `sharc_harness.setup_voice()`
#    (reused as-is, per this project's own convention of not re-deriving a
#    voice-record poke sequence twice) is (re-)applied here, EVERY frame
#    the keep-alive window is open, because something in this capture's own
#    per-voice dispatch -- almost certainly one of docs/findings/06's own
#    "four position/step setters", fed by this capture's own stale/null
#    per-track parameter tables -- overwrites STEP/END back to a stale,
#    near-zero value every single frame, not just once after the arm (this
#    lane's own finding, by execution: see `_make_voice_rearm_hook()`'s
#    docstring for the before/after trace). FIELD_PHASE is the one field
#    `setup_voice()` sets that this hook does NOT let it clobber past the
#    first application -- saved and restored around every later call, so
#    the render's own natural advancement through the sample survives.
#
# This all happens inside `sharc_harness.call_frame_with_track_injection()`'s
# own per-instruction step loop (its new `post_step_hook` parameter, this
# lane's own addition there) -- a poke applied only between
# `call_frame_with_track_injection()` calls (i.e. once per frame) would be
# too late: the very same clears/clobbers run again, for the very same
# voice, before that later frame's own render check, so only a hook that
# can react between two instructions of the SAME frame can keep the voice
# alive, and correctly paced, across more than the one frame K1 already
# found.
#
# The one thing this lane's own step-1 check established: the built-in
# click/default sample at `0x8045a6c8` (every voice's own post-init word+0,
# per K1's report) is real audio, not silence -- a single-cycle,
# near-full-scale waveform, 368 samples long (matching `FIELD_SAMPLE_LENGTH`'s
# own init value, `0x170`) -- so no separate synthetic source or SDRAM
# write is needed; this replay only ever *points* word+0 at memory the
# firmware's own boot already filled.
BUILTIN_SAMPLE_BASE = 0x8045A6C8
BUILTIN_SAMPLE_LEN = 368

# FUN_1c4f81's own DO 64 interpolation loop (sharc_harness's module
# docstring, "Corrected voice call convention") advances the raw-sample
# read position by `pitch_step` per iteration, 64 iterations per voice
# render call, and one voice render call happens once per frame
# (`sharc_harness.reference_render()`'s own `n_output * 2` = 64, one
# `call_render()`/frame in `FUN_1c642a`'s per-voice dispatch) -- so at
# pitch_step=1.0 a frame consumes 64 raw samples.
RAW_SAMPLES_PER_FRAME = 64

# A sample shorter than this needs FIELD_LOOP=1 to survive more than a
# handful of frames without hitting FUN_1c4f81's own past-limit/wrap logic
# (docs/findings/06's `0x1c5008`/`0x1c50fe`) -- matching
# `sharc_harness.render_blocks()`'s own default `sample_len=4096` as the
# project's baseline "not short" buffer size.
SHORT_SAMPLE_THRESHOLD = 4096

VOICE_ARM_PC = 0x1C4EBB
REARM_CLEAR_PC = 0x1C4E86
REARM_ACTIVE_CLEAR_PC = 0x1C4E8A


def _frames_for_sample(sample_len: int, pitch_step: float) -> int:
    """How many frames FUN_1c4f81's own DO 64 loop needs to read through
    SAMPLE_LEN raw samples once, at PITCH_STEP -- see RAW_SAMPLES_PER_FRAME's
    own docstring. Rounds up: a sample not landing exactly on a frame
    boundary still gets a whole frame to finish it."""
    return math.ceil(sample_len / (RAW_SAMPLES_PER_FRAME * pitch_step))


def _make_voice_rearm_hook(
    image: str,
    voice: int,
    *,
    sample_base: int,
    sample_len: int,
    pitch_step: float,
    loop: bool,
    frames_needed: int,
    shared: dict,
    events: list[dict],
    frame_cell: list[int],
) -> Callable[[sr.Runner], None]:
    """A `sharc_harness.call_frame_with_track_injection(post_step_hook=...)`
    callback (see that function's own docstring) that plays SAMPLE_BASE
    (SAMPLE_LEN raw samples, at PITCH_STEP) through VOICE the instant the
    firmware itself arms it (`VOICE_ARM_PC`), and keeps it alive against
    `FUN_1c4e70`'s own generic per-frame clear (`REARM_CLEAR_PC`/
    `REARM_ACTIVE_CLEAR_PC`) for FRAMES_NEEDED frames -- see this section's
    own module note for why this needs to run inside the per-instruction
    step loop, not between frames.

    SHARED is a caller-owned `{"armed": bool, "remaining": int, "setup_done":
    bool}` (mutable across every frame's own hook instance -- watch_log
    itself resets every frame, per `Runner.fresh_call()`'s own docstring,
    but this dict does not); EVENTS collects one dict per poke this hook
    applies (or declines to apply), for the caller's own report; FRAME_CELL
    is a one-element list the caller mutates (`frame_cell[0] = idx`) before
    each frame's own `call_frame_with_track_injection()` call, so events are
    logged against the right frame number without this function needing its
    own frame counter.

    **Why the real `setup_voice()` poke happens at `voice_render` entry,
    every frame, not once at the arm event (lane N2, found by execution,
    not predicted).** The first version of this hook applied
    `setup_voice()` once, immediately on `VOICE_ARM_PC` (the same frame's
    own `ACTIVE=1` write) -- and measured a rendered amplitude about 15x
    smaller than `sharc_harness.reference_render()`'s own independent math
    predicts for this sample. Reading FIELD_STEP/FIELD_END back after that
    frame's own call showed neither held what `setup_voice()` had just
    written (`step` read back as `~0.0124`, not `1.0`; `end` as `-1.0`,
    not `sample_len`) -- something *else* writes those same fields, later
    in the SAME frame's own per-voice dispatch, after the arm but before
    the render: almost certainly one of docs/findings/06's own "four
    position/step setters" (`0x1c4afe`/`0x1c4bf9`/`0x1c4d88`/`0x1c4a31`),
    from this capture's own stale/null per-track parameter tables (the
    same "no real kit/mixer configuration is loaded" gap this project's
    own frame-render lanes already found elsewhere).

    Moving the poke to `voice_render`'s own entry (`profile(image).
    voice_render`, checked against the incoming `R4` -- the record base,
    per `sharc_harness`'s own "Corrected voice call convention" --
    since this entry point is shared by all 32 voices' own dispatch, once
    per frame each) fixed frame 304 alone (the arm frame): its own
    injected output then matched `reference_render()` almost exactly. But
    reading STEP/END back after frame 305 showed them clobbered again, to
    the SAME stale values, and frame 305's own injected output was
    correspondingly near-flat (PHASE barely advancing) -- so whatever
    writes these fields runs EVERY frame this voice is active, not just
    once after the arm (an ordinary per-frame parameter refresh from the
    same stale tables, not a one-shot "position/step setter" reacting to
    SEED_PENDING as this lane first assumed). This hook therefore reapplies
    `setup_voice()`'s own sample-pointer/length/start/end/loop_start/step/
    loop/reverse fields at `voice_render` entry EVERY frame the keep-alive
    window is open (`shared["armed"]`) -- but saves and restores
    FIELD_PHASE around each call after the first: `setup_voice()` always
    resets PHASE to `start`, and only the very first application should do
    that (seeding position 0) -- every later frame must instead preserve
    PHASE at wherever that SAME frame's own upstream clobber-then-fix
    sequence left it, letting the render actually advance through the
    sample instead of restarting it every frame."""
    record = h.voice_record_address(image, voice)
    voice_render_entry = h.profile(image).voice_render
    word0_label = "%s%d" % (VOICE_WORD0_LABEL_PREFIX, voice)
    active_label = "%s%d" % (VOICE_ACTIVE_LABEL_PREFIX, voice)
    seen = [0]

    def hook(runner: sr.Runner) -> None:
        if shared["armed"] and runner.state.pc_sw == voice_render_entry:
            from tools.sharc_dev.sharc_core.encoding import UREG_CODES

            r4 = runner.state.uregs.get(UREG_CODES["R4"])
            if r4 is not None and getattr(r4, "value", None) == record:
                first = not shared.get("setup_done")
                saved_phase = (
                    None
                    if first
                    else h._read_q31_pair(runner.state, record + h.FIELD_PHASE)
                )
                h.setup_voice(
                    runner.state,
                    image,
                    voice,
                    sample_len=sample_len,
                    pitch_step=pitch_step,
                    start=0,
                    end=sample_len,
                    loop_start=0,
                    loop=loop,
                    reverse=False,
                    sample_base=sample_base,
                )
                if saved_phase is not None:
                    h._write_q31_pair(runner.state, record + h.FIELD_PHASE, saved_phase)
                shared["setup_done"] = True
                events.append(
                    {
                        "frame": frame_cell[0],
                        "field": "setup_voice" if first else "setup_voice-repeat",
                        "pc": "%#x" % voice_render_entry,
                        "value": (
                            "sample_base=%#x sample_len=%d pitch_step=%s "
                            "loop=%s%s"
                            % (
                                sample_base,
                                sample_len,
                                pitch_step,
                                loop,
                                "" if first else " (phase preserved)",
                            )
                        ),
                    }
                )
        log = runner.watch_log
        if seen[0] > len(log):
            seen[0] = 0  # a new frame's own fresh Runner: watch_log reset
        while seen[0] < len(log):
            event = log[seen[0]]
            seen[0] += 1
            if event.access != "write":
                continue
            if event.label == word0_label:
                if (
                    event.pc_sw == REARM_CLEAR_PC
                    and event.new_value == 0
                    and shared["armed"]
                    and shared["remaining"] > 0
                ):
                    h._poke(runner.state, record + h.FIELD_SAMPLE_PTR, sample_base)
                    events.append(
                        {
                            "frame": frame_cell[0],
                            "field": "word0",
                            "pc": "%#x" % event.pc_sw,
                            "value": "%#x" % sample_base,
                        }
                    )
            elif event.label == active_label:
                if (
                    event.pc_sw == VOICE_ARM_PC
                    and event.new_value == 1
                    and not shared["armed"]
                ):
                    shared["armed"] = True
                    shared["remaining"] = frames_needed - 1
                    shared["setup_done"] = False
                    events.append(
                        {
                            "frame": frame_cell[0],
                            "field": "arm-detected",
                            "pc": "%#x" % event.pc_sw,
                            "value": "real firmware arm; setup_voice() applied at "
                            "voice_render entry, not here (see this function's "
                            "own docstring)",
                        }
                    )
                elif event.pc_sw == REARM_ACTIVE_CLEAR_PC and event.new_value == 0:
                    if shared["armed"] and shared["remaining"] > 0:
                        h._poke(runner.state, record + h.FIELD_ACTIVE, 1, width=1)
                        shared["remaining"] -= 1
                        events.append(
                            {
                                "frame": frame_cell[0],
                                "field": "active",
                                "pc": "%#x" % event.pc_sw,
                                "value": "1 (remaining=%d)" % shared["remaining"],
                            }
                        )
                    elif shared["armed"]:
                        # The keep-alive window just closed (`remaining`
                        # reached 0 on a previous frame's own generic
                        # clear): let this and every later recurrence of
                        # the SAME generic clear silence the voice, same as
                        # a real one-shot playback -- `armed=False` also
                        # re-arms this hook for a genuinely later real
                        # trigger, and stops this branch from logging one
                        # entry per remaining frame of the whole replay.
                        shared["armed"] = False
                        events.append(
                            {
                                "frame": frame_cell[0],
                                "field": "active",
                                "pc": "%#x" % event.pc_sw,
                                "value": "0 (keep-alive window ended)",
                            }
                        )
                elif shared["armed"] and event.new_value == 0:
                    # A different pc cleared ACTIVE while armed: a real
                    # end-of-sample path (0x1c5008/0x1c50fe), not this
                    # hook's own target -- stop the keep-alive window early
                    # rather than fight a genuine firmware deactivation.
                    shared["armed"] = False
                    shared["remaining"] = 0
                    events.append(
                        {
                            "frame": frame_cell[0],
                            "field": "active",
                            "pc": "%#x" % event.pc_sw,
                            "value": "0 (natural end-of-sample, not counteracted)",
                        }
                    )

    return hook


#: Every poke `replay_armed_voice(inject_real_sample=True)` applies, in the
#: order it applies them, for `main()`'s own `--armed-voice-wav` help text
#: and this function's own report (`result["pokes"]`) -- kept as one static
#: list so both stay in sync with the constants above instead of drifting
#: (task brief: "List every poke in the CLI help and in the report").
POKE_DESCRIPTIONS: tuple[str, ...] = (
    "record+0x000 (FIELD_SAMPLE_PTR) = %#x (the boot-time click/default "
    "sample, real audio -- see this section's own module note): via "
    "sharc_harness.setup_voice(), at profile(image).voice_render's own "
    "entry (R4==record), EVERY frame the keep-alive window is open (a "
    "per-frame parameter refresh clobbers this, not just a one-time "
    "arm-time write); also independently re-applied by REARM_CLEAR_PC=%#x's "
    "own counteraction whenever FUN_1c4e70's generic per-frame clear "
    "zeroes it first" % (BUILTIN_SAMPLE_BASE, REARM_CLEAR_PC),
    "record+0x188/0x18c (FIELD_SAMPLE_LENGTH/_HI) = %d / 0: via "
    "setup_voice() at voice_render entry, every frame the keep-alive "
    "window is open" % BUILTIN_SAMPLE_LEN,
    "record+0x190/0x194 (FIELD_LOOP_START, Q31 pair) = 0: via setup_voice() "
    "at voice_render entry, every frame the keep-alive window is open",
    "record+0x198/0x19c (FIELD_START, Q31 pair) = 0: via setup_voice() at "
    "voice_render entry, every frame the keep-alive window is open",
    "record+0x1a0/0x1a4 (FIELD_END, Q31 pair) = %d: via setup_voice() at "
    "voice_render entry, every frame the keep-alive window is open "
    "(this is the field this lane found being clobbered back to a stale "
    "value every frame -- see _make_voice_rearm_hook()'s own docstring)"
    % BUILTIN_SAMPLE_LEN,
    "record+0x1a8/0x1ac (FIELD_STEP, Q31 pair) = 1.0 (pitch_step): via "
    "setup_voice() at voice_render entry, every frame the keep-alive "
    "window is open (same clobber as FIELD_END above)",
    "record+0x1b0/0x1b4 (FIELD_PHASE, Q31 pair) = 0 (=start): via "
    "setup_voice() at voice_render entry, ONLY the first frame the voice "
    "arms -- every later frame's own setup_voice() call has this field "
    "saved beforehand and restored after, so the render's own natural "
    "advance through the sample is preserved instead of being rewound",
    "record+0x17c/0x17d/0x17e (FADE_IN/ZERO_CROSS_MUTE/RESEED) = 0: via "
    "setup_voice() at voice_render entry, every frame the keep-alive "
    "window is open (harmless to repeat: these are already 0)",
    "record+0x180 (FIELD_PREVIOUS_SAMPLE) = 0: via setup_voice() at "
    "voice_render entry, every frame the keep-alive window is open",
    "record+0x1bb (FIELD_REVERSE) = 0: via setup_voice() at voice_render "
    "entry, every frame the keep-alive window is open",
    "record+0x1bc (FIELD_LOOP) = 1 (sample is short: %d < %d samples): via "
    "setup_voice() at voice_render entry, every frame the keep-alive "
    "window is open" % (BUILTIN_SAMPLE_LEN, SHORT_SAMPLE_THRESHOLD),
    "record+0x1b8 (FIELD_ACTIVE) = 1: via setup_voice() at voice_render "
    "entry, every frame the keep-alive window is open (redundant with the "
    "firmware's own %#x write on the arm frame); independently re-applied "
    "by REARM_ACTIVE_CLEAR_PC=%#x's own counteraction whenever "
    "FUN_1c4e70's generic per-frame clear zeroes it first"
    % (VOICE_ARM_PC, REARM_ACTIVE_CLEAR_PC),
    "record+0x1ba (FIELD_SEED_PENDING) = 0: via setup_voice() at "
    "voice_render entry, every frame the keep-alive window is open",
)


def replay_armed_voice(
    image: str,
    capture_path: str,
    *,
    voice: int = 4,
    extra_frames: int = 20,
    n_frames: int | None = None,
    start_frame: int = 0,
    inject_real_sample: bool = False,
    frame_overrides: list[tuple[int, int, int]] | None = None,
    flexbus_log: str | None = None,
    record_voices: tuple[int, ...] = (),
) -> dict:
    """Replay CAPTURE_PATH continuously from frame 0 by default (no
    `start_frame` shortcut -- lane J1's own open question, see this
    section's module note; the report this lane wrote used `start_frame=0`
    throughout) on ONE Runner, through `sharc_harness.call_frame_with_track_
    injection()` every frame (real DMA delivery via `sharc_harness.
    write_dma_transfer()`/`drive_dma_completion()`, the same as `replay()`
    above -- NOT the older `write_capture_frame()`/`CAPTURE_FRAME_BASE`
    path `render_frames_to_ring_a()` uses, which lane G1 already found does
    not reach the real arming code), with two things attached to every
    frame's own `fresh_call()`:

    `START_FRAME` (default 0), if given, skips that many of CAPTURE's own
    leading frames without executing them at all -- lane J1's own
    `tools/sharc_dev/sharc_armpath.py` shortcut, `per_frame`'s own real capture index
    preserved (every reported frame number below is `start_frame +
    local_idx`, never a re-based 0). Only safe once whatever it skips is
    known not to matter: this lane's own from-frame-0 run (see the module
    note) found VOICE's own word+0 already null by frame ~2, independent
    of the capture's own TRIG byte, so `start_frame=300` (well past that)
    reproduces this same run's own `arm_frame`/`active_frames`/
    `deactivate_pc`/`sample_pointer_at_arm` for THIS capture exactly --
    used by this lane's own slow test to pin the demo WAV without a ~3
    minute full replay on every run. Do not assume this for a different
    capture or a different voice without checking word+0's own null-timing
    there first.

    1. A log-only write Watchpoint on word+0 (`FIELD_SAMPLE_PTR`) of all
       32 voice records (`_voice_word0_watchpoints()`) plus one on VOICE's
       own FIELD_ACTIVE (`_voice_active_watchpoint()`) -- every hit is
       collected into `word0_writes`/`active_writes` (frame, pc, old/new),
       oldest first, across the WHOLE replay, not just a bracketed window.

    2. VOICE's own decimated render output injected straight into the
       master mix (`inject_track=False, write_master_mix=True` --
       `sharc_harness.inject_master_mix()`, the SAME "existing harness
       path" `sharc_harness.render_frames_to_ring_a()`'s `--ring-a` CLI
       already uses, just pointed at VOICE's record instead of a
       hand-driven one) on every frame -- harmless before VOICE is ever
       armed (its own work buffer reads as silence), and the one thing
       that lets a firmware-armed voice's real output reach ring A without
       a second, separate render pass, since master mix
       (`sharc_dac.MASTER_MIX_BASE`) is a disjoint memory range from every
       voice record and every byte this lane's own watchpoints track --
       this injection cannot perturb the arming logic itself. **This
       injection is the one hand step in this replay** (see `main()`'s own
       `--armed-voice-wav` help text).

    VOICE's own FIELD_ACTIVE is re-read (not just watched) after every
    frame's call, into `active_by_frame` (`bool`, oldest first): the
    firmware's own `+0x1b8` state right after that frame finished, the
    same convention lane J1 used ("ACTIVE is 1 at frame 304"). `arm_frame`
    is the first frame index where this is True (`None` if VOICE is never
    armed in this replay); `active_frames` counts how many CONSECUTIVE
    frames from `arm_frame` stay True; `deactivate_pc` is the first
    `active_writes` entry at or after `arm_frame` whose new value is 0 (the
    exact pc of the `+0x1b8` clear -- docs/findings/06's `0x1c5008`
    past-limit or `0x1c50fe` wrap), or `None` if the run ends first.
    `sample_pointer_at_arm` is VOICE's own word+0 value from JUST BEFORE
    `arm_frame`'s own frame was delivered (`sample_ptr_before_frame[arm_frame]`
    -- matching lane J1's own "just before this lane's first fed frame"
    phrasing) -- not the value after that frame's own render, which may
    already have changed it.

    Ring A (`sharc_harness.read_ring_a()`, deinterleaved L/R -- NOT this
    module's own `_read_ring_a()`/`_mono()`, whose plain `0.5*(L+R)`
    downmix cancels to silence for a coherent signal identically written
    to both channels, per `sharc_harness`'s own "ring A's own L channel
    is deliberately negated" module note) is read every frame and kept
    per-frame; `render_left`/`render_right` are the concatenation of frames
    `[arm_frame, arm_frame + active_frames + EXTRA_FRAMES)` -- the demo
    window the task brief asks for -- or `[]` if VOICE was never armed.

    `INJECT_REAL_SAMPLE` (lane N2, 2026-09-26, default False -- the
    original K1 behaviour is unchanged when this is left off): instead of
    only watching/logging, drive a real sample (`BUILTIN_SAMPLE_BASE`/
    `BUILTIN_SAMPLE_LEN`, this lane's own step-1 finding -- see this
    section's own module note) through VOICE the instant the firmware
    itself arms it, and keep it alive against `FUN_1c4e70`'s own generic
    per-frame clear for as long as the sample needs to play once through
    (`_frames_for_sample()`) -- see `_make_voice_rearm_hook()`'s own
    docstring for exactly what this pokes and when. Adds `pokes` (the
    static `POKE_DESCRIPTIONS` list, so a report always documents every
    poke it could have applied) and `rearm_events` (what actually fired,
    oldest first: `{"frame", "field", "pc", "value"}`) to the returned
    dict; `active_by_frame`/`arm_frame`/`active_frames`/`deactivate_pc`/
    `render_left`/`render_right` above need no code change to reflect the
    extended, real-audio window -- they already read VOICE's own state
    fresh after each frame, which this keep-alive is now sustaining for
    longer than the one silent frame K1 found.

    `FRAME_OVERRIDES` (lane O1, 2026-09-26, default None -- no bytes changed
    when left off): a list of `(track, offset, value)` triples, each applied
    by `apply_frame_overrides()` to every delivered frame's own TX bytes
    before `sharc_harness.write_dma_transfer()` -- see that function's own
    docstring for the exact byte offset and encoding, and this lane's own
    O1 report for what reached (and did not reach) FUN_1c4e70/FUN_1c7442
    when it used this to carry the built-in sample's pointer/length for
    track 2. This is independent of `INJECT_REAL_SAMPLE` above: it changes
    only the bytes delivered over the (real, DMA) transfer, never a voice
    record field directly, so combining the two is possible but conflates
    two different hand steps -- this lane's own report used one at a time.

    Returns a dict with `capture`, `frames_replayed`, `voice`,
    `word0_writes`, `active_writes`, `active_by_frame`, `arm_frame`,
    `active_frames`, `deactivate_pc`, `sample_pointer_at_arm`,
    `render_left`, `render_right`, `frame_stops` (per frame: the halt
    reason, pc, form, text, unknowns and instruction count of that
    frame's call),
    `voice_outputs` (only with RECORD_VOICES: {voice: the concatenated
    decimated work buffer of that voice over the same window as
    `render_left`, read-only, see `_make_voice_output_hook()`),
    `sample_rate_hz`
    (`sharc_dac`'s own ring-A rate, `int(sharc_harness.SOURCE_SAMPLE_RATE //
    2)` = 48000), `pokes`/`rearm_events` (only when `inject_real_sample`),
    and `error` (only present, and everything else absent, if `run_init()`
    itself failed)."""
    cap = sharc_capture.load(capture_path)
    end = None if n_frames is None else start_frame + n_frames
    frames = cap.dspi2_frames[start_frame:end]

    memory = h.load_image_memory(image)
    init = h.run_init(memory, image)
    if not init.ran:
        return {
            "capture": capture_path,
            "frames_in_capture": len(cap.dspi2_frames),
            "frames_replayed": 0,
            "error": "run_init failed: %s" % init.error,
        }

    runner = h.new_runner(memory, image, init=init)
    state = runner.state
    h.setup_frame_dma(state, image, ring_flag=0)
    lp0_report = None
    if flexbus_log is not None:
        runner, lp0_report = lp0.feed(runner, image, lp0.read_log(flexbus_log))
        state = runner.state

    record = h.voice_record_address(image, voice)
    watchpoints = [
        *_voice_word0_watchpoints(image),
        _voice_active_watchpoint(image, voice),
    ]
    active_label = "%s%d" % (VOICE_ACTIVE_LABEL_PREFIX, voice)

    word0_writes: list[dict] = []
    active_writes: list[dict] = []
    active_by_frame: list[bool] = []
    sample_ptr_before_frame: list[int | None] = []
    ring_left_by_frame: list[list[float]] = []
    ring_right_by_frame: list[list[float]] = []
    frame_stops: list[dict] = []

    rearm_hook = None
    rearm_events: list[dict] = []
    frame_cell = [0]
    if inject_real_sample:
        pitch_step = 1.0
        loop = BUILTIN_SAMPLE_LEN < SHORT_SAMPLE_THRESHOLD
        frames_needed = _frames_for_sample(BUILTIN_SAMPLE_LEN, pitch_step)
        rearm_shared = {"armed": False, "remaining": 0}
        rearm_hook = _make_voice_rearm_hook(
            image,
            voice,
            sample_base=BUILTIN_SAMPLE_BASE,
            sample_len=BUILTIN_SAMPLE_LEN,
            pitch_step=pitch_step,
            loop=loop,
            frames_needed=frames_needed,
            shared=rearm_shared,
            events=rearm_events,
            frame_cell=frame_cell,
        )

    arm_events: list[dict] = []
    frame_fields_by_frame: list[dict] = []
    voice_out: dict[int, dict[int, list[float]]] = {v: {} for v in record_voices}
    step_hook = _chain_hooks(
        rearm_hook,
        _make_arm_event_hook(image, arm_events, frame_cell),
        _make_voice_output_hook(image, tuple(record_voices), voice_out, frame_cell)
        if record_voices
        else None,
    )

    for local_idx, frame in enumerate(frames):
        idx = start_frame + local_idx
        frame_cell[0] = idx
        ptr_before = st._dm_read(state, record + h.FIELD_SAMPLE_PTR, 4)
        sample_ptr_before_frame.append(
            ptr_before.value & 0xFFFFFFFF if ptr_before is not None else None
        )

        tx = apply_frame_overrides(frame.tx, frame_overrides)
        frame_fields_by_frame.append(
            {"frame": idx, **summarize_frame_fields(frame_fields(tx))}
        )
        h.write_dma_transfer(state, image, tx)
        runner = h.drive_dma_completion(runner, image)
        state = runner.state

        runner, frame_result, _injected = h.call_frame_with_track_injection(
            runner,
            image,
            record,
            track=0,
            patch_table=h.FRAME_PATCH_TABLE,
            write_master_mix=True,
            inject_track=False,
            watchpoints=watchpoints,
            post_step_hook=step_hook,
        )
        state = runner.state
        halt = frame_result.halt
        frame_stops.append(
            {
                "frame": idx,
                "reason": halt.reason,
                "pc": "%#x" % halt.pc_sw,
                "form": halt.form,
                "text": halt.text,
                "unknowns": list(halt.unknowns),
                "instructions": frame_result.instructions,
            }
        )

        for event in runner.watch_log:
            if event.access != "write":
                continue
            vi = _voice_index_from_label(event.label, VOICE_WORD0_LABEL_PREFIX)
            if vi is not None:
                word0_writes.append(
                    {
                        "frame": idx,
                        "pc": "%#x" % event.pc_sw,
                        "voice": vi,
                        "old": _hex_or_none(event.old_value),
                        "new": _hex_or_none(event.new_value),
                    }
                )
            elif event.label == active_label:
                active_writes.append(
                    {
                        "frame": idx,
                        "pc": "%#x" % event.pc_sw,
                        "old": event.old_value,
                        "new": event.new_value,
                    }
                )

        active_raw = st._dm_read(state, record + h.FIELD_ACTIVE, 1)
        active_by_frame.append(bool(active_raw is not None and active_raw.value))

        ring = h.read_ring_a(memory, image, runner)
        ring_left_by_frame.append([v or 0.0 for v in ring["left"]])
        ring_right_by_frame.append([v or 0.0 for v in ring["right"]])

    local_arm_idx = next((i for i, a in enumerate(active_by_frame) if a), None)
    arm_frame = None if local_arm_idx is None else start_frame + local_arm_idx
    active_frames = 0
    deactivate_pc = None
    if local_arm_idx is not None:
        i = local_arm_idx
        while i < len(active_by_frame) and active_by_frame[i]:
            active_frames += 1
            i += 1
        for w in active_writes:
            if w["frame"] >= arm_frame and w["new"] == 0:
                deactivate_pc = w["pc"]
                break

    render_left: list[float] = []
    render_right: list[float] = []
    voice_outputs: dict[int, list[float]] = {v: [] for v in record_voices}
    if local_arm_idx is not None:
        hi = min(len(frames), local_arm_idx + active_frames + extra_frames)
        for i in range(local_arm_idx, hi):
            render_left.extend(ring_left_by_frame[i])
            render_right.extend(ring_right_by_frame[i])
            for v in record_voices:
                voice_outputs[v].extend(voice_out[v].get(start_frame + i, [0.0] * 32))

    result = {
        "capture": capture_path,
        "frames_in_capture": len(cap.dspi2_frames),
        "frames_replayed": len(frames),
        "start_frame": start_frame,
        "voice": voice,
        "record": "%#x" % record,
        "word0_writes": word0_writes,
        "active_writes": active_writes,
        "active_by_frame": active_by_frame,
        "arm_frame": arm_frame,
        "active_frames": active_frames,
        "deactivate_pc": deactivate_pc,
        "sample_pointer_at_arm": (
            _hex_or_none(sample_ptr_before_frame[local_arm_idx])
            if local_arm_idx is not None
            else None
        ),
        "render_left": render_left,
        "render_right": render_right,
        "sample_rate_hz": int(h.SOURCE_SAMPLE_RATE // 2),
        "lp0": lp0_report,
        "frame_stops": frame_stops,
        "frame_fields": frame_fields_by_frame,
        "arm_events": arm_events,
    }
    if record_voices:
        result["voice_outputs"] = {str(v): voice_outputs[v] for v in record_voices}
    if inject_real_sample:
        result["pokes"] = list(POKE_DESCRIPTIONS)
        result["rearm_events"] = rearm_events
        result["sample_base"] = "%#x" % BUILTIN_SAMPLE_BASE
        result["sample_len"] = BUILTIN_SAMPLE_LEN
        result["cleared_by"] = (
            "word+0 by %#x, ACTIVE by %#x (both FUN_1c4e70's own generic "
            "per-frame clear, called once per frame for every 'in-service' "
            "voice slot -- docs/findings/06 lane K1)"
            % (REARM_CLEAR_PC, REARM_ACTIVE_CLEAR_PC)
        )
    return result


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("image")
    p.add_argument("capture")
    p.add_argument("--frames", type=int, default=None)
    p.add_argument("--out", help="write ring A (L+R averaged) as a mono WAV")
    p.add_argument("--report", help="write the full replay report as JSON")
    p.add_argument(
        "--freq",
        type=float,
        default=1000.0,
        help="the hand-set-up voice's own sine source frequency, in Hz, at "
        "sharc_harness.SOURCE_SAMPLE_RATE (default 1000 Hz)",
    )
    p.add_argument(
        "--sample-len",
        type=int,
        default=4096,
        help="the hand-set-up voice's source buffer length, in samples",
    )
    p.add_argument(
        "--force-command",
        type=int,
        default=None,
        help="override every frame's own header bytes (TX offset 0-1) to "
        "this command number before delivering the transfer, instead of "
        "reading it from that frame's own captured header (frame_command()) "
        "-- diagnostic A/B, still delivered through the real DMA path",
    )
    p.add_argument(
        "--provisional",
        action="append",
        default=[],
        metavar="FORM=MODE",
        help="an opt-in tools/sharc_dev/sharc_core provisional interpretation (e.g. "
        "21p_undoc16=nop) to get a replay past an undocumented form -- may "
        "be given more than once; any result built with this is provisional "
        "([D], not [V]) -- see replay()'s own docstring",
    )
    p.add_argument(
        "--armed-voice-wav",
        action="store_true",
        default=False,
        help="lane K1: instead of the hand-set-up-voice-0 replay() above, "
        "run replay_armed_voice() -- a continuous, from-frame-0 replay "
        "(no start_frame shortcut) that watches all 32 voice records' own "
        "word+0 and --voice's own FIELD_ACTIVE (log-only, never stops the "
        "run), and writes ring A's own output for whichever voice the "
        "FIRMWARE ITSELF arms (--voice, default 4) as a stereo WAV to "
        "--out, from the frame it arms through that many active frames "
        "plus --extra-frames. HAND STEP: this still calls sharc_harness."
        "inject_master_mix() every frame (via call_frame_with_track_"
        "injection(inject_track=False, write_master_mix=True)) to get "
        "--voice's own decimated render output into the master mix -- no "
        "SHARC-side code path from the real per-track mix/gate tables to "
        "the master mix is established yet (see sharc_harness.py's "
        "MASTER_MIX_INJECT_PC module note), so without this hand step "
        "ring A stays silent even for a voice the firmware itself armed "
        "and rendered. --out is a stereo WAV (ring A's own L channel is "
        "the firmware's own negation of R for an identical signal on both "
        "channels -- see that same module note -- so an L+R mono downmix "
        "of this output cancels to silence; play/measure L and R, not "
        "their average).",
    )
    p.add_argument(
        "--voice",
        type=int,
        default=4,
        help="--armed-voice-wav only: which voice record to watch/render "
        "(default 4, docs/findings/06 Lane J1's own finding for "
        "out/captures/dt2-1.16-running-trig.dt2cap)",
    )
    p.add_argument(
        "--extra-frames",
        type=int,
        default=20,
        help="--armed-voice-wav only: frames to render past the voice's "
        "own active window",
    )
    p.add_argument(
        "--start-frame",
        type=int,
        default=0,
        help="--armed-voice-wav only: skip this many of the capture's own "
        "leading frames without executing them (see replay_armed_voice()'s "
        "own docstring for when this is, and is not, safe to use) -- "
        "default 0, a genuinely continuous replay from frame 0",
    )
    p.add_argument(
        "--inject-real-sample",
        action="store_true",
        default=False,
        help="--armed-voice-wav only (lane N2): instead of leaving --voice "
        "silent once the firmware itself arms it (K1's own result), point "
        "it at the boot-time click/default sample (%#x, %d samples -- real "
        "audio, this lane's own step-1 finding, not silence) the instant "
        "the real arm fires, and keep it alive against FUN_1c4e70's own "
        "generic per-frame clear (%#x word+0 / %#x ACTIVE) for as long as "
        "the sample needs to play once through, then let it fall silent "
        "naturally. Every poke this applies, in order:\n- "
        % (
            BUILTIN_SAMPLE_BASE,
            BUILTIN_SAMPLE_LEN,
            REARM_CLEAR_PC,
            REARM_ACTIVE_CLEAR_PC,
        )
        + "\n- ".join(POKE_DESCRIPTIONS),
    )
    p.add_argument(
        "--frame-override",
        action="append",
        default=[],
        metavar="TRACK:OFFSET=VALUE",
        help="--armed-voice-wav only (lane O1): applied to EVERY delivered "
        "transfer's own TX bytes before write_dma_transfer(), as a raw "
        "4-byte big-endian poke at TX offset PER_TRACK_PARAM_BLOCK_OFFSET "
        "(%#x) + TRACK*PER_TRACK_PARAM_BLOCK_STRIDE (%#x) + OFFSET -- see "
        "apply_frame_overrides()'s own docstring for exactly why this "
        "offset, and this lane's own O1 report for what it did and did not "
        "reach. HAND STEP, documented, not a firmware-native field -- may "
        "be given more than once (repeatable, one per TRACK:OFFSET=VALUE)."
        % (PER_TRACK_PARAM_BLOCK_OFFSET, PER_TRACK_PARAM_BLOCK_STRIDE),
    )
    p.add_argument(
        "--fields",
        action="store_true",
        default=False,
        help="print the trig/release masks and per-track machine, slot, "
        "pitch and velocity fields (frame_fields()) of the capture's frames "
        "that carry a trig or release bit, one JSON line per frame, without "
        "running the SHARC (--all-frames: every frame; --report: all as JSON)",
    )
    p.add_argument("--all-frames", action="store_true", default=False)
    p.add_argument(
        "--record-voice",
        dest="record_voice",
        action="append",
        type=int,
        default=[],
        metavar="V",
        help="--armed-voice-wav only: also record voice V's own decimated "
        "render output (its work buffer, read-only) every frame, over the "
        "same window as --out; repeatable. Goes to the report as "
        "voice_outputs and, with --voices-wav, to a WAV",
    )
    p.add_argument(
        "--voices-wav",
        dest="voices_wav",
        default=None,
        metavar="PATH",
        help="--armed-voice-wav only: write the --record-voice outputs as a "
        "WAV, one channel per voice (two voices: stereo, first = left)",
    )
    p.add_argument(
        "--flexbus-log",
        default=None,
        metavar="PATH",
        help="before the first frame, feed this log of ColdFire FlexBus "
        "writes to 0x8C000002 (sample pages and slot headers) through the "
        "modelled link port 0, so the DSP's own receive callback fills "
        "the slot table and sample memory (tools/sharc_dev/sharc_lp0.py has the "
        "format; `sharc_lp0.py synth` makes one from a native sample)",
    )
    return p.parse_args(argv)


def _parse_provisional(pairs: list[str]) -> dict[str, str] | None:
    if not pairs:
        return None
    out = {}
    for pair in pairs:
        form, _, mode = pair.partition("=")
        out[form] = mode
    return out


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.fields:
        rows = capture_fields(args.capture, only_events=not args.all_frames)
        for row in rows:
            print(json.dumps(row))
        if args.report:
            with open(args.report, "w") as fh:
                json.dump(rows, fh, indent=1)
        return 0
    if args.armed_voice_wav:
        frame_overrides = [parse_frame_override(spec) for spec in args.frame_override]
        result = replay_armed_voice(
            args.image,
            args.capture,
            voice=args.voice,
            extra_frames=args.extra_frames,
            n_frames=args.frames,
            start_frame=args.start_frame,
            inject_real_sample=args.inject_real_sample,
            frame_overrides=frame_overrides,
            flexbus_log=args.flexbus_log,
            record_voices=tuple(args.record_voice),
        )
        if args.voices_wav and result.get("voice_outputs"):
            chans = [result["voice_outputs"][str(v)] for v in args.record_voice]
            if len(chans) == 1:
                h.write_wav(
                    args.voices_wav, chans[0], sample_rate=result["sample_rate_hz"]
                )
            else:
                h.sharc_dac.write_wav_stereo(
                    args.voices_wav,
                    chans[0],
                    chans[1],
                    sample_rate=result["sample_rate_hz"],
                )
        if args.out and result.get("render_left"):
            h.sharc_dac.write_wav_stereo(
                args.out,
                result["render_left"],
                result["render_right"],
                sample_rate=result["sample_rate_hz"],
            )
        if args.report:
            with open(args.report, "w") as fh:
                json.dump(result, fh, indent=1)
        print(
            "%s: %d/%d frame(s) replayed, voice=%d, arm_frame=%s, "
            "active_frames=%d, deactivate_pc=%s, sample_pointer_at_arm=%s"
            % (
                args.capture,
                result.get("frames_replayed", 0),
                result.get("frames_in_capture", 0),
                args.voice,
                result.get("arm_frame"),
                result.get("active_frames", 0),
                result.get("deactivate_pc"),
                result.get("sample_pointer_at_arm"),
            )
        )
        for event in result.get("arm_events", []):
            if event["event"] in ("trig", "load", "arm", "release"):
                print("  frame %(frame)d %(event)s voice=%(voice)s" % event)
        if args.inject_real_sample:
            print(
                "  real-sample injection: sample=%s (%s samples), "
                "cleared_by=%s, rearm_events=%d"
                % (
                    result.get("sample_base"),
                    result.get("sample_len"),
                    result.get("cleared_by"),
                    len(result.get("rearm_events", [])),
                )
            )
        return 0 if "error" not in result else 1
    result = replay(
        args.image,
        args.capture,
        n_frames=args.frames,
        command=args.force_command,
        tone_freq=args.freq,
        sample_len=args.sample_len,
        provisional_interpretations=_parse_provisional(args.provisional),
        flexbus_log=args.flexbus_log,
    )
    if args.out and "ring_a_mono" in result:
        h.write_wav(args.out, result["ring_a_mono"], sample_rate=48000)
    if args.report:
        with open(args.report, "w") as fh:
            json.dump(result, fh, indent=1)
    print(
        "%s: %d/%d frame(s) replayed, commands_seen=%s, first_stop=%s, "
        "other_voices_active=%s"
        % (
            args.capture,
            result.get("frames_replayed", 0),
            result.get("frames_in_capture", 0),
            result.get("commands_seen"),
            result.get("first_stop"),
            result.get("other_voices_active_by_firmware"),
        )
    )
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
