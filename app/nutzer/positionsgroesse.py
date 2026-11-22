"""Positionsgrößen-Rechner: Wie viele Stück darf ich kaufen, wenn ich beim Stop höchstens X € verlieren will?

Die eigentliche Rechnung (Stückzahl = maximaler Verlust ÷ Abstand Einstieg–Stop) macht die Oberfläche
sofort beim Tippen. Von hier kommen nur die Zutaten:
- aktueller Kurs in Euro und die ATR (durchschnittliche Tagesschwankung) für Stop-Vorschläge,
- drei Zahlen aus den Tagesrenditen des letzten Jahres, mit denen sich für jeden Kaufbetrag b die neue
  Schwankung des Depots ausrechnen lässt: Varianz(b) = A + 2·b·B + b²·C.
  A = Schwankung des heutigen Depots, B = wie stark der Wert mit dem Depot mitläuft, C = seine eigene Schwankung.
Alles in Euro, mit heutigen Gewichten.
"""
import math

import numpy as np
import pandas as pd

from ..markt.euro_kurse import euro_kurse
from ..markt.historie import CHANDELIER_FAKTOR, hole_tageskurse
from ..markt.kurse import kurs_eur
from .exits import _stop_vorschlag

TAGE_JAHR = 252


def zutaten(positionen: list[dict], ticker: str) -> dict:
    pos = [p for p in positionen if p.get("ticker") and p.get("wert_eur")]
    kurs = kurs_eur(ticker)
    preis = kurs.get("preis_eur")
    if not preis:
        return {"fehler": f"Für {ticker} gibt es gerade keinen Kurs."}
    eigen = next((p for p in pos if p["ticker"] == ticker), None)
    ergebnis = {
        "ticker": ticker, "name": eigen["name"] if eigen else ticker, "kurs": preis,
        "im_depot": {"stueck": eigen.get("stueck"), "wert": eigen["wert_eur"],
                     "einstand": (eigen.get("einstand_gesamt") or 0) / eigen["stueck"] if eigen.get("stueck") else None} if eigen else None,
    }

    historie = hole_tageskurse(ticker)
    if historie:
        stop = _stop_vorschlag(historie, preis)
        ergebnis["atr"] = stop["atr"]
        ergebnis["stops"] = [
            {"name": "2 × ATR unter dem Kurs", "kurs": preis - 2 * stop["atr"]},
            {"name": f"Trailing-Stop wie bei den Exit-Alarmen ({CHANDELIER_FAKTOR} × ATR)", "kurs": stop["trailing"]},
        ]

    # Zutaten für die Depot-Schwankung
    kurse = euro_kurse(sorted({p["ticker"] for p in pos} | {ticker}), tage=400)
    if ticker in kurse and pos:
        tabelle = pd.DataFrame(kurse).sort_index().ffill(limit=5)
        tabelle = tabelle[tabelle.index >= tabelle.index[-1] - pd.Timedelta(days=365)]
        R = tabelle.pct_change(fill_method=None).iloc[1:]
        R = R.where(R.abs() < 0.5).fillna(0.0)
        namen = [p["ticker"] for p in pos if p["ticker"] in R]
        v = np.array([p["wert_eur"] for p in pos if p["ticker"] in R])
        spalten = namen + ([ticker] if ticker not in namen else [])
        sigma = R[spalten].cov().to_numpy() * TAGE_JAHR
        v_voll = np.append(v, 0.0) if ticker not in namen else v
        k = spalten.index(ticker)
        sv = sigma @ v_voll
        ergebnis["risiko"] = {
            "A": float(v_voll @ sv), "B": float(sv[k]), "C": float(sigma[k, k]),
            "depotwert": float(v.sum()), "wert_jetzt": float(v_voll[k]),
            "vol_eigen": math.sqrt(float(sigma[k, k])) * 100,
        }
    return ergebnis
