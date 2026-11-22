"""Zentrale Konfiguration: wo die Daten liegen und wie Vinter läuft.

Alles kommt aus Umgebungsvariablen mit sinnvollen Vorgaben für den PC. Auf dem Server
(Home-Assistant-Add-on) zeigt VINTER_DATEN auf /data, sonst muss nichts geändert werden.

  VINTER_DATEN       Datenverzeichnis (Vorgabe: Ordner "daten" neben dem Programm)
  VINTER_BENUTZER    Benutzer, der lokal automatisch angemeldet wird (Vorgabe: "standard")
  VINTER_ANMELDUNG   "lokal" = automatisch angemeldet. Echte Anmeldung kommt später dazu.

Im Datenverzeichnis:
  markt/             für alle gleich (Themen, Krisen, News-Quellen, Ticker-Zuordnung …)
  benutzer/<name>/   persönliche Daten je Benutzer (Export, Thesen, Watchlist …)
  cache/             Zwischenspeicher, darf jederzeit gelöscht werden
"""
import os
import shutil
from pathlib import Path

PROGRAMM = Path(__file__).resolve().parent.parent   # der Programmordner (im Add-on schreibgeschützt)
VORLAGEN = PROGRAMM / "vorlagen"                     # mitgelieferte Startdateien, gehören ins Repo

DATEN = Path(os.environ.get("VINTER_DATEN") or PROGRAMM / "daten").resolve()
MARKT_DATEN = DATEN / "markt"
BENUTZER_DATEN = DATEN / "benutzer"
CACHE = DATEN / "cache"
CACHE_MARKT = CACHE / "markt.sqlite"
# Gespeicherte Volatilitätsoberflächen: eine Historie, die sich nicht nachladen lässt.
# Deshalb bei den Daten und nicht im Cache, der gefahrlos gelöscht werden darf.
VOLA_HISTORIE = MARKT_DATEN / "volaflaeche.sqlite"

STANDARD_BENUTZER = os.environ.get("VINTER_BENUTZER", "standard")
ANMELDUNG = os.environ.get("VINTER_ANMELDUNG", "lokal")


def vorlagen_kopieren() -> None:
    """Legt fehlende Startdateien aus vorlagen/ im Datenverzeichnis an. Vorhandene bleiben unberührt,
    denn die bearbeitest du selbst. So bringt ein Update neue Dateien mit, ohne deine zu überschreiben."""
    if not VORLAGEN.exists():
        return
    for quelle in VORLAGEN.rglob("*"):
        if quelle.is_dir():
            continue
        ziel = DATEN / quelle.relative_to(VORLAGEN)
        if not ziel.exists():
            ziel.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(quelle, ziel)
