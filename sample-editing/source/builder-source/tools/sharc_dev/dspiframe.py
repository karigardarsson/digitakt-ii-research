"""One definition of the ColdFire<->SHARC DSPI2 frame link.

This used to be scattered: `unicorn-reference-final:tools/sharcframe.py` (frame capture),
`unicorn-reference-final:tools/machinecheck.py` (frame-word addressing), `unicorn-reference-final:emu/dspi2.py` (the eDMA/DSPI2
transport model) and `docs/findings/04-coldfire-dsp-link.md` each carried a
partial, independently-phrased copy of the same facts. This module is the one
place they live now; the other three import from here instead of restating
them. See `docs/findings/04-coldfire-dsp-link.md`, "The ColdFire tells the
SHARC through a periodic DSPI2 frame", "eDMA and DSPI transfer inventory" and
"The mirror index to TX frame map, and the 17-word header" for how each fact
below was established.

Two layers:

- **Transport** (SoC hardware, not firmware-specific): the DSPI2 peripheral's
  register block, eDMA channels 28/29 and their completion vectors, and the
  fixed 2,748-byte full-duplex frame size. Same on every image because it is
  wired into silicon and the RTOS-level driver shape, not chosen per build.
- **TX frame contents** (firmware-specific): where a track's fields sit inside
  the frame once it's built. The per-track stride, header size and mirror-index
  map are the same on every Digitakt-family image seen so far (`docs/findings/
  04-coldfire-dsp-link.md`), but the frame's *guest address* and the per-device
  real-payload length are not -- they come from `tools/sharc_dev/framelink.py`'s
  per-image, SHA-256-keyed profile (the DSPI2-frame analogue of what
  `tools/coldfire/symbols.py` does for RTOS/boot symbols: resolve from the image rather
  than hardcode one build's addresses for every build). `tx_base_for()` /
  `rx_base_for()` below pull the guest address out of a resolved profile
  instead of a third hardcoded copy; `TX_BASE_DT2` / `TX_BASE_DN2` are the
  known values for callers that already know which device they're on and have
  no image to resolve against (`unicorn-reference-final:tools/machinecheck.py`'s pure-function tests,
  which build addresses with no snapshot or image present).
"""

import struct

# --------------------------------------------------------------------------
# Transport: DSPI2 registers, eDMA channels 28/29, the frame's wire size.
# --------------------------------------------------------------------------

DSPI2_BASE = 0xEC038000
DSPI2_MCR = DSPI2_BASE + 0x00
DSPI2_TCR = DSPI2_BASE + 0x08
DSPI2_CTAR0 = DSPI2_BASE + 0x0C
DSPI2_SR = DSPI2_BASE + 0x2C
DSPI2_RSER = DSPI2_BASE + 0x30
DSPI2_PUSHR = DSPI2_BASE + 0x34
DSPI2_POPR = DSPI2_BASE + 0x38

# Channel roles, confirmed independently from the Ghidra decompile/disassembly
# dump and from a raw-byte scan of the image (see docs/findings/04-coldfire-
# dsp-link.md, "eDMA and DSPI transfer inventory"): TCD 29's DADDR is
# DSPI2_PUSHR (TX, SRAM staging -> hardware), TCD 28's SADDR is a POPR-area
# hardware address (RX, hardware -> SRAM). Same on Digitone II 1.11's driver
# (FUN_400cf7be) at the same TCD offsets, only the SRAM addresses differ.
TX_CHAN, RX_CHAN = 29, 28
TX_VECTOR, RX_VECTOR = TX_CHAN + 120, RX_CHAN + 120  # 149, 148
SR_LINK_IDLE = 1 << 28  # DSPI2_SR bit the driver polls before arming either TCD
INT_MAJOR = 0x0002  # TCD CSR bit: raise the channel's completion vector
E_SG = 0x0010  # TCD CSR bit: scatter-gather reload instead of CITER<-BITER

# The eDMA word count of both TCDs is set from one shared variable, so this is
# a single fixed-length full-duplex exchange, not two independently sized
# transfers -- see docs/findings/04-coldfire-dsp-link.md, "the eDMA word
# count of both the receive and the transmit descriptor is set from one
# variable". 2,748 bytes on every Digitakt-family image seen so far.
FRAME_BYTES = 0xABC

# Real payload bytes the driver copies out of the caller's buffer before
# padding the rest of the frame with tag-only entries; device-specific,
# `docs/findings/04-coldfire-dsp-link.md`, "the whole received frame is
# copied back... The first argument -- 0x802 on Digitakt II, 0xa80 on
# Digitone II -- is how many real payload words the driver copies".
TX_PAYLOAD_BYTES = {"dt2": 0x802, "dn2": 0xA80}

# Each PUSHR entry the driver builds is `0x8001xxxx`: CONT|PCS0 in the high
# 16 bits (consumed by the DSPI2 peripheral, never shifted onto the wire),
# TXDATA in the low 16. `docs/findings/04-coldfire-dsp-link.md`, "Each PUSHR
# entry is 0x8001xxxx"; independently confirmed by disassembling the frame
# builder (`move.w #$8001,(a0)` then `move.w (a2)+,-$2(a0)`).
PUSHR_TAG = 0x8001

# The DSPI2 driver's own calling convention -- (tx_len, tx, rx_len, rx) --
# reached via `jsr`, so on entry the stack holds the return address below the
# four arguments. Shared by unicorn-reference-final:tools/sharcframe.py's capture and
# unicorn-reference-final:tools/machinecheck.py's capture_frame(), which both intercept this call the
# same way.
DRIVER_CALL_FORMAT = ">IIIII"  # ret, tx_len, tx, rx_len, rx


