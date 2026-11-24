"""Szenario-Rechner: eigene Szenarien (Wahrscheinlichkeit × Kursbewegung) als Verteilung, gegen den Optionsmarkt.

Modell (Mischverteilung in log-Renditen bis zum Stichtag):
  jedes Szenario i mit Gewicht p_i ist eine Normalverteilung mit Mitte ln(1 + Bewegung_i) und Streuung
  sqrt(Unsicherheit_i² + σ² · T). σ ist die normale Schwankung der Aktie ohne das Ereignis (realisierte Vola
  robust aus 120 Tagen ohne Sprungtage, annualisiert), T die Zeit bis zum Stichtag in Jahren. Zusammen ergibt das bei Ja/Nein-Ereignissen
  eine zweigipflige Verteilung.
Kennzahlen: Erwartungswert E[e^X] − 1, erwartete Ausschlagsgröße E|e^X − 1|, Streuung, Schiefe, Wahrscheinlichkeit
  eines Verlusts, VaR 5 % und Expected Shortfall 5 % (Schnitt der schlechtesten 5 %).
Markt (nur US-Werte mit Optionen): risikoneutrale Verteilung des ersten Verfalls am oder nach dem Stichtag
  (Breeden-Litzenberger wie in wahrscheinlichkeit.py). Eingepreiste Wahrscheinlichkeit: das gewählte "große"
  Szenario bekommt das Gewicht p, die anderen behalten ihr Verhältnis; p (0–80 %) und die Grundschwankung
  (0,5- bis 1,6-fach; Optionen sind in unruhigen Zeiten teurer) werden so gewählt, dass die Verteilungsfunktion des Modells der des Markts am nächsten kommt.
  Bereich = alle p, deren Fehler höchstens 50 % über dem besten liegt. Dazu: Hat die Marktverteilung selbst zwei
  Gipfel?
Simulation: 3.000 Pfade mit täglicher Schwankung, am Stichtag ein Sprung nach den Szenarien, danach 10 weitere
  Handelstage; Wahrscheinlichkeit, Kurs-Level vorher zu berühren.
Alles risikoneutral beim Markt (mit Absicherungsprämie), deine Szenarien sind deine Sicht. Keine Empfehlung.
"""
import math
from datetime import date

import numpy as np

GITTER = np.linspace(-1.2, 0.9, 700)  # log-Renditen (−70 % bis +146 %)
PFADE = 3000
NACHLAUF = 10


def komponenten(szenarien: list[dict], sigma: float, jahre: float) -> list[tuple[float, float, float]]:
    """(Gewicht, Mitte, Streuung) je Szenario in log-Renditen. Gewichte werden auf 1 normiert."""
    summe = sum(max(0.0, s["p"]) for s in szenarien) or 1.0
    basis = sigma * math.sqrt(max(jahre, 1 / 365))
    aus = []
    for s in szenarien:
        bew = max(-0.95, s["bewegung"])
        unsicher = max(0.0, s.get("unsicherheit") or 0.0)
        # Unsicherheit in Prozentpunkten der Bewegung → in log-Renditen um die Mitte herum
        u = math.log(1 + min(0.9, unsicher)) if unsicher else 0.0
        # Mindestbreite 0,6 %: schmaler als das Rechengitter würde die Kennzahlen verfälschen
        aus.append((max(0.0, s["p"]) / summe, math.log1p(bew), max(0.006, math.sqrt(u * u + basis * basis))))
    return aus


def dichte(komp, x: np.ndarray = GITTER) -> np.ndarray:
    d = np.zeros_like(x)
    for w, m, s in komp:
        d += w * np.exp(-0.5 * ((x - m) / s) ** 2) / (s * math.sqrt(2 * math.pi))
    return d


def verteilungsfunktion(komp, x: np.ndarray) -> np.ndarray:
    from scipy.stats import norm
    return sum(w * norm.cdf((x - m) / s) for w, m, s in komp)


