"""Tests für Max Pain, Spread-Prüfung und auffällige Aktivität (app/markt/optionen.py)."""
import unittest

import pandas as pd

from app.markt.optionen import auffaellig, max_pain, spread


class Optionen(unittest.TestCase):
    def test_max_pain(self):
        # Viele Calls bei 110, viele Puts bei 90: am wenigsten wert ist alles dazwischen bei 100
        calls = pd.DataFrame({"strike": [90, 100, 110], "openInterest": [10, 10, 500]})
        puts = pd.DataFrame({"strike": [90, 100, 110], "openInterest": [500, 10, 10]})
        self.assertEqual(max_pain(calls, puts), 100)
        leer = pd.DataFrame({"strike": [100], "openInterest": [0]})
        self.assertIsNone(max_pain(leer, leer))

    def test_spread(self):
        s = spread(99.9, 100.1, 100, 300, 200)
        self.assertTrue(s["gueltig"])
        self.assertAlmostEqual(s["spread_prozent"], 0.2, places=3)
        self.assertFalse(spread(620, 655.84, 594.51)["gueltig"])  # alte Quotes am Wochenende (MU, 22.11.2026)
        self.assertFalse(spread(None, 10, 10)["gueltig"])

    def test_auffaellig(self):
        k = pd.DataFrame({"strike": [600, 650, 700], "volume": [8151, 400, 900], "openInterest": [317, 10, 5000],
                          "lastPrice": [24.77, 1.0, 2.0], "impliedVolatility": [1.01, 0.9, float("nan")]})
        a = auffaellig(k, "Put", "2026-11-23")
        self.assertEqual([x["strike"] for x in a], [600.0])  # 650 zu wenig Volumen, 700 weniger als offen
        self.assertAlmostEqual(a[0]["praemie"], 8151 * 24.77 * 100)


if __name__ == "__main__":
    unittest.main()
