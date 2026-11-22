"""Tests für die Makro-Daten (Punkt 28): Einlesen der echten Formate, Umformen, Ampel, Sahm-Regel, Satz."""
import unittest

import pandas as pd

from app.markt import makrodaten as m


def _k(id_):
    return next(k for k in m.KENNZAHLEN if k["id"] == id_)


class Einlesen(unittest.TestCase):
    def test_bls(self):
        d = {"status": "REQUEST_SUCCEEDED", "Results": {"series": [{"seriesID": "LNS14000000", "data": [
            {"year": "2026", "period": "M10", "value": "4.1"}, {"year": "2026", "period": "M09", "value": "4.2"},
            {"year": "2025", "period": "M13", "value": "4.0"}]}]}}
        s = m.bls_lesen(d)["LNS14000000"]
        self.assertEqual(list(s.values), [4.2, 4.1])
        self.assertEqual(s.index[-1], pd.Timestamp(2026, 10, 1))

    def test_bea(self):
        text = '%SeriesCode,Period,Value\nA191RL,2026Q2,"2.2"\nA191RL,2026Q3,"3.4"\nA001RC,2026Q3,"244,142"\nDPCERG,2026M09,"132.257"\n'
        r = m.bea_lesen(text, {"A191RL", "DPCERG"})
        self.assertEqual(r["A191RL"].index[-1], pd.Timestamp(2026, 7, 1))
        self.assertEqual(r["A191RL"].iloc[-1], 3.4)
        self.assertEqual(r["DPCERG"].iloc[0], 132.257)

    def test_census(self):
        z = [["cell_value", "time_slot_id", "time"], ["701860", "0", "2026-01"], ["707959", "0", "2026-02"]]
        self.assertEqual(m.census_lesen(z).iloc[-1], 707959.0)

    def test_eurostat(self):
        d = {"dimension": {"time": {"category": {"index": {"2026-08": 0, "2026-09": 1, "2026-10": 2}}}}, "value": {"0": 2.4, "2": 2.6}}
        s = m.eurostat_lesen(d)
        self.assertEqual(len(s), 2)
        self.assertEqual(s[pd.Timestamp(2026, 10, 1)], 2.6)

    def test_ezb_quartal(self):
        text = "KEY,TIME_PERIOD,OBS_VALUE\nX,2026-Q2,1.2\nX,2026-Q3,1.3\n"
        s = m.ezb_lesen(text)
        self.assertEqual(s.index[-1], pd.Timestamp(2026, 7, 1))


class Rechnen(unittest.TestCase):
    def test_vorjahr(self):
        idx = pd.date_range("2024-01-01", periods=24, freq="MS")
        s = pd.Series([100.0] * 12 + [103.0] * 12, index=idx)
        j = m.umformen(s, "jahr")
        self.assertEqual(len(j), 12)
        self.assertAlmostEqual(j.iloc[-1], 3.0)

    def test_diff(self):
        s = pd.Series([100.0, 110.0, 105.0], index=pd.date_range("2026-01-01", periods=3, freq="MS"))
        self.assertEqual(list(m.umformen(s, "diff")), [10.0, -5.0])

    def test_ampel(self):
        self.assertEqual(m.ampel(3.1, (1.5, 2.5), "hoch"), "heiß")
        self.assertEqual(m.ampel(1.0, (1.5, 2.5), "hoch"), "kalt")
        self.assertEqual(m.ampel(2.0, (1.5, 2.5), "hoch"), "neutral")
        self.assertEqual(m.ampel(3.5, (4.0, 4.6), "tief"), "heiß")   # niedrige Arbeitslosigkeit = eng
        self.assertIsNone(m.ampel(4.0, None, None))

    def test_sahm(self):
        idx = pd.date_range("2025-01-01", periods=16, freq="MS")
        ruhig = pd.Series([4.0] * 16, index=idx)
        self.assertEqual(m.sahm(ruhig), 0.0)
        steigt = pd.Series([4.0] * 13 + [4.6, 4.7, 4.8], index=idx)
        self.assertGreaterEqual(m.sahm(steigt), 0.5)

    def test_kennzahl_payrolls_ampel_nach_schnitt(self):
        idx = pd.date_range("2024-01-01", periods=30, freq="MS")
        stand = pd.Series(range(150000, 150000 + 30 * 100, 100), index=idx, dtype=float)
        stand.iloc[-1] = stand.iloc[-2] + 400      # ein starker Monat
        w = m.kennzahl(_k("payrolls"), stand)
        self.assertEqual(w["wert"], 400.0)
        self.assertEqual(w["stufe"], "neutral")    # 3-Monats-Schnitt 200
        self.assertIn("Normalbereich", w["satz"])
        self.assertNotIn("im dem", w["satz"])

    def test_kennzahl_inflation(self):
        idx = pd.date_range("2015-01-01", periods=140, freq="MS")
        index = pd.Series([100 * 1.03 ** (i / 12) for i in range(140)], index=idx)
        w = m.kennzahl(_k("pce"), index)
        self.assertAlmostEqual(w["wert"], 3.0, places=1)
        self.assertEqual(w["stufe"], "heiß")
        self.assertTrue(w["satz"].startswith("PCE-Inflation 3,0 %"))
        self.assertEqual(len(w["spur"]), 13)


