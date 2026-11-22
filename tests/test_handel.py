"""Tests für die Einleser der Handelsdaten (Punkt 31b), mit echten Lieferungen aus tests/daten/handel.

Start: .venv\\Scripts\\python.exe -m unittest tests.test_handel
"""
import json
import unittest
from pathlib import Path

from app.markt.handel import einlesen as E
from app.markt.handel.laender import iso3, von_comtrade

DATEN = Path(__file__).parent / "daten" / "handel"


def lies(name: str) -> str:
    return (DATEN / name).read_text(encoding="utf-8", errors="ignore")


class Laender(unittest.TestCase):
    def test_namen(self):
        for name, iso in [("KOREA, SOUTH", "KOR"), ("R. O. Korea", "KOR"), ("Taiwan, China", "TWN"), ("United States (US)", "USA"),
                          ("of which: Germany", "DEU"), ("Hong Kong, China", "HKG"), ("Viet Nam", "VNM"), ("CHINA", "CHN"), ("United Kingdom (UK)", "GBR")]:
            self.assertEqual(iso3(name), iso, name)
        self.assertIsNone(iso3("European Union"))

    def test_comtrade_codes(self):
        self.assertEqual(von_comtrade(842), "USA")
        self.assertEqual(von_comtrade(0), "WLD")
        self.assertEqual(von_comtrade(490), "TWN")


class Gacc(unittest.TestCase):
    def test_laender_oktober(self):
        zeilen, probleme, unbekannt = E.gacc_laender(lies("gacc_laender_okt.html"))
        self.assertEqual(probleme, [])
        welt = {z["richtung"]: z["wert_usd"] for z in zeilen if z["partnerland"] == "WLD"}
        self.assertAlmostEqual(welt["export"], 412330.3e6)
        self.assertAlmostEqual(welt["import"], 307053.0e6)
        usa = [z for z in zeilen if z["partnerland"] == "USA" and z["richtung"] == "export"][0]
        self.assertEqual(usa["zeitraum"], "2026-10")
        self.assertAlmostEqual(usa["wert_usd"], 46414.1e6)
        self.assertIn("European Union", unbekannt)  # Regionen werden nicht als Land gespeichert
        hk = [z for z in zeilen if z["partnerland"] == "HKG"][0]
        self.assertIn("reexport_moeglich", hk["sonderfall"])

    def test_februar_mit_berechnetem_januar(self):
        zeilen, probleme, _ = E.gacc_laender(lies("gacc_laender_feb.html"))
        self.assertEqual(probleme, [])
        feb = [z for z in zeilen if z["partnerland"] == "WLD" and z["richtung"] == "export" and z["zeitraum"] == "2026-02"][0]
        jan = [z for z in zeilen if z["partnerland"] == "WLD" and z["richtung"] == "export" and z["zeitraum"] == "2026-01"][0]
        self.assertAlmostEqual(feb["wert_usd"], 299878.0e6)
        self.assertAlmostEqual(jan["wert_usd"], (656577.6 - 299878.0) * 1e6, places=-3)
        self.assertIn("januar_berechnet", jan["sonderfall"])

    def test_aeltere_tabellen(self):
        # 2024: Monate ausgeschrieben, Januar und Februar nur zusammen, doppelte Leerzeichen im Titel
        zeilen, probleme, _ = E.gacc_laender(lies("gacc_laender_maerz_2024.html"))
        self.assertEqual(probleme, [])
        self.assertEqual(zeilen[0]["zeitraum"], "2024-03")
        zeilen, probleme, _ = E.gacc_laender(lies("gacc_laender_janfeb_2024.html"))
        self.assertEqual(probleme, [])
        self.assertEqual(zeilen[0]["zeitraum"], "2024-02")
        self.assertIn("jan_feb_kombiniert", zeilen[0]["sonderfall"])
        self.assertFalse(any(z["zeitraum"] == "2024-01" for z in zeilen))

    def test_waren(self):
        zeilen, probleme = E.gacc_waren(lies("gacc_export_okt.html"), "export")
        self.assertEqual(probleme, [])
        chips = [z for z in zeilen if z["ware_text"].startswith("Electronic integrated circuits")][0]
        self.assertAlmostEqual(chips["wert_usd"], 39594.4e6)
        self.assertEqual(chips["ware"], "cn:electronic_integrated_circuits")

    def test_verweise(self):
        v = E.gacc_verweise(lies("gacc_index.html"), 2026)
        self.assertIn("2026-10", v["4"])
        self.assertTrue(v["4"]["2026-10"].startswith("http"))
        alt = E.gacc_verweise(lies("gacc_index_2024.html"), 2024)  # ältere Seiten haben Zeilenumbrüche im HTML
        self.assertEqual(sorted(alt["4"])[-1], "2024-12")
        self.assertEqual(len(alt["4"]), 11)  # Februar bis Dezember, Januar steckt in der Februar-Tabelle

    def test_strukturaenderung(self):
        kaputt = lies("gacc_laender_okt.html").replace("Import Source", "Quelle")
        with self.assertRaises(E.StrukturFehler):
            E.gacc_laender(kaputt)
        with self.assertRaises(E.StrukturFehler):
            E.gacc_laender("<html><p>Wartungsarbeiten</p></html>")

    def test_summenpruefung(self):
        falsch = lies("gacc_laender_okt.html").replace("412,330.3", "400,000.0", 1)
        _, probleme, _ = E.gacc_laender(falsch)
        self.assertTrue(probleme)  # Export + Import passt nicht mehr zur Summe


