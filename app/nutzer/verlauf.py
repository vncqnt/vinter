"""Performance-Verlauf des Depots für den Chart auf der Startseite.

Gerechnet wird eine zeitgewichtete Rendite: Jeden Tag zählt nur, wie sich der Bestand
vom Vorabend entwickelt hat. Einzahlungen und Käufe verändern die Linie deshalb nicht,
sie zeigt nur echte Kursgewinne und -verluste. Ergebnis ist ein Index, Start = 100.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import pandas as pd
import yfinance as yf

from ..benutzer import Benutzer
from ..cache import STUNDE, nutzer_lesen as cache_lesen, nutzer_schreiben as cache_schreiben
from ..markt.boersen import EU, US, boerse_von, ist_offen
from ..markt.euro_kurse import in_euro as _in_euro, tagesreihe as _reihe
from ..markt.kurse import ticker_fuer, waehrung_von, zweitboerse
from .tr_parser import lade_trades

SCHLUESSEL = "depot:verlauf:3"  # :2 seit den deutschen Kursen für US-Aktien


def _zweit(ticker: dict, vorboerse: bool = True) -> dict:
    """Deutsche Notierung je ISIN für US-Aktien. Sie läuft schon, wenn New York noch geschlossen ist."""
    if not vorboerse:
        return {}
    return {i: z for i, t in ticker.items() if boerse_von(t) == US and (z := zweitboerse(t))}


def _juengster(us: pd.Series, de: pd.Series) -> pd.Series:
    """Zu jedem Zeitpunkt der zuletzt gehandelte Kurs, egal ob aus New York oder aus Deutschland."""
    zeiten = us.index.union(de.index)
    zeit_us = pd.Series(us.index, index=us.index).reindex(zeiten).ffill()
    zeit_de = pd.Series(de.index, index=de.index).reindex(zeiten).ffill()
    nimm_de = zeit_de.notna() & (zeit_us.isna() | (zeit_de > zeit_us))
    return us.reindex(zeiten).ffill().where(~nimm_de, de.reindex(zeiten).ffill())


def _berechne(b: Benutzer, vorboerse: bool = True) -> dict:
    trades = lade_trades(b)
    if not trades:
        return {"punkte": [], "tag": None, "stand": None}

    # Optionsscheine haben bei Yahoo keine Kurse und bleiben außen vor.
    arten = {t["isin"]: t["art"] for t in trades}
    ticker = {i: ticker_fuer(i, a) for i, a in arten.items() if a != "DERIVATIVE"}
    ticker = {i: t for i, t in ticker.items() if t}

    erster = trades[0]["zeit"].tz_convert("Europe/Berlin").normalize().tz_localize(None)
    start = (erster - pd.Timedelta(days=10)).date().isoformat()
    zweit = _zweit(ticker, vorboerse)
    abfragen = {**ticker, **{("de", i): z for i, z in zweit.items()}}
    with ThreadPoolExecutor(max_workers=4) as pool:
        reihen = dict(zip(abfragen, pool.map(lambda t: _reihe(t, start), abfragen.values())))
    euro = _in_euro(reihen, start)
    # Tage, an denen New York noch keinen Schlusskurs hat (z. B. heute vor 15:30), nimmt der deutsche Kurs.
    for isin in zweit:
        de, us = euro.pop(("de", isin), None), euro.get(isin)
        if de is not None and us is not None and len(us):
            euro[isin] = pd.concat([us, de[de.index > us.index[-1]]])
    if not euro:
        raise ValueError("keine Kurse")

    # Nur Werktage, sonst gäbe es am Wochenende lauter Nulltage.
    tage = pd.bdate_range(erster, date.today())
    roh = pd.DataFrame({i: s.reindex(tage) for i, s in euro.items()})
    preise = pd.DataFrame({i: s.reindex(s.index.union(tage)).ffill().reindex(tage) for i, s in euro.items()})

    # Bestand am Ende jedes Tages. Wochenend-Trades zählen am nächsten Werktag.
    aenderung = pd.DataFrame(0.0, index=tage, columns=preise.columns)
    for t in trades:
        if t["isin"] in aenderung.columns:
            tag = t["zeit"].tz_convert("Europe/Berlin").normalize().tz_localize(None)
            pos = tage.searchsorted(tag)
            if pos < len(tage):
                aenderung.iloc[pos, aenderung.columns.get_loc(t["isin"])] += t["stueck"]
    bestand = aenderung.cumsum()
    bestand = bestand.where(bestand.abs() > 1e-6, 0.0)
    vorher = bestand.shift(1)

    # Hat heute noch kaum eine Börse geöffnet, zählt der letzte vollständige Tag.
    ende = len(tage)
    while ende > 2:
        gehalten = vorher.iloc[ende - 1] > 0
        if gehalten.sum() and (roh.iloc[ende - 1][gehalten].notna().sum() / gehalten.sum()) >= 0.5:
            break
        ende -= 1
    tage, preise, vorher = tage[:ende], preise.iloc[:ende], vorher.iloc[:ende]

    gestern = (vorher * preise.shift(1)).where(vorher > 0)
    heute = (vorher * preise).where(vorher > 0)
    gueltig = gestern.notna() & heute.notna()
    basis, neu = gestern.where(gueltig).sum(axis=1), heute.where(gueltig).sum(axis=1)
    rendite = (neu / basis - 1).where(basis > 0, 0.0).fillna(0.0)
    index = 100 * (1 + rendite).cumprod()
    # Gewinn in Euro: Summe der täglichen Wertänderung des gehaltenen Bestands. Käufe und Einzahlungen
    # verschieben die Linie so nicht, genau wie beim Prozentverlauf.
    euro = (neu - basis).where(basis > 0, 0.0).fillna(0.0).cumsum()

    return {
        "punkte": [{"datum": d.date().isoformat(), "wert": round(float(w), 3), "eur": round(float(e), 2)} for (d, w), e in zip(index.items(), euro)],
        "tag": float(rendite.iloc[-1]) * 100,
        "stand": tage[-1].date().isoformat(),
    }


STUNDEN_SCHLUESSEL = "depot:verlauf:stunden:2"


def _stunden_reihen(ticker: dict, vorboerse: bool = True) -> dict:
    """Stundenkurse der letzten zwei Monate je ISIN in Euro, in deutscher Zeit."""
    zweit = _zweit(ticker, vorboerse)
    ticker = {**ticker, **{("de", i): z for i, z in zweit.items()}}
    alle = list(ticker.values())
    tabelle = yf.download(alle, period="2mo", interval="60m", auto_adjust=False, progress=False, group_by="ticker", threads=True)
    roh = {}
    for isin, t in ticker.items():
        try:
            reihe = tabelle[t]["Close"].dropna()
        except KeyError:
            continue
        if len(reihe):
            reihe = reihe.tz_convert("Europe/Berlin").tz_localize(None)
            roh[isin] = reihe[~reihe.index.duplicated(keep="last")]

    # Umrechnung in Euro mit dem stündlichen Wechselkurs, die Währung kommt aus den Tageskursen.
    einheit = lambda w: ("GBP", 0.01) if w in ("GBp", "GBX") else (w.upper(), 1.0)
    # Deutsche Notierungen sind immer in Euro, auch wenn ihr Kurs noch nie einzeln abgefragt wurde.
    waehrung = {isin: waehrung_von(t) or ("EUR" if isinstance(isin, tuple) else None) for isin, t in ticker.items() if isin in roh}
    fremd = sorted({einheit(w)[0] for w in waehrung.values() if w} - {"EUR"})
    kurse = {}
    if fremd:
        fx = yf.download([f"{w}EUR=X" for w in fremd], period="2mo", interval="60m", auto_adjust=False, progress=False, group_by="ticker")
        for w in fremd:
            try:
                reihe = fx[f"{w}EUR=X"]["Close"].dropna().tz_convert("Europe/Berlin").tz_localize(None)
                kurse[w] = reihe[~reihe.index.duplicated(keep="last")]
            except KeyError:
                pass
    euro = {}
    for isin, reihe in roh.items():
        if not waehrung.get(isin):
            continue
        name, faktor = einheit(waehrung[isin])
        if name == "EUR":
            euro[isin] = reihe * faktor
        elif name in kurse:
            fx = kurse[name]
            euro[isin] = reihe * faktor * fx.reindex(fx.index.union(reihe.index)).ffill().reindex(reihe.index)
    for isin in zweit:
        de, us = euro.pop(("de", isin), None), euro.get(isin)
        if de is not None and us is not None:
            euro[isin] = _juengster(us.dropna(), de.dropna())
    return euro


def _berechne_stunden(b: Benutzer, vorboerse: bool = True) -> dict:
    """Wie der Tagesverlauf, aber Stunde für Stunde: Jede Stunde zählt nur, wie sich der Bestand
    seit der vorigen Stunde entwickelt hat. Nachts und am Wochenende gibt es keine Punkte."""
    trades = lade_trades(b)
    arten = {t["isin"]: t["art"] for t in trades}
    ticker = {i: ticker_fuer(i, a) for i, a in arten.items() if a != "DERIVATIVE"}
    ticker = {i: t for i, t in ticker.items() if t}
    euro = _stunden_reihen(ticker, vorboerse)
    # Die Zeitachse geben die Börsen vor; Krypto handelt rund um die Uhr und würde Nachtstunden erzeugen.
    boersen = [s for i, s in euro.items() if arten.get(i) != "CRYPTO"]
    if not boersen:
        raise ValueError("keine Stundenkurse")
    zeiten = boersen[0].index
    for s in boersen[1:]:
        zeiten = zeiten.union(s.index)
    preise = pd.DataFrame({i: s.reindex(s.index.union(zeiten)).ffill().reindex(zeiten) for i, s in euro.items()})

    aenderung = pd.DataFrame(0.0, index=zeiten, columns=preise.columns)
    vorher_stueck = pd.Series(0.0, index=preise.columns)
    for t in trades:
        if t["isin"] not in aenderung.columns:
            continue
        zeit = t["zeit"].tz_convert("Europe/Berlin").tz_localize(None)
        pos = zeiten.searchsorted(zeit)
        if pos == 0:
            vorher_stueck[t["isin"]] += t["stueck"]
        elif pos < len(zeiten):
            aenderung.iloc[pos, aenderung.columns.get_loc(t["isin"])] += t["stueck"]
    bestand = aenderung.cumsum() + vorher_stueck
    bestand = bestand.where(bestand.abs() > 1e-6, 0.0)
    vorher = bestand.shift(1).fillna(vorher_stueck)

    gestern = (vorher * preise.shift(1)).where(vorher > 0)
    jetzt = (vorher * preise).where(vorher > 0)
    gueltig = gestern.notna() & jetzt.notna()
    basis, neu = gestern.where(gueltig).sum(axis=1), jetzt.where(gueltig).sum(axis=1)
    rendite = (neu / basis - 1).where(basis > 0, 0.0).fillna(0.0)
    index = 100 * (1 + rendite).cumprod()
    return {
        "punkte": [{"zeit": z.strftime("%Y-%m-%dT%H:%M"), "wert": round(float(w), 4)} for z, w in index.items()],
        "aktualisiert": datetime.now(timezone.utc).isoformat(),
    }


def stunden_verlauf(b: Benutzer, vorboerse: bool = True) -> dict:
    schluessel = STUNDEN_SCHLUESSEL if vorboerse else f"{STUNDEN_SCHLUESSEL}:ohne-vorboerse"
    alt, alter = cache_lesen(b, schluessel)
    max_alter = 4 * 60 if ist_offen(EU) or ist_offen(US) else STUNDE
    if alt and alter < max_alter:
        return alt
    try:
        neu = _berechne_stunden(b, vorboerse)
    except Exception:
        return alt or {"fehler": "Der Stundenverlauf konnte gerade nicht berechnet werden."}
    cache_schreiben(b, schluessel, neu)
    return neu


def depot_verlauf(b: Benutzer, vorboerse: bool = True) -> dict:
    schluessel = SCHLUESSEL if vorboerse else f"{SCHLUESSEL}:ohne-vorboerse"
    alt, alter = cache_lesen(b, schluessel)
    if alt and alter < STUNDE:
        return alt
    try:
        neu = _berechne(b, vorboerse)
    except Exception:
        return alt or {"fehler": "Der Depot-Verlauf konnte gerade nicht berechnet werden."}
    cache_schreiben(b, schluessel, neu)
    return neu