class Kalender(unittest.TestCase):
    """M3: tatsächliche Werte vergangener Termine aus den gespeicherten Reihen."""

    def test_berichtsmonat(self):
        from datetime import date
        self.assertEqual(m.berichtsmonat("US-Inflation CPI (Okt.)", date(2026, 11, 10)), pd.Timestamp(2026, 10, 1))
        self.assertEqual(m.berichtsmonat("US-Arbeitsmarktbericht (Dez.)", date(2027, 1, 8)), pd.Timestamp(2026, 12, 1))
        self.assertEqual(m.berichtsmonat("US-Inflation CPI (März)", date(2027, 4, 10)), pd.Timestamp(2027, 3, 1))
        self.assertIsNone(m.berichtsmonat("Fed-Zinsentscheid", date(2026, 12, 9)))

    def test_luecke_wird_geschaetzt(self):
        idx = [pd.Timestamp(2025, 8, 1), pd.Timestamp(2025, 9, 1), pd.Timestamp(2025, 11, 1)]
        s = m.luecken_fuellen(pd.Series([99.0, 100.0, 104.04], index=idx))
        self.assertAlmostEqual(s[pd.Timestamp(2025, 10, 1)], 102.0, places=2)

    def test_ergebnisse(self):
        from datetime import date
        bls = {"CUUR0000SA0": {"2025-10-01": 100.0, "2026-10-01": 103.0}, "CUUR0000SA0L1E": {"2025-10-01": 100.0, "2026-10-01": 102.5},
               "CES0000000001": {"2026-09-01": 159128.0, "2026-10-01": 159293.0}, "LNS14000000": {"2026-10-01": 4.1}}
        e = m.ergebnis("US-Inflation CPI (Okt.)", date(2026, 11, 10), bls, {}, {})
        self.assertEqual(e["text"], "3,0 % ggü. Vorjahr, Kern 2,5 %")
        e = m.ergebnis("US-Arbeitsmarktbericht (Okt.)", date(2026, 11, 6), bls, {}, {})
        self.assertEqual(e["text"], "+165 Tsd. Stellen, Quote 4,1 %")
        fed = {"2026-12-08": 4.0, "2026-12-09": 4.0, "2026-12-10": 3.75}
        self.assertEqual(m.ergebnis("Fed-Zinsentscheid", date(2026, 12, 9), {}, fed, {})["text"], "Obergrenze 3,75 % (−0,25 Pkt.)")
        self.assertIsNone(m.ergebnis("Fed-Zinsentscheid", date(2026, 12, 9), {}, {"2026-12-08": 4.0}, {}))   # noch kein Wert danach
        self.assertIsNone(m.ergebnis("US-Inflation CPI (Nov.)", date(2026, 12, 10), bls, {}, {}))


if __name__ == "__main__":
    unittest.main()
