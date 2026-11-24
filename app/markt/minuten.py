"""Eigene Minutendaten für die Event Study (Intraday-Fenster).

Yahoo liefert 1-Minuten-Kurse nur etwa 30 Tage zurück. Damit spätere Auswertungen in Minuten möglich sind, speichert
Vinter für die wichtigsten Reihen jeden Abend die Minuten der letzten Tage in daten/markt/minuten.sqlite
(nicht im Cache, weil sich das nicht nachholen lässt). Beim ersten Lauf werden die letzten ~29 Tage in
Blöcken zu 7 Tagen geholt (mehr erlaubt Yahoo je Abfrage nicht).
Anleihen-Renditen gibt es nicht minutengenau kostenlos: Statt der Rendite wird der Kurs der Anleihe-ETFs
gespeichert (IEF für 7–10 J., SHY für 1–3 J.); fällt der Kurs, steigt die Rendite.
"""
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from ..konfig import MARKT_DATEN

log = logging.getLogger(__name__)
DATEI = MARKT_DATEN / "minuten.sqlite"
# Schlüssel der Event-Study-Reihe → Symbol für die Minuten (bei Zinsen der ETF statt der Rendite)
SYMBOLE = {"spx": "SPY", "ndx": "QQQ", "rut": "IWM", "dax": "^GDAXI", "us10": "IEF", "us2": "SHY", "vix": "^VIX",
           "gold": "GC=F", "wti": "CL=F", "dxy": "DX-Y.NYB", "eurusd": "EURUSD=X", "usdcnh": "CNH=X", "btc": "BTC-USD"}
ERSATZ = {"us10": "IEF (Kurs statt Rendite)", "us2": "SHY (Kurs statt Rendite)"}
_gestartet = [False]


def _db():
    MARKT_DATEN.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATEI, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS kurse (symbol TEXT, zeit INTEGER, schluss REAL, PRIMARY KEY (symbol, zeit)) WITHOUT ROWID")
    return db


def speichern(symbol: str, s: pd.Series) -> int:
    s = s.dropna()
    if s.empty:
        return 0
    idx = pd.DatetimeIndex(s.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    zeilen = [(symbol, int(t.timestamp()), float(v)) for t, v in zip(idx, s.to_numpy())]
    db = _db()
    try:
        with db:
            vorher = db.total_changes
            db.executemany("INSERT OR IGNORE INTO kurse VALUES (?, ?, ?)", zeilen)
            return db.total_changes - vorher
    finally:
        db.close()


def _holen(symbole: list[str], start: datetime, ende: datetime) -> dict[str, pd.Series]:
    import yfinance as yf
    roh = yf.download(symbole, start=start, end=ende, interval="1m", progress=False, group_by="ticker", threads=False, auto_adjust=False)
    aus = {}
    for s in symbole:
        try:
            aus[s] = roh[s]["Close"].dropna()
        except (KeyError, TypeError):
            continue
    return aus


def aktualisieren(tage: int = 6) -> int:
    """Minuten der letzten Tage nachtragen (bereits gespeicherte bleiben)."""
    db = _db()
    try:
        leer = db.execute("SELECT COUNT(*) FROM kurse").fetchone()[0] == 0
    finally:
        db.close()
    jetzt = datetime.now(timezone.utc)
    tage = 29 if leer else tage
    neu = 0
    symbole = sorted(set(SYMBOLE.values()))
    start = jetzt - timedelta(days=tage)
    while start < jetzt:
        ende = min(jetzt, start + timedelta(days=7))
        for sym, s in _holen(symbole, start, ende).items():
            neu += speichern(sym, s)
        start = ende
        time.sleep(2)
    return neu


def reihe(info: dict) -> pd.Series | None:
    """Gespeicherte Minuten einer Event-Study-Reihe (UTC-Index). Zinsen über den ETF-Kurs, Einheit dann Prozent."""
    sym = SYMBOLE.get(info["schluessel"]) or (info["symbol"] if info["schluessel"].startswith("t:") else None)
    if not sym:
        return None
    db = _db()
    try:
        zeilen = db.execute("SELECT zeit, schluss FROM kurse WHERE symbol=? ORDER BY zeit", (sym,)).fetchall()
    finally:
        db.close()
    if not zeilen:
        return None
    if info["schluessel"] in ERSATZ:
        info["einheit"] = "prozent"
        info["name"] = f"{info['name']}: {ERSATZ[info['schluessel']]}"
    s = pd.Series([z[1] for z in zeilen], index=pd.to_datetime([z[0] for z in zeilen], unit="s", utc=True))
    return s


def umfang() -> dict:
    db = _db()
    try:
        r = db.execute("SELECT COUNT(*), MIN(zeit), MAX(zeit), COUNT(DISTINCT symbol) FROM kurse").fetchone()
    finally:
        db.close()
    iso = lambda t: None if t is None else datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="minutes")  # noqa: E731
    return {"zeilen": r[0], "von": iso(r[1]), "bis": iso(r[2]), "reihen": r[3]}


def _schleife():
    from ..status import hinweis, job
    time.sleep(180)
    letzter = None
    while True:
        heute = datetime.now(timezone.utc).date()
        # einmal am Tag nach US-Schluss (22:30 UTC), beim Start sofort, falls heute noch nicht gelaufen
        if letzter != heute and (datetime.now(timezone.utc).hour >= 22 or letzter is None):
            try:
                with job("Minutendaten"):
                    n = aktualisieren()
                    hinweis("Minutendaten", f"{n} neue Minuten, gespeichert {umfang()['zeilen']}")
            except Exception as f:
                log.warning("Minutendaten: %s", f)
            letzter = heute
        time.sleep(1800)


def starte() -> None:
    if _gestartet[0]:
        return
    _gestartet[0] = True
    threading.Thread(target=_schleife, name="minutendaten", daemon=True).start()
