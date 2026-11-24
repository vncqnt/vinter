"""Tests für den Szenario-Rechner (ohne Netz)."""
import unittest

import numpy as np

from app.markt import szenario as sz
from app.nutzer import szenario as nsz

HUANG = [{"name": "geht", "p": 0.1, "bewegung": -0.15, "unsicherheit": 0.0}, {"name": "bleibt", "p": 0.9, "bewegung": 0.02, "unsicherheit": 0.0}]


class Rechnen(unittest.TestCase):
    def test_erwartungswert_wie_von_hand(self):
        k = sz.kennzahlen(sz.komponenten(HUANG, 0.01, 1 / 252))
        self.assertAlmostEqual(k["mittel"], 0.1 * -15 + 0.9 * 2, delta=0.05)  # +0,3 %
        self.assertAlmostEqual(k["ausschlag"], 0.1 * 15 + 0.9 * 2, delta=0.15)  # 3,3 %
        self.assertLess(k["var5"], -10)

    def test_gewichte_werden_normiert_und_umverteilt(self):
        neu = sz.mit_gewicht(HUANG + [{"name": "x", "p": 0.0, "bewegung": 0.1}], 0, 0.3)
        self.assertAlmostEqual(sum(s["p"] for s in neu), 1.0)
        self.assertAlmostEqual(neu[0]["p"], 0.3)

    def test_markt_wahrscheinlichkeit_wird_wiedergefunden(self):
        # künstlicher "Markt" mit 25 % für das große Szenario und 1,2-facher Grundschwankung
        x = np.linspace(-0.6, 0.5, 400)
        markt = sz.verteilungsfunktion(sz.komponenten(sz.mit_gewicht(HUANG, 0, 0.25), 0.4 * 1.2, 30 / 365), x)
        fit = sz.markt_anpassen(HUANG, 0, 0.4, 30 / 365, x, markt)
        self.assertAlmostEqual(fit["p"], 0.25, delta=0.03)
        self.assertLessEqual(fit["von"], 0.25)
        self.assertGreaterEqual(fit["bis"], 0.25)

    def test_zwei_gipfel(self):
        x = np.linspace(-0.6, 0.4, 300)
        zwei = sz.dichte(sz.komponenten([{"name": "a", "p": 0.4, "bewegung": -0.3}, {"name": "b", "p": 0.6, "bewegung": 0.05}], 0.2, 10 / 252), x)
        eins = sz.dichte(sz.komponenten([{"name": "a", "p": 1, "bewegung": 0}], 0.3, 30 / 252), x)
        self.assertTrue(sz.zwei_gipfel(x, zwei))
        self.assertFalse(sz.zwei_gipfel(x, eins))

    def test_heatmap_hat_break_even(self):
        h = sz.heatmap(HUANG, 0, 0.3, 20 / 252)
        werte = np.array(h["werte"])
        self.assertGreater(werte[:, 0].min(), 0)   # p = 0: nur das gute Szenario
        self.assertLess(werte[-1, -1], 0)          # großer Absturz mit 50 %
        self.assertEqual(werte.shape, (len(h["bewegung"]), len(h["p"])))

    def test_simulation_level(self):
        s = sz.simulation(HUANG, 0.3, 20, level=[-0.05, -0.5, 0.5])
        self.assertEqual(len(s["schritte"]), 20 + sz.NACHLAUF + 1)
        nah, fern, oben = s["beruehren"]
        self.assertGreater(nah, fern)
        self.assertLess(oben, 5)

    def test_eingaben_pruefen(self):
        with self.assertRaises(ValueError):
            nsz.pruefen({"ticker": "", "datum": "2026-12-24", "szenarien": []})
        with self.assertRaises(ValueError):
            nsz.pruefen({"ticker": "NVDA", "datum": "2026-12-24", "szenarien": [{"name": "a", "p": 100, "bewegung": 1}]})
        e = nsz.pruefen({"ticker": "nvda", "datum": "2026-12-24", "gross": 9, "szenarien": [{"name": "a", "p": "10", "bewegung": "-15,5"}, {"name": "b", "p": 90, "bewegung": 2}]})
        self.assertEqual((e["ticker"], e["gross"], e["szenarien"][0]["bewegung"]), ("NVDA", 1, -15.5))
        self.assertIn("fehler", sz.rechnen("NVDA", "2026-12-24", [{"name": "a", "p": 0.5, "bewegung": 0}, {"name": "b", "p": 0.4, "bewegung": 0}]))


if __name__ == "__main__":
    unittest.main()
