"""Tägliche Schlusskurse in Euro, für alle Benutzer gemeinsam zwischengespeichert.

Wird von Verlauf, Risiko, Benchmark, Stresstest und Makro-Sensitivität gebraucht. Weil es reine
Marktdaten sind, liegen sie im gemeinsamen Cache: Jede Aktie wird nur einmal abgefragt, egal
wie viele Benutzer sie im Depot haben.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from ..cache import STUNDE, markt_lesen as cache_lesen, markt_schreiben as cache_schreiben


def tagesreihe(ticker: str, start: str):
    """Tägliche Schlusskurse und Währung, oder None wenn Yahoo nichts liefert."""
    try:
        t = yf.Ticker(ticker)
        schluss = t.history(start=start, auto_adjust=False)["Close"].dropna()
        waehrung = t.history_metadata.get("currency")
    except Exception:
        return None
    if schluss.empty or not waehrung:
        return None
    schluss.index = schluss.index.tz_localize(None).normalize()
    return schluss[~schluss.index.duplicated(keep="last")], waehrung


def in_euro(reihen: dict, start: str) -> dict:
    """Rechnet alle Kursreihen mit dem Wechselkurs des jeweiligen Tages in Euro um."""
    einheit = lambda w: ("GBP", 0.01) if w in ("GBp", "GBX") else (w.upper(), 1.0)
    fremd = sorted({einheit(r[1])[0] for r in reihen.values() if r} - {"EUR"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        kurse = dict(zip(fremd, pool.map(lambda w: tagesreihe(f"{w}EUR=X", start), fremd)))

    euro = {}
    for schluessel, r in reihen.items():
        if not r:
            continue
        schluss, waehrung = r
        name, faktor = einheit(waehrung)
        if name == "EUR":
            euro[schluessel] = schluss * faktor
        elif kurse.get(name):
            euro[schluessel] = schluss * faktor * kurse[name][0].reindex(schluss.index, method="ffill")
    return euro


def euro_kurse(ticker: list[str], tage: int = 400, start: str | None = None) -> dict[str, pd.Series]:
    """Tägliche Schlusskurse in Euro je Ticker, eine Stunde im Cache.

    start: fester Anfangstag statt "tage zurück", z. B. für den Stresstest ab 2019.
    """
    schluessel = f"kurse-eur:{start or tage}:" + ",".join(sorted(ticker))
    alt, alter = cache_lesen(schluessel)
    if alt and alter < STUNDE:
        return {t: pd.Series(w, index=pd.to_datetime(list(d))) for t, (d, w) in ((t, zip(*v)) for t, v in alt.items())}
    start = start or (date.today() - timedelta(days=tage)).isoformat()
    with ThreadPoolExecutor(max_workers=4) as pool:
        reihen = dict(zip(ticker, pool.map(lambda t: tagesreihe(t, start), ticker)))
    euro = in_euro(reihen, start)
    cache_schreiben(schluessel, {t: [[d.date().isoformat(), float(w)] for d, w in s.dropna().items()] for t, s in euro.items()})
    return euro
