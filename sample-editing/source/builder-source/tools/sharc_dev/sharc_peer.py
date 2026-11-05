"""Client peer for `tools/sharc_dev/sharc_proc.py`: launches the SHARC server as a
subprocess and speaks its protocol (version 1; see that module's docstring
for the message table) over its stdin/stdout.

`SharcServerPeer` implements every peer shape `unicorn-reference-final:emu/dspi2.py` and
`unicorn-reference-final:emu/ssi.py`'s docstrings already specify, plus `unicorn-reference-final:emu/longrun.py`'s
`spin` `async_events` pair, so one instance plugs into all three at once:

    Dspi2Link(m, peer=p)                       # p.exchange(tx) -> bytes
    Ssi0Dma(m, ..., peer=p)                    # p.rx(n) -> bytes, p.tx(data)
    spin(m, pc, n, pits=pits, async_events=(p,))  # p.step/p.service

See `unicorn-reference-final:emu/dspi2.py`'s "Lockstep interface for a future SHARC stepper" and
`unicorn-reference-final:emu/ssi.py`'s "SSI0 peer hook" -- this is the stepper those sections
describe, backed by a process instead of in-process state. Today it talks to
`tools/sharc_dev/sharc_proc.py`'s `StubBackend`; nothing here changes when a real
runner backend replaces it (see that module's docstring).

## Launching the server

`default_command()` prefers a `pypy3.11` (on `PATH`, else uv-managed) since
the SHARC core is fastest under PyPy's JIT, and falls back to CPython
(`sys.executable`) when neither is available. Pass `command=[...]` to
`SharcServerPeer` to bypass this (a fixed interpreter path, a wrapper
script, a remote launcher, ...).

## Failure handling

Every round trip can fail two ways: the server answers with an `ERROR`
message (`SharcServerError`, e.g. a bad request or -- today -- a
`--backend runner` NotImplementedError), or the child process/pipe is gone
(`SharcServerDied`, whose message includes the last lines of the child's
stderr, captured continuously by a background thread so a crash mid-request
is not a bare `BrokenPipeError`).
"""

from __future__ import annotations

import collections
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Sequence

PROTOCOL_VERSION = 1  # keep in lockstep with tools/sharc_dev/sharc_proc.py

# Message type constants and framing are intentionally duplicated from
# tools/sharc_dev/sharc_proc.py rather than imported -- see that module's docstring,
# "Framing. Deliberately duplicated...": this file lives in the `emu`
# package (CPython + Unicorn only), the server has to stay importable under
# PyPy with no `emu` package on its path, and the two sides are small enough
# that hand-keeping them in sync is cheaper than a shared-import seam. A
# version mismatch is caught at HELLO, not silently ignored.
HELLO, HELLO_OK = 1, 2
RESET, RESET_OK = 3, 4
EXCHANGE, EXCHANGE_OK = 5, 6
AUDIO_RX, AUDIO_RX_OK = 7, 8
AUDIO_TX, AUDIO_TX_OK = 9, 10
ADVANCE, ADVANCE_OK = 11, 12
STATS, STATS_OK = 13, 14
SHUTDOWN, SHUTDOWN_OK = 15, 16
ERROR = 255

_LEN = struct.Struct(">I")

_DEFAULT_SERVER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "tools",
    "sharc_dev",
    "sharc_proc.py",
)


class SharcServerError(RuntimeError):
    """The server answered a request with an `ERROR` message."""


class SharcServerDied(RuntimeError):
    """The server process exited, or its pipe closed, mid-conversation."""


class _StderrTail:
    """Background reader keeping the last `lines` of a process's stderr.

    `Popen`'s stderr pipe has a finite OS buffer; a child that writes more
    than that without anyone reading it blocks on the write, so this has to
    run continuously from the moment the process is launched, not be read
    lazily on demand.
    """

    def __init__(self, stream: object, lines: int = 40) -> None:
        self._buf: collections.deque[str] = collections.deque(maxlen=lines)
        self._thread = threading.Thread(target=self._run, args=(stream,), daemon=True)
        self._thread.start()

    def _run(self, stream) -> None:
        try:
            for line in iter(stream.readline, b""):
                self._buf.append(line.decode("utf-8", "replace").rstrip("\n"))
        except (OSError, ValueError):
            pass

    def text(self) -> str:
        return "\n".join(self._buf)


