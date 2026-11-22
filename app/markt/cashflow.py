"""Seite "Cashflow": Free Cashflow, Investitionen und Ausschüttungen großer US-Firmen.

Quelle ist die offizielle Datenbank der SEC ("companyfacts"): alle Zahlen aus den Quartals- und
Jahresberichten seit etwa 2009. Die Kapitalflussrechnung steht dort meist als Summe seit
Geschäftsjahresbeginn (6 Monate, 9 Monate, Jahr). Daraus werden einzelne Quartale zurückgerechnet:
z. B. 4. Quartal = Jahr minus die ersten drei Quartale.

Free Cashflow (FCF) = operativer Cashflow minus Investitionen in Sachanlagen (Capex).
"""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd
import yaml

from ..konfig import MARKT_DATEN
from .abruf import TAG, gecacht, hole

DATEI = MARKT_DATEN / "cashflow_firmen.yaml"
BEGRIFFE = {
    "ocf": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets", "PaymentsForCapitalImprovements",
              "PaymentsToAcquireOtherPropertyPlantAndEquipment"],
    "umsatz": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
               "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "rueckkauf": ["PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity"],
    "dividende": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
}
JAHRE = 12


def firmen() -> dict[str, list[str]]:
    try:
        return yaml.safe_load(DATEI.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def cik_liste() -> dict:
    def holen():
        d = hole("https://www.sec.gov/files/company_tickers.json", sec=True).json()
        return {z["ticker"].upper(): {"cik": z["cik_str"], "name": z["title"]} for z in d.values()}
    return gecacht("sec:ticker", 7 * TAG, holen)


def _tag(s: str) -> date:
    return date.fromisoformat(s)


def _quartale(fakten: list[dict]) -> dict[str, float]:
    """{Kalenderquartal: Wert} aus Angaben mit beliebiger Dauer (3, 6, 9 oder 12 Monate seit
    Geschäftsjahresbeginn). Je Zeitraum zählt die erste Veröffentlichung: spätere Berichte rechnen
    Vorjahre oft um (z. B. nach Abspaltungen), und alte und neue Zahlen passen dann nicht zusammen."""
    eindeutig = {}
    for f in fakten:
        if "start" not in f:
            continue
        k = (f["start"], f["end"])
        if k not in eindeutig or f.get("filed", "") < eindeutig[k].get("filed", ""):
            eindeutig[k] = f
    dauer = lambda f: (_tag(f["end"]) - _tag(f["start"])).days
    q = {}  # Ende -> (Start, Wert)
    for f in eindeutig.values():
        if 80 <= dauer(f) <= 100:
            q[f["end"]] = (f["start"], f["val"])
    lang = sorted((f for f in eindeutig.values() if dauer(f) >= 150), key=dauer)
    for _ in range(3):
        for f in lang:
            if f["end"] in q:
                continue
            drin = [(e, s, v) for e, (s, v) in q.items() if s >= f["start"] and e < f["end"]]
            n = round(dauer(f) / 91)
            if n >= 2 and len(drin) == n - 1:
                start = (_tag(max(e for e, _, _ in drin)) + timedelta(days=1)).isoformat()
                q[f["end"]] = (start, f["val"] - sum(v for _, _, v in drin))
    # Kalenderquartal: Firmen mit abweichendem Geschäftsjahr (z. B. Ende Januar) auf das nächste Quartalsende
    return {str(pd.Period(_tag(e) - timedelta(days=20), freq="Q")): v for e, (s, v) in q.items()}


def _reihe(gaap: dict, begriffe: list[str]) -> dict[str, float]:
    """Quartalswerte über mehrere mögliche Begriffe (Firmen wechseln sie manchmal)."""
    aus = {}
    for b in begriffe:
        einheiten = gaap.get(b, {}).get("units", {}).get("USD")
        if not einheiten:
            continue
        for e, v in _quartale(einheiten).items():
            aus.setdefault(e, v)
    return aus


def firma(ticker: str) -> dict:
    def holen():
        info = cik_liste().get(ticker.upper())
        if not info:
            raise LookupError(f"{ticker} nicht bei der SEC gefunden")
        d = hole(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{info['cik']:010d}.json", sec=True, zeitlimit=60).json()
        gaap = d.get("facts", {}).get("us-gaap", {})
        reihen = {k: _reihe(gaap, b) for k, b in BEGRIFFE.items()}
        grenze = str(date.today().year - JAHRE)
        schluessel = sorted(k for k in set(reihen["ocf"]) & set(reihen["capex"]) if k >= grenze)
        quartale = {k: {k2: (round(reihen[k2][k] / 1e9, 3) if k in reihen[k2] else None) for k2 in BEGRIFFE} for k in schluessel}
        for z in quartale.values():
            z["fcf"] = round(z["ocf"] - z["capex"], 3)
        return {"ticker": ticker.upper(), "name": info["name"].title(), "quartale": quartale}
    return gecacht(f"cashflow:{ticker.upper()}", TAG, holen)


def _ttm(quartale: dict) -> dict:
    """Summe der letzten 4 Quartale je Kalenderquartal (nur wenn alle 4 vorhanden sind)."""
    schluessel = sorted(quartale)
    aus = {}
    for i, kq in enumerate(schluessel):
        if i < 3:
            continue
        vier = [str(pd.Period(kq, freq="Q") - k) for k in range(4)]
        if not all(v in quartale for v in vier):
            continue
        aus[kq] = {k: (round(sum(quartale[v][k] for v in vier), 2) if all(quartale[v][k] is not None for v in vier) else None)
                   for k in ("ocf", "capex", "fcf", "umsatz", "rueckkauf", "dividende")}
    return aus


def cashflow_seite() -> dict:
    gruppen = firmen()
    alle = sorted({t for liste in gruppen.values() for t in liste})
    with ThreadPoolExecutor(4) as pool:
        ergebnisse = dict(zip(alle, pool.map(lambda t: _sicher(t), alle)))
    caps = _boersenwerte(alle)
    sektoren = []
    for sektor, liste in gruppen.items():
        werte = []
        for t in liste:
            f = ergebnisse.get(t)
            if not f or "fehler" in f:
                werte.append({"ticker": t, "fehler": (f or {}).get("fehler", "keine Daten")})
                continue
            ttm = _ttm(f["quartale"])
            werte.append({"ticker": t, "name": f["name"], "ttm": ttm, "quartale": f["quartale"], "cap": caps.get(t)})
        sektoren.append({"name": sektor, "firmen": werte})
    return {"sektoren": sektoren, "quelle": "SEC EDGAR (companyfacts), Werte in Mrd. US-Dollar"}


def _sicher(ticker: str) -> dict:
    try:
        return firma(ticker)
    except Exception as f:
        return {"fehler": f"Keine SEC-Daten ({type(f).__name__})"}


def _boersenwerte(ticker: list[str]) -> dict:
    """Börsenwert in Mrd. $ je Ticker: aus den S&P-500-Kennzahlen, sonst einzeln von Yahoo."""
    from ..cache import markt_lesen
    import yfinance as yf
    try:
        sp = markt_lesen("kennzahlen:sp500")[0] or {}
    except Exception:
        sp = {}

    def einer(t):
        if sp.get(t, {}).get("cap"):
            return t, sp[t]["cap"] / 1e9
        try:
            return t, gecacht(f"cap:{t}", TAG, lambda: float(yf.Ticker(t).fast_info["market_cap"])) / 1e9
        except Exception:
            return t, None
    with ThreadPoolExecutor(4) as pool:
        return {t: (round(c, 1) if c else None) for t, c in pool.map(einer, ticker)}
