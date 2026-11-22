"""Der heutige Tag im Minutentakt, so nah wie möglich an der Trade-Republic-Ansicht,
und die Bewegung seit der Eröffnung in New York.

Trade Republic handelt über die LS Exchange etwa von 7:30 bis 23 Uhr. Deren Kurse gibt es nicht
kostenlos. Vinter nimmt deshalb zu jeder Minute den jüngsten Kurs, den es auf einem der frei
verfügbaren Handelsplätze gibt: Heimatbörse (z. B. New York, London, Mailand), Xetra und Frankfurt
(8 bis 22 Uhr), alles in Euro. Vergleichswert ist der letzte Kurs des Vortags, auch wenn der erst
am Abend in Frankfurt oder New York zustande kam. ETFs, die es frei nur in London oder Mailand
gibt, laufen nach 17:30 mit einem US-ETF auf denselben Index weiter (daten/markt/stellvertreter.yaml),
wie bei Trade Republic, wo sie bis 23 Uhr gehandelt werden. Das kommt Trade Republic nahe, ist aber nicht
identisch: Abweichungen von einigen Zehntelprozent sind normal.
"""
from datetime import datetime, timezone

import pandas as pd
import yaml

from ..benutzer import Benutzer
from ..cache import STUNDE, nutzer_lesen as cache_lesen, nutzer_schreiben as cache_schreiben
from ..konfig import MARKT_DATEN
from ..markt.boersen import EU, US, _kalender, boerse_von, ist_offen
from ..markt.kurse import minutenkurse, ticker_fuer, waehrung_von, zweitboerse
from .perioden import REST
from .tr_parser import lade_trades
from .verlauf import _juengster

OFFEN_INTERVALL = 90  # während der Handelszeit höchstens 90 Sekunden alt
TR_BEGINN = 8 * 60  # die Achse beginnt um 8 Uhr wie der Handel in Frankfurt
STELLVERTRETER_DATEI = MARKT_DATEN / "stellvertreter.yaml"


def _plaetze(ticker: str) -> list[str]:
    """Alle frei verfügbaren Handelsplätze eines Werts: Heimatbörse, Xetra, Frankfurt."""
    plaetze = [ticker]
    deutsch = zweitboerse(ticker) if boerse_von(ticker) == US else ticker if ticker.endswith(".DE") else None
    if deutsch:
        plaetze += [deutsch, deutsch[: -len(".DE")] + ".F"] if deutsch.endswith(".DE") else [deutsch]
    return list(dict.fromkeys(plaetze))


def _einheit(waehrung: str) -> tuple[str, float]:
    return ("GBP", 0.01) if waehrung in ("GBp", "GBX") else (waehrung.upper(), 1.0)


