"""Bewertet das KGV einer Aktie im Vergleich zu ihrem Sektor, einmal pro Tag von Yahoo.

Als Sektor-KGV dient das KGV des passenden US-Sektor-ETFs. Ein KGV von 15 kann so bei
Technologie billig und bei Energie normal sein. Bewertet wird das Verhältnis der beiden.
"""
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

from .kurse import TAG, cache_lesen, cache_schreiben
from .sektoren import SEKTOREN

SEKTOR_ETF = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}
BILLIG_UNTER = 0.8  # 20 % unter dem Sektor
TEUER_UEBER = 1.25  # 25 % über dem Sektor, als Faktor das Gegenstück zu 0,8


def _positiv(wert) -> float | None:
    """Yahoo liefert bei Verlusten nichts, negative Werte oder 'Infinity'. Sinnvoll ist nur > 0."""
    return float(wert) if isinstance(wert, (int, float)) and 0 < wert < float("inf") else None


def _kennzahlen(ticker: str) -> dict:
    schluessel = f"kgv:{ticker}"
    alt, alter = cache_lesen(schluessel)
    if alt and alter < TAG:
        return alt
    try:
        roh = yf.Ticker(ticker).info
        if not roh.get("quoteType"):
            raise ValueError("keine Daten")
        neu = {
            "kgv": _positiv(roh.get("trailingPE")),
            "kgv_erwartet": _positiv(roh.get("forwardPE")),
            "eps": roh.get("trailingEps"),
            "sector": roh.get("sector"),
        }
    except Exception:
        return alt or {}
    cache_schreiben(schluessel, neu)
    return neu


def _urteil(kgv: float | None, eps, sektor_kgv: float | None) -> str | None:
    if kgv is None:
        return "verlust" if isinstance(eps, (int, float)) and eps < 0 else None
    if sektor_kgv is None:
        return None
    faktor = kgv / sektor_kgv
    return "billig" if faktor < BILLIG_UNTER else "teuer" if faktor > TEUER_UEBER else "normal"


def bewerte(tickers: list[str]) -> dict:
    """Gibt je Ticker KGV, Sektor-KGV und das Urteil billig, normal, teuer oder verlust zurück."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        werte = dict(zip(tickers, pool.map(_kennzahlen, tickers)))
        etf_von = {t: SEKTOR_ETF.get(w.get("sector")) for t, w in werte.items()}
        etfs = sorted({e for e in etf_von.values() if e})
        sektoren = dict(zip(etfs, pool.map(_kennzahlen, etfs)))

    ergebnis = {}
    for ticker, w in werte.items():
        etf = etf_von[ticker]
        sektor_kgv = sektoren.get(etf, {}).get("kgv")
        ergebnis[ticker] = {
            "kgv": w.get("kgv"),
            "kgv_erwartet": w.get("kgv_erwartet"),
            "sektor": SEKTOREN.get(etf),
            "sektor_kgv": sektor_kgv,
            "urteil": _urteil(w.get("kgv"), w.get("eps"), sektor_kgv),
        }
    return ergebnis
