"""Prüft die Add-on-Dateien für Home Assistant (config.yaml, Dockerfile, run.sh, .dockerignore).
Docker selbst läuft hier nicht, deshalb nur Inhalt und Zusammenspiel der Dateien."""
import re
import unittest
from fnmatch import fnmatch
from pathlib import Path

import yaml

PROJEKT = Path(__file__).resolve().parent.parent


class AddonDateien(unittest.TestCase):
    def setUp(self):
        self.config = yaml.safe_load((PROJEKT / "config.yaml").read_text(encoding="utf-8"))
        self.docker = (PROJEKT / "Dockerfile").read_text(encoding="utf-8")
        self.run = (PROJEKT / "run.sh").read_bytes().decode("utf-8")

    def test_pflichtfelder(self):
        for feld in ("name", "version", "slug", "description", "arch"):
            self.assertIn(feld, self.config)
        self.assertIsInstance(self.config["version"], str)
        self.assertIn("amd64", self.config["arch"])
        self.assertTrue((PROJEKT / "repository.yaml").exists())

    def test_nur_host_ohne_seitenleiste(self):
        self.assertFalse(self.config.get("ingress", False))
        self.assertNotIn("panel_icon", self.config)
        self.assertEqual(self.config["ports"], {"8099/tcp": 8099})

    def test_port_und_daten_passen_zusammen(self):
        self.assertIn("--port 8099", self.run)
        self.assertIn("--host 0.0.0.0", self.run)
        self.assertIn("VINTER_DATEN=/data", self.docker)
        self.assertIn("VINTER_ANMELDUNG=passwort", self.docker)

    def test_version_wie_status_seite(self):
        from app.status import VERSION
        self.assertTrue(self.config["version"].startswith(VERSION.split()[0]), "config.yaml und app/status.py VERSION angleichen")

    def test_run_sh_mit_linux_zeilenenden(self):
        self.assertNotIn("\r", self.run)
        self.assertTrue(self.run.startswith("#!/bin/sh"))

    def test_keine_privaten_dateien_im_bild(self):
        regeln = [z.strip() for z in (PROJEKT / ".dockerignore").read_text(encoding="utf-8").splitlines() if z.strip() and not z.startswith("#")]
        self.assertEqual(regeln[0], "*")
        erlaubt = {r[1:] for r in regeln if r.startswith("!")}
        self.assertEqual(erlaubt, {"app", "vorlagen", "requirements.txt", "run.sh"})
        for kopiert in re.findall(r"^COPY (\S+)", self.docker, re.M):
            self.assertIn(kopiert, erlaubt)

    def test_alle_importe_in_requirements(self):
        paket = {"yaml": "pyyaml", "dotenv": "python-dotenv", "sentence_transformers": "sentence-transformers"}
        installiert = {re.split(r"[=<>]", z)[0].strip().lower() for z in (PROJEKT / "requirements.txt").read_text(encoding="utf-8").splitlines()
                       if z.strip() and not z.startswith("#")}
        fremd = {"certifi", "cryptography", "exchange_calendars", "fastapi", "feedparser", "numpy", "pandas", "requests", "scipy",
                 "websockets", "yfinance", "yaml", "dotenv", "sentence_transformers"}
        gefunden = set()
        for datei in (PROJEKT / "app").rglob("*.py"):
            for m in re.finditer(r"^\s*(?:import|from) ([a-zA-Z_]+)", datei.read_text(encoding="utf-8"), re.M):
                if m.group(1) in fremd:
                    gefunden.add(m.group(1))
        for modul in gefunden:
            self.assertIn(paket.get(modul, modul).lower(), installiert, modul)


if __name__ == "__main__":
    unittest.main()
