"""Tests für den Post-Earnings-Drift (app/markt/drift.py)."""
import unittest

import pandas as pd

from app.markt.drift import drift_reihe, einordnen


class Drift(unittest.TestCase):
    def test_drift_gegen_markt(self):
        idx = pd.bdate_range("2026-01-01", periods=80)
        aktie = pd.Series([100 * 1.002 ** i for i in range(80)], idx)
        markt = pd.Series([100.0] * 80, idx)
        d = drift_reihe(aktie, markt, idx[5], fenster=(20, 60))
        self.assertAlmostEqual(d["20"], (1.002 ** 20 - 1) * 100, places=6)
        self.assertNotIn("bisher", d)
        kurz = drift_reihe(aktie, markt, idx[70], fenster=(20, 60))
        self.assertEqual(kurz["bisher"], 9)  # Fenster noch nicht voll

    def test_einordnen(self):
        termine = [{"reaktion": 5, "drift": {"60": 3}}] * 6 + [{"reaktion": -4, "drift": {"60": -2}}] * 2 + [{"reaktion": 5, "drift": {"60": -1}}] * 2
        e = einordnen(termine)
        self.assertEqual(e["anzahl"], 10)
        self.assertAlmostEqual(e["fortsetzung"], 80.0)
        self.assertEqual(e["urteil"], "typisch")
        self.assertIsNone(einordnen(termine[:5])["urteil"])  # unter 8 Terminen


if __name__ == "__main__":
    unittest.main()
