"""Tests für die Cluster-Erkennung bei Insider-Käufen (app/markt/insider_cluster.py)."""
import unittest

from app.markt.insider_cluster import cluster_finden


def k(tk, tag, wer, wert=1000):
    return {"ticker": tk, "datum": tag, "wer": wer, "wert": wert}


class InsiderCluster(unittest.TestCase):
    def test_drei_in_zehn_tagen(self):
        kaeufe = [k("A", "2026-11-01", "X"), k("A", "2026-11-05", "Y"), k("A", "2026-11-10", "Z"),
                  k("B", "2026-11-01", "X"), k("B", "2026-11-05", "Y"), k("B", "2026-11-20", "Z")]  # B: Z zu spät
        c = cluster_finden(kaeufe)
        self.assertEqual([x["ticker"] for x in c], ["A"])
        self.assertEqual((c[0]["personen"], c[0]["summe"]), (3, 3000))

    def test_gleiche_person_zaehlt_einmal(self):
        self.assertEqual(cluster_finden([k("A", "2026-11-01", "X"), k("A", "2026-11-02", "X"), k("A", "2026-11-03", "Y")]), [])


if __name__ == "__main__":
    unittest.main()
