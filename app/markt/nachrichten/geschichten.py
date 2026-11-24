"""Geschichten bilden und bewerten.

Jede neue Meldung läuft durch:
1. Extraktion (extraktion.py): Firmen, Länder, Institutionen, Personen, Ereignistyp.
2. Filter: Werbung und Rauschen werden nicht gelöscht, sondern mit Grund markiert (Bereich "Ausgefiltert").
   - Titel wie "Top 5 Aktien", "Sollte man kaufen?", "Prediction:" (Liste filter_titel in news_begriffe.yaml)
   - Domains auf der Sperrliste (filter_domains)
   - Pressemitteilungen (Newswires) ohne börsennotierte Firma und ohne Marktereignis
   - SEC-Meldungen von Firmen ohne Aktie (Fonds, private Kreditfonds …)
3. Geschichte: Das Sprachmodell (dasselbe wie bei den Thesen-News) macht aus jeder Schlagzeile einen Vektor.
   Ähnliche Meldungen der letzten 12 Stunden kommen in dieselbe Geschichte.
4. Score 0–100 aus Ereignistyp, Institutionen und Personen, bekannten Firmen, Quellen-Stufe, Zahl unabhängiger
   Quellen, Wachstum der Geschichte und Eilmeldungs-Stil. Für die Reihenfolge zählt zusätzlich das Alter.
   Eine Geschichte gilt als bestätigt, sobald zwei unabhängige Quellen oder eine Originalquelle sie bringen.

Läuft in einem eigenen Thread mit Warteschlange (das Sprachmodell soll nicht doppelt gleichzeitig rechnen).
"""
import json
import logging
import math
import queue
import re
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import numpy as np
import yaml

from . import extraktion, speicher

log = logging.getLogger(__name__)

AEHNLICH = 0.72            # Kosinus-Ähnlichkeit, ab der eine Meldung zur Geschichte gehört
AEHNLICH_MIT_BEZUG = 0.58  # niedriger, wenn Firma, Land, Institution oder Person übereinstimmen
AEHNLICH_GLEICHES_EREIGNIS = 0.50  # noch niedriger bei gleichem Markt-Ereignis (z. B. mehrere Zoll-Meldungen)
# Gemessen am 24.11.2026: Meldungen zur selben Zoll-Ankündigung lagen bei 0,52–0,70, ein anderes Handelsthema
# (China setzt Exportkontrollen aus) bei 0,10–0,47, Notenbank gegen Zölle bei 0,0–0,3.
FENSTER = timedelta(hours=12)
NEWSWIRES = {"GlobeNewswire", "PR Newswire", "Business Wire"}
BEHOERDEN = {"Weißes Haus Erlasse", "Fed", "EZB", "BLS"}  # der Nachrichten-Blog des Weißen Hauses ist meist PR
GEWICHT = {"zoelle_handel": 35, "notenbank": 35, "konjunktur": 30, "geopolitik": 30, "uebernahme": 25, "insolvenz": 25,
           "prognose": 20, "geschaeftszahlen": 20, "bilanzproblem": 20, "regulierung": 15, "schulden": 15,
           "rechtsstreit": 10, "personalien": 10, "rating": 10, "abschreibung": 10, "umbau": 10, "vertrag": 8,
           "kapitalmassnahme": 8, "finanzierung": 5, "mitteilung": 5, "boersennotierung": 5, "pflichtmeldung": 3, "sonstiges": 5}
WICHTIGE_STELLEN = {"Fed", "EZB", "Weißes Haus", "USTR", "US-Finanzministerium", "US-Handelsministerium", "OPEC", "PBOC", "BoJ"}
WICHTIGE_PERSONEN = {"Trump", "Powell", "Bessent", "Lutnick", "Greer", "Lagarde", "Xi Jinping", "Putin"}
MARKTEREIGNISSE = {"zoelle_handel", "notenbank", "konjunktur", "geopolitik", "uebernahme", "prognose", "geschaeftszahlen", "insolvenz"}

