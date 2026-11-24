"""Newsfeed-Neubau, Phase A/B: Feeds lesen, Speicher, Sichtbarkeit, manueller Eingang, Telegram-Meldung, Umstellung der YAML."""
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.markt.nachrichten import abruf, eingang, extraktion, geschichten, quellen, speicher, telegram


def _rss(eintraege: list[tuple[str, datetime, str]]) -> bytes:
    teile = "".join(
        f"<item><title>{t}</title><link>{l}</link><guid>{l}</guid><pubDate>{z.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
        f"<description>&lt;p&gt;Text zu {t}&lt;/p&gt;</description></item>" for t, z, l in eintraege)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{teile}</channel></rss>'.encode()


class Grundlage(unittest.TestCase):
    def setUp(self):
        self.ordner = Path(tempfile.mkdtemp())
        self.p = [mock.patch.object(speicher, "DB", self.ordner / "n.sqlite"), mock.patch.object(speicher, "_bereit", [False])]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        shutil.rmtree(self.ordner, ignore_errors=True)


class FeedTest(Grundlage):
    def test_eintraege_vorspann_ausblenden_alter(self):
        jetzt = datetime.now(timezone.utc)
        inhalt = _rss([("FinancialJuice: US to impose 25% tariffs on chips", jetzt, "https://a/1"),
                       ("FJElite Correlation Matrix", jetzt, "https://a/2"),
                       ("Alte Meldung", jetzt - timedelta(days=5), "https://a/3")])
        einst = {"vorspann": ["FinancialJuice:"], "ausblenden": ["FJElite"]}
        m = abruf.eintraege({"name": "FJ", "stufe": 2}, inhalt, einst)
        self.assertEqual([x["titel"] for x in m], ["US to impose 25% tariffs on chips"])
        self.assertEqual(m[0]["stufe"], 2)
        self.assertEqual(m[0]["text"], "Text zu FinancialJuice: US to impose 25% tariffs on chips")

    def test_einfuegen_nur_einmal(self):
        m = {"schluessel": speicher.schluessel("Q", "1"), "quelle": "Q", "titel": "A"}
        self.assertEqual(len(speicher.einfuegen([m])), 1)
        self.assertEqual(speicher.einfuegen([m]), [])

    def test_private_meldungen_nur_fuer_besitzer(self):
        speicher.einfuegen([
            {"schluessel": "a", "quelle": "Q", "titel": "für alle"},
            {"schluessel": "b", "quelle": "Telegram @X", "titel": "nur standard", "nur_fuer": "standard"},
        ])
        self.assertEqual({m["titel"] for m in speicher.neueste("standard")}, {"für alle", "nur standard"})
        self.assertEqual({m["titel"] for m in speicher.neueste("freund")}, {"für alle"})

    def test_backoff_nach_fehlern(self):
        q = {"name": "Q", "intervall_s": 60}
        speicher.quelle_melden("Q", False, fehler="HTTP 429")
        speicher.quelle_melden("Q", False, fehler="HTTP 429")
        self.assertAlmostEqual(abruf._naechster(q) - __import__("time").time(), 240, delta=2)
        speicher.quelle_melden("Q", True)
        self.assertAlmostEqual(abruf._naechster(q) - __import__("time").time(), 60, delta=2)


class EingangTest(Grundlage):
    def setUp(self):
        super().setUp()
        self.p2 = mock.patch.object(eingang, "DATEI", self.ordner / "eingang.json")
        self.p2.start()

    def tearDown(self):
        self.p2.stop()
        super().tearDown()

    def test_token(self):
        t1 = eingang.token_erzeugen("standard")
        self.assertEqual(eingang.token_pruefen(f"Bearer {t1}"), "standard")
        self.assertIsNone(eingang.token_pruefen("Bearer vt_falsch"))
        self.assertIsNone(eingang.token_pruefen(None))
        t2 = eingang.token_erzeugen("standard")
        self.assertIsNone(eingang.token_pruefen(f"Bearer {t1}"))  # alter Token ungültig
        self.assertEqual(eingang.token_pruefen(t2), "standard")
        self.assertNotIn(t2, (self.ordner / "eingang.json").read_text())  # nur der Hash liegt auf der Platte

    def test_aufnehmen_text_mit_link(self):
        with mock.patch.object(abruf, "melden"):
            i = eingang.aufnehmen("standard", "Neue Zölle auf Chips https://example.com/x.")
        m = speicher.neueste("standard")[0]
        self.assertEqual((m["id"], m["titel"], m["link"], m["nur_fuer"], m["quelle"]),
                         (i, "Neue Zölle auf Chips", "https://example.com/x", "standard", "von mir"))
        self.assertEqual(speicher.neueste("freund"), [])

    def test_keine_privaten_adressen(self):
        self.assertEqual(eingang.seitentitel("http://127.0.0.1:8000/"), "")
        self.assertEqual(eingang.seitentitel("file:///etc/passwd"), "")
        with self.assertRaises(eingang.EingangFehler):
            eingang.aufnehmen("standard", "", "ftp://x")


class TelegramTest(unittest.TestCase):
    def test_meldung_aus_nachricht(self):
        n = SimpleNamespace(id=42, message="*TRUMP: CHINA TARIFFS TO 60%\nmehr dazu", date=datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc))
        with mock.patch.object(telegram, "key", return_value="standard"):
            m = telegram._meldung("WalterBloomberg", n)
        self.assertEqual((m["titel"], m["text"], m["link"], m["nur_fuer"], m["stufe"]),
                         ("*TRUMP: CHINA TARIFFS TO 60%", "mehr dazu", "https://t.me/WalterBloomberg/42", "standard", 2))
        self.assertIsNone(telegram._meldung("X", SimpleNamespace(id=1, message="", date=None)))


