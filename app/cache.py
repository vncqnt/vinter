"""Kleine Datenbanken als Zwischenspeicher: einer für Marktdaten (für alle gemeinsam, jede Aktie
wird nur einmal abgefragt) und einer je Benutzer für seine persönlichen Rechenergebnisse."""
import json
import sqlite3
import time
from functools import partial
from pathlib import Path

from .benutzer import Benutzer
from .konfig import CACHE_MARKT

STUNDE = 3600
TAG = 24 * STUNDE


def _db(datei: Path):
    datei.parent.mkdir(parents=True, exist_ok=True)
    verbindung = sqlite3.connect(datei)
    verbindung.execute(
        "CREATE TABLE IF NOT EXISTS cache (schluessel TEXT PRIMARY KEY, wert TEXT, zeit REAL)"
    )
    return verbindung


def _lesen(datei: Path, schluessel: str):
    """Gibt (Wert, Alter in Sekunden) zurück, oder (None, None) wenn nichts da ist."""
    db = _db(datei)
    try:
        z = db.execute("SELECT wert, zeit FROM cache WHERE schluessel = ?", (schluessel,)).fetchone()
    finally:
        db.close()
    return (json.loads(z[0]), time.time() - z[1]) if z else (None, None)


def _schreiben(datei: Path, schluessel: str, wert) -> None:
    db = _db(datei)
    try:
        with db:
            db.execute(
                "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                (schluessel, json.dumps(wert), time.time()),
            )
    finally:
        db.close()


markt_lesen = partial(_lesen, CACHE_MARKT)
markt_schreiben = partial(_schreiben, CACHE_MARKT)


def markt_veraltet(*schluessel: str) -> None:
    """Einträge als alt markieren (Wert bleibt als Rückfall erhalten), damit der nächste Aufruf neu lädt."""
    db = _db(CACHE_MARKT)
    try:
        with db:
            db.executemany("UPDATE cache SET zeit = 0 WHERE schluessel = ?", [(s,) for s in schluessel])
    finally:
        db.close()


def nutzer_lesen(b: Benutzer, schluessel: str):
    return _lesen(b.cache, schluessel)


def nutzer_schreiben(b: Benutzer, schluessel: str, wert) -> None:
    _schreiben(b.cache, schluessel, wert)
