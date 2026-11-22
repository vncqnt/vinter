"""Prognosen von Manifold Markets und ein kleiner Verlauf der Wahrscheinlichkeiten.

Manifold ist ein Prognosemarkt mit Spielgeld: Leute wetten ohne echtes Geld auf Ereignisse.
Die Schnittstelle ist ohne Konto lesbar; die Bedingungen erlauben persönliche, nicht-kommerzielle
Nutzung, verbieten aber, eine große Datenbank daraus aufzubauen. Deshalb fragt Vinter nur ab, was
gerade angezeigt wird, und merkt sich nur je angezeigter Prognose einen Wert pro Tag (für "seit gestern").
Wie verlässlich eine Prognose ist, hängt stark davon ab, wie viele mitwetten: Das steht immer dabei.
"""
import json
import re
import sqlite3
import ssl
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import certifi
import yaml

from ..cache import markt_lesen as cache_lesen, markt_schreiben as cache_schreiben
from ..konfig import MARKT_DATEN

API = "https://api.manifold.markets/v0"
THEMEN_DATEI = MARKT_DATEN / "prognosen_themen.yaml"
VERLAUF = MARKT_DATEN / "prognosen_verlauf.sqlite"  # eine Zeile je Prognose und Tag, nicht nachladbar
GUELTIG = 15 * 60
_SSL = ssl.create_default_context(cafile=certifi.where())


class PrognosenFehler(Exception):
    pass


def _hole(pfad: str, **parameter):
    adresse = f"{API}/{pfad}" + (f"?{urllib.parse.urlencode(parameter)}" if parameter else "")
    anfrage = urllib.request.Request(adresse, headers={"User-Agent": "Vinter (persoenliches Dashboard)"})
    with urllib.request.urlopen(anfrage, timeout=15, context=_SSL) as antwort:
        return json.loads(antwort.read())


def themen() -> dict:
    roh = yaml.safe_load(THEMEN_DATEI.read_text(encoding="utf-8")) if THEMEN_DATEI.exists() else {}
    roh = roh or {}
    return {"themen": [t for t in roh.get("themen") or [] if t.get("name") and t.get("suche")], "min_wetter": int(roh.get("min_wetter") or 10)}


def _kurz(m: dict) -> dict:
    """Nur die Felder, die die Seite braucht."""
    return {
        "id": m["id"], "frage": m.get("question", ""), "link": m.get("url", ""),
        "art": "ja_nein" if m.get("outcomeType") == "BINARY" else "auswahl" if m.get("outcomeType") == "MULTIPLE_CHOICE" else "andere",
        "p": m.get("probability"), "wetter": m.get("uniqueBettorCount") or 0,
        "liquiditaet": round(m.get("totalLiquidity") or 0), "schluss": m.get("closeTime"),
        "geschlossen": bool(m.get("isResolved") or m.get("closed")),
    }


def suche(begriff: str) -> dict:
    """Offene Märkte zu einem Suchbegriff, die liquidesten zuerst, 15 Minuten im Cache."""
    begriff = begriff.strip()[:80]
    if len(begriff) < 2:
        return {"treffer": []}
    schluessel = f"manifold-suche3:{begriff.lower()}"
    alt, alter = cache_lesen(schluessel)
    if alt is not None and alter < GUELTIG:
        return alt
    try:
        roh = _hole("search-markets", term=begriff, filter="open", sort="liquidity", limit=40)
    except Exception:
        return alt or {"fehler": "Manifold ist gerade nicht erreichbar."}
    minimum = themen()["min_wetter"]
    # Manifolds Suche ist unscharf. Mindestens ein Suchwort muss in der Frage stehen.
    woerter = [w for w in re.findall(r"\w+", begriff.lower()) if len(w) >= 3]
    passt = lambda frage: not woerter or any(re.search(rf"\b{re.escape(w)}", frage.lower()) for w in woerter)
    neu = {"treffer": [_kurz(m) for m in roh if (m.get("uniqueBettorCount") or 0) >= minimum
                       and m.get("outcomeType") in ("BINARY", "MULTIPLE_CHOICE") and passt(m.get("question", ""))][:20]}
    cache_schreiben(schluessel, neu)
    return neu


