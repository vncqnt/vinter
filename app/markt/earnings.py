"""Earnings-Termine der nächsten 4 Wochen für eigene und verbundene Werte.

Yahoo ist die Hauptquelle, weil es Erwartungen und Uhrzeiten für alle Werte liefert.
FMP kennt im Gratis-Tarif nur wenige Werte und dient zum Gegenprüfen der Termine.
Beides wird höchstens einmal pro Tag abgefragt.
Gemeldete Zahlen von US-Werten kommen von Finnhub, weil Yahoo sie oft erst Stunden später hat.
"""
import json
import math
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from ..einstellungen import key as einstellung
from . import finnhub
from .kurse import STUNDE, TAG, cache_lesen, cache_schreiben
from .news import hole_google

# Eine Schlagzeile zählt nur, wenn sie vom Ergebnis handelt und keine Vorschau darauf ist.
THEMA_ZAHLEN = re.compile(r"quartal|\bzahlen\b|umsatz|gewinn|prognose|\bq[1-4]\b|earnings|results|revenue|profit|guidance|\bbeats?\b|\bmiss(es)?\b", re.I)
VORSCHAU = re.compile(
    r"preview|what to expect|ahead of|expected to|set to|to report|will report|before earnings"
    r"|\bvor\s+(den\s+)?(quartals)?zahlen|zahlen-\w+|\bprognose\s+\d{4}|kursziel|wartet auf|steht bevor|\bam\s+(montag|dienstag|mittwoch|donnerstag|freitag)\b",
    re.I,
)
NAMENS_ENDE = re.compile(r"\s+(Technology|Technologies|Semiconductors?|Health|Group|Inc\.?|AG|SE)$", re.I)

TAGE_VORAUS = 28
TAGE_ZURUECK = 7  # so lange bleiben gemeldete Zahlen im Kalender sichtbar
FINNHUB_INTERVALL = 15 * 60
US_AUF, US_ZU = 9 * 60 + 30, 16 * 60  # Handelszeiten in New Yorker Zeit
# Finnhub nennt nur den Zeitabschnitt: Stunde in New Yorker Zeit und Bezeichnung.
FINNHUB_SITZUNG = {"bmo": (6, "vor Börse"), "dmh": (12, "während Börse"), "amc": (16, "nach Börse")}


def _zahl(wert):
    """Yahoo liefert manchmal None oder NaN. Beides wird zu None."""
    try:
        z = float(wert)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(z) else z


def _uhrzeit(aktie, ticker: str, tag: date) -> dict:
    """Sucht die Uhrzeit zum Termin und ordnet sie vor/nach Börse ein.

    Nur für US-Werte: Bei europäischen Tickern (mit Punkt) nennt Yahoo für künftige
    Termine immer 17:00 Uhr, das ist nachweislich ein Platzhalter.
    """
    if "." in ticker:
        return {}
    try:
        zeiten = aktie.get_earnings_dates(limit=8).index
    except Exception:
        return {}
    for zeit in zeiten:
        ort = zeit.tz_convert("America/New_York")
        if ort.date() != tag or (ort.hour == 0 and ort.minute == 0):
            continue
        minuten = ort.hour * 60 + ort.minute
        sitzung = "vor Börse" if minuten < US_AUF else "nach Börse" if minuten >= US_ZU else "während Börse"
        return {"zeit_de": zeit.tz_convert("Europe/Berlin").strftime("%H:%M"), "sitzung": sitzung}
    return {}


def _termin_yahoo(ticker: str) -> tuple[dict | None, bool]:
    """Gibt (Termin, Abruf fehlgeschlagen?) zurück."""
    schluessel = f"earnings:{ticker}"
    alt, alter = cache_lesen(schluessel)
    if alt is not None and alter < TAG:
        return alt, False
    try:
        aktie = yf.Ticker(ticker)
        kalender = aktie.calendar
    except Exception:
        return alt, True
    if not kalender:
        return alt, True

    neu = {}
    tage = kalender.get("Earnings Date") or []
    if tage:
        neu = {
            "datum": tage[0].isoformat(),
            "zeitraum": len(tage) > 1,  # Yahoo nennt dann nur eine Spanne
            "eps": _zahl(kalender.get("Earnings Average")),
            "umsatz": _zahl(kalender.get("Revenue Average")),
            "eps_spanne": [_zahl(kalender.get("Earnings Low")), _zahl(kalender.get("Earnings High"))],
            "umsatz_spanne": [_zahl(kalender.get("Revenue Low")), _zahl(kalender.get("Revenue High"))],
            **_uhrzeit(aktie, ticker, tage[0]),
        }
    cache_schreiben(schluessel, neu)
    return neu, False


