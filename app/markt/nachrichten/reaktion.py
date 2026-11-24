"""Reaktions-Erkennung: Welche Schlagzeile hat den Markt bewegt?

Jede Minute holt Vinter Minutenkurse wichtiger Märkte (Yahoo, ein Sammelabruf):
- zur US-Handelszeit ETFs, fast ohne Verzug (gemessen 24.11.2026: etwa 1 Minute): SPY (S&P 500), QQQ (Nasdaq 100),
  SHY (2-jährige US-Anleihen), IEF (10-jährige), UUP (Dollar), USO (Öl), GLD (Gold)
- außerhalb davon die Futures (etwa 10–15 Minuten verzögert): ES=F, NQ=F, ZT=F, ZN=F, Dollar-Index, CL=F, GC=F
- immer USD/CNH (Yuan, fast in Echtzeit)
Den VIX gibt es kostenlos nicht in Echtzeit.

Ein Sprung ist eine Bewegung über 3 Sigma der üblichen Bewegung desselben Markts (Sigma aus den letzten
5 Handelstagen, robust über den Median der Abweichungen), entweder in einer Minute oder über 5 Minuten.
Die Meldungen der 15 Minuten davor gelten als mögliche Auslöser: Ihre Geschichten bekommen die Reaktion
angehängt (+20 Punkte Wichtigkeit). Das ist ein zeitlicher Zusammenhang, kein Beweis.
"""
import json
import logging
import math
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import speicher

log = logging.getLogger(__name__)

US = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "SHY": "2J-Anleihen", "IEF": "10J-Anleihen", "UUP": "Dollar", "USO": "Öl", "GLD": "Gold"}
FUTURES = {"ES=F": "S&P 500", "NQ=F": "Nasdaq 100", "ZT=F": "2J-Anleihen", "ZN=F": "10J-Anleihen", "DX-Y.NYB": "Dollar",
           "CL=F": "Öl", "GC=F": "Gold"}
IMMER = {"CNH=X": "USD/CNH"}
SCHWELLE = 3.0
# Mindestbewegung in Prozent: Bei ruhigen Märkten wäre sonst schon ein einzelner Preisschritt (1 Cent) ein "Sprung"
MINDEST = {"S&P 500": 0.15, "Nasdaq 100": 0.2, "2J-Anleihen": 0.03, "10J-Anleihen": 0.06, "Dollar": 0.08,
           "Öl": 0.3, "Gold": 0.2, "USD/CNH": 0.05}
VORLAUF = timedelta(minutes=15)
TAKT = 60
# Welche Märkte zeigt das Mini-Chart je Ereignistyp (Name wie oben)
CHART = {"zoelle_handel": ["S&P 500", "USD/CNH"], "notenbank": ["2J-Anleihen", "Dollar"], "konjunktur": ["2J-Anleihen", "S&P 500"],
         "geopolitik": ["Öl", "Gold"], "_": ["S&P 500", "Nasdaq 100"]}

_kurse: dict[str, pd.Series] = {}      # Name → Minutenkurse (UTC), aus ETF und Future zusammengesetzt nach Uhrzeit
_sigma: dict[str, float] = {}
_sigma_zeit = [0.0]
_modus = [""]
_gesehen: dict[str, float] = {}         # letzte Sprung-Meldung je Markt und Richtung (gegen Doppelte)
_gestartet = [False]
_sperre = threading.Lock()


