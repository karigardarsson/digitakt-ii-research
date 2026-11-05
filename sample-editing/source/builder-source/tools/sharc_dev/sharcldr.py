#!/usr/bin/env python3
"""Parse and characterise the SHARC DSP blob (container section 7).

Section 7 is the SHARC DSP image. The ColdFire MAIN OS makes no audio; it
RPCs parameter changes to this DSP, so anything sound-related lives here.

The blob is a pure ADI boot-stream: with the FILL bit correctly identified
(see below), the entire file parses as a single block chain -- 104 blocks
for Digitakt II 1.15C and 96 for Digitone II 1.10E, consuming 100% of both
files. Per Table 40-29, a BFLAG_FIRST block's target_address is the start
address of the application it begins; the stream carries more than one
(Digitakt has BFLAG_FIRST blocks at 0x120230 and 0x1c1338), so the last
one is the final application's entry point (0x1c1338 for Digitakt, 0x1c12e2
for Digitone). BFLAG_FINAL's target_address carries no documented meaning
(Table 40-30).

The blob begins with an ADI boot-stream: a 16-byte, little-endian, four
32-bit-field (block_code, target_address, byte_count, argument) header per
block. A header is valid when its HDRSIGN names a target core and the
byte-wise XOR of all 16 header bytes is zero. block_code bit 8 is
BFLAG_FILL (0x100): when set, no
payload follows the header (the block is a zero/constant fill of
byte_count bytes) -- a FILL block has no payload in the stream; otherwise
exactly byte_count payload bytes follow. Table 40-33 confirms a FILL
block's `argument` field is the 32-bit fill value. Bit 12 is BFLAG_IGNORE,
not FILL. Other block_code bits are decoded per the standard ADI BFLAG set
(see BFLAGS below); any bit not in that set is reported as bitN rather than
inventing a meaning. The bit assignments below are confirmed against
Table 40-27 "Block Code Flags" of the ADSP-2156x hardware reference: bit 4
is BFLAG_SAVE, bit 9 is Reserved (there is no BFLAG_QUICKBOOT), and bits
24-31 (HDRSIGN) select the target core -- 0xAD/0xAC/0xAB for core 0/1/2.

Measured region split, as examples, not universal rules: Digitakt's tail
(everything after the 11,248-byte prologue) is roughly 56 KB of float-like
tables and 244 KB of "other"; Digitone's tail is roughly 72 KB float and
712 KB other -- Digitone's blob is 833,060 bytes total against Digitakt's
320,780. `kind` is a heuristic over bit patterns only: "float" means the
32-bit words decode as plausible little-endian IEEE-754 floats, which is
strong evidence of coefficient or wavetable data; "other" means only "not
obviously float or zero" -- it is NOT evidence of executable code.
Confirming code needs a SHARC disassembler, and stock Ghidra 12.1.3 ships
no SHARC, Blackfin or ADSP processor module (verified by listing its
Processors directory).

A previously circulated figure of "32 blocks, 178,796 of 320,780 bytes
loaded" does not reproduce with this parser: it finds 4 blocks and 11,184
payload bytes. That is a discrepancy to re-check, not a claim that either
figure is right.

Alignment analysis of the 10,312-byte loader payload (see `alignment()`)
finds repeated code motifs landing on even offsets exclusively but spread
roughly uniformly across mod 4, mod 6 and mod 8. That is 16-bit-granular
variable-length encoding, not fixed 48-bit words, and on SHARC+ that
encoding is VISA. This is an inference from alignment statistics, not a
decoded instruction, but it scopes any future Ghidra SLEIGH work to
SHARC+ VISA rather than classic ADSP-21xx fixed 48-bit. The encoding spec
is the publicly downloadable "SHARC+ Core Programming Reference"
(sc58x-2158x-prm.pdf) from analog.com, no registration required.

Usage:
    uv run python -m tools.sharc_dev.sharcldr section_7_digitakt.bin --json out.json
    uv run python -m tools.sharc_dev.sharcldr section_7_digitakt.bin --dump-blocks blocks/
    uv run python -m tools.sharc_dev.sharcldr section_7_digitakt.bin --main out/sharc/dt2-1.16-main.bin

--main writes the final application's code as one file: the run of blocks
that starts at the last BFLAG_FIRST entry (as a byte address) and continues
while each block starts where the previous one ends, FILL blocks included.
"""