class UmstellungTest(unittest.TestCase):
    def test_alte_datei_wird_beiseitegelegt(self):
        ordner = Path(tempfile.mkdtemp())
        try:
            datei, alt = ordner / "news_quellen.yaml", ordner / "news_quellen_alt.yaml"
            datei.write_text("quellen:\n  - name: Alt\n    art: rss\n", encoding="utf-8")
            with mock.patch.object(quellen, "DATEI", datei), mock.patch.object(quellen, "ALT", alt), \
                    mock.patch.object(quellen, "_stand", {"mtime": None, "daten": None}):
                d = quellen.laden()
            self.assertEqual(d["version"], 2)
            self.assertIn("name: Alt", alt.read_text(encoding="utf-8"))
            self.assertTrue(any(q["art"] == "telegram" for q in d["quellen"]))
        finally:
            shutil.rmtree(ordner, ignore_errors=True)


class ExtraktionTest(unittest.TestCase):
    def setUp(self):
        # ohne Netz: nur Aliase statt S&P-500-Liste
        self.p = mock.patch("app.markt.indizes.mitglieder", side_effect=OSError("offline"))
        self.p.start()
        extraktion._stand.update(firmen=None, mtime=None)

    def tearDown(self):
        self.p.stop()
        extraktion._stand.update(firmen=None, mtime=None)

    def test_firmen_laender_ereignis(self):
        x = extraktion.extrahieren("US to impose 25% tariffs on chips from China; Nvidia falls")
        self.assertEqual(x["ereignis"], "zoelle_handel")
        self.assertIn("NVDA", x["ticker"])
        self.assertIn("China", x["laender"])

    def test_squawk_grossbuchstaben_und_cashtag(self):
        x = extraktion.extrahieren("*TRUMP: NVIDIA CAN SELL H200 TO CHINA")
        self.assertTrue(x["eil"])
        self.assertEqual((x["ticker"], x["ereignis"]), (["NVDA"], "zoelle_handel"))
        self.assertIn("Trump", x["personen"])
        self.assertEqual(extraktion.extrahieren("Big move in $MU today (NASDAQ: MU)")["ticker"], ["MU"])

    def test_kein_fehltreffer_bei_bindestrich(self):
        self.assertEqual(extraktion.extrahieren("Greene: Target-consistent wage growth is around 3%")["ticker"], [])

    def test_sec_items(self):
        x = extraktion.extrahieren("8-K - Micron Technology Inc (0000723125) (Filer)", "Item 2.02: Results of Operations Item 9.01", "SEC 8-K")
        self.assertEqual(x["ereignis"], "geschaeftszahlen")


class BewertungTest(unittest.TestCase):
    def _m(self, quelle, ereignis, minuten=0, stufe=2, **x):
        zeit = (datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc) + timedelta(minutes=minuten)).isoformat()
        return {"quelle": quelle, "stufe": stufe, "titel": f"{quelle} {ereignis}", "gesehen": zeit, "nur_fuer": None,
                "extrakt": {"ereignis": ereignis, "ticker": [], "firmen": [], "institutionen": x.get("inst", []),
                            "personen": x.get("pers", []), "laender": [], "eil": False}}

    def test_mehr_quellen_und_wachstum_heben_den_score(self):
        eins = geschichten.bewerten([self._m("FinancialJuice", "zoelle_handel")])
        drei = geschichten.bewerten([self._m("FinancialJuice", "zoelle_handel"), self._m("Telegram @X", "zoelle_handel", 5),
                                     self._m("Weißes Haus Erlasse", "zoelle_handel", 10, stufe=1)])
        self.assertFalse(eins["bestaetigt"])
        self.assertTrue(drei["bestaetigt"])
        self.assertGreater(drei["score"], eins["score"] + 20)

    def test_pr_des_weissen_hauses_zaehlt_wenig(self):
        pr = geschichten.bewerten([self._m("Weißes Haus", "sonstiges", stufe=1, pers=["Trump"])])
        zoll = geschichten.bewerten([self._m("FinancialJuice", "zoelle_handel", pers=["Trump"])])
        self.assertLess(pr["score"], 10)
        self.assertGreaterEqual(zoll["score"], 43)

    def test_filter(self):
        x = {"ticker": [], "ereignis": "sonstiges"}
        self.assertIsNotNone(geschichten.filtergrund({"titel": "3 Top Stocks to Buy Before 2027", "quelle": "Finnhub (Yahoo)", "link": ""}, x))
        self.assertIsNotNone(geschichten.filtergrund({"titel": "Local roofer explains insurance", "quelle": "PR Newswire", "link": ""}, x))
        self.assertIsNotNone(geschichten.filtergrund({"titel": "8-K - Some Credit Fund (0001) (Filer)", "quelle": "SEC 8-K", "link": ""}, x))
        self.assertIsNone(geschichten.filtergrund({"titel": "Apple to acquire X", "quelle": "PR Newswire", "link": ""}, {"ticker": ["AAPL"], "ereignis": "uebernahme"}))
        self.assertIsNotNone(geschichten.filtergrund({"titel": "Why X", "quelle": "Finnhub", "link": "https://www.fool.com/investing/x"}, x))

    def test_rang_halbiert_sich_nach_8_stunden(self):
        jetzt = datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc)
        self.assertAlmostEqual(geschichten.rang(60, (jetzt - timedelta(hours=8)).isoformat(), jetzt), 30, places=3)


if __name__ == "__main__":
    unittest.main()
