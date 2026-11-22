"""Tests für die Fed-Tage: Termine aus den Kalenderseiten der Fed und Reaktion um den Termin.

Die Ausschnitte folgen dem Aufbau der echten Seiten (fomccalendars.htm und fomchistorical2020.htm, Stand 22.11.2026).
"""
import unittest

import pandas as pd

from app.markt.fed_tage import entscheidung, normal, reaktionen, termine_lesen

AKTUELL = """
<div class="fomc-meeting__month col-xs-5"><strong>July</strong></div>
            <div class="fomc-meeting__date col-xs-4">29-30</div>
  <a href="/newsevents/pressreleases/monetary20250730a.htm">HTML</a>
<div class="fomc-meeting__month col-xs-5"><strong>August</strong></div>
            <div class="fomc-meeting__date col-xs-4">22 (notation vote)</div>
  <a href="/newsevents/pressreleases/monetary20250822a.htm">HTML</a>
<div class="fomc-meeting__month col-xs-5"><strong>September</strong></div>
            <div class="fomc-meeting__date col-xs-4">16-17*</div>
  <a href="/newsevents/pressreleases/monetary20250917a.htm">HTML</a>
<div class="fomc-meeting__month col-xs-5"><strong>January</strong></div>
            <div class="fomc-meeting__date col-xs-4">26-27</div>
"""
HISTORISCH = """
<h5 class="panel-title">March 2 (unscheduled) Meeting - 2020</h5>
 <a href="/newsevents/pressreleases/monetary20200303a.htm">Statement</a>
<h5>March 17-18 (cancelled) Meeting - 2020</h5>
<h5>March 19 (notation vote) - 2020</h5>
 <a href="/newsevents/pressreleases/monetary20200319a.htm">Statement</a>
<h5>April 28-29 Meeting - 2020</h5>
 <a href="/newsevents/pressreleases/monetary20200429a.htm">Statement</a>
"""


class FedTage(unittest.TestCase):
    def test_aktuelle_seite(self):
        t = termine_lesen(AKTUELL)
        self.assertEqual([x["datum"] for x in t], ["2025-07-30", "2025-09-17"])  # ohne notation vote und Zukunft
        self.assertTrue(t[1]["projektionen"])

    def test_jahresseite(self):
        t = termine_lesen(HISTORISCH)
        self.assertEqual([x["datum"] for x in t], ["2020-03-03", "2020-04-29"])  # Datum der Mitteilung, nicht der Sitzung
        self.assertTrue(t[0]["notsitzung"])

    def test_entscheidung(self):
        ziel = pd.Series([1.75, 1.75, 1.25, 1.25], index=pd.to_datetime(["2020-03-01", "2020-03-02", "2020-03-04", "2020-03-05"]))
        self.assertEqual(entscheidung(ziel, pd.Timestamp("2020-03-03").date()), ("gesenkt", -50))

    def test_reaktion_wochenende(self):
        # Entscheidung am Sonntag: Reaktion = Freitag bis Montag, Folgetag = Dienstag
        s = pd.Series([100.0, 90.0, 99.0], index=pd.to_datetime(["2020-03-13", "2020-03-16", "2020-03-17"]))
        r = reaktionen(s, ["2020-03-15"])["2020-03-15"]
        self.assertAlmostEqual(r["tag"], -0.10)
        self.assertAlmostEqual(r["folge"], -0.01)

    def test_luecke_und_normal(self):
        s = pd.Series([100.0, 101.0, 102.0, 100.0, 101.0], index=pd.bdate_range("2024-01-01", periods=5))
        self.assertEqual(reaktionen(s, ["2023-06-01"]), {})  # vor Beginn der Reihe
        n = normal(s, [])
        self.assertGreater(n["tag"], 0)


if __name__ == "__main__":
    unittest.main()
