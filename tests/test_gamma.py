"""Gamma-Exposure: Rechnung ohne Netz (Gamma, Flip, Verfallsauswahl, Zwischenstände)."""
import json
import math
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from app.markt import gamma


class GammaTest(unittest.TestCase):
    def test_gamma_wie_lehrbuch(self):
        # S = K = 100, 1 Jahr, 20 %, r = 0: d1 = 0,1; Gamma = φ(0,1) / (100 · 0,2)
        g = gamma._gamma(100.0, np.array([100.0]), np.array([1.0]), np.array([0.2]), 0.0)[0]
        self.assertAlmostEqual(g, math.exp(-0.005) / math.sqrt(2 * math.pi) / 20, places=10)

    def test_iv_findet_preis_wieder(self):
        f, k, t, r = np.array([100.0]), np.array([105.0]), np.array([0.25]), 0.03
        st = 0.3 * math.sqrt(0.25)
        d1 = (math.log(100 / 105) + 0.5 * st ** 2) / st
        n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
        preis = math.exp(-r * 0.25) * (100 * n(d1) - 105 * n(d1 - st))
        iv = gamma._iv(np.array([preis]), f, k, t, r, np.array([True]))
        self.assertAlmostEqual(iv[0], 0.3, places=4)

    def test_flip_zwischen_puts_und_calls(self):
        # Viele Puts bei 90, viele Calls bei 110: unten negativ, oben positiv, Wechsel dazwischen
        d = pd.DataFrame({"strike": [90.0, 110.0], "oi": [1000.0, 1000.0], "t": [0.1, 0.1], "iv": [0.25, 0.25], "call": [False, True]})
        flip, profil = gamma._flip(d, 100.0, 0.0)
        self.assertIsNotNone(flip)
        self.assertTrue(95 < flip < 105, flip)
        self.assertLess(profil[0][1], 0)
        self.assertGreater(profil[-1][1], 0)

    def test_verfaelle(self):
        heute = date(2026, 11, 23)
        tage = [heute + timedelta(days=i) for i in range(0, 120)]
        alle = tuple(t.isoformat() for t in tage if t.weekday() < 5)
        self.assertEqual(gamma._verfaelle(alle, "0dte", heute), ["2026-11-23"])
        woche = gamma._verfaelle(alle, "woche", heute)
        self.assertTrue(all((date.fromisoformat(v) - heute).days <= 7 for v in woche))
        quartal = gamma._verfaelle(alle, "quartal", heute)
        self.assertLessEqual(len(quartal), gamma.HOECHSTENS_VERFAELLE)
        spaet = [v for v in quartal if (date.fromisoformat(v) - heute).days > 14]
        self.assertTrue(all(date.fromisoformat(v).weekday() == 4 for v in spaet))

    def test_staende_vor_15_und_60_minuten(self):
        with tempfile.TemporaryDirectory() as ordner, mock.patch.object(gamma, "DB", Path(ordner) / "g.sqlite"):
            jetzt = datetime(2026, 11, 23, 18, 0, tzinfo=timezone.utc)
            for minuten in (5, 16, 30, 58, 75):
                z = (jetzt - timedelta(minutes=minuten)).isoformat(timespec="seconds")
                db = gamma._db()
                with db:
                    db.execute("INSERT INTO staende VALUES (?, ?, ?, ?)", ("SPY", "monat", z, json.dumps({"kurs": minuten, "call_gex": 1, "put_gex": 2, "zeilen": {}})))
                db.close()
            jetzt_iso = jetzt.isoformat(timespec="seconds")
            self.assertEqual(gamma._stand_vor("SPY", "monat", jetzt_iso, 15)["kurs"], 16)
            self.assertEqual(gamma._stand_vor("SPY", "monat", jetzt_iso, 60)["kurs"], 58)
            self.assertIsNone(gamma._stand_vor("QQQ", "monat", jetzt_iso, 15))

    def test_ungueltige_ticker(self):
        self.assertIsNone(gamma.gueltig("SAP.DE"))
        self.assertIsNone(gamma.gueltig("../x"))
        self.assertEqual(gamma.gueltig(" spy "), "SPY")
        self.assertIn("fehler", gamma.gamma_exposure("SAP.DE"))


if __name__ == "__main__":
    unittest.main()
