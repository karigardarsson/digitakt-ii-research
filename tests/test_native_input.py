"""Stock-shaped phase and trigger inputs for the offline DV V1 seam."""

import unittest

from dv import DV_F, DV_T
from native_input import NativeInputAdapter, PERIOD, stock_step_position
from offline_mux import OfflineWaveMux


def middle_of_bin(bin_number: int) -> int:
    return ((2 * bin_number + 1) * PERIOD) // 32


class NativeInputTests(unittest.TestCase):
    def test_exact_stock_boundary(self):
        self.assertEqual(stock_step_position(0x05265C00), 0)
        self.assertEqual(stock_step_position(0x05265C01), 1)
        self.assertEqual(stock_step_position(PERIOD - 1), 15)

    def test_forward_reverse_jumps_and_wrap(self):
        a = NativeInputAdapter()
        self.assertEqual(a.advance(0, 0, middle_of_bin(0), 1, 0), (0, False))
        self.assertEqual(a.advance(0, 0, middle_of_bin(3), 1, 0), (3, False))
        self.assertEqual(a.advance(0, 0, middle_of_bin(14), 1, 0), (14, False))
        self.assertEqual(a.advance(0, 0, middle_of_bin(2), 1, 0), (18, False))

        self.assertEqual(a.advance(0, 1, middle_of_bin(10), -1, 0), (0, False))
        self.assertEqual(a.advance(0, 1, middle_of_bin(7), -1, 0), (3, False))
        self.assertEqual(a.advance(0, 1, middle_of_bin(1), -1, 0), (9, False))
        self.assertEqual(a.advance(0, 1, middle_of_bin(14), -1, 0), (12, False))
        self.assertEqual(a.advance(0, 1, middle_of_bin(2), 1, 0), (16, False))

    def test_full_turn_increment_disambiguates_same_position(self):
        a = NativeInputAdapter()
        self.assertEqual(a.advance(2, 2, 0, 1, 0), (0, False))
        self.assertEqual(a.advance(2, 2, 0, 1, 0, phase_delta=PERIOD),
                         (16, False))
        self.assertEqual(a.advance(2, 2, 0, 1, 0, phase_delta=PERIOD),
                         (32, False))
        self.assertEqual(a.advance(3, 2, 0, -1, 0), (0, False))
        self.assertEqual(a.advance(3, 2, 0, -1, 0, phase_delta=-PERIOD),
                         (16, False))
        with self.assertRaises(ValueError):
            a.advance(2, 2, 1, 1, 0, phase_delta=PERIOD)

    def test_track_mask_reset_and_48_independent_cursors(self):
        a = NativeInputAdapter()
        for track in range(16):
            for lfo in range(3):
                self.assertEqual(a.advance(track, lfo, middle_of_bin(0), 1, 0,
                                           retrigger=True), (0, False))
                self.assertEqual(a.advance(track, lfo, middle_of_bin(2), 1, 0,
                                           retrigger=True), (2, False))
        for track in range(16):
            for lfo in range(3):
                expected = (0, True) if track == 5 else (2, False)
                self.assertEqual(a.advance(track, lfo, middle_of_bin(2), 1,
                                           1 << 5, retrigger=True), expected)
        self.assertEqual(a.advance(5, 0, middle_of_bin(4), 1, 0,
                                   retrigger=True), (2, False))
        self.assertEqual(a.advance(5, 1, middle_of_bin(2), 1, 0,
                                   retrigger=True), (0, False))


