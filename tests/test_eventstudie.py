"""Tests der Event Study (ohne Netz): Tag 0, Pfade, Zufallsbasis, Tests, Filter, Import, Vorschlags-Regeln."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from app.markt import eventstudie as es
from app.nutzer import eventstudie as n


def tage(werte, start="2024-01-01"):
    return pd.Series(werte, index=pd.bdate_range(start, periods=len(werte)), dtype=float)


class Zeitpunkte(unittest.TestCase):
    def test_zeitpunkt_und_tag_null(self):
        t = es.zeit_lesen("2025-04-02T16:00:00-04:00")  # Liberation Day nach Börsenschluss
        self.assertEqual(es.zeitpunkt(t), "nachbörslich")
        self.assertEqual(es.tag_null_datum(t), pd.Timestamp("2025-04-03"))
        self.assertEqual(es.zeitpunkt(es.zeit_lesen("2025-04-09T13:18:00-04:00")), "Handelszeit")
        self.assertEqual(es.zeitpunkt(es.zeit_lesen("2025-05-12T03:00:00-04:00")), "vorbörslich")
        self.assertEqual(es.zeitpunkt(es.zeit_lesen("2025-10-12T12:00:00-04:00")), "Wochenende")

    def test_zone_und_naive_zeit(self):
        self.assertEqual(str(es.zeit_lesen("2025-04-02 16:00").tz), "America/New_York")
        berlin = es.zeit_lesen("2025-04-02 22:00", "Europe/Berlin")
        self.assertEqual(es.zeitpunkt(berlin), "nachbörslich")  # 22 Uhr Berlin = 16 Uhr New York

    def test_wochenende_naechster_handelstag(self):
        s = tage(range(1, 31), "2025-10-01")
        p = es.position_tag(s.index, es.zeit_lesen("2025-10-12T12:00:00-04:00"))
        self.assertEqual(s.index[p], pd.Timestamp("2025-10-13"))


class Rechnen(unittest.TestCase):
    def test_pfade_prozent_und_bp(self):
        w = np.array([100, 100, 110, 121, 121.0])
        m = es.pfade(w, [2], -1, 2, "prozent")
        np.testing.assert_allclose(m[0], [0, 10, 21, 21])
        r = np.array([4.0, 4.1, 4.3])
        self.assertAlmostEqual(es.pfade(r, [2], 0, 0, "bp")[0][0], 20.0)
        # außerhalb der Reihe NaN statt Fehler
        self.assertTrue(np.isnan(es.pfade(w, [4], 0, 3, "prozent")[0][-1]))

    def test_sprung_ist_auffaellig_und_zufall_nicht(self):
        rng = np.random.default_rng(1)
        r = rng.normal(0, 0.01, 1500)
        tage_mit_event = list(range(300, 1400, 100))
        for p in tage_mit_event:
            r[p] = -0.05  # jedes Event −5 %
        s = tage(100 * np.exp(np.cumsum(r)))
        events = [{"id": str(i), "zeit": es.zeit_lesen(f"{s.index[p].date()}T10:00:00")} for i, p in enumerate(tage_mit_event)]
        a = es.auswerten(s, events, -3, 5, "prozent", 0)
        self.assertEqual(a["n"], len(tage_mit_event))
        self.assertLess(a["kennzahlen"]["mittel"], -4)
        self.assertLess(a["basis"]["p"], 0.01)
        self.assertEqual(a["kennzahlen"]["treffer"], 0.0)
        # zufällige Tage als "Events": kein Befund
        zufall = [{"id": str(i), "zeit": es.zeit_lesen(f"{s.index[p].date()}T10:00:00")} for i, p in enumerate(range(250, 1450, 97))]
        b = es.auswerten(s, zufall, -3, 5, "prozent", 2)
        self.assertGreater(b["basis"]["p"], 0.01)

    def test_permutation(self):
        a, b = np.full(10, 1.0) + np.arange(10) * 0.01, np.full(10, -1.0)
        self.assertLess(es.p_permutation(a, b), 0.01)
        self.assertIsNone(es.p_permutation(np.array([1.0]), b))
        rng = np.random.default_rng(2)
        self.assertGreater(es.p_permutation(rng.normal(size=30), rng.normal(size=30)), 0.05)

    def test_kennzahlen(self):
        k = es.kennzahlen(np.array([1.0, -1.0, 2.0, np.nan]))
        self.assertEqual(k["n"], 3)
        self.assertAlmostEqual(k["mittel"], 0.667, places=3)
        self.assertAlmostEqual(k["treffer"], 66.7)

    def test_marktlage(self):
        spx = tage(np.r_[np.full(30, 100.0), np.linspace(100, 110, 25)])
        vix = tage(np.full(55, 25.0))
        lage = es.marktlage(spx, vix, es.zeit_lesen(f"{spx.index[-1].date()}T10:00:00"), {"rally_ab": 5, "vix_hoch_ab": 20})
        self.assertEqual(lage["trend"], "nach Rally")
        self.assertEqual(lage["vix"], "VIX hoch")
        self.assertEqual(lage["hoch"], "nahe Hoch")

    def test_minuten_position_mit_luecke(self):
        idx = pd.date_range("2025-01-02 14:30", periods=60, freq="1min", tz="UTC")
        self.assertEqual(es.position_minute(idx, pd.Timestamp("2025-01-02 14:40:30", tz="UTC")), 11)
        self.assertIsNone(es.position_minute(idx, pd.Timestamp("2025-01-02 20:00", tz="UTC")))


class Benutzerschicht(unittest.TestCase):
    def setUp(self):
        self.datei = Path(tempfile.mkdtemp()) / "events.json"
        self.p = mock.patch.object(n, "_datei", return_value=self.datei)
        self.p.start()
        self.b = mock.Mock(name="b")

    def tearDown(self):
        self.p.stop()

    def test_startdatensatz_bitte_pruefen(self):
        ev = n.events(self.b)
        self.assertEqual(len(ev), 10)
        self.assertTrue(all(e["pruefen"] for e in ev))
        self.assertTrue(any("Liberation Day" in e["titel"] for e in ev))

    def test_pruefen_fehler(self):
        kat = n.alle_kategorien(self.b)["kategorien"]
        with self.assertRaises(ValueError):
            n.pruefen({"zeit": "2025-01-01", "titel": "", "kategorie": "handel"}, kat)
        with self.assertRaises(ValueError):
            n.pruefen({"zeit": "gestern", "titel": "x", "kategorie": "handel"}, kat)
        with self.assertRaises(ValueError):
            n.pruefen({"zeit": "2025-01-01", "titel": "x", "kategorie": "gibtsnicht"}, kat)
        e = n.pruefen({"zeit": "2025-01-01 10:00", "titel": "x", "kategorie": "Handel", "untertyp": "Zölle",
                       "merkmale": {"region": "China, EU"}}, kat)
        self.assertEqual((e["kategorie"], e["untertyp"], e["merkmale"]["region"]), ("handel", "zoelle", ["China", "EU"]))

    def test_csv_import(self):
        text = "zeit;titel;kategorie;untertyp;richtung;region\n2025-03-12T00:01:00-04:00;Stahlzölle 25 %;Handel;zoelle;Eskalation;Welt|EU\nkaputt;;;;;\n"
        r = n.csv_import(self.b, text)
        self.assertEqual(r["neu"], 1)
        self.assertEqual(len(r["fehler"]), 1)
        self.assertEqual(n.csv_import(self.b, text)["neu"], 0)  # doppelt wird nicht eingefügt
        neu = [e for e in n.events(self.b) if e["herkunft"] == "CSV"][0]
        self.assertEqual(neu["merkmale"]["region"], ["Welt", "EU"])

    def test_filtern(self):
        ev = [{"id": "a", "zeit": "2025-04-02T16:00:00-04:00", "kategorie": "handel", "untertyp": "zoelle", "zeitpunkt": "nachbörslich",
               "merkmale": {"richtung": "Eskalation", "region": ["China"]}, "tags": [], "lage": {"trend": "nach Rally"}},
              {"id": "b", "zeit": "2025-04-09T13:18:00-04:00", "kategorie": "handel", "untertyp": "zoelle", "zeitpunkt": "Handelszeit",
               "merkmale": {"richtung": "Deeskalation", "region": ["Welt"]}, "tags": [], "lage": {"trend": "nach Rückgang"}, "pruefen": True}]
        self.assertEqual([e["id"] for e in n.filtern(ev, {"richtung": ["Eskalation"]})], ["a"])
        self.assertEqual([e["id"] for e in n.filtern(ev, {"region": ["Welt", "China"]})], ["a", "b"])
        self.assertEqual([e["id"] for e in n.filtern(ev, {"trend": ["nach Rally"], "richtung": ["Deeskalation"]})], [])
        self.assertEqual([e["id"] for e in n.filtern(ev, {"nur_geprueft": True})], ["a"])
        self.assertEqual([e["id"] for e in n.filtern(ev, {"untertypen": ["handel.zoelle"], "bis": "2025-04-05"})], ["a"])

    def test_test_zaehler(self):
        self.assertEqual(n._test_zaehlen(self.b, {"versionen": [1]}), 1)
        self.assertEqual(n._test_zaehlen(self.b, {"versionen": [1]}), 1)
        self.assertEqual(n._test_zaehlen(self.b, {"versionen": [2]}), 2)


class Vorschlagsregeln(unittest.TestCase):
    def test_richtung(self):
        self.assertEqual(n.richtung_raten("Trump pauses tariffs for 90 days"), "Deeskalation")
        self.assertEqual(n.richtung_raten("U.S. PREPARES TO RAISE TARIFFS ON CHINA"), "Eskalation")
        self.assertEqual(n.richtung_raten("Central bank says inflation high"), "neutral")  # "ban" steckt in "bank"
        self.assertEqual(n.richtung_raten("ECB must raise rates", "geldpolitik"), "neutral")

    def test_pflichtwoerter_und_werbung(self):
        self.assertFalse(n.HANDEL_PFLICHT.search("Insiders breach their fiduciary duties"))
        self.assertTrue(n.HANDEL_PFLICHT.search("New tariffs on chips"))
        self.assertTrue(n.WERBUNG.search("Electronic Warfare Market Report 2026"))
        self.assertEqual(n._untertyp("notenbank", "BoC's Macklem: shocks larger"), "andere_notenbank")
        self.assertEqual(n._untertyp("notenbank", "Fed's Waller: more cuts likely"), "fed_rede")


if __name__ == "__main__":
    unittest.main()
