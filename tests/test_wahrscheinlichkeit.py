"""Tests für die optionsimplizite Verteilung (app/markt/wahrscheinlichkeit.py)."""
import math
import unittest

import numpy as np

from app.markt.volaflaeche import _n
from app.markt.wahrscheinlichkeit import _iv_funktion, quantile, ueber


class Wahrscheinlichkeit(unittest.TestCase):
    def test_flache_iv_gleich_black_scholes(self):
        # Ohne Smile muss P(S > K) = N(d2) sein
        f, t, r, s = 100.0, 0.5, 0.04, 0.3
        g = np.geomspace(30, 300, 4000)
        p = ueber(f, t, r, lambda k: s, g)
        for k in (80, 100, 130):
            d2 = (math.log(f / k) - 0.5 * s * s * t) / (s * math.sqrt(t))
            self.assertAlmostEqual(float(np.interp(k, g, p)), float(_n(d2)), places=3)

    def test_erwartungswert_gleich_terminkurs(self):
        kurve = {"f": 100.0, "t": 1.0, "koeff": [0.1, -0.12, 0.25], "von": -0.4, "bis": 0.25}
        g = np.geomspace(1, 600, 20000)
        p = ueber(100.0, 1.0, 0.04, _iv_funktion(kurve), g)
        self.assertAlmostEqual(g[0] + np.trapezoid(p, g), 100.0, delta=0.3)
        self.assertTrue(np.all(np.diff(p) <= 1e-12))  # fällt mit dem Strike

    def test_quantile_und_fluegel_ohne_sprung(self):
        kurve = {"f": 100.0, "t": 0.25, "koeff": [0.5, -0.4, 0.35], "von": -0.3, "bis": 0.2}
        iv = _iv_funktion(kurve)
        links = [iv(100 * math.exp(-0.3 + e)) for e in (-1e-4, 1e-4)]
        self.assertAlmostEqual(links[0], links[1], places=3)  # knickfrei am Rand
        g = np.geomspace(20, 400, 5000)
        q = quantile(g, ueber(100.0, 0.25, 0.04, iv, g))
        self.assertTrue(q["q5"] < q["q25"] < q["q50"] < q["q75"] < q["q95"])


if __name__ == "__main__":
    unittest.main()
