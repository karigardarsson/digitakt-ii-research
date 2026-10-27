"""Execute independent native sampler contexts; diagnostic only, no firmware output.

Run from the retained DigiKit import root. Inputs are generated PCM/slot metadata,
not a sequencer or hardware observation. All playback/setup instructions come
from the hash-gated latest complete image. Host mixing is explicitly not DSP code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path

from tools.patchkit.port_resolve import sections_of
from tools.sharc_dev import sharc_run as sr
from tools.sharc_dev import sharc_trace as st
from tools.sharc_dev.sharcldr import LoadedMemory

SHA = "b9ee18b521d1dc5bd39b9e882dfa7ce4ab8bdfa9be692d63182748a95a5be740"
STACK, RET = 0x2A0000, 0x1C6B06
PRIVATE, OUT = 0x200E8000, 0x200E9000
REC = 0x1D8
TABLE, PCM = 0x257810, 0x8422B9C8


def bits(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]


class Native:
    def __init__(self, mem, approx_recips=False):
        self.runner = sr.Runner(mem, 0x1C3F78, explicit_memory_model=True,
                                diagnose_unknown=True, approx_recips=approx_recips)
        self.cost = []

    def put(self, a, x, n=4):
        st._dm_write(self.runner.state, a, n, st.Const(x & ((1 << (8 * n)) - 1)))

    def get(self, a, n=4):
        x = st._dm_read(self.runner.state, a, n)
        assert isinstance(x, st.Const), (hex(a), x)
        return x.value & ((1 << (8 * n)) - 1)

    def data(self, a, n):
        return bytes(self.get(a + k, 1) for k in range(n))

    def call(self, pc, regs, args=(), bound=25000, *, stack=STACK, stop=RET):
        base = {"M5": 0, "M6": 1, "M7": -1, "M13": 0, "M14": 1, "M15": -1,
                "I6": stack, "I7": stack - 8, "L6": 0, "L7": 0, **regs}
        self.put(stack - 4, RET - 1)
        self.put(stack, stack + 0x10000)
        for k, val in enumerate(args, 1):
            self.put(stack + k * 4, val)
        self.runner = self.runner.fresh_call(pc, regs=base, return_address=RET)
        self.runner.breakpoints = frozenset((stop,))
        result = self.runner.run(bound)
        assert result.final_pc_sw == stop, (hex(pc), result.halt.to_json(), hex(result.final_pc_sw),
            {name: str(self.runner.state.uregs.get(st.UREG_CODES[name]))
             for name in ("R0", "R1", "R2", "R4", "R8", "R9", "R10", "I4", "I5")},
            self.runner.state.call_stack)
        self.cost.append((hex(pc), result.instructions))

    def slot(self, slot, channel, length=2048, rate=48000):
        # Each channel contains a different deterministic tone, including guards.
        offset = slot * 0x10000 + channel * 0x4000
        for k in range(length + 8):
            x = round(10000 * math.sin(2 * math.pi * k / (37 + slot + 11 * channel)))
            self.put(PCM + offset + 2 * k, x, 2)
        return offset

    def load_slots(self):
        for slot in (7, 31):
            offsets = [self.slot(slot, ch) for ch in range(2)]
            for k, val in enumerate((*offsets, 4096, 48000, 1)):
                self.put(TABLE + slot * 20 + k * 4, val)

    def setup(self, record, slot, channel, note=60, reverse=0, loop=0):
        self.call(0x1C3F78, {"R4": slot, "R1": OUT + 0x400})
        ptr = self.get(OUT + 0x400 + channel * 4)
        length, rate = self.get(OUT + 0x408), self.get(OUT + 0x40C)
        self.call(0x1C4E70, {"R4": record, "R8": ptr}, (length, 0, rate))
        self.call(0x1C4EAF, {"R4": record})
        # This native alternative takes a pitch ratio directly, avoiding the
        # powf routine's currently incomplete interpreter path. It is also used
        # by stock playback; no routine is mocked or skipped.
        self.call(0x1C4D88, {"R4": record, "R8": bits(2 ** ((note - 60) / 12)), "R12": 0},
                  (reverse, loop, bits(0), bits(1)))
        return {"slot": slot, "channel": channel, "pointer": ptr,
                "length": length, "rate": rate, "step_q31": self.q31(record + 0x1A8),
                "phase_q31": self.q31(record + 0x1B0)}

    def q31(self, a):
        x = self.get(a) | self.get(a + 4) << 32
        return x - (1 << 64) if x & (1 << 63) else x

    def render(self, record, out):
        self.call(0x1C4ECF, {"R4": record, "R8": out, "R12": 32})
        words = [self.get(out + 4 * k) for k in range(32)]
        return [struct.unpack("<f", struct.pack("<I", w))[0] for w in words]


def run(candidate):
    raw = candidate.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SHA
    mem = LoadedMemory.from_stream(sections_of(candidate)[7])
    native = Native(mem)
    native.load_slots()
    # Private records deliberately do not coincide with the engine's 32 voices.
    records = [PRIVATE + REC * k for k in range(4)]
    for a in range(PRIVATE - 16, PRIVATE + REC * 4 + 16, 4):
        native.put(a, 0 if PRIVATE <= a < PRIVATE + REC * 4 else 0xA5A5A5A5)
    setup = []
    for k, rec in enumerate(records):
        setup.append(native.setup(rec, (7, 31)[k // 2], k & 1,
                                  note=(60, 72)[k // 2], reverse=k // 2, loop=1))
    controls = [native.data(r + 0x184, REC - 0x184) for r in records]
    pcm = [[] for _ in records]
    costs = []
    for block in range(4):
        for k, rec in enumerate(records):
            before = [native.data(r, REC) for r in records]
            samples = native.render(rec, OUT + k * 0x80)
            assert all(math.isfinite(x) for x in samples)
            assert any(x != 0 for x in samples), (k, block, "silent")
            pcm[k].extend(samples)
            for j, other in enumerate(records):
                if j != k:
                    assert native.data(other, REC) == before[j], (k, j, "state leak")
            costs.append(native.cost[-1][1])
    assert pcm[0] != pcm[1] and pcm[0] != pcm[2] and pcm[2] != pcm[3]
    assert native.data(PRIVATE - 16, 16) == bytes.fromhex("a5" * 16)
    assert native.data(PRIVATE + REC * 4, 16) == bytes.fromhex("a5" * 16)
    # Audio sum is only a host-side reference for a future integrated DSP mixer.
    mixed = [0.5 * a + 0.5 * b for a, b in zip(pcm[0], pcm[2], strict=True)]
    assert all(math.isfinite(x) for x in mixed)
    assert candidate.read_bytes() == raw
    return {"status": "PASS", "feature": "offline private native-context proof only",
            "candidate_sha256": SHA, "setup": setup, "initial_controls_hex": [x.hex() for x in controls],
            "independent_stereo_channels": True, "other_contexts_unchanged": True,
            "record_guards": "PASS", "render_instructions_per_channel_block": costs,
            "pcm_sha256": [hashlib.sha256(struct.pack('<128f', *x)).hexdigest() for x in pcm],
            "host_mix_peak": max(map(abs, mixed)), "firmware_written": False,
            "not_tested": ["native sequencer", "p-lock delivery", "hardware", "integrated DSP mixer",
                           "slot replacement", "note-off policy", "silicon timing"]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()
    report = run(a.candidate)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