FILTER_TITEL_STANDARD = [
    r"^\d+ (top |best )?(stocks?|shares|aktien)\b", r"\btop \d+ (stocks?|aktien)\b", r"\bbest (stocks?|etfs?) to (buy|own)\b",
    r"\bshould you (buy|sell)\b", r"\bis it (time|too late) to buy\b", r"\bbuy (now|before)\b", r"^prediction:",
    r"\bmillionaire\b", r"\b(could|will) (soar|skyrocket|double|triple)\b", r"\bno-brainer\b", r"\bunstoppable\b",
    r"\bsollte(n)? man (jetzt )?kaufen\b", r"\bjetzt kaufen\b", r"\bpassive income\b", r"\bdividend stocks? to\b",
    r"\bsponsored\b", r"\bwebinar\b", r"\bto (present|participate|ring)\b.*\b(conference|bell)\b", r"\bjoin us\b",
]
FILTER_DOMAINS_STANDARD = ["fool.com", "investorplace.com", "247wallst.com", "insidermonkey.com", "zacks.com/stock/news",
                           "benzinga.com/pressreleases", "stocknews.com", "tipranks.com/news/promoted"]

_schlange: "queue.Queue[list[int]]" = queue.Queue()
_gestartet = [False]
_regeln = {"mtime": None, "titel": None, "domains": None}


def _filter_regeln():
    datei = extraktion.DATEI
    extraktion._begriffe()  # legt die Datei bei Bedarf an
    mtime = datei.stat().st_mtime
    if _regeln["mtime"] != mtime:
        roh = yaml.safe_load(datei.read_text(encoding="utf-8")) or {}
        titel = FILTER_TITEL_STANDARD + [str(x) for x in roh.get("filter_titel") or []]
        _regeln.update(mtime=mtime, titel=re.compile("|".join(f"(?:{t})" for t in titel), re.I),
                       domains=FILTER_DOMAINS_STANDARD + [str(x) for x in roh.get("filter_domains") or []])
    return _regeln["titel"], _regeln["domains"]


def filtergrund(m: dict, x: dict) -> str | None:
    """Warum eine Meldung aussortiert wird (None = bleibt). Reine Funktion."""
    titel_muster, domains = _filter_regeln()
    if titel_muster.search(m["titel"]):
        return "Werbung oder Klickköder (Titel)"
    adresse = (m.get("link") or "").lower()
    host = urlparse(adresse).netloc
    if any(d in host + urlparse(adresse).path for d in domains):
        return "Werbung (Seite auf der Sperrliste)"
    if m["quelle"] in NEWSWIRES and not x["ticker"] and x["ereignis"] not in MARKTEREIGNISSE:
        return "Pressemitteilung ohne börsennotierte Firma"
    if m["quelle"].startswith("SEC") and not x["ticker"]:
        return "SEC-Meldung ohne börsennotierte Aktie"
    return None


# ---- Vektoren ----

def _vektoren(titel: list[str]) -> np.ndarray:
    from ..news import lade_modell
    v = lade_modell().encode(titel, batch_size=32, show_progress_bar=False, normalize_embeddings=True)
    return np.asarray(v, dtype=np.float32)


