"""Tests für die Formelsprache der Kennzahlen-Bibliothek (app/markt/fundament/kennzahlen.py, Punkt 24c).

Geprüft wird, dass der Rechner richtig rechnet, fehlende Werte sauber als "n. a." (None) weitergibt, alles
Gefährliche ablehnt und dass jede Formel der mitgelieferten Bibliothek (vorlagen/markt/kennzahlen.yaml) gültig ist
und nur bekannte Felder benutzt.
Start: .venv\\Scripts\\python.exe -m unittest discover tests
"""
import ast
import unittest
from pathlib import Path
from unittest import mock

from app.markt.fundament import kennzahlen as K
from app.markt.fundament.kennzahlen import FormelFehler, rechne

VORLAGE = Path(__file__).resolve().parent.parent / "vorlagen" / "markt" / "kennzahlen.yaml"
# Felder laut Kopf der YAML-Datei (je Periode), dazu die bereinigten Varianten aus basis.FLUSS
FELDER = {"umsatz", "bruttogewinn", "ebitda", "ebitda_ber", "ebit", "operatives_ergebnis", "nettogewinn", "nettogewinn_ber", "eps", "aktien",
          "fue", "zinsaufwand", "steuerquote", "vorsteuer", "ocf", "capex", "fcf", "abschreibungen", "sbc", "dividenden", "rueckkauf", "cash",
          "schulden", "eigenkapital", "bilanzsumme", "umlaufvermoegen", "kurzfr_verbindlichkeiten", "verbindlichkeiten", "goodwill", "lager",
          "forderungen", "gewinnruecklagen", "working_capital", "investiertes_kapital", "aktien_ausstehend", "umsatzkosten"}
MARKT = {"kurs", "boersenwert", "ev", "mitarbeiter"}
FUNKTIONEN = {"abs", "min", "max", "mittel"}


class Rechnen(unittest.TestCase):
    def test_grundrechenarten_und_reihenfolge(self):
        self.assertEqual(rechne("a + b * c", {"a": 1, "b": 2, "c": 3}), 7)
        self.assertEqual(rechne("(a + b) * c", {"a": 1, "b": 2, "c": 3}), 9)
        self.assertEqual(rechne("a / b - 1", {"a": 110, "b": 100}), 0.10000000000000009)
        self.assertEqual(rechne("a ** 0.5", {"a": 16}), 4)
        self.assertEqual(rechne("-a + 2", {"a": 5}), -3)

    def test_funktionen(self):
        self.assertEqual(rechne("abs(a)", {"a": -3}), 3)
        self.assertEqual(rechne("max(a, b, 0)", {"a": -3, "b": -1}), 0)
        self.assertEqual(rechne("min(a, b)", {"a": 2, "b": 5}), 2)
        self.assertEqual(rechne("mittel(a, b)", {"a": 10, "b": 20}), 15)

    def test_fehlender_wert_ergibt_na(self):
        self.assertIsNone(rechne("a / b", {"a": 1}))
        self.assertIsNone(rechne("a + b", {"a": 1, "b": None}))
        self.assertIsNone(rechne("max(a, b)", {"a": 1, "b": None}))

    def test_mittel_nimmt_den_vorhandenen_wert(self):
        self.assertEqual(rechne("mittel(a, b)", {"a": 10, "b": None}), 10)
        self.assertIsNone(rechne("mittel(a, b)", {}))

    def test_division_durch_null_und_unmoegliches(self):
        self.assertIsNone(rechne("a / b", {"a": 1, "b": 0}))
        self.assertIsNone(rechne("a / (b - b)", {"a": 1, "b": 3}))
        self.assertIsNone(rechne("a ** 1000", {"a": 10}))  # zu groß
        self.assertIsNone(rechne("a ** 0.5", {"a": -8}))  # komplexe Zahl

    def test_text_mit_zahl(self):
        self.assertEqual(rechne("a * 100", {"a": "0.25"}), 25)


class Ablehnen(unittest.TestCase):
    def test_gefaehrliches_wird_abgelehnt(self):
        for formel in ["__import__('os').system('dir')", "open('x')", "a.b", "a[0]", "'text'", "lambda: 1", "a if b else c",
                       "a < b", "a and b", "min(a, key=b)", "[a, b]", "{'a': 1}", "eval('1')", "a.__class__", "(a := 1)", "f'{a}'"]:
            with self.subTest(formel=formel):
                with self.assertRaises(FormelFehler):
                    rechne(formel, {"a": 1, "b": 2, "c": 3})

    def test_unlesbare_formel(self):
        with self.assertRaises(FormelFehler):
            rechne("a +* b", {"a": 1, "b": 2})


class Bibliothek(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.object(K, "DATEI", VORLAGE):
            cls.bib = K.bibliothek()
        cls.nur_heute = set(K.marktwerte({}, None, None, None))

    def test_alle_formeln_gueltig(self):
        self.assertEqual(self.bib["fehler"], [])
        self.assertGreater(len(self.bib["kennzahlen"]), 30)

    def test_nur_bekannte_felder(self):
        erlaubt = FELDER | {f"vj_{f}" for f in FELDER} | MARKT | self.nur_heute
        for kz in self.bib["kennzahlen"]:
            namen = {n.id for n in ast.walk(ast.parse(kz["formel"], mode="eval")) if isinstance(n, ast.Name)} - FUNKTIONEN
            with self.subTest(kennzahl=kz["id"]):
                self.assertEqual(namen - erlaubt, set())
                self.assertEqual(set(kz["positiv"]) - erlaubt, set())

    def test_heute_kennzahlen_sind_markiert(self):
        for kz in self.bib["kennzahlen"]:
            namen = {n.id for n in ast.walk(ast.parse(kz["formel"], mode="eval")) if isinstance(n, ast.Name)}
            if namen & self.nur_heute:
                with self.subTest(kennzahl=kz["id"]):
                    self.assertTrue(kz["aktuell"], "Kennzahl aus Werten, die es nur heute gibt, braucht aktuell: true")

    def test_eindeutig_und_gueltige_einheiten(self):
        ids = [k["id"] for k in self.bib["kennzahlen"]]
        self.assertEqual(len(ids), len(set(ids)))
        for kz in self.bib["kennzahlen"]:
            with self.subTest(kennzahl=kz["id"]):
                self.assertIn(kz["einheit"], {"prozent", "faktor", "betrag", "je_aktie", "tage", "zahl", "punkte"})
                self.assertIn(kz["kategorie"], K.KATEGORIEN)

    def test_sets_nennen_vorhandene_kennzahlen(self):
        ids = {k["id"] for k in self.bib["kennzahlen"]} | {"umsatz_wachstum", "perf_6m"}
        for s in self.bib["sets"].values():
            with self.subTest(set=s["id"]):
                self.assertEqual(set(s["kennzahlen"]) - ids, set())

    def test_beispiel_rechnung(self):
        # Ein paar Kennzahlen mit bekannten Zahlen nachrechnen
        nach_id = {k["id"]: k for k in self.bib["kennzahlen"]}
        werte = {"umsatz": 1000.0, "vj_umsatz": 800.0, "fcf": 150.0, "operatives_ergebnis": 200.0}
        if "rule_of_40" in nach_id:
            w, grund = K._wert(nach_id["rule_of_40"], werte, None)
            self.assertAlmostEqual(w, 40.0)  # 25 % Wachstum + 15 % FCF-Marge
        if "operative_marge" in nach_id:
            w, _ = K._wert(nach_id["operative_marge"], werte, None)
            self.assertAlmostEqual(w, 20.0)


if __name__ == "__main__":
    unittest.main()
