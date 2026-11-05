"""SHARC coprocessor server: the process peer at the far end of
``tools/sharc_dev/sharc_peer.py``.

Runs the SHARC core as its own OS process so it can be stepped in lockstep
with the ColdFire emulator (CPython + patched Unicorn) across a process
boundary, instead of in the same interpreter -- the SHARC core (pure Python,
`tools/sharc_dev/sharc_core/`) is fastest under PyPy, while the ColdFire side needs
CPython for Unicorn:

    uv run python -m tools.sharc_dev.sharc_proc [--backend stub|runner]
        [--audio-mode silence|tone] [--device dt2] [--image PATH]
        [--image-sha256 HEX]

    # under PyPy (once PyPy 3.11 has pytest/networkx available to it, or
    # with plain stdlib-only args below -- this module itself needs neither):
    uv run --no-project --python pypy3.11 python -m tools.sharc_dev.sharc_proc

This module is pure standard library (plus, for a future runner backend,
`tools/sharc_dev/sharc_core`/`tools/sharc_dev/sharc_run.py` -- see ``RunnerBackend``): no `unicorn`,
`capstone` or `pypcode`, so it starts under PyPy. Everything CPython-only
(Unicorn, the DSPI2/SSI0 models themselves) stays on the client side, in
``tools/sharc_dev/sharc_peer.py``.

## Protocol (version 1)

Length-prefixed binary frames on the child's stdin (client -> server) and
stdout (server -> client): a 4-byte big-endian length, then that many bytes
of body. The body's first byte is a message type; the rest is either a raw
byte payload (``EXCHANGE``/``EXCHANGE_OK``, ``AUDIO_RX_OK``, ``AUDIO_TX``) or
a UTF-8 JSON object (everything else, including ``ERROR``). No pickle, so a
message is safe to log or replay without executing anything. stdout carries
*only* this framing; all diagnostics go to stderr (see
``tools.sharc_dev.sharc_peer.SharcServerDied``, which reads stderr's tail on a crash).

| type | name         | direction | body | meaning                                |
|-----:|--------------|-----------|------|-----------------------------------------|
|    1 | HELLO        | c -> s    | json | ``{version, image_path, image_sha256, device}`` |
|    2 | HELLO_OK     | s -> c    | json | ``{version, backend, device, pid}``     |
|    3 | RESET        | c -> s    | json | ``{}``                                  |
|    4 | RESET_OK     | s -> c    | json | ``{}``                                  |
|    5 | EXCHANGE     | c -> s    | raw  | DSPI2 frame TX bytes (`unicorn-reference-final:emu/dspi2.py` peer) |
|    6 | EXCHANGE_OK  | s -> c    | raw  | DSPI2 frame RX bytes, same length       |
|    7 | AUDIO_RX     | c -> s    | json | ``{nbytes}`` -- this period's `unicorn-reference-final:emu/ssi.py` `peer.rx` request |
|    8 | AUDIO_RX_OK  | s -> c    | raw  | `nbytes` of RX/DAC sample bytes         |
|    9 | AUDIO_TX     | c -> s    | raw  | this period's captured TX/codec sample bytes (`unicorn-reference-final:emu/ssi.py` `peer.tx`) |
|   10 | AUDIO_TX_OK  | s -> c    | json | ``{}``                                  |
|   11 | ADVANCE      | c -> s    | json | ``{unit: "instructions" or "samples", count}`` |
|   12 | ADVANCE_OK   | s -> c    | json | ``{executed}``                          |
|   13 | STATS        | c -> s    | json | ``{}``                                  |
|   14 | STATS_OK     | s -> c    | json | backend-defined stats dict              |
|   15 | SHUTDOWN     | c -> s    | json | ``{}``                                  |
|   16 | SHUTDOWN_OK  | s -> c    | json | ``{}`` (server exits after replying)    |
|  255 | ERROR        | s -> c    | json | ``{message}`` -- sent instead of the OK reply for whatever request failed; the server keeps serving afterwards |

``AUDIO`` is two message types, not one, because ``unicorn-reference-final:emu/ssi.py``'s peer hook
already is two independent calls per DMA period -- ``peer.rx(nbytes)`` before
the RX channel's destination is written, ``peer.tx(data)`` after the TX
channel's source is captured (see that module's "SSI0 peer hook") -- and RX
for period N does not causally depend on TX for period N, so there is
nothing to gain by forcing them into one round trip.

``ADVANCE`` lets a caller step the SHARC side's own clock independently of
frame/audio traffic (`unicorn-reference-final:emu/longrun.py`'s `spin` `async_events` calls `service()` at
every chunk boundary; see `tools.sharc_dev.sharc_peer.SharcServerPeer.step`/`service`).
**Open question, not settled by this module**: what ColdFire-instruction- or
DSPI2-frame-cadence should map to how many `ADVANCE` samples/instructions.
`StubBackend` does not need an answer (nothing here depends on wall time
between exchanges); the real runner will.

A client and server must agree on `PROTOCOL_VERSION`; `HELLO` fails
(`ERROR`) on a mismatch instead of silently proceeding on the wrong wire
shape. Extending the protocol means bumping this constant and either adding
a new message type (with an ``ERROR`` if the peer's version does not have
it) or leaving new JSON fields optional so an old client's request still
parses.

## Backends

``Backend`` is the interface every message type after ``HELLO`` dispatches
to: ``reset``, ``exchange``, ``audio_rx``, ``audio_tx``, ``advance``,
``stats``. Two implementations exist:

- ``StubBackend`` (default): a deterministic stand-in for a SHARC core.
  ``exchange`` echoes zeros with a little-endian... no, big-endian 32-bit
  frame counter stamped into the first 4 bytes (when the frame is at least
  4 bytes); ``audio_rx`` returns silence or (``--audio-mode tone``) an 8-bit
  ramp, so a test can assert on either without decoding a PCM format.
- ``RunnerBackend``: where the real SHARC core (`tools/sharc_dev/sharc_run.py` /
  `tools/sharc_dev/sharc_harness/`, a different lane's work) plugs in once it can
  run the audio task end to end. Not implemented yet -- lane A2 is still
  getting one voice to render correctly, so there is nothing to plug in.
  Every method raises ``NotImplementedError`` with the same message; the
  server answers with an ``ERROR`` reply rather than crashing, so a client
  that asks a runner-backed server to do anything gets a clear reason
  instead of a dead pipe. Swapping ``StubBackend`` for a finished
  ``RunnerBackend`` needs no protocol change: every message this module
  defines already carries what a real backend would need.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from abc import ABC, abstractmethod
from typing import Any, BinaryIO

PROTOCOL_VERSION = 1

HELLO, HELLO_OK = 1, 2
RESET, RESET_OK = 3, 4
EXCHANGE, EXCHANGE_OK = 5, 6
AUDIO_RX, AUDIO_RX_OK = 7, 8
AUDIO_TX, AUDIO_TX_OK = 9, 10
ADVANCE, ADVANCE_OK = 11, 12
STATS, STATS_OK = 13, 14
SHUTDOWN, SHUTDOWN_OK = 15, 16
ERROR = 255

MESSAGE_NAMES = {
    HELLO: "HELLO",
    HELLO_OK: "HELLO_OK",
    RESET: "RESET",
    RESET_OK: "RESET_OK",
    EXCHANGE: "EXCHANGE",
    EXCHANGE_OK: "EXCHANGE_OK",
    AUDIO_RX: "AUDIO_RX",
    AUDIO_RX_OK: "AUDIO_RX_OK",
    AUDIO_TX: "AUDIO_TX",
    AUDIO_TX_OK: "AUDIO_TX_OK",
    ADVANCE: "ADVANCE",
    ADVANCE_OK: "ADVANCE_OK",
    STATS: "STATS",
    STATS_OK: "STATS_OK",
    SHUTDOWN: "SHUTDOWN",
    SHUTDOWN_OK: "SHUTDOWN_OK",
    ERROR: "ERROR",
}

_LEN = struct.Struct(">I")  # 4-byte big-endian body length


# ---------------------------------------------------------------------------
# Framing. Deliberately duplicated (not imported) in tools/sharc_dev/sharc_peer.py: this
# module has to stay stdlib-only to start under PyPy, and the client lives in
# the emu/ package that many other CPython-only (Unicorn) modules import at
# package scope -- importing across that boundary would tie two things that
# should be free to diverge (protocol version, target Python) to one file.
# Keep the two copies in sync by hand; PROTOCOL_VERSION and HELLO's version
# check are what catch a drift at run time.
# ---------------------------------------------------------------------------


def send_message(stream: BinaryIO, msg_type: int, payload: bytes) -> None:
    body = bytes((msg_type,)) + payload
    stream.write(_LEN.pack(len(body)))
    stream.write(body)
    stream.flush()


def send_json(stream: BinaryIO, msg_type: int, obj: Any) -> None:
    send_message(stream, msg_type, json.dumps(obj).encode("utf-8"))


def _read_exact(stream: BinaryIO, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None  # EOF partway through, or exactly at a boundary
        buf += chunk
    return bytes(buf)


def read_message(stream: BinaryIO) -> tuple[int, bytes] | None:
    """-> (msg_type, payload), or None at a clean EOF (no bytes at all)."""
    header = _read_exact(stream, 4)
    if header is None:
        return None
    (length,) = _LEN.unpack(header)
    body = _read_exact(stream, length)
    if body is None:
        raise EOFError("sharc_proc: connection closed mid-message")
    if not body:
        raise EOFError("sharc_proc: zero-length message (missing type byte)")
    return body[0], body[1:]


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class Backend(ABC):
    """What the server dispatches every post-HELLO request to.

    A backend answers in guest instruction/byte terms only; it knows nothing
    about the wire protocol above, or about the DSPI2/SSI0 peer contracts on
    the ColdFire side -- see ``unicorn-reference-final:emu/dspi2.py`` and ``unicorn-reference-final:emu/ssi.py`` for those.
    """

    @abstractmethod
    def reset(self) -> None: ...

    @abstractmethod
    def exchange(self, tx: bytes) -> bytes:
        """-> exactly ``len(tx)`` bytes: this period's DSPI2 RX frame."""

    @abstractmethod
    def audio_rx(self, nbytes: int) -> bytes:
        """-> exactly ``nbytes``: this period's SSI0 RX/DAC samples."""

    @abstractmethod
    def audio_tx(self, data: bytes) -> None:
        """This period's captured SSI0 TX/codec samples."""

    @abstractmethod
    def advance(self, unit: str, count: int) -> int:
        """Step the SHARC clock by ``count`` (unit: instructions|samples).

        -> how much was actually executed (a real runner may halt early;
        ``StubBackend`` always executes the full count)."""

    @abstractmethod
    def stats(self) -> dict[str, Any]: ...


