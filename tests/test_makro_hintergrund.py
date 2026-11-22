"""Tests für den Makro-Hintergrund (M4, M6): Fed-Rechnung für frühere Tage, fällige Termine, Quellen je Termin."""
import unittest
from datetime import date, datetime

from app.markt.fed_futures import anteile, rechnen
from app.markt.makro_hintergrund import BERLIN, faellige, quellen_fuer


class Fed(unittest.TestCase):
    def test_rechnen_erhoehung(self):
        # Sitzung am 9.12., 22 Tage danach im Monat; Futures sagen 4,05 % im Dezember, Tagesgeld 3,88 %
        geld = {"satz": 3.88, "von": 3.75, "bis": 4.0}
        r = rechnen(geld, {(2026, 12): 4.05, (2027, 1): 4.10}, [date(2026, 12, 9)])
        self.assertEqual(len(r), 1)
        a = anteile(r[0])
        self.assertGreater(a["erhoehung"], 0.5)
        self.assertAlmostEqual(a["erhoehung"] + a["senkung"] + a["halten"], 1.0, places=6)

    def test_ohne_future_kein_ergebnis(self):
        self.assertEqual(rechnen({"satz": 3.88, "von": 3.75, "bis": 4.0}, {}, [date(2026, 12, 9)]), [])


class Termine(unittest.TestCase):
    def test_quellen(self):
        self.assertEqual(quellen_fuer("US-Inflation CPI (Nov.)"), ["makro:bls"])
        self.assertEqual(quellen_fuer("Fed-Zinsentscheid"), ["fred:DFEDTARU"])
        self.assertEqual(quellen_fuer("EZB-Zinsentscheid"), ["makro:ezb:FM/D.U2.EUR.4F.KR.DFR.LEV"])
        self.assertEqual(quellen_fuer("Ifo-Index"), [])

    def test_faellig_erst_20_minuten_danach(self):
        t = [{"datum": "2026-12-10", "uhrzeit": "14:30", "name": "US-Inflation CPI (Nov.)"}]
        self.assertEqual(faellige(t, datetime(2026, 12, 10, 14, 40, tzinfo=BERLIN), set()), [])
        self.assertEqual(len(faellige(t, datetime(2026, 12, 10, 14, 55, tzinfo=BERLIN), set())), 1)
        self.assertEqual(faellige(t, datetime(2026, 12, 10, 15, 0, tzinfo=BERLIN), {"2026-12-10:US-Inflation CPI (Nov.)"}), [])
        self.assertEqual(faellige(t, datetime(2026, 12, 11, 15, 0, tzinfo=BERLIN), set()), [])


if __name__ == "__main__":
    unittest.main()
