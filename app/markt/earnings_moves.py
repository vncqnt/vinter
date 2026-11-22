"""Earnings-Moves: Wie stark hat sich eine Aktie an ihren letzten Quartalszahlen bewegt,
und welche Bewegung erwartet der Optionsmarkt für den nächsten Termin?

Kursreaktion: Kamen die Zahlen vor Börsenbeginn oder während des Handels, zählt der Schlusskurs
desselben Tages gegen den Vortag. Kamen sie nach Börsenschluss, zählt der nächste Handelstag.
Ohne Uhrzeit wird ein Zwei-Tage-Fenster genommen (Vortag bis Tag danach), damit nichts fehlt.
Gerechnet in der Handelswährung, damit Wechselkurse die Reaktion nicht verfälschen.

Erwartete Bewegung (nur US-Werte, nur dort gibt es bei Yahoo Optionen): Preis eines Straddles
(Call plus Put zum Strike am nächsten am Kurs) mit dem ersten Verfall nach dem Termin, geteilt
durch den Kurs. So viel Bewegung in beide Richtungen ist "eingepreist".
"""
import math
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

from ..cache import STUNDE, TAG, markt_lesen as cache_lesen, markt_schreiben as cache_schreiben

ANZAHL = 8
# Börsenzeiten in Ortszeit: (Zeitzone, Beginn in Minuten, Schluss in Minuten)
ZEITEN = {"US": ("America/New_York", 9 * 60 + 30, 16 * 60), "EU": ("Europe/Berlin", 9 * 60, 17 * 60 + 30)}


def _zahl(wert) -> float | None:
    try:
        z = float(wert)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(z) else z


def _reaktion(schluss: pd.Series, zeitpunkt: pd.Timestamp, ort: str) -> dict | None:
    """Kursreaktion auf einen Termin. schluss: Tagesschlusskurse mit Datum als Index (ohne Zeitzone)."""
    tz, auf, zu = ZEITEN[ort]
    lokal = zeitpunkt.tz_convert(tz)
    tag = pd.Timestamp(lokal.date())
    minuten = lokal.hour * 60 + lokal.minute
    tage = schluss.index
    if minuten == 0:  # keine Uhrzeit bekannt
        art, vorher_bis, nachher_ab = "fenster", tag - pd.Timedelta(days=1), tag + pd.Timedelta(days=1)
    elif minuten >= zu:
        art, vorher_bis, nachher_ab = "nach Börse", tag, tag + pd.Timedelta(days=1)
    else:
        art, vorher_bis, nachher_ab = "vor Börse" if minuten < auf else "während Börse", tag - pd.Timedelta(days=1), tag
    vorher = schluss[tage <= vorher_bis]
    nachher = schluss[tage >= nachher_ab]
    if vorher.empty or nachher.empty or (nachher.index[0] - vorher.index[-1]).days > 6:
        return None  # Kurse fehlen rund um den Termin
    return {
        "datum": tag.date().isoformat(),
        "zeitpunkt": art,
        "prozent": float(nachher.iloc[0] / vorher.iloc[-1] - 1) * 100,
        "eps_ueberraschung": None,
    }


def reaktionen(ticker: str) -> dict:
    """Die letzten Kursreaktionen und der nächste Termin, einen Tag im Cache."""
    schluessel = f"earnings-moves:{ticker}"
    alt, alter = cache_lesen(schluessel)
    if alt is not None and alter < TAG:
        return alt
    try:
        aktie = yf.Ticker(ticker)
        termine = aktie.get_earnings_dates(limit=ANZAHL + 6)
        schluss = aktie.history(period="4y", auto_adjust=False)["Close"].dropna()
    except Exception:
        return alt or {"fehler": "keine Daten"}
    if termine is None or termine.empty or schluss.empty:
        return alt or {"fehler": "keine Termine"}
    schluss.index = pd.DatetimeIndex(schluss.index).tz_localize(None).normalize()
    jetzt = pd.Timestamp.now(tz="UTC")
    ort = "EU" if "." in ticker else "US"

    vergangen, naechster = [], None
    for zeitpunkt, zeile in termine.sort_index().iterrows():
        if zeitpunkt > jetzt:
            naechster = naechster or {"datum": zeitpunkt.tz_convert(ZEITEN[ort][0]).date().isoformat(),
                                      "eps_erwartet": _zahl(zeile.get("EPS Estimate"))}
            continue
        r = _reaktion(schluss, zeitpunkt, ort)
        if r:
            r["eps_ueberraschung"] = _zahl(zeile.get("Surprise(%)"))
            vergangen.append(r)
    vergangen = vergangen[-ANZAHL:]
    neu = {"reaktionen": vergangen, "naechster": naechster}
    cache_schreiben(schluessel, neu)
    return neu


def _mitte(zeile) -> float | None:
    bid, ask = _zahl(zeile.get("bid")), _zahl(zeile.get("ask"))
    if bid and ask and ask >= bid:
        return (bid + ask) / 2
    return _zahl(zeile.get("lastPrice"))


