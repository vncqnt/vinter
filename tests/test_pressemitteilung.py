"""Tests für das Leseprogramm der Zahlen-Auswertung (app/markt/pressemitteilung.py).

Die englischen Beispiele sind kurze Auszüge aus echten Quartalsmitteilungen (SEC 8-K, Anlage 99.1) vom
Oktober/November 2026, so wie sie beim Kopieren aus dem Browser ankommen (Tabellenzellen mit Tab getrennt).
Start: .venv\\Scripts\\python.exe -m unittest discover tests
"""
import unittest
from datetime import date

from app.markt import pressemitteilung as pm

NVIDIA = """NVIDIA Announces Financial Results for Third Quarter Fiscal 2027
•
Revenue of $115.8 billion, up 103% from a year ago
•
Data Center revenue of $107.6 billion, up 110% from a year ago
NVIDIA (NASDAQ: NVDA) today reported revenue for the third quarter ended October 25, 2026, of $115.8 billion, up 20% from the previous quarter and up 103% from a year ago. For the quarter, GAAP and non-GAAP gross margins were 74.8% and 74.9%, respectively. GAAP and non-GAAP earnings per diluted share were $2.70 and $2.68, respectively.
Q3 Fiscal 2027 Summary
GAAP
($ in billions, except earnings per share)\tQ3 FY27\tQ2 FY27\tQ3 FY26\tQ/Q*\tY/Y*
Revenue\t$115.8\t$96.2\t$57.0\t20\t%\t103\t%
Net income\t$65.3\t$59.7\t$31.9\t9\t%\t105\t%
Outlook
NVIDIA's outlook for the fourth quarter of fiscal 2027 is as follows:
Revenue is expected to be $130.0 billion, plus or minus 3%.
GAAP and non-GAAP gross margins are expected to be between 71.0% and 72.0%.
"""

CISCO = """Cisco Reports First Quarter Earnings
•Record revenue of $18.5 billion, up 24% year over year, record GAAP earnings per share of $1.14, up 58% year over year, and record non-GAAP earnings per share of $1.40, up 40% year over year
•Raised FY27 revenue and non-GAAP EPS guidance
◦Earnings per Share: GAAP: $1.14; Non-GAAP: $1.40
•Q2 FY 2027 Guidance:
◦Revenue: $18.0 billion to $18.2 billion
◦Earnings per Share: GAAP: $1.07 to $1.09; Non-GAAP: $1.34 to $1.36
•FY 2027 Guidance:
◦Revenue: $74.8 billion to $75.8 billion
◦Earnings per Share: GAAP: $4.38 to $4.44; Non-GAAP: $5.47 to $5.53
SAN JOSE, Calif. -- November 12, 2026 -- Cisco (NASDAQ: CSCO) today reported first quarter results for the period ended October 24, 2026.
"""

MICROSOFT = """Microsoft Cloud and AI Strength Drives First Quarter Results
•Revenue was $92.3 billion and increased 19%
•Operating income was $45.9 billion and increased 21% (up 22% in constant currency)
•Diluted earnings per share was $4.92 and increased 32% on a GAAP basis, and was $4.89 and increased 18% (up 19% in constant currency) on a non-GAAP basis
•Microsoft Cloud revenue was $63.9 billion and increased 30% (up 31% in constant currency)
"""

TARGET = """Target Corporation Reports Third Quarter 2026 Earnings
Third quarter operating income, which included a $301 million benefit from tariff refunds, was $1.7 billion, compared with $1.1 billion last year.
Net Sales of $27.1 billion in the third quarter were 7.1 percent higher than last year.
"""

AMAZON_TABELLE = """AMAZON.COM ANNOUNCES THIRD QUARTER RESULTS
Amazon.com, Inc. today announced financial results for its third quarter ended September 30, 2026.
Consolidated Statements of Operations (in millions, except per share data)
\tThree Months EndedSeptember 30,\tNine Months EndedSeptember 30,\t
\t2025\t2026\t2025\t2026\t
Operating income\t$\t17,411\t$\t28,103\t$\t53,999\t$\t72,433\t
Diluted earnings per share\t$\t1.95\t$\t1.89\t$\t5.22\t$\t10.42\t
"""

