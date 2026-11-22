"""Volatilitätsoberfläche für SPY und QQQ: implizite Volatilität je Strike und Laufzeit.

Implizite Volatilität (IV) ist die Schwankung, die der Optionspreis "einpreist": Man sucht die
Volatilität, bei der die Black-Formel genau den Marktpreis ergibt. Trägt man sie für viele
Strikes (x) und Laufzeiten (y) auf, entsteht eine Fläche.

Ablauf einmal pro Handelstag, ca. 30 Minuten nach US-Börsenschluss:
1. Optionsketten bis gut 6 Monate laden (yfinance).
2. Nur liquide Optionen aus dem Geld behalten: Puts unter, Calls über dem Terminkurs.
3. IV selbst aus dem Mittelkurs von Geld und Brief rechnen. Die IV von Yahoo ist dafür zu
   unzuverlässig (Call und Put am selben Strike weichen dort oft mehrere Punkte ab).
4. Je Verfall eine glatte Kurve durch die Punkte legen, Ausreißer entfernen.
5. Zwischen den Verfällen über die "Gesamtvarianz" (IV² × Zeit) interpolieren und auf ein festes
   Raster bringen. Nur dieses Raster wird gespeichert, nicht die Rohdaten.
"""
import json
import logging
import math
import re
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf

from ..cache import STUNDE, markt_lesen, markt_schreiben
from ..konfig import VOLA_HISTORIE
from .boersen import US, _kalender
from .makro import makro_termine

log = logging.getLogger(__name__)

# Immer dabei; weitere US-Werte kommen dazu, sobald jemand sie auf der Seite öffnet (Tabelle "beobachtet").
TICKER = ("SPY", "QQQ")
INDEX_TICKER = {"SPY", "QQQ", "IWM", "DIA"}
LIVE_ALLE = 30 * 60          # während des US-Handels: Fläche alle 30 Minuten neu
BEOBACHTEN_TAGE = 30         # so lange wird ein geöffneter Wert jeden Abend mitgespeichert
LIVE_HOECHSTENS = 8          # so viele Werte rechnet der Hintergrund während des Handels mit
MONEYNESS = [80 + 2.5 * i for i in range(17)]  # Strike in % vom Kurs
TAGE = [7, 14, 21, 30, 45, 60, 75, 90, 120, 150, 180]  # Restlaufzeit in Kalendertagen
WARTEN_NACH_SCHLUSS = timedelta(minutes=30)
KUERZESTER_VERFALL = 4  # Tage; noch kürzere Optionen schwanken zu wild
LAENGSTER_VERFALL = 190

# Richtwerte, solange noch keine 60 Tage eigene Historie gespeichert sind (in Vol-Punkten).
# ATM-IV grob angelehnt an den VIX (SPY) bzw. VXN (QQQ).
RICHTWERTE = {
    "atm30": {"SPY": (18, 28), "QQQ": (23, 33), None: (25, 40)},
    "skew25": {"SPY": (7, 10), "QQQ": (6, 9), None: (8, 12)},
}
MIN_HISTORIE = 60

_sperre = threading.Lock()


# ---- Mathematik: Black-76 und IV ----

_erf = np.vectorize(math.erf)


def _n(x):
    """Verteilungsfunktion der Standardnormalverteilung (ohne scipy)."""
    return 0.5 * (1 + _erf(np.asarray(x, dtype=float) / math.sqrt(2)))


def _preis(f, k, t, r, s, call):
    """Black-76: Optionspreis aus Terminkurs f. So spielen Dividenden keine Rolle mehr."""
    st = s * np.sqrt(t)
    d1 = (np.log(f / k) + 0.5 * st ** 2) / st
    d2 = d1 - st
    abzinsen = math.exp(-r * t)
    return np.where(call, abzinsen * (f * _n(d1) - k * _n(d2)), abzinsen * (k * _n(-d2) - f * _n(-d1)))


def _iv(preis, f, k, t, r, call):
    """Sucht per Halbierung die Volatilität, bei der der Preis passt (für alle Optionen auf einmal)."""
    tief = np.full(len(k), 1e-3)
    hoch = np.full(len(k), 4.0)
    for _ in range(50):
        mitte = (tief + hoch) / 2
        zu_teuer = _preis(f, k, t, r, mitte, call) > preis
        hoch = np.where(zu_teuer, mitte, hoch)
        tief = np.where(zu_teuer, tief, mitte)
    iv = (tief + hoch) / 2
    iv[(iv < 0.02) | (iv > 3.9)] = np.nan  # nicht lösbar, z. B. Preis unter dem inneren Wert
    return iv


