"""Datenbank des Newsfeeds: daten/markt/nachrichten.sqlite (bei den Daten, nicht im Cache: die Messung,
welche Quelle eine Geschichte zuerst hatte, braucht den Verlauf).

meldungen  jede Meldung genau einmal (Schlüssel aus Quelle und Kennung), mit dem Zeitpunkt, zu dem Vinter
           sie zuerst gesehen hat ("gesehen") und dem Zeitpunkt laut Quelle ("zeit").
           nur_fuer: Meldungen aus Telegram oder dem manuellen Eingang gehören einem Benutzer und
           werden nur ihm gezeigt (NULL = für alle).
quellen    Zustand je Quelle: ETag/Last-Modified für bedingte Anfragen, Fehlerserie für den Backoff,
           letzter Erfolg, letzte neue Meldung.
"""
import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone

from ...konfig import MARKT_DATEN

DB = MARKT_DATEN / "nachrichten.sqlite"
_sperre = threading.Lock()
_bereit = [False]


def jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def verbinden() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    if not _bereit[0]:
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meldungen (
                id INTEGER PRIMARY KEY, schluessel TEXT UNIQUE, quelle TEXT, art TEXT, stufe INTEGER,
                titel TEXT, text TEXT, link TEXT, zeit TEXT, gesehen TEXT, nur_fuer TEXT,
                extrakt TEXT, story INTEGER, filter TEXT, score REAL);
            CREATE INDEX IF NOT EXISTS meldungen_gesehen ON meldungen(gesehen);
            CREATE INDEX IF NOT EXISTS meldungen_story ON meldungen(story);
            CREATE TABLE IF NOT EXISTS quellen (
                name TEXT PRIMARY KEY, etag TEXT, geaendert TEXT, letzter_versuch TEXT, letzter_ok TEXT,
                fehler_serie INTEGER DEFAULT 0, letzter_fehler TEXT, fehler_text TEXT,
                letzte_neue TEXT, neue_heute INTEGER DEFAULT 0, tag TEXT);
        """)
        _bereit[0] = True
    return db


def schluessel(quelle: str, kennung: str) -> str:
    return hashlib.sha1(f"{quelle}\n{kennung}".encode()).hexdigest()


_LEER = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")


def sauber(text: str | None, laenge: int = 0) -> str:
    """HTML entfernen, Leerraum zusammenfassen, auf Wunsch kürzen."""
    import html
    t = _LEER.sub(" ", html.unescape(_TAG.sub(" ", text or ""))).strip()
    return t[:laenge] if laenge else t


def einfuegen(meldungen: list[dict]) -> list[int]:
    """Neue Meldungen speichern; schon bekannte werden übergangen. Gibt die neuen ids zurück."""
    if not meldungen:
        return []
    neu = []
    with _sperre:
        db = verbinden()
        try:
            with db:
                for m in meldungen:
                    cur = db.execute(
                        "INSERT OR IGNORE INTO meldungen (schluessel, quelle, art, stufe, titel, text, link, zeit, gesehen, nur_fuer) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (m["schluessel"], m["quelle"], m.get("art", "rss"), m.get("stufe", 3), m["titel"], m.get("text") or "",
                         m.get("link") or "", m.get("zeit"), m.get("gesehen") or jetzt(), m.get("nur_fuer")))
                    if cur.rowcount:
                        neu.append(cur.lastrowid)
        finally:
            db.close()
    return neu


def quelle_zustand(name: str) -> dict:
    db = verbinden()
    try:
        r = db.execute("SELECT * FROM quellen WHERE name=?", (name,)).fetchone()
        return dict(r) if r else {"name": name, "fehler_serie": 0}
    finally:
        db.close()


def quelle_melden(name: str, ok: bool, neue: int = 0, etag: str | None = None, geaendert: str | None = None,
                  fehler: str | None = None) -> None:
    """Zustand nach einem Abruf festhalten."""
    t = jetzt()
    heute = t[:10]
    with _sperre:
        db = verbinden()
        try:
            with db:
                alt = db.execute("SELECT * FROM quellen WHERE name=?", (name,)).fetchone()
                alt = dict(alt) if alt else {}
                neue_heute = (alt.get("neue_heute") or 0) if alt.get("tag") == heute else 0
                db.execute(
                    "INSERT OR REPLACE INTO quellen (name, etag, geaendert, letzter_versuch, letzter_ok, fehler_serie, letzter_fehler, "
                    "fehler_text, letzte_neue, neue_heute, tag) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (name,
                     etag if ok and etag is not None else alt.get("etag"),
                     geaendert if ok and geaendert is not None else alt.get("geaendert"),
                     t,
                     t if ok else alt.get("letzter_ok"),
                     0 if ok else (alt.get("fehler_serie") or 0) + 1,
                     alt.get("letzter_fehler") if ok else t,
                     alt.get("fehler_text") if ok else fehler,
                     t if neue else alt.get("letzte_neue"),
                     neue_heute + neue,
                     heute))
        finally:
            db.close()


def alle_quellen() -> list[dict]:
    db = verbinden()
    try:
        return [dict(r) for r in db.execute("SELECT * FROM quellen ORDER BY name")]
    finally:
        db.close()


def neueste(benutzer: str | None, seit: str | None = None, grenze: int = 300) -> list[dict]:
    """Meldungen, neueste zuerst. Private Meldungen (Telegram, manuell) nur für ihren Benutzer."""
    db = verbinden()
    try:
        sql = "SELECT * FROM meldungen WHERE (nur_fuer IS NULL OR nur_fuer = ?)"
        werte: list = [benutzer or ""]
        if seit:
            sql += " AND gesehen >= ?"
            werte.append(seit)
        sql += " ORDER BY gesehen DESC, id DESC LIMIT ?"
        werte.append(grenze)
        aus = []
        for r in db.execute(sql, werte):
            m = dict(r)
            m["extrakt"] = json.loads(m["extrakt"]) if m.get("extrakt") else None
            aus.append(m)
        return aus
    finally:
        db.close()
