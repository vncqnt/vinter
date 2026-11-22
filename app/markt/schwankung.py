"""Schwankung eines Werts: realisierte Volatilität, Vola-Kegel, ATR und (bei US-Werten) IV gegen realisierte Vola.

Realisierte Volatilität (annualisiert, Wurzel aus 252 Handelstagen):
- Schluss zu Schluss: Standardabweichung der logarithmischen Tagesrenditen.
- Parkinson: nutzt Hoch und Tief jedes Tages, sieht also auch Ausschläge innerhalb des Tages; rund 5-mal
  effizienter, unterschätzt aber Kurslücken über Nacht.
- Yang-Zhang: verbindet Nacht (Eröffnung gegen Vortagesschluss), Tagesverlauf (Schluss gegen Eröffnung)
  und Hoch/Tief (Rogers-Satchell). Gilt als der beste einfache Schätzer, auch bei Kurslücken.
Vola-Kegel: Für jedes Fenster (10 bis 252 Tage) die Spanne aller rollierenden Werte der letzten 3 Jahre
(Minimum, 25 %, Median, 75 %, Maximum) und wo der heutige Wert darin liegt.
IV: ATM-IV 30 Tage aus den gespeicherten Volatilitätsflächen (app/markt/volaflaeche.py). Für SPY, QQQ,
IWM und DIA gibt es offizielle Indizes mit langer Historie (VIX, VXN, RVX, VXD), die dort als IV dienen.
"""
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from ..cache import STUNDE, markt_lesen, markt_schreiben

JAHR = 252
FENSTER = [10, 20, 30, 60, 90, 120, 252]
IV_INDEX = {"SPY": ("^VIX", "VIX"), "QQQ": ("^VXN", "VXN"), "IWM": ("^RVX", "RVX"), "DIA": ("^VXD", "VXD")}
MIN_IV_TAGE = 20  # eigene IV-Historie: ab so vielen Tagen IV-Rang und -Perzentil


# ---- Reine Rechnungen (getestet in tests/test_schwankung.py) ----

def rv_schluss(schluss: pd.Series, n: int) -> pd.Series:
    r = np.log(schluss).diff()
    return r.rolling(n).std() * math.sqrt(JAHR) * 100


def rv_parkinson(hoch: pd.Series, tief: pd.Series, n: int) -> pd.Series:
    x = np.log(hoch / tief) ** 2
    return np.sqrt(x.rolling(n).mean() / (4 * math.log(2))) * math.sqrt(JAHR) * 100


def rv_yang_zhang(o: pd.Series, h: pd.Series, l: pd.Series, c: pd.Series, n: int) -> pd.Series:
    nacht = np.log(o / c.shift(1))
    tag = np.log(c / o)
    rs = np.log(h / c) * np.log(h / o) + np.log(l / c) * np.log(l / o)
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    var = nacht.rolling(n).var() + k * tag.rolling(n).var() + (1 - k) * rs.rolling(n).mean()
    return np.sqrt(var) * math.sqrt(JAHR) * 100


def atr(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14) -> pd.Series:
    wahr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    return wahr.ewm(alpha=1 / n, adjust=False).mean()  # Wilder-Glättung


def kegel(schluss: pd.Series) -> list[dict]:
    aus = []
    for n in FENSTER:
        reihe = rv_schluss(schluss, n).dropna()
        if len(reihe) < 60:
            continue
        jetzt = float(reihe.iloc[-1])
        q = reihe.quantile([0, .25, .5, .75, 1]).tolist()
        aus.append({"fenster": n, "min": q[0], "q25": q[1], "median": q[2], "q75": q[3], "max": q[4], "jetzt": jetzt,
                    "perzentil": float((reihe <= jetzt).mean() * 100)})
    return aus


def rang(werte: list[float], jetzt: float) -> dict:
    """IV-Rang (wo zwischen Tief und Hoch) und IV-Perzentil (an wie viel % der Tage niedriger)."""
    lo, hi = min(werte), max(werte)
    return {"rang": (jetzt - lo) / (hi - lo) * 100 if hi > lo else 50.0,
            "perzentil": sum(w < jetzt for w in werte) / len(werte) * 100, "tief": lo, "hoch": hi, "tage": len(werte)}


def crush(iv: dict[str, float], earnings: list[str]) -> list[dict]:
    """IV am letzten gespeicherten Tag vor dem Termin gegen den ersten danach (Termin-Tag zählt als 'danach',
    wenn die Zahlen vor der Börse kamen, ist das nicht unterscheidbar: deshalb Tag vor und Tag nach)."""
    tage = sorted(iv)
    aus = []
    for e in earnings:
        vor = [t for t in tage if t < e and (date.fromisoformat(e) - date.fromisoformat(t)).days <= 4]
        nach = [t for t in tage if t > e and (date.fromisoformat(t) - date.fromisoformat(e)).days <= 4]
        if vor and nach:
            a, b = iv[vor[-1]], iv[nach[0]]
            aus.append({"termin": e, "vor": a, "nach": b, "veraenderung": (b / a - 1) * 100 if a else None})
    return aus


# ---- Daten ----

