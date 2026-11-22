"""Tests für die Schwankungs-Rechnungen (app/markt/schwankung.py) mit künstlichen Kursen bekannter Volatilität."""
import math
import unittest

import numpy as np
import pandas as pd

from app.markt.schwankung import atr, crush, kegel, rang, rv_parkinson, rv_schluss, rv_yang_zhang


def kurse(sigma_jahr=0.30, tage=900, seed=1):
    rng = np.random.default_rng(seed)
    s = sigma_jahr / math.sqrt(252)
    idx = pd.bdate_range("2023-01-02", periods=tage)
    c = 100 * np.exp(np.cumsum(rng.normal(0, s, tage)))
    o = np.r_[100, c[:-1]] * np.exp(rng.normal(0, s * 0.3, tage))
    spanne = np.abs(rng.normal(0, s, tage))
    h = np.maximum(o, c) * np.exp(spanne / 2)
    l = np.minimum(o, c) * np.exp(-spanne / 2)
    return pd.Series(o, idx), pd.Series(h, idx), pd.Series(l, idx), pd.Series(c, idx)


class Schwankung(unittest.TestCase):
    def test_schluss_trifft_vorgabe(self):
        _, _, _, c = kurse(0.30)
        self.assertAlmostEqual(rv_schluss(c, 252).iloc[-1], 30, delta=3.5)

    def test_schaetzer_plausibel(self):
        o, h, l, c = kurse(0.30)
        p, y = rv_parkinson(h, l, 60).iloc[-1], rv_yang_zhang(o, h, l, c, 60).iloc[-1]
        self.assertTrue(5 < p < 80 and 5 < y < 80, (p, y))

    def test_atr_positiv_und_kegel(self):
        o, h, l, c = kurse(0.20)
        self.assertGreater(atr(h, l, c).iloc[-1], 0)
        k = kegel(c)
        self.assertEqual([x["fenster"] for x in k], [10, 20, 30, 60, 90, 120, 252])
        for x in k:
            self.assertTrue(x["min"] <= x["q25"] <= x["median"] <= x["q75"] <= x["max"])
            self.assertTrue(0 <= x["perzentil"] <= 100)

    def test_rang(self):
        r = rang([10, 20, 30, 40], 30)
        self.assertAlmostEqual(r["rang"], 66.67, places=1)
        self.assertEqual(r["perzentil"], 50)

    def test_crush(self):
        iv = {"2026-10-26": 80.0, "2026-10-27": 90.0, "2026-10-29": 60.0}
        c = crush(iv, ["2026-10-28"])
        self.assertEqual((c[0]["vor"], c[0]["nach"]), (90.0, 60.0))
        self.assertAlmostEqual(c[0]["veraenderung"], -33.33, places=1)
        self.assertEqual(crush(iv, ["2026-06-01"]), [])  # keine gespeicherten Tage rund um den Termin


if __name__ == "__main__":
    unittest.main()
