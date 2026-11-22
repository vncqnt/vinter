"""Tests für die Einstufung der Makro-Überraschung (app/nutzer/makro_tage.py)."""
import unittest

from app.nutzer.makro_tage import einstufen


class MakroTage(unittest.TestCase):
    def test_einstufen(self):
        self.assertEqual(einstufen(0.08), "heißer")
        self.assertEqual(einstufen(-0.05), "kühler")
        self.assertEqual(einstufen(0.02), "wie erwartet")
        self.assertIsNone(einstufen(None))


if __name__ == "__main__":
    unittest.main()
