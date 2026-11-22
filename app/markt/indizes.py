"""Mitglieder großer Indizes und die Kurse aller S&P-500-Werte.

- mitglieder(): S&P 500, DAX und Euro Stoxx 50 aus den Tabellen der Wikipedia, Nasdaq-100 von nasdaq.com.
  Einmal pro Woche neu.
- sp500_kurse(): Schlusskurse aller S&P-500-Werte über 2 Jahre (eine Tabelle, einmal am Tag).
- kennzahlen(): je Index Börsenwert, Forward-KGV und KGV der letzten 12 Monate aller Mitglieder
  (Yahoo, einmal am Tag). Daraus das Forward-KGV des ganzen Index, siehe stil.py.
"""
import io
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf

from .abruf import STUNDE, TAG, gecacht, hole, tabelle_lesen, tabelle_schreiben

INDIZES = {
    "sp500": {"name": "S&P 500", "wiki": "List_of_S%26P_500_companies", "spalte": "Symbol"},
    "ndx": {"name": "Nasdaq 100"},
    "dax": {"name": "DAX", "wiki": "DAX", "spalte": "Ticker"},
    "sx5e": {"name": "Euro Stoxx 50", "wiki": "EURO_STOXX_50", "spalte": "Ticker"},
}
_sperre_kurse = threading.Lock()
_sperre_kgv = {i: threading.Lock() for i in INDIZES}


def _wiki(seite: str, spalte: str) -> list[dict]:
    text = hole(f"https://en.wikipedia.org/wiki/{seite}").text
    for tab in pd.read_html(io.StringIO(text)):
        spalten = [str(c) for c in tab.columns]
        if spalte in spalten and len(tab) >= 30:
            name = next((c for c in ("Security", "Company", "Name") if c in spalten), spalte)
            sektor = next((c for c in ("GICS Sector", "Sector", "Prime Standard Sector") if c in spalten), None)
            zeilen = []
            for _, z in tab.iterrows():
                t = str(z[spalte]).strip()
                if not t or t == "nan":
                    continue
                if seite.startswith("List_of_S"):
                    t = t.replace(".", "-")  # Yahoo schreibt BRK-B statt BRK.B
                zeilen.append({"ticker": t, "name": str(z[name]), "sektor": str(z[sektor]) if sektor else "",
                               "cik": int(z["CIK"]) if "CIK" in spalten and str(z["CIK"]).isdigit() else None})
            return zeilen
    raise ValueError(f"Tabelle {spalte} auf {seite} nicht gefunden")


def _nasdaq100() -> list[dict]:
    d = hole("https://api.nasdaq.com/api/quote/list-type/nasdaq100").json()
    return [{"ticker": z["symbol"].replace(".", "-"), "name": re.sub(r"\s+(Common Stock|Class [A-C].*|Ordinary Shares.*|American Depositary.*)$", "", z["companyName"]),
             "sektor": z.get("sector") or "", "cik": None} for z in d["data"]["data"]["rows"]]


def mitglieder(index: str) -> list[dict]:
    info = INDIZES[index]
    holen = _nasdaq100 if index == "ndx" else (lambda: _wiki(info["wiki"], info["spalte"]))
    return gecacht(f"mitglieder:{index}", 7 * TAG, holen)


def sp500_kurse() -> pd.DataFrame:
    """Schlusskurse (Spalten = Ticker) der letzten 2 Jahre, einmal am Tag neu (dauert beim ersten Mal ~1 Minute)."""
    tab = tabelle_lesen("sp500_kurse", 12 * STUNDE)
    if tab is not None:
        return tab
    with _sperre_kurse:
        tab = tabelle_lesen("sp500_kurse", 12 * STUNDE)
        if tab is not None:
            return tab
        ticker = [m["ticker"] for m in mitglieder("sp500")]
        try:
            roh = yf.download(ticker, period="2y", auto_adjust=True, progress=False, threads=True, group_by="column")
            tab = roh["Close"].dropna(axis=1, how="all")
            # Tage, an denen für viele Werte noch kein Kurs da ist (z. B. heute vor US-Börsenbeginn), weglassen
            tab = tab[tab.notna().sum(axis=1) >= 0.95 * tab.shape[1]]
            if tab.shape[1] < 400:
                raise ValueError("zu wenige Werte geladen")
            tab_alt = tabelle_lesen("sp500_kurse")
            tabelle_schreiben("sp500_kurse", tab)
        except Exception:
            tab_alt = tabelle_lesen("sp500_kurse")
            if tab_alt is None:
                raise
            return tab_alt
        return tab


def _info(ticker: str) -> dict | None:
    try:
        i = yf.Ticker(ticker).info
        umsatz = i.get("totalRevenue")
        dy = i.get("dividendYield")
        return {"cap": i.get("marketCap"), "fwd": i.get("forwardPE"), "ttm": i.get("trailingPE"),
                "eps_fwd": i.get("forwardEps"), "eps_ttm": i.get("trailingEps"), "preis": i.get("currentPrice") or i.get("regularMarketPrice"),
                # für die Fundamentalanalyse (Punkt 24c): Indizes und Sektoren aus ihren Bestandteilen
                "ps": i.get("priceToSalesTrailing12Months"), "ev_ebitda": i.get("enterpriseToEbitda"), "pb": i.get("priceToBook"),
                "gewinn": i.get("netIncomeToCommon"), "umsatz": umsatz, "brutto": i.get("grossProfits"),
                "op_gewinn": umsatz * i["operatingMargins"] if umsatz and i.get("operatingMargins") is not None else None,
                "fcf": i.get("freeCashflow"), "umsatz_wachstum": i.get("revenueGrowth"), "gewinn_wachstum": i.get("earningsGrowth"), "peg": i.get("trailingPegRatio"),
                "roe": i.get("returnOnEquity"), "div": dy / 100 if dy is not None else None,  # Yahoo liefert Prozent
                "yahoo_sektor": i.get("sector"), "branche": i.get("industry")}
    except Exception:
        return None


def kennzahlen(index: str) -> dict:
    """{Ticker: {cap, fwd, ttm, ...}} aller Mitglieder, einmal am Tag (dauert für den S&P 500 einige Minuten)."""
    def holen():
        ticker = [m["ticker"] for m in mitglieder(index)]
        with ThreadPoolExecutor(8) as pool:
            werte = dict(zip(ticker, pool.map(_info, ticker)))
        werte = {t: w for t, w in werte.items() if w and w.get("cap")}
        if len(werte) < 0.8 * len(ticker):
            raise ValueError("zu viele Werte fehlen")
        return werte
    with _sperre_kgv[index]:
        return gecacht(f"kennzahlen:{index}", 20 * STUNDE, holen)
