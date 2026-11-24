"""Tests für "RSI: Was danach geschah" (ohne Netz)."""
import unittest

import numpy as np
import pandas as pd

from app.markt import rsi_folgen as rf


class RsiFolgen(unittest.TestCase):
    def test_signale_nur_beim_kreuzen_mit_abstand(self):
        r = pd.Series([50, 60, 72, 75, 68, 71, 50, 20, 25, 35, 28] + [50] * 15 + [75])
        s = rf.signale(r, abstand=10)
        self.assertEqual(s["oben"], [2, 26])  # 5 ist zu nah an 2
        self.assertEqual(s["unten"], [7])     # 10 ist zu nah an 7

    def test_ab_tag(self):
        w = np.array([100, 100, 110, 121, 99.0])
        m = rf.ab_tag(w, [2], 1, 2)
        np.testing.assert_allclose(m[0], [100 / 110 * 100 - 100, 0, 10, -10])
        self.assertTrue(np.isnan(rf.ab_tag(w, [4], 0, 2)[0][-1]))

    def test_auswerten_echt_aussehende_reihe(self):
        rng = np.random.default_rng(3)
        s = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, 1500))), index=pd.bdate_range("2020-01-01", periods=1500))
        d = rf.auswerten(s)
        self.assertIsNone(d.get("fehler"))
        self.assertGreater(d["arten"]["oben"]["n"], 5)
        h = d["arten"]["oben"]["horizonte"]["20"]
        self.assertIsNotNone(h["p"])
        self.assertEqual(len(d["arten"]["oben"]["mittel"]), len(d["schritte"]))
        self.assertEqual(d["arten"]["oben"]["mittel"][d["schritte"].index(0)], 0.0)

    def test_zu_kurz(self):
        self.assertIn("fehler", rf.auswerten(pd.Series(np.arange(1, 100, dtype=float))))


if __name__ == "__main__":
    unittest.main()
