"""Offline DT2 Déjà Vu waveform model; no image writer or device I/O.

The caller supplies an unwrapped 1/16-cycle step count from the existing LFO
phase path. Device phase-to-step conversion and a confirmed note-on source are
integration work; this module deliberately does not infer either one.
"""

from dataclasses import dataclass, field

DV_F = 7
DV_T = 8
LENGTHS = (2, 4, 8, 16, 32)
MASK32 = 0xFFFFFFFF


def _mix32(value: int) -> int:
    value = (value ^ (value >> 16)) * 0x7FEB352D & MASK32
    value = (value ^ (value >> 15)) * 0x846CA68B & MASK32
    return (value ^ (value >> 16)) or 0x6D2B79F5


@dataclass
class _Voice:
    rng: int
    slots: list[int] = field(default_factory=list)
    cursor: int = 0
    last_step: int | None = None

    def random32(self) -> int:
        x = self.rng
        x ^= x << 13 & MASK32
        x ^= x >> 17
        x ^= x << 5 & MASK32
        self.rng = x & MASK32
        return self.rng

    def random_level(self) -> int:
        return (self.random32() >> 24) - 128

    def initialize(self) -> None:
        if not self.slots:
            self.slots = [self.random_level() for _ in range(32)]

    def step(self, length: int, sph: int) -> None:
        self.cursor = (self.cursor + 1) % length
        if sph < 64:
            # SPH 0: every step is new. SPH 64: the stored loop is locked.
            if self.random32() % 64 < 64 - sph:
                self.slots[self.cursor] = self.random_level()
        elif sph > 64:
            # SPH 127: only the order changes; values are retained.
            if self.random32() % 63 < sph - 64:
                other = self.random32() % length
                self.slots[self.cursor], self.slots[other] = (
                    self.slots[other], self.slots[self.cursor]
                )


class DejaVu:
    """Independent, deterministic 32-value storage for 16 tracks x 3 LFOs."""

    def __init__(self, seed: int):
        self.seed = seed & MASK32
        self._voices: dict[tuple[int, int], _Voice] = {}

    def _voice(self, track: int, lfo: int) -> _Voice:
        if not 0 <= track < 16 or not 0 <= lfo < 3:
            raise ValueError("track must be 0..15 and LFO must be 0..2")
        key = track, lfo
        if key not in self._voices:
            self._voices[key] = _Voice(_mix32(self.seed ^ ((track + 1) * 0x9E3779B9)
                                               ^ ((lfo + 1) * 0x85EBCA6B)))
        return self._voices[key]

    def sample(self, track: int, lfo: int, wave: int, step: int,
               sph: int, mode: int, *, confirmed_note_on: bool = False) -> int:
        """Return signed Q31 level. WAVE 0..6 must remain on the stock path.

        `step` is monotonically increasing and unwrapped. DV-T retriggers only
        when its caller explicitly asserts a confirmed note-on. The stored
        random loop survives retrigger; its cursor restarts at position zero.
        """
        if wave not in (DV_F, DV_T):
            raise ValueError("only DV-F (7) and DV-T (8) are handled here")
        if step < 0 or not 0 <= sph <= 127 or not 0 <= mode <= 4:
            raise ValueError("invalid step, SPH or MODE")
        voice = self._voice(track, lfo)
        voice.initialize()
        length = LENGTHS[mode]
        if voice.cursor >= length:
            voice.cursor %= length
        if confirmed_note_on and wave == DV_T:
            voice.cursor = 0
            voice.last_step = step
        elif voice.last_step is None:
            voice.last_step = step
        elif step < voice.last_step:
            raise ValueError("step went backward without a confirmed reset")
        else:
            for _ in range(step - voice.last_step):
                voice.step(length, sph)
            voice.last_step = step
        return voice.slots[voice.cursor] << 24

    def loop(self, track: int, lfo: int, length: int) -> tuple[int, ...]:
        """Inspect host state for deterministic tests; levels are signed bytes."""
        if length not in LENGTHS:
            raise ValueError("invalid loop length")
        voice = self._voice(track, lfo)
        voice.initialize()
        return tuple(voice.slots[:length])