class StubBackend(Backend):
    """Deterministic stand-in while there is no SHARC core to run here yet.

    ``exchange`` never invents data that looks like a real SHARC reply --
    zeros with a frame counter are unambiguous in a hex dump and cheap to
    assert on. ``audio_rx``'s silence/tone choice exists for the same
    reason: a test should be able to tell "the stub answered" from "some
    real DSP happened" without decoding a PCM format.
    """

    def __init__(self, audio_mode: str = "silence") -> None:
        if audio_mode not in ("silence", "tone"):
            raise ValueError("audio_mode must be 'silence' or 'tone'")
        self.audio_mode = audio_mode
        self._tone_phase = 0
        self.reset()

    def reset(self) -> None:
        self.frames = 0
        self.tx_bytes = 0
        self.audio_rx_bytes = 0
        self.audio_tx_bytes = 0
        self.advanced = {"instructions": 0, "samples": 0}
        self._tone_phase = 0

    def exchange(self, tx: bytes) -> bytes:
        self.frames += 1
        self.tx_bytes += len(tx)
        out = bytearray(len(tx))
        if len(tx) >= 4:
            struct.pack_into(">I", out, 0, self.frames & 0xFFFFFFFF)
        return bytes(out)

    def audio_rx(self, nbytes: int) -> bytes:
        self.audio_rx_bytes += nbytes
        if self.audio_mode == "silence":
            return bytes(nbytes)
        out = bytearray(nbytes)
        phase = self._tone_phase
        for i in range(nbytes):
            out[i] = phase & 0xFF
            phase += 1
        self._tone_phase = phase & 0xFF
        return bytes(out)

    def audio_tx(self, data: bytes) -> None:
        self.audio_tx_bytes += len(data)

    def advance(self, unit: str, count: int) -> int:
        if unit not in ("instructions", "samples"):
            raise ValueError("advance unit must be 'instructions' or 'samples'")
        if count < 0:
            raise ValueError("advance count must be >= 0")
        self.advanced[unit] += count
        return count

    def stats(self) -> dict[str, Any]:
        return {
            "backend": "stub",
            "audio_mode": self.audio_mode,
            "frames": self.frames,
            "tx_bytes": self.tx_bytes,
            "audio_rx_bytes": self.audio_rx_bytes,
            "audio_tx_bytes": self.audio_tx_bytes,
            "advanced": dict(self.advanced),
        }


