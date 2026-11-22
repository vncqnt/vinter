"""Tests für das Backtest-Labor (app/markt/backtest.py): Kosten, kein Blick in die Zukunft, Kennzahlen."""
import unittest

import numpy as np
import pandas as pd

from app.markt.backtest import ergebnis, kennzahlen, position


class Backtest(unittest.TestCase):
    def setUp(self):
        idx = pd.bdate_range("2020-01-01", periods=300)
        self.c = pd.Series(100 * np.exp(np.cumsum(np.random.default_rng(5).normal(0, 0.01, 300))), idx)

    def test_kein_blick_in_die_zukunft(self):
        # Position am Tag t darf sich nicht ändern, wenn man spätere Kurse verändert
        p1 = position("trend", {"n": 50}, self.c)
        c2 = self.c.copy()
        c2.iloc[200:] *= 3
        p2 = position("trend", {"n": 50}, c2)
        self.assertTrue((p1.iloc[:200] == p2.iloc[:200]).all())
        for regel, par in (("rsi", {"x": 30, "y": 65}), ("ruecksetzer", {"x": 5, "n": 20, "h": 20}), ("momentum", {"n": 63})):
            a, b = position(regel, par, self.c), position(regel, par, c2)
            self.assertTrue((a.iloc[:200] == b.iloc[:200]).all(), regel)

    def test_kosten_und_verschiebung(self):
        pos = pd.Series([0, 1, 1, 0], pd.bdate_range("2021-01-01", periods=4), dtype=float)
        r = pd.Series([0.0, 0.10, 0.10, 0.10], pos.index)
        e = ergebnis(pos, r, 0.01)
        # Signal am Schluss von Tag 1, wirksam ab Tag 2: dort Rendite und die Kosten des Kaufs
        self.assertAlmostEqual(e.iloc[1], 0.0)
        self.assertAlmostEqual(e.iloc[2], 0.10 - 0.01)
        self.assertAlmostEqual(e.iloc[3], 0.10)           # Verkaufssignal an Tag 3, Kosten fallen am Tag danach an

    def test_kennzahlen(self):
        t = pd.Series([0.01] * 252)
        k = kennzahlen(t)
        self.assertAlmostEqual(k["gesamt"], (1.01 ** 252 - 1) * 100)
        self.assertEqual(k["max_dd"], 0.0)


if __name__ == "__main__":
    unittest.main()