def kennzahlen(komp) -> dict:
    """Kennzahlen in Prozent (einfache Rendite) aus der Mischverteilung. Reine Funktion."""
    x = GITTER
    d = dichte(komp, x)
    gew = d / d.sum()
    r = np.expm1(x) * 100
    mittel = float(sum(w * math.exp(m + s * s / 2) for w, m, s in komp) - 1) * 100
    streu = float(math.sqrt(max(0.0, np.sum(gew * (r - mittel) ** 2))))
    schiefe = float(np.sum(gew * ((r - mittel) / (streu or 1)) ** 3))
    cdf = np.cumsum(gew)
    var5 = float(np.interp(0.05, cdf, r))
    unten = r <= var5
    es5 = float(np.sum(gew[unten] * r[unten]) / max(gew[unten].sum(), 1e-12))
    return {"mittel": round(mittel, 2), "ausschlag": round(float(np.sum(gew * np.abs(r))), 2), "streuung": round(streu, 2),
            "schiefe": round(schiefe, 2), "p_verlust": round(float(gew[r < 0].sum() * 100), 1),
            "var5": round(var5, 2), "es5": round(es5, 2), "median": round(float(np.interp(0.5, cdf, r)), 2)}


def mit_gewicht(szenarien: list[dict], index: int, p: float) -> list[dict]:
    """Szenario 'index' bekommt Gewicht p, die anderen teilen sich 1 − p im alten Verhältnis."""
    rest = sum(s["p"] for i, s in enumerate(szenarien) if i != index) or 1.0
    return [{**s, "p": p if i == index else (1 - p) * s["p"] / rest} for i, s in enumerate(szenarien)]


def markt_anpassen(szenarien: list[dict], index: int, sigma: float, jahre: float, x_markt: np.ndarray, cdf_markt: np.ndarray) -> dict:
    """Gewicht p des Szenarios 'index' (und Grundschwankung), das die Marktverteilung am besten trifft."""
    lebendig = (cdf_markt > 0.02) & (cdf_markt < 0.98)
    x, f = x_markt[lebendig], cdf_markt[lebendig]
    if len(x) < 10:
        return {}
    p_werte = np.round(np.arange(0.0, 0.801, 0.01), 2)
    faktoren = np.linspace(0.5, 1.6, 23)
    fehler = np.full((len(p_werte), len(faktoren)), np.inf)
    for i, p in enumerate(p_werte):
        sz = mit_gewicht(szenarien, index, p)
        for j, fk in enumerate(faktoren):
            fehler[i, j] = float(np.mean((verteilungsfunktion(komponenten(sz, sigma * fk, jahre), x) - f) ** 2))
    bester = np.unravel_index(np.argmin(fehler), fehler.shape)
    je_p = fehler.min(axis=1)
    gut = p_werte[je_p <= je_p.min() * 1.5 + 1e-7]
    return {"p": float(p_werte[bester[0]]), "von": float(gut.min()), "bis": float(gut.max()),
            "sigma_faktor": round(float(faktoren[bester[1]]), 2), "fehler": float(fehler[bester]),
            "kurve": [round(float(e), 6) for e in je_p]}


def zwei_gipfel(x: np.ndarray, d: np.ndarray) -> bool:
    """Hat eine Dichte zwei deutliche Gipfel (zweiter ≥ 15 % des höchsten, Tal dazwischen ≤ 80 % des kleineren)?"""
    if len(d) < 7:
        return False
    g = np.convolve(d, np.ones(5) / 5, mode="same")
    spitzen = [i for i in range(2, len(g) - 2) if g[i] >= g[i - 1] and g[i] >= g[i + 1] and g[i] >= 0.15 * g.max()]
    for a, b in zip(spitzen, spitzen[1:]):
        if b - a > 3 and g[a:b + 1].min() <= 0.8 * min(g[a], g[b]):
            return True
    return False


def heatmap(szenarien: list[dict], index: int, sigma: float, jahre: float) -> dict:
    """Erwartungswert (%) für p = 0…50 % und Bewegung des Szenarios 'index' zwischen 0,25- und 2-fach."""
    eigen = szenarien[index]["bewegung"]
    ps = [round(x, 2) for x in np.arange(0.0, 0.501, 0.05)]
    faktoren = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
    bewegungen = [max(-0.95, eigen * f) for f in faktoren]
    werte = [[kennzahlen(komponenten(mit_gewicht([*szenarien[:index], {**szenarien[index], "bewegung": bw}, *szenarien[index + 1:]], index, p), sigma, jahre))["mittel"]
              for p in ps] for bw in bewegungen]
    return {"p": ps, "bewegung": [round(b * 100, 2) for b in bewegungen], "werte": werte}


