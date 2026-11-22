"""Namen zu Tickern (für Werte, die nicht im Depot liegen), 30 Tage gemerkt.

Bekannte Gruppen (Sektor-ETFs, Leitindizes) stehen fest hier, alles andere fragt Yahoo einmal ab.
"""
import re

import yfinance as yf

from .abruf import gecacht

TAG = 86400
FEST = {
    "SPY": "S&P 500 (SPY)", "QQQ": "Nasdaq 100 (QQQ)", "IWM": "Russell 2000 (IWM)", "DIA": "Dow Jones (DIA)",
    "^GSPC": "S&P 500", "^NDX": "Nasdaq 100", "^GDAXI": "DAX", "^STOXX50E": "Euro Stoxx 50", "EXS1.DE": "DAX (ETF)",
    "XLK": "Technologie (XLK)", "XLF": "Finanzen (XLF)", "XLV": "Gesundheit (XLV)", "XLE": "Energie (XLE)", "XLI": "Industrie (XLI)",
    "XLY": "Zyklischer Konsum (XLY)", "XLP": "Basiskonsum (XLP)", "XLU": "Versorger (XLU)", "XLB": "Rohstoffe (XLB)",
    "XLRE": "Immobilien (XLRE)", "XLC": "Kommunikation (XLC)", "SMH": "Halbleiter (SMH)", "SOXX": "Halbleiter (SOXX)",
}
TICKER_FORM = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,14}$")


def name(ticker: str) -> str:
    if ticker in FEST:
        return FEST[ticker]

    def holen():
        info = yf.Ticker(ticker).info or {}
        return info.get("shortName") or info.get("longName") or ticker
    try:
        return gecacht(f"name:{ticker}", 30 * TAG, holen)
    except Exception:
        return ticker


def zusatz_werte(extra: str | None, schon: set[str] | None = None, hoechstens: int = 15) -> list[dict]:
    """"?extra=XLK,SPY,AAPL" als Liste von Werten, die Funktionen wie Depot-Positionen behandeln können.
    Markiert mit extra=True und ohne Wert in Euro: Sie erscheinen in Tabellen, zählen aber nicht in Depot-Summen."""
    schon = schon or set()
    aus, gesehen = [], set()
    for t in (extra or "").upper().replace(" ", "").split(","):
        if not t or t in gesehen or t in schon or not TICKER_FORM.match(t):
            continue
        gesehen.add(t)
        aus.append({"ticker": t, "name": name(t), "art": "STOCK", "extra": True, "wert_eur": None, "isin": None,
                    "stueck": 0, "einstand_gesamt": None, "kurs_eur": None, "verkauft": False})
        if len(aus) >= hoechstens:
            break
    return aus