def markt(markt_id: str) -> dict:
    """Ein einzelner Markt mit allen Antworten (bei Auswahl-Fragen), 15 Minuten im Cache."""
    if not markt_id.replace("-", "").replace("_", "").isalnum() or len(markt_id) > 40:
        raise PrognosenFehler("Ungültige Kennung.")
    schluessel = f"manifold-markt:{markt_id}"
    alt, alter = cache_lesen(schluessel)
    if alt is not None and alter < GUELTIG:
        return alt
    try:
        m = _hole(f"market/{urllib.parse.quote(markt_id)}")
    except Exception:
        if alt:
            return {**alt, "veraltet": True}
        raise PrognosenFehler("Diese Prognose ist gerade nicht erreichbar.") from None
    neu = _kurz(m)
    if neu["art"] == "auswahl":
        antworten = sorted(m.get("answers") or [], key=lambda a: a.get("probability") or 0, reverse=True)
        neu["antworten"] = [{"text": a.get("text", ""), "p": a.get("probability")} for a in antworten[:6]]
    cache_schreiben(schluessel, neu)
    return neu


def uebersicht(je_thema: int = 8) -> dict:
    """Alle Themen auf einmal, je Thema die liquidesten offenen Fragen. Jede Suche ist 15 Minuten im Cache."""
    liste = themen()["themen"]
    with ThreadPoolExecutor(max_workers=5) as pool:
        ergebnisse = list(pool.map(lambda t: suche(t["suche"]), liste))
    return {"themen": [{"name": t["name"], "suche": t["suche"], "treffer": (e.get("treffer") or [])[:je_thema], "fehler": e.get("fehler")}
                       for t, e in zip(liste, ergebnisse)]}


# ---- Verlauf: ein Wert pro Prognose und Tag ----
def _db():
    VERLAUF.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(VERLAUF)
    db.execute("CREATE TABLE IF NOT EXISTS verlauf (schluessel TEXT, tag TEXT, wert REAL, PRIMARY KEY (schluessel, tag))")
    return db


def merken(schluessel: str, wert: float | None) -> None:
    if wert is None:
        return
    db = _db()
    try:
        with db:
            db.execute("INSERT OR REPLACE INTO verlauf VALUES (?, ?, ?)", (schluessel, date.today().isoformat(), float(wert)))
    finally:
        db.close()


def merken_am(schluessel: str, tag: str, wert: float | None, ueberschreiben: bool = True) -> None:
    """Wert für einen bestimmten Tag; ueberschreiben=False lässt einen vorhandenen Tag stehen (Nachrechnen)."""
    if wert is None:
        return
    db = _db()
    try:
        with db:
            db.execute(f"INSERT OR {'REPLACE' if ueberschreiben else 'IGNORE'} INTO verlauf VALUES (?, ?, ?)", (schluessel, tag, float(wert)))
    finally:
        db.close()


def verlauf(schluessel: str, tage: int = 90) -> list[dict]:
    db = _db()
    try:
        zeilen = db.execute("SELECT tag, wert FROM verlauf WHERE schluessel = ? AND tag >= ? ORDER BY tag",
                            (schluessel, (date.today() - timedelta(days=tage)).isoformat())).fetchall()
    finally:
        db.close()
    return [{"tag": t, "wert": w} for t, w in zeilen]


def veraenderung(schluessel: str, jetzt: float | None) -> dict:
    """Veränderung gegenüber dem letzten gemerkten Wert vor 1 und vor 7 Tagen (in Prozentpunkten)."""
    if jetzt is None:
        return {}
    werte = {v["tag"]: v["wert"] for v in verlauf(schluessel, 30)}
    heute = date.today()
    ergebnis = {}
    for name, tage in (("tag", 1), ("woche", 7)):
        frueher = [w for t, w in werte.items() if t <= (heute - timedelta(days=tage)).isoformat()]
        if frueher:
            ergebnis[name] = round((jetzt - frueher[-1]) * 100, 1)
    return ergebnis
