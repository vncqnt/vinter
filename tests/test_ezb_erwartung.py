"""Tests für den EZB-Zinspfad (M5): Rechnung aus Terminzinsen und Satz nach Regeln."""
import unittest

from app.markt.ezb_erwartung import BASIS, einordnen, pfad


class Pfad(unittest.TestCase):
    def test_ohne_erwartete_aenderung(self):
        # Terminzins = €STR + BASIS (also typischer Abstand) → keine Änderung erwartet
        p = pfad({6: 2.44 + BASIS, 12: 2.44 + BASIS}, estr=2.44, einlage=2.5)
        self.assertAlmostEqual(p[0]["veraenderung"], 0.0, places=6)
        self.assertIn("keine klare Änderung", einordnen(p))

    def test_erhoehung(self):
        p = pfad({6: 2.44 + BASIS + 0.5, 12: 2.44 + BASIS + 0.75}, estr=2.44, einlage=2.5)
        self.assertAlmostEqual(p[0]["einlage"], 3.0, places=6)
        self.assertEqual(p[0]["schritte"], 2.0)
        self.assertIn("Erhöhungen", einordnen(p))

    def test_senkung(self):
        p = pfad({6: 2.0, 12: 1.8}, estr=2.44, einlage=2.5)
        self.assertLess(p[1]["veraenderung"], 0)
        self.assertIn("Senkungen", einordnen(p))


if __name__ == "__main__":
    unittest.main()