class Tsmc(unittest.TestCase):
    def test_monate(self):
        zeilen, probleme = E.tsmc(lies("tsmc_2026.html"), 2026)
        self.assertEqual(probleme, [])
        self.assertEqual(len(zeilen), 10)
        self.assertEqual(zeilen[0]["zeitraum"], "2026-01")
        self.assertAlmostEqual(zeilen[-1]["wert_orig"], 586890e6)

    def test_kaputt(self):
        with self.assertRaises(E.StrukturFehler):
            E.tsmc("<table><tr><td>x</td></tr></table>", 2026)


class Census(unittest.TestCase):
    def test_importe(self):
        daten = json.loads(lies("census_import_8542.json"))
        zeilen, probleme, unbekannt = E.census(daten, "import", "8542", "Chips", 1)
        self.assertEqual(probleme, [])
        welt = [z for z in zeilen if z["partnerland"] == "WLD"][0]
        laender = sum(z["wert_usd"] for z in zeilen if z["partnerland"] != "WLD")
        self.assertLess(abs(laender / welt["wert_usd"] - 1), 0.01)
        self.assertTrue(any(z["partnerland"] == "TWN" for z in zeilen))
        self.assertFalse(any(z["partnerland"] is None for z in zeilen))

    def test_summe_falsch(self):
        daten = json.loads(lies("census_import_8542.json"))
        kopf = daten[0]
        i = kopf.index("GEN_VAL_MO")
        for r in daten[1:]:
            if r[kopf.index("CTY_CODE")] == "-":
                r[i] = str(int(r[i]) * 2)
        _, probleme, _ = E.census(daten, "import", "8542", "Chips", 1)
        self.assertTrue(probleme)


class Comtrade(unittest.TestCase):
    def test_korea(self):
        zeilen, probleme = E.comtrade(json.loads(lies("comtrade_kor_2024.json")), 10)
        self.assertEqual(probleme, [])
        welt = [z for z in zeilen if z["partnerland"] == "WLD" and z["ware"] == "TOTAL"][0]
        self.assertEqual(welt["berichtsland"], "KOR")
        self.assertEqual(welt["zeitraum"], "2024")
        self.assertGreater(welt["wert_usd"], 5e11)
        self.assertTrue(any(z["partnerland"] == "CHN" and z["ware"] == "8542" for z in zeilen))


if __name__ == "__main__":
    unittest.main()