def _straddle(aktie, verfall: date, kurs: float) -> float | None:
    """Preis von Call plus Put zum Strike am nächsten am Kurs, in Prozent vom Kurs."""
    kette = aktie.option_chain(verfall.isoformat())
    strike = min(kette.calls["strike"], key=lambda k: abs(k - kurs))
    call = kette.calls[kette.calls["strike"] == strike]
    put = kette.puts[kette.puts["strike"] == strike]
    if call.empty or put.empty:
        return None
    preis_call, preis_put = _mitte(call.iloc[0]), _mitte(put.iloc[0])
    return (preis_call + preis_put) / kurs * 100 if preis_call and preis_put else None


def erwartete_bewegung(ticker: str, termin: str) -> dict | None:
    """Vom Optionsmarkt eingepreiste Bewegung bis kurz nach dem Termin. Nur US-Werte, eine Stunde im Cache."""
    if "." in ticker or "-" in ticker:
        return None
    schluessel = f"earnings-straddle3:{ticker}:{termin}"  # 3: Handelstage, keine Zahl ohne saubere Trennung
    alt, alter = cache_lesen(schluessel)
    if alt is not None and alter < STUNDE:
        return alt or None
    neu = {}
    try:
        aktie = yf.Ticker(ticker)
        kurs = _zahl(aktie.fast_info.get("lastPrice"))
        # Erster Verfall nach dem Termin (bei Zahlen nach Börsenschluss zählt erst der Folgetag).
        tag = date.fromisoformat(termin)
        verfaelle = [date.fromisoformat(v) for v in aktie.options]
        nach = next((v for v in verfaelle if v > tag), None)
        vor = next((v for v in reversed(verfaelle) if date.today() < v < tag), None)
        gesamt = _straddle(aktie, nach, kurs) if kurs and nach else None
        if gesamt:
            # Der Straddle nach dem Termin enthält auch die normale Schwankung bis zum Verfall.
            # Mit einem Verfall vor dem Termin lässt sie sich herausrechnen (Varianz wächst mit der Zeit):
            # Termin-Anteil = Wurzel(nach² − vor² · Tage_nach / Tage_vor).
            nur_termin = None
            normal = _straddle(aktie, vor, kurs) if vor else None
            if normal:
                handelstage = lambda bis: max(1, int(np.busday_count(date.today(), bis)))
                t_nach, t_vor = handelstage(nach), handelstage(vor)
                rest = gesamt ** 2 - normal ** 2 * t_nach / t_vor
                nur_termin = math.sqrt(rest) if rest > 0 else None
            # Ohne sauberen Termin-Anteil zählt der ganze Straddle nur, wenn der Verfall direkt nach dem
            # Termin liegt und bis dahin kaum normale Schwankung dazukommt. Sonst lieber keine Zahl.
            nah = (tag - date.today()).days <= 10
            prozent = nur_termin if nur_termin is not None else (gesamt if nah else None)
            neu = {"prozent": prozent, "bereinigt": nur_termin is not None,
                   "straddle": gesamt, "verfall": nach.isoformat(), "tage_nach_termin": (nach - tag).days,
                   "stand": datetime.now(timezone.utc).isoformat()}
    except Exception:
        return alt or None
    cache_schreiben(schluessel, neu)
    return neu or None


def chart_termine(ticker: str) -> dict:
    """Alle Quartalstermine der letzten Jahre und der nächste, für die Markierungen unten im Chart der Chartanalyse.

    Je Termin: Datum (Ortszeit der Börse), vor/nach Börse, EPS erwartet und gemeldet, Überraschung und die
    Kursreaktion (Schluss vor dem Termin bis Schluss danach, wie bei den Earnings-Moves). Einen Tag im Cache."""
    schluessel = f"earnings-chart:{ticker}"
    alt, alter = cache_lesen(schluessel)
    if alt is not None and alter < TAG:
        return alt
    try:
        aktie = yf.Ticker(ticker)
        termine = aktie.get_earnings_dates(limit=28)
        schluss = aktie.history(period="8y", auto_adjust=False)["Close"].dropna()
    except Exception:
        return alt or {"termine": []}
    if termine is None or termine.empty:
        return alt or {"termine": []}
    schluss.index = pd.DatetimeIndex(schluss.index).tz_localize(None).normalize()
    jetzt = pd.Timestamp.now(tz="UTC")
    ort = "EU" if "." in ticker else "US"
    aus = []
    for zeitpunkt, zeile in termine.sort_index().iterrows():
        lokal = zeitpunkt.tz_convert(ZEITEN[ort][0])
        r = _reaktion(schluss, zeitpunkt, ort) if zeitpunkt <= jetzt and not schluss.empty else None
        aus.append({
            "datum": lokal.date().isoformat(), "zukunft": bool(zeitpunkt > jetzt),
            "sitzung": r["zeitpunkt"] if r else None,
            "eps_erwartet": _zahl(zeile.get("EPS Estimate")), "eps_ist": _zahl(zeile.get("Reported EPS")),
            "ueberraschung": _zahl(zeile.get("Surprise(%)")), "reaktion": r["prozent"] if r else None,
        })
    neu = {"termine": aus}
    cache_schreiben(schluessel, neu)
    return neu
