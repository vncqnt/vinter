"""Tests für Volatility Decay und Beta im Absicherungs-Rechner (app/nutzer/absicherung.py)."""
import math
import unittest

import numpy as np
import pandas as pd

from app.nutzer.absicherung import beta, decay_faktor, zertifikat_wert


class Absicherung(unittest.TestCase):
    def test_decay(self):
        self.assertAlmostEqual(decay_faktor(-3, 0.30, 60 / 252), math.exp(-12 * 0.09 * (60 / 252) / 2))
        self.assertAlmostEqual(decay_faktor(1, 0.5, 1.0), 1.0)          # ohne Hebel kein Decay
        self.assertLess(decay_faktor(-5, 0.4, 0.25), decay_faktor(-2, 0.4, 0.25))  # mehr Hebel, mehr Decay

    def test_zertifikat_mit_bewegung(self):
        self.assertAlmostEqual(zertifikat_wert(-2, -0.1, 0.0, 1.0), 0.9 ** -2)

    def test_beta(self):
        rng = np.random.default_rng(1)
        idx = pd.Series(rng.normal(0, 0.01, 500))
        depot = 1.5 * idx + pd.Series(rng.normal(0, 0.002, 500))
        b, k = beta(depot, idx)
        self.assertAlmostEqual(b, 1.5, delta=0.05)
        self.assertGreater(k, 0.9)


if __name__ == "__main__":
    unittest.main()