APPLIED = """Applied Materials Announces Fourth Quarter and Fiscal Year 2026 Results
Business Outlook
Applied's total revenue and non-GAAP diluted EPS for the first quarter of fiscal 2027 are expected to be as follows:
\tQ1 FY2027\t
(In millions, except per share amounts)\t
Total revenue\t$\t11,100\t+/-\t$\t500\t
Non-GAAP diluted EPS\t$\t4.35\t+/-\t$\t0.20\t
"""

DEUTSCH = """Musterfirma AG: Starkes drittes Quartal 2026
Walldorf, 21. Oktober 2026 – Die Musterfirma AG hat im dritten Quartal 2026 ihren Umsatz um 9 % auf 8,47 Mrd. € gesteigert (Vorjahr: 7,74 Mrd. €).
Das operative Ergebnis stieg um 12 % auf 2,10 Mrd. €. Das Ergebnis je Aktie (IFRS) lag bei 1,23 €, das bereinigte Ergebnis je Aktie (non-IFRS) bei 1,45 €.
Ausblick
Für das Geschäftsjahr 2026 erwartet die Musterfirma einen Umsatz von 33,1 bis 33,6 Mrd. €. Die Prognose wird bestätigt.
"""


class Zahlen(unittest.TestCase):
    def test_englisch_und_deutsch(self):
        self.assertEqual(pm.zahl("1,234.5", False), 1234.5)
        self.assertEqual(pm.zahl("1.234,5", True), 1234.5)
        self.assertEqual(pm.zahl("8,47", True), 8.47)
        self.assertEqual(pm.zahl("11,100", False), 11100)


class Kennzahlen(unittest.TestCase):
    def test_nvidia_gaap_und_bereinigt_mit_respectively(self):
        r = pm.auswerten(NVIDIA)
        w = r["werte"]
        self.assertAlmostEqual(w["umsatz"]["wert"], 115.8e9)
        self.assertEqual(w["umsatz"]["vj_prozent"], 103)
        self.assertEqual(w["eps"]["wert"], 2.70)
        self.assertEqual(w["eps_adj"]["wert"], 2.68)
        self.assertEqual(w["brutto_marge"]["wert"], 74.8)
        self.assertAlmostEqual(w["netto"]["wert"], 65.3e9)  # aus der Tabelle mit "$ in billions"
        self.assertEqual(r["ticker"], "NVDA")
        self.assertEqual(r["quartal"]["text"], "Q3 GJ 2027")

    def test_segment_zaehlt_nicht_als_umsatz(self):
        r = pm.auswerten(MICROSOFT)
        self.assertAlmostEqual(r["werte"]["umsatz"]["wert"], 92.3e9)  # nicht "Microsoft Cloud revenue"

    def test_gaap_und_non_gaap_basis_nach_dem_wert(self):
        w = pm.auswerten(MICROSOFT)["werte"]
        self.assertEqual(w["eps"]["wert"], 4.92)
        self.assertEqual(w["eps_adj"]["wert"], 4.89)

    def test_beschriftete_werte(self):
        w = pm.auswerten(CISCO)["werte"]
        self.assertEqual(w["eps"]["wert"], 1.14)
        self.assertEqual(w["eps_adj"]["wert"], 1.40)

    def test_posten_mit_artikel_ist_nicht_der_wert(self):
        w = pm.auswerten(TARGET)["werte"]
        self.assertAlmostEqual(w["operativ"]["wert"], 1.7e9)  # nicht "a $301 million benefit"

    def test_tabelle_mit_vorjahr_in_der_ersten_spalte(self):
        w = pm.auswerten(AMAZON_TABELLE)["werte"]
        self.assertAlmostEqual(w["operativ"]["wert"], 28_103e6)
        self.assertEqual(w["eps"]["wert"], 1.89)

    def test_deutsch(self):
        r = pm.auswerten(DEUTSCH)
        w = r["werte"]
        self.assertEqual(r["sprache"], "de")
        self.assertAlmostEqual(w["umsatz"]["wert"], 8.47e9)
        self.assertEqual(w["umsatz"]["waehrung"], "EUR")
        self.assertAlmostEqual(w["umsatz"]["vj_prozent"], (8.47 / 7.74 - 1) * 100)
        self.assertAlmostEqual(w["operativ"]["wert"], 2.10e9)
        self.assertEqual(w["eps"]["wert"], 1.23)
        self.assertEqual(w["eps_adj"]["wert"], 1.45)
        self.assertEqual(r["quartal"]["text"], "Q3 2026")


