"""Offline adapter from stock DT2 phase and track-trigger inputs to DV steps.

The caller supplies the stock tick's direction (+1 or -1). A signed phase
increment is optional; it disambiguates a jump spanning a complete cycle.
This is a host model, not a ColdFire hook or a natural note-on trace.
"""

from dataclasses import dataclass

PERIOD = 0x5265C000
PHASE_SCALE = 0x636BA875
STEPS_PER_CYCLE = 16


def stock_step_position(phase: int) -> int:
    """Return the exact 0..15 bin of the stock normalized waveform phase."""
    if not 0 <= phase < PERIOD:
        raise ValueError("phase must be in the stock LFO period")
    q = ((phase * PHASE_SCALE) >> 32) << 2
    return q >> 27


def _unwrapped_position(phase: int) -> int:
    turns, remainder = divmod(phase, PERIOD)
    return turns * STEPS_PER_CYCLE + stock_step_position(remainder)


@dataclass
class _PhaseState:
    phase: int | None = None
    step: int = 0


class NativeInputAdapter:
    """Independent phase cursors for 16 tracks and three LFOs per track."""

    def __init__(self) -> None:
        self._states = {(track, lfo): _PhaseState()
                        for track in range(16) for lfo in range(3)}

    def advance(self, track: int, lfo: int, phase: int, direction: int,
                trigger_mask: int, *, retrigger: bool = False,
                phase_delta: int | None = None) -> tuple[int, bool]:
        """Return (monotonic DV step, confirmed synthetic trigger-mask bit).

        Without `phase_delta`, endpoint bins resolve jumps of 0..15 steps.
        Supply the stock signed phase increment to count a full turn or more.
        `retrigger` is selected by the caller only for DV-T.
        """
        if (track, lfo) not in self._states:
            raise ValueError("track must be 0..15 and LFO must be 0..2")
        position = stock_step_position(phase)
        if direction not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        if not 0 <= trigger_mask <= 0xFFFF:
            raise ValueError("trigger mask must be 16 bits")
        if phase_delta is not None and phase_delta * direction < 0:
            raise ValueError("phase increment disagrees with direction")

        state = self._states[track, lfo]
        reset = retrigger and bool(trigger_mask & (1 << track))
        if reset:
            state.phase = phase
            state.step = 0
            return state.step, True
        if state.phase is None:
            state.phase = phase
            return state.step, False

        if phase_delta is not None:
            if (state.phase + phase_delta) % PERIOD != phase:
                raise ValueError("phase does not match the supplied stock increment")
            previous = _unwrapped_position(state.phase)
            current = _unwrapped_position(state.phase + phase_delta)
            crossed = abs(current - previous)
        else:
            previous = stock_step_position(state.phase)
            crossed = ((position - previous) if direction > 0
                       else (previous - position)) % STEPS_PER_CYCLE

        state.phase = phase
        state.step += crossed
        return state.step, False
