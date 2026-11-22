"""Verbindet den Live-Newsfeed mit dem Depot: markiert Meldungen zu eigenen Positionen und Thesen.

Der Feed selbst (app/markt/squawk.py) kennt kein Depot. Hier entsteht die Liste der US-Werte,
für die Finnhub und die SEC abgefragt werden, und die Wörter, an denen man sie erkennt.
"""
import re

from ..benutzer import Benutzer
from ..markt import squawk
from ..markt.kurse import ticker_fuer
from . import thesen
from .portfolio import berechne
from .tr_parser import lade_trades

POSITIONEN = "Meine Positionen"
# Erste Wörter, die allein zu viel treffen würden ("Siemens" ist nicht Siemens Energy).
ZU_ALLGEMEIN = {"siemens", "sharon", "core", "physical", "ftse", "general", "american", "first"}
# Ticker, die auch normale englische Wörter sind, werden nicht als Erkennungswort benutzt.
TICKER_WOERTER = {"NOW", "ALL", "ON", "IT", "ARE", "CAT", "KEY", "LOW", "BIG", "FAST"}


def _aliase(name: str, ticker: str | None) -> list[str]:
    aliase = [name]
    erstes = name.split()[0]
    if len(name.split()) > 1 and len(erstes) >= 4 and erstes.lower() not in ZU_ALLGEMEIN:
        aliase.append(erstes)
    if ticker and "." not in ticker and "-" not in ticker and len(ticker) >= 3 and ticker not in TICKER_WOERTER:
        aliase.append(ticker)
    return aliase


def _depot(b: Benutzer) -> list[dict]:
    """Aktien und Krypto im Depot mit Ticker und Erkennungswörtern. ETFs bleiben außen vor."""
    try:
        positionen = berechne(lade_trades(b))["positionen"]
    except Exception:
        return []
    werte = []
    for p in positionen:
        if p["art"] not in ("STOCK", "CRYPTO"):
            continue
        ticker = ticker_fuer(p["isin"], p["art"])
        werte.append({"name": p["name"], "ticker": ticker, "aliase": _aliase(p["name"], ticker)})
    return werte


def _themen_muster(b: Benutzer) -> list[tuple[str, re.Pattern]]:
    """News-Themen aus den Thesen. Ein Thema trifft, wenn alle seine längeren Wörter in der Schlagzeile stehen."""
    muster = []
    for t in thesen.alle_thesen(b).get("thesen", []):
        for thema in t.get("news_themen") or []:
            woerter = [w for w in re.findall(r"\w+", thema) if len(w) >= 4]
            if woerter:
                muster.append((thema, [re.compile(rf"(?<!\w){re.escape(w)}", re.IGNORECASE) for w in woerter]))
    return muster


def newsfeed(b: Benutzer) -> dict:
    depot = _depot(b)
    us = {w["ticker"]: {"name": w["name"], "aliase": w["aliase"]}
          for w in depot if w["ticker"] and "." not in w["ticker"] and "-" not in w["ticker"]}
    daten = squawk.feed(us)

    eigene = [(w, re.compile(r"(?<!\w)(?:" + "|".join(re.escape(a) for a in w["aliase"]) + r")(?!\w)",
                             re.IGNORECASE)) for w in depot]
    themen = _themen_muster(b)
    wichtig_eigene = squawk.muster_wichtig_eigene()
    for m in daten["meldungen"]:
        treffer = [w["name"] for w, muster in eigene if w["ticker"] in m["ticker"] or muster.search(m["titel"])]
        if treffer:
            m["positionen"] = treffer
            m["tags"] = sorted(set(m["tags"]) | {POSITIONEN})
            m["wichtig"] = m["wichtig"] or bool(wichtig_eigene.search(m["titel"]))
        thesen_treffer = [thema for thema, teile in themen if all(t.search(m["titel"]) for t in teile)]
        if thesen_treffer:
            m["thesen"] = thesen_treffer
    daten["tags"] = ["Wichtig", POSITIONEN, "Makro", "Notenbank", "Earnings", "Geopolitik", "Rohstoffe"]
    return daten