# ---- Daten holen und je Verfall eine Kurve legen ----

def _zins() -> float:
    """Kurzfristiger US-Zins aus der 13-Wochen-T-Bill (^IRX), sonst 4 %."""
    try:
        return float(yf.Ticker("^IRX").history(period="5d")["Close"].dropna().iloc[-1]) / 100
    except Exception:
        return 0.04


def _saubere_optionen(tabelle: pd.DataFrame) -> pd.DataFrame:
    """Nur Optionen mit echtem Geld- und Briefkurs, enger Spanne und etwas Handel."""
    t = tabelle[(tabelle["bid"] > 0) & (tabelle["ask"] > tabelle["bid"])].copy()
    t["mid"] = (t["bid"] + t["ask"]) / 2
    t["spanne"] = (t["ask"] - t["bid"]) / t["mid"]
    liquide = (t["openInterest"].fillna(0) >= 10) | (t["volume"].fillna(0) >= 5)
    return t[(t["mid"] >= 0.05) & (t["spanne"] <= 0.35) & liquide]


def _handel(kette, kurs: float) -> tuple[list[float], list[float]]:
    """Open Interest und Tagesvolumen je Strike-Spalte des Rasters (Calls und Puts zusammen).

    Jeder Strike zählt zur nächstgelegenen Spalte (80, 82,5, … 120 % vom Kurs). Rohdaten, ungefiltert:
    Hier geht es darum, wo gehandelt wird, nicht um saubere Preise.
    """
    oi, vol = [0.0] * len(MONEYNESS), [0.0] * len(MONEYNESS)
    for tabelle in (kette.calls, kette.puts):
        for strike, offen, menge in zip(tabelle["strike"], tabelle["openInterest"].fillna(0), tabelle["volume"].fillna(0)):
            spalte = round((strike / kurs * 100 - MONEYNESS[0]) / (MONEYNESS[1] - MONEYNESS[0]))
            if 0 <= spalte < len(MONEYNESS):
                oi[spalte] += float(offen)
                vol[spalte] += float(menge)
    return oi, vol


def _handel_raster(verfaelle: list[tuple[int, list[float]]]) -> list[list[float]]:
    """Auf die festen Laufzeiten des Rasters bringen: zwischen zwei Verfällen logarithmisch
    interpoliert, weil Monatsverfälle oft zehnmal so viel Handel haben wie Wochenverfälle."""
    tage = [v[0] for v in verfaelle]
    raster = []
    for ziel in TAGE:
        zeile = []
        for j in range(len(MONEYNESS)):
            werte = [math.log1p(v[1][j]) for v in verfaelle]
            zeile.append(round(math.expm1(float(np.interp(ziel, tage, werte)))))
        raster.append(zeile)
    return raster


def _kurve(kette, kurs: float, t: float, r: float) -> dict | None:
    calls, puts = _saubere_optionen(kette.calls), _saubere_optionen(kette.puts)
    if calls.empty or puts.empty:
        return None

    # Terminkurs aus der Put-Call-Parität: F = K + e^(rT) * (Call - Put), nahe am Geld gemittelt.
    paare = calls.merge(puts, on="strike", suffixes=("_c", "_p"))
    nah = paare[(paare["strike"] / kurs - 1).abs() <= 0.05]
    if len(nah) >= 2:
        f = float(np.median(nah["strike"] + math.exp(r * t) * (nah["mid_c"] - nah["mid_p"])))
    else:
        f = kurs * math.exp(r * t)

    aus_dem_geld = pd.concat([puts[puts["strike"] < f].assign(call=False), calls[calls["strike"] >= f].assign(call=True)])
    aus_dem_geld = aus_dem_geld[(aus_dem_geld["strike"] / kurs).between(0.7, 1.3)]
    if len(aus_dem_geld) < 6:
        return None
    k = aus_dem_geld["strike"].to_numpy(float)
    iv = _iv(aus_dem_geld["mid"].to_numpy(float), f, k, t, r, aus_dem_geld["call"].to_numpy(bool))
    lm = np.log(k / f)  # log-Moneyness: 0 = am Geld
    ok = np.isfinite(iv)
    lm, iv = lm[ok], iv[ok]
    # Es braucht Punkte auf beiden Seiten des Kurses, sonst ist die Kurve geraten.
    if len(lm) < 6 or not ((lm < 0).any() and (lm >= 0).any()) or np.abs(lm).min() > 0.03:
        return None

    # Glatte Kurve (Polynom 3. Grades); Punkte, die weit daneben liegen, fliegen raus.
    grad = 3 if len(lm) >= 8 else 2
    for _ in range(3):
        koeff = np.polyfit(lm, iv, grad)
        rest = iv - np.polyval(koeff, lm)
        streuung = 1.4826 * np.median(np.abs(rest - np.median(rest)))
        behalten = np.abs(rest) <= max(3 * streuung, 0.01)
        if behalten.all() or behalten.sum() < 6:
            break
        lm, iv = lm[behalten], iv[behalten]
    return {"t": t, "f": f, "koeff": koeff.tolist(), "von": float(lm.min()), "bis": float(lm.max()), "punkte": int(len(lm))}


