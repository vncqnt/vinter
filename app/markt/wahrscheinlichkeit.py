"""Optionsimplizite Wahrscheinlichkeiten (Roadmap Q2): Welche Kursverteilung preist der Optionsmarkt ein?

Methode (Breeden-Litzenberger): Für jeden Verfall wird die geglättete IV-Kurve der Volatilitätsfläche genommen
(Polynom über der log-Moneyness, aus Geld/Brief-Mitten aus dem Geld liegender Optionen, Terminkurs aus der
Put-Call-Parität, siehe volaflaeche._kurve). Daraus werden Call-Preise auf einem feinen Strike-Gitter gerechnet.
Die Ableitung nach dem Strike ergibt die Wahrscheinlichkeit, dass der Kurs am Verfallstag über dem Strike liegt:
P(S > K) = −e^(rT) · ∂C/∂K. Außerhalb der gehandelten Strikes bleibt die IV am Rand konstant (die Ränder der
Verteilung sind deshalb unsicherer).
Wichtig: risikoneutrale Wahrscheinlichkeit. Sie enthält die Prämie, die Anleger für Absicherung zahlen
(Abwärtsrisiken wirken größer, Aufwärtschancen kleiner als in der Wirklichkeit). Keine Prognose.
"""
import math
from datetime import date

import numpy as np
import yfinance as yf

from ..cache import STUNDE, markt_lesen, markt_schreiben
from .volaflaeche import _kurve, _preis, _zins

ZIELE = [7, 30, 60, 91, 182, 273, 365, 730]   # gewünschte Laufzeiten (Tage); je Ziel der nächste Verfall
GITTER_PUNKTE = 600
AUSLAUF = 0.1   # log-Moneyness, über die eine nach außen fallende IV-Kurve flach ausläuft


def ueber(f: float, t: float, r: float, iv_von_k, gitter: np.ndarray) -> np.ndarray:
    """P(S_T > K) je Strike im Gitter aus Call-Preisen mit Smile. Reine Funktion (getestet)."""
    sigma = np.array([iv_von_k(k) for k in gitter])
    c = _preis(f, gitter, t, r, sigma, True)
    p = -math.exp(r * t) * np.gradient(c, gitter)
    p = np.clip(p, 0.0, 1.0)
    return np.minimum.accumulate(p)  # darf mit dem Strike nur fallen


def quantile(gitter: np.ndarray, p_ueber: np.ndarray, stufen=(0.05, 0.25, 0.5, 0.75, 0.95)) -> dict:
    """Kurs, unter dem der Kurs mit Wahrscheinlichkeit q liegt (aus P(S > K) = 1 − q)."""
    unter = 1 - p_ueber
    return {f"q{int(q * 100)}": float(np.interp(q, unter, gitter)) for q in stufen}


def _iv_funktion(kurve: dict):
    """IV je Strike: im gehandelten Bereich die geglättete Kurve, außerhalb knickfrei mit der Randsteigung
    fortgesetzt (sonst entsteht am Rand ein Sprung in der Wahrscheinlichkeit). Gedeckelt nach Lee: die
    Gesamtvarianz IV² · T wächst in den Flügeln höchstens mit Steigung 2 in der log-Moneyness."""
    koeff, von, bis, t = kurve["koeff"], kurve["von"], kurve["bis"], kurve["t"]
    abl = np.polyder(koeff)

    def iv(k):
        lm = math.log(k / kurve["f"])
        if von <= lm <= bis:
            return max(0.01, float(np.polyval(koeff, lm)))
        rand = von if lm < von else bis
        s0, steigung = float(np.polyval(koeff, rand)), float(np.polyval(abl, rand))
        abstand = lm - rand
        if steigung * abstand < 0:
            # IV würde nach außen fallen: sanft auf einen festen Wert auslaufen lassen (knickfrei, kein Absturz)
            wert = s0 + steigung * AUSLAUF * (1 - math.exp(-abstand / AUSLAUF)) if abstand > 0 else s0 - steigung * AUSLAUF * (1 - math.exp(abstand / AUSLAUF))
        else:
            wert = s0 + steigung * abstand
        grenze = math.sqrt(max(s0, 0.01) ** 2 + 2 * abs(lm - rand) / t)
        return float(min(max(wert, 0.01), grenze))
    return iv


