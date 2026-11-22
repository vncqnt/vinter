"""Markt-Cockpit: Stand und Veränderung zu Vortag und Vorwoche, stündlich aktualisiert."""
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf

from .kurse import STUNDE, cache_lesen, cache_schreiben, zeit_des_abrufs

# Art steuert die Darstellung: "rendite" in Prozent mit Änderung in Basispunkten,
# "index" und "roh" mit Grün/Rot, "neutral" ohne Farbe (steigender VIX ist kein Gewinn).
GRUPPEN = [
    ("Indizes", [
        ("^GSPC", "S&P 500", "index"),
        ("^IXIC", "Nasdaq", "index"),
        ("^GDAXI", "DAX", "index"),
    ]),
    ("Zinsen und Volatilität", [
        ("^VIX", "VIX", "neutral"),
        ("2YY=F", "US-Rendite 2 Jahre", "rendite"),
        ("^TNX", "US-Rendite 10 Jahre", "rendite"),
    ]),
    ("Währungen", [
        ("DX-Y.NYB", "Dollar-Index", "neutral"),
        ("EURUSD=X", "EUR/USD", "fx"),
    ]),
    ("Rohstoffe und Krypto", [
        ("CL=F", "Öl WTI", "roh"),
        ("BZ=F", "Öl Brent", "roh"),
        ("GC=F", "Gold", "roh"),
        ("BTC-USD", "Bitcoin", "roh"),
    ]),
]


def _verlauf(ticker: str) -> tuple[dict | None, bool]:
    """Gibt (Daten, veraltet?) zurück."""
    schluessel = f"markt2:{ticker}"
    alt, alter = cache_lesen(schluessel)
    if alt and alter < STUNDE:
        return alt, False
    try:
        schluss = yf.Ticker(ticker).history(period="1y", auto_adjust=False)["Close"].dropna()
        if len(schluss) < 2:
            raise ValueError("zu wenig Daten")
        vorwoche = schluss[schluss.index <= schluss.index[-1] - pd.Timedelta(days=7)]
        neu = {
            "wert": float(schluss.iloc[-1]),
            "vortag": float(schluss.iloc[-2]),
            "vorwoche": float(vorwoche.iloc[-1]) if len(vorwoche) else None,
            "stand": schluss.index[-1].date().isoformat(),
            # Drei gleiche Schlusskurse in Folge deuten auf eine eingefrorene Datenquelle hin.
            "eingefroren": len(schluss) >= 3 and schluss.iloc[-1] == schluss.iloc[-2] == schluss.iloc[-3],
        }
        neu["eingefroren"] = bool(neu["eingefroren"])
        neu["jahr_tief"], neu["jahr_hoch"] = float(schluss.min()), float(schluss.max())
    except Exception:
        return alt, True
    cache_schreiben(schluessel, neu)
    return neu, False


def _aenderung(wert: float, basis: float | None, art: str):
    if basis is None:
        return None
    if art == "rendite":
        return (wert - basis) * 100  # Basispunkte: 1 Bp = 0,01 Prozentpunkte
    return (wert / basis - 1) * 100


def _baue_skala(wert: float, start: float, zonen: list, skala_ende: float, links: str, rechts: str) -> dict:
    """zonen: Liste aus (Obergrenze, Name, Farbstufe 0 bis 4)."""
    teile, von = [], start
    for grenze, name, stufe in zonen:
        teile.append({"name": name, "stufe": stufe, "breite": grenze - von})
        von = grenze
    label, stufe = next(((n, s) for g, n, s in zonen if wert < g), zonen[-1][1:])
    position = max(0.0, min(100.0, (wert - start) / (skala_ende - start) * 100))
    return {"label": label, "stufe": stufe, "position": position, "zonen": teile, "links": links, "rechts": rechts}


def _skala(ticker: str, daten: dict) -> dict | None:
    """Einordnung für VIX (feste Faustregeln) und Öl (Lage in der eigenen 12-Monats-Spanne)."""
    wert = daten["wert"]
    if ticker == "^VIX":
        zonen = [(15, "Sehr ruhig", 0), (20, "Normal", 1), (30, "Nervös", 2), (40, "Angst", 3), (50, "Panik", 4)]
        return _baue_skala(wert, 10, zonen, 50, "10", "50+")
    if ticker in ("CL=F", "BZ=F"):
        tief, hoch = daten["jahr_tief"], daten["jahr_hoch"]
        if hoch <= tief:
            return None
        spanne = hoch - tief
        zonen = [
            (tief + spanne * 0.25, "Niedrig", 0),
            (tief + spanne * 0.60, "Normal", 1),
            (tief + spanne * 0.85, "Hoch", 3),
            (hoch + 0.000001, "Sehr hoch", 4),
        ]
        return _baue_skala(wert, tief, zonen, hoch, f"{tief:.0f} $ Jahrestief", f"{hoch:.0f} $ Jahreshoch")
    return None


def index_verlauf() -> dict:
    """S&P 500 der letzten 12 Monate für den Hintergrund der Startseite.

    Wochen-Schlusskurse statt Tageskurse ergeben eine ruhigere Linie.
    """
    schluessel = "verlauf:^GSPC"
    alt, alter = cache_lesen(schluessel)
    if alt and alter < STUNDE:
        return alt
    try:
        schluss = yf.Ticker("^GSPC").history(period="1y", auto_adjust=False)["Close"].dropna()
        woche = schluss.resample("W-FRI").last().dropna()
        if len(woche) < 2:
            raise ValueError("zu wenig Daten")
        neu = {"name": "S&P 500", "punkte": [{"wert": float(w)} for w in woche]}
    except Exception:
        return alt or {"fehler": "Kein Index-Verlauf verfügbar."}
    cache_schreiben(schluessel, neu)
    return neu


def cockpit() -> dict:
    alle = [wert for _, werte in GRUPPEN for wert in werte]
    with ThreadPoolExecutor(max_workers=4) as pool:
        ergebnisse = dict(zip((w[0] for w in alle), pool.map(_verlauf, (w[0] for w in alle))))

    gruppen = []
    for titel, werte in GRUPPEN:
        kacheln = []
        for ticker, name, art in werte:
            daten, veraltet = ergebnisse[ticker]
            kachel = {"ticker": ticker, "name": name, "art": art, "wert": None, "veraltet": veraltet}
            if daten:
                kachel.update(
                    wert=daten["wert"],
                    tag=_aenderung(daten["wert"], daten["vortag"], art),
                    woche=_aenderung(daten["wert"], daten["vorwoche"], art),
                    stand=daten["stand"],
                    aktualisiert=zeit_des_abrufs(f"markt2:{ticker}"),
                    eingefroren=daten["eingefroren"],
                    skala=_skala(ticker, daten),
                )
            kacheln.append(kachel)
        gruppen.append({"titel": titel, "kacheln": kacheln})
    return {"gruppen": gruppen}
