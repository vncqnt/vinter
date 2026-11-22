"""Tests für den Gewichtungs-Rechner (gleicher Risikobeitrag, app/nutzer/umbau.py)."""
import unittest

import numpy as np

from app.nutzer.umbau import gleicher_risikobeitrag, risikoanteile


class Umbau(unittest.TestCase):
    def test_unkorreliert_gleich_kehrwert_vola(self):
        vol = np.array([0.1, 0.2, 0.4])
        w = gleicher_risikobeitrag(np.diag(vol ** 2))
        erwartet = (1 / vol) / (1 / vol).sum()
        np.testing.assert_allclose(w, erwartet, atol=1e-6)

    def test_gleiche_beitraege_mit_korrelation(self):
        vol = np.array([0.15, 0.3, 0.6, 0.25])
        korr = np.array([[1, .6, .3, .2], [.6, 1, .5, .1], [.3, .5, 1, .0], [.2, .1, .0, 1]])
        cov = np.outer(vol, vol) * korr
        w = gleicher_risikobeitrag(cov)
        self.assertAlmostEqual(w.sum(), 1.0)
        np.testing.assert_allclose(risikoanteile(w, cov), np.full(4, 0.25), atol=1e-6)

    def test_eine_position(self):
        self.assertEqual(gleicher_risikobeitrag(np.array([[0.04]])).tolist(), [1.0])


if __name__ == "__main__":
    unittest.main()
