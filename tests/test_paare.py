"""Tests für den Paar-Monitor (app/nutzer/paare.py)."""
import unittest

import numpy as np
import pandas as pd

from app.nutzer.paare import kennzahlen


class Paare(unittest.TestCase):
    def test_rueckkehr_zum_mittel(self):
        rng = np.random.default_rng(3)
        idx = pd.bdate_range("2024-01-01", periods=400)
        b = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400))), idx)
        # log(A/B) als AR(1) mit Rückkehr zum Mittel (Halbwertszeit ln2/0,1 ≈ 6,6 Tage)
        x = [0.0]
        for _ in range(399):
            x.append(0.9 * x[-1] + rng.normal(0, 0.01))
        a = b * np.exp(np.array(x))
        k = kennzahlen(a, b)
        self.assertIsNotNone(k["halbwertszeit"])
        self.assertTrue(2 < k["halbwertszeit"] < 20, k["halbwertszeit"])
        self.assertGreater(k["korrelation"], 0.3)
        self.assertEqual(len(k["verlauf"]["daten"]), len(k["verlauf"]["z"]))

    def test_zu_wenig_daten(self):
        idx = pd.bdate_range("2024-01-01", periods=50)
        s = pd.Series(np.linspace(100, 110, 50), idx)
        self.assertIsNone(kennzahlen(s, s * 1.1))


if __name__ == "__main__":
    unittest.main()
