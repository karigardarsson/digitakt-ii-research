"""Deterministic host tests for the offline waveform model."""

import unittest

from dv import DV_F, DV_T, LENGTHS, DejaVu


class DejaVuTests(unittest.TestCase):
    def test_lengths_and_locked_repeat(self):
        for mode, length in enumerate(LENGTHS):
            dv = DejaVu(0x12345678)
            first = [dv.sample(0, 0, DV_F, n, 64, mode) for n in range(2 * length)]
            self.assertEqual(first[:length], first[length:])

    def test_new_values_and_reorder_extremes(self):
        fresh = DejaVu(0x12345678)
        seq = [fresh.sample(0, 0, DV_F, n, 0, 2) for n in range(48)]
        self.assertGreater(len(set(seq)), 20)
        self.assertNotEqual(seq[:8], seq[8:16])
        shuffled = DejaVu(0x12345678)
        before = shuffled.loop(0, 0, 8)
        for n in range(100):
            shuffled.sample(0, 0, DV_F, n, 127, 2)
        self.assertCountEqual(before, shuffled.loop(0, 0, 8))
        self.assertNotEqual(before, shuffled.loop(0, 0, 8))

    def test_dvt_explicit_retrigger(self):
        dv = DejaVu(42)
        seq = [dv.sample(1, 2, DV_T, n, 64, 2) for n in range(6)]
        self.assertEqual(dv.sample(1, 2, DV_T, 6, 64, 2,
                                   confirmed_note_on=True), seq[0])
        self.assertEqual(dv.sample(1, 2, DV_T, 7, 64, 2), seq[1])
        free, control = DejaVu(42), DejaVu(42)
        free.sample(1, 2, DV_F, 0, 64, 2)
        control.sample(1, 2, DV_F, 0, 64, 2)
        self.assertEqual(free.sample(1, 2, DV_F, 1, 64, 2,
                                     confirmed_note_on=True),
                         control.sample(1, 2, DV_F, 1, 64, 2))

    def test_state_independent_of_track_interleaving(self):
        a, b = DejaVu(73), DejaVu(73)
        expected = [a.sample(4, 1, DV_F, n, 24, 3) for n in range(32)]
        actual = []
        for n in range(32):
            b.sample(9, 0, DV_F, n, 0, 0)
            actual.append(b.sample(4, 1, DV_F, n, 24, 3))
            b.sample(4, 2, DV_F, n, 127, 4)
        self.assertEqual(expected, actual)
        self.assertNotEqual(b.loop(4, 1, 16), b.loop(4, 2, 16))

    def test_repeatable_seed_and_reject_stock(self):
        for mode in range(5):
            a, b = DejaVu(999), DejaVu(999)
            self.assertEqual([a.sample(0, 2, DV_F, n, 33, mode) for n in range(40)],
                             [b.sample(0, 2, DV_F, n, 33, mode) for n in range(40)])
        with self.assertRaises(ValueError):
            DejaVu(1).sample(0, 0, 6, 0, 64, 0)
        with self.assertRaises(ValueError):
            DejaVu(1).sample(0, 0, DV_T, 0, 64, 5)
        with self.assertRaises(ValueError):
            DejaVu(1).sample(16, 0, DV_F, 0, 64, 0)


if __name__ == "__main__":
    unittest.main()