def _iv_auf_kurve(kurve: dict, kurs: float, m: float) -> tuple[float, bool]:
    """IV bei Strike m × Kurs. Außerhalb der gehandelten Strikes: am Rand fortgeschrieben."""
    lm = math.log(m * kurs / kurve["f"])
    geschaetzt = not (kurve["von"] <= lm <= kurve["bis"])
    lm = min(max(lm, kurve["von"]), kurve["bis"])
    return max(0.01, float(np.polyval(kurve["koeff"], lm))), geschaetzt


def _iv_bei(kurven: list[dict], kurs: float, m: float, t: float) -> tuple[float, bool]:
    """IV bei Strike m und Laufzeit t (Jahre): linear in der Gesamtvarianz IV² × t zwischen den Verfällen."""
    if t <= kurven[0]["t"]:
        iv, geschaetzt = _iv_auf_kurve(kurven[0], kurs, m)
        return iv, geschaetzt or t < kurven[0]["t"] * 0.8
    if t >= kurven[-1]["t"]:
        iv, geschaetzt = _iv_auf_kurve(kurven[-1], kurs, m)
        return iv, geschaetzt or t > kurven[-1]["t"] * 1.05
    for a, b in zip(kurven, kurven[1:]):
        if a["t"] <= t <= b["t"]:
            iva, ga = _iv_auf_kurve(a, kurs, m)
            ivb, gb = _iv_auf_kurve(b, kurs, m)
            anteil = (t - a["t"]) / (b["t"] - a["t"])
            varianz = (1 - anteil) * iva ** 2 * a["t"] + anteil * ivb ** 2 * b["t"]
            return math.sqrt(max(varianz, 1e-6) / t), ga or gb
    raise ValueError("Laufzeit nicht gefunden")


def _skew25(kurven: list[dict], kurs: float, tage: int = 30) -> float | None:
    """IV des 25-Delta-Puts minus IV des 25-Delta-Calls, in Vol-Punkten.

    Delta ist die Kursempfindlichkeit einer Option. Ein 25-Delta-Put liegt etwa so weit unter dem
    Kurs wie ein 25-Delta-Call darüber, gemessen in Schwankungen. Je größer die Differenz, desto
    mehr zahlt der Markt für Schutz gegen Kursrückgänge.
    """
    t = tage / 365
    # Terminkurs bei 30 Tagen, zwischen den Verfällen interpoliert.
    f = float(np.interp(t, [k["t"] for k in kurven], [k["f"] for k in kurven]))
    werte = []
    for m in np.arange(0.7, 1.3001, 0.0025):
        iv, _ = _iv_bei(kurven, kurs, float(m), t)
        d1 = (math.log(f / (m * kurs)) + 0.5 * iv ** 2 * t) / (iv * math.sqrt(t))
        werte.append((float(_n(d1)), iv))
    put = _bei_delta(werte, 0.75)   # Put-Delta −0,25 heißt N(d1) = 0,75
    call = _bei_delta(werte, 0.25)
    return None if put is None or call is None else (put - call) * 100


def _bei_delta(werte, ziel):
    for (n1, iv1), (n2, iv2) in zip(werte, werte[1:]):  # N(d1) fällt mit steigendem Strike
        if n1 >= ziel >= n2 and n1 != n2:
            return iv1 + (iv2 - iv1) * (n1 - ziel) / (n1 - n2)
    return None


