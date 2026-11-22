"""Earnings-Termine des ganzen Marktes: alle großen Firmen (ab 20 Mrd. $ Börsenwert), nicht nur die eigenen.

Quelle ist der Earnings-Kalender von nasdaq.com (wie beim Earnings-Barometer): je Tag alle US-notierten
Firmen mit Zahlen, Börsenwert, Uhrzeit (vor/nach Börse) und EPS-Konsens. Je Tag 6 Stunden gemerkt.
"""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from .abruf import STUNDE, gecacht, hole

MINDEST_BOERSENWERT = 20e9
SITZUNG = {"time-pre-market": "vor Börse", "time-after-hours": "nach Börse"}


def _zahl(text):
    try:
        return float(re.sub(r"[$,]", "", str(text)).replace("(", "-").replace(")", ""))
    except (TypeError, ValueError):
        return None


def _tag(tag: date) -> list[dict]:
    def holen():
        d = hole(f"https://api.nasdaq.com/api/calendar/earnings?date={tag.isoformat()}").json()
        return ((d.get("data") or {}).get("rows")) or []
    try:
        return gecacht(f"markt_termine:{tag.isoformat()}", 6 * STUNDE, holen)
    except Exception:
        return []


def markt_termine(tage: int = 28, mindest: float = MINDEST_BOERSENWERT) -> dict:
    heute = date.today()
    tage_liste = [heute + timedelta(days=i) for i in range(tage + 1) if (heute + timedelta(days=i)).weekday() < 5]
    with ThreadPoolExecutor(3) as pool:  # nasdaq.com bremst sonst
        je_tag = dict(zip(tage_liste, pool.map(_tag, tage_liste)))
    termine = []
    for tag, zeilen in je_tag.items():
        for z in zeilen:
            wert = _zahl(z.get("marketCap"))
            if not wert or wert < mindest:
                continue
            termine.append({"ticker": z["symbol"].replace(".", "-"), "name": z.get("name") or z["symbol"], "datum": tag.isoformat(),
                            "sitzung": SITZUNG.get(z.get("time")), "zeit_de": None, "eps": _zahl(z.get("epsForecast")), "umsatz": None,
                            "boersenwert": wert, "markt": True, "eigen": False, "bezug": []})
    termine.sort(key=lambda t: (t["datum"], -t["boersenwert"]))
    fehlt = sum(1 for z in je_tag.values() if not z)
    return {"termine": termine, "mindest": mindest, "tage_ohne_daten": fehlt}
