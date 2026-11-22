"""Tests für das Trade-Journal: FIFO-Zuordnung, Haltedauer, Kennzahlen (app/nutzer/journal.py)."""
import unittest

import pandas as pd

from app.nutzer.journal import kennzahlen, rundreisen


def t(tag, typ, stueck, betrag, gebuehr=1.0, isin="X1", name="Test"):
    return {"zeit": pd.Timestamp(tag, tz="Europe/Berlin"), "typ": typ, "art": "STOCK", "name": name, "isin": isin,
            "stueck": stueck if typ == "BUY" else -stueck, "betrag": betrag, "gebuehr": gebuehr, "vorlaeufig": False}


class Journal(unittest.TestCase):
    def test_fifo_und_haltedauer(self):
        roh = [t("2026-01-01", "BUY", 10, -1000), t("2026-01-11", "BUY", 10, -2000), t("2026-01-21", "SELL", 15, 2400)]
        zeilen, warn = rundreisen(roh)
        v = zeilen[-1]
        # Einstand: 10 × 100,1 + 5 × 200,1 = 2001,5; Erlös 2400 − 1 = 2399
        self.assertAlmostEqual(v["einstand"], 2001.5)
        self.assertAlmostEqual(v["gewinn"], 397.5)
        self.assertAlmostEqual(v["haltedauer"], (10 * 20 + 5 * 10) / 15)
        self.assertEqual(warn, [])

    def test_verkauf_ohne_kauf(self):
        zeilen, warn = rundreisen([t("2026-01-01", "SELL", 1, 100)])
        self.assertTrue(zeilen[0]["ohne_kauf"])
        self.assertEqual(len(warn), 1)

    def test_kennzahlen(self):
        v = [{"gewinn": 30, "gewinn_prozent": 30, "haltedauer": 100, "betrag": 130, "danach": {"4": 10}},
             {"gewinn": 10, "gewinn_prozent": 10, "haltedauer": 50, "betrag": 110, "danach": {"4": -10}},
             {"gewinn": -10, "gewinn_prozent": -10, "haltedauer": 20, "betrag": 90, "danach": {}}]
        k = kennzahlen(v)
        self.assertAlmostEqual(k["trefferquote"], 66.67, places=1)
        self.assertEqual(k["verhaeltnis"], 2.0)
        self.assertAlmostEqual(k["erwartung"], 10.0)
        self.assertAlmostEqual(k["liegen_gelassen"]["4"]["summe"], 13 - 11)
        self.assertTrue(k["stichprobe_klein"])


if __name__ == "__main__":
    unittest.main()
