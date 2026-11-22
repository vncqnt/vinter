"""Tests für Beiträge (Analyse-Charts) und Cross-Asset-Veränderungen."""
import unittest

import pandas as pd

from app.markt.zinsen import veraenderungen
from app.nutzer.analyse_charts import beitraege


class AnalyseCharts(unittest.TestCase):
    def test_beitraege(self):
        idx = pd.bdate_range("2026-01-01", periods=10)
        kurse = {"A": pd.Series(range(100, 110), idx, dtype=float), "B": pd.Series([50.0] * 10, idx)}
        b = beitraege({"A": 2, "B": 4}, kurse, idx[4])
        self.assertAlmostEqual(b["A"]["beitrag"], 2 * (109 - 104))
        self.assertAlmostEqual(b["B"]["beitrag"], 0.0)
        self.assertFalse(b["A"]["ab_spaeter"])

    def test_veraenderungen(self):
        idx = pd.bdate_range("2025-12-01", periods=300)
        s = pd.Series([100.0 + i for i in range(300)], idx)
        v = veraenderungen(s)
        self.assertAlmostEqual(v["1t"], (399 / 398 - 1) * 100)
        vorjahr = s[s.index < pd.Timestamp(f"{s.index[-1].year}-01-01")].iloc[-1]
        self.assertAlmostEqual(v["ytd"], (399 / vorjahr - 1) * 100)


if __name__ == "__main__":
    unittest.main()
