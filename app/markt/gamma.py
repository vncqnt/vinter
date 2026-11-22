"""Gamma-Exposure (GEX) je Strike aus den Optionsketten von Yahoo (kostenlos, etwa 15 Minuten verzögert).

Was es zeigt: Wie stark Händler (Market Maker) ihre Absicherung anpassen müssen, wenn sich der Kurs um 1 %
bewegt, getrennt nach Calls und Puts und je Strike. Rechnung je Option:
    Gamma (Black-Scholes, IV aus dem Mittelkurs) × Open Interest × 100 × Kurs² × 1 %
Übliche Annahme (wie bei den bekannten GEX-Seiten): Händler halten Calls long und Puts short.
Calls zählen dann positiv, Puts negativ. Das stimmt nicht immer, deshalb kommt zusätzlich das heutige
Volumen dazu: Viele neu gehandelte Calls über dem Kurs heißen meist, dass Kunden kaufen und Händler
sich gegen einen Anstieg absichern müssen (sie kaufen dann den Basiswert nach, wenn der Kurs steigt).

Kennzahlen:
  Call Wall  Strike mit dem größten Call-Gamma über dem Kurs (dort wird oft gebremst)
  Put Wall   Strike mit dem größten Put-Gamma unter dem Kurs
  Flip       Kurs, an dem das Netto-Gamma das Vorzeichen wechselt (darüber dämpfend, darunter verstärkend)

Open Interest meldet die Börse nur einmal am Tag (Stand Vorabend). Die Veränderung über 15 Minuten und
1 Stunde kommt deshalb aus Kurs, IV und Restlaufzeit, nicht aus neuen Kontrakten.
"""
import json
import logging
import math
import re
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from ..cache import markt_lesen, markt_schreiben
from ..konfig import MARKT_DATEN
from .volaflaeche import _handelszeit, _n, _zins

log = logging.getLogger(__name__)

DB = MARKT_DATEN / "gamma.sqlite"
NY = ZoneInfo("America/New_York")
MODI = {"0dte": 0, "woche": 7, "monat": 31, "quartal": 92}  # Verfälle bis so viele Kalendertage
HOECHSTENS_VERFAELLE = 30
FRISCH = 10 * 60              # Sekunden: so lange gilt eine Rechnung (Yahoo-Abrufe schonen)
HINTERGRUND_ALLE = 15 * 60    # während des US-Handels
HINTERGRUND_HOECHSTENS = 4    # SPY, QQQ und zuletzt angesehene Werte
BEOBACHTEN = timedelta(days=1)
STAENDE_TAGE = 5              # so lange bleiben Zwischenstände gespeichert
STAMM = ("SPY", "QQQ")
_sperre = threading.Lock()


def gueltig(ticker: str) -> str | None:
    t = ticker.strip().upper()
    return t if re.fullmatch(r"\^?[A-Z][A-Z0-9\-]{0,9}", t) else None


def _gamma(s, k, t, iv, r):
    """Black-Scholes-Gamma für viele Optionen auf einmal (s darf ein Spaltenvektor sein)."""
    st = iv * np.sqrt(t)
    d1 = (np.log(s / k) + (r + 0.5 * iv ** 2) * t) / st
    return np.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi) / (s * st)


def _iv(preis, f, k, t, r, call):
    """IV per Halbierung, wie in volaflaeche, aber mit eigener Restlaufzeit je Option (Black-76 auf den Terminkurs)."""
    tief, hoch = np.full(len(k), 1e-3), np.full(len(k), 4.0)
    abzinsen = np.exp(-r * t)
    for _ in range(50):
        mitte = (tief + hoch) / 2
        st = mitte * np.sqrt(t)
        d1 = (np.log(f / k) + 0.5 * st ** 2) / st
        d2 = d1 - st
        wert = np.where(call, abzinsen * (f * _n(d1) - k * _n(d2)), abzinsen * (k * _n(-d2) - f * _n(-d1)))
        zu_teuer = wert > preis
        hoch = np.where(zu_teuer, mitte, hoch)
        tief = np.where(zu_teuer, tief, mitte)
    iv = (tief + hoch) / 2
    iv[(iv < 0.02) | (iv > 3.9)] = np.nan
    return iv


def _restlaufzeit(verfall: str, jetzt: datetime) -> float:
    ende = datetime.combine(date.fromisoformat(verfall), datetime.min.time(), NY).replace(hour=16)
    return max((ende - jetzt).total_seconds(), 15 * 60) / (365 * 86400)


