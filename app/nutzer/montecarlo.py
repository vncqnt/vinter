"""Monte-Carlo fürs Depot (Roadmap Q10): Bandbreite möglicher Depotverläufe über ein Jahr.

Annahmen (stehen auch auf der Seite):
- Die Tagesrenditen des heutigen Depots (heutige Gewichte, in Euro, Börsentage) der letzten 3 oder 5 Jahre sind
  der Vorrat, aus dem die Zukunft zusammengesetzt wird (Bootstrap). Was es in diesem Zeitraum nicht gab, kommt
  nicht vor.
- Gezogen werden Blöcke von 20 Handelstagen (zirkulärer Block-Bootstrap: jeder Tag gleich oft), damit ruhige und
  unruhige Phasen zusammenbleiben.
- Gewichte bleiben täglich gleich (wie ständiges Umschichten), keine Einzahlungen, keine Steuern, keine Gebühren.
- Werte ohne genug Historie zählen an fehlenden Tagen nicht; die übrigen werden entsprechend höher gewichtet.
- Rendite: Die heutigen Positionen sind oft die Gewinner der Vergangenheit; ihre Rückblick-Rendite in die Zukunft
  zu schreiben wäre geschönt. Standard deshalb: Schwankung und Ausreißer aus der Vergangenheit, aber die
  durchschnittliche Tagesrendite auf 7 % pro Jahr gesetzt (langfristiger Aktienschnitt). Wählbar: 0 % oder die
  historische Rendite (mit Warnung).
5.000 Pfade à 252 Handelstage, fester Zufallsstart (gleiche Eingabe, gleiches Ergebnis).
"""
import math

import numpy as np
import pandas as pd

from ..benutzer import Benutzer
from ..markt.euro_kurse import euro_kurse

PFADE = 5000
TAGE = 252
BLOCK = 20


def block_bootstrap(r: np.ndarray, pfade: int = PFADE, tage: int = TAGE, block: int = BLOCK, seed: int = 7) -> np.ndarray:
    """Matrix pfade × tage aus Blöcken der Rendite-Reihe r. Reine Funktion."""
    rng = np.random.default_rng(seed)
    n = len(r)
    bloecke = math.ceil(tage / block)
    # zirkulär: Blöcke dürfen über das Ende hinaus am Anfang weiterlaufen, so wird jeder Tag gleich oft gezogen
    starts = rng.integers(0, n, size=(pfade, bloecke))
    idx = ((starts[:, :, None] + np.arange(block)[None, None, :]) % n).reshape(pfade, -1)[:, :tage]
    return r[idx]


RENDITEN = {"neutral": 7.0, "null": 0.0, "hist": None}


def mit_rendite(r: np.ndarray, pro_jahr: float | None) -> np.ndarray:
    """Tagesrenditen so verschieben, dass ihr Schnitt einer Jahresrendite entspricht (Schwankung bleibt). Reine Funktion."""
    if pro_jahr is None:
        return r
    ziel = (1 + pro_jahr / 100) ** (1 / 252) - 1
    # auf Log-Ebene verschieben, damit die Verkettung im Mittel die Zielrendite ergibt
    log = np.log1p(r)
    return np.expm1(log - log.mean() + math.log1p(ziel))


def auswertung(b: Benutzer, positionen: list[dict], jahre: int = 3, rendite: str = "neutral") -> dict:
    pos = [p for p in positionen if p.get("ticker") and p.get("wert_eur") and not p.get("extra")]
    if not pos:
        return {"fehler": "Keine Positionen mit Kurs."}
    jahre = 5 if jahre >= 5 else 3
    kurse = euro_kurse(sorted({p["ticker"] for p in pos}), tage=365 * jahre + 30)
    preise = pd.DataFrame({tk: s for tk, s in kurse.items()})
    preise = preise[preise.index.day_of_week < 5].ffill(limit=5)
    r = preise.pct_change(fill_method=None).iloc[1:]
    w = pd.Series({p["ticker"]: p["wert_eur"] for p in pos if p["ticker"] in r.columns})
    da = r[w.index].notna().mul(w, axis=1).sum(axis=1)
    depot = (r[w.index].fillna(0).mul(w, axis=1).sum(axis=1) / da.replace(0, np.nan)).dropna()
    depot = depot[da.loc[depot.index] / w.sum() >= 0.5]  # Tage mit weniger als der Hälfte des Depots weglassen
    if len(depot) < 250:
        return {"fehler": "Zu wenig gemeinsame Kurshistorie."}
    wert = sum(p["wert_eur"] for p in pos)
    rendite = rendite if rendite in RENDITEN else "neutral"
    sim = block_bootstrap(mit_rendite(depot.to_numpy(), RENDITEN[rendite]))
    verlauf = np.cumprod(1 + sim, axis=1)
    ende = verlauf[:, -1]
    tief = (verlauf / np.maximum.accumulate(verlauf, axis=1)).min(axis=1)
    stufen = [5, 25, 50, 75, 95]
    faecher = {f"p{q}": [round(float(x) * wert, 2) for x in np.percentile(verlauf, q, axis=0)[::5]] for q in stufen}
    # 150 Beispielpfade für die Grafik (blasse Linien), gleichmäßig über die Endwerte verteilt, damit Ränder dabei sind
    reihenfolge = np.argsort(ende)
    auswahl = reihenfolge[np.linspace(0, len(ende) - 1, 150).astype(int)]
    beispiele = [[round(float(x) * wert) for x in verlauf[i, ::5]] for i in auswahl]
    stichprobe = [round(float(x) * wert) for x in ende[np.random.default_rng(5).choice(len(ende), 1500, replace=False)]]
    hist, kanten = np.histogram((ende - 1) * 100, bins=40)
    return {
        "wert": wert, "jahre": jahre, "tage_historie": len(depot), "pfade": PFADE, "block": BLOCK, "rendite": rendite, "rendite_pa": RENDITEN[rendite],
        "ende": {f"p{q}": float(np.percentile(ende, q) - 1) * 100 for q in stufen},
        "ende_euro": {f"p{q}": float(np.percentile(ende, q)) * wert for q in stufen},
        "p_verlust": float((ende < 1).mean() * 100), "p_minus20": float((ende < 0.8).mean() * 100), "p_plus20": float((ende > 1.2).mean() * 100),
        "tief_median": float(np.median(tief) - 1) * 100, "tief_p5": float(np.percentile(tief, 5) - 1) * 100,
        "historisch": {"pro_jahr": float((1 + depot).prod() ** (252 / len(depot)) - 1) * 100, "vol": float(depot.std() * math.sqrt(252)) * 100,
                       "schlechtester_tag": float(depot.min()) * 100, "bester_tag": float(depot.max()) * 100,
                       "von": depot.index[0].strftime("%Y-%m-%d"), "bis": depot.index[-1].strftime("%Y-%m-%d")},
        "faecher": {"tage": list(range(0, TAGE, 5)), **faecher},
        "beispiele": beispiele, "stichprobe": stichprobe,
        "histogramm": {"mitte": [round(float((a + c) / 2), 1) for a, c in zip(kanten, kanten[1:])], "anzahl": [int(x) for x in hist]},
        "ohne": [p["name"] for p in pos if p["ticker"] not in r.columns],
    }
