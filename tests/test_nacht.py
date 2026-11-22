"""Tests für Punkt 32: Rendite über Nacht gegen im Handel (app/markt/nacht.py)."""
import unittest

import pandas as pd

from app.markt.nacht import _reihe, kennzahlen


class Nacht(unittest.TestCase):
    def setUp(self):
        tage = pd.bdate_range("2026-01-01", periods=60)
        # Jeden Tag: nachts +1 %, im Handel −0,5 %
        schluss, eroeffnung, c = [], [], 100.0
        for _ in tage:
            o = c * 1.01
            c = o * 0.995
            eroeffnung.append(o); schluss.append(c)
        self.r = _reihe(pd.Series(eroeffnung, tage), pd.Series(schluss, tage))

    def test_zerlegung(self):
        self.assertTrue(self.r["echte_eroeffnung"])
        self.assertAlmostEqual(self.r["nacht"][-1], 0.01, places=6)
        self.assertAlmostEqual(self.r["handel"][-1], -0.005, places=6)

    def test_teile_ergeben_die_gesamtrendite(self):
        k = kennzahlen(self.r, 20)
        self.assertAlmostEqual((1 + k["nacht"] / 100) * (1 + k["handel"] / 100), 1 + k["gesamt"] / 100, places=9)
        self.assertAlmostEqual(k["nacht"], (1.01 ** 20 - 1) * 100, places=3)

    def test_divergenz(self):
        k = kennzahlen(self.r, 20)  # 20 Tage: nachts rund +22 %, im Handel rund −9,5 %
        self.assertEqual(k["divergenz"], "nachts hoch, tagsüber runter")

    def test_ohne_echte_eroeffnung(self):
        tage = pd.bdate_range("2026-01-01", periods=60)
        schluss = pd.Series([100 + i for i in range(60)], tage, dtype=float)
        eroeffnung = schluss.shift(1).fillna(100.0)  # Eröffnung = Vortagesschluss
        r = _reihe(eroeffnung, schluss)
        self.assertFalse(r["echte_eroeffnung"])
        self.assertIsNone(kennzahlen(r, 20)["divergenz"])


if __name__ == "__main__":
    unittest.main()