def _verfaelle(alle: tuple, modus: str, heute: date) -> list[str]:
    bis = MODI[modus]
    liste = [(v, (date.fromisoformat(v) - heute).days) for v in alle]
    liste = [(v, d) for v, d in liste if d >= 0]
    if not liste:
        return []
    if modus == "0dte":
        return [liste[0][0]]  # nächster Verfall (heute, sonst der nächste Handelstag)
    gewaehlt = [v for v, d in liste if d <= bis] or [liste[0][0]]
    if len(gewaehlt) > HOECHSTENS_VERFAELLE:
        # Viele tägliche Verfälle (SPY, QQQ): die ersten zwei Wochen alle, danach nur Freitage
        gewaehlt = [v for v, d in liste if d <= bis and (d <= 14 or date.fromisoformat(v).weekday() == 4)][:HOECHSTENS_VERFAELLE]
    return gewaehlt


def _kurs(tk) -> float:
    try:
        k = float(tk.fast_info["lastPrice"])
        if k > 0:
            return k
    except Exception:
        pass
    return float(tk.history(period="5d")["Close"].dropna().iloc[-1])


def _optionen(tk, verfaelle: list[str], kurs: float, r: float, jetzt: datetime) -> pd.DataFrame:
    """Alle Optionen der Verfälle mit Strike, Art, OI, Volumen, Restlaufzeit und IV."""
    teile = []
    for v in verfaelle:
        try:
            kette = tk.option_chain(v)
        except Exception as fehler:
            log.info("Optionskette %s %s: %s", tk.ticker, v, fehler)
            continue
        t = _restlaufzeit(v, jetzt)
        for art, tab in (("call", kette.calls), ("put", kette.puts)):
            if tab is None or tab.empty:
                continue
            d = pd.DataFrame({
                "strike": tab["strike"].astype(float),
                "oi": tab["openInterest"].fillna(0).astype(float),
                "vol": tab["volume"].fillna(0).astype(float),
                "bid": tab["bid"].fillna(0).astype(float),
                "ask": tab["ask"].fillna(0).astype(float),
                "iv_yahoo": tab["impliedVolatility"].fillna(0).astype(float),
            })
            d["call"] = art == "call"
            d["t"] = t
            d["verfall"] = v
            teile.append(d)
    if not teile:
        return pd.DataFrame()
    d = pd.concat(teile, ignore_index=True)
    d = d[(d["oi"] > 0) | (d["vol"] > 0)].copy()
    # Eigene IV aus dem Mittelkurs (Yahoo-IV ist oft daneben), sonst die von Yahoo, sonst weglassen
    mid = (d["bid"] + d["ask"]) / 2
    brauchbar = (d["bid"] > 0) & (d["ask"] > d["bid"]) & (mid >= 0.03)
    iv = np.full(len(d), np.nan)
    if brauchbar.any():
        b = brauchbar.to_numpy()
        f = kurs * np.exp(r * d["t"].to_numpy()[b])
        iv[b] = _iv(mid.to_numpy()[b], f, d["strike"].to_numpy()[b], d["t"].to_numpy()[b], r, d["call"].to_numpy()[b])
    yahoo = d["iv_yahoo"].to_numpy()
    ersatz = np.isnan(iv) & (yahoo > 0.03) & (yahoo < 3)
    iv[ersatz] = yahoo[ersatz]
    d["iv"] = iv
    return d[np.isfinite(d["iv"])].reset_index(drop=True)


def _gex(d: pd.DataFrame, s, r: float, spalte: str = "oi"):
    """Dollar-Gamma je 1 % Bewegung je Option (positiv), für Kurs s."""
    return _gamma(s, d["strike"].to_numpy(), d["t"].to_numpy(), d["iv"].to_numpy(), r) * d[spalte].to_numpy() * 100 * s ** 2 * 0.01


def _flip(d: pd.DataFrame, kurs: float, r: float) -> tuple[float | None, list[list[float]]]:
    """Netto-Gamma für gedachte Kurse ±15 %; Flip = Vorzeichenwechsel am nächsten zum Kurs."""
    kurse = np.linspace(kurs * 0.85, kurs * 1.15, 121)
    vorzeichen = np.where(d["call"].to_numpy(), 1.0, -1.0)
    g = _gamma(kurse[:, None], d["strike"].to_numpy()[None, :], d["t"].to_numpy()[None, :], d["iv"].to_numpy()[None, :], r)
    netto = (g * (d["oi"].to_numpy() * vorzeichen)[None, :]).sum(axis=1) * 100 * kurse ** 2 * 0.01
    flip, abstand = None, math.inf
    for i in range(1, len(kurse)):
        if netto[i - 1] == 0 or np.sign(netto[i - 1]) == np.sign(netto[i]):
            continue
        x = kurse[i - 1] + (kurse[i] - kurse[i - 1]) * netto[i - 1] / (netto[i - 1] - netto[i])
        if abs(x - kurs) < abstand:
            flip, abstand = float(x), abs(x - kurs)
    return flip, [[round(float(k), 2), round(float(n))] for k, n in zip(kurse[::2], netto[::2])]