import argparse
import bisect
import hashlib
import json
import math
import os
import struct
import sys
from collections import defaultdict
from types import MappingProxyType
from typing import Any

HEADER_LEN = 16
BFLAGS = {
    "SAVE": 4,
    "AUX": 5,
    "FILL": 8,
    "CALLBACK": 10,
    "INIT": 11,
    "IGNORE": 12,
    "INDIRECT": 13,
    "FIRST": 14,
    "FINAL": 15,
}
FILL_BIT = BFLAGS["FILL"]
SW_ALIAS_BASE = 0x28000000
# The boot stream's contiguous service/runtime image uses the processor's L2
# byte window, while direct VISA calls name the same code through this
# short-word execution window.  Prefer the ordinary SW alias and use this
# mapping only when that address is absent from the loaded image.
L2_BYTE_BASE = 0x20000000
# ADSP-2156x hardware reference (out/refs/adsp-2156x-hwr), "L2CTL0 contains
# 1M byte of RAM grouped into eight banks, 128K bytes each and 96K bytes of
# boot ROM": the addressable L2 SRAM window is 1 MB, not one 128 KB bank.
# The old bound (0x20020000, one bank) wrongly refused a real, unoverwritten
# read of DN2 1.11/1.10E's loader block 57 (target 0x2001e888, byte_count
# 67652) past its first 128 KB -- read_sw()'s fallback returned None for the
# other ~91% of the block even though LoadedMemory (byte-address read) shows
# every one of those bytes is exactly what the block itself last wrote, no
# later block touches them. See docs/findings/05-sharc-isa-and-decoding.md,
# "One decode path" / DB_VERSION v13.
L2_BYTE_LIMIT = 0x20100000
L2_SW_BASE = 0x00B80000

# block_code bits 24-31, per Table 40-27: which core the block is for.
HDRSIGN = {0xAD: 0, 0xAC: 1, 0xAB: 2}


def target_core(block_code):
    """Core number the block targets, from block_code's top byte, or None
    for an unrecognised signature."""
    return HDRSIGN.get(block_code >> 24)


def sw_to_byte(addr):
    """Short-word (execution) address -> loader byte address."""
    return 2 * addr + SW_ALIAS_BASE


def byte_to_sw(addr):
    """Loader byte address -> short-word (execution) address. Returns None if addr is below SW_ALIAS_BASE or is odd."""
    if addr < SW_ALIAS_BASE:
        return None
    delta = addr - SW_ALIAS_BASE
    if delta % 2:
        return None
    return delta // 2


# Measured on this firmware's own aPLib-compressed container streams, which
# is the only honest local definition of "compressed": sections 2/3/7 of
# both shipping images sit at 7.83-7.88 bits/byte. Two known-raw controls
# sit far below it -- section 4 (the updater, documented as stored raw) at
# 6.057, and this blob's own SHARC loader payload at 6.066. So a region
# below roughly 7.5 is not compressed, and the section 7 payload regions
# (Digitakt 6.416, Digitone 7.260) are therefore raw, not packed. This
# matters because a compressed payload would need unpacking before any
# disassembly, and it does not.
APLIB_ENTROPY_BAND = (7.83, 7.88)
ENTROPY_RAW_CEILING = 7.5

# Bits 16-23 are HDRCHK and bits 24-31 are HDRSIGN; neither byte contains
# block flags. HDRCHK makes the byte-wise XOR of the 16-byte header zero,
# while HDRSIGN selects core 0/1/2 as documented in Table 40-27.
FLAG_BITS = 16