def _als_bytes(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def _aus_bytes(b: bytes | None) -> np.ndarray | None:
    return np.frombuffer(b, dtype=np.float32) if b else None


def _schema(db) -> None:
    spalten = {r[1] for r in db.execute("PRAGMA table_info(meldungen)")}
    if "vektor" not in spalten:
        db.execute("ALTER TABLE meldungen ADD COLUMN vektor BLOB")
    db.execute("""CREATE TABLE IF NOT EXISTS stories (
        id INTEGER PRIMARY KEY, titel TEXT, erstes TEXT, letztes TEXT, anzahl INTEGER, quellen TEXT, ereignis TEXT,
        ticker TEXT, laender TEXT, stellen TEXT, score REAL, bestaetigt INTEGER, eil INTEGER, nur_fuer TEXT,
        vektor BLOB, aktualisiert TEXT)""")
    db.execute("CREATE INDEX IF NOT EXISTS stories_letztes ON stories(letztes)")


# ---- Bewertung ----

def bewerten(mitglieder: list[dict]) -> dict:
    """Kennzahlen einer Geschichte aus ihren Meldungen (reine Funktion). mitglieder: Zeilen mit extrakt (dict)."""
    zeiten = sorted(m["gesehen"] for m in mitglieder)
    quellen = list(dict.fromkeys(m["quelle"] for m in sorted(mitglieder, key=lambda m: m["gesehen"])))
    ex = [m["extrakt"] or {} for m in mitglieder]
    ereignisse = [e.get("ereignis", "sonstiges") for e in ex]
    ereignis = max(ereignisse, key=lambda e: (GEWICHT.get(e, 0), ereignisse.count(e)))
    ticker = list(dict.fromkeys(t for e in ex for t in e.get("ticker", [])))
    stellen = list(dict.fromkeys(s for e in ex for s in e.get("institutionen", []) + e.get("personen", [])))
    laender = list(dict.fromkeys(l for e in ex for l in e.get("laender", [])))
    stufe1 = any(m.get("stufe") == 1 for m in mitglieder)
    unabhaengig = len({q.split(" @")[0] if q.startswith("Telegram") else q for q in quellen})
    score = GEWICHT.get(ereignis, 5)
    markt = ereignis in MARKTEREIGNISSE or ereignis == "regulierung"
    # Wichtige Stellen und Personen zählen nur, wenn es um ein Markt-Ereignis geht (nicht bei PR wie Truthahn-Begnadigung)
    score += 10 if markt and any(s in WICHTIGE_STELLEN for s in stellen) else 0
    score += 8 if markt and any(s in WICHTIGE_PERSONEN for s in stellen) else 0
    score += 8 if any(f for e in ex for f in e.get("firmen", [])) else 0
    score += 10 if ereignis != "sonstiges" and any(m["quelle"] in BEHOERDEN for m in mitglieder) else 0
    if quellen == ["Weißes Haus"] and ereignis not in ("zoelle_handel", "regulierung", "notenbank"):
        score -= 10  # nur der Nachrichten-Blog des Weißen Hauses, kein Politik-Ereignis
    score += min(21, 7 * (unabhaengig - 1))
    letzte = datetime.fromisoformat(zeiten[-1])
    in_30 = sum(1 for z in zeiten if datetime.fromisoformat(z) >= letzte - timedelta(minutes=30))
    score += 10 if in_30 >= 3 else 0
    score += 5 if any(e.get("eil") for e in ex) else 0
    titel_von = next((m for m in sorted(mitglieder, key=lambda m: m["gesehen"]) if m.get("stufe") == 1 and not m["quelle"].startswith("SEC")), None) \
        or sorted(mitglieder, key=lambda m: m["gesehen"])[0]
    privat = {m.get("nur_fuer") for m in mitglieder}
    return {
        "titel": titel_von["titel"], "erstes": zeiten[0], "letztes": zeiten[-1], "anzahl": len(mitglieder),
        "quellen": quellen, "ereignis": ereignis, "ticker": ticker, "laender": laender, "stellen": stellen,
        "score": float(min(100, score)), "bestaetigt": stufe1 or unabhaengig >= 2,
        "eil": any(e.get("eil") for e in ex), "nur_fuer": next(iter(privat)) if len(privat) == 1 else None,
    }


def rang(score: float, letztes: str, jetzt: datetime | None = None) -> float:
    """Für die Reihenfolge: Score mit Halbwertszeit 8 Stunden."""
    jetzt = jetzt or datetime.now(timezone.utc)
    alter_h = max(0.0, (jetzt - datetime.fromisoformat(letztes)).total_seconds() / 3600)
    return score * math.pow(0.5, alter_h / 8)


# ---- Verarbeitung ----

def verarbeiten(ids: list[int]) -> None:
    db = speicher.verbinden()
    try:
        with db:
            _schema(db)
        zeilen = [dict(r) for r in db.execute(f"SELECT * FROM meldungen WHERE id IN ({','.join('?' * len(ids))})", ids)]
        zeilen = [z for z in zeilen if z.get("story") is None and z.get("filter") is None]
        if not zeilen:
            return
        frueher = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        im_stapel = set()
        for z in zeilen:
            z["extrakt"] = extraktion.extrahieren(z["titel"], z["text"], z["quelle"])
            z["filter"] = filtergrund(z, z["extrakt"])
            if not z["filter"] and db.execute("SELECT 1 FROM meldungen WHERE quelle=? AND titel=? AND id<? AND gesehen>=? AND story IS NOT NULL",
                                              (z["quelle"], z["titel"], z["id"], frueher)).fetchone():
                z["filter"] = "Doppelt (dieselbe Quelle hat das schon gemeldet)"
            if not z["filter"] and (z["quelle"], z["titel"]) in im_stapel:
                z["filter"] = "Doppelt (dieselbe Quelle hat das schon gemeldet)"
            im_stapel.add((z["quelle"], z["titel"]))
        behalten = [z for z in zeilen if not z["filter"]]
        vektoren = _vektoren([z["titel"] for z in behalten]) if behalten else []
        grenze = (datetime.now(timezone.utc) - FENSTER).isoformat()
        offen = [dict(r) for r in db.execute("SELECT id, vektor, ticker, stellen, laender, ereignis, nur_fuer FROM stories WHERE letztes >= ?", (grenze,))]
        for s in offen:
            s["v"] = _aus_bytes(s["vektor"])
            s["bezug"] = set(json.loads(s["ticker"] or "[]")) | set(json.loads(s["stellen"] or "[]")) | set(json.loads(s["laender"] or "[]"))
        with db:
            for z in zeilen:
                db.execute("UPDATE meldungen SET extrakt=?, filter=? WHERE id=?", (json.dumps(z["extrakt"], ensure_ascii=False), z["filter"], z["id"]))
            for z, v in zip(behalten, vektoren):
                x = z["extrakt"]
                bezug = set(x["ticker"]) | set(x["institutionen"]) | set(x["personen"]) | set(x["laender"])
                beste, beste_wert = None, 0.0
                for s in offen:
                    if s["v"] is None or (s["nur_fuer"] and z.get("nur_fuer") not in (None, s["nur_fuer"])):
                        continue
                    wert = float(np.dot(v, s["v"]))
                    gleich = x["ereignis"] == s["ereignis"] and x["ereignis"] in MARKTEREIGNISSE
                    gemeinsam = bool(bezug & s["bezug"])
                    passt = (wert >= AEHNLICH or (wert >= AEHNLICH_MIT_BEZUG and (gemeinsam or gleich))
                             or (gleich and gemeinsam and wert >= AEHNLICH_GLEICHES_EREIGNIS))
                    if passt and wert > beste_wert:
                        beste, beste_wert = s, wert
                if beste is None:
                    cur = db.execute("INSERT INTO stories (titel, erstes, letztes, anzahl, vektor) VALUES (?, ?, ?, 1, ?)",
                                     (z["titel"], z["gesehen"], z["gesehen"], _als_bytes(v)))
                    beste = {"id": cur.lastrowid, "v": v, "bezug": bezug, "ereignis": x["ereignis"], "nur_fuer": z.get("nur_fuer")}
                    offen.append(beste)
                    db.execute("UPDATE meldungen SET story=?, vektor=? WHERE id=?", (beste["id"], _als_bytes(v), z["id"]))
                    _aktualisieren(db, beste["id"], beste["v"])
                    continue
                else:
                    # Schwerpunkt mitziehen (normiert), damit die Geschichte sich mit neuen Meldungen weiterentwickeln kann
                    neu = beste["v"] * 0.7 + v * 0.3
                    beste["v"] = (neu / (np.linalg.norm(neu) or 1)).astype(np.float32)
                    beste["bezug"] |= bezug
                db.execute("UPDATE meldungen SET story=?, vektor=? WHERE id=?", (beste["id"], _als_bytes(v), z["id"]))
                _aktualisieren(db, beste["id"], beste["v"])
    finally:
        db.close()


def _aktualisieren(db, story_id: int, vektor: np.ndarray) -> None:
    mitglieder = []
    for r in db.execute("SELECT quelle, stufe, titel, gesehen, nur_fuer, extrakt FROM meldungen WHERE story=?", (story_id,)):
        m = dict(r)
        m["extrakt"] = json.loads(m["extrakt"]) if m["extrakt"] else {}
        mitglieder.append(m)
    b = bewerten(mitglieder)
    db.execute("""UPDATE stories SET titel=?, erstes=?, letztes=?, anzahl=?, quellen=?, ereignis=?, ticker=?, laender=?, stellen=?,
                  score=?, bestaetigt=?, eil=?, nur_fuer=?, vektor=?, aktualisiert=? WHERE id=?""",
               (b["titel"], b["erstes"], b["letztes"], b["anzahl"], json.dumps(b["quellen"], ensure_ascii=False), b["ereignis"],
                json.dumps(b["ticker"]), json.dumps(b["laender"], ensure_ascii=False), json.dumps(b["stellen"], ensure_ascii=False),
                b["score"], int(b["bestaetigt"]), int(b["eil"]), b["nur_fuer"], _als_bytes(vektor), speicher.jetzt(), story_id))


def nachholen(grenze: int = 2000) -> int:
    """Noch nicht verarbeitete Meldungen (z. B. nach einem Update) der Reihe nach verarbeiten."""
    db = speicher.verbinden()
    try:
        ids = [r[0] for r in db.execute("SELECT id FROM meldungen WHERE extrakt IS NULL ORDER BY gesehen LIMIT ?", (grenze,))]
    finally:
        db.close()
    for i in range(0, len(ids), 64):
        verarbeiten(ids[i:i + 64])
    return len(ids)


def _arbeiter():
    try:
        nachholen()
    except Exception as f:
        log.warning("Geschichten nachholen: %s", f)
    while True:
        ids = _schlange.get()
        while not _schlange.empty() and len(ids) < 200:  # aufgelaufene zusammen verarbeiten
            ids += _schlange.get_nowait()
        try:
            verarbeiten(ids)
        except Exception as f:
            log.warning("Geschichten: %s", f)


def einreihen(ids: list[int]) -> None:
    _schlange.put(list(ids))


def starte() -> None:
    if _gestartet[0]:
        return
    _gestartet[0] = True
    from .abruf import nach_neuen
    nach_neuen(einreihen)
    threading.Thread(target=_arbeiter, name="newsfeed-geschichten", daemon=True).start()


# ---- Abfrage für die Seite ----

def liste(benutzer: str, stunden: int = 24, ausgefiltert: bool = False) -> list[dict]:
    """Geschichten der letzten Stunden, sichtbar für den Benutzer, sortiert nach Rang."""
    grenze = (datetime.now(timezone.utc) - timedelta(hours=stunden)).isoformat()
    db = speicher.verbinden()
    try:
        with db:
            _schema(db)
        jetzt = datetime.now(timezone.utc)
        stories = [dict(r) for r in db.execute(
            "SELECT id, titel, erstes, letztes, anzahl, quellen, ereignis, ticker, laender, stellen, score, bestaetigt, eil, nur_fuer "
            "FROM stories WHERE letztes >= ? AND (nur_fuer IS NULL OR nur_fuer = ?)", (grenze, benutzer))]
        ids = [s["id"] for s in stories]
        meldungen: dict[int, list] = {}
        if ids:
            for r in db.execute(f"SELECT id, story, quelle, stufe, titel, link, zeit, gesehen, nur_fuer, art FROM meldungen "
                                f"WHERE story IN ({','.join('?' * len(ids))}) AND (nur_fuer IS NULL OR nur_fuer = ?) ORDER BY gesehen",
                                ids + [benutzer]):
                meldungen.setdefault(r["story"], []).append(dict(r))
        aus = []
        for s in stories:
            for k in ("quellen", "ticker", "laender", "stellen"):
                s[k] = json.loads(s[k] or "[]")
            s["meldungen"] = meldungen.get(s["id"], [])
            if not s["meldungen"]:
                continue
            # Quellen, die dieser Benutzer nicht sehen darf (fremdes Telegram), nicht nennen
            s["quellen"] = list(dict.fromkeys(m["quelle"] for m in s["meldungen"]))
            s["bestaetigt"] = bool(s["bestaetigt"])
            s["eil"] = bool(s["eil"])
            s["rang"] = round(rang(s["score"], s["letztes"], jetzt), 2)
            aus.append(s)
        aus.sort(key=lambda s: -s["rang"])
        if ausgefiltert:
            gefiltert = [dict(r) for r in db.execute(
                "SELECT id, quelle, titel, link, gesehen, filter FROM meldungen WHERE filter IS NOT NULL AND gesehen >= ? "
                "AND (nur_fuer IS NULL OR nur_fuer = ?) ORDER BY gesehen DESC LIMIT 300", (grenze, benutzer))]
            return aus, gefiltert
        return aus
    finally:
        db.close()
