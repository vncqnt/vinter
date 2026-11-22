"""Themen-Ranking: Wie sich die Aktien eines Themas entwickelt haben, verglichen mit S&P 500 und Nasdaq 100.

Themen und Werte stehen in daten/markt/themen.yaml. Die Kurse kommen gebündelt von Yahoo
(Tagesschlusskurse, an offenen Börsen inklusive des laufenden Tages), in Blöcken zu je 60 Tickern.
Während eine Börse offen ist, höchstens alle 5 Minuten neu, sonst stündlich. Finnhub wird hier
nicht gefragt, damit dessen Minutenlimit für die eigenen Depotwerte frei bleibt.
Prozentwerte rechnen in der Handelswährung der Aktie, Wechselkurse spielen keine Rolle.
"""
import re
from datetime import date, datetime, timezone

import pandas as pd
import yaml
import yfinance as yf

from ..cache import STUNDE, markt_lesen as cache_lesen, markt_schreiben as cache_schreiben
from ..konfig import MARKT_DATEN
from .boersen import EU, US, ist_offen

DATEI = MARKT_DATEN / "themen.yaml"
LOGOS = MARKT_DATEN / "logos"
LOGO_ENDUNGEN = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".webp": "image/webp"}
VERGLEICH = {"^GSPC": "S&P 500", "^NDX": "Nasdaq 100"}
BLOCK = 60
SCHLUESSEL = "themen:kurse"
ZEITRAEUME = ("tag", "woche", "monat", "ytd")


class ThemenFehler(Exception):
    """Die Themen-Datei fehlt oder ist kaputt."""


def lade_themen() -> list[dict]:
    try:
        roh = yaml.safe_load(DATEI.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise ThemenFehler("Die Datei daten/markt/themen.yaml fehlt.")
    except yaml.YAMLError:
        raise ThemenFehler("daten/markt/themen.yaml ist nicht lesbar. Prüfe Einrückung und Doppelpunkte.")
    themen = []
    for eintrag in roh.get("themen") or []:
        werte = eintrag.get("werte") or {}
        if eintrag.get("name") and isinstance(werte, dict) and werte:
            themen.append({"name": str(eintrag["name"]), "werte": {str(t).strip(): str(n) for t, n in werte.items()}})
    if not themen:
        raise ThemenFehler("In daten/markt/themen.yaml steht noch kein Thema mit Werten.")
    return themen


def _prozent(wert: float, schluss: pd.Series, bis: pd.Timestamp) -> float | None:
    """Veränderung seit dem letzten Schlusskurs am oder vor dem Stichtag."""
    davor = schluss[schluss.index <= bis]
    return (wert / float(davor.iloc[-1]) - 1) * 100 if len(davor) else None


def _veraenderung(schluss: pd.Series) -> dict | None:
    if len(schluss) < 2:
        return None
    letzter = schluss.index[-1]
    wert = float(schluss.iloc[-1])
    return {
        "tag": (wert / float(schluss.iloc[-2]) - 1) * 100,
        "woche": _prozent(wert, schluss, letzter - pd.Timedelta(days=7)),
        "monat": _prozent(wert, schluss, letzter - pd.DateOffset(months=1)),
        "ytd": _prozent(wert, schluss, letzter.replace(month=1, day=1) - pd.Timedelta(days=1)),
        "stand": letzter.date().isoformat(),
    }


def _hole(tickers: list[str]) -> dict:
    """Veränderungen aller Ticker, blockweise in gebündelten Abfragen."""
    heute = date.today()
    # Genug Vorlauf für "seit Jahresbeginn" (Schluss des Vorjahres) und "Monat".
    start = min(date(heute.year - 1, 12, 15), (pd.Timestamp(heute) - pd.Timedelta(days=45)).date())
    ergebnis = {}
    for i in range(0, len(tickers), BLOCK):
        block = tickers[i:i + BLOCK]
        try:
            tabelle = yf.download(block, start=start.isoformat(), interval="1d", auto_adjust=False,
                                  progress=False, group_by="ticker", threads=True)
        except Exception:
            continue
        for t in block:
            try:
                schluss = tabelle[t]["Close"].dropna()
            except KeyError:
                continue
            schluss.index = pd.DatetimeIndex(schluss.index).tz_localize(None).normalize()
            daten = _veraenderung(schluss[~schluss.index.duplicated(keep="last")])
            if daten:
                ergebnis[t] = daten
    return ergebnis


def _kurse(tickers: list[str]) -> tuple[dict, bool, float | None]:
    """Aus dem Cache, solange er frisch genug ist. Liefert Daten, ob sie veraltet sind, und ihr Alter."""
    alt, alter = cache_lesen(SCHLUESSEL)
    max_alter = 5 * 60 if ist_offen(EU) or ist_offen(US) else STUNDE
    # Neu holen, wenn zu alt oder wenn in der Datei neue Ticker dazugekommen sind.
    if alt and alter < max_alter and set(tickers) <= set(alt["abgefragt"]):
        return alt["werte"], False, alter
    neu = _hole(tickers)
    if not neu:
        return (alt["werte"], True, alter) if alt else ({}, True, None)
    cache_schreiben(SCHLUESSEL, {"werte": neu, "abgefragt": tickers})
    return neu, False, 0.0


def _schnitt(werte: list[dict]) -> dict:
    """Gleichgewichteter Durchschnitt je Zeitraum über alle Werte mit Daten."""
    ergebnis = {}
    for z in ZEITRAEUME:
        zahlen = [w[z] for w in werte if w.get(z) is not None]
        ergebnis[z] = sum(zahlen) / len(zahlen) if zahlen else None
    return ergebnis


def logo_pfad(ticker: str):
    """Pfad zu einem selbst abgelegten Logo, oder None. Nur einfache Dateinamen sind erlaubt."""
    if not re.fullmatch(r"[A-Za-z0-9.\-^=]{1,20}", ticker):
        return None
    for endung in LOGO_ENDUNGEN:
        pfad = LOGOS / f"{ticker}{endung}"
        if pfad.is_file():
            return pfad
    return None


def themen_ranking() -> dict:
    themen = lade_themen()
    alle = sorted({t for th in themen for t in th["werte"]} | set(VERGLEICH))
    kurse, veraltet, alter = _kurse(alle)
    ergebnis = []
    for th in themen:
        werte = [{"ticker": t, "name": n, **{z: kurse.get(t, {}).get(z) for z in ZEITRAEUME}} for t, n in th["werte"].items()]
        ergebnis.append({"name": th["name"], "schnitt": _schnitt(werte), "werte": werte})
    staende = [k["stand"] for k in kurse.values() if k.get("stand")]
    aktualisiert = None if alter is None else datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - alter, timezone.utc).isoformat()
    return {
        "themen": ergebnis,
        "vergleich": [{"ticker": t, "name": n, **{z: kurse.get(t, {}).get(z) for z in ZEITRAEUME}} for t, n in VERGLEICH.items()],
        "ohne_daten": sorted(t for t in alle if t not in kurse),
        "logos": sorted(p.stem for p in LOGOS.glob("*") if p.suffix.lower() in LOGO_ENDUNGEN) if LOGOS.is_dir() else [],
        "stand": max(staende) if staende else None,
        "aktualisiert": aktualisiert,
        "veraltet": veraltet,
    }