_BIT_NAMES = {bit: name for name, bit in BFLAGS.items()}


def _flags(block_code):
    flags = []
    for bit in range(FLAG_BITS):
        if block_code & (1 << bit):
            flags.append(_BIT_NAMES.get(bit, "bit%d" % bit))
    return flags


def _header_valid(hdr):
    if len(hdr) != HEADER_LEN or hdr[3] not in HDRSIGN:
        return False
    x = 0
    for b in hdr:
        x ^= b
    return x == 0


class LoadedMemory:
    """Read-only view of the address space written by an ADI boot stream.

    Loader byte addresses are canonical.  Repeated or overlapping writes are
    resolved in stream order, with the last write winning.  FILL values use
    the same repeated little-endian 32-bit argument semantics as
    :func:`main_program`; an importer such as Ghidra may instead represent
    fills as uninitialized memory as an analysis policy.
    """

    def __init__(self, data, blocks):
        try:
            self.data = bytes(data)
        except (TypeError, ValueError) as exc:
            raise ValueError("stream data must be bytes-like") from exc
        self.blocks = tuple(self._validate_block(block) for block in blocks)
        self._segments = self._resolve_segments()

    @classmethod
    def from_stream(cls, data, blocks=None):
        """Build a view from stream bytes and optional parsed block records."""
        try:
            immutable_data = bytes(data)
        except (TypeError, ValueError) as exc:
            raise ValueError("stream data must be bytes-like") from exc
        return cls(
            immutable_data, parse_blocks(immutable_data) if blocks is None else blocks
        )

    def _validate_block(self, block):
        if not hasattr(block, "__getitem__"):
            raise ValueError("block metadata must be a mapping")
        try:
            record = dict(block)
        except (TypeError, ValueError) as exc:
            raise ValueError("block metadata must be a mapping") from exc

        def nonnegative(name):
            value = record.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(
                    "block metadata %s must be a nonnegative integer" % name
                )
            return value

        nonnegative("target_address")
        if "index" in record:
            nonnegative("index")
        count = nonnegative("byte_count")
        fill = record.get("fill")
        if not isinstance(fill, bool):
            raise ValueError("block metadata fill must be a boolean")
        if fill:
            argument = record.get("argument")
            if (
                not isinstance(argument, int)
                or isinstance(argument, bool)
                or not 0 <= argument <= 0xFFFFFFFF
            ):
                raise ValueError(
                    "FILL block argument must be a 32-bit unsigned integer"
                )
        else:
            offset = nonnegative("payload_offset")
            payload_len = nonnegative("payload_len")
            if payload_len != count:
                raise ValueError("non-FILL block payload_len must equal byte_count")
            if offset + payload_len > len(self.data):
                raise ValueError("non-FILL block payload metadata exceeds stream data")
        if "flags" in record:
            record["flags"] = tuple(record["flags"])
        return MappingProxyType(record)

    @staticmethod
    def _check_address_size(address, size):
        for name, value in (("address", address), ("size", size)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("%s must be a nonnegative integer" % name)

    def _resolve_segments(self):
        """Disjoint (starts, ends, owners) lists, sorted by start: each segment
        [start, end) is covered by one final block, the last in stream order
        to write it. Block edges split the address space, so coverage is the
        same at every address of a segment."""
        edges = sorted(
            {
                edge
                for block in self.blocks
                if block["byte_count"]
                for edge in (
                    block["target_address"],
                    block["target_address"] + block["byte_count"],
                )
            }
        )
        starts, ends, owners = [], [], []
        for low, high in zip(edges, edges[1:], strict=False):
            for index in range(len(self.blocks) - 1, -1, -1):
                block = self.blocks[index]
                if (
                    block["byte_count"]
                    and block["target_address"]
                    <= low
                    < block["target_address"] + block["byte_count"]
                ):
                    starts.append(low)
                    ends.append(high)
                    owners.append(index)
                    break
        return starts, ends, owners

    def _segment(self, address):
        """(segment end, block index) covering address, or None for a gap."""
        starts, ends, owners = self._segments
        i = bisect.bisect_right(starts, address) - 1
        if i < 0 or address >= ends[i]:
            return None
        return ends[i], owners[i]

    def _covering_block(self, address):
        found = self._segment(address)
        if found is None:
            return None, None
        return found[1], self.blocks[found[1]]

    def read(self, address, size):
        """Return exactly *size* final loaded bytes, or None for any gap."""
        self._check_address_size(address, size)
        result = bytearray()
        end = address + size
        while address < end:
            found = self._segment(address)
            if found is None:
                return None
            segment_end, index = found
            block = self.blocks[index]
            stop = min(end, segment_end)
            offset = address - block["target_address"]
            if block["fill"]:
                pattern = block["argument"].to_bytes(4, "little")
                result += bytes(
                    pattern[(offset + k) % 4] for k in range(stop - address)
                )
            else:
                start = block["payload_offset"] + offset
                result += self.data[start : start + stop - address]
            address = stop
        return bytes(result)

    def owner_runs(self, address, size):
        """[(start, end, owner_index_or_None), ...]: half-open, sorted,
        contiguous runs covering exactly [address, address+size), each
        naming the stream-order index of the block that finally owns that
        span (last write wins, same resolution as read()) or None for a
        gap no block ever wrote. Lets a caller ask "how much of block N's
        own declared range does block N still own in the final image" --
        see docs/findings/05-sharc-isa-and-decoding.md's loader block 1
        finding (DB_VERSION v13): a code block whose byte range is later,
        entirely overwritten by other blocks in the same boot stream is
        never resident in the image the emulator actually boots from."""
        self._check_address_size(address, size)
        starts, ends, owners = self._segments
        runs = []
        addr = address
        end = address + size
        while addr < end:
            found = self._segment(addr)
            if found is None:
                i = bisect.bisect_right(starts, addr)
                nxt = starts[i] if i < len(starts) else end
                stop = min(nxt, end)
                runs.append((addr, stop, None))
                addr = stop
                continue
            segment_end, index = found
            stop = min(end, segment_end)
            runs.append((addr, stop, self.blocks[index].get("index", index)))
            addr = stop
        return runs

    def read_sw(self, pc_sw, size=6):
        """Read from a VISA short-word PC using loader byte addressing."""
        if not isinstance(pc_sw, int) or isinstance(pc_sw, bool) or pc_sw < 0:
            raise ValueError("pc_sw must be a nonnegative integer")
        primary = self.read(sw_to_byte(pc_sw), size)
        if primary is not None or pc_sw < L2_SW_BASE:
            return primary
        fallback = L2_BYTE_BASE + 2 * (pc_sw - L2_SW_BASE)
        if fallback < L2_BYTE_BASE or fallback + size > L2_BYTE_LIMIT:
            return None
        return self.read(fallback, size)

    def source_block(self, address):
        """Return the stream-order index of the final block covering address."""
        self._check_address_size(address, 0)
        position, block = self._covering_block(address)
        if block is None:
            return None
        return block.get("index", position)

    def ranges(self):
        """Return sorted, merged half-open ranges written by nonempty blocks."""
        spans = sorted(
            (block["target_address"], block["target_address"] + block["byte_count"])
            for block in self.blocks
            if block["byte_count"]
        )
        merged: list[tuple[int, int]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return tuple(merged)

    def has_final_marker(self):
        """Whether parsing reached a final loader-stream marker block."""
        return bool(self.blocks and "FINAL" in self.blocks[-1].get("flags", ()))


def parse_blocks(data):
    """Walk the ADI boot-stream, returning parsed block dicts.

    Stops at the first invalid header (bad checksum) or at any header whose
    payload would overrun the file.
    """
    blocks = []
    offset = 0
    index = 0
    while offset + HEADER_LEN <= len(data):
        hdr = data[offset : offset + HEADER_LEN]
        if not _header_valid(hdr):
            break
        block_code, target_address, byte_count, argument = struct.unpack("<IIII", hdr)
        fill = bool(block_code & (1 << FILL_BIT))
        payload_offset = offset + HEADER_LEN
        payload_len = 0 if fill else byte_count
        if payload_offset + payload_len > len(data):
            break
        blocks.append(
            {
                "index": index,
                "offset": offset,
                "block_code": block_code,
                "target_address": target_address,
                "byte_count": byte_count,
                "argument": argument,
                "fill": fill,
                "flags": _flags(block_code),
                "core": target_core(block_code),
                "payload_offset": payload_offset,
                "payload_len": payload_len,
            }
        )
        offset = payload_offset + payload_len
        index += 1
    return blocks


def offset_for_address(blocks, addr, space="sw"):
    """File offset holding the byte at `addr`, or None if no loaded block
    covers it. space="sw" treats addr as a short-word address and converts
    via sw_to_byte first; space="byte" uses it directly. FILL blocks are
    never a match -- they occupy address space but have no bytes in the
    file."""
    byte_addr = sw_to_byte(addr) if space == "sw" else addr
    for b in blocks:
        if b["fill"]:
            continue
        if b["target_address"] <= byte_addr < b["target_address"] + b["payload_len"]:
            return b["payload_offset"] + (byte_addr - b["target_address"])
    return None


def fill_block_for_address(blocks, addr, space="sw"):
    """The FILL block whose constant covers `addr`, or None.

    A FILL block occupies address space without contributing bytes to the
    file, so offset_for_address() cannot see it. The loader still writes such
    an address -- with the block's `argument` as the repeating value -- so
    reporting "no file offset" as "not covered" would wrongly claim the
    loader leaves the location alone."""
    byte_addr = sw_to_byte(addr) if space == "sw" else addr
    for b in blocks:
        if not b["fill"]:
            continue
        if b["target_address"] <= byte_addr < b["target_address"] + b["byte_count"]:
            return b
    return None


def address_for_offset(blocks, offset, space="sw"):
    """Inverse: the address that file `offset` loads to, or None if the
    offset is a header or lies outside every block payload."""
    for b in blocks:
        if b["fill"]:
            continue
        if b["payload_offset"] <= offset < b["payload_offset"] + b["payload_len"]:
            byte_addr = b["target_address"] + (offset - b["payload_offset"])
            return byte_to_sw(byte_addr) if space == "sw" else byte_addr
    return None


def entry_points(blocks):
    """Target addresses of every BFLAG_FIRST block, in stream order.

    Per Table 40-29, a FIRST block's target_address is the start address of
    the application it begins; a multi-application boot stream carries
    several. These are short-word (VISA PC) addresses, not loader byte
    addresses -- the PRM (p.4-14) states the PC points to short-word
    address space under VISA, which is why they differ in form from data
    blocks' byte targets."""
    return [b["target_address"] for b in blocks if "FIRST" in b["flags"]]


def main_program(data, blocks):
    """-> (byte address, bytes, [block indices]) of the final application's code,
    or (None, b"", []) if there is none.

    The run starts at the non-FILL block whose target is the last BFLAG_FIRST
    entry as a byte address, and takes each following block, FILL blocks
    included, while it starts where the previous one ends. A FILL block gives
    byte_count bytes of its 32-bit argument, little-endian."""
    firsts = entry_points(blocks)
    if not firsts:
        return None, b"", []
    entry = sw_to_byte(firsts[-1])
    start = next(
        (
            i
            for i, b in enumerate(blocks)
            if not b["fill"] and b["byte_count"] and b["target_address"] == entry
        ),
        None,
    )
    if start is None:
        return None, b"", []
    code, used, end = bytearray(), [], None
    for b in blocks[start:]:
        if end is not None and b["target_address"] != end:
            break
        if b["fill"]:
            pattern = struct.pack("<I", b["argument"])
            code += (pattern * (b["byte_count"] // 4 + 1))[: b["byte_count"]]
        else:
            code += data[b["payload_offset"] : b["payload_offset"] + b["payload_len"]]
        used.append(b["index"])
        end = b["target_address"] + b["byte_count"]
    return entry, bytes(code), used


def entropy(data):
    """Shannon entropy in bits/byte over `data`. 0.0 for empty input."""
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    ent = 0.0
    for c in counts:
        if c:
            p = c / n
            ent -= p * math.log2(p)
    return ent


def _entropy_annotation(value):
    lo, hi = APLIB_ENTROPY_BAND
    if lo <= value <= hi:
        return "(compressed?)"
    if value < ENTROPY_RAW_CEILING:
        return "(raw)"
    return ""


def alignment(data, min_count=8, gram=4, strides=(2, 4, 6, 8)):
    """Answer "what instruction width is this code?" from repeat offsets.

    Finds every `gram`-byte sequence occurring at least `min_count` times,
    collects all of its occurrence offsets, and for each stride in
    `strides` builds a histogram of `offset % stride` across all of those
    occurrences. Returns a dict mapping stride -> {residue: count}, plus a
    "total" key with the count of occurrences considered.

    A fixed-width instruction set concentrates repeated code motifs at ONE
    residue for its width: 48-bit SHARC code would be constant mod 6,
    32-bit code constant mod 4. Measured on the 10,312-byte loader payload
    of both images, repeated motifs land on EVEN offsets exclusively (the
    6-byte motif f29fc09f1200 occurs 56 times, all at offset % 2 == 0;
    3f083f34089c 38 times; an 11-byte motif 6 times) but spread roughly
    uniformly across mod 4, mod 6 and mod 8. That is 16-bit-granular
    variable-length encoding, which on SHARC+ is VISA. This is an
    inference from alignment statistics, NOT a decoded instruction -- no
    instruction has been decoded, and confirming it needs the SHARC+ Core
    Programming Reference.
    """
    seen = defaultdict(list)
    for i in range(len(data) - gram + 1):
        seen[data[i : i + gram]].append(i)
    occurrences = []
    for offs in seen.values():
        if len(offs) >= min_count:
            occurrences.extend(offs)
    result: dict[str | int, Any] = {}
    for stride in strides:
        hist = {r: 0 for r in range(stride)}
        for off in occurrences:
            hist[off % stride] += 1
        result[stride] = hist
    result["total"] = len(occurrences)
    return result


def _format_hist(hist, stride):
    return "{" + ",".join("%d:%d" % (r, hist[r]) for r in range(stride)) + "}"


def _is_float_like(word):
    try:
        f = struct.unpack("<f", struct.pack("<I", word))[0]
    except struct.error:
        return False
    if f != f:  # NaN
        return False
    af = abs(f)
    return 1e-6 < af < 1e6


def _classify_window(chunk):
    words = [
        struct.unpack("<I", chunk[i : i + 4])[0]
        for i in range(0, len(chunk) - (len(chunk) % 4), 4)
    ]
    n = len(words)
    if n == 0:
        return "other", 0.0, 0.0
    zeros = sum(1 for w in words if w == 0)
    floats = sum(1 for w in words if _is_float_like(w))
    pct_zero = 100.0 * zeros / n
    pct_float = 100.0 * floats / n
    if pct_zero > 90.0:
        kind = "zero"
    elif pct_float > 90.0:
        kind = "float"
    else:
        kind = "other"
    return kind, pct_float, pct_zero


def characterise(data, start, window):
    """Classify data[start:] in fixed-size windows, then coalesce runs."""
    windows = []
    offset = start
    while offset < len(data):
        chunk = data[offset : offset + window]
        kind, pct_float, pct_zero = _classify_window(chunk)
        windows.append(
            {
                "offset": offset,
                "length": len(chunk),
                "kind": kind,
                "pct_float": round(pct_float, 2),
                "pct_zero": round(pct_zero, 2),
            }
        )
        offset += len(chunk)

    regions: list[dict[str, Any]] = []
    for w in windows:
        if (
            regions
            and regions[-1]["kind"] == w["kind"]
            and regions[-1]["end"] == w["offset"]
        ):
            regions[-1]["end"] = w["offset"] + w["length"]
            regions[-1]["length"] = regions[-1]["end"] - regions[-1]["offset"]
        else:
            regions.append(
                {
                    "offset": w["offset"],
                    "end": w["offset"] + w["length"],
                    "length": w["length"],
                    "kind": w["kind"],
                }
            )
    for r in regions:
        r["entropy"] = round(entropy(data[r["offset"] : r["end"]]), 3)
    return windows, regions


def summarise(path, data, blocks, stop_offset):
    header_bytes = len(blocks) * HEADER_LEN
    payload_bytes = sum(b["payload_len"] for b in blocks if not b["fill"])
    fill_bytes = sum(b["byte_count"] for b in blocks if b["fill"])
    return {
        "path": path,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "entropy": round(entropy(data), 3),
        "block_count": len(blocks),
        "header_bytes": header_bytes,
        "payload_bytes": payload_bytes,
        "fill_bytes": fill_bytes,
        "parse_stopped_at": stop_offset,
        "unparsed_bytes": len(data) - stop_offset,
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob", help="path to a section_7_*.bin")
    ap.add_argument(
        "--window", type=int, default=4096, help="characterisation window size in bytes"
    )
    ap.add_argument("--json", help="write the full result dict as JSON")
    ap.add_argument("--dump-blocks", help="write each block's payload here")
    ap.add_argument(
        "--main", help="write the final application's code region to this file"
    )
    ap.add_argument(
        "--align",
        action="store_true",
        help="run alignment() over regions and block payloads",
    )
    ap.add_argument(
        "--addr",
        action="append",
        default=[],
        help="address to resolve to a file offset (hex 0x... or decimal); repeatable",
    )
    ap.add_argument(
        "--addr-space",
        choices=("sw", "byte"),
        default="sw",
        help="how to interpret --addr values (default: sw)",
    )
    args = ap.parse_args()

    with open(args.blob, "rb") as f:
        data = f.read()
    blocks = parse_blocks(data)
    stop_offset = (
        blocks[-1]["payload_offset"] + blocks[-1]["payload_len"] if blocks else 0
    )
    windows, regions = characterise(data, stop_offset, args.window)
    summary = summarise(args.blob, data, blocks, stop_offset)

    if args.dump_blocks:
        os.makedirs(args.dump_blocks, exist_ok=True)
        for b in blocks:
            if b["fill"]:
                continue
            name = "blk%02d_%08x.bin" % (b["index"], b["target_address"])
            with open(os.path.join(args.dump_blocks, name), "wb") as f:
                f.write(
                    data[b["payload_offset"] : b["payload_offset"] + b["payload_len"]]
                )

    if args.main:
        base, code, used = main_program(data, blocks)
        if base is None:
            raise SystemExit("no code region at the last BFLAG_FIRST entry")
        os.makedirs(os.path.dirname(os.path.abspath(args.main)), exist_ok=True)
        with open(args.main, "wb") as f:
            f.write(code)
        print(
            "main program: %d bytes at byte address 0x%x, sha256 %s, blocks %s -> %s"
            % (len(code), base, hashlib.sha256(code).hexdigest(), used, args.main)
        )

    result = {
        "summary": summary,
        "blocks": blocks,
        "windows": windows,
        "regions": regions,
    }

    alignment_results: dict[str, Any] = {}
    if args.align:
        alignment_results = {"regions": [], "blocks": []}
        for r in regions:
            if r["kind"] == "zero":
                continue
            span = data[r["offset"] : r["end"]]
            alignment_results["regions"].append(
                {
                    "label": "%s@%d..%d" % (r["kind"], r["offset"], r["end"]),
                    "length": len(span),
                    "alignment": alignment(span),
                }
            )
        for b in blocks:
            if b["fill"]:
                continue
            span = data[b["payload_offset"] : b["payload_offset"] + b["payload_len"]]
            alignment_results["blocks"].append(
                {
                    "label": "blk%02d" % b["index"],
                    "length": len(span),
                    "alignment": alignment(span),
                }
            )
        result["alignment"] = alignment_results

    if args.json:
        with open(args.json, "w") as f:
            json.dump(result, f, indent=2)

    print("=== %s ===" % args.blob)
    print(
        "size=%d  sha256=%s  entropy=%.3f %s"
        % (
            summary["size"],
            summary["sha256"],
            summary["entropy"],
            _entropy_annotation(summary["entropy"]),
        )
    )
    print(
        "blocks=%d  header_bytes=%d  payload_bytes=%d  fill_bytes=%d"
        % (
            summary["block_count"],
            summary["header_bytes"],
            summary["payload_bytes"],
            summary["fill_bytes"],
        )
    )
    print(
        "parse_stopped_at=%d  unparsed_bytes=%d"
        % (summary["parse_stopped_at"], summary["unparsed_bytes"])
    )

    print("\n--- blocks ---")
    for b in blocks:
        print(
            "blk%02d  @%-6d  code=0x%08x  addr=0x%08x  cnt=%-6d  arg=0x%08x  core=%s  %s"
            % (
                b["index"],
                b["offset"],
                b["block_code"],
                b["target_address"],
                b["byte_count"],
                b["argument"],
                b["core"] if b["core"] is not None else "?",
                ",".join(b["flags"]) or "-",
            )
        )

    print("\n--- entry points (BFLAG_FIRST) ---")
    for ep in entry_points(blocks):
        print("sw=0x%x  byte=0x%x" % (ep, sw_to_byte(ep)))

    print("\n--- regions ---")
    for r in regions:
        print(
            "%-6s %8d..%-8d  (%d KB)  entropy=%.3f %s"
            % (
                r["kind"],
                r["offset"],
                r["end"],
                r["length"] // 1024,
                r["entropy"],
                _entropy_annotation(r["entropy"]),
            )
        )

    if args.align:
        print("\n--- alignment ---")
        for item in alignment_results["regions"] + alignment_results["blocks"]:
            a = item["alignment"]
            hist_str = "  ".join(
                "mod%d=%s" % (stride, _format_hist(a[stride], stride))
                for stride in sorted(k for k in a if k != "total")
            )
            print(
                "%-20s len=%-8d n=%-6d %s"
                % (item["label"], item["length"], a["total"], hist_str)
            )

    if args.addr:
        print("\n--- addr ---")
        for addr_str in args.addr:
            addr = int(addr_str, 0)
            offset = offset_for_address(blocks, addr, space=args.addr_space)
            print("addr=0x%x (%s)" % (addr, args.addr_space))
            if offset is None:
                fill = fill_block_for_address(blocks, addr, space=args.addr_space)
                if fill is None:
                    print("  not covered by any loaded block")
                else:
                    print(
                        "  no file bytes: covered by %s blk%02d  fill=0x%08x"
                        "  range=0x%x..0x%x"
                        % (
                            "zero-fill" if fill["argument"] == 0 else "constant-fill",
                            fill["index"],
                            fill["argument"],
                            fill["target_address"],
                            fill["target_address"] + fill["byte_count"],
                        )
                    )
                continue
            block = None
            for b in blocks:
                if (
                    not b["fill"]
                    and b["payload_offset"]
                    <= offset
                    < b["payload_offset"] + b["payload_len"]
                ):
                    block = b
                    break
            print(
                "  offset=0x%x  block=blk%02d"
                % (offset, block["index"] if block else -1)
            )
            chunk = data[offset : offset + 32]
            print("  " + " ".join("%02x" % byte for byte in chunk))

    return 0


if __name__ == "__main__":
    sys.exit(main())
