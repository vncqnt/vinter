"""Heatmap der 11 US-Sektoren über die SPDR-ETFs, stündlich aktualisiert."""
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf

from .kurse import STUNDE, TAG, cache_lesen, cache_schreiben, zeit_des_abrufs

SEKTOREN = {
    "XLK": "Technologie",
    "XLC": "Kommunikation",
    "XLY": "Zyklischer Konsum",
    "XLP": "Basiskonsum",
    "XLE": "Energie",
    "XLF": "Finanzen",
    "XLV": "Gesundheit",
    "XLI": "Industrie",
    "XLB": "Grundstoffe",
    "XLRE": "Immobilien",
    "XLU": "Versorger",
}


# Feinere Branchen, jeweils über einen bekannten Branchen-ETF.
BRANCHEN = {
    "SOXX": "Halbleiter",
    "IGV": "Software",
    "ITA": "Rüstung",
    "XBI": "Biotech",
    "KBE": "Banken",
    "XOP": "Öl & Gas",
    "URA": "Uran",
    "ICLN": "Saubere Energie",
    "PAVE": "Infrastruktur",
    "XHB": "Hausbau",
    "XRT": "Einzelhandel",
    "IYT": "Transport",
}


def _prozent(wert: float, schluss: pd.Series, bis: pd.Timestamp) -> float | None:
    """Veränderung seit dem letzten Schlusskurs am oder vor dem Stichtag."""
    davor = schluss[schluss.index <= bis]
    return (wert / float(davor.iloc[-1]) - 1) * 100 if len(davor) else None


def hole_veraenderung(ticker: str) -> tuple[dict | None, bool]:
    schluessel = f"sektor:{ticker}"
    alt, alter = cache_lesen(schluessel)
    if alt and alter < STUNDE:
        return alt, False
    try:
        schluss = yf.Ticker(ticker).history(period="1y", auto_adjust=False)["Close"].dropna()
        if len(schluss) < 2:
            raise ValueError("zu wenig Daten")
        letzter = schluss.index[-1]
        wert = float(schluss.iloc[-1])
        neu = {
            "tag": (wert / float(schluss.iloc[-2]) - 1) * 100,
            "woche": _prozent(wert, schluss, letzter - pd.Timedelta(days=7)),
            "monat": _prozent(wert, schluss, letzter - pd.DateOffset(months=1)),
            # Basis ist der letzte Handelstag des Vorjahres.
            "ytd": _prozent(wert, schluss, letzter.replace(month=1, day=1) - pd.Timedelta(days=1)),
            "stand": letzter.date().isoformat(),
        }
    except Exception:
        return alt, True
    cache_schreiben(schluessel, neu)
    return neu, False


def _top_werte(etf: str) -> list | None:
    """Die 10 größten Werte des ETFs. Die Gewichte ändern sich langsam, daher täglich."""
    schluessel = f"top:{etf}"
    alt, alter = cache_lesen(schluessel)
    if alt and alter < TAG:
        return alt
    try:
        tabelle = yf.Ticker(etf).funds_data.top_holdings
        neu = [
            {"ticker": symbol, "name": str(z["Name"]), "gewicht": float(z["Holding Percent"]) * 100}
            for symbol, z in tabelle.iterrows()
        ]
        if not neu:
            raise ValueError("keine Daten")
    except Exception:
        return alt
    cache_schreiben(schluessel, neu)
    return neu


def sektor_details(etf: str) -> dict:
    if etf not in SEKTOREN and etf not in BRANCHEN:
        return {"fehler": "Diesen Sektor gibt es nicht."}
    top = _top_werte(etf)
    if not top:
        return {"fehler": "Für diesen Sektor gibt es gerade keine Top-Werte."}
    with ThreadPoolExecutor(max_workers=4) as pool:
        ergebnisse = list(pool.map(hole_veraenderung, (t["ticker"] for t in top)))
    return {"werte": [{**t, "veraltet": alt, **(daten or {})} for t, (daten, alt) in zip(top, ergebnisse)]}


def sektoren() -> dict:
    def liste(gruppe: dict) -> list:
        with ThreadPoolExecutor(max_workers=4) as pool:
            ergebnisse = list(pool.map(hole_veraenderung, gruppe))
        return [
            {"ticker": ticker, "name": name, "veraltet": veraltet, "aktualisiert": zeit_des_abrufs(f"sektor:{ticker}"), **(daten or {})}
            for (ticker, name), (daten, veraltet) in zip(gruppe.items(), ergebnisse)
        ]

    return {"sektoren": liste(SEKTOREN), "branchen": liste(BRANCHEN)}
