"""Tests für die Bewertungsregeln des Markt-Regimes (app/markt/regime.py)."""
import unittest

from app.markt.regime import einstufen, linear, s_breite, s_gamma, s_kredit, s_skew, s_vix


class Regime(unittest.TestCase):
    def test_linear(self):
        self.assertEqual(linear(0.88, 0.88, 1.02), 1.0)
        self.assertEqual(linear(1.02, 0.88, 1.02), -1.0)
        self.assertAlmostEqual(linear(0.95, 0.88, 1.02), 0.0)
        self.assertEqual(linear(2.0, 0.88, 1.02), -1.0)  # begrenzt

    def test_regeln(self):
        self.assertEqual(s_vix(1.16), -1.0)
        self.assertEqual(s_skew(4), 1.0)
        self.assertEqual(s_breite(75), 1.0)
        self.assertEqual(s_gamma(-6.5), -1.0)
        self.assertAlmostEqual(s_kredit(50, 0.0), 0.0)
        self.assertAlmostEqual(s_kredit(50, 0.6), -0.5)   # schnelle Ausweitung
        self.assertAlmostEqual(s_kredit(10, 1.2), -0.2)

    def test_einstufen(self):
        self.assertEqual(einstufen(30), "Risk-on")
        self.assertEqual(einstufen(-25), "Risk-off")
        self.assertEqual(einstufen(0), "neutral")


if __name__ == "__main__":
    unittest.main()


class Kreditwarnung(unittest.TestCase):
    def test_ausweitung(self):
        import pandas as pd
        from app.markt.stil import ausweitung
        self.assertEqual(ausweitung(pd.Series([3.0] * 30 + [3.6]))["warnung"], "schnell")
        self.assertEqual(ausweitung(pd.Series([3.0] * 30 + [4.1]))["warnung"], "sehr schnell")
        self.assertIsNone(ausweitung(pd.Series([3.0] * 30 + [3.2]))["warnung"])
        self.assertEqual(ausweitung(pd.Series([2.0] * 30 + [2.45]))["warnung"], "schnell")  # +22 %


class AdvanceDecline(unittest.TestCase):
    def test_zaehlung(self):
        import pandas as pd
        from app.markt.signale import advance_decline
        k = pd.DataFrame({"A": [10, 11, 12], "B": [10, 9, 9], "C": [10, 11, 10]})
        ad = advance_decline(k)
        self.assertEqual(ad["auf"].tolist(), [0, 2, 1])
        self.assertEqual(ad["ab"].tolist(), [0, 1, 1])
        self.assertEqual(ad["netto"].cumsum().tolist(), [0, 1, 1])


class Walls(unittest.TestCase):
    def test_waende(self):
        import pandas as pd
        from app.markt.positionierung import waende
        tab = pd.DataFrame({"art": ["C", "C", "P", "P", "C"], "strike": [105, 110, 95, 90, 200], "gex": [5.0, 9.0, -7.0, -3.0, 99.0]})
        w = waende(tab, 100)
        self.assertEqual((w["call_wall"], w["put_wall"]), (110.0, 95.0))  # 200 liegt außerhalb von ±15 %
