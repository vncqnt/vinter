"""Earnings Cheat Sheets für die eigenen und die verbundenen Werte mit anstehenden Quartalszahlen."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from ..benutzer import Benutzer
from ..markt.earnings_moves import erwartete_bewegung, reaktionen
from .earnings_moves import OPTIONEN_AB_TAGE
from ..markt.earnings_sheet import sheet
from .kalender import _werte

TAGE_VORAUS = 75  # weiter voraus sind Termine meist nur geschätzt


def earnings_sheets(b: Benutzer, positionen: list[dict]) -> dict:
    eigene, namen, bezug, _ = _werte(b)
    werte = {p.get("ticker"): p.get("wert_eur") for p in positionen if p.get("ticker")}
    # Werte aus dem Markt (extra): auch mit Termin später als 75 Tage; ohne Depot nur sie
    extra = {p["ticker"]: p["name"] for p in positionen if p.get("extra") and p.get("ticker")}
    if not any(not p.get("extra") for p in positionen):
        eigene, namen, bezug = {}, {}, {}
    namen = {**namen, **extra}

    def eins(ticker):
        try:
            s = sheet(ticker)
        except Exception:
            return None
        datum = s.get("termin", {}).get("datum")
        if not datum or datum < date.today().isoformat():
            return None
        tage = (date.fromisoformat(datum) - date.today()).days
        if tage > TAGE_VORAUS and ticker not in extra:
            return None
        aus = {**s, "name": namen.get(ticker) or s["info"].get("name") or ticker, "eigen": ticker in eigene, "extra": ticker in extra,
               "bezug": bezug.get(ticker, []), "wert_eur": werte.get(ticker), "tage": tage}
        try:
            r = reaktionen(ticker).get("reaktionen") or []
            aus["reaktionen"] = r
        except Exception:
            aus["reaktionen"] = []
        if tage <= OPTIONEN_AB_TAGE:
            try:
                aus["erwartet"] = erwartete_bewegung(ticker, datum)
            except Exception:
                aus["erwartet"] = None
        return aus

    with ThreadPoolExecutor(max_workers=6) as pool:
        liste = [x for x in pool.map(eins, list(namen)) if x]
    liste.sort(key=lambda x: (x["termin"]["datum"], not x["eigen"]))
    return {"firmen": liste, "tage_voraus": TAGE_VORAUS, "optionen_ab_tage": OPTIONEN_AB_TAGE}
