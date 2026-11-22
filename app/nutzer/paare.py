"""Paar-Monitor (Roadmap Q6): Kursverhältnis eng verwandter Werte als Z-Score gegen den eigenen Durchschnitt.

Je Paar A/B: Verhältnis der Kurse (beide in Euro, damit Wechselkurse beide gleich treffen), logarithmiert.
Z-Score = Abstand des heutigen Verhältnisses zum Durchschnitt der letzten 60 bzw. 250 Handelstage, in
Standardabweichungen. Dazu: Korrelation der Tagesrenditen (wie eng hängen die beiden zusammen?) und die
Halbwertszeit, mit der sich Abweichungen früher zurückgebildet haben (aus einem AR(1)-Modell; fehlt sie,
gab es keine erkennbare Rückkehr zum Mittel). Nur Information, keine Handelssignale.
Paare je Benutzer in paare.yaml, im Tool bearbeitbar; ohne Datei die Startpaare unten.
"""
import math

import numpy as np
import pandas as pd
import yaml

from ..benutzer import Benutzer
from ..markt.euro_kurse import euro_kurse

STANDARD = [
    {"a": "CEG", "b": "VST", "name": "Constellation gegen Vistra", "warum": "beide US-Stromerzeuger mit Kernkraft und Rechenzentrums-Fantasie"},
    {"a": "MU", "b": "000660.KS", "name": "Micron gegen SK Hynix", "warum": "die zwei großen Speicherchip-Hersteller außerhalb von Samsung"},
    {"a": "UBER", "b": "LYFT", "name": "Uber gegen Lyft", "warum": "Fahrdienst-Vermittler im selben Markt"},
    {"a": "NVDA", "b": "AMD", "name": "NVIDIA gegen AMD", "warum": "KI-Beschleuniger"},
    {"a": "LMT", "b": "NOC", "name": "Lockheed Martin gegen Northrop Grumman", "warum": "US-Rüstung"},
]
FENSTER = (60, 250)
MAX_PAARE = 20


def _datei(b: Benutzer):
    return b.datei("paare.yaml")


def paare(b: Benutzer) -> list[dict]:
    d = _datei(b)
    if not d.exists():
        return STANDARD
    try:
        liste = yaml.safe_load(d.read_text(encoding="utf-8")) or []
        return [p for p in liste if p.get("a") and p.get("b")][:MAX_PAARE]
    except Exception:
        return STANDARD


def speichern(b: Benutzer, liste: list[dict]) -> list[dict]:
    sauber = []
    for p in liste[:MAX_PAARE]:
        a, bb = str(p.get("a", "")).strip().upper()[:15], str(p.get("b", "")).strip().upper()[:15]
        if a and bb and a != bb:
            sauber.append({"a": a, "b": bb, "name": str(p.get("name") or f"{a} gegen {bb}")[:60], "warum": str(p.get("warum") or "")[:200]})
    d = _datei(b)
    d.parent.mkdir(parents=True, exist_ok=True)
    zw = d.with_suffix(".tmp")
    zw.write_text(yaml.safe_dump(sauber, allow_unicode=True, sort_keys=False), encoding="utf-8")
    zw.replace(d)
    return sauber


def kennzahlen(a: pd.Series, b: pd.Series) -> dict | None:
    """Z-Scores, Korrelation und Halbwertszeit für zwei Kursreihen (gleiche Währung). Reine Funktion."""
    tab = pd.concat([a, b], axis=1, keys=["a", "b"]).dropna()
    tab = tab[tab.index.day_of_week < 5]
    if len(tab) < 80:
        return None
    x = np.log(tab["a"] / tab["b"])
    aus = {"tage": len(x)}
    for n in FENSTER:
        teil = x.iloc[-n:]
        s = float(teil.std())
        aus[f"z{n}"] = float((x.iloc[-1] - teil.mean()) / s) if s > 0 else None
    r = tab.pct_change().dropna()
    aus["korrelation"] = float(r["a"].iloc[-250:].corr(r["b"].iloc[-250:]))
    # AR(1) auf dem Verhältnis über ein Jahr: x_t − x_{t−1} = c + β·x_{t−1}; Halbwertszeit = −ln 2 / ln(1 + β)
    y = x.iloc[-250:]
    dx, lag = y.diff().dropna(), y.shift(1).dropna()
    beta = float(np.polyfit(lag.loc[dx.index], dx, 1)[0]) if len(dx) > 30 else 0.0
    aus["halbwertszeit"] = -math.log(2) / math.log(1 + beta) if -1 < beta < 0 else None
    aus["veraenderung_60"] = float((tab["a"].iloc[-1] / tab["a"].iloc[-61] - 1) - (tab["b"].iloc[-1] / tab["b"].iloc[-61] - 1)) * 100 if len(tab) > 61 else None
    teil = x.iloc[-500:]
    m, s = teil.rolling(250, min_periods=60).mean(), teil.rolling(250, min_periods=60).std()
    aus["verlauf"] = {"daten": [d.strftime("%Y-%m-%d") for d in teil.index], "verhaeltnis": [round(float(math.exp(v)), 5) for v in teil],
                      "mitte": [None if pd.isna(v) else round(float(math.exp(v)), 5) for v in m],
                      "oben": [None if pd.isna(u) or pd.isna(w) else round(float(math.exp(u + 2 * w)), 5) for u, w in zip(m, s)],
                      "unten": [None if pd.isna(u) or pd.isna(w) else round(float(math.exp(u - 2 * w)), 5) for u, w in zip(m, s)],
                      "z": [None if pd.isna(w) or w == 0 else round(float((v - u) / w), 3) for v, u, w in zip(teil, m, s)]}
    return aus


def auswertung(b: Benutzer) -> dict:
    liste = paare(b)
    ticker = sorted({p["a"] for p in liste} | {p["b"] for p in liste})
    kurse = euro_kurse(ticker, tage=760)
    aus = []
    for p in liste:
        if p["a"] not in kurse or p["b"] not in kurse:
            aus.append({**p, "fehler": f"Keine Kurse für {p['a'] if p['a'] not in kurse else p['b']}."})
            continue
        k = kennzahlen(kurse[p["a"]], kurse[p["b"]])
        aus.append({**p, **(k or {"fehler": "Zu wenig gemeinsame Kurse."})})
    return {"paare": aus, "eigene": _datei(b).exists()}