def simulation(szenarien: list[dict], sigma: float, tage: int, level: list[float] | None = None, seed: int = 11) -> dict:
    """Pfade in Prozent ab heute: tägliche Schwankung, am Stichtag ein Sprung, danach NACHLAUF Handelstage."""
    rng = np.random.default_rng(seed)
    tage = max(1, int(tage))
    n = tage + NACHLAUF
    taeglich = rng.normal(-0.5 * sigma ** 2 / 252, sigma / math.sqrt(252), size=(PFADE, n))
    w = np.array([max(0.0, s["p"]) for s in szenarien], float)
    w = w / (w.sum() or 1)
    wahl = rng.choice(len(szenarien), size=PFADE, p=w)
    mitte = np.array([math.log1p(max(-0.95, s["bewegung"])) for s in szenarien])
    unsicher = np.array([math.log1p(min(0.9, max(0.0, s.get("unsicherheit") or 0.0))) for s in szenarien])
    taeglich[:, tage - 1] += mitte[wahl] + rng.normal(0, 1, PFADE) * unsicher[wahl]
    pfad = np.concatenate([np.zeros((PFADE, 1)), np.cumsum(taeglich, axis=1)], axis=1)
    prozent = np.expm1(pfad) * 100
    stufen = {f"p{q}": [round(float(v), 2) for v in np.percentile(prozent, q, axis=0)] for q in (5, 25, 50, 75, 95)}
    reihenfolge = np.argsort(prozent[:, -1])
    beispiele = [[round(float(v), 2) for v in prozent[i]] for i in reihenfolge[np.linspace(0, PFADE - 1, 40).astype(int)]]
    beruehren = []
    for l in level or []:
        ziel = l * 100
        if ziel >= 0:
            beruehren.append(round(float((prozent.max(axis=1) >= ziel).mean() * 100), 1))
        else:
            beruehren.append(round(float((prozent.min(axis=1) <= ziel).mean() * 100), 1))
    return {"schritte": list(range(0, n + 1)), "stichtag": tage, **stufen, "beispiele": beispiele,
            "ende": [round(float(v), 2) for v in prozent[rng.choice(PFADE, 1200, replace=False), -1]], "beruehren": beruehren}


def handelstage_bis(datum: str) -> int:
    ziel = date.fromisoformat(datum)
    return max(1, int(np.busday_count(date.today(), ziel)))


def markt_verteilung(ticker: str, datum: str) -> dict:
    """Optionsimplizite Verteilung des ersten Verfalls am oder nach dem Stichtag (1 Stunde Cache)."""
    import yfinance as yf

    from ..cache import STUNDE, markt_lesen, markt_schreiben
    from .volaflaeche import _kurve, _zins
    from .wahrscheinlichkeit import _iv_funktion, ueber
    ticker = ticker.strip().upper()
    if "." in ticker or "-" in ticker or "=" in ticker or ticker.startswith("^"):
        return {"fehler": "Optionen gibt es kostenlos nur für US-Aktien und -ETFs."}
    schluessel = f"szenario:markt:{ticker}:{datum}"
    alt, alter = markt_lesen(schluessel)
    if alt and alter < STUNDE:
        return alt
    t = yf.Ticker(ticker)
    verfaelle = list(t.options or [])
    if not verfaelle:
        return {"fehler": f"Für {ticker} gibt es bei Yahoo keine Optionen."}
    # Erster brauchbarer Verfall ab dem Stichtag; Yahoo liefert für manche Verfälle unbrauchbare Kurse,
    # dann der nächste (bis zu 5 später), notfalls der letzte davor
    kandidaten = [v for v in verfaelle if v >= datum][:5] + [v for v in reversed(verfaelle) if v < datum][:2]
    kurs = float(t.history(period="5d")["Close"].dropna().iloc[-1])
    kurve, v = None, None
    for v in kandidaten:
        jahre = max((date.fromisoformat(v) - date.today()).days, 1) / 365
        try:
            kurve = _kurve(t.option_chain(v), kurs, jahre, _zins())
        except Exception:
            kurve = None
        if kurve:
            break
    if not kurve:
        return {"fehler": "Die Optionen um den Stichtag sind zu dünn gehandelt."}
    gitter = np.geomspace(kurve["f"] * 0.25, kurve["f"] * 3.0, 600)
    p = ueber(kurve["f"], jahre, _zins(), _iv_funktion(kurve), gitter)
    x = np.log(gitter / kurs)
    cdf = 1 - p
    d = np.gradient(cdf, x)
    aus = {"verfall": v, "tage": (date.fromisoformat(v) - date.today()).days, "kurs": kurs, "iv": round(_iv_funktion(kurve)(kurve["f"]) * 100, 1),
           "x": [round(float(a), 5) for a in x], "cdf": [round(float(a), 5) for a in cdf], "dichte": [round(float(a), 5) for a in np.maximum(d, 0)],
           "abweichung_tage": (date.fromisoformat(v) - date.fromisoformat(datum)).days}
    markt_schreiben(schluessel, aus)
    return aus


