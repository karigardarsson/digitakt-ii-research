"""The offline seam must never change stock WAVE 0..6 calls."""

import unittest

from dv import DV_F, DV_T
from offline_mux import OfflineWaveMux


class MuxTests(unittest.TestCase):
    def test_stock_seven_values_are_verbatim(self):
        calls = []

        def stock(wave, phase):
            calls.append((wave, phase))
            return 0x10000 + wave

        mux = OfflineWaveMux(1, stock)
        for wave in range(7):
            self.assertEqual(mux.sample(0, 0, wave, 0x31415926, 0, 0, 0),
                             0x10000 + wave)
        self.assertEqual(calls, [(wave, 0x31415926) for wave in range(7)])

    def test_dv_never_calls_stock_and_dvt_resets(self):
        def stock(_wave, _phase):
            self.fail("stock called for DV")

        mux = OfflineWaveMux(10, stock)
        first = mux.sample(0, 0, DV_T, 0, 0, 64, 2)
        mux.sample(0, 0, DV_T, 0, 1, 64, 2)
        self.assertEqual(mux.sample(0, 0, DV_T, 0, 2, 64, 2,
                                    confirmed_note_on=True), first)
        self.assertIsInstance(mux.sample(0, 1, DV_F, 0, 0, 64, 2), int)


if __name__ == "__main__":
    unittest.main()