def _termine_fmp(meldungen: list[str]) -> dict:
    """Gibt {Ticker: Zeile} aus dem FMP-Kalender zurück."""
    alt, alter = cache_lesen("fmp:kalender")
    if alt is not None and alter < TAG:
        return alt
    key = einstellung("FMP_API_KEY")
    if not key:
        meldungen.append("Kein FMP-Key in der .env gefunden. Die Termine stammen nur von Yahoo.")
        return alt or {}
    heute = date.today()
    abfrage = urllib.parse.urlencode({
        "from": heute.isoformat(),
        "to": (heute + timedelta(days=TAGE_VORAUS)).isoformat(),
        "apikey": key,
    })
    try:
        url = f"https://financialmodelingprep.com/stable/earnings-calendar?{abfrage}"
        with urllib.request.urlopen(url, timeout=30) as antwort:
            zeilen = json.loads(antwort.read())
        neu = {z["symbol"]: z for z in zeilen}
    except Exception:
        meldungen.append("FMP ist gerade nicht erreichbar. Die Termine stammen nur von Yahoo.")
        return alt or {}
    cache_schreiben("fmp:kalender", neu)
    return neu


def _gueltigkeit(eintraege: list) -> int:
    """Einmal am Tag reicht, außer ein Ergebnis ist gerade fällig: dann stündlich nachsehen."""
    jetzt = datetime.now(timezone.utc)
    for e in eintraege:
        abstand = datetime.fromisoformat(e["zeit"]) - jetzt
        if e["ist"] is None and timedelta(days=-2) <= abstand <= timedelta(days=1):
            return STUNDE
    return TAG


def _ergebnisse(ticker: str) -> list:
    schluessel = f"ergebnisse:{ticker}"
    alt, alter = cache_lesen(schluessel)
    if alt is not None and alter < _gueltigkeit(alt):
        return alt
    try:
        tabelle = yf.Ticker(ticker).get_earnings_dates(limit=8)
        neu = [
            {
                "zeit": zeit.isoformat(),
                "erwartet": _zahl(z["EPS Estimate"]),
                "ist": _zahl(z["Reported EPS"]),
                "ueberraschung": _zahl(z["Surprise(%)"]),
            }
            for zeit, z in tabelle.iterrows()
        ]
    except Exception:
        return alt or []
    cache_schreiben(schluessel, neu)
    return neu


def _abweichung(ist, erwartet):
    return (ist - erwartet) / abs(erwartet) * 100 if ist is not None and erwartet else None


def _finnhub_ergebnisse(ticker: str) -> list:
    """Gemeldete Zahlen der letzten Tage. Fällt Finnhub aus, bleibt der letzte Stand."""
    schluessel = f"finnhub_ergebnisse:{ticker}"
    alt, alter = cache_lesen(schluessel)
    if alt is not None and alter < FINNHUB_INTERVALL:
        return alt
    heute = date.today()
    zeilen = finnhub.earnings(ticker, (heute - timedelta(days=TAGE_ZURUECK)).isoformat(), heute.isoformat())
    if zeilen is None:
        return alt or []
    neu = []
    for z in zeilen:
        ist, erwartet = _zahl(z.get("epsActual")), _zahl(z.get("epsEstimate"))
        if ist is None:
            continue
        stunde, sitzung = FINNHUB_SITZUNG.get(z.get("hour"), (12, None))
        umsatz, umsatz_erwartet = _zahl(z.get("revenueActual")), _zahl(z.get("revenueEstimate"))
        neu.append({
            "zeit": pd.Timestamp(f"{z['date']} {stunde:02d}:00", tz="America/New_York").isoformat(),
            "sitzung": sitzung,
            "erwartet": erwartet,
            "ist": ist,
            "ueberraschung": _abweichung(ist, erwartet),
            "umsatz": umsatz,
            "umsatz_erwartet": umsatz_erwartet,
            "umsatz_ueberraschung": _abweichung(umsatz, umsatz_erwartet),
        })
    cache_schreiben(schluessel, neu)
    return neu


