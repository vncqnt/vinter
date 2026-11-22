"""Tests für die Regel-Simulation der Exit-Analyse (app/nutzer/exitanalyse.py)."""
import unittest

import pandas as pd

from app.nutzer.exitanalyse import simuliere


def kurse(werte, start="2026-01-01"):
    idx = pd.bdate_range(start, periods=len(werte))
    s = pd.Series(werte, idx, dtype=float)
    return pd.DataFrame({"Open": s, "High": s * 1.01, "Low": s * 0.99, "Close": s})


class Exitanalyse(unittest.TestCase):
    def setUp(self):
        # 100 -> steigt auf 130 -> fällt auf 90
        self.df = kurse([100, 105, 110, 120, 130, 128, 125, 115, 100, 95, 90])
        self.kauf, self.verkauf = self.df.index[0], self.df.index[6]  # Verkauf bei 125

    def test_eigen_und_halten(self):
        self.assertAlmostEqual(simuliere(self.df, self.kauf, self.verkauf, {"art": "eigen", "wert": None})["rendite"], 0.25)
        h = simuliere(self.df, self.kauf, self.verkauf, {"art": "heute", "wert": None})
        self.assertAlmostEqual(h["rendite"], -0.10)
        self.assertTrue(h["offen"])

    def test_ziel(self):
        # +25 % zuerst bei 130 (Tag 4): Hälfte +30 %, Rest wie du (+25 %) = +27,5 %
        self.assertAlmostEqual(simuliere(self.df, self.kauf, self.verkauf, {"art": "ziel", "wert": 25})["rendite"], 0.275)
        # Ziel nie erreicht: alles wie du
        self.assertAlmostEqual(simuliere(self.df, self.kauf, self.verkauf, {"art": "ziel", "wert": 50})["rendite"], 0.25)

    def test_haltedauer(self):
        r = simuliere(self.df, self.kauf, self.verkauf, {"art": "halten", "wert": 7})  # Do 1.1. + 7 Tage = Do 8.1., Kurs 128
        self.assertAlmostEqual(r["rendite"], 0.28)
        self.assertFalse(r["offen"])

    def test_atr_stop_loest_nach_hoch_aus(self):
        r = simuliere(self.df, self.kauf, self.verkauf, {"art": "atr", "wert": 2})
        self.assertFalse(r["offen"])
        self.assertGreater(r["tag"], self.df.index[4])  # erst nach dem Hoch


if __name__ == "__main__":
    unittest.main()