def realisierte_vola(ticker: str) -> float:
    """Normale Schwankung ohne Sprungtage: 1,4826 × mittlere absolute Abweichung der letzten 120 Tagesrenditen
    (ein einzelner Tag mit −27 % würde die normale Standardabweichung stark aufblähen und das Ereignis doppelt zählen)."""
    from .abruf import schlusskurse
    s = schlusskurse(ticker, "2y")
    r = np.diff(np.log(s.to_numpy(dtype=float)))[-120:]
    return float(1.4826 * np.median(np.abs(r - np.median(r))) * math.sqrt(252))


def rechnen(ticker: str, datum: str, szenarien: list[dict], gross: int | None = None, level: list[float] | None = None) -> dict:
    """Alles für die Seite. szenarien: [{name, p (0–1), bewegung (−0,15 = −15 %), unsicherheit (0–1)}]."""
    if not 2 <= len(szenarien) <= 5:
        return {"fehler": "Bitte 2 bis 5 Szenarien angeben."}
    summe = sum(s["p"] for s in szenarien)
    if abs(summe - 1) > 0.005:
        return {"fehler": f"Die Wahrscheinlichkeiten ergeben {summe * 100:.0f} % statt 100 %."}
    tage = handelstage_bis(datum)
    jahre = tage / 252
    sigma = realisierte_vola(ticker)
    # "Großes" Szenario: angegeben, sonst das mit der größten Bewegung (betragsmäßig)
    gross = gross if gross is not None and 0 <= gross < len(szenarien) else int(np.argmax([abs(s["bewegung"]) for s in szenarien]))
    komp = komponenten(szenarien, sigma, jahre)
    aus = {"ticker": ticker.upper(), "datum": datum, "handelstage": tage, "sigma": round(sigma * 100, 1), "gross": gross,
           "kennzahlen": kennzahlen(komp), "ohne_ereignis": kennzahlen(komponenten([{"name": "", "p": 1, "bewegung": 0}], sigma, jahre)),
           "beitraege": [{"name": s["name"], "p": s["p"], "bewegung": s["bewegung"] * 100, "beitrag": round(s["p"] * s["bewegung"] * 100, 3)} for s in szenarien],
           "x": [round(float(a), 4) for a in GITTER[::3]], "dichte": [round(float(a), 5) for a in dichte(komp)[::3]],
           "heatmap": heatmap(szenarien, gross, sigma, jahre), "simulation": simulation(szenarien, sigma, tage, level)}
    try:
        m = markt_verteilung(ticker, datum)
    except Exception as f:
        m = {"fehler": f"Optionsdaten nicht verfügbar ({type(f).__name__})."}
    if m.get("fehler"):
        aus["markt"] = {"fehler": m["fehler"]}
        return aus
    xm, cdf, dm = np.array(m["x"]), np.array(m["cdf"]), np.array(m["dichte"])
    # Markt auf das eigene Gitter bringen (für die gemeinsame Grafik)
    d_markt = np.interp(GITTER, xm, dm, left=0, right=0)
    anpassung = markt_anpassen(szenarien, gross, sigma, max(m["tage"], 1) / 365, xm, cdf)
    gew = d_markt / (d_markt.sum() or 1)
    r = np.expm1(GITTER) * 100
    aus["markt"] = {
        "verfall": m["verfall"], "tage": m["tage"], "iv": m["iv"], "abweichung_tage": m["abweichung_tage"],
        "dichte": [round(float(a), 5) for a in d_markt[::3]],
        "ausschlag": round(float(np.sum(gew * np.abs(r))), 2), "mittel": round(float(np.sum(gew * r)), 2),
        "var5": round(float(np.interp(0.05, np.cumsum(gew), r)), 2), "p_verlust": round(float(gew[r < 0].sum() * 100), 1),
        "zwei_gipfel": zwei_gipfel(xm, dm), "anpassung": anpassung,
        # deine Szenarien auf genau dem Zeitraum bis zu diesem Verfall (für den fairen Vergleich der Kurven)
        "dichte_eigen": [round(float(a), 5) for a in dichte(komponenten(szenarien, sigma, max(m["tage"], 1) / 365))[::3]],
        "eigen": kennzahlen(komponenten(szenarien, sigma, max(m["tage"], 1) / 365)),
    }
    return aus