def verteilung(ticker: str) -> dict:
    ticker = ticker.strip().upper()
    if "." in ticker:
        return {"fehler": f"{ticker}: Optionen gibt es kostenlos nur für US-Werte."}
    schluessel = f"wahrscheinlichkeit:{ticker}"
    alt, alter = markt_lesen(schluessel)
    if alt and alter < STUNDE:
        return alt
    t = yf.Ticker(ticker)
    try:
        verfaelle = list(t.options or [])
        kurs = float(t.history(period="5d")["Close"].dropna().iloc[-1])
        waehrung = t.history_metadata.get("currency")
    except Exception:
        return alt or {"fehler": "Die Optionsdaten konnten gerade nicht geladen werden."}
    if not verfaelle:
        return {"fehler": f"Für {ticker} gibt es bei Yahoo keine Optionen."}
    heute = date.today()
    tage_je = {v: (date.fromisoformat(v) - heute).days for v in verfaelle}
    gewaehlt = []
    for ziel in ZIELE:
        kandidaten = [v for v in verfaelle if tage_je[v] >= 5 and v not in gewaehlt]
        if not kandidaten:
            break
        v = min(kandidaten, key=lambda x: abs(tage_je[x] - ziel))
        if abs(tage_je[v] - ziel) <= max(10, ziel * 0.35):
            gewaehlt.append(v)
    r = _zins()
    aus = []
    for v in sorted(gewaehlt):
        try:
            jahre = max(tage_je[v], 1) / 365
            kurve = _kurve(t.option_chain(v), kurs, jahre, r)
        except Exception:
            kurve = None
        if not kurve:
            continue
        f = kurve["f"]
        gitter = np.geomspace(f * 0.25, f * 3.0, GITTER_PUNKTE)
        p = ueber(f, jahre, r, _iv_funktion(kurve), gitter)
        # Für den Browser ausdünnen: nur der Bereich, in dem sich etwas tut (0,1 % bis 99,9 %)
        lebendig = np.where((p > 0.001) & (p < 0.999))[0]
        a, b = (max(0, lebendig[0] - 3), min(len(p), lebendig[-1] + 4)) if len(lebendig) else (0, len(p))
        schritt = max(1, (b - a) // 160)
        iv_am_geld = _iv_funktion(kurve)(f) * 100
        aus.append({"verfall": v, "tage": tage_je[v], "termin": round(f, 4), "iv": round(iv_am_geld, 2),
                    "punkte": kurve["punkte"], "von_strike": round(f * math.exp(kurve["von"]), 2), "bis_strike": round(f * math.exp(kurve["bis"]), 2),
                    "gitter": [round(float(x), 4) for x in gitter[a:b:schritt]], "ueber": [round(float(x), 5) for x in p[a:b:schritt]],
                    "ueber_kurs": round(float(np.interp(kurs, gitter, p)), 4),
                    **{k: round(w, 2) for k, w in quantile(gitter, p).items()}})
    if not aus:
        return alt or {"fehler": f"Für {ticker} sind die Optionen zu dünn gehandelt für eine Verteilung."}
    ergebnis = {"ticker": ticker, "kurs": kurs, "waehrung": waehrung, "zins": round(r * 100, 2), "verfaelle": aus}
    markt_schreiben(schluessel, ergebnis)
    return ergebnis


def p_bei(v: dict, level: float) -> float:
    """P(Kurs am Verfall > level) aus dem ausgedünnten Gitter eines Verfalls."""
    g, p = v["gitter"], v["ueber"]
    if level <= g[0]:
        return p[0]
    if level >= g[-1]:
        return p[-1]
    return float(np.interp(level, g, p))