def berechne(ticker: str, modus: str = "monat") -> dict:
    tk = yf.Ticker(ticker)
    alle = tk.options
    if not alle:
        raise LookupError(f"Für {ticker} hat Yahoo keine Optionen.")
    jetzt = datetime.now(timezone.utc)
    heute = jetzt.astimezone(NY).date()
    verfaelle = _verfaelle(alle, modus, heute)
    kurs = _kurs(tk)
    r = _zins()
    d = _optionen(tk, verfaelle, kurs, r, jetzt)
    if d.empty:
        raise LookupError(f"Für {ticker} gibt es gerade keine brauchbaren Optionsdaten.")
    d["gex"] = _gex(d, kurs, r)
    d["gex_vol"] = _gex(d, kurs, r, "vol")
    # Strikes im Bereich ±20 % (Index-ETFs ±10 %), je Strike Calls und Puts zusammengefasst
    breite = 0.10 if ticker in ("SPY", "QQQ", "IWM", "DIA", "^SPX", "^NDX", "^RUT") else 0.20
    im = d[(d["strike"] >= kurs * (1 - breite)) & (d["strike"] <= kurs * (1 + breite))]
    calls = im[im["call"]].groupby("strike").agg(gex=("gex", "sum"), oi=("oi", "sum"), vol=("vol", "sum"), gex_vol=("gex_vol", "sum"))
    puts = im[~im["call"]].groupby("strike").agg(gex=("gex", "sum"), oi=("oi", "sum"), vol=("vol", "sum"), gex_vol=("gex_vol", "sum"))
    strikes = sorted(set(calls.index) | set(puts.index))
    zeilen = []
    for k in strikes:
        c = calls.loc[k] if k in calls.index else None
        p = puts.loc[k] if k in puts.index else None
        zeilen.append({
            "strike": float(k),
            "call": round(float(c["gex"])) if c is not None else 0,
            "put": round(float(p["gex"])) if p is not None else 0,
            "call_oi": int(c["oi"]) if c is not None else 0,
            "put_oi": int(p["oi"]) if p is not None else 0,
            "call_vol": int(c["vol"]) if c is not None else 0,
            "put_vol": int(p["vol"]) if p is not None else 0,
        })
    # Kleine Strikes ohne Gewicht weglassen (unter 0,5 % des größten Werts), damit die Liste lesbar bleibt
    groesst = max([max(z["call"], z["put"]) for z in zeilen] or [0])
    zeilen = [z for z in zeilen if max(z["call"], z["put"]) >= groesst * 0.005]

    call_sum, put_sum = float(d.loc[d["call"], "gex"].sum()), float(d.loc[~d["call"], "gex"].sum())
    ueber = [z for z in zeilen if z["strike"] > kurs]
    unter = [z for z in zeilen if z["strike"] < kurs]
    call_wall = max(ueber, key=lambda z: z["call"])["strike"] if ueber else None
    put_wall = max(unter, key=lambda z: z["put"])["strike"] if unter else None
    flip, profil = _flip(d, kurs, r)
    # Heutiges Volumen über bzw. unter dem Kurs: wo wird gerade neu gehandelt?
    c_ueber = d[d["call"] & (d["strike"] > kurs)]
    p_unter = d[~d["call"] & (d["strike"] < kurs)]
    heute_werte = {
        "call_vol_ueber": int(c_ueber["vol"].sum()),
        "call_oi_ueber": int(c_ueber["oi"].sum()),
        "put_vol_unter": int(p_unter["vol"].sum()),
        "put_oi_unter": int(p_unter["oi"].sum()),
        "call_gex_vol_ueber": round(float(c_ueber["gex_vol"].sum())),
        "put_gex_vol_unter": round(float(p_unter["gex_vol"].sum())),
    }
    return {
        "ticker": ticker,
        "modus": modus,
        "kurs": round(kurs, 2),
        "zeit": jetzt.isoformat(timespec="seconds"),
        "live": bool(_handelszeit()),
        "verfaelle": verfaelle,
        "optionen": int(len(d)),
        "call_gex": round(call_sum),
        "put_gex": round(put_sum),
        "netto": round(call_sum - put_sum),
        "call_wall": call_wall,
        "put_wall": put_wall,
        "flip": round(flip, 2) if flip else None,
        "profil": profil,
        "heute": heute_werte,
        "zeilen": zeilen,
    }


# ---- Zwischenstände (für die Veränderung über 15 Minuten und 1 Stunde) ----

def _db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS staende (ticker TEXT, modus TEXT, zeit TEXT, daten TEXT, PRIMARY KEY (ticker, modus, zeit))")
    db.execute("CREATE TABLE IF NOT EXISTS beobachtet (ticker TEXT PRIMARY KEY, zuletzt TEXT)")
    return db


