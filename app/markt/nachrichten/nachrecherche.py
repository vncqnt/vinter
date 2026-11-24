"""Nachrecherche, Teil 1: Firmen-News von Finnhub je Ticker (Stufe 3, Medien).

Welche Ticker? Was gerade gebraucht wird:
- eigene US-Werte (Depot, Watchlist): die Benutzerschicht meldet sie mit beobachten() an
- die größten Indexbeweger: die Markttreiber fragen sie direkt mit firmen_news() ab
Jeder Ticker wird höchstens alle 5 Minuten abgefragt (Finnhub erlaubt 60 Anfragen je Minute, Vinter bleibt
mit höchstens einer Anfrage je Sekunde weit darunter). Nur Schlagzeilen, die die Firma nennen, werden übernommen
(Finnhub ordnet einem Wert auch allgemeine Marktberichte zu). Alles läuft durch dieselbe Pipeline wie die
anderen Quellen: Werbung und Klickköder werden dort aussortiert, Treffer zu Geschichten gruppiert.
"""
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

from .. import finnhub
from . import speicher

log = logging.getLogger(__name__)

QUELLE = "Finnhub"
ALLE = 5 * 60            # Sekunden je Ticker
BEOBACHTEN = 26 * 3600   # so lange bleibt ein angemeldeter Ticker in der Liste
HOECHSTENS = 40
_letzte: dict[str, float] = {}
_beobachtet: dict[str, tuple[float, list[str]]] = {}
_sperre = threading.Lock()
_takt = threading.Lock()
_gestartet = [False]


def beobachten(werte: dict[str, list[str]]) -> None:
    """werte: Ticker → Erkennungswörter (Name, Kurzname). Nur US-Ticker ohne Punkt/Bindestrich."""
    jetzt = time.time()
    with _sperre:
        for t, aliase in werte.items():
            if t and "." not in t and "-" not in t:
                _beobachtet[t] = (jetzt, aliase)


def firmen_news(ticker: str, aliase: list[str]) -> int:
    """Finnhub-News eines Tickers holen (höchstens alle 5 Minuten). Gibt die Zahl neuer Meldungen zurück."""
    if not finnhub.verfuegbar() or "." in ticker or "-" in ticker:
        return 0
    with _sperre:
        if time.time() - _letzte.get(ticker, 0) < ALLE:
            return 0
        _letzte[ticker] = time.time()
    with _takt:  # höchstens eine Anfrage je Sekunde
        time.sleep(1.0)
        heute = date.today()
        try:
            roh = finnhub._abruf("company-news", symbol=ticker, **{"from": str(heute - timedelta(days=2)), "to": str(heute)})
            speicher.quelle_melden(QUELLE, True)
        except Exception as f:
            speicher.quelle_melden(QUELLE, False, fehler=type(f).__name__)
            return 0
    muster = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(a) for a in (aliase or [ticker]) if a) + r")(?!\w)", re.I)
    meldungen = []
    for m in (roh or [])[:20]:
        titel = speicher.sauber(m.get("headline"), 500)
        if not titel or not m.get("datetime") or not muster.search(titel):
            continue
        zeit = datetime.fromtimestamp(m["datetime"], timezone.utc).isoformat(timespec="seconds")
        meldungen.append({
            "schluessel": speicher.schluessel(QUELLE, str(m.get("id") or m.get("url") or titel)),
            "quelle": f"{QUELLE} ({m.get('source') or 'Medien'})", "art": "finnhub", "stufe": 3,
            "titel": titel, "text": speicher.sauber(m.get("summary"), 1000), "link": m.get("url") or "", "zeit": zeit,
            # Ältere Artikel hat Vinter nicht live gesehen: gesehen = Zeit laut Quelle
            "gesehen": zeit if zeit < (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat() else None,
        })
    ids = speicher.einfuegen(meldungen)
    if ids:
        from .abruf import melden
        melden(ids)
    return len(ids)


def _schleife():
    while True:
        try:
            grenze = time.time() - BEOBACHTEN
            with _sperre:
                for t in [t for t, (z, _) in _beobachtet.items() if z < grenze]:
                    _beobachtet.pop(t, None)
                liste = sorted(_beobachtet.items(), key=lambda x: -x[1][0])[:HOECHSTENS]
            for t, (_, aliase) in liste:
                firmen_news(t, aliase)
        except Exception as f:
            log.warning("Finnhub-Nachrecherche: %s", f)
        time.sleep(30)


def starte() -> None:
    if _gestartet[0]:
        return
    _gestartet[0] = True
    threading.Thread(target=_schleife, name="newsfeed-finnhub", daemon=True).start()
