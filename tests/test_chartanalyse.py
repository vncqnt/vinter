"""Tests der Chartanalyse. Starten im Projektordner mit:
    .venv\\Scripts\\python.exe -m unittest discover tests
Die Tests brauchen kein Internet: Sie bauen sich eigene Kurstabellen.
"""
import unittest

import numpy as np
import pandas as pd

from app.markt import chart_konfig as K
from app.markt.chartanalyse import analysiere, ausbrueche, ipo_modus, swings, zonen_clustern


def tabelle(schluss, volumen=None, spanne=0.5):
    """Kurstabelle aus Schlusskursen: Hoch/Tief = Schluss ± spanne, Eröffnung = Vortagesschluss."""
    schluss = np.asarray(schluss, dtype=float)
    tage = pd.bdate_range("2024-01-01", periods=len(schluss))
    return pd.DataFrame({"Open": np.r_[schluss[0], schluss[:-1]], "High": schluss + spanne, "Low": schluss - spanne,
                         "Close": schluss, "Volume": np.full(len(schluss), 1000.0) if volumen is None else np.asarray(volumen, float)}, index=tage)


def welle(tage=300, seed=1):
    rng = np.random.default_rng(seed)
    x = np.arange(tage)
    return 100 + 10 * np.sin(x / 9) + np.cumsum(rng.normal(0, 0.6, tage))


class SwingTest(unittest.TestCase):
    def test_einfaches_hoch_und_tief(self):
        kurse = [10, 11, 12, 13, 14, 20, 14, 13, 12, 11, 10, 5, 10, 11, 12, 13, 14]
        p = swings(tabelle(kurse, spanne=0), n=5)
        self.assertIn(("hoch", 5), [(x["art"], x["i"]) for x in p])
        self.assertIn(("tief", 11), [(x["art"], x["i"]) for x in p])

    def test_kein_blick_in_die_zukunft(self):
        """Ein Swing-Punkt an Position i darf erst existieren, wenn i + n Tage da sind; auf jeder
        gekürzten Tabelle müssen genau die Punkte der vollen Tabelle mit bestaetigt_i <= Ende stehen."""
        df = tabelle(welle(), spanne=0.3)
        n = 5
        voll = swings(df, n)
        for ende in range(30, len(df), 7):
            teil = swings(df.iloc[:ende + 1], n)
            erwartet = [(p["art"], p["i"]) for p in voll if p["bestaetigt_i"] <= ende]
            self.assertEqual([(p["art"], p["i"]) for p in teil], erwartet, f"Ende {ende}")
            self.assertTrue(all(p["i"] <= ende - n for p in teil))

    def test_letzte_n_tage_nie_swing(self):
        kurse = list(range(1, 40)) + [100]  # das höchste Hoch ganz am Ende
        p = swings(tabelle(kurse, spanne=0), n=5)
        self.assertFalse([x for x in p if x["i"] >= len(kurse) - 5])


class ZonenTest(unittest.TestCase):
    def test_clustering_nach_toleranz(self):
        punkte = [{"preis": v} for v in (100, 100.2, 100.45, 105, 105.3, 110)]
        gruppen = zonen_clustern(punkte, 0.5)
        self.assertEqual([len(g) for g in gruppen], [3, 2, 1])

    def test_keine_kette(self):
        """Viele Punkte mit kleinen Abständen dürfen keine riesige Zone bilden."""
        gruppen = zonen_clustern([{"preis": 100 + 0.3 * k} for k in range(20)], 0.5)
        self.assertTrue(all(g[-1]["preis"] - g[0]["preis"] <= 0.5 for g in gruppen))

    def test_clustering_trennt_bei_groesserem_abstand(self):
        gruppen = zonen_clustern([{"preis": 100}, {"preis": 100.6}], 0.5)
        self.assertEqual(len(gruppen), 2)

    def test_leer(self):
        self.assertEqual(zonen_clustern([], 1), [])


class AusbruchTest(unittest.TestCase):
    def _df(self, letzter_schluss, letztes_volumen):
        kurse = [95.0] * 25 + [letzter_schluss]
        vol = [1000.0] * 25 + [letztes_volumen]
        return tabelle(kurse, vol, spanne=0.2)

    def test_bruch_nach_oben_mit_volumen(self):
        b = ausbrueche(self._df(102.5, 2000), 99, 100, ab_i=0)  # 2,5 % über der Oberkante, Volumen doppelt
        self.assertEqual(b["seite"], "ueber")
        self.assertEqual(b["ereignisse"][-1]["richtung"], "oben")

    def test_kein_bruch_ohne_volumen(self):
        b = ausbrueche(self._df(102.5, 900), 99, 100, ab_i=0)
        self.assertEqual(b["seite"], "unter")
        self.assertFalse(b["ereignisse"])

    def test_kein_bruch_unter_2_prozent(self):
        b = ausbrueche(self._df(101.5, 3000), 99, 100, ab_i=0)  # nur 1,5 % darüber
        self.assertFalse(b["ereignisse"])

    def test_rollentausch_zurueck(self):
        kurse = [95.0] * 25 + [103.0] * 3 + [96.0]
        vol = [1000.0] * 25 + [3000.0] * 3 + [5000.0]
        b = ausbrueche(tabelle(kurse, vol, spanne=0.2), 99, 100, ab_i=0)
        self.assertEqual([e["richtung"] for e in b["ereignisse"]], ["oben", "unten"])
        self.assertEqual(b["seite"], "unter")


class IpoTest(unittest.TestCase):
    def test_umschaltung(self):
        kurz = tabelle(np.linspace(20, 30, K.IPO_TAGE - 1))
        lang = tabelle(np.linspace(20, 30, K.IPO_TAGE))
        self.assertIsNotNone(ipo_modus(kurz))
        self.assertIsNone(ipo_modus(lang))

    def test_analyse_blendet_swings_aus(self):
        a = analysiere(tabelle(np.linspace(20, 30, 30)))
        self.assertIsNotNone(a["ipo"])
        self.assertEqual(a["zonen"], [])
        self.assertIsNone(a["trend"]["regime"])

    def test_ipo_preis_eingetragen(self):
        i = ipo_modus(tabelle(np.linspace(20, 30, 10)), ipo_preis=18)
        self.assertEqual(i["ipo_preis"], 18)
        self.assertEqual(i["ipo_quelle"], "eingetragen")


class GesamtTest(unittest.TestCase):
    def test_analyse_laeuft_und_begrenzt_zonen(self):
        a = analysiere(tabelle(welle(600, seed=3), spanne=0.8))
        self.assertLessEqual(len(a["zonen"]), K.MAX_ZONEN)
        self.assertIn(a["trend"]["regime"], ("Aufwärtstrend", "Abwärtstrend", "Seitwärts / unklar"))
        for z in a["zonen"]:
            self.assertLess(z["unten"], z["oben"])


if __name__ == "__main__":
    unittest.main()
