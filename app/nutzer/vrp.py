"""Volatilitäts-Risikoprämie je Wert (Roadmap Q5): Ist Absicherung gerade teuer oder billig?

Prämie = implizite Volatilität (ATM, rund 30 Tage) minus realisierte Volatilität der letzten 20 Tage, in
Vol-Punkten, dazu das Verhältnis IV / RV. Normal liegt die IV etwas über der realisierten (Anleger zahlen für
Schutz). Verlauf und Perzentil aus der IV-Historie: für SPY/QQQ aus VIX/VXN (ein Jahr), für Aktien aus Vinters
eigener Sammlung (ab 20 Tagen). Fehlt die gespeicherte IV, kommt die heutige aus den Optionsketten
(wahrscheinlichkeit.py, Verfall nahe 30 Tagen).
Bezug zu Hebelprodukten: Optionsscheine enthalten die IV im Preis (hohe IV = teuer gekauft); Faktor-Zertifikate
haben keine IV im Preis, verlieren aber mit hoher realisierter Schwankung an Wert (Volatility Decay).
"""
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from ..benutzer import Benutzer
from ..markt.schwankung import analyse

IMMER = ["SPY", "QQQ"]


def _iv_heute(tk: str) -> float | None:
    try:
        from ..markt.wahrscheinlichkeit import verteilung
        v = verteilung(tk)
        if v.get("fehler"):
            return None
        return min(v["verfaelle"], key=lambda x: abs(x["tage"] - 30))["iv"]
    except Exception:
        return None


def zeile(tk: str, name: str) -> dict | None:
    a = analyse(tk)
    if a.get("fehler") or not a["rv"].get(20):
        return None
    iv = (a.get("iv") or {}).get("atm30")
    quelle = (a.get("iv") or {}).get("quelle")
    if iv is None:
        iv, quelle = _iv_heute(tk), "Optionskette heute"
    if iv is None:
        return None
    rv = a["rv"][20]
    aus = {"ticker": tk, "name": name, "iv": iv, "rv20": rv, "praemie": iv - rv, "verhaeltnis": iv / rv if rv else None, "quelle": quelle}
    # Verlauf der Prämie (nur Tage mit IV und RV)
    v = a["verlauf"]
    paare = [(d, i - r) for d, i, r in zip(v["daten"], v.get("iv") or [], v["rv20"]) if i is not None and r is not None]
    if paare:
        aus["verlauf"] = {"daten": [p[0] for p in paare], "praemie": [round(p[1], 2) for p in paare]}
    if len(paare) >= 20:
        werte = np.array([p[1] for p in paare])
        aus["perzentil"] = float((werte < aus["praemie"]).mean() * 100)
        aus["median"] = float(np.median(werte))
    aus["tage"] = len(paare)
    r = aus["verhaeltnis"] or 1
    aus["einordnung"] = "Absicherung teuer" if r >= 1.3 else "Absicherung günstig" if r <= 0.9 else "üblich"
    # Lagen Quartalszahlen in den letzten 20 Handelstagen, bläht der Sprung die realisierte Vola auf
    try:
        from datetime import date, timedelta
        from ..markt.earnings_moves import chart_termine
        grenze = (date.today() - timedelta(days=29)).isoformat()
        zahlen = [x["datum"] for x in chart_termine(tk).get("termine", []) if not x.get("zukunft") and x["datum"] >= grenze]
        if zahlen:
            aus["zahlen_im_fenster"] = zahlen[-1]
            if aus["einordnung"] == "Absicherung günstig":
                aus["einordnung"] = "unklar (Zahlen-Sprung in der realisierten Vola)"
    except Exception:
        pass
    return aus


def auswertung(b: Benutzer, positionen: list[dict]) -> dict:
    us = [(p["ticker"], p["name"]) for p in positionen if p.get("ticker") and "." not in p["ticker"] and "-" not in p["ticker"]
          and p.get("art") in ("STOCK", None) and not p.get("extra")]
    werte = list(dict.fromkeys([(t, t) for t in IMMER] + us))
    with ThreadPoolExecutor(4) as pool:
        zeilen = [z for z in pool.map(lambda x: _sicher(*x), werte) if z]
    return {"werte": sorted(zeilen, key=lambda z: -(z["verhaeltnis"] or 0))}


def _sicher(tk, name):
    try:
        return zeile(tk, name)
    except Exception:
        return None
