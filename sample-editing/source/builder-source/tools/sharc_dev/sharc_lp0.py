"""Feed a captured ColdFire FlexBus stream into the SHARC's link port 0.

    uv run python -m tools.sharc_dev.sharc_lp0 feed IMAGE LOG [--report OUT.json]
    uv run python -m tools.sharc_dev.sharc_lp0 synth NATIVE_FILE --slot N -o LOG

The ColdFire loads samples into SHARC memory through FlexBus `0x8C000002`,
which drives the SHARC's link port 0 (ColdFire `FUN_40154540` ->
`FUN_400cd638`; SHARC `FUN_1c7ec5` sets up the receive). This module turns a log of those FlexBus writes back into
link-port words and delivers each 0x401-word transfer the way the DSP's own
driver would: the words land in the receive buffer the firmware's
descriptor names, and the firmware's own completion callback (`FUN_1c7e1f`)
runs. That callback does the rest:

- tag word `0xFFFFFFFF`: slot header `{slot, startL, startR, rate, len}`;
  `FUN_1c3fe5` stores `{startL, startR, len, rate, stereo}` at
  `0x257810 + slot*0x14` and `FUN_1c400f` copies 5 guard words past each
  channel's end.
- tag word below `0x19200`: a 4 KiB page; `FUN_1c403c` copies it to SDRAM
  `0x8422b9c8 + page*0x1000`.

Log format (the input to `feed`, and what `synth` writes). One record per
16-bit CPU write to FlexBus `0x8C000002`, in program order:

- JSONL (any extension but `.bin` or `.raw`): `{"addr": A, "value": V}` per line,
  A and V as integers or hex strings. V is the halfword written: the data
  byte is `V >> 8`, bit 7 is the link-port clock. Other keys are ignored.
  A line may give `"byte": B` instead of `"value"`: an already latched
  byte.
- binary (`.bin`): 6-byte records, little-endian `u32 addr, u16 value`.
- raw (`.raw`): the latched bytes themselves, in wire order, as
  `unicorn-reference-final:tools/guirun.py --flexbus-log` records them (`unicorn-reference-final:emu/dsp.py`).

Records for other addresses (such as the `0x8C00000A` latch writes) are
skipped. A byte is latched on the clock's falling edge (a write with bit 7
clear after one with bit 7 set, the link-port protocol's falling-edge
latch), and bytes pack into words least significant first
(ADSP-2156x HWR Figure 14-3; the ColdFire sender `FUN_400ccda0` sends the
low byte first).

Modelled by hand, not by the DSP's code: the DMA engine itself (the
words are written straight into the buffer), the driver's interrupt
dispatch (`0x1c8705` -> `FUN_1c85ba` -> callback, reproduced as a direct
callback call with the event code the driver passes, 4), and the
driver's re-submit (`FUN_1c834a`): the callback runs until it calls that,
then the interrupted context's registers are restored, as an interrupt
return would.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import struct
import sys
from collections.abc import Iterable, Iterator

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc_harness as h  # noqa: E402
from tools.sharc_dev import sharc_run as sr  # noqa: E402
from tools.sharc_dev import sharc_trace as st  # noqa: E402

FLEXBUS_DATA = 0x8C000002  # 16-bit: data in bits 15:8, clock in bit 7
CLOCK = 0x80
HEADER_TAG = 0xFFFFFFFF
PAGE_BYTES = 0x1000
PAGE_WORDS = PAGE_BYTES // 4
# Firmware side of the ColdFire: page = (dest - 0x400) >> 12 (FUN_40153734).
COLDFIRE_BLOCK_BASE = 0x400


@dataclasses.dataclass(frozen=True)
class Lp0Profile:
    """LP0 receive path addresses in one SHARC image."""

    arm_start: int  # FUN_1c7ec5's descriptor setup, after the driver open
    arm_stop: int  # FUN_1c83ff: registers the callback; not modelled
    descriptor: int  # DMA descriptor the driver is given (FUN_1c834a's R8)
    callback: int  # the callback FUN_1c83ff registers
    resubmit: int  # FUN_1c834a: the callback's re-submit to the driver
    event_buffer_processed: int  # R8 the driver passes (FUN_1c85ba)
    slot_table: int  # FUN_1c3fe5 / FUN_1c3f78
    slot_stride: int
    slot_count: int
    default_slot: int  # FUN_1c3f78's record for slot >= slot_count
    sample_base: int  # FUN_1c403c / FUN_1c400f / FUN_1c3f78
    max_page: int  # FUN_1c7e1f / FUN_1c403c accept page < max_page
    slot_reader: int  # FUN_1c3f78(R4=slot, R1=out)


PROFILES = {
    "dt2-1.16": Lp0Profile(
        arm_start=0x1C7ED7,
        arm_stop=0x1C83FF,
        descriptor=0x268220,
        callback=0x1C7E1F,
        resubmit=0x1C834A,
        event_buffer_processed=4,
        slot_table=0x257810,
        slot_stride=0x14,
        slot_count=0x401,
        default_slot=0x25C914,
        sample_base=0x8422B9C8,
        max_page=0x19200,
        slot_reader=0x1C3F78,
    ),
}


def profile(image: str) -> Lp0Profile:
    try:
        return PROFILES[image]
    except KeyError:
        raise ValueError("no LP0 profile for image %r" % image) from None


# --- the wire ---------------------------------------------------------------


def _int(value) -> int:
    return int(value, 0) if isinstance(value, str) else int(value)


def read_log(path: str) -> Iterator[tuple[int, int, bool]]:
    """Yield `(addr, value, latched)` per record; `latched` is True for a
    JSONL `"byte"` or `.raw` record, whose value is the byte itself."""
    if path.endswith(".raw"):
        with open(path, "rb") as fh:
            for b in fh.read():
                yield FLEXBUS_DATA, b, True
        return
    if path.endswith(".bin"):
        with open(path, "rb") as fh:
            data = fh.read()
        if len(data) % 6:
            raise ValueError(
                "%s: %d bytes is not a whole number of records" % (path, len(data))
            )
        for addr, value in struct.iter_unpack("<IH", data):
            yield addr, value, False
        return
    with open(path) as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if "byte" in rec:
                yield _int(rec["addr"]), _int(rec["byte"]), True
            elif "value" in rec:
                yield _int(rec["addr"]), _int(rec["value"]), False
            else:
                raise ValueError("%s:%d: record has neither value nor byte" % (path, n))


def latch_bytes(records: Iterable[tuple[int, int, bool]]) -> bytes:
    """The bytes the link port latches from RECORDS (see module docstring)."""
    out = bytearray()
    clock_high = False
    for addr, value, latched in records:
        if addr != FLEXBUS_DATA:
            continue
        if latched:
            out.append(value & 0xFF)
            continue
        high = bool(value & CLOCK)
        if clock_high and not high:
            out.append((value >> 8) & 0xFF)
        clock_high = high
    return bytes(out)


def pack_words(data: bytes) -> tuple[list[int], bytes]:
    """Little-endian 32-bit words, and any trailing partial word."""
    whole = len(data) - len(data) % 4
    return list(struct.unpack("<%dI" % (whole // 4), data[:whole])), data[whole:]


def encode_word_writes(word: int, swap16: bool = False) -> list[tuple[int, int]]:
    """The FlexBus writes the ColdFire makes for one word: `FUN_400ccda0`
    (low byte first), or `FUN_400cce2c` when SWAP16 (each big-endian int16
    half of the word as it sits in ColdFire memory, low byte first, so
    big-endian PCM arrives little-endian)."""
    be = struct.pack(">I", word & 0xFFFFFFFF)
    order = (be[1], be[0], be[3], be[2]) if swap16 else (be[3], be[2], be[1], be[0])
    writes = []
    for b in order:
        writes.append((FLEXBUS_DATA, (b << 8) | CLOCK))
        writes.append((FLEXBUS_DATA, b << 8))
    return writes


def encode_transfer(tag: int, block: bytes, swap16: bool) -> list[tuple[int, int]]:
    """`FUN_400cd638(tag, block, hdr)`: the tag word, then 4096 bytes read
    as big-endian ColdFire words (hdr=1: swap16 False; hdr=0: True)."""
    if len(block) != PAGE_BYTES:
        raise ValueError("a transfer carries exactly %d bytes" % PAGE_BYTES)
    writes = encode_word_writes(tag)
    for (word,) in struct.iter_unpack(">I", block):
        writes.extend(encode_word_writes(word, swap16))
    return writes


def native_sample_transfers(
    native: bytes, slot: int, dest: int = COLDFIRE_BLOCK_BASE
) -> list[tuple[int, bytes, bool]]:
    """The `(tag, block, swap16)` transfers `FUN_40154540` sends for one
    native sample file (docs/findings/14's native format: 64-byte header,
    big-endian int16 PCM, 16-byte trailer) loaded at ColdFire sample-memory
    offset DEST: a reset header, the pages, then the slot header.

    Stereo files are split into two halves of 16-bit units (even units to
    the left half, odd to the right; FUN_4015358c), so each half starts
    with 0x20 header bytes. The page order within the stream is a
    simplification (whole buffer, ascending); the SHARC side does not
    depend on it."""
    if len(native) < 0x40:
        raise ValueError("native sample shorter than its 64-byte header")
    stereo = native[1] == 1
    length = struct.unpack_from(">I", native, 4)[0]
    rate = struct.unpack_from(">I", native, 8)[0]
    size = len(native)
    alloc = (size + 0x200F) & ~0x1FFF
    buf = bytearray(alloc)
    if stereo:
        units = native[: size - size % 4]
        left = b"".join(units[i : i + 2] for i in range(0, len(units), 4))
        right = b"".join(units[i + 2 : i + 4] for i in range(0, len(units), 4))
        buf[: len(left)] = left
        buf[alloc // 2 : alloc // 2 + len(right)] = right
        start_l = dest + 0x20 - COLDFIRE_BLOCK_BASE
        start_r = dest + alloc // 2 + 0x20 - COLDFIRE_BLOCK_BASE
        chan_len = length >> 1
    else:
        buf[:size] = native
        start_l = start_r = dest + 0x40 - COLDFIRE_BLOCK_BASE
        chan_len = length
    first_page = (dest - COLDFIRE_BLOCK_BASE) >> 12

    def header(fields: tuple[int, ...]) -> bytes:
        return struct.pack(">5I", *fields).ljust(PAGE_BYTES, b"\0")

    out = [(HEADER_TAG, header((slot, 0xFFFFFC00, 0xFFFFFC00, 0, 0)), False)]
    for i in range(alloc // PAGE_BYTES):
        out.append(
            (first_page + i, bytes(buf[i * PAGE_BYTES : (i + 1) * PAGE_BYTES]), True)
        )
    out.append(
        (
            HEADER_TAG,
            header((slot, start_l & 0xFFFFFFFF, start_r, rate, chan_len)),
            False,
        )
    )
    return out


def write_log(path: str, writes: Iterable[tuple[int, int]]) -> int:
    n = 0
    if path.endswith(".bin"):
        with open(path, "wb") as fh:
            for addr, value in writes:
                fh.write(struct.pack("<IH", addr, value))
                n += 1
        return n
    with open(path, "w") as fh:
        for addr, value in writes:
            fh.write('{"addr": "%#x", "value": "%#06x"}\n' % (addr, value))
            n += 1
    return n


# --- the SHARC side -----------------------------------------------------------


def arm(runner: sr.Runner, image: str) -> sr.Runner:
    """Run the firmware's own receive-descriptor setup (the tail of
    `FUN_1c7ec5`, after its driver open) and stop at the driver call that
    registers the callback. The driver open itself (`FUN_1c8895`) is not
    run: it programs SEC/DMA registers the harness does not model."""
    p = profile(image)
    new = runner.fresh_call(p.arm_start, diagnose_unknown=True)
    new.breakpoints = frozenset({p.arm_stop})
    result = new.run(max_steps=64)
    if result.halt.reason != "breakpoint" or result.halt.pc_sw != p.arm_stop:
        raise RuntimeError("LP0 arm did not reach %#x: %s" % (p.arm_stop, result.halt))
    new.state = dataclasses.replace(new.state, uregs=dict(runner.state.uregs))
    new.breakpoints = runner.breakpoints
    return new


def descriptor(state, image: str) -> dict[str, int]:
    """The receive descriptor the callback re-submits: ADSP-2156x HWR
    descriptor-array order (DSCPTR_NXT, ADDRSTART, CFG, XCNT, XMOD)."""
    base = profile(image).descriptor
    names = ("dscptr_nxt", "addrstart", "cfg", "xcnt", "xmod")
    return {name: h._dm_word(state, base + 4 * i) for i, name in enumerate(names)}


def deliver(runner: sr.Runner, image: str, words: list[int]) -> tuple[sr.Runner, int]:
    """Write one transfer at the descriptor's buffer and run the callback
    up to its re-submit. Returns the new Runner and the instructions run."""
    p = profile(image)
    desc = descriptor(runner.state, image)
    if desc["addrstart"] == 0 or desc["xcnt"] == 0:
        raise RuntimeError("LP0 is not armed (descriptor %r); call arm() first" % desc)
    if len(words) != desc["xcnt"]:
        raise ValueError(
            "transfer of %d words, descriptor XCNT %d" % (len(words), desc["xcnt"])
        )
    for i, word in enumerate(words):
        h._poke(runner.state, desc["addrstart"] + i * desc["xmod"], word)
    new = runner.fresh_call(
        p.callback,
        regs={"R4": 0, "R8": p.event_buffer_processed, "R12": p.descriptor},
        diagnose_unknown=True,
    )
    new.breakpoints = frozenset({p.resubmit})
    result = new.run(max_steps=20_000)
    if result.halt.reason != "breakpoint" or result.halt.pc_sw != p.resubmit:
        raise RuntimeError(
            "LP0 callback %#x did not reach its re-submit %#x: %s"
            % (p.callback, p.resubmit, result.halt)
        )
    new.state = dataclasses.replace(new.state, uregs=dict(runner.state.uregs))
    new.breakpoints = runner.breakpoints
    return new, result.instructions


def slot_record(state, image: str, slot: int) -> dict[str, int]:
    """The raw table record FUN_1c3fe5 wrote for SLOT."""
    p = profile(image)
    base = p.slot_table + slot * p.slot_stride
    return {
        "start_l": h._dm_word(state, base),
        "start_r": h._dm_word(state, base + 4),
        "len": h._dm_word(state, base + 8),
        "rate": h._dm_word(state, base + 12),
        "stereo": h._dm_word(state, base + 16) & 0xFF,
    }


def read_slot(
    runner: sr.Runner, image: str, slot: int, out: int = 0x2E0000
) -> dict[str, int]:
    """Call the firmware's slot reader FUN_1c3f78(slot, out) -- what the
    voice path (FUN_1c3289, FUN_1c2ac9) gets for SLOT."""
    p = profile(image)
    new = runner.fresh_call(
        p.slot_reader, regs={"R4": slot, "R1": out}, diagnose_unknown=True
    )
    result = new.run(max_steps=500)
    if result.halt.reason != "return without followed call":
        raise RuntimeError("FUN_1c3f78 did not return: %s" % result.halt)
    names = ("addr_l", "addr_r", "frames", "rate")
    rec = {name: h._dm_word(new.state, out + 4 * i) for i, name in enumerate(names)}
    rec["stereo"] = h._dm_word(new.state, out + 16) & 0xFF
    return rec


def read_bytes(state, address: int, n: int) -> bytes:
    """N bytes of SHARC memory; unwritten bytes read as 0."""
    out = bytearray()
    for i in range(n):
        raw = st._dm_read(state, address + i, 1)
        out.append(raw.value & 0xFF if raw is not None else 0)
    return bytes(out)


def feed(runner: sr.Runner, image: str, records: Iterable[tuple[int, int, bool]]):
    """Arm LP0 and deliver every whole transfer in RECORDS. Returns the new
    Runner and a report dict."""
    p = profile(image)
    words, tail = pack_words(latch_bytes(records))
    runner = arm(runner, image)
    n = descriptor(runner.state, image)["xcnt"]
    pages: list[int] = []
    headers: list[dict[str, int]] = []
    ignored: list[int] = []
    instructions = 0
    whole = len(words) - len(words) % n
    for i in range(0, whole, n):
        transfer = words[i : i + n]
        tag = transfer[0]
        runner, count = deliver(runner, image, transfer)
        instructions += count
        if tag == HEADER_TAG:
            names = ("slot", "start_l", "start_r", "rate", "len")
            headers.append(dict(zip(names, transfer[1:6], strict=True)))
        elif tag < p.max_page:
            pages.append(tag)
        else:
            ignored.append(tag)
    slots = sorted({hd["slot"] for hd in headers if hd["slot"] < p.slot_count})
    report = {
        "words": len(words),
        "transfers": whole // n,
        "leftover_words": len(words) - whole,
        "leftover_bytes": len(tail),
        "pages": len(pages),
        "page_range": [min(pages), max(pages)] if pages else None,
        "headers": headers,
        "ignored_tags": ignored,
        "callback_instructions": instructions,
        "slots": {slot: slot_record(runner.state, image, slot) for slot in slots},
        "slot_reader": {slot: read_slot(runner, image, slot) for slot in slots},
    }
    return runner, report


# --- CLI ---------------------------------------------------------------------


def _hexify(value):
    if isinstance(value, dict):
        return {str(k): _hexify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_hexify(v) for v in value]
    if isinstance(value, int) and not isinstance(value, bool) and value > 9:
        return "%#x" % value
    return value


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("feed", help="run init, then feed LOG through LP0")
    f.add_argument("image")
    f.add_argument("log")
    f.add_argument("--report", help="write the report as JSON")
    f.add_argument(
        "--dump",
        action="append",
        default=[],
        metavar="ADDR:N",
        help="also print N bytes of SHARC memory at ADDR",
    )
    s = sub.add_parser("synth", help="write the log FUN_40154540 would send")
    s.add_argument("native", help="a native sample file (docs/findings/14)")
    s.add_argument("--slot", type=lambda v: int(v, 0), required=True)
    s.add_argument("--dest", type=lambda v: int(v, 0), default=COLDFIRE_BLOCK_BASE)
    s.add_argument("-o", "--out", required=True, help="LOG path (.bin or JSONL)")
    args = ap.parse_args(argv)

    if args.cmd == "synth":
        with open(args.native, "rb") as fh:
            native = fh.read()
        transfers = native_sample_transfers(native, args.slot, args.dest)
        n = write_log(
            args.out,
            (
                w
                for tag, block, swap in transfers
                for w in encode_transfer(tag, block, swap)
            ),
        )
        print("%s: %d transfers, %d writes" % (args.out, len(transfers), n))
        return 0

    memory = h.load_image_memory(args.image)
    init = h.run_init(memory, args.image)
    if not init.ran:
        print("run_init failed: %s" % init.error)
        return 1
    runner = h.new_runner(memory, args.image, init=init)
    runner, report = feed(runner, args.image, read_log(args.log))
    dumps = {}
    for spec in args.dump:
        addr, _, count = spec.partition(":")
        dumps[addr] = read_bytes(runner.state, int(addr, 0), int(count, 0)).hex()
    report["dumps"] = dumps
    out = _hexify(report)
    print(json.dumps({k: v for k, v in out.items() if k != "headers"}, indent=1))
    if args.report:
        with open(args.report, "w") as fh:
            json.dump(out, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
