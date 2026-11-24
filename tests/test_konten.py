"""Tests für Anmeldung, Einladungen, Sperre und Keys im Passwort-Modus (wie auf dem NUC).

Läuft in einem eigenen, leeren Datenordner und ohne Start der Hintergrund-Abrufe (TestClient ohne "with").
Start: .venv\\Scripts\\python.exe -m unittest discover tests
"""
import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

MODULE = [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]


class Konten(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ordner = tempfile.mkdtemp(prefix="vinter_test_")
        cls.alt = {k: os.environ.get(k) for k in ("VINTER_ANMELDUNG", "VINTER_DATEN")}
        os.environ["VINTER_ANMELDUNG"] = "passwort"
        os.environ["VINTER_DATEN"] = cls.ordner
        # app neu laden, damit die Konfiguration (Modus, Datenordner) greift
        for m in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
            del sys.modules[m]
        from fastapi.testclient import TestClient
        cls.main = importlib.import_module("app.main")
        cls.konten = importlib.import_module("app.konten")
        cls.TestClient = TestClient

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.alt.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for m in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
            del sys.modules[m]
        shutil.rmtree(cls.ordner, ignore_errors=True)

    def client(self):
        return self.TestClient(self.main.app, base_url="http://vinter.test")

    def post(self, c, pfad, daten, methode="post"):
        if methode == "delete":  # DELETE ohne Inhalt
            return c.request("DELETE", pfad, headers={"x-vinter": "konto"})
        return getattr(c, methode)(pfad, json=daten, headers={"x-vinter": "konto"})

    def test_ablauf(self):
        k = self.konten
        c = self.client()
        # 1) Ohne Konto: Seite leitet zur Anmeldung, API gibt 401
        r = c.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/anmelden")
        self.assertEqual(c.get("/api/watchlist").status_code, 401)
        self.assertEqual(c.get("/anmelden").status_code, 200)
        self.assertFalse(c.get("/api/konto/status").json()["eingerichtet"])
        # App-Symbol und Manifest sind offen (das iPhone lädt sie ohne Cookie), die API nicht
        self.assertEqual(c.get("/manifest.webmanifest").status_code, 200)
        self.assertEqual(c.get("/symbole/apple-touch-icon.png").status_code, 200)
        self.assertEqual(c.get("/api/optionen?ticker=MU").status_code, 401)

        # 2) Einrichten nur mit Code
        r = self.post(c, "/api/konto/einrichten", {"code": "FALSCH", "name": "vince", "passwort": "sehr-geheim-123"})
        self.assertEqual(r.status_code, 400)
        code = k.einrichtungscode()
        r = self.post(c, "/api/konto/einrichten", {"code": code, "name": "vince", "passwort": "sehr-geheim-123"})
        self.assertEqual(r.status_code, 200, r.text)
        cookie = r.cookies.get("vinter_sitzung") or c.cookies.get("vinter_sitzung")
        self.assertTrue(cookie)
        set_cookie = r.headers.get("set-cookie", "").lower()
        self.assertIn("httponly", set_cookie)
        self.assertIn("samesite=strict", set_cookie)
        s = c.get("/api/konto/status").json()
        self.assertEqual((s["name"], s["rolle"]), ("vince", "admin"))
        self.assertEqual(k.konten()["vince"]["ordner"], "standard")  # übernimmt die bisherigen Daten
        self.assertNotIn("sehr-geheim-123", Path(self.ordner, "konten", "konten.json").read_text(encoding="utf-8"))
        # zweites Einrichten geht nicht mehr
        self.assertEqual(self.post(c, "/api/konto/einrichten", {"code": code, "name": "x2", "passwort": "sehr-geheim-123"}).status_code, 400)

        # 3) Ohne Header kein Schreiben
        self.assertEqual(c.post("/api/verwaltung/einladung", json={}).status_code, 403)

        # 4) Einladung erstellen und annehmen
        r = self.post(c, "/api/verwaltung/einladung", {"notiz": "Max"})
        self.assertEqual(r.status_code, 200, r.text)
        link = r.json()["link"]
        einl_code = link.split("einladung=")[1]
        self.assertTrue(link.startswith("http://vinter.test/anmelden?einladung="))
        freund = self.client()
        self.assertTrue(self.post(freund, "/api/konto/einladung/pruefen", {"code": einl_code}).json()["gueltig"])
        r = self.post(freund, "/api/konto/einladung/annehmen", {"code": einl_code, "name": "max", "passwort": "kurz"})
        self.assertEqual(r.status_code, 400)  # Passwort zu kurz
        r = self.post(freund, "/api/konto/einladung/annehmen", {"code": einl_code, "name": "max", "passwort": "max-passwort-42"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(freund.get("/api/konto/status").json()["rolle"], "freund")
        # Einladung nur einmal
        self.assertEqual(self.post(self.client(), "/api/konto/einladung/annehmen", {"code": einl_code, "name": "max2", "passwort": "max-passwort-42"}).status_code, 400)

        # 5) Freund darf nicht verwalten, hat eigenen Ordner
        self.assertEqual(freund.get("/api/verwaltung").status_code, 403)
        self.assertEqual(freund.get("/api/status").status_code, 403)  # Status-Seite nur für den Admin
        # Gemeinsame Marktdaten ändern nur der Admin
        self.assertEqual(freund.post("/api/handel/neu-einlesen/census", headers={"x-vinter": "handel"}).status_code, 403)
        self.assertEqual(freund.post("/api/fund/gruppe", json={"name": "x", "werte": ["AAPL"]}, headers={"x-vinter": "fundament"}).status_code, 403)
        self.assertEqual(freund.request("DELETE", "/api/fund/gruppe/abc", headers={"x-vinter": "fundament"}).status_code, 403)
        self.assertEqual(k.konten()["max"]["ordner"], "max")

        # 6) Keys: speichern, Wert kommt nie zurück, Datei ist verschlüsselt
        r = self.post(c, "/api/verwaltung/einstellung", {"name": "FRED_API_KEY", "wert": "abc-geheimer-wert-123"}, "put")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("abc-geheimer-wert-123", r.text)
        self.assertNotIn("abc-geheimer-wert-123", c.get("/api/verwaltung").text)
        roh = Path(self.ordner, "konten", "keys.enc").read_bytes()
        self.assertNotIn(b"abc-geheimer-wert-123", roh)
        # Nur pruefbar, wenn der Key nicht schon in Umgebung oder .env steht (die haben Vorrang vor dem Tool)
        from app.einstellungen import _env_dateien, key
        if not os.environ.get("FRED_API_KEY") and not _env_dateien().get("FRED_API_KEY"):
            self.assertEqual(key("FRED_API_KEY"), "abc-geheimer-wert-123")
        self.assertEqual(self.post(freund, "/api/verwaltung/einstellung", {"name": "FRED_API_KEY", "wert": "x"}, "put").status_code, 403)

        # 7) Sperren meldet sofort ab
        self.assertEqual(self.post(c, "/api/verwaltung/konto/max", {"aktiv": False}).status_code, 200)
        self.assertEqual(freund.get("/api/watchlist").status_code, 401)
        self.assertEqual(self.post(self.client(), "/api/konto/anmelden", {"name": "max", "passwort": "max-passwort-42"}).status_code, 400)
        self.post(c, "/api/verwaltung/konto/max", {"aktiv": True})

        # 8) Fehlversuche: nach 5 falschen Passwörtern Sperre, auch mit richtigem Passwort
        fremd = self.client()
        for _ in range(5):
            self.assertEqual(self.post(fremd, "/api/konto/anmelden", {"name": "max", "passwort": "falsch-falsch"}).status_code, 400)
        r = self.post(fremd, "/api/konto/anmelden", {"name": "max", "passwort": "max-passwort-42"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Fehlversuche", r.json()["fehler"])

        # 9) Verwaltung zeigt nichts über die Nutzung (keine letzte Anmeldung, keine Geräte)
        konto_max = next(x for x in c.get("/api/verwaltung").json()["konten"] if x["name"] == "max")
        self.assertNotIn("letzte_anmeldung", konto_max)
        self.assertNotIn("geraete", konto_max)

        # 10) Entfernen: Konto und Daten weg, Admin-Daten bleiben, Freund kann sich nicht mehr anmelden
        freund2 = self.client()
        k._fehler.clear()
        self.assertEqual(self.post(freund2, "/api/konto/anmelden", {"name": "max", "passwort": "max-passwort-42"}).status_code, 200)
        ordner_max = os.path.join(self.ordner, "benutzer", "max")
        os.makedirs(ordner_max, exist_ok=True)
        Path(ordner_max, "watchlist.yaml").write_text("- ticker: AAPL\n", encoding="utf-8")
        ordner_admin = os.path.join(self.ordner, "benutzer", "standard")
        os.makedirs(ordner_admin, exist_ok=True)
        Path(ordner_admin, "watchlist.yaml").write_text("- ticker: MU\n", encoding="utf-8")
        self.assertEqual(self.post(freund2, "/api/verwaltung/konto/vince", {}, "delete").status_code, 403)  # Freund darf nicht
        self.assertEqual(self.post(c, "/api/verwaltung/konto/vince", {}, "delete").status_code, 400)    # sich selbst nicht
        self.assertEqual(self.post(c, "/api/verwaltung/konto/max", {}, "delete").status_code, 200)
        self.assertFalse(os.path.exists(ordner_max))
        self.assertTrue(os.path.exists(os.path.join(ordner_admin, "watchlist.yaml")))
        self.assertNotIn("max", k.konten())
        self.assertEqual(freund2.get("/api/watchlist").status_code, 401)
        self.assertEqual(self.post(self.client(), "/api/konto/anmelden", {"name": "max", "passwort": "max-passwort-42"}).status_code, 400)

        # 11) Abmelden
        self.assertEqual(self.post(c, "/api/konto/abmelden", {}).status_code, 200)
        self.assertEqual(c.get("/api/watchlist").status_code, 401)

    def test_passwort_hash(self):
        k = self.konten
        h = k._passwort_hash("richtig-richtig")
        self.assertTrue(h.startswith("scrypt$"))
        self.assertTrue(k._passwort_pruefen("richtig-richtig", h))
        self.assertFalse(k._passwort_pruefen("falsch-falsch", h))
        self.assertNotEqual(h, k._passwort_hash("richtig-richtig"))  # eigenes Salz


if __name__ == "__main__":
    unittest.main()
