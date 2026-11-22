"""Absicherungs-Rechner (Roadmap Q8): Wie viel Index-Short bräuchte es, um das Beta des Depots oder des Tech-Anteils
zu halbieren oder aufzuheben, und was kostet das über die Haltedauer?

Beta: Regression der Tagesrenditen (in Euro, ein Jahr, Börsentage) des Depots bzw. des Tech-Teils (Sektor
Technology) auf den Nasdaq 100 (QQQ) bzw. S&P 500 (SPY), beide in Euro (also ohne Währungssicherung, wie die
meisten Zertifikate). Absicherungsbetrag = Anteil × Beta × Wert. Mit Faktor-Zertifikat −L braucht es nur ein
L-tel davon als Kapital, dafür kostet die tägliche Anpassung (Volatility Decay): bei unverändertem Index nach T
Jahren etwa der Faktor exp((f − f²) · σ² · T / 2) mit f = −L und σ = Schwankung des Index (aus VIX bzw. VXN).
Gebühren und Finanzierung als Annahme (Standard 1 % pro Jahr auf das Kapital). Ein Short ohne tägliche
Anpassung (Future, CFD) hat keinen Decay, aber Finanzierungskosten und Nachschusspflicht.
Rest-Schwankung: Schwankung von Depot minus abgesicherte Indexbewegung (gleiche Daten).
Information, keine Empfehlung; Zertifikate sind riskant und können wertlos verfallen (Knock-out bei −1/L).
"""
import math

import numpy as np
import pandas as pd

from ..benutzer import Benutzer
from ..markt.abruf import schlusskurse
from ..markt.euro_kurse import euro_kurse
from ..markt.kurse import stammdaten

INDIZES = {"ndx": ("QQQ", "^VXN", "Nasdaq 100"), "spx": ("SPY", "^VIX", "S&P 500")}
SZENARIEN = [-20, -10, -5, 0, 5, 10, 20]
HEBEL = [1, 2, 3, 5, 10]


def decay_faktor(f: float, sigma: float, jahre: float) -> float:
    """Erwarteter Wertfaktor eines täglich angepassten Produkts mit Faktor f bei unverändertem Index. Reine Funktion."""
    return math.exp((f - f * f) * sigma * sigma * jahre / 2)


def zertifikat_wert(f: float, index_veraenderung: float, sigma: float, jahre: float) -> float:
    """Erwarteter Wertfaktor bei einer Indexveränderung (z. B. −0,1) über die Haltedauer. Reine Funktion."""
    return (1 + index_veraenderung) ** f * decay_faktor(f, sigma, jahre)


def beta(depot: pd.Series, index: pd.Series) -> tuple[float, float]:
    tab = pd.concat([depot, index], axis=1).dropna()
    b = float(np.cov(tab.iloc[:, 0], tab.iloc[:, 1])[0, 1] / tab.iloc[:, 1].var())
    return b, float(tab.iloc[:, 0].corr(tab.iloc[:, 1]))


def rechnen(b: Benutzer, positionen: list[dict], ziel: str = "depot", anteil: int = 50, index: str = "ndx",
            tage: int = 90, gebuehr: float = 1.0) -> dict:
    pos = [p for p in positionen if p.get("ticker") and p.get("wert_eur") and not p.get("extra")]
    if not pos:
        return {"fehler": "Keine Positionen mit Kurs."}
    sektoren = {}
    for p in pos:
        try:
            sektoren[p["ticker"]] = (stammdaten(p["ticker"]) or {}).get("sector")
        except Exception:
            sektoren[p["ticker"]] = None
    tech = [p for p in pos if sektoren.get(p["ticker"]) == "Technology"]
    auswahl = tech if ziel == "tech" else pos
    if not auswahl:
        return {"fehler": "Im Depot gibt es keine Positionen im Sektor Technologie."}
    wert = sum(p["wert_eur"] for p in auswahl)
    etf, vix, name = INDIZES.get(index, INDIZES["ndx"])
    kurse = euro_kurse(sorted({p["ticker"] for p in auswahl} | {etf}), tage=400)
    preise = pd.DataFrame({tk: s for tk, s in kurse.items()})
    preise = preise[preise.index.day_of_week < 5].ffill(limit=5)
    r = preise.pct_change(fill_method=None).iloc[-252:]
    gewichte = pd.Series({p["ticker"]: p["wert_eur"] for p in auswahl if p["ticker"] in r.columns})
    teil = r[gewichte.index]
    da = teil.notna().mul(gewichte, axis=1).sum(axis=1)
    depot_r = teil.fillna(0).mul(gewichte, axis=1).sum(axis=1) / da.replace(0, np.nan)
    b_wert, korr = beta(depot_r, r[etf])
    try:
        sigma = float(schlusskurse(vix, "1y").iloc[-1]) / 100
        sigma_quelle = vix.lstrip("^")
    except Exception:
        sigma = float(r[etf].std() * math.sqrt(252))
        sigma_quelle = "realisiert"
    jahre = tage / 365
    betrag = anteil / 100 * b_wert * wert
    vol_ohne = float(depot_r.std() * math.sqrt(252)) * 100
    vol_mit = float((depot_r - anteil / 100 * b_wert * r[etf]).std() * math.sqrt(252)) * 100

    produkte = [{"art": "future", "name": "Index-Short ohne tägliche Anpassung (Future/CFD)", "hebel": 1, "kapital": betrag,
                 "decay": 0.0, "gebuehr": betrag * gebuehr / 100 * jahre}]
    for L in HEBEL:
        kapital = betrag / L
        produkte.append({"art": "faktor", "name": f"Faktor-Zertifikat Short {L}x" if L > 1 else "Short-Indexprodukt 1x (täglich angepasst)", "hebel": L,
                         "kapital": kapital, "decay": kapital * (1 - decay_faktor(-L, sigma, jahre)), "gebuehr": kapital * gebuehr / 100 * jahre,
                         "knockout": 100 / L if L > 1 else None})
    szen = []
    for m in SZENARIEN:
        zeile = {"index": m, "depot": wert * b_wert * m / 100}
        for pr in produkte:
            if pr["art"] == "future":
                ergebnis = -betrag * m / 100 - pr["gebuehr"]
            else:
                ko = pr.get("knockout")
                ergebnis = (-pr["kapital"] if ko and m >= ko else pr["kapital"] * (zertifikat_wert(-pr["hebel"], m / 100, sigma, jahre) - 1)) - pr["gebuehr"]
            zeile[f"{pr['art']}{pr['hebel']}"] = ergebnis
        szen.append(zeile)
    return {"ziel": ziel, "anteil": anteil, "index": index, "index_name": name, "etf": etf, "tage": tage, "gebuehr": gebuehr,
            "wert": wert, "beta": b_wert, "korrelation": korr, "betrag": betrag, "sigma": sigma * 100, "sigma_quelle": sigma_quelle,
            "vol_ohne": vol_ohne, "vol_mit": vol_mit, "produkte": produkte, "szenarien": szen,
            "tech": [{"name": p["name"], "wert": p["wert_eur"]} for p in tech], "depot_wert": sum(p["wert_eur"] for p in pos)}