def berechne(ticker: str, sitzung: date, schluss: datetime) -> dict:
    """Die Fläche für einen Handelstag. schluss = Börsenschluss dieses Tages (UTC)."""
    t = yf.Ticker(ticker)
    verlauf = t.history(period="10d")["Close"].dropna()
    verlauf.index = pd.DatetimeIndex(verlauf.index).tz_localize(None).normalize()
    bis_tag = verlauf[verlauf.index.date <= sitzung]
    if bis_tag.empty:
        raise ValueError("kein Schlusskurs")
    kurs = float(bis_tag.iloc[-1])
    r = _zins()

    # Alle Verfälle von knapp einer Woche bis gut 6 Monate (plus den ersten danach zum Interpolieren).
    verfaelle = []
    for v in t.options:
        tage = (date.fromisoformat(v) - sitzung).days
        if tage < KUERZESTER_VERFALL:
            continue
        verfaelle.append((v, tage))
        if tage > LAENGSTER_VERFALL:
            break
    kurven, handel = [], []
    for v, tage in verfaelle:
        # Restlaufzeit vom Schluss heute bis zum Schluss am Verfallstag (16 Uhr New York).
        t_jahre = tage / 365
        try:
            kette = t.option_chain(v)
            handel.append((tage, *_handel(kette, kurs)))
            kurve = _kurve(kette, kurs, t_jahre, r)
        except Exception:
            kurve = None
        if kurve:
            kurve["verfall"] = v
            kurven.append(kurve)
    if len(kurven) < 4:
        raise ValueError("zu wenige brauchbare Verfälle")

    iv, geschaetzt = [], []
    for tage in TAGE:
        zeile, flags = [], []
        for m in MONEYNESS:
            wert, g = _iv_bei(kurven, kurs, m / 100, tage / 365)
            zeile.append(wert * 100)
            flags.append(g)
        iv.append(zeile)
        geschaetzt.append(flags)
    # Leicht glätten entlang der Laufzeit (1-2-1), damit einzelne Verfälle keine Rillen ziehen.
    roh = np.array(iv)
    glatt = roh.copy()
    glatt[1:-1] = (roh[:-2] + 2 * roh[1:-1] + roh[2:]) / 4
    iv = np.round(glatt, 3).tolist()

    i30, i90, mitte = TAGE.index(30), TAGE.index(90), MONEYNESS.index(100)
    kennzahlen = {
        "atm30": round(iv[i30][mitte], 2),
        "skew25": _runden(_skew25(kurven, kurs)),
        "steigung": round(iv[i90][mitte] - iv[i30][mitte], 2),
    }
    return {
        "ticker": ticker,
        "datum": sitzung.isoformat(),
        "abgerufen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kurs": round(kurs, 2),
        "zins": round(r * 100, 2),
        "iv": iv,
        "geschaetzt": geschaetzt,
        "kennzahlen": kennzahlen,
        "verfaelle": [{"verfall": k["verfall"], "punkte": k["punkte"]} for k in kurven],
        # Liquidität je Feld: Open Interest (offene Kontrakte) und Volumen des Tages.
        "oi": _handel_raster([(h[0], h[1]) for h in handel]) if handel else None,
        "volumen": _handel_raster([(h[0], h[2]) for h in handel]) if handel else None,
    }


def _runden(x):
    return None if x is None else round(x, 2)


# ---- Speichern: ein Raster pro Tag und Ticker ----

def _db():
    VOLA_HISTORIE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(VOLA_HISTORIE)
    db.execute("CREATE TABLE IF NOT EXISTS flaechen (ticker TEXT, datum TEXT, daten TEXT, PRIMARY KEY (ticker, datum))")
    db.execute("CREATE TABLE IF NOT EXISTS beobachtet (ticker TEXT PRIMARY KEY, zuletzt TEXT)")
    # Depot-Werte für die IV-Historie (IV-Rang): nur abends speichern, keine Live-Flächen; mit_optionen 0 = nie abrufen
    db.execute("CREATE TABLE IF NOT EXISTS vorgemerkt (ticker TEXT PRIMARY KEY, zuletzt TEXT, mit_optionen INTEGER)")
    db.execute("CREATE TABLE IF NOT EXISTS live_flaechen (ticker TEXT, zeit TEXT, daten TEXT, PRIMARY KEY (ticker, zeit))")
    db.execute("CREATE TABLE IF NOT EXISTS tagesverlauf (ticker TEXT, zeit TEXT, atm30 REAL, skew25 REAL, steigung REAL, kurs REAL, PRIMARY KEY (ticker, zeit))")
    return db