def default_command(server_path: str | None = None, prefer: str = "pypy") -> list[str]:
    """-> argv to launch `tools/sharc_dev/sharc_proc.py` (minus its own CLI args).

    `prefer='pypy'` (default): a `pypy3.11` on `PATH`, else a uv-managed
    `pypy3.11` (via `uv run --no-project --python pypy3.11`), else CPython
    (`sys.executable`) -- the SHARC core benefits from PyPy's JIT but nothing
    here requires it. `prefer='cpython'` always uses `sys.executable`.
    """
    server_path = server_path or _DEFAULT_SERVER_PATH
    if prefer == "cpython":
        return [sys.executable, server_path]
    if prefer != "pypy":
        raise ValueError("prefer must be 'pypy' or 'cpython', got %r" % prefer)
    pypy = shutil.which("pypy3.11")
    if pypy:
        return [pypy, server_path]
    uv = shutil.which("uv")
    if uv:
        probe = subprocess.run(
            [uv, "python", "find", "pypy3.11"],
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0 and probe.stdout.strip():
            return [
                uv,
                "run",
                "--no-project",
                "--python",
                "pypy3.11",
                "python",
                server_path,
            ]
    return [sys.executable, server_path]


class SharcServerPeer:
    """Launches `tools/sharc_dev/sharc_proc.py` and speaks protocol v1 to it.

    `advance_every`, if given, is a ColdFire-instruction cadence: `service()`
    (called by `unicorn-reference-final:emu/longrun.py`'s `spin` at every chunk boundary when this object is
    in `async_events`) sends one `ADVANCE` message per `advance_every`
    instructions of guest time. `None` (default) never sends one -- correct
    for `StubBackend`, whose replies do not depend on wall time between
    exchanges; how a real runner should map ColdFire instructions to SHARC
    time is not decided (see `tools/sharc_dev/sharc_proc.py`'s docstring, "Open
    question").
    """

    def __init__(
        self,
        image_path: str | None = None,
        image_sha256: str | None = None,
        device: str = "dt2",
        command: Sequence[str] | None = None,
        prefer: str = "pypy",
        backend: str = "stub",
        audio_mode: str = "silence",
        advance_every: int | None = None,
        advance_unit: str = "instructions",
        extra_args: Sequence[str] = (),
        server_path: str | None = None,
    ) -> None:
        if advance_unit not in ("instructions", "samples"):
            raise ValueError("advance_unit must be 'instructions' or 'samples'")
        self.advance_every = advance_every
        self.advance_unit = advance_unit
        self._last_advance_done = 0

        argv = (
            list(command)
            if command is not None
            else default_command(server_path, prefer=prefer)
        )
        argv = argv + [
            "--backend",
            backend,
            "--audio-mode",
            audio_mode,
            "--device",
            device,
        ]
        if image_path is not None:
            argv += ["--image", image_path]
        if image_sha256 is not None:
            argv += ["--image-sha256", image_sha256]
        argv += list(extra_args)
        self._argv = argv

        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._stderr_tail = _StderrTail(self.proc.stderr)
        self._closed = False

        self.frames = 0
        self.exchange_count = 0
        self.exchange_seconds = 0.0

        reply = self._roundtrip_json(
            HELLO,
            {
                "version": PROTOCOL_VERSION,
                "image_path": image_path,
                "image_sha256": image_sha256,
                "device": device,
            },
            HELLO_OK,
        )
        self.backend_name = reply.get("backend")
        self.server_pid = reply.get("pid")

    # -- process / wire plumbing --------------------------------------

    def _died(self, detail: str) -> SharcServerDied:
        rc = self.proc.poll()
        tail = self._stderr_tail.text()
        return SharcServerDied(
            "%s (exit=%r): %s%s"
            % (
                " ".join(self._argv),
                rc,
                detail,
                ("\n--- stderr tail ---\n" + tail) if tail else "",
            )
        )

    def _send(self, msg_type: int, payload: bytes) -> None:
        body = bytes((msg_type,)) + payload
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(_LEN.pack(len(body)))
            self.proc.stdin.write(body)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise self._died("write failed: %s" % exc) from exc

    def _send_json(self, msg_type: int, obj: object) -> None:
        self._send(msg_type, json.dumps(obj).encode("utf-8"))

    def _read_exact(self, n: int) -> bytes:
        assert self.proc.stdout is not None
        buf = bytearray()
        while len(buf) < n:
            chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                raise self._died("unexpected EOF from server")
            buf += chunk
        return bytes(buf)

    def _recv(self) -> tuple[int, bytes]:
        (length,) = _LEN.unpack(self._read_exact(4))
        body = self._read_exact(length)
        if not body:
            raise self._died("zero-length reply (missing type byte)")
        msg_type, payload = body[0], body[1:]
        if msg_type == ERROR:
            raise SharcServerError(json.loads(payload.decode("utf-8"))["message"])
        return msg_type, payload

    def _roundtrip_json(self, send_type: int, obj: object, expect_type: int) -> dict:
        self._send_json(send_type, obj)
        msg_type, payload = self._recv()
        if msg_type != expect_type:
            raise self._died(
                "unexpected reply type %d (wanted %d)" % (msg_type, expect_type)
            )
        return json.loads(payload.decode("utf-8"))

    # -- unicorn-reference-final:emu/dspi2.py peer: exchange(tx: bytes) -> bytes -------------------

    def exchange(self, tx: bytes) -> bytes:
        t0 = time.perf_counter()
        self._send(EXCHANGE, bytes(tx))
        msg_type, payload = self._recv()
        if msg_type != EXCHANGE_OK:
            raise self._died("unexpected reply type %d for exchange" % msg_type)
        if len(payload) != len(tx):
            raise ValueError(
                "sharc_proc returned %d bytes for a %d-byte exchange frame"
                % (len(payload), len(tx))
            )
        self.exchange_seconds += time.perf_counter() - t0
        self.exchange_count += 1
        self.frames += 1
        return payload

    # -- unicorn-reference-final:emu/ssi.py peer: rx(nbytes) -> bytes, tx(data) -> None ------------

    def rx(self, nbytes: int) -> bytes:
        self._send_json(AUDIO_RX, {"nbytes": nbytes})
        msg_type, payload = self._recv()
        if msg_type != AUDIO_RX_OK:
            raise self._died("unexpected reply type %d for audio rx" % msg_type)
        if len(payload) != nbytes:
            raise ValueError(
                "sharc_proc returned %d bytes, expected %d" % (len(payload), nbytes)
            )
        return payload

    def tx(self, data: bytes) -> None:
        self._send(AUDIO_TX, bytes(data))
        msg_type, _payload = self._recv()
        if msg_type != AUDIO_TX_OK:
            raise self._died("unexpected reply type %d for audio tx" % msg_type)

    # -- direct protocol access -----------------------------------------

    def reset(self) -> None:
        self._roundtrip_json(RESET, {}, RESET_OK)
        self._last_advance_done = 0

    def advance(self, unit: str, count: int) -> int:
        reply = self._roundtrip_json(
            ADVANCE, {"unit": unit, "count": count}, ADVANCE_OK
        )
        return int(reply["executed"])

    def stats(self) -> dict:
        return self._roundtrip_json(STATS, {}, STATS_OK)

    # -- unicorn-reference-final:emu/longrun.py spin async_events: step/service ---------------------

    def step(self, done: int, remaining: int | None = None) -> int | None:
        """-> instructions until this peer's next deadline, or None.

        No deadline (None) unless `advance_every` opts into one -- see the
        class docstring.
        """
        if self.advance_every is None:
            return None
        n = max(1, self._last_advance_done + self.advance_every - done)
        return min(n, remaining) if remaining is not None else n

    def service(self, done: int) -> None:
        """Send one ADVANCE covering however much time has passed, if due."""
        if self.advance_every is None:
            return
        elapsed = done - self._last_advance_done
        if elapsed >= self.advance_every:
            self.advance(self.advance_unit, elapsed)
            self._last_advance_done = done

    # -- lifecycle ---------------------------------------------------------

    def close(self, timeout: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._send_json(SHUTDOWN, {})
            self._recv()
        except (SharcServerDied, SharcServerError, OSError):
            pass
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()

    def __enter__(self) -> SharcServerPeer:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
