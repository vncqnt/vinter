"""Tests für die Event-Bewegung (app/markt/eventbewegung.py)."""
import unittest
from types import SimpleNamespace

import pandas as pd

from app.markt.eventbewegung import event_bewegung, straddle, termine_aus_archiv

SEITE = """
<ul><!-- <li><a href="/news.release/archives/empsit_12042026.htm">November 2026</a></li> -->
<li><a href="/news.release/archives/empsit_11062026.htm">October 2026 Employment Situation</a> (<a href="/news.release/archives/empsit_11062026.pdf">PDF</a>)</li>
<li>October 2025 Consumer Price Index &ndash; Not published</li>
<li><a href="/news.release/archives/empsit_10022026.htm">September 2026</a></li></ul>
"""


class Eventbewegung(unittest.TestCase):
    def test_archiv(self):
        self.assertEqual(termine_aus_archiv(SEITE, "empsit"), ["2026-10-02", "2026-11-06"])  # auskommentiert zählt nicht

    def test_bewegung(self):
        # Straddle 5 bis zum Vortag, 13 bis zum Termin bei Kurs 100: sqrt(169 − 25) = 12 %
        self.assertAlmostEqual(event_bewegung(13, 5, 100), 12.0)
        self.assertEqual(event_bewegung(3, 5, 100), 0.0)  # nie negativ
        self.assertIsNone(event_bewegung(None, 5, 100))

    def test_straddle(self):
        calls = pd.DataFrame({"strike": [95, 100, 105], "bid": [6, 2, 0.5], "ask": [6.2, 2.2, 0.7]})
        puts = pd.DataFrame({"strike": [95, 100, 105], "bid": [0.4, 1.8, 5.5], "ask": [0.6, 2.0, 5.7]})
        self.assertAlmostEqual(straddle(SimpleNamespace(calls=calls, puts=puts), 100.4), 2.1 + 1.9)


if __name__ == "__main__":
    unittest.main()
