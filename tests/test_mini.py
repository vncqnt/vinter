"""Tests für die Mini-Kennzahlen (app/markt/mini.py)."""
import unittest

import numpy as np
import pandas as pd

from app.markt.mini import kennzahlen


class Mini(unittest.TestCase):
    def test_kennzahlen(self):
        rng = np.random.default_rng(4)
        idx = pd.bdate_range("2024-01-01", periods=400)
        r = rng.normal(0, 0.01, 400)
        r[-1] = 0.05  # großer letzter Tag
        c = pd.Series(100 * np.exp(np.cumsum(r)), idx)
        k = kennzahlen(c)
        self.assertGreater(k["sigma_heute"], 3)
        self.assertLessEqual(k["abstand_hoch52"], 0.0001)
        self.assertGreaterEqual(k["abstand_tief52"], 0)
        self.assertLessEqual(k["drawdown"], 0.0001)
        self.assertTrue(0 <= k["bb_perzentil"] <= 100)

    def test_kompression(self):
        idx = pd.bdate_range("2024-01-01", periods=400)
        rng = np.random.default_rng(9)
        r = np.r_[rng.normal(0, 0.02, 370), rng.normal(0, 0.001, 30)]  # zuletzt sehr ruhig
        k = kennzahlen(pd.Series(100 * np.exp(np.cumsum(r)), idx))
        self.assertTrue(k["kompression"])


if __name__ == "__main__":
    unittest.main()