class RunnerBackend(Backend):
    """Where the real SHARC core plugs in. Not implemented yet.

    The real SHARC audio task cannot run end to end: lane A2 is still
    getting one voice to render correctly (`tools/sharc_dev/sharc_run.py`'s
    ``sharc_run`` harness / `tools/sharc_dev/sharc_harness/`), and the server-process
    split is orthogonal to that work -- this class exists so the protocol
    and the client (`tools/sharc_dev/sharc_peer.py`) are already exercised against the
    exact shape a finished runner will need, not so the runner is ready now.
    Every method raises the same ``NotImplementedError``; the server turns
    that into an ``ERROR`` reply (see ``Server.handle_one``) rather than
    dying, so a client asking a runner-backed server to do anything gets a
    clear reason instead of a broken pipe.
    """

    _MESSAGE = (
        "RunnerBackend is not implemented: the real SHARC audio task does "
        "not run end to end yet (lane A2's one-voice render harness is "
        "still in progress). Start tools/sharc_dev/sharc_proc.py with --backend stub."
    )

    def __init__(
        self, image_path: str | None = None, image_sha256: str | None = None
    ) -> None:
        self.image_path = image_path
        self.image_sha256 = image_sha256

    def reset(self) -> None:
        raise NotImplementedError(self._MESSAGE)

    def exchange(self, tx: bytes) -> bytes:
        raise NotImplementedError(self._MESSAGE)

    def audio_rx(self, nbytes: int) -> bytes:
        raise NotImplementedError(self._MESSAGE)

    def audio_tx(self, data: bytes) -> None:
        raise NotImplementedError(self._MESSAGE)

    def advance(self, unit: str, count: int) -> int:
        raise NotImplementedError(self._MESSAGE)

    def stats(self) -> dict[str, Any]:
        raise NotImplementedError(self._MESSAGE)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class Server:
    """Dispatches framed messages from ``instream`` to ``backend``, replying
    on ``outstream``. ``instream``/``outstream`` default to the process's own
    stdin/stdout in binary mode; pass in-memory streams (e.g. `io.BytesIO`
    wrapped to look like a pipe) to drive this in a test with no subprocess.
    """

    def __init__(
        self,
        backend: Backend,
        device: str = "dt2",
        instream: BinaryIO | None = None,
        outstream: BinaryIO | None = None,
        log: Any = None,
    ) -> None:
        self.backend = backend
        self.device = device
        self.instream = instream if instream is not None else sys.stdin.buffer
        self.outstream = outstream if outstream is not None else sys.stdout.buffer
        self.log = log if log is not None else sys.stderr
        self.hello_seen = False

    def _error(self, message: object) -> None:
        send_json(self.outstream, ERROR, {"message": str(message)})

    def handle_one(self) -> bool:
        """Read and dispatch one message. -> False at SHUTDOWN or clean EOF."""
        msg = read_message(self.instream)
        if msg is None:
            return False
        msg_type, payload = msg
        if not self.hello_seen and msg_type != HELLO:
            self._error(
                "sharc_proc: no HELLO received yet (got %s)"
                % MESSAGE_NAMES.get(msg_type, msg_type)
            )
            return True
        try:
            self._dispatch(msg_type, payload)
        except NotImplementedError as exc:
            self._error(exc)
        except Exception as exc:  # noqa: BLE001 -- answer every request, never die
            self._error("%s: %s" % (type(exc).__name__, exc))
        return msg_type != SHUTDOWN

    def _dispatch(self, msg_type: int, payload: bytes) -> None:
        if msg_type == HELLO:
            self._handle_hello(payload)
        elif msg_type == RESET:
            self.backend.reset()
            send_json(self.outstream, RESET_OK, {})
        elif msg_type == EXCHANGE:
            rx = self.backend.exchange(payload)
            if len(rx) != len(payload):
                raise ValueError(
                    "backend.exchange returned %d bytes for a %d-byte frame"
                    % (len(rx), len(payload))
                )
            send_message(self.outstream, EXCHANGE_OK, rx)
        elif msg_type == AUDIO_RX:
            req = json.loads(payload.decode("utf-8"))
            nbytes = int(req["nbytes"])
            data = self.backend.audio_rx(nbytes)
            if len(data) != nbytes:
                raise ValueError(
                    "backend.audio_rx returned %d bytes, expected %d"
                    % (len(data), nbytes)
                )
            send_message(self.outstream, AUDIO_RX_OK, data)
        elif msg_type == AUDIO_TX:
            self.backend.audio_tx(payload)
            send_json(self.outstream, AUDIO_TX_OK, {})
        elif msg_type == ADVANCE:
            req = json.loads(payload.decode("utf-8"))
            executed = self.backend.advance(req["unit"], int(req["count"]))
            send_json(self.outstream, ADVANCE_OK, {"executed": executed})
        elif msg_type == STATS:
            send_json(self.outstream, STATS_OK, self.backend.stats())
        elif msg_type == SHUTDOWN:
            send_json(self.outstream, SHUTDOWN_OK, {})
        else:
            raise ValueError("unknown message type %d" % msg_type)

    def _handle_hello(self, payload: bytes) -> None:
        req = json.loads(payload.decode("utf-8"))
        if req.get("version") != PROTOCOL_VERSION:
            raise ValueError(
                "protocol version mismatch: server=%d client=%r"
                % (PROTOCOL_VERSION, req.get("version"))
            )
        self.hello_seen = True
        print(
            "sharc_proc: hello from device=%r image=%r sha256=%s backend=%s"
            % (
                req.get("device"),
                req.get("image_path"),
                req.get("image_sha256"),
                type(self.backend).__name__,
            ),
            file=self.log,
            flush=True,
        )
        send_json(
            self.outstream,
            HELLO_OK,
            {
                "version": PROTOCOL_VERSION,
                "backend": type(self.backend).__name__,
                "device": self.device,
                "pid": os.getpid(),
            },
        )

    def serve_forever(self) -> None:
        try:
            while self.handle_one():
                pass
        except (EOFError, BrokenPipeError) as exc:
            print("sharc_proc: %s" % exc, file=self.log, flush=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_backend(args: argparse.Namespace) -> Backend:
    if args.backend == "stub":
        return StubBackend(audio_mode=args.audio_mode)
    if args.backend == "runner":
        return RunnerBackend(image_path=args.image, image_sha256=args.image_sha256)
    raise ValueError("unknown backend %r" % args.backend)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--backend", choices=("stub", "runner"), default="stub")
    p.add_argument("--audio-mode", choices=("silence", "tone"), default="silence")
    p.add_argument("--device", default="dt2")
    p.add_argument(
        "--image",
        default=None,
        help="SHARC image path, recorded for a --backend runner",
    )
    p.add_argument("--image-sha256", default=None)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    backend = build_backend(args)
    print(
        "sharc_proc: protocol v%d backend=%s pid=%d"
        % (PROTOCOL_VERSION, type(backend).__name__, os.getpid()),
        file=sys.stderr,
        flush=True,
    )
    Server(backend, device=args.device).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
