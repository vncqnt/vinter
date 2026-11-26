"""Tests für ähnliche Werte und erwartete Bewegung (reine Funktionen, ohne Netz)."""
import unittest

import numpy as np
import pandas as pd

from app.markt import aehnlich as ae
from app.markt import erwartet as er


def reihe(werte, start="2026-01-01"):
    return pd.Series(werte, index=pd.bdate_range(start, periods=len(werte)), dtype=float)


class Aehnlich(unittest.TestCase):
    def test_ausrichten_start_100_und_luecken(self):
        a = reihe([10, 11, 12, 13])
        b = reihe([20, 22, 24, 26]).drop(a.index[2])          # ein Feiertag fehlt bei b
        df = ae.ausrichten({"A": a, "B": b}, "A", 3)
        self.assertEqual(list(df.iloc[0]), [100.0, 100.0])
        self.assertAlmostEqual(df["B"].iloc[2], 110.0)        # mit dem Vortag gefüllt
        self.assertAlmostEqual(df["A"].iloc[-1], 130.0)

    def test_gleichlauf(self):
        rng = np.random.default_rng(1)
        r = rng.normal(0, 0.01, 300)
        basis = reihe(100 * np.cumprod(1 + r))
        doppelt = reihe(100 * np.cumprod(1 + 2 * r))
        zufall = reihe(100 * np.cumprod(1 + rng.normal(0, 0.01, 300)))
        g = ae.gleichlauf({"X": basis, "Y": doppelt, "Z": zufall}, "X")
        self.assertGreater(g["Y"]["korrelation"], 0.99)
        self.assertAlmostEqual(g["Y"]["beta"], 2.0, delta=0.05)
        self.assertLess(abs(g["Z"]["korrelation"]), 0.2)

    def test_um_termin(self):
        s = reihe(list(range(100, 140)))
        tag = s.index[10].strftime("%Y-%m-%d")
        nach = ae.um_termin(s, tag, "nach Börse")   # Reaktion am Tag 11, Basis = Schluss Tag 10
        self.assertEqual(len(nach), ae.FENSTER_VOR + ae.FENSTER_NACH + 1)
        self.assertEqual(nach[ae.FENSTER_VOR], 0.0)
        vor = ae.um_termin(s, tag, "vor Börse")     # Reaktion am Tag 10, Basis = Schluss Tag 9
        self.assertAlmostEqual(vor[ae.FENSTER_VOR + 1], (110 / 109 - 1) * 100, places=2)
        self.assertIsNone(ae.um_termin(s, s.index[2].strftime("%Y-%m-%d"), None))   # zu wenig davor

    def test_feste_gruppen(self):
        w = ae.werte_fuer({"art": "wert", "id": "CL=F", "ticker": "CL=F"})
        self.assertIn("BZ=F", w["werte"])
        f = ae.werte_fuer({"art": "richtung", "id": "faktor:klein_gross", "gruppe": "Faktoren", "mitglieder": ["IWM", "SPY"]})
        self.assertEqual(f["werte"], ["IWM", "SPY"])


class Erwartet(unittest.TestCase):
    def test_je_tag(self):
        self.assertAlmostEqual(er.je_tag(10, 4), 5)
        self.assertAlmostEqual(er.je_tag(3, 1), 3)

    def test_straddle_mitte_und_rueckfall(self):
        calls = pd.DataFrame({"strike": [95, 100, 105], "bid": [6, 3, 1], "ask": [6.4, 3.4, 1.2], "lastPrice": [6.1, 3.1, 1.1]})
        puts = pd.DataFrame({"strike": [95, 100, 105], "bid": [1, 2.8, 6], "ask": [1.2, 3.2, 6.4], "lastPrice": [1.1, 3.0, 6.2]})
        kette = type("K", (), {"calls": calls, "puts": puts})
        preis, letzte = er.straddle(kette, 100.4)
        self.assertAlmostEqual(preis, 3.2 + 3.0)
        self.assertFalse(letzte)
        calls.loc[:, ["bid", "ask"]] = 0                      # Feiertag: keine Geld- und Briefkurse
        preis, letzte = er.straddle(kette, 100.4)
        self.assertAlmostEqual(preis, 3.1 + 3.0)
        self.assertTrue(letzte)

    def test_historisch(self):
        s = reihe([100 * 1.01 ** i for i in range(400)])
        h = er.historisch(s, 1)
        self.assertAlmostEqual(h["mittel"], 1.0, places=2)
        self.assertEqual(er.anteil_groesser(h["werte"], 0.5), 100.0)


class Richtung(unittest.TestCase):
    def test_kennzahlen_und_breite(self):
        from app.markt import richtung as ri
        tage = pd.bdate_range("2025-01-01", periods=300)
        df = pd.DataFrame({"HOCH": np.linspace(100, 200, 300), "TIEF": np.linspace(200, 100, 300)}, index=tage)
        k = ri.kennzahlen(df)
        self.assertTrue(k["HOCH"]["ueber200"])
        self.assertFalse(k["TIEF"]["ueber50"])
        self.assertAlmostEqual(k["HOCH"]["tag"], (200 / df["HOCH"].iloc[-2] - 1) * 100, places=2)
        self.assertEqual(k["HOCH"]["abstand_hoch"], 0.0)
        b = ri.breite(k)
        self.assertEqual((b["n"], b["ueber200"], b["nahe_hoch"]), (2, 50.0, 50.0))

    def test_korb_gleichgewichtet(self):
        from app.markt import richtung as ri
        tage = pd.bdate_range("2025-01-01", periods=3)
        df = pd.DataFrame({"A": [100, 110, 110], "B": [100, 90, 90]}, index=tage)   # +10 % und -10 % am selben Tag
        k = ri.korb(df)
        self.assertAlmostEqual(k.iloc[-1], 100.0)


if __name__ == "__main__":
    unittest.main()
