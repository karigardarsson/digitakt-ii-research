"""Non-flashable dispatch seam for isolated host tests.

The stock callback is deliberately mandatory and receives WAVE 0..6 verbatim.
No MAIN OS bytes, clamp or table entries are changed by this module.
"""

from collections.abc import Callable

from dv import DV_F, DV_T, DejaVu
from native_input import NativeInputAdapter


class OfflineWaveMux:
    def __init__(self, seed: int, stock: Callable[[int, int], int]):
        self.dv = DejaVu(seed)
        self.stock = stock
        self.native_input = NativeInputAdapter()

    def sample(self, track: int, lfo: int, wave: int, phase: int,
               step: int, sph: int, mode: int, *,
               confirmed_note_on: bool = False) -> int:
        if 0 <= wave <= 6:
            return self.stock(wave, phase)
        if wave in (DV_F, DV_T):
            return self.dv.sample(track, lfo, wave, step, sph, mode,
                                  confirmed_note_on=confirmed_note_on)
        raise ValueError("WAVE outside 0..8")

    def sample_native(self, track: int, lfo: int, wave: int, phase: int,
                      sph: int, mode: int, *, direction: int,
                      trigger_mask: int, phase_delta: int | None = None) -> int:
        """Feed real stock-shaped inputs into the offline DV generator."""
        if 0 <= wave <= 6:
            return self.stock(wave, phase)
        if wave not in (DV_F, DV_T):
            raise ValueError("WAVE outside 0..8")
        if not 0 <= sph <= 127 or not 0 <= mode <= 4:
            raise ValueError("invalid SPH or MODE")
        step, reset = self.native_input.advance(
            track, lfo, phase, direction, trigger_mask,
            retrigger=(wave == DV_T), phase_delta=phase_delta)
        return self.dv.sample(track, lfo, wave, step, sph, mode,
                              confirmed_note_on=reset)