class Ausblick(unittest.TestCase):
    def test_plus_minus_prozent_und_spanne(self):
        a = {x["kennzahl"]: x for x in pm.auswerten(NVIDIA)["ausblick"]}
        self.assertAlmostEqual(a["umsatz"]["von"], 126.1e9)
        self.assertAlmostEqual(a["umsatz"]["bis"], 133.9e9)
        self.assertEqual(a["umsatz"]["periode"], "Quartal")
        self.assertEqual((a["brutto_marge"]["von"], a["brutto_marge"]["bis"]), (71.0, 72.0))

    def test_quartal_und_jahr_getrennt(self):
        r = pm.auswerten(CISCO)
        a = {(x["kennzahl"], x["periode"]): x for x in r["ausblick"]}
        self.assertAlmostEqual(a[("umsatz", "Quartal")]["mitte"], 18.1e9)
        self.assertAlmostEqual(a[("umsatz", "Jahr")]["mitte"], 75.3e9)
        self.assertEqual((a[("eps_adj", "Quartal")]["von"], a[("eps_adj", "Quartal")]["bis"]), (1.34, 1.36))
        self.assertEqual((a[("eps", "Jahr")]["von"], a[("eps", "Jahr")]["bis"]), (4.38, 4.44))
        self.assertEqual(r["prognose"]["art"], "angehoben")
        # Die Prognose-Zeilen zählen nicht als gemeldete Zahlen
        self.assertAlmostEqual(r["werte"]["umsatz"]["wert"], 18.5e9)

    def test_tabelle_mit_plus_minus_betrag(self):
        a = {x["kennzahl"]: x for x in pm.auswerten(APPLIED)["ausblick"]}
        self.assertAlmostEqual(a["umsatz"]["von"], 10.6e9)
        self.assertAlmostEqual(a["umsatz"]["bis"], 11.6e9)
        self.assertAlmostEqual(a["eps_adj"]["mitte"], 4.35)

    def test_deutsch_spanne_und_bestaetigt(self):
        r = pm.auswerten(DEUTSCH)
        a = {x["kennzahl"]: x for x in r["ausblick"]}
        self.assertEqual(a["umsatz"]["periode"], "Jahr")
        self.assertAlmostEqual(a["umsatz"]["von"], 33.1e9)
        self.assertAlmostEqual(a["umsatz"]["bis"], 33.6e9)
        self.assertEqual(r["prognose"]["art"], "bestätigt")


class Ordner(unittest.TestCase):
    def test_kalenderquartal_nach_den_meisten_monaten(self):
        from app.nutzer.zahlen import _ordner_aus_datum
        self.assertEqual(_ordner_aus_datum(date(2026, 10, 25)), "2026-Q3")  # August bis Oktober
        self.assertEqual(_ordner_aus_datum(date(2026, 9, 30)), "2026-Q3")
        self.assertEqual(_ordner_aus_datum(date(2026, 11, 30)), "2026-Q4")
        self.assertEqual(_ordner_aus_datum(date(2027, 1, 31)), "2026-Q4")


if __name__ == "__main__":
    unittest.main()
