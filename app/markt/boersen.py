"""Weiß, welche Börse gerade offen ist, inklusive Zeitumstellung, Feiertagen und verkürzten Tagen."""
from functools import lru_cache

import exchange_calendars as xc
import pandas as pd

US = "US"
EU = "EU"
_KALENDER = {US: "XNYS", EU: "XETR"}  # New York und Xetra


@lru_cache(maxsize=None)
def _kalender(boerse: str):
    return xc.get_calendar(_KALENDER[boerse])


def boerse_von(ticker: str) -> str | None:
    """Ordnet einen Yahoo-Ticker einer Börse zu. Krypto handelt rund um die Uhr: None."""
    if ticker.endswith(("-EUR", "-USD")):
        return None
    if ticker.endswith("=X"):  # Wechselkurse werden gemeinsam mit den Europa-Werten geholt
        return EU
    return EU if "." in ticker else US


def ist_aktuelle_sitzung(boerse: str | None, stand: str) -> bool:
    """Gehört ein Kurs zum aktuellen Handelstag? Vor Handelsbeginn zählt der Vortag nicht mehr."""
    if boerse is None:  # Krypto handelt immer
        return True
    try:
        kalender = _kalender(boerse)
        heute = pd.Timestamp.now(tz=kalender.tz).tz_localize(None).normalize()
        return stand >= kalender.date_to_session(heute, direction="previous").date().isoformat()
    except Exception:
        return False  # lieber kein Alarm als ein falscher


def ist_offen(boerse: str | None) -> bool:
    if boerse is None:
        return False
    try:
        return bool(_kalender(boerse).is_open_on_minute(pd.Timestamp.now(tz="UTC").floor("min")))
    except Exception:  # z. B. Datum außerhalb des Kalenders: lieber seltener abfragen
        return False
