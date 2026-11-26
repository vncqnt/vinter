"""Tests für den Bezug der Arbeitsplätze (ohne Netz)."""
import unittest
from unittest import mock

from app.markt import bezug as bz
from app.nutzer import tafel


class Bezug(unittest.TestCase):
    def setUp(self):
        p = mock.patch("app.markt.namen.name", side_effect=lambda t: f"Name {t}")
        p.start()
        self.addCleanup(p.stop)

    def test_arten_und_stellvertreter(self):
        self.assertEqual(bz.wert_art("ES=F"), "future")
        self.assertEqual(bz.wert_art("EURUSD=X"), "devise")
        self.assertEqual(bz.wert_art("^GDAXI"), "index")
        self.assertEqual(bz.wert_art("BTC-USD"), "krypto")
        self.assertEqual(bz.wert_art("MU"), "aktie")
        self.assertEqual(bz.optionen_fuer("ES=F"), ("SPY", True))
        self.assertEqual(bz.optionen_fuer("MU"), ("MU", False))
        self.assertEqual(bz.optionen_fuer("RHM.DE"), (None, False))
        self.assertEqual(bz.optionen_fuer("^GDAXI"), (None, False))

    def test_wert(self):
        w = bz.aufloesen("nq=f")
        self.assertEqual((w["art"], w["ticker"], w["optionen"], w["cot"], w["vola_index"]), ("wert", "NQ=F", "QQQ", "ndx", "^VXN"))
        with self.assertRaises(ValueError):
            bz.aufloesen("kein ticker!")

    def test_richtungen(self):
        ids = [r["id"] for r in bz.richtungen()]
        self.assertIn("markt:sp500", ids)
        self.assertIn("sektor:XLK", ids)
        self.assertIn("klasse:oel", ids)
        r = bz.aufloesen("klasse:oel")
        self.assertEqual((r["art"], r["ticker"], r["optionen"], r["cot"]), ("richtung", "CL=F", "USO", "oel"))
        f = bz.aufloesen("faktor:klein_gross")
        self.assertEqual(f["mitglieder"], ["IWM", "SPY"])
        s = bz.aufloesen("sektor:XLE")
        self.assertEqual((s["ticker"], s["yahoo_sektor"]), ("XLE", "Energy"))
        with self.assertRaises(ValueError):
            bz.aufloesen("sektor:XYZ")
        with self.assertRaises(ValueError):
            bz.aufloesen("thema:gibt es nicht")


class Layout(unittest.TestCase):
    def test_bezug_bleibt_beim_pruefen(self):
        roh = {"aktiv": "A", "layouts": [
            {"name": "A", "kacheln": [{"art": "gamma", "w": 4, "h": 2}], "vorlage": "e", "bezug": {"art": "wert", "id": "NVDA", "name": "NVIDIA"}},
            {"name": "B", "kacheln": [], "bezug": {"art": "richtung", "id": "thema:KI-Strom & Versorger"}},
            {"name": "C", "kacheln": [], "bezug": {"art": "boese", "id": "x"}, "vorlage": "<script>"},
        ]}
        aus = tafel._pruefen(roh)["layouts"]
        self.assertEqual(aus[0]["bezug"], {"art": "wert", "id": "NVDA", "name": "NVIDIA"})
        self.assertEqual(aus[0]["vorlage"], "e")
        self.assertEqual(aus[1]["bezug"]["id"], "thema:KI-Strom & Versorger")
        self.assertNotIn("bezug", aus[2])
        self.assertNotIn("vorlage", aus[2])


if __name__ == "__main__":
    unittest.main()
