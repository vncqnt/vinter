"""Täglicher Abruf für die Fundamentalanalyse (Punkt 24c, B6 und C4).

Einmal am Tag nach US-Börsenschluss (ab 22:30 Uhr deutscher Zeit, beim Start nachgeholt, wenn der Tag fehlt):
- Gewinn- und Umsatzschätzungen aller S&P-500-Werte, aller Peers und aller angesehenen Werte speichern
  (fundament.sqlite). Yahoo nennt zusätzlich die Schätzung von vor 30 und 90 Tagen; daraus der Schätzungs-Trend.
- Bei 6 gleichzeitigen Anfragen dauert das gut 5 Minuten.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import yfinance as yf

from . import speicher as SP

_gestartet = False
_status = {"laeuft": False, "letzter": None, "fehler": None, "anzahl": 0}


def status() -> dict:
    return dict(_status)


def _einer(ticker: str, sektor: str | None, im_index: bool, tag: str) -> dict | None:
    try:
        a = yf.Ticker(ticker)
        i = a.info or {}
        trend = a.eps_trend
        umsatz = a.revenue_estimate
    except Exception:  # noqa: BLE001
        return None
    def zelle(df, zeile, spalte):
        try:
            v = float(df.at[zeile, spalte])
            return None if v != v else v
        except Exception:  # noqa: BLE001
            return None
    return {"ticker": ticker, "tag": tag, "sektor": sektor or i.get("sector"), "im_index": int(im_index), "boersenwert": i.get("marketCap"),
            "kurs": i.get("currentPrice") or i.get("regularMarketPrice"),
            "eps0y": zelle(trend, "0y", "current"), "eps1y": zelle(trend, "+1y", "current"),
            "eps1y_30": zelle(trend, "+1y", "30daysAgo"), "eps1y_90": zelle(trend, "+1y", "90daysAgo"),
            "umsatz0y": zelle(umsatz, "0y", "avg"), "umsatz1y": zelle(umsatz, "+1y", "avg"),
            "fwd_kgv": i.get("forwardPE"), "eps_ttm": i.get("trailingEps")}


def lauf() -> None:
    from ..indizes import mitglieder
    from .peers import _lesen
    tag = date.today().isoformat()
    _status.update(laeuft=True, fehler=None)
    try:
        sp = {m["ticker"]: m.get("sektor") for m in mitglieder("sp500")}
        andere = set(SP.beobachtete()) | {t for g in _lesen().values() for t in g["werte"]}
        auftraege = [(t, s, True) for t, s in sp.items()] + [(t, None, False) for t in sorted(andere - set(sp))]
        with ThreadPoolExecutor(6) as pool:
            zeilen = [z for z in pool.map(lambda x: _einer(x[0], x[1], x[2], tag), auftraege) if z]
        SP.schaetzung_speichern(zeilen)
        _status.update(letzter=datetime.now().isoformat(timespec="seconds"), anzahl=len(zeilen))
    except Exception as f:  # noqa: BLE001
        _status["fehler"] = f"{type(f).__name__}: {f}"[:200]
    finally:
        _status["laeuft"] = False


def _schleife() -> None:
    time.sleep(90)  # Start nicht ausbremsen
    while True:
        jetzt = datetime.now()
        # Fehlt der heutige Stand und ist es nach 22:30 Uhr – oder fehlt sogar der von gestern –, laufen
        fehlt_heute = SP.tag_vorhanden() < 300
        letzte = SP.letzte_schaetzungen(index_nur=True)
        alt = not letzte or letzte[0]["tag"] < date.fromordinal(date.today().toordinal() - 1).isoformat()
        if fehlt_heute and (jetzt.strftime("%H:%M") >= "22:30" or alt) and not _status["laeuft"]:
            lauf()
        time.sleep(15 * 60)


def starte() -> None:
    global _gestartet
    if _gestartet:
        return
    _gestartet = True
    threading.Thread(target=_schleife, name="fundament", daemon=True).start()