def _stand_speichern(g: dict) -> None:
    knapp = {"kurs": g["kurs"], "call_gex": g["call_gex"], "put_gex": g["put_gex"],
             "zeilen": {str(z["strike"]): [z["call"], z["put"]] for z in g["zeilen"]}}
    db = _db()
    try:
        with db:
            db.execute("INSERT OR REPLACE INTO staende VALUES (?, ?, ?, ?)", (g["ticker"], g["modus"], g["zeit"], json.dumps(knapp)))
            grenze = (datetime.now(timezone.utc) - timedelta(days=STAENDE_TAGE)).isoformat(timespec="seconds")
            db.execute("DELETE FROM staende WHERE zeit < ?", (grenze,))
    finally:
        db.close()


def _stand_vor(ticker: str, modus: str, zeit: str, minuten: int) -> dict | None:
    """Gespeicherter Stand, der etwa so alt ist (zwischen 0,6- und 2-fachem Abstand, am nächsten dran)."""
    jetzt = datetime.fromisoformat(zeit)
    von = (jetzt - timedelta(minutes=minuten * 2)).isoformat(timespec="seconds")
    bis = (jetzt - timedelta(minutes=minuten * 0.6)).isoformat(timespec="seconds")
    ziel = jetzt - timedelta(minutes=minuten)
    db = _db()
    try:
        reihen = db.execute("SELECT zeit, daten FROM staende WHERE ticker=? AND modus=? AND zeit BETWEEN ? AND ?", (ticker, modus, von, bis)).fetchall()
    finally:
        db.close()
    if not reihen:
        return None
    z, daten = min(reihen, key=lambda x: abs((datetime.fromisoformat(x[0]) - ziel).total_seconds()))
    return {"zeit": z, **json.loads(daten)}


def _merken(ticker: str) -> None:
    db = _db()
    try:
        with db:
            db.execute("INSERT OR REPLACE INTO beobachtet VALUES (?, ?)", (ticker, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    finally:
        db.close()


def _beobachtet() -> list[str]:
    grenze = (datetime.now(timezone.utc) - BEOBACHTEN).isoformat(timespec="seconds")
    db = _db()
    try:
        return [r[0] for r in db.execute("SELECT ticker FROM beobachtet WHERE zuletzt >= ? ORDER BY zuletzt DESC", (grenze,))]
    finally:
        db.close()


def _holen(ticker: str, modus: str) -> dict:
    schluessel = f"gamma:{ticker}:{modus}"
    alt, alter = markt_lesen(schluessel)
    if alt and alter < FRISCH:
        return alt
    with _sperre:
        alt, alter = markt_lesen(schluessel)
        if alt and alter < FRISCH:
            return alt
        neu = berechne(ticker, modus)
        markt_schreiben(schluessel, neu)
        if neu["live"]:
            _stand_speichern(neu)
        return neu


def gamma_exposure(ticker: str, modus: str = "monat") -> dict:
    """Antwort für die Seite: Rechnung plus Veränderung gegenüber vor 15 Minuten und vor 1 Stunde."""
    t = gueltig(ticker)
    if not t:
        return {"fehler": "Bitte einen US-Ticker eingeben, z. B. SPY oder NVDA."}
    if modus not in MODI:
        modus = "monat"
    try:
        g = dict(_holen(t, modus))
    except LookupError as fehler:
        return {"fehler": str(fehler), "ticker": t}
    _merken(t)
    for name, minuten in (("vor15", 15), ("vor60", 60)):
        s = _stand_vor(t, modus, g["zeit"], minuten)
        g[name] = None if not s else {"zeit": s["zeit"], "kurs": s["kurs"], "call_gex": s["call_gex"], "put_gex": s["put_gex"],
                                      "zeilen": {k: v for k, v in s["zeilen"].items()}}
    return g


def _hintergrund():
    """Während des US-Handels alle 15 Minuten SPY, QQQ und zuletzt angesehene Werte rechnen (Modus Monat),
    damit es die Veränderung über 15 Minuten und 1 Stunde gibt, auch wenn die Seite nicht offen ist."""
    while True:
        try:
            if _handelszeit():
                werte = list(dict.fromkeys([*STAMM, *_beobachtet()]))[:HINTERGRUND_HOECHSTENS]
                for t in werte:
                    try:
                        _holen(t, "monat")
                    except Exception as fehler:
                        log.warning("Gamma-Exposure %s: %s", t, fehler)
        except Exception as fehler:
            log.warning("Gamma-Exposure Hintergrund: %s", fehler)
        time.sleep(HINTERGRUND_ALLE)


def starte_hintergrund() -> None:
    threading.Thread(target=_hintergrund, name="gamma", daemon=True).start()