class NativeMuxTests(unittest.TestCase):
    def test_48_random_loops_are_independent_under_interleaving(self):
        isolated = OfflineWaveMux(456, lambda *_: 0)
        interleaved = OfflineWaveMux(456, lambda *_: 0)
        expected = {}
        for track in range(16):
            for lfo in range(3):
                expected[track, lfo] = (
                    isolated.sample_native(track, lfo, DV_F, 0, 0, 2,
                                           direction=1, trigger_mask=0),
                    isolated.sample_native(track, lfo, DV_F, 0, 0, 2,
                                           direction=1, trigger_mask=0,
                                           phase_delta=PERIOD),
                )
        actual = {}
        for track in range(16):
            for lfo in range(3):
                actual[track, lfo] = [
                    interleaved.sample_native(track, lfo, DV_F, 0, 0, 2,
                                              direction=1, trigger_mask=0)]
        for lfo in reversed(range(3)):
            for track in reversed(range(16)):
                actual[track, lfo].append(
                    interleaved.sample_native(track, lfo, DV_F, 0, 0, 2,
                                              direction=1, trigger_mask=0,
                                              phase_delta=PERIOD))
        self.assertEqual(expected, {key: tuple(value)
                                    for key, value in actual.items()})
        self.assertEqual(len(interleaved.dv._voices), 48)

    def test_stock_wave_0_to_6_is_verbatim(self):
        calls = []
        mux = OfflineWaveMux(77, lambda wave, phase:
                             (calls.append((wave, phase)) or wave + 100))
        for wave in range(7):
            self.assertEqual(mux.sample_native(0, 0, wave, 0x31415926,
                                               64, 2, direction=1,
                                               trigger_mask=1), wave + 100)
        self.assertEqual(calls, [(wave, 0x31415926) for wave in range(7)])

    def test_dvf_continues_dvt_restarts_without_losing_locked_loop(self):
        mux = OfflineWaveMux(91, lambda *_: self.fail("stock called for DV"))
        locked_f = mux.dv.loop(0, 0, 8)
        locked_t = mux.dv.loop(0, 1, 8)
        for bin_number in range(3):
            phase = middle_of_bin(bin_number)
            mux.sample_native(0, 0, DV_F, phase, 64, 2,
                              direction=1, trigger_mask=0)
            mux.sample_native(0, 1, DV_T, phase, 64, 2,
                              direction=1, trigger_mask=0)
        phase = middle_of_bin(3)
        self.assertEqual(mux.sample_native(0, 0, DV_F, phase, 64, 2,
                                           direction=1, trigger_mask=1),
                         locked_f[3] << 24)
        self.assertEqual(mux.sample_native(0, 1, DV_T, phase, 64, 2,
                                           direction=1, trigger_mask=1),
                         locked_t[0] << 24)
        self.assertEqual(mux.dv.loop(0, 1, 8), locked_t)
        self.assertEqual(mux.sample_native(0, 1, DV_T, middle_of_bin(4),
                                           64, 2, direction=1, trigger_mask=0),
                         locked_t[1] << 24)

    def test_all_loop_lengths_and_two_cycles_for_32(self):
        for mode, length in enumerate((2, 4, 8, 16, 32)):
            mux = OfflineWaveMux(100 + mode, lambda *_: 0)
            locked = mux.dv.loop(1, 2, length)
            self.assertEqual(mux.sample_native(1, 2, DV_F, 0, 64, mode,
                                               direction=1, trigger_mask=0),
                             locked[0] << 24)
            if length == 32:
                for step in range(1, 33):
                    output = mux.sample_native(
                        1, 2, DV_F, middle_of_bin(step % 16), 64, mode,
                        direction=1, trigger_mask=0)
                    if step == 16:
                        self.assertEqual(output, locked[16] << 24)
                    if step == 32:
                        self.assertEqual(output, locked[0] << 24)
            else:
                self.assertEqual(mux.sample_native(1, 2, DV_F, 0, 64, mode,
                                                   direction=1, trigger_mask=0,
                                                   phase_delta=PERIOD),
                                 locked[16 % length] << 24)

    def test_native_reorder_keeps_values(self):
        mux = OfflineWaveMux(321, lambda *_: 0)
        before = mux.dv.loop(7, 1, 8)
        mux.sample_native(7, 1, DV_F, 0, 127, 2,
                          direction=1, trigger_mask=0)
        for _ in range(6):
            mux.sample_native(7, 1, DV_F, 0, 127, 2,
                              direction=1, trigger_mask=0,
                              phase_delta=PERIOD)
        self.assertCountEqual(before, mux.dv.loop(7, 1, 8))
        self.assertNotEqual(before, mux.dv.loop(7, 1, 8))


if __name__ == "__main__":
    unittest.main()
