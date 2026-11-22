"""Erkennt, ob sich ein Wert heute außergewöhnlich stark bewegt.

Maßstab ist die eigene übliche Tagesschwankung des Werts: die Standardabweichung seiner
Tagesrenditen der letzten 60 Handelstage. +5 % sind bei einer wilden Aktie normal, bei einer
ruhigen eine Sensation. Zählt nur, wenn die Bewegung mindestens das 4-Fache des Üblichen UND
mindestens 4 % beträgt. An zwei Jahren Kursdaten von 16 Werten löst das rund 1,7-mal pro
Aktie und Jahr aus.
"""
import pandas as pd
import yfinance as yf

from .boersen import boerse_von, ist_aktuelle_sitzung
from .kurse import STUNDE, cache_lesen, cache_schreiben

FENSTER = 60
MIN_TAGE = 30
MIN_VIELFACHES = 4.0
MIN_PROZENT = 4.0


def _tagesreihe(ticker: str) -> dict[str, float]:
    """Schlusskurse des letzten Jahres, stündlich aktualisiert."""
    schluessel = f"tagesreihe:{ticker}"
    alt, alter = cache_lesen(schluessel)
    if alt and alter < STUNDE:
        return alt
    try:
        schluss = yf.Ticker(ticker).history(period="1y", auto_adjust=False)["Close"].dropna()
        neu = {d.date().isoformat(): float(w) for d, w in schluss.items()}
        if not neu:
            raise ValueError("keine Daten")
    except Exception:
        return alt or {}
    cache_schreiben(schluessel, neu)
    return neu


def bewerte_reihe(reihe: dict[str, float], preis: float, stand: str) -> dict:
    """Tagesveränderung in % und das Vielfache der üblichen Schwankung, aus Kursen vor `stand`."""
    tage = sorted(t for t in reihe if t < stand)
    if not tage:
        return {"tag": None, "vielfaches": None}
    tag = (preis / reihe[tage[-1]] - 1) * 100
    renditen = pd.Series([reihe[t] for t in tage[-(FENSTER + 1):]]).pct_change().dropna() * 100
    schwankung = float(renditen.std())
    if len(renditen) < MIN_TAGE or not schwankung:
        return {"tag": tag, "vielfaches": None}  # zu wenig Historie für eine faire Einordnung
    return {"tag": tag, "vielfaches": abs(tag) / schwankung}


def auswerten(ticker: str, preis: float, stand: str, veraltet: bool = False) -> dict:
    ergebnis = bewerte_reihe(_tagesreihe(ticker), preis, stand)
    vielfaches = ergebnis["vielfaches"]
    ergebnis["aussergewoehnlich"] = bool(
        vielfaches is not None and vielfaches >= MIN_VIELFACHES and abs(ergebnis["tag"]) >= MIN_PROZENT
        and not veraltet and ist_aktuelle_sitzung(boerse_von(ticker), stand)
    )
    return ergebnis
