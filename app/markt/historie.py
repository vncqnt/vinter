"""Tageskurse mit Hoch, Tief und Schluss der letzten Monate, für Charts und die ATR.

Die ATR (Average True Range) ist die durchschnittliche Tagesschwankung: Je größer, desto mehr
Abstand braucht ein Stop, damit normales Rauschen ihn nicht auslöst.
"""
import pandas as pd
import yfinance as yf

from ..cache import STUNDE, markt_lesen as cache_lesen, markt_schreiben as cache_schreiben

ATR_TAGE = 14
CHANDELIER_TAGE = 22   # Hoch der letzten gut vier Wochen
CHANDELIER_FAKTOR = 3  # Stop = dieses Hoch minus 3 ATR


def hole_tageskurse(ticker: str) -> dict | None:
    """Sechs Monate Tageskurse in Handelswährung. Stündlich aus dem Cache, sonst None."""
    schluessel = f"historie:{ticker}"
    alt, alter = cache_lesen(schluessel)
    if alt and alter < STUNDE:
        return alt
    try:
        tabelle = yf.Ticker(ticker).history(period="6mo", interval="1d", auto_adjust=False)[["High", "Low", "Close"]].dropna()
        if len(tabelle) < CHANDELIER_TAGE + 1:
            raise ValueError("zu wenig Daten")
    except Exception:
        return alt
    tabelle.index = pd.DatetimeIndex(tabelle.index).tz_localize(None).normalize()
    tabelle = tabelle[~tabelle.index.duplicated(keep="last")]

    # True Range: größte der drei Spannen (Hoch-Tief, Hoch-Vortagesschluss, Tief-Vortagesschluss).
    vorher = tabelle["Close"].shift(1)
    spanne = pd.concat([
        tabelle["High"] - tabelle["Low"],
        (tabelle["High"] - vorher).abs(),
        (tabelle["Low"] - vorher).abs(),
    ], axis=1).max(axis=1)
    atr = float(spanne.tail(ATR_TAGE).mean())
    hoch = float(tabelle["High"].tail(CHANDELIER_TAGE).max())
    neu = {
        "tage": [{"d": d.date().isoformat(), "c": round(float(c), 4)} for d, c in tabelle["Close"].items()],
        "schluss": float(tabelle["Close"].iloc[-1]),
        "atr": atr,
        "hoch": hoch,
        "trailing": hoch - CHANDELIER_FAKTOR * atr,
    }
    cache_schreiben(schluessel, neu)
    return neu