def frische_ergebnisse(namen: dict, stunden: int = 48) -> list[dict]:
    """Bereits gemeldete Zahlen der letzten Stunden für {Ticker: Name}."""
    # Ein Punkt im Ticker heißt Nicht-US-Börse, die kennt Finnhub im Gratis-Tarif nicht.
    us_werte = [t for t in namen if "." not in t] if finnhub.verfuegbar() else []
    with ThreadPoolExecutor(max_workers=4) as pool:
        yahoo = dict(zip(namen, pool.map(_ergebnisse, namen)))
        von_finnhub = dict(zip(us_werte, pool.map(_finnhub_ergebnisse, us_werte)))

    jetzt = datetime.now(timezone.utc)
    frisch = []
    for ticker, eintraege in yahoo.items():
        fh = von_finnhub.get(ticker, [])
        tage = {e["zeit"][:10] for e in fh}
        for e in fh + [e for e in eintraege if e["zeit"][:10] not in tage]:
            alter = jetzt - datetime.fromisoformat(e["zeit"])
            # Die Uhrzeit ist oft nur geschätzt. Ein Ist-Wert beweist, dass die Zahlen da sind.
            if e["ist"] is not None and timedelta(days=-1) <= alter <= timedelta(hours=stunden):
                frisch.append({"ticker": ticker, "name": namen[ticker], **e})
    return frisch


def ergebnis_meldungen(ticker: str, name: str, ab: pd.Timestamp) -> list[dict]:
    """Schlagzeilen zu gerade gemeldeten Zahlen, als Überbrückung bis die Daten-Quellen sie haben."""
    schluessel = f"ergebnis_news:{ticker}:{ab.date().isoformat()}"
    alt, alter = cache_lesen(schluessel)
    if alt is not None and alter < FINNHUB_INTERVALL:
        return alt
    kurz = NAMENS_ENDE.sub("", name)
    try:
        if "." in ticker:
            roh = hole_google(f'"{kurz}" Quartalszahlen', "de", "DE", "DE:de")
        else:
            roh = hole_google(f'"{kurz}" earnings', "en-US", "US", "US:en")
    except Exception:
        return alt or []
    neu = [
        m for m in roh
        if kurz.casefold() in m["titel"].casefold() and THEMA_ZAHLEN.search(m["titel"])
        and not VORSCHAU.search(m["titel"]) and datetime.fromisoformat(m["zeit"]) >= ab
    ]
    neu.sort(key=lambda m: m["zeit"], reverse=True)
    neu = neu[:4]
    cache_schreiben(schluessel, neu)
    return neu


def meldungen_zum_termin(t: dict) -> list[dict]:
    """Schlagzeilen für einen heutigen Termin ohne Zahlen, erst ab seiner Uhrzeit."""
    ab = pd.Timestamp(f"{t['datum']} {t.get('zeit_de') or '00:00'}", tz="Europe/Berlin") - pd.Timedelta(minutes=30)
    if pd.Timestamp.now(tz="Europe/Berlin") < ab:
        return []
    return ergebnis_meldungen(t["ticker"], t["name"], ab)


def termine_fuer(namen: dict) -> tuple[list[dict], list[str]]:
    """Termine der nächsten 4 Wochen für {Ticker: Name}. Gibt (Termine, Meldungen) zurück."""
    meldungen: list[str] = []
    fmp = _termine_fmp(meldungen)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ergebnisse = dict(zip(namen, pool.map(_termin_yahoo, namen)))

    fehlgeschlagen = [namen[t] for t, (_, fehler) in ergebnisse.items() if fehler]
    if fehlgeschlagen:
        meldungen.append(f"Yahoo hat gerade nicht geantwortet für: {', '.join(fehlgeschlagen)}.")

    heute = date.today()
    bis = heute + timedelta(days=TAGE_VORAUS)
    termine = []
    for ticker, (yahoo, _) in ergebnisse.items():
        f = fmp.get(ticker)
        if yahoo and yahoo.get("datum"):
            t = dict(yahoo)
            if f and f["date"] != t["datum"]:
                t["abweichung"] = f["date"]
        elif f:
            t = {"datum": f["date"], "eps": _zahl(f.get("epsEstimated")), "umsatz": _zahl(f.get("revenueEstimated"))}
        else:
            continue
        if heute.isoformat() <= t["datum"] <= bis.isoformat():
            t.update(ticker=ticker, name=namen[ticker])
            termine.append(t)
    return termine, meldungen
