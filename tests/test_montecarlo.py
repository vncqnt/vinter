"""Tests für den Monte-Carlo-Bootstrap (app/nutzer/montecarlo.py)."""
import math
import unittest

import numpy as np

from app.nutzer.montecarlo import block_bootstrap, mit_rendite


class MonteCarlo(unittest.TestCase):
    def test_jeder_tag_gleich_oft(self):
        r = np.arange(100, dtype=float)
        s = block_bootstrap(r, pfade=4000, tage=60, block=20)
        zaehl = np.bincount(s.astype(int).ravel(), minlength=100)
        self.assertLess(zaehl.max() / zaehl.min(), 1.25)  # zirkulär: auch Rand-Tage kommen gleich oft vor

    def test_rendite_ziel(self):
        rng = np.random.default_rng(2)
        r = rng.normal(0.002, 0.015, 800)
        r2 = mit_rendite(r, 7.0)
        self.assertAlmostEqual(np.log1p(r2).mean() * 252, math.log(1.07), places=6)
        self.assertAlmostEqual(np.std(np.log1p(r2)), np.std(np.log1p(r)), places=9)  # Schwankung bleibt
        self.assertIs(mit_rendite(r, None), r)


if __name__ == "__main__":
    unittest.main()
