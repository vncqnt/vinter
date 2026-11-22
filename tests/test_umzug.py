"""Umzug PC -> NUC (app/umzug.py): Export ohne Geheimnisse, Import führt Datenbanken zusammen und sichert Ersetztes."""
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.umzug import UmzugFehler, einspielen, export_bauen


def db(pfad: Path, zeilen: list[tuple], ohne_schluessel: list[tuple] = ()):
    pfad.parent.mkdir(parents=True, exist_ok=True)
    v = sqlite3.connect(pfad)
    v.execute("CREATE TABLE IF NOT EXISTS verlauf (schluessel TEXT, tag TEXT, wert REAL, PRIMARY KEY (schluessel, tag))")
    v.execute("CREATE TABLE IF NOT EXISTS log (text TEXT)")
    v.executemany("INSERT OR REPLACE INTO verlauf VALUES (?, ?, ?)", zeilen)
    v.executemany("INSERT INTO log VALUES (?)", ohne_schluessel)
    v.commit()
    v.close()


def zeilen(pfad: Path, sql: str) -> list:
    v = sqlite3.connect(pfad)
    try:
        return v.execute(sql).fetchall()
    finally:
        v.close()


class Umzug(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = Path(self.tmp.name)
        self.pc_markt, self.pc_ich = r / "pc/markt", r / "pc/benutzer/standard"
        self.nuc_markt, self.nuc_ich, self.arbeit = r / "nuc/markt", r / "nuc/benutzer/standard", r / "nuc/umzug"
        db(self.pc_markt / "historie.sqlite", [("a", "2026-11-01", 1.0), ("a", "2026-11-02", 2.0)], [("pc",)])
        (self.pc_markt / "themen.yaml").write_text("eigene: themen\n", encoding="utf-8")
        (self.pc_markt / ".env").write_text("GEHEIM=1234567890\n", encoding="utf-8")
        (self.pc_ich / "thesen").mkdir(parents=True)
        (self.pc_ich / "thesen/uber.yaml").write_text("name: Uber\n", encoding="utf-8")
        (self.pc_ich / "tafel.yaml").write_text("pc: ja\n", encoding="utf-8")
        self.zip = r / "umzug.zip"
        self.info = export_bauen(self.zip, self.pc_markt, self.pc_ich, "0.9")

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_ohne_geheimnisse(self):
        with zipfile.ZipFile(self.zip) as z:
            namen = set(z.namelist())
        self.assertIn("umzug.json", namen)
        self.assertIn("markt/historie.sqlite", namen)
        self.assertIn("benutzer/thesen/uber.yaml", namen)
        self.assertFalse(any(".env" in n for n in namen))

    def test_auf_leeren_nuc(self):
        b = einspielen(self.zip, self.nuc_markt, self.nuc_ich, self.arbeit)
        self.assertEqual(zeilen(self.nuc_markt / "historie.sqlite", "SELECT COUNT(*) FROM verlauf")[0][0], 2)
        self.assertEqual((self.nuc_ich / "thesen/uber.yaml").read_text(encoding="utf-8"), "name: Uber\n")
        self.assertIsNone(b["sicherung"])
        self.assertFalse(any(p.name.startswith("eingang_") for p in self.arbeit.iterdir()))

    def test_zusammenfuehren_und_sichern(self):
        # Am NUC gibt es schon einen eigenen Tag, einen abweichenden Wert und ein Layout.
        db(self.nuc_markt / "historie.sqlite", [("a", "2026-11-02", 9.0), ("a", "2026-11-20", 5.0)], [("nuc",)])
        self.nuc_ich.mkdir(parents=True)
        (self.nuc_ich / "tafel.yaml").write_text("nuc: ja\n", encoding="utf-8")
        b = einspielen(self.zip, self.nuc_markt, self.nuc_ich, self.arbeit)
        werte = dict(((s, t), w) for s, t, w in zeilen(self.nuc_markt / "historie.sqlite", "SELECT * FROM verlauf"))
        self.assertEqual(werte[("a", "2026-11-01")], 1.0)   # vom PC dazu
        self.assertEqual(werte[("a", "2026-11-02")], 9.0)   # NUC-Wert bleibt
        self.assertEqual(werte[("a", "2026-11-20")], 5.0)   # NUC-Tag bleibt
        self.assertEqual(zeilen(self.nuc_markt / "historie.sqlite", "SELECT text FROM log"), [("nuc",)])  # ohne Schlüssel: nicht doppeln
        self.assertEqual(b["tabellen_uebersprungen"], 1)
        self.assertEqual((self.nuc_ich / "tafel.yaml").read_text(encoding="utf-8"), "pc: ja\n")
        gesichert = self.arbeit / b["sicherung"] / "benutzer/tafel.yaml"
        self.assertEqual(gesichert.read_text(encoding="utf-8"), "nuc: ja\n")
        self.assertTrue((self.arbeit / b["sicherung"] / "markt/historie.sqlite").exists())

    def test_zweimal_einspielen_aendert_nichts(self):
        einspielen(self.zip, self.nuc_markt, self.nuc_ich, self.arbeit)
        b = einspielen(self.zip, self.nuc_markt, self.nuc_ich, self.arbeit)
        self.assertEqual(b["zeilen_neu"], 0)
        self.assertEqual(b["ersetzt"], 0)
        self.assertEqual(zeilen(self.nuc_markt / "historie.sqlite", "SELECT COUNT(*) FROM log")[0][0], 1)

    def test_boese_dateien(self):
        r = Path(self.tmp.name)
        for name, eintrag in (("raus.zip", "markt/../../raus.txt"), ("fremd.zip", "konten/konten.json")):
            with zipfile.ZipFile(r / name, "w") as z:
                z.writestr("umzug.json", json.dumps({"art": "vinter-umzug"}))
                z.writestr(eintrag, "x")
            with self.assertRaises(UmzugFehler):
                einspielen(r / name, self.nuc_markt, self.nuc_ich, self.arbeit)
        self.assertFalse((r / "raus.txt").exists())
        with zipfile.ZipFile(r / "ohne.zip", "w") as z:
            z.writestr("markt/a.yaml", "x")
        with self.assertRaises(UmzugFehler):
            einspielen(r / "ohne.zip", self.nuc_markt, self.nuc_ich, self.arbeit)
        with zipfile.ZipFile(r / "env.zip", "w") as z:
            z.writestr("umzug.json", json.dumps({"art": "vinter-umzug"}))
            z.writestr("benutzer/.env", "GEHEIM=1")
        einspielen(r / "env.zip", self.nuc_markt, self.nuc_ich, self.arbeit)
        self.assertFalse((self.nuc_ich / ".env").exists())


if __name__ == "__main__":
    unittest.main()
