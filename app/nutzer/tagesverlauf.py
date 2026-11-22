"""Tagesverlauf des Depots in Prozent, Minute für Minute.

Yahoo liefert kein 10-Minuten-Raster. Deshalb wird aus 1-Minuten-Kursen für jede Minute das
Tagesergebnis des ganzen Depots gerechnet. Die Kerzen baut die Seite daraus selbst. Gerechnet wird wie beim Tageswert: Käufe zählen nicht als Gewinn.
Eine noch geschlossene Börse bleibt auf ihrem letzten Kurs und bewegt sich nur mit dem Wechselkurs.
Ausnahme: US-Aktien mit deutscher Notierung folgen vor der US-Eröffnung dem Kurs in Deutschland.
"""
from bisect import bisect_left
from datetime import datetime, timezone

import pandas as pd

from ..benutzer import Benutzer
from ..cache import STUNDE, nutzer_lesen as cache_lesen, nutzer_schreiben as cache_schreiben
from ..markt.boersen import EU, US, boerse_von, ist_offen
from ..markt.kurse import minutenkurse, ticker_fuer, waehrung_von, zweitboerse
from .perioden import REST, _schlusskurse, letzter_handelstag
from .tr_parser import lade_trades

SCHLUESSEL = "depot:tagesverlauf:minuten"
OFFEN_INTERVALL = 4 * 60  # knapp unter dem 5-Minuten-Takt der Seite


def _auf_minuten(reihe: pd.Series, zeiten: pd.DatetimeIndex) -> pd.Series:
    """Jede Minute der letzte bekannte Kurs. So zählt vor Handelsbeginn der Vortag."""
    return reihe.reindex(reihe.index.union(zeiten)).ffill().reindex(zeiten)


def _einheit(waehrung: str) -> tuple[str, float]:
    return ("GBP", 0.01) if waehrung in ("GBp", "GBX") else (waehrung.upper(), 1.0)


def _berechne(positionen: list[dict], trades: list[dict], vorboerse: bool = True) -> dict:
    tag = letzter_handelstag(positionen)
    info = {t["isin"]: t for t in trades}
    ticker = {i: ticker_fuer(i, t["art"]) for i, t in info.items() if t["art"] != "DERIVATIVE"}
    ticker = {i: t for i, t in ticker.items() if t}
    schlusskurse = _schlusskurse(ticker)

    # Bestand zu Tagesbeginn und Käufe oder Verkäufe am Tag selbst.
    anfang, heute = {}, {}
    for t in trades:
        isin, datum = t["isin"], t["zeit"].date().isoformat()
        if datum < tag:
            anfang[isin] = anfang.get(isin, 0.0) + t["stueck"]
        elif datum == tag:
            heute.setdefault(isin, []).append(t)
    betroffen = [i for i in set(anfang) | set(heute) if anfang.get(i, 0.0) > REST or i in heute]

    waehrungen = {i: waehrung_von(ticker[i]) for i in betroffen if i in ticker}
    gueltig = {i: w for i, w in waehrungen.items() if w}
    fremd = sorted({_einheit(w)[0] for w in gueltig.values()} - {"EUR"})
    fx_ticker = {w: f"{w}EUR=X" for w in fremd}
    zweit = {i: z for i in gueltig if vorboerse and boerse_von(ticker[i]) == US and (z := zweitboerse(ticker[i]))}
    minuten = minutenkurse([ticker[i] for i in gueltig] + list(fx_ticker.values()) + list(zweit.values()))

    # Die Zeitachse läuft von der ersten bis zur letzten Börsenminute. Krypto handelt rund um die Uhr.
    an_dem_tag = [
        r[r.index.strftime("%Y-%m-%d") == tag]
        for i, r in ((i, minuten.get(ticker[i])) for i in gueltig if info[i]["art"] != "CRYPTO") if r is not None
    ]
    an_dem_tag = [r for r in an_dem_tag if len(r)]
    if not an_dem_tag:
        raise ValueError("keine Minutenkurse")
    zeiten = pd.date_range(min(r.index[0] for r in an_dem_tag), max(r.index[-1] for r in an_dem_tag), freq="1min")

    wert = pd.Series(0.0, index=zeiten)
    geldfluss = pd.Series(0.0, index=zeiten)
    startwert = kaeufe = 0.0
    ohne_daten = 0
    for isin in betroffen:
        reihe = minuten.get(ticker.get(isin))
        schluss = schlusskurse.get(isin, {})
        tage = sorted(schluss)
        stelle = bisect_left(tage, tag)
        vortag = schluss[tage[stelle - 1]] if stelle else None
        menge = anfang.get(isin, 0.0) if anfang.get(isin, 0.0) > REST else 0.0
        if reihe is None or isin not in gueltig or (menge and vortag is None):
            ohne_daten += 1
            continue

        name, faktor = _einheit(gueltig[isin])
        preis = _auf_minuten(reihe, zeiten).bfill() * faktor
        if name != "EUR":
            kurs = minuten.get(fx_ticker[name])
            if kurs is None:
                ohne_daten += 1
                continue
            preis = preis * _auf_minuten(kurs, zeiten).bfill()
        deutsch = minuten.get(zweit.get(isin))
        if deutsch is not None:
            deutsch = deutsch[deutsch.index.strftime("%Y-%m-%d") == tag]
            us_heute = reihe[reihe.index.strftime("%Y-%m-%d") == tag]
            if len(deutsch):
                # Bis zum ersten US-Kurs des Tages gilt der deutsche Kurs (schon in Euro).
                vorher = zeiten < (us_heute.index[0] if len(us_heute) else zeiten[-1] + pd.Timedelta(minutes=1))
                ersatz = _auf_minuten(deutsch, zeiten)
                preis = preis.where(~(vorher & ersatz.notna()), ersatz)

        stueck = pd.Series(menge, index=zeiten)
        for t in heute.get(isin, []):
            ab = zeiten >= t["zeit"]
            stueck[ab] += t["stueck"]
            geldfluss[ab] += t["betrag"] - t["gebuehr"]
            if t["stueck"] > 0:
                kaeufe += t["gebuehr"] - t["betrag"]
        wert += stueck.where(stueck > REST, 0.0) * preis
        startwert += menge * vortag if menge else 0.0

    basis = startwert + kaeufe
    if basis <= 0:
        raise ValueError("kein Bestand")
    prozent = (wert + geldfluss - startwert) / basis * 100
    # Jede Minute ein Wert: Die Seite baut daraus Kerzen in der gewählten Länge (5 bis 60 Minuten).
    return {
        "datum": tag,
        "minuten": [{"zeit": z.strftime("%H:%M"), "w": round(float(w), 3)} for z, w in prozent.dropna().items()],
        "ohne_daten": ohne_daten,
        "aktualisiert": datetime.now(timezone.utc).isoformat(),
        "anzahl_trades": len(trades),
    }


def tagesverlauf(b: Benutzer, positionen: list[dict], vorboerse: bool = True) -> dict:
    trades = lade_trades(b)
    schluessel = SCHLUESSEL if vorboerse else f"{SCHLUESSEL}:ohne-vorboerse"
    alt, alter = cache_lesen(b, schluessel)
    max_alter = OFFEN_INTERVALL if ist_offen(EU) or ist_offen(US) else STUNDE
    if alt and alter < max_alter and alt["anzahl_trades"] == len(trades):
        return alt
    try:
        neu = _berechne(positionen, trades, vorboerse)
    except Exception:
        if alt:
            return alt
        raise
    cache_schreiben(b, schluessel, neu)
    return neu