def _stellvertreter() -> dict[str, str]:
    try:
        return yaml.safe_load(STELLVERTRETER_DATEI.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _abends_fortfuehren(heimat: pd.Series, ersatz: pd.Series) -> pd.Series:
    """Nach dem letzten Kurs der Heimatbörse eines Tages bewegt sich der Wert wie der Stellvertreter
    (beide in Euro). So zählt der Abendhandel mit, den Trade Republic bis 23 Uhr hat."""
    teile = [heimat]
    for _, tag in heimat.groupby(heimat.index.date):
        ende = tag.index[-1]
        bis = ersatz[ersatz.index <= ende]
        danach = ersatz[(ersatz.index > ende) & (ersatz.index.date == ende.date())]
        if bis.empty or danach.empty:
            continue
        teile.append(float(tag.iloc[-1]) * danach / float(bis.iloc[-1]))
    return pd.concat(teile).sort_index()


def _euro_minuten(werte: dict[str, str]) -> dict[str, pd.Series]:
    """Minutenkurse in Euro je ISIN, aus dem jeweils jüngsten Kurs aller Handelsplätze kombiniert.
    werte: ISIN -> Ticker der Heimatbörse."""
    plaetze = {isin: _plaetze(t) for isin, t in werte.items()}
    vertreter = {t: s for t, s in _stellvertreter().items() if t in werte.values() and s}
    alle = sorted({p for liste in plaetze.values() for p in liste} | set(vertreter.values()))
    waehrung = {p: waehrung_von(p) or ("EUR" if p.endswith((".DE", ".F")) else None) for p in alle}
    waehrung.update({s: waehrung_von(s) or "USD" for s in vertreter.values()})  # US-ETFs handeln in Dollar
    fremd = sorted({_einheit(w)[0] for w in waehrung.values() if w} - {"EUR"})
    minuten = minutenkurse(alle + [f"{w}EUR=X" for w in fremd])

    def in_euro(platz: str) -> pd.Series | None:
        reihe, w = minuten.get(platz), waehrung.get(platz)
        if reihe is None or not w:
            return None
        name, faktor = _einheit(w)
        reihe = reihe * faktor
        if name == "EUR":
            return reihe
        fx = minuten.get(f"{name}EUR=X")
        if fx is None:
            return None
        return reihe * fx.reindex(fx.index.union(reihe.index)).ffill().reindex(reihe.index)

    ergebnis = {}
    for isin, liste in plaetze.items():
        kombiniert = None
        for platz in liste:
            reihe = in_euro(platz)
            if reihe is None or reihe.dropna().empty:
                continue
            reihe = reihe.dropna()
            reihe = reihe[~reihe.index.duplicated(keep="last")]
            kombiniert = reihe if kombiniert is None else _juengster(kombiniert, reihe)
        ersatz = in_euro(vertreter[werte[isin]]) if werte[isin] in vertreter else None
        if kombiniert is not None and ersatz is not None and not ersatz.dropna().empty:
            kombiniert = _abends_fortfuehren(kombiniert, ersatz.dropna())
        if kombiniert is not None:
            ergebnis[isin] = kombiniert
    return ergebnis


def _bestand(b: Benutzer, ohne_krypto: bool) -> tuple[dict, dict, dict, list]:
    """(Ticker je ISIN, Stück vor heute, Trades von heute je ISIN, alle Trades)."""
    trades = lade_trades(b)
    heute = datetime.now().date().isoformat()
    info = {t["isin"]: t for t in trades}
    anfang, heutige = {}, {}
    for t in trades:
        if t["zeit"].date().isoformat() < heute:
            anfang[t["isin"]] = anfang.get(t["isin"], 0.0) + t["stueck"]
        else:
            heutige.setdefault(t["isin"], []).append(t)
    isins = [i for i in set(anfang) | set(heutige)
             if (anfang.get(i, 0.0) > REST or i in heutige) and info[i]["art"] != "DERIVATIVE"
             and not (ohne_krypto and info[i]["art"] == "CRYPTO")]
    ticker = {i: t for i in isins if (t := ticker_fuer(i, info[i]["art"]))}
    return ticker, anfang, heutige, trades


def _berechne_tr(b: Benutzer) -> dict:
    ticker, anfang, heutige, trades = _bestand(b, ohne_krypto=True)
    kurse = _euro_minuten(ticker)
    jetzt = pd.Timestamp.now(tz="Europe/Berlin")
    mitternacht = jetzt.normalize()
    heute = {i: r[r.index >= mitternacht] for i, r in kurse.items()}
    mit_heute = [r for r in heute.values() if len(r)]
    if not mit_heute:
        raise ValueError("heute noch keine Kurse")
    ende = max(r.index[-1] for r in mit_heute)
    zeiten = pd.date_range(mitternacht + pd.Timedelta(minutes=TR_BEGINN), ende, freq="1min")

    wert = pd.Series(0.0, index=zeiten)
    geldfluss = pd.Series(0.0, index=zeiten)
    startwert = kaeufe = 0.0
    ohne_daten, je_position = 0, {}
    for isin in ticker:
        reihe = kurse.get(isin)
        vorher = reihe[reihe.index < mitternacht] if reihe is not None else None
        menge = anfang.get(isin, 0.0) if anfang.get(isin, 0.0) > REST else 0.0
        if reihe is None or (menge and (vorher is None or vorher.empty)):
            ohne_daten += 1
            continue
        preis = reihe.reindex(reihe.index.union(zeiten)).ffill().reindex(zeiten)
        stueck = pd.Series(menge, index=zeiten)
        fluss = pd.Series(0.0, index=zeiten)
        gekauft = 0.0
        for t in heutige.get(isin, []):
            ab = zeiten >= t["zeit"]
            stueck[ab] += t["stueck"]
            fluss[ab] += t["betrag"] - t["gebuehr"]
            if t["stueck"] > 0:
                gekauft += t["gebuehr"] - t["betrag"]
        position = stueck.where(stueck > REST, 0.0) * preis
        start = menge * float(vorher.iloc[-1]) if menge else 0.0
        wert += position.fillna(0.0)
        geldfluss += fluss
        startwert += start
        kaeufe += gekauft
        letzter = position.dropna()
        if len(letzter):
            gv = float(letzter.iloc[-1] + fluss.iloc[-1] - start)
            je_position[isin] = {"gv_eur": gv, "gv_prozent": gv / (start + gekauft) * 100 if start + gekauft else None}

    basis = startwert + kaeufe
    if basis <= 0:
        raise ValueError("kein Bestand")
    prozent = (wert + geldfluss - startwert) / basis * 100
    return {
        "datum": mitternacht.date().isoformat(),
        "minuten": [{"zeit": z.strftime("%H:%M"), "w": round(float(w), 3)} for z, w in prozent.dropna().items()],
        "ohne_daten": ohne_daten,
        "aktualisiert": datetime.now(timezone.utc).isoformat(),
        "anzahl_trades": len(trades),
        "je_position": je_position,
        "gv_eur": float(prozent.iloc[-1] / 100 * basis),
        "gv_prozent": float(prozent.iloc[-1]),
        "modus": "tr",
        "achse": {"von": "08:00", "bis": "23:00"},  # Handelszeit bei Trade Republic (LS Exchange)
    }


def tr_tag(b: Benutzer) -> dict:
    """Der heutige Tag wie bei Trade Republic: Chart-Minuten und Ergebnis je Position."""
    schluessel = "depot:tagesverlauf:tr"
    alt, alter = cache_lesen(b, schluessel)
    max_alter = OFFEN_INTERVALL if ist_offen(EU) or ist_offen(US) else STUNDE
    if alt and alter < max_alter:
        return alt
    try:
        neu = _berechne_tr(b)
    except Exception:
        if alt:
            return alt
        raise
    cache_schreiben(b, schluessel, neu)
    return neu


def ny_tagesverlauf(b: Benutzer, positionen: list[dict]) -> dict:
    """Der Tageschart ab der Eröffnung in New York: startet bei 0 %, die Bewegung davor zählt nicht."""
    oeffnung = ny_oeffnung()
    if oeffnung is None:
        return {"fehler": "Heute ist in New York kein Handelstag."}
    if pd.Timestamp.now(tz="Europe/Berlin") < oeffnung:
        return {"fehler": f"New York öffnet heute um {oeffnung:%H:%M} Uhr. Bis dahin gibt es hier noch nichts zu sehen."}
    schluessel = f"depot:tagesverlauf:ny:{oeffnung.date()}"
    alt, alter = cache_lesen(b, schluessel)
    if alt and alter < OFFEN_INTERVALL:
        return alt
    pos = [p for p in positionen if p.get("ticker") and p.get("stueck")]
    kurse = _euro_minuten({p["isin"]: p["ticker"] for p in pos})
    jetzt = max((r.index[-1] for r in kurse.values() if len(r)), default=oeffnung)
    zeiten = pd.date_range(oeffnung, max(jetzt, oeffnung), freq="1min")
    wert = pd.Series(0.0, index=zeiten)
    start, ohne = 0.0, 0
    for p in pos:
        reihe = kurse.get(p["isin"])
        bis_dahin = reihe[reihe.index <= oeffnung] if reihe is not None else None
        if bis_dahin is None or bis_dahin.empty:
            ohne += 1
            continue
        preis = reihe.reindex(reihe.index.union(zeiten)).ffill().reindex(zeiten)
        wert += (preis * p["stueck"]).fillna(float(bis_dahin.iloc[-1]) * p["stueck"])
        start += float(bis_dahin.iloc[-1]) * p["stueck"]
    if not start:
        raise ValueError("keine Kurse zur Eröffnung")
    prozent = (wert / start - 1) * 100
    neu = {
        "datum": oeffnung.date().isoformat(),
        "minuten": [{"zeit": z.strftime("%H:%M"), "w": round(float(w), 3)} for z, w in prozent.items()],
        "ohne_daten": ohne, "aktualisiert": datetime.now(timezone.utc).isoformat(),
        "modus": "ny", "oeffnung": f"{oeffnung:%H:%M}",
        "achse": {"von": f"{oeffnung:%H:%M}", "bis": "22:00"},
    }
    cache_schreiben(b, schluessel, neu)
    return neu


def ny_oeffnung() -> pd.Timestamp | None:
    """Eröffnung in New York heute, in deutscher Zeit (15:30, in den Wochen der Zeitumstellung 14:30)."""
    try:
        kal = _kalender(US)
        heute = pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize()
        if not kal.is_session(heute):
            return None
        return kal.session_open(heute).tz_convert("Europe/Berlin")
    except Exception:
        return None


def seit_ny(b: Benutzer, positionen: list[dict], ohne_krypto: bool = False) -> dict:
    """Gewinn oder Verlust jeder Position seit der Eröffnung in New York heute."""
    oeffnung = ny_oeffnung()
    if oeffnung is None:
        return {"hinweis": "Heute ist in New York kein Handelstag.", "positionen": [], "gv_eur": 0, "gv_prozent": None, "ohne_daten": 0}
    uhr = oeffnung.strftime("%H:%M")
    if pd.Timestamp.now(tz="Europe/Berlin") < oeffnung:
        return {"hinweis": f"New York öffnet heute um {uhr} Uhr.", "oeffnung": uhr, "positionen": [], "gv_eur": 0, "gv_prozent": None, "ohne_daten": 0}
    schluessel = f"depot:seit-ny:{oeffnung.date()}:{int(ohne_krypto)}"
    alt, alter = cache_lesen(b, schluessel)
    if alt and alter < OFFEN_INTERVALL:
        return alt

    pos = [p for p in positionen if p.get("ticker") and p.get("stueck") and not (ohne_krypto and p["art"] == "CRYPTO")]
    kurse = _euro_minuten({p["isin"]: p["ticker"] for p in pos})
    zeilen, gv, basis, ohne = [], 0.0, 0.0, 0
    for p in pos:
        reihe = kurse.get(p["isin"])
        bis_dahin = reihe[reihe.index <= oeffnung] if reihe is not None else None
        if reihe is None or bis_dahin is None or bis_dahin.empty:
            ohne += 1
            zeilen.append({"isin": p["isin"], "name": p["name"], "art": p["art"], "ticker": p["ticker"], "gv_eur": None, "gv_prozent": None, "verkauft": False})
            continue
        start = float(bis_dahin.iloc[-1])
        jetzt = float(p["kurs_eur"]) if p.get("kurs_eur") else float(reihe.iloc[-1])
        eur = (jetzt - start) * p["stueck"]
        gv += eur
        basis += start * p["stueck"]
        zeilen.append({"isin": p["isin"], "name": p["name"], "art": p["art"], "ticker": p["ticker"],
                       "gv_eur": eur, "gv_prozent": (jetzt / start - 1) * 100, "verkauft": False})
    zeilen.sort(key=lambda z: -float("inf") if z["gv_eur"] is None else z["gv_eur"], reverse=True)
    neu = {"oeffnung": uhr, "gv_eur": gv, "gv_prozent": gv / basis * 100 if basis else None, "ohne_daten": ohne, "positionen": zeilen}
    cache_schreiben(b, schluessel, neu)
    return neu