def _kerzen(ticker: str) -> pd.DataFrame | None:
    schluessel = f"schwankung-kerzen:{ticker}"
    alt, alter = markt_lesen(schluessel)
    if alt and alter < 6 * STUNDE:
        t = pd.DataFrame(alt["werte"], columns=alt["spalten"], index=pd.to_datetime(alt["index"]))
        return t
    try:
        t = yf.Ticker(ticker).history(period="4y", auto_adjust=True)[["Open", "High", "Low", "Close"]].dropna()
        waehrung = yf.Ticker(ticker).history_metadata.get("currency")
    except Exception:
        return None
    if t.empty:
        return None
    t.index = pd.DatetimeIndex(t.index).tz_localize(None).normalize()
    t = t[~t.index.duplicated(keep="last")]
    t = t[(t["Open"] > 0) & (t["High"] >= t["Low"])]
    t.attrs["waehrung"] = waehrung
    markt_schreiben(schluessel, {"spalten": list(t.columns), "index": [d.date().isoformat() for d in t.index],
                                 "werte": t.values.tolist(), "waehrung": waehrung})
    return t


def _waehrung(ticker: str) -> str | None:
    alt, _ = markt_lesen(f"schwankung-kerzen:{ticker}")
    return (alt or {}).get("waehrung")


def iv_verlauf(ticker: str) -> tuple[dict[str, float], dict[str, float], str]:
    """ATM-IV 30 Tage und 7 Tage je Tag. Für Indizes-ETFs der offizielle Index (1 Jahr), sonst die eigene Sammlung."""
    if ticker in IV_INDEX:
        symbol, name = IV_INDEX[ticker]
        alt, alter = markt_lesen(f"schwankung-iv:{symbol}")
        if not (alt and alter < 6 * STUNDE):
            try:
                s = yf.Ticker(symbol).history(period="2y")["Close"].dropna()
                alt = {pd.Timestamp(d).date().isoformat(): float(v) for d, v in s.items()}
                markt_schreiben(f"schwankung-iv:{symbol}", alt)
            except Exception:
                alt = alt or {}
        if alt:
            return alt, {}, name
    from .volaflaeche import MONEYNESS, TAGE, _alle
    i7, i30, mitte = TAGE.index(7), TAGE.index(30), MONEYNESS.index(100)
    iv30, iv7 = {}, {}
    for f in _alle(ticker):
        try:
            iv30[f["datum"]] = float(f["iv"][i30][mitte])
            iv7[f["datum"]] = float(f["iv"][i7][mitte])
        except (KeyError, IndexError, TypeError):
            continue
    return iv30, iv7, "gespeicherte Flächen"


def analyse(ticker: str, mit_iv: bool = True) -> dict:
    ticker = ticker.strip().upper()
    k = _kerzen(ticker)
    if k is None or len(k) < 80:
        return {"fehler": f"Für {ticker} gibt es nicht genug Tageskurse bei Yahoo."}
    o, h, l, c = k["Open"], k["High"], k["Low"], k["Close"]
    letzte = lambda s: None if s.dropna().empty or pd.isna(s.iloc[-1]) else float(s.iloc[-1])
    # Ohne echte Eröffnungskurse (Open = Vortagesschluss bei manchen Werten) sind Parkinson und Yang-Zhang wertlos
    echte_eroeffnung = float((o.iloc[-250:] != c.shift(1).iloc[-250:]).mean()) > 0.5 and float((h != l).iloc[-250:].mean()) > 0.8
    rv = {n: letzte(rv_schluss(c, n)) for n in (10, 20, 60, 252)}
    a = atr(h, l, c)
    verlauf_rv = rv_schluss(c, 20).iloc[-JAHR:]
    ergebnis = {
        "ticker": ticker, "waehrung": _waehrung(ticker), "stand": k.index[-1].date().isoformat(), "kurs": float(c.iloc[-1]),
        "rv": rv,
        "parkinson20": letzte(rv_parkinson(h, l, 20)) if echte_eroeffnung else None,
        "yang_zhang20": letzte(rv_yang_zhang(o, h, l, c, 20)) if echte_eroeffnung else None,
        "hoch_tief": echte_eroeffnung,
        "kegel": kegel(c.iloc[-3 * JAHR:]),
        "atr": letzte(a), "atr_prozent": letzte(a / c * 100),
        "verlauf": {"daten": [d.date().isoformat() for d in verlauf_rv.index], "rv20": [None if pd.isna(v) else round(float(v), 2) for v in verlauf_rv]},
    }
    if not mit_iv or "." in ticker:
        return ergebnis
    iv30, iv7, quelle = iv_verlauf(ticker)
    if iv30:
        tage = sorted(iv30)
        jetzt = iv30[tage[-1]]
        jahr = [iv30[t] for t in tage if t >= (date.today() - timedelta(days=365)).isoformat()]
        ergebnis["iv"] = {
            "quelle": quelle, "stand": tage[-1], "atm30": jetzt,
            "gegen_rv20": jetzt - rv[20] if rv[20] else None, "verhaeltnis": jetzt / rv[20] if rv[20] else None,
            "rang": rang(jahr, jetzt) if len(jahr) >= MIN_IV_TAGE else None, "tage": len(jahr), "min_tage": MIN_IV_TAGE,
        }
        ergebnis["verlauf"]["iv"] = [iv30.get(d) for d in ergebnis["verlauf"]["daten"]]
        if iv7:
            try:
                from .volaflaeche import _earnings_daten
                ergebnis["crush"] = crush(iv7, _earnings_daten(ticker))
            except Exception:
                ergebnis["crush"] = []
    return ergebnis
