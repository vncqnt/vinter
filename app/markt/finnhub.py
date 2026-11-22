"""US-Kurse von Finnhub. Der Key kommt aus den Einstellungen und wird im Header statt in der Adresse gesendet."""
import json
import urllib.parse
import urllib.request

import pandas as pd

from ..einstellungen import key


def _key() -> str:
    return key("FINNHUB_API_KEY")


def verfuegbar() -> bool:
    return bool(_key())


def _abruf(pfad: str, **parameter) -> dict:
    adresse = f"https://finnhub.io/api/v1/{pfad}?" + urllib.parse.urlencode(parameter)
    anfrage = urllib.request.Request(adresse, headers={"X-Finnhub-Token": _key(), "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(anfrage, timeout=10) as antwort:
        return json.loads(antwort.read())


def earnings(ticker: str, von: str, bis: str) -> list[dict] | None:
    """Earnings-Einträge im Zeitraum, gemeldete mit Ist-Werten. None, wenn Finnhub ausfällt."""
    try:
        return _abruf("calendar/earnings", **{"from": von, "to": bis, "symbol": ticker})["earningsCalendar"]
    except Exception:
        return None


def kurs(ticker: str) -> dict | None:
    """Letzter Kurs in Dollar, oder None wenn Finnhub gerade nichts liefert."""
    try:
        d = _abruf("quote", symbol=ticker)
        if not d.get("c") or not d.get("t"):  # unbekannte Ticker liefern 0
            return None
        return {
            "preis": float(d["c"]),
            "waehrung": "USD",
            "stand": pd.Timestamp(d["t"], unit="s", tz="UTC").tz_convert("America/New_York").date().isoformat(),
            "quelle": "finnhub",
            "zeitpunkt": int(d["t"]),
        }
    except Exception:
        return None
