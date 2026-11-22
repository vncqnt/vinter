"""Analyse-Charts fürs Depot (Roadmap Stufe 5): Treemap, Wasserfall der Beiträge, Kalender-Heatmap, Rendite gegen
Volatilität, Korrelations-Netzwerk.

- Treemap und Wasserfall: heutige Stückzahlen, Kurse in Euro. Beitrag = Stück × Kursveränderung im Zeitraum.
  Käufe und Verkäufe im Zeitraum sind darin nicht enthalten (dafür gibt es Gewinn & Verlust → Verlauf).
- Kalender-Heatmap: zeitgewichtete Tagesrendite des echten Depots (depot_verlauf), also mit Käufen und Verkäufen,
  aber ohne dass Einzahlungen als Gewinn zählen.
- Rendite gegen Volatilität: je Position Rendite im Zeitraum und Jahresschwankung (ein Jahr).
- Korrelations-Netzwerk: Positionen als Punkte, Linien ab einer Korrelation der Tagesrenditen von 0,5 (ein Jahr).
"""
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..benutzer import Benutzer
from ..markt.euro_kurse import euro_kurse
from .verlauf import depot_verlauf

ZEITRAEUME = {"1m": 30, "3m": 91, "ytd": None, "1j": 365}
KANTE = 0.5


def _start(zeitraum: str) -> pd.Timestamp:
    heute = date.today()
    tage = ZEITRAEUME.get(zeitraum, 91)
    return pd.Timestamp(date(heute.year, 1, 1) if tage is None else heute - timedelta(days=tage))


def beitraege(stueck: dict[str, float], kurse: dict[str, pd.Series], ab: pd.Timestamp) -> dict[str, dict]:
    """Je Ticker Wert am Anfang, Wert heute, Beitrag in Euro und Rendite. Reine Funktion."""
    aus = {}
    for tk, n in stueck.items():
        s = kurse.get(tk)
        if s is None:
            continue
        s = s.dropna()
        vor = s[s.index <= ab]
        anfang = float(vor.iloc[-1]) if not vor.empty else float(s.iloc[0])
        jetzt = float(s.iloc[-1])
        aus[tk] = {"anfang": n * anfang, "jetzt": n * jetzt, "beitrag": n * (jetzt - anfang), "rendite": (jetzt / anfang - 1) * 100 if anfang else None,
                   "ab_spaeter": vor.empty}
    return aus


def auswertung(b: Benutzer, positionen: list[dict], zeitraum: str = "3m") -> dict:
    zeitraum = zeitraum if zeitraum in ZEITRAEUME else "3m"
    pos = [p for p in positionen if p.get("ticker") and p.get("wert_eur") and not p.get("extra")]
    if not pos:
        return {"fehler": "Keine Positionen mit Kurs."}
    kurse = euro_kurse(sorted({p["ticker"] for p in pos}), tage=400)
    ab = _start(zeitraum)
    b_je = beitraege({p["ticker"]: p["stueck"] for p in pos}, kurse, ab)
    gesamt = sum(p["wert_eur"] for p in pos)

    preise = pd.DataFrame({tk: s for tk, s in kurse.items()})
    preise = preise[preise.index.day_of_week < 5].ffill(limit=5)
    r = preise.pct_change(fill_method=None).iloc[-252:]
    vol = (r.std() * math.sqrt(252) * 100).to_dict()

    zeilen = []
    for p in pos:
        x = b_je.get(p["ticker"])
        zeilen.append({"ticker": p["ticker"], "name": p["name"], "wert": p["wert_eur"], "anteil": p["wert_eur"] / gesamt * 100,
                       "beitrag": x["beitrag"] if x else None, "rendite": x["rendite"] if x else None, "ab_spaeter": bool(x and x["ab_spaeter"]),
                       "vol": vol.get(p["ticker"]), "art": p.get("art")})

    # Korrelations-Netzwerk (nur Werte mit mindestens einem halben Jahr Daten)
    gut = [c for c in r.columns if r[c].notna().sum() >= 120]
    korr = r[gut].corr()
    kanten = [{"a": a, "b": c, "k": round(float(korr.at[a, c]), 2)} for i, a in enumerate(gut) for c in gut[i + 1:] if korr.at[a, c] >= KANTE]

    # Kalender: zeitgewichteter Depotverlauf
    kalender = None
    v = depot_verlauf(b)
    if not v.get("fehler") and v.get("punkte"):
        idx = pd.Series({pd.Timestamp(x["datum"]): x["wert"] for x in v["punkte"]})
        tag = idx.pct_change().dropna() * 100
        monat = (idx.resample("ME").last().pct_change() * 100).dropna()
        erster = idx.resample("ME").last().index[0]
        # erster (angebrochener) Monat ab Beginn des Verlaufs
        m0 = idx[idx.index <= erster]
        if len(m0) > 1:
            monat.loc[erster] = (m0.iloc[-1] / m0.iloc[0] - 1) * 100
        monat = monat.sort_index()
        kalender = {"tage": {d.strftime("%Y-%m-%d"): round(float(w), 2) for d, w in tag.items()},
                    "monate": {d.strftime("%Y-%m"): round(float(w), 2) for d, w in monat.items()},
                    "von": idx.index[0].strftime("%Y-%m-%d")}
    return {"zeitraum": zeitraum, "ab": ab.strftime("%Y-%m-%d"), "gesamt": gesamt, "positionen": zeilen,
            "summe_beitrag": sum(z["beitrag"] for z in zeilen if z["beitrag"] is not None),
            "netz": {"knoten": [{"ticker": t, "name": next(p["name"] for p in pos if p["ticker"] == t), "anteil": next(z["anteil"] for z in zeilen if z["ticker"] == t)} for t in gut],
                     "kanten": kanten, "schwelle": KANTE},
            "kalender": kalender}