def _merken(ticker: str) -> None:
    db = _db()
    try:
        with db:
            db.execute("INSERT OR REPLACE INTO beobachtet VALUES (?, ?)", (ticker, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    finally:
        db.close()


def vormerken(ticker: list[str]) -> None:
    """US-Werte des Depots abends mitspeichern, damit eine eigene IV-Historie wächst. Ob Yahoo Optionen hat,
    wird einmal geprüft und gemerkt (Werte ohne Optionen werden nie abgerufen)."""
    jetzt = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db = _db()
    try:
        bekannt = dict(db.execute("SELECT ticker, mit_optionen FROM vorgemerkt").fetchall())
        for tk in ticker:
            if not re.fullmatch(r"[A-Z][A-Z0-9]{0,5}", tk):
                continue
            if tk in bekannt:
                with db:
                    db.execute("UPDATE vorgemerkt SET zuletzt = ? WHERE ticker = ?", (jetzt, tk))
                continue
            try:
                hat = 1 if yf.Ticker(tk).options else 0
            except Exception:
                continue
            with db:
                db.execute("INSERT OR REPLACE INTO vorgemerkt VALUES (?, ?, ?)", (tk, jetzt, hat))
    finally:
        db.close()


def _vorgemerkt(seit: timedelta) -> list[str]:
    grenze = (datetime.now(timezone.utc) - seit).isoformat(timespec="seconds")
    db = _db()
    try:
        return [z[0] for z in db.execute("SELECT ticker FROM vorgemerkt WHERE zuletzt >= ? AND mit_optionen = 1", (grenze,)).fetchall()]
    finally:
        db.close()


def _neuestes_datum(ticker: str) -> str | None:
    db = _db()
    try:
        return db.execute("SELECT MAX(datum) FROM flaechen WHERE ticker = ?", (ticker,)).fetchone()[0]
    finally:
        db.close()


def _beobachtet(seit: timedelta) -> list[str]:
    grenze = (datetime.now(timezone.utc) - seit).isoformat(timespec="seconds")
    db = _db()
    try:
        zeilen = db.execute("SELECT ticker FROM beobachtet WHERE zuletzt >= ? ORDER BY zuletzt DESC", (grenze,)).fetchall()
    finally:
        db.close()
    return [z[0] for z in zeilen]


def _live_flaechen(ticker: str) -> list[dict]:
    db = _db()
    try:
        zeilen = db.execute("SELECT daten FROM live_flaechen WHERE ticker = ? ORDER BY zeit", (ticker,)).fetchall()
    finally:
        db.close()
    return [json.loads(z[0]) for z in zeilen]


def _tagesverlauf_speichern(f: dict) -> None:
    db = _db()
    try:
        with db:
            # Die Live-Fläche selbst (nur das Raster), damit sie sich mit Uhrzeit abspielen und vergleichen lässt; 10 Tage
            db.execute("INSERT OR REPLACE INTO live_flaechen VALUES (?, ?, ?)", (f["ticker"], f["abgerufen"], json.dumps({"datum": f["datum"], "zeit": f["abgerufen"], "iv": f["iv"], "kurs": f["kurs"]})))
            db.execute("DELETE FROM live_flaechen WHERE zeit < ?", ((datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),))
            k = f["kennzahlen"]
            db.execute("INSERT OR REPLACE INTO tagesverlauf VALUES (?, ?, ?, ?, ?, ?)", (f["ticker"], f["abgerufen"], k.get("atm30"), k.get("skew25"), k.get("steigung"), f.get("kurs")))
            db.execute("DELETE FROM tagesverlauf WHERE zeit < ?", ((datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),))
    finally:
        db.close()


def _tagesverlauf(ticker: str, datum: str) -> list[dict]:
    db = _db()
    try:
        zeilen = db.execute("SELECT zeit, atm30, skew25, steigung, kurs FROM tagesverlauf WHERE ticker = ? ORDER BY zeit", (ticker,)).fetchall()
    finally:
        db.close()
    zone = _kalender(US).tz
    aus = []
    for zeit, atm30, skew25, steigung, kurs in zeilen:
        ny = pd.Timestamp(zeit).tz_convert(zone)
        if ny.date().isoformat() == datum:
            aus.append({"zeit": zeit, "atm30": atm30, "skew25": skew25, "steigung": steigung, "kurs": kurs})
    return aus


def _speichern(flaeche: dict) -> None:
    db = _db()
    try:
        with db:
            db.execute("INSERT OR REPLACE INTO flaechen VALUES (?, ?, ?)", (flaeche["ticker"], flaeche["datum"], json.dumps(flaeche)))
    finally:
        db.close()


def _alle(ticker: str) -> list[dict]:
    db = _db()
    try:
        zeilen = db.execute("SELECT daten FROM flaechen WHERE ticker = ? ORDER BY datum", (ticker,)).fetchall()
    finally:
        db.close()
    return [json.loads(z[0]) for z in zeilen]


# ---- Wann wird abgerufen? ----

def _faellige_sitzung(jetzt: pd.Timestamp | None = None):
    """Der Handelstag, dessen Schlusskurse gerade gelten: ab 30 Minuten nach Schluss bis zur
    nächsten Eröffnung. Während des Handels: None (dann wären es keine Schlusskurse)."""
    kal = _kalender(US)
    jetzt = jetzt or pd.Timestamp.now(tz="UTC")
    heute = jetzt.tz_convert(kal.tz).tz_localize(None).normalize()
    letzte = kal.date_to_session(heute, direction="previous")
    if kal.session_open(letzte) <= jetzt < kal.session_close(letzte) + WARTEN_NACH_SCHLUSS:
        return None  # heute wird noch gehandelt oder es sind keine 30 Minuten vorbei
    if jetzt < kal.session_open(letzte):
        letzte = kal.previous_session(letzte)
    return letzte.date(), kal.session_close(letzte).to_pydatetime()


def _handelszeit(jetzt: pd.Timestamp | None = None):
    """Heutige Sitzung (Datum, Schluss), wenn die US-Börse gerade offen ist, sonst None."""
    kal = _kalender(US)
    jetzt = jetzt or pd.Timestamp.now(tz="UTC")
    heute = jetzt.tz_convert(kal.tz).tz_localize(None).normalize()
    if not kal.is_session(heute):
        return None
    if kal.session_open(heute) <= jetzt < kal.session_close(heute):
        return heute.date(), kal.session_close(heute).to_pydatetime()
    return None


def live(ticker: str, auch_ausserhalb: bool = False) -> dict | None:
    """Momentaufnahme mit aktuellen Kursen, höchstens alle 30 Minuten neu. Wird nicht als Tagesfläche gespeichert,
    aber jede Aufnahme kommt in den Tagesverlauf (IV über den Tag)."""
    sitzung = _handelszeit()
    if not sitzung and not auch_ausserhalb:
        return None
    schluessel = f"volaflaeche:live:{ticker}"
    alt, alter = markt_lesen(schluessel)
    if alt and alter < LIVE_ALLE and (not sitzung or alt.get("datum") == sitzung[0].isoformat()):
        return alt
    if not sitzung:
        kal = _kalender(US)
        heute = pd.Timestamp.now(tz=kal.tz).tz_localize(None).normalize()
        tag = kal.date_to_session(heute, direction="previous")
        sitzung = (tag.date(), kal.session_close(tag).to_pydatetime())
    neu = berechne(ticker, *sitzung)
    neu["live"] = bool(_handelszeit())
    markt_schreiben(schluessel, neu)
    if neu["live"]:
        _tagesverlauf_speichern(neu)
    return neu


def aktualisieren(ticker: str) -> dict | None:
    """Holt die Fläche des fälligen Handelstags, wenn sie noch fehlt. Gibt sie zurück oder None."""
    faellig = _faellige_sitzung()
    if not faellig:
        return None
    sitzung, schluss = faellig
    with _sperre:
        neuestes = _neuestes_datum(ticker)  # nur das Datum, nicht alle gespeicherten Flächen laden
        if neuestes and neuestes >= sitzung.isoformat():
            return None
        flaeche = berechne(ticker, sitzung, schluss)
        _speichern(flaeche)
        return flaeche


def _hintergrund():
    """Alle 10 Minuten: fehlende Tagesflächen nach US-Schluss speichern (SPY, QQQ und alles, was in den letzten
    30 Tagen geöffnet wurde). Während des Handels zusätzlich alle 30 Minuten eine Live-Fläche für SPY, QQQ und
    die Werte, die in den letzten 24 Stunden angesehen wurden. Läuft nur, solange der Server läuft."""
    while True:
        try:
            alle = list(dict.fromkeys([*TICKER, *_beobachtet(timedelta(days=BEOBACHTEN_TAGE)), *_vorgemerkt(timedelta(days=BEOBACHTEN_TAGE))]))
            frisch = list(dict.fromkeys([*TICKER, *_beobachtet(timedelta(days=1))]))[:LIVE_HOECHSTENS]
        except Exception:
            alle, frisch = list(TICKER), list(TICKER)
        from ..status import hinweis, job
        ohne = []
        with job("Volatilitätsflächen"):
            for ticker in alle:
                try:
                    aktualisieren(ticker)
                except Exception as fehler:
                    ohne.append(ticker)
                    log.warning("Volatilitätsoberfläche %s: %s", ticker, fehler)
            if _handelszeit():
                for ticker in frisch:
                    try:
                        live(ticker)
                    except Exception as fehler:
                        log.warning("Live-Fläche %s: %s", ticker, fehler)
        hinweis("Volatilitätsflächen", f"{len(alle) - len(ohne)} von {len(alle)} Werten aktuell" + (f", ohne: {', '.join(ohne[:5])}" if ohne else ""))
        time.sleep(600)


def starte_hintergrund() -> None:
    threading.Thread(target=_hintergrund, name="volaflaeche", daemon=True).start()


# ---- Antwort für die Seite ----

def _einordnung(name: str, wert, ticker: str, historie: list[float]) -> dict:
    if wert is None:
        return {"stufe": None, "text": "nicht berechenbar"}
    if name == "steigung":
        # Normal kosten lange Optionen mehr als kurze (steigende Kurve). Kippt sie, ist es Stress.
        if wert >= 0.25:
            return {"stufe": "normal", "text": "lange Laufzeiten teurer als kurze, wie üblich"}
        if wert > -0.5:
            return {"stufe": "erhöht", "text": "Kurve fast flach: kurzfristig mehr Unruhe eingepreist"}
        return {"stufe": "Stress", "text": "Kurve invertiert: der Markt erwartet die Unruhe jetzt"}
    if len(historie) >= MIN_HISTORIE:
        rang = sum(h <= wert for h in historie) / len(historie) * 100
        stufe = "normal" if rang < 80 else "erhöht" if rang < 95 else "Stress"
        return {"stufe": stufe, "text": f"höher als an {rang:.0f} % der {len(historie)} gespeicherten Tage", "quelle": "historie"}
    if ticker not in RICHTWERTE[name]:
        return {"stufe": None, "text": f"Einordnung ab {MIN_HISTORIE} gespeicherten Handelstagen (bisher {len(historie)})", "quelle": "fehlt"}
    erhoeht, stress = RICHTWERTE[name][ticker]
    stufe = "normal" if wert < erhoeht else "erhöht" if wert < stress else "Stress"
    return {"stufe": stufe, "text": f"Richtwert: unter {erhoeht} normal, ab {stress} Stress", "quelle": "richtwert"}


def _termine(sitzung: date) -> list[dict]:
    """Wichtige US-Termine aus dem Makro-Kalender, die in die Laufzeiten der Fläche fallen."""
    kuerzel = (("CPI", "CPI"), ("Fed", "Fed"), ("Arbeitsmarkt", "Payrolls"))
    liste = []
    for t in makro_termine().get("termine", []):
        kurz = next((k for suche, k in kuerzel if suche in t["name"]), None)
        tage = (date.fromisoformat(t["datum"]) - sitzung).days
        if kurz and 0 < tage <= TAGE[-1]:
            liste.append({"datum": t["datum"], "tage": tage, "name": t["name"], "kurz": kurz})
    return liste


def _earnings_daten(ticker: str) -> list[str]:
    """Vergangene Earnings-Tage (New Yorker Datum) aus dem Earnings-Kalender, neueste zuerst."""
    from .earnings import _ergebnisse
    tage = []
    for e in _ergebnisse(ticker):
        if e.get("ist") is None:
            continue
        zeit = pd.Timestamp(e["zeit"])
        zeit = zeit.tz_convert("America/New_York") if zeit.tzinfo else zeit
        tage.append(zeit.date().isoformat())
    return sorted(set(tage), reverse=True)


def oberflaeche(ticker: str) -> dict:
    ticker = ticker.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker) or "." in ticker:
        return {"fehler": f"Für {ticker} gibt es hier keine Volatilitätsoberfläche: nur US-Werte mit Optionen bei Yahoo."}
    gespeichert = _alle(ticker)
    if not gespeichert:
        try:
            if not yf.Ticker(ticker).options:
                return {"fehler": f"Für {ticker} gibt es bei Yahoo keine Optionen. Die Fläche geht nur für US-Werte mit gehandelten Optionen."}
        except Exception:
            return {"fehler": "Die Optionsdaten konnten gerade nicht geladen werden. Später noch einmal versuchen."}
    _merken(ticker)
    if not gespeichert:
        try:
            aktualisieren(ticker)
        except Exception:
            pass
        gespeichert = _alle(ticker)
    jetzt_live = None
    try:
        jetzt_live = live(ticker, auch_ausserhalb=not gespeichert)
    except Exception:
        if not gespeichert:
            return {"fehler": "Die Optionsdaten konnten gerade nicht geladen werden. Später noch einmal versuchen."}
    jetzt = jetzt_live or gespeichert[-1]
    ist_live = bool(jetzt_live and jetzt_live.get("live"))
    sitzung = date.fromisoformat(jetzt["datum"])
    frueher = [g for g in gespeichert if g["datum"] < jetzt["datum"]] if jetzt_live else gespeichert[:-1]

    def vor(tage_min: int, tage_max: int):
        f = next((f for f in reversed(frueher) if date.fromisoformat(f["datum"]) <= sitzung - timedelta(days=tage_min)), None)
        return f if f and date.fromisoformat(f["datum"]) >= sitzung - timedelta(days=tage_max) else None

    gestern = frueher[-1] if frueher else None
    vor_woche, vor_monat = vor(7, 12), vor(28, 40)
    vor_earnings, earnings_tag = None, None
    if ticker not in INDEX_TICKER:
        try:
            tage = [t for t in _earnings_daten(ticker) if t <= sitzung.isoformat()]
        except Exception:
            tage = []
        if tage:
            earnings_tag = tage[0]
            vorher = [f for f in frueher if f["datum"] < earnings_tag]
            if vorher and date.fromisoformat(vorher[-1]["datum"]) >= date.fromisoformat(earnings_tag) - timedelta(days=6):
                vor_earnings = vorher[-1]

    verlauf = [{"datum": f["datum"], **f["kennzahlen"]} for f in gespeichert]
    einordnung = {
        name: _einordnung(name, jetzt["kennzahlen"][name], ticker, [v[name] for v in verlauf if v.get(name) is not None and v["datum"] < jetzt["datum"]])
        for name in ("atm30", "skew25", "steigung")
    }
    vergleich = lambda f: {"datum": f["datum"], "iv": f["iv"]} if f else None
    return {
        **{k: jetzt[k] for k in ("ticker", "datum", "abgerufen", "kurs", "zins", "iv", "geschaetzt", "kennzahlen", "verfaelle")},
        "oi": jetzt.get("oi"),
        "volumen": jetzt.get("volumen"),
        "moneyness": MONEYNESS,
        "tage": TAGE,
        "live": ist_live,
        "vorlaeufig": bool(jetzt_live) and not gespeichert,
        "gestern": vergleich(gestern),
        "woche": vergleich(vor_woche),
        "monat": vergleich(vor_monat),
        "earnings": vergleich(vor_earnings),
        "earnings_tag": earnings_tag,
        "index": ticker in INDEX_TICKER,
        "verlauf": verlauf,
        "tagesverlauf": _tagesverlauf(ticker, jetzt["datum"]) if ist_live else [],
        # Zum Abspielen und als frei wählbarer Startpunkt: Schlussflächen (bis 40 Handelstage) und Live-Aufnahmen (10 Tage), nach Zeit
        "archiv": sorted([{"datum": f["datum"], "zeit": f.get("abgerufen"), "art": "schluss", "iv": f["iv"], "kurs": f["kurs"]} for f in gespeichert[-40:]]
                         + [{**f, "art": "live"} for f in _live_flaechen(ticker)], key=lambda f: (f["datum"], f["art"] == "schluss", f.get("zeit") or "")),
        "einordnung": einordnung,
        "termine": _termine(sitzung),
        "erster_tag": gespeichert[0]["datum"] if gespeichert else jetzt["datum"],
        "ticker_liste": list(dict.fromkeys([*TICKER, *_beobachtet(timedelta(days=BEOBACHTEN_TAGE))])),
    }
