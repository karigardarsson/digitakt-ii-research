"""Opt-in capture of real ColdFire<->SHARC traffic, for tools/sharc_dev/sharc_replay.py.

Four independent things a run can record, all off unless a caller wires
them in (nothing here is installed by `unicorn-reference-final:emu/longrun.py`'s `build()` on its own):

- Every DSPI2 frame the ColdFire's driver call builds and sends toward the
  SHARC (docs/findings/04-coldfire-dsp-link.md, "The ColdFire tells the
  SHARC through a periodic DSPI2 frame"): the TX bytes it actually built,
  and the RX bytes it got back (always silence here -- see `CapturingPeer`
  below -- since nothing in this repo yet runs a real SHARC to answer).
- Every SSI0 "audio in" request (`unicorn-reference-final:emu/ssi.py`'s `peer.rx(nbytes)` hook):
  size and cadence only, again always silence, since real input hardware
  is unmodeled (this matches `tools/sharc_dev/sharc_proc.py`'s own
  `audio_mode="silence"` convention).
- Every call to the DSPI1 driver (`FUN_400cd48a` on DT2 1.16, docs/findings/
  04-coldfire-dsp-link.md "eDMA and DSPI transfer inventory", channel 14):
  a candidate second ColdFire<->something link this lane tested and ruled
  out as a note/sample-data transport to the SHARC (its TX/RX staging pair,
  `0x4fe7a340`/`0x4fe79340`, sits in the ColdFire's own external SDRAM, far
  from any `0x80xxxxxx`-range address the SHARC program references, and
  the driver uses a *different* physical DSPI block, `0xfc03c034`, than the
  SHARC's own `0xec038034`) -- kept as a record type so a future run can
  reopen the question with evidence instead of re-deriving the address.
  Observational only (`unicorn-reference-final:tools/sharc_capture_run.py`'s hook does not alter
  control flow, unlike the DSPI2 driver hook below, which must fake a reply
  since nothing here answers as a real SHARC would): logs the call's own
  three arguments (length, source pointer or 0, completion callback), never
  the peripheral's behaviour.
- Any write into a watched raw memory range (`unicorn-reference-final:tools/sharc_capture_run.py`'s
  `--watch-mem LO:HI`): this lane's other candidate for a shared/bulk
  transport, the 16-byte FlexBus window at `0x8c000000` (docs/findings/04,
  "Ruled out as the control link"; confirmed on 1.16 by `tools/coldfire/refscan.py`
  to be the SHARC program loader's own boot-time GPIO/status handshake,
  `FUN_400ccda0`/`FUN_400ccf74`, not a sample-data path -- see the finding's
  1.16 correction). Generic by address range, not tied to FlexBus, so the
  same mechanism can watch any other candidate window later.

`CapturingPeer` implements both `unicorn-reference-final:emu/dspi2.py`'s peer contract
(`.exchange`) and `unicorn-reference-final:emu/ssi.py`'s (`.rx`/`.tx`), the same way
`tools.sharc_dev.sharc_peer.SharcServerPeer`
answers both with one object -- so one instance can be wired to both
`unicorn-reference-final:emu/longrun.py`'s `build(dspi2_peer=..., ssi0_peer=...)` parameters, or driven
directly from a hand-installed driver-call hook (see
`unicorn-reference-final:tools/sharc_capture_run.py`, which does the latter: hooking the DSPI2
driver call directly, the way `unicorn-reference-final:tools/sharcframe.py` already does, rather
than the eDMA/DSPI2 register model, since nothing about capturing needs the
transport modeled -- only what crossed it).

## File format (not committed -- firmware-derived; keep captures under
`out/captures/`, which `.gitignore` should already exclude via `out/`)

    magic    8 bytes   b"DT2CAP1\\n"
    header   u32 length, then that many bytes of UTF-8 JSON:
             {"frame_bytes": 2748, "kind": "idle"|"note"|"play", "device": "dt2",
              "source_sha256": "...", ...caller-supplied metadata}
    then records, back to back, each:
        type     1 byte    1=DSPI2 TX  2=DSPI2 RX  3=SSI0 RX
                           4=DSPI1 call  5=watched memory write
        instr    8 bytes   big-endian ColdFire instruction count, or
                           0xFFFFFFFFFFFFFFFF when not known
        length   4 bytes   big-endian payload length
        payload  `length` bytes
    to EOF. A DSPI2 TX record is always immediately followed by its RX
    record (one `write_dspi2()` call writes both) -- `load()` pairs them
    positionally and raises if that invariant is violated by a
    hand-truncated file. Type 4's payload is `>III` (length, source pointer,
    callback), a fixed 12 bytes. Type 5's payload is `>I` (guest address)
    followed by the bytes written, so its length varies with the access
    width. Readers of an old capture never see types 4/5: they were not
    written before this format extension, so the file simply has none, and
    `load()` leaves the new `Capture.dspi1_calls`/`mem_writes` lists empty
    -- old captures stay readable exactly as before.

`instr` is whatever the caller's `counter` callable returns, e.g. a
`spin()`-driven run's `pits.now` -- accurate to the last scheduling
deadline the writer's own run stepped to, not to the exact instruction (see
`unicorn-reference-final:tools/sharc_capture_run.py`), which is enough to place a frame in the
run's rough timeline without paying for an instruction-exact hook.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field

MAGIC = b"DT2CAP1\n"

REC_DSPI2_TX = 1
REC_DSPI2_RX = 2
REC_SSI0_RX = 3
REC_DSPI1_CALL = 4
REC_MEM_WRITE = 5

_NO_COUNT = 0xFFFFFFFFFFFFFFFF
_REC_HEADER = struct.Struct(">BQI")
_DSPI1_CALL = struct.Struct(">III")  # length, src pointer (0 if none), callback
_MEM_WRITE_ADDR = struct.Struct(">I")

VALID_KINDS = ("idle", "note", "play")


class CaptureWriter:
    """Writes one capture file (see the module docstring for the format).

    `counts` is a running tally (`dspi2_frames`, `ssi0_rx`, `dspi1_calls`,
    `mem_writes`) a driver can print without re-reading the file back.
    """

    def __init__(
        self,
        path: str,
        *,
        frame_bytes: int,
        kind: str,
        device: str = "dt2",
        source_sha256: str | None = None,
        extra: dict | None = None,
    ) -> None:
        if kind not in VALID_KINDS:
            raise ValueError("kind must be one of %r, got %r" % (VALID_KINDS, kind))
        self.path = path
        self._fh = open(path, "wb")  # noqa: SIM115 -- held open for the writer's life
        meta = {
            "frame_bytes": frame_bytes,
            "kind": kind,
            "device": device,
            "source_sha256": source_sha256,
        }
        if extra:
            meta.update(extra)
        body = json.dumps(meta).encode("utf-8")
        self._fh.write(MAGIC)
        self._fh.write(struct.pack(">I", len(body)))
        self._fh.write(body)
        self.counts = {
            "dspi2_frames": 0,
            "ssi0_rx": 0,
            "dspi1_calls": 0,
            "mem_writes": 0,
        }

    def _write(self, rec_type: int, instr_count, payload: bytes) -> None:
        payload = bytes(payload)
        count = _NO_COUNT if instr_count is None else int(instr_count) & _NO_COUNT
        self._fh.write(_REC_HEADER.pack(rec_type, count, len(payload)))
        self._fh.write(payload)

    def write_dspi2(self, instr_count, tx: bytes, rx: bytes) -> None:
        self._write(REC_DSPI2_TX, instr_count, tx)
        self._write(REC_DSPI2_RX, instr_count, rx)
        self.counts["dspi2_frames"] += 1

    def write_ssi0_rx(self, instr_count, data: bytes) -> None:
        self._write(REC_SSI0_RX, instr_count, data)
        self.counts["ssi0_rx"] += 1

    def write_dspi1_call(
        self, instr_count, length: int, src: int, callback: int
    ) -> None:
        """Record one observed call to the DSPI1 driver (see the module
        docstring): the call's own arguments only, never a reply -- this
        record type is observational (`unicorn-reference-final:tools/sharc_capture_run.py`'s hook
        does not intercept the call), unlike a DSPI2 TX/RX pair."""
        self._write(
            REC_DSPI1_CALL, instr_count, _DSPI1_CALL.pack(length, src, callback)
        )
        self.counts["dspi1_calls"] += 1

    def write_mem_write(self, instr_count, address: int, data: bytes) -> None:
        """Record one write the guest made into a watched raw address range
        (`unicorn-reference-final:tools/sharc_capture_run.py --watch-mem`)."""
        self._write(
            REC_MEM_WRITE, instr_count, _MEM_WRITE_ADDR.pack(address) + bytes(data)
        )
        self.counts["mem_writes"] += 1

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> CaptureWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


@dataclass(frozen=True)
class Dspi2Frame:
    instr_count: int | None
    tx: bytes
    rx: bytes


@dataclass(frozen=True)
class Ssi0Rx:
    instr_count: int | None
    data: bytes


@dataclass(frozen=True)
class Dspi1Call:
    instr_count: int | None
    length: int
    src: int
    callback: int


@dataclass(frozen=True)
class MemWrite:
    instr_count: int | None
    address: int
    data: bytes


@dataclass
class Capture:
    frame_bytes: int
    kind: str
    device: str
    source_sha256: str | None
    meta: dict = field(default_factory=dict)
    dspi2_frames: list[Dspi2Frame] = field(default_factory=list)
    ssi0_rx: list[Ssi0Rx] = field(default_factory=list)
    dspi1_calls: list[Dspi1Call] = field(default_factory=list)
    mem_writes: list[MemWrite] = field(default_factory=list)


def load(path: str) -> Capture:
    """The inverse of `CaptureWriter`: read PATH back into a `Capture`."""
    with open(path, "rb") as fh:
        magic = fh.read(len(MAGIC))
        if magic != MAGIC:
            raise ValueError("%s: not a capture file (bad magic %r)" % (path, magic))
        (hlen,) = struct.unpack(">I", fh.read(4))
        meta = json.loads(fh.read(hlen).decode("utf-8"))
        dspi2_frames: list[Dspi2Frame] = []
        ssi0_rx: list[Ssi0Rx] = []
        dspi1_calls: list[Dspi1Call] = []
        mem_writes: list[MemWrite] = []
        pending_tx = None
        while True:
            header = fh.read(_REC_HEADER.size)
            if not header:
                break
            if len(header) != _REC_HEADER.size:
                raise ValueError("%s: truncated record header" % path)
            rec_type, count, length = _REC_HEADER.unpack(header)
            payload = fh.read(length)
            if len(payload) != length:
                raise ValueError("%s: truncated record payload" % path)
            instr = None if count == _NO_COUNT else count
            if rec_type == REC_DSPI2_TX:
                if pending_tx is not None:
                    raise ValueError(
                        "%s: two DSPI2 TX records with no RX between them" % path
                    )
                pending_tx = (instr, payload)
            elif rec_type == REC_DSPI2_RX:
                if pending_tx is None:
                    raise ValueError("%s: DSPI2 RX record with no preceding TX" % path)
                tx_instr, tx = pending_tx
                dspi2_frames.append(Dspi2Frame(tx_instr, tx, payload))
                pending_tx = None
            elif rec_type == REC_SSI0_RX:
                ssi0_rx.append(Ssi0Rx(instr, payload))
            elif rec_type == REC_DSPI1_CALL:
                if len(payload) != _DSPI1_CALL.size:
                    raise ValueError("%s: bad DSPI1 call record length" % path)
                length_, src, callback = _DSPI1_CALL.unpack(payload)
                dspi1_calls.append(Dspi1Call(instr, length_, src, callback))
            elif rec_type == REC_MEM_WRITE:
                if len(payload) < _MEM_WRITE_ADDR.size:
                    raise ValueError("%s: bad memory-write record length" % path)
                (address,) = _MEM_WRITE_ADDR.unpack(payload[: _MEM_WRITE_ADDR.size])
                mem_writes.append(
                    MemWrite(instr, address, payload[_MEM_WRITE_ADDR.size :])
                )
            else:
                raise ValueError("%s: unknown record type %d" % (path, rec_type))
        if pending_tx is not None:
            raise ValueError("%s: DSPI2 TX record with no matching RX" % path)
        return Capture(
            frame_bytes=meta.get("frame_bytes"),
            kind=meta.get("kind"),
            device=meta.get("device"),
            source_sha256=meta.get("source_sha256"),
            meta=meta,
            dspi2_frames=dspi2_frames,
            ssi0_rx=ssi0_rx,
            dspi1_calls=dspi1_calls,
            mem_writes=mem_writes,
        )


class CapturingPeer:
    """Implements both `unicorn-reference-final:emu/dspi2.py`'s peer contract
    (`.exchange(tx) -> bytes`) and `unicorn-reference-final:emu/ssi.py`'s
    (`.rx(nbytes) -> bytes`, `.tx(data) -> None`), recording
    every call to WRITER.

    Never invents data -- `exchange()`/`rx()` always answer with zeros (the
    same convention as `unicorn-reference-final:emu/dspi2.py`'s `ZeroPeer` and `tools/sharc_dev/sharc_proc.py`'s
    `audio_mode="silence"`): this module exists to observe real ColdFire
    output, not to stand in for a SHARC that would answer it for real.
    `.tx()` (SSI0's captured-samples half) is not recorded: the task this
    module serves only asks for "SSI0 audio in".

    `counter`, if given, is a zero-arg callable returning the current
    ColdFire instruction count (the same convention as
    `unicorn-reference-final:emu/dspi2.py`'s `RecordingPeer`'s own `counter` parameter) -- called fresh at
    every request, so a caller may swap in a real clock (e.g. a `Pits`'s
    `.now`) after construction by mutating whatever the closure reads.
    """

    def __init__(self, writer: CaptureWriter, counter=None) -> None:
        self.writer = writer
        self.counter = counter

    def _count(self):
        return self.counter() if self.counter is not None else None

    def exchange(self, tx: bytes) -> bytes:
        rx = bytes(len(tx))
        self.writer.write_dspi2(self._count(), tx, rx)
        return rx

    def rx(self, nbytes: int) -> bytes:
        data = bytes(nbytes)
        self.writer.write_ssi0_rx(self._count(), data)
        return data

    def tx(self, data: bytes) -> None:
        pass