def _schema(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS reaktionen (id INTEGER PRIMARY KEY, zeit TEXT, markt TEXT, symbol TEXT,
                  fenster INTEGER, veraenderung REAL, sigma REAL, stories TEXT)""")
    spalten = {r[1] for r in db.execute("PRAGMA table_info(stories)")}
    if spalten and "reaktion" not in spalten:
        db.execute("ALTER TABLE stories ADD COLUMN reaktion TEXT")


def _us_handel() -> bool:
    try:
        from ..volaflaeche import _handelszeit
        return bool(_handelszeit())
    except Exception:
        return False


def _laden(symbole: dict[str, str], zeitraum: str) -> dict[str, pd.Series]:
    import yfinance as yf
    roh = yf.download(list(symbole), period=zeitraum, interval="1m", progress=False, group_by="ticker", threads=False)
    aus = {}
    for sym, name in symbole.items():
        try:
            s = roh[sym]["Close"].dropna()
        except (KeyError, TypeError):
            continue
        if len(s):
            s.index = pd.DatetimeIndex(s.index).tz_convert("UTC")
            aus[name] = s
    return aus


def sigma_aus(s: pd.Series) -> float | None:
    """Übliche Minutenbewegung (log), robust: 1,4826 × Median der absoluten Abweichung."""
    r = np.diff(np.log(s.to_numpy(dtype=float)))
    r = r[np.isfinite(r)]
    if len(r) < 200:
        return None
    mad = float(np.median(np.abs(r - np.median(r)))) * 1.4826
    return mad if mad > 0 else None


def spruenge(s: pd.Series, sigma: float, seit: pd.Timestamp, mindest: float = 0.0) -> list[dict]:
    """Sprünge ab 'seit' über SCHWELLE: 1 Minute und 5 Minuten (reine Funktion)."""
    if s is None or len(s) < 6 or not sigma:
        return []
    lg = np.log(s.astype(float))
    aus = []
    for fenster in (1, 5):
        r = (lg - lg.shift(fenster)).dropna()
        z = r / (sigma * math.sqrt(fenster))
        for t, wert in z[z.index >= seit].items():
            ny = t.tz_convert("America/New_York")
            if ny.hour == 9 and 30 <= ny.minute < 35 + fenster:
                continue  # Eröffnung: die ersten Minuten springen immer, das ist keine Nachrichtenreaktion
            if abs(wert) >= SCHWELLE and abs(math.expm1(r[t]) * 100) >= mindest:
                aus.append({"zeit": t - pd.Timedelta(minutes=fenster), "ende": t, "fenster": fenster,
                            "veraenderung": float(math.expm1(r[t]) * 100), "sigma": float(wert)})
    return aus


def _durchgang() -> None:
    us = _us_handel()
    symbole = {**(US if us else FUTURES), **IMMER}
    modus = "us" if us else "futures"
    if time.time() - _sigma_zeit[0] > 3600 or not _sigma or _modus[0] != modus:
        basis = _laden(symbole, "5d")
        neu = {name: sg for name, kurse in basis.items() if (sg := sigma_aus(kurse))}
        _sigma.clear()
        _sigma.update(neu)
        _sigma_zeit[0] = time.time()
        _modus[0] = modus
    kurz = _laden(symbole, "1d")
    with _sperre:
        _kurse.update(kurz)
    seit = pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=12)
    for name, s in kurz.items():
        for sp in spruenge(s, _sigma.get(name), seit, MINDEST.get(name, 0.0)):
            schluessel = f"{name}:{'+' if sp['veraenderung'] > 0 else '-'}"
            if time.time() - _gesehen.get(schluessel, 0) < 15 * 60:
                continue
            _gesehen[schluessel] = time.time()
            _verknuepfen(name, sp)


def _verknuepfen(name: str, sp: dict) -> None:
    """Sprung speichern und an die Geschichten der Meldungen aus den 15 Minuten davor hängen."""
    von = (sp["zeit"] - VORLAUF).isoformat(timespec="seconds")
    bis = (sp["ende"] + pd.Timedelta(minutes=2)).isoformat(timespec="seconds")
    db = speicher.verbinden()
    try:
        with db:
            _schema(db)
            stories = sorted({r[0] for r in db.execute(
                "SELECT story FROM meldungen WHERE story IS NOT NULL AND filter IS NULL AND gesehen BETWEEN ? AND ?", (von, bis))})
            eintrag = {"markt": name, "zeit": sp["zeit"].isoformat(timespec="seconds"), "fenster": sp["fenster"],
                       "veraenderung": round(sp["veraenderung"], 3), "sigma": round(sp["sigma"], 1)}
            db.execute("INSERT INTO reaktionen (zeit, markt, symbol, fenster, veraenderung, sigma, stories) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (eintrag["zeit"], name, name, sp["fenster"], eintrag["veraenderung"], eintrag["sigma"], json.dumps(stories)))
            for sid in stories:
                alt = db.execute("SELECT reaktion FROM stories WHERE id=?", (sid,)).fetchone()
                liste = json.loads(alt[0]) if alt and alt[0] else []
                if not any(r["markt"] == name and r["zeit"] == eintrag["zeit"] for r in liste):
                    liste.append(eintrag)
                db.execute("UPDATE stories SET reaktion=? WHERE id=?", (json.dumps(liste, ensure_ascii=False), sid))
            from .geschichten import _aktualisieren, _aus_bytes
            for sid in stories:
                v = db.execute("SELECT vektor FROM stories WHERE id=?", (sid,)).fetchone()
                if v and v[0]:
                    _aktualisieren(db, sid, _aus_bytes(v[0]))
    finally:
        db.close()


def verlauf(story: dict) -> dict:
    """Mini-Chart für eine Geschichte: zwei passende Märkte von 30 Min. vor bis 90 Min. nach dem ersten Auftauchen,
    in Prozent ab dem ersten Auftauchen."""
    start = pd.Timestamp(story["erstes"])
    namen = CHART.get(story.get("ereignis"), CHART["_"])
    with _sperre:
        kurse = dict(_kurse)
    aus = []
    for name in namen:
        s = kurse.get(name)
        if s is None:
            continue
        teil = s[(s.index >= start - pd.Timedelta(minutes=30)) & (s.index <= start + pd.Timedelta(minutes=90))]
        vorher = s[s.index <= start]
        if len(teil) < 5 or not len(vorher):
            continue
        basis = float(vorher.iloc[-1])
        aus.append({"markt": name, "punkte": [[t.isoformat(timespec="minutes"), round((float(v) / basis - 1) * 100, 3)] for t, v in teil.items()]})
    return {"start": start.isoformat(), "maerkte": aus}


def _schleife():
    while True:
        try:
            _durchgang()
        except Exception as f:
            log.warning("Reaktions-Erkennung: %s", f)
        time.sleep(TAKT)


def starte() -> None:
    if _gestartet[0]:
        return
    _gestartet[0] = True
    db = speicher.verbinden()
    try:
        with db:
            from .geschichten import _schema as story_schema
            story_schema(db)
            _schema(db)
    finally:
        db.close()
    threading.Thread(target=_schleife, name="newsfeed-reaktion", daemon=True).start()
