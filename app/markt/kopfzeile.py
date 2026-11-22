"""Was die Leiste oben zusätzlich zeigt: die größten Kursbewegungen des Tages."""
import re
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf
from yfinance import EquityQuery

from .kurse import FUENF_MINUTEN, cache_lesen, cache_schreiben

# Mindest-Börsenwert in der jeweiligen Währung, damit keine Kleinstwerte die Liste füllen.
MAERKTE = (("us", 10e9), ("de", 2e9))
_RECHTSFORM = re.compile(
    r"[,\s]+(Inc\.?|Incorporated|Corp\.?|Corporation|Company|Co\.?|Ltd\.?|Limited|plc|PLC|AG|SE|N\.?V\.?|S\.?A\.?|KGaA|Holdings?|Group)\b.*$"
)


def _name(z: dict) -> str:
    voll = (z.get("longName") or z.get("shortName") or z["symbol"]).strip()
    return _RECHTSFORM.sub("", voll) or voll


def _abfrage(region: str, mindestwert: float, aufsteigend: bool) -> list:
    filter_ = EquityQuery("and", [
        EquityQuery("eq", ["region", region]),
        EquityQuery("gt", ["intradaymarketcap", mindestwert]),
        EquityQuery("gt", ["dayvolume", 100_000]),
    ])
    return yf.screen(filter_, sortField="percentchange", sortAsc=aufsteigend, size=100)["quotes"]


def _berechne_mover() -> list:
    auftraege = [(r, w, a) for r, w in MAERKTE for a in (False, True)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        teile = list(pool.map(lambda x: _abfrage(*x), auftraege))

    werte = {}
    for z in (z for teil in teile for z in teil):
        if z.get("regularMarketChangePercent") is None or z.get("fullExchangeName", "").startswith("OTC"):
            continue
        if z["symbol"].endswith(".F") or z.get("region") == "DE" and z.get("exchange") != "GER":
            continue  # in Deutschland nur Xetra, sonst steht jeder Wert mehrfach in der Liste
        werte[z["symbol"]] = z
    alle = list(werte.values())
    # Solange irgendwo gehandelt wird, zählen nur offene Börsen. Die übrigen zeigen noch den Vortag.
    offen = [z for z in alle if z.get("marketState") == "REGULAR"]

    # Yahoos Sortierung ist nur grob, deshalb wird hier genau sortiert.
    sortiert = sorted(offen or alle, key=lambda z: z["regularMarketChangePercent"], reverse=True)
    eindeutig, gesehen = [], set()
    for z in sorted(sortiert, key=lambda z: "." in z["symbol"]):  # US-Notierung gewinnt bei Doppelten
        wort = _name(z).split()[0].casefold()
        if wort not in gesehen:
            gesehen.add(wort)
            eindeutig.append(z)
    eindeutig.sort(key=lambda z: z["regularMarketChangePercent"], reverse=True)

    eintrag = lambda z: {"ticker": z["symbol"], "name": _name(z), "prozent": z["regularMarketChangePercent"]}
    gewinner = [z for z in eindeutig if z["regularMarketChangePercent"] > 0][:2]
    verlierer = [z for z in reversed(eindeutig) if z["regularMarketChangePercent"] < 0][:2]
    return [eintrag(z) for z in gewinner + verlierer]


def top_mover() -> list:
    alt, alter = cache_lesen("kopf:mover")
    if alt and alter < FUENF_MINUTEN:
        return alt
    try:
        neu = _berechne_mover()
        if not neu:
            raise ValueError("keine Daten")
    except Exception:
        return alt or []
    cache_schreiben("kopf:mover", neu)
    return neu