def read_driver_call(uc, sp):
    """-> (ret, tx_len, tx, rx_len, rx), read from the guest stack at `sp`."""
    return struct.unpack(DRIVER_CALL_FORMAT, bytes(uc.mem_read(sp, 20)))


# --------------------------------------------------------------------------
# TX frame contents: per-track layout, once the frame is built.
# --------------------------------------------------------------------------

# Known TX/RX frame guest addresses, keyed by device short name
# (devices/*.toml's [device].short) -- for callers that already know which
# device they're on and have no image to resolve a framelink profile against.
# Same values as tools/sharc_dev/framelink.py's TABLES 'tx_frame'/'rx_frame' entries for
# Digitakt II (both 1.15C and 1.16 share them); Digitone II's are read from
# docs/findings/04-coldfire-dsp-link.md, "Digitone II 1.11: the same link and
# the same machine table shape" (FUN_400cf7be(0xa80, 0x80005e60, 0xabc,
# 0x800053a4)).
TX_BASE_DT2 = 0x80005348
RX_BASE_DT2 = 0x8000488C
TX_BASE_DN2 = 0x80005E60
RX_BASE_DN2 = 0x800053A4

# A row's header: 17 words (0x22 bytes) before per-track parameters begin.
# docs/findings/04-coldfire-dsp-link.md, "The mirror index to TX frame map,
# and the 17-word header".
HEADER_WORDS = 17
HEADER_BYTES = 0x22

# Per-track stride of the SRC/filter/amp/FX block within the TX frame (the
# four memcpy ranges tile one contiguous 0x60-byte block per track, `0xda` to
# `0x13a`). docs/findings/04-coldfire-dsp-link.md, "eDMA and DSPI transfer
# inventory" (frame_addr's `TX_BASE + track*0x60 + offset` usage) and "The
# four destination ranges tile one contiguous 0x60-byte block per track".
TRACK_STRIDE = 0x60

# The machine-type word array is NOT track-tiled inside the 0x60 block: it is
# a flat 2-byte-per-track table elsewhere in the frame.
# docs/findings/04-coldfire-dsp-link.md, "The machine type reaches the SHARC,
# at TX frame offset 0x94 + 2i".
MACHINE_TYPE_OFFSET = 0x94


def frame_addr(tx_base, offset, track):
    """-> the absolute guest address of `offset` on `track`'s row of the TX
    frame based at `tx_base`.

    `MACHINE_TYPE_OFFSET` (0x94) is a flat, 2-byte-per-track array, addressed
    `tx_base + 0x94 + 2*track`; every other offset is inside the per-track
    `TRACK_STRIDE`-byte SRC/filter/amp/FX block, addressed
    `tx_base + track*TRACK_STRIDE + offset`. See the module docstring and
    docs/findings/04-coldfire-dsp-link.md, "The mirror index to TX frame map,
    and the 17-word header" (mirror index 27 (CFADE) -> +0xde, 31 (SLICE) ->
    +0xe6, 32 (LEN) -> +0xe8, 33 -> +0xea).
    """
    if offset == MACHINE_TYPE_OFFSET:
        return tx_base + MACHINE_TYPE_OFFSET + 2 * track
    return tx_base + track * TRACK_STRIDE + offset


# mirror_index -> (frame_offset_of_mirror_index_lo, page name), for the range
# [lo, hi] inclusive. docs/findings/04-coldfire-dsp-link.md, "The mirror
# index to TX frame map, and the 17-word header": four memcpy ranges land
# exactly on the SRC/filter/amp/FX page boundaries (25, 35, 49, 64).
MIRROR_RANGES = (
    (25, 34, 0xDA, "SRC"),
    (35, 48, 0xFA, "filter"),
    (49, 61, 0x116, "amp and FX sends"),
    (64, 68, 0x130, "FX"),
)


def mirror_to_frame_offset(mirror_index):
    """-> the TX frame offset (add `track*TRACK_STRIDE` for track > 0) that
    `mirror_index` lands at, or None if it is outside the four mapped ranges
    (indices 62-63, Portamento, are skipped; indices >= 54 do not fit in one
    track's row -- see docs/findings/04-coldfire-dsp-link.md's "row overrun"
    note)."""
    for lo, hi, frame_off, _page in MIRROR_RANGES:
        if lo <= mirror_index <= hi:
            return frame_off + 2 * (mirror_index - lo)
    return None


# --------------------------------------------------------------------------
# Per-image guest addresses: reuse tools/sharc_dev/framelink.py's SHA-256-keyed
# resolution (the DSPI2-frame analogue of tools/coldfire/symbols.py's rule-based
# resolution for RTOS/boot symbols) instead of hardcoding a build's addresses
# a third time.
# --------------------------------------------------------------------------


def _table_base(profile, name):
    for base, _size, _rows, table_name in profile.get("tables", ()):
        if table_name == name:
            return base
    raise KeyError("profile %r has no %r table" % (profile.get("name"), name))


def tx_base_for(profile):
    """-> the TX frame's guest address from a framelink-shaped profile
    (`tools/sharc_dev/framelink.profile_for()`'s return value)."""
    return _table_base(profile, "tx_frame")


def rx_base_for(profile):
    """-> the RX frame's guest address from a framelink-shaped profile."""
    return _table_base(profile, "rx_frame")
