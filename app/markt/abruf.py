"""Gemeinsame Bausteine für die Seiten rund um "Großes Geld": Abruf aus dem Netz mit Zwischenspeicher.

- hole(): lädt eine Adresse. Für die SEC mit Kontaktangabe im User-Agent (verlangt die SEC) und
  höchstens etwa 6 Anfragen pro Sekunde (die SEC erlaubt 10).
- gecacht(): merkt sich ein Ergebnis eine Weile. Fällt die Quelle aus, gibt es den letzten bekannten Wert.
- tabelle_*: große Tabellen (z. B. Kurse aller S&P-500-Werte) als Datei statt in der Datenbank.
- fred(): Zeitreihen der St. Louis Fed über die öffentliche CSV-Datei, ohne Key.
- schlusskurse(): Tagesschlusskurse von Yahoo.
"""
import io
import pickle
import threading
import time

import pandas as pd
import requests
import yfinance as yf

from ..cache import STUNDE, TAG, _lesen, _schreiben, markt_lesen, markt_schreiben
from ..einstellungen import key
from ..konfig import CACHE, MARKT_DATEN

BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Vinter/1.0"}
TABELLEN = CACHE / "tabellen"
_sec_sperre = threading.Lock()
_sec_letzt = [0.0]


def hole(url: str, sec: bool = False, zeitlimit: int = 30) -> requests.Response:
    if sec:
        ua = key("SEC_USER_AGENT")
        if not ua:
            raise LookupError("SEC_USER_AGENT fehlt in der .env")
        with _sec_sperre:
            warte = 0.16 - (time.time() - _sec_letzt[0])
            if warte > 0:
                time.sleep(warte)
            _sec_letzt[0] = time.time()
        kopf = {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}
    else:
        kopf = {**BROWSER, "Accept": "application/json, text/plain, */*"}
    antwort = requests.get(url, headers=kopf, timeout=zeitlimit)
    antwort.raise_for_status()
    return antwort


def gecacht(schluessel: str, max_alter: float, holen):
    """Wert aus dem Speicher, wenn jünger als max_alter Sekunden; sonst neu holen."""
    alt, alter = markt_lesen(schluessel)
    if alt is not None and alter < max_alter:
        return alt
    try:
        neu = holen()
    except Exception:
        if alt is not None:
            return alt
        raise
    markt_schreiben(schluessel, neu)
    return neu


# Selbst gesammelte Verläufe (z. B. Forward-KGV, Put/Call), die sich nicht nachladen lassen:
# deshalb bei den Daten und nicht im Cache, der gefahrlos gelöscht werden darf.
HISTORIE = MARKT_DATEN / "historie.sqlite"


def historie_lesen(schluessel: str) -> dict:
    return _lesen(HISTORIE, schluessel)[0] or {}


def historie_eintragen(schluessel: str, tag: str, wert) -> None:
    verlauf = historie_lesen(schluessel)
    verlauf[tag] = wert
    _schreiben(HISTORIE, schluessel, verlauf)


def tabelle_lesen(name: str, max_alter: float | None = None):
    datei = TABELLEN / f"{name}.pkl"
    if not datei.exists():
        return None
    if max_alter is not None and time.time() - datei.stat().st_mtime > max_alter:
        return None
    try:
        return pickle.loads(datei.read_bytes())
    except Exception:
        return None


def tabelle_schreiben(name: str, wert) -> None:
    TABELLEN.mkdir(parents=True, exist_ok=True)
    zwischen = TABELLEN / f"{name}.tmp"
    zwischen.write_bytes(pickle.dumps(wert))
    zwischen.replace(TABELLEN / f"{name}.pkl")


def fred(serie: str) -> pd.Series:
    """Eine FRED-Zeitreihe (z. B. BAMLH0A0HYM2), alle 6 Stunden neu."""
    def holen():
        text = hole(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={serie}&cosd=1950-01-01").text
        tab = pd.read_csv(io.StringIO(text))
        tab.columns = ["datum", "wert"]
        tab["wert"] = pd.to_numeric(tab["wert"], errors="coerce")
        tab = tab.dropna()
        return {str(d)[:10]: float(w) for d, w in zip(tab["datum"], tab["wert"])}
    daten = gecacht(f"fred:{serie}", 6 * STUNDE, holen)
    reihe = pd.Series(daten, dtype=float)
    reihe.index = pd.to_datetime(reihe.index)
    return reihe.sort_index()


def schlusskurse(ticker: str, periode: str = "10y") -> pd.Series:
    """Bereinigte Tagesschlusskurse, alle 6 Stunden neu."""
    def holen():
        h = yf.Ticker(ticker).history(period=periode, auto_adjust=True)["Close"].dropna()
        if h.empty:
            raise ValueError(f"keine Kurse für {ticker}")
        return {d.strftime("%Y-%m-%d"): float(w) for d, w in h.items()}
    daten = gecacht(f"schluss:{ticker}:{periode}", 6 * STUNDE, holen)
    reihe = pd.Series(daten, dtype=float)
    reihe.index = pd.to_datetime(reihe.index)
    return reihe.sort_index()


def als_chart(reihen: dict[str, pd.Series], runden: int = 4) -> dict:
    """Mehrere Zeitreihen auf gemeinsame Tage bringen: {daten: [ISO], werte: {Name: [Zahl|None]}}."""
    tab = pd.concat(reihen, axis=1).sort_index()
    daten = [d.strftime("%Y-%m-%d") for d in tab.index]
    werte = {n: [None if pd.isna(v) else round(float(v), runden) for v in tab[n]] for n in tab.columns}
    return {"daten": daten, "werte": werte}


def perzentil(reihe: pd.Series, wert: float) -> float | None:
    reihe = reihe.dropna()
    return None if reihe.empty else round(float((reihe < wert).mean() * 100), 1)


__all__ = ["historie_lesen", "historie_eintragen", "hole", "gecacht", "tabelle_lesen", "tabelle_schreiben", "fred", "schlusskurse", "als_chart", "perzentil", "STUNDE", "TAG"]
