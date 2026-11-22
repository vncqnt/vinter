"""Gewinn und Verlust pro Position für Tag, 1 Woche, Monat, seit Jahresbeginn und 1 Jahr.

Gerechnet wird: Wert am Ende + Verkaufserlöse - Käufe - Wert am Anfang.
Käufe im Zeitraum zählen so nicht als Gewinn, und auch im Zeitraum komplett verkaufte
Positionen sind enthalten. Je Position bezieht sich der Prozentwert auf Wert am Anfang plus Käufe,
fürs ganze Depot auf das im Schnitt investierte Geld (siehe _schnitt_kapital). Dividenden sind nicht enthalten.
"""
from bisect import bisect_left
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import hashlib
import json

from ..benutzer import Benutzer
from ..cache import STUNDE, markt_lesen as cache_lesen, markt_schreiben as cache_schreiben
from ..markt.euro_kurse import in_euro as _in_euro, tagesreihe as _reihe
from ..markt.kurse import ticker_fuer
from .tr_parser import lade_trades

SCHLUESSEL = "depot:schlusskurse"
REST = 1e-6  # kleinere Stückzahlen gelten als Rundungsrest


def _schlusskurse(ticker: dict[str, str]) -> dict[str, dict[str, float]]:
    """Tägliche Schlusskurse in Euro je ISIN seit Ende des Vorjahres, höchstens eine Stunde alt.
    Reine Marktdaten: im gemeinsamen Cache, der Schlüssel hängt an der Liste der Werte."""
    # Puffer für Feiertage zum Jahreswechsel; für "1 Jahr" reicht es gut ein Jahr zurück.
    start = min(date(date.today().year - 1, 12, 20), date.today() - timedelta(days=380)).isoformat()
    schluessel = f"{SCHLUESSEL}:" + hashlib.sha1(json.dumps(ticker, sort_keys=True).encode()).hexdigest()
    alt, alter = cache_lesen(schluessel)
    passt = bool(alt) and alt["start"] == start and alt["ticker"] == ticker
    if passt and alter < STUNDE:
        return alt["kurse"]

    bisher = alt["kurse"] if passt else {}
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            reihen = dict(zip(ticker, pool.map(lambda t: _reihe(t, start), ticker.values())))
        euro = _in_euro(reihen, start)
    except Exception:
        if passt:
            return bisher
        raise
    if ticker and not euro and not passt:
        raise ValueError("keine Kurse")

    neu = {i: {d.date().isoformat(): float(w) for d, w in s.dropna().items()} for i, s in euro.items()}
    kurse = {**bisher, **neu}  # fällt ein einzelner Wert aus, bleibt sein letzter Stand erhalten
    cache_schreiben(schluessel, {"start": start, "ticker": ticker, "kurse": kurse})
    return kurse


def _buchungen(trades: list[dict]) -> dict[str, list[tuple]]:
    """Je ISIN eine Liste aus (Tag, Stück, Geldfluss). Geld hinein ist negativ, Gebühren zählen mit."""
    je_isin = defaultdict(list)
    bestand = defaultdict(float)
    for t in trades:
        isin = t["isin"]
        if t["stueck"] < 0 and -t["stueck"] > bestand[isin] + REST:
            continue  # Verkauf ohne passenden Kauf im Export, wie in portfolio.py nicht mitgezählt
        bestand[isin] += t["stueck"]
        je_isin[isin].append((t["zeit"].date().isoformat(), t["stueck"], t["betrag"] - t["gebuehr"]))
    return je_isin


def _ergebnis(buchungen: list[tuple], schluss: dict[str, float], grenze: str, kurs: float | None) -> dict | None:
    """Ergebnis ab dem letzten Schlusskurs vor `grenze`. None, wenn die Position nicht betroffen ist."""
    tage = sorted(schluss)
    stelle = bisect_left(tage, grenze)
    referenz = tage[stelle - 1] if stelle else None
    stichtag = referenz or (date.fromisoformat(grenze) - timedelta(days=1)).isoformat()

    im_zeitraum = [b for b in buchungen if b[0] > stichtag]
    anfang = sum(b[1] for b in buchungen if b[0] <= stichtag)
    ende = sum(b[1] for b in buchungen)
    anfang = anfang if anfang > REST else 0.0
    ende = ende if ende > REST else 0.0
    if not anfang and not im_zeitraum:
        return None

    # Ein Kurs ist nur nötig, wenn am Anfang oder Ende wirklich Stücke im Depot liegen.
    if (anfang and not referenz) or (ende and kurs is None):
        return {"gv_eur": None, "gv_prozent": None, "verkauft": not ende}

    startwert = anfang * schluss[referenz] if anfang else 0.0
    endwert = ende * kurs if ende else 0.0
    kaeufe = -sum(b[2] for b in im_zeitraum if b[1] > 0)
    verkaeufe = sum(b[2] for b in im_zeitraum if b[1] < 0)
    gv = endwert + verkaeufe - kaeufe - startwert
    basis = startwert + kaeufe
    # Geldflüsse im Zeitraum (Kauf positiv, Verkaufserlös negativ) für das im Schnitt investierte Geld des Depots
    fluesse = [(b[0], -b[2]) for b in im_zeitraum]
    return {"gv_eur": gv, "gv_prozent": gv / basis * 100 if basis else None, "basis": basis, "verkauft": not ende,
            "startwert": startwert, "fluesse": fluesse, "stichtag": stichtag}


def _vor_einem_jahr(tag: date) -> date:
    try:
        return tag.replace(year=tag.year - 1)
    except ValueError:  # 29. Februar
        return tag.replace(year=tag.year - 1, day=28)


def _schnitt_kapital(zeilen: list[dict], heute: date) -> float | None:
    """Im Schnitt investiertes Geld des Depots im Zeitraum: Wert am Anfang plus bis dahin eingezahltes
    minus herausgenommenes Geld, über alle Kalendertage gemittelt. So zählt Geld, das mehrfach
    umgeschichtet wurde, nur einmal, und Geld, das erst spät dazukam, nur anteilig."""
    if not zeilen:
        return None
    stichtag = min(z["stichtag"] for z in zeilen)
    tage = (heute - date.fromisoformat(stichtag)).days
    if tage < 1:
        return None
    kapital = sum(z["startwert"] for z in zeilen)
    je_tag = defaultdict(float)
    for z in zeilen:
        for tag, betrag in z["fluesse"]:
            je_tag[tag] += betrag
    summe = 0.0
    tag = date.fromisoformat(stichtag)
    for _ in range(tage):
        tag += timedelta(days=1)
        kapital += je_tag.get(tag.isoformat(), 0.0)
        summe += max(kapital, 0.0)
    schnitt = summe / tage
    return schnitt if schnitt > 0 else None


def letzter_handelstag(positionen: list[dict]) -> str:
    """Der jüngste Tag mit Aktienkursen, für das ganze Depot derselbe.

    Vor der US-Eröffnung stehen US-Werte so auf 0, statt die Bewegung vom Vortag mitzuzählen.
    Krypto zählt hier nicht, sonst wäre am Wochenende jeder Aktienwert auf 0.
    """
    staende = [p["kurs_stand"] for p in positionen if p.get("kurs_stand") and p["art"] != "CRYPTO"]
    return max(staende or [p["kurs_stand"] for p in positionen if p.get("kurs_stand")] or [date.today().isoformat()])


def perioden(b: Benutzer, positionen: list[dict], ohne_krypto: bool = False) -> dict:
    """ohne_krypto: wie das Aktien-und-ETF-Depot bei Trade Republic, Krypto zählt dort getrennt."""
    trades = lade_trades(b)
    info = {t["isin"]: t for t in trades}
    # Optionsscheine haben bei Yahoo keine Kurshistorie.
    ticker = {i: ticker_fuer(i, t["art"]) for i, t in info.items() if t["art"] != "DERIVATIVE"}
    ticker = {i: t for i, t in ticker.items() if t}
    kurse = _schlusskurse(ticker)
    buchungen = _buchungen(trades)
    aktuell = {p["isin"]: p for p in positionen}

    heute = date.today()
    grenzen = {
        "tag": letzter_handelstag(positionen),
        # Bezug ist der letzte Schlusskurs vor der Grenze: bei 1 Woche der Schluss von vor 7 Tagen
        "woche": (heute - timedelta(days=6)).isoformat(),
        "monat": heute.replace(day=1).isoformat(),
        "ytd": heute.replace(month=1, day=1).isoformat(),
        "jahr": (_vor_einem_jahr(heute) + timedelta(days=1)).isoformat(),
    }

    zeitraeume = {}
    for zeitraum in ("tag", "woche", "monat", "ytd", "jahr"):
        zeilen = []
        for isin, liste in buchungen.items():
            if ohne_krypto and info[isin]["art"] == "CRYPTO":
                continue
            schluss = kurse.get(isin, {})
            p = aktuell.get(isin, {})
            ergebnis = _ergebnis(liste, schluss, grenzen[zeitraum], p.get("kurs_eur"))
            if ergebnis:
                zeilen.append({
                    "isin": isin, "name": info[isin]["name"], "art": info[isin]["art"],
                    "ticker": p.get("ticker") or ticker.get(isin), **ergebnis,
                })

        zeilen.sort(key=lambda z: -float("inf") if z["gv_eur"] is None else z["gv_eur"], reverse=True)
        bekannt = [z for z in zeilen if z["gv_eur"] is not None]
        gv = sum(z["gv_eur"] for z in bekannt)
        basis = _schnitt_kapital(bekannt, heute) or sum(z["basis"] for z in bekannt)
        for z in zeilen:
            for feld in ("basis", "startwert", "fluesse", "stichtag"):
                z.pop(feld, None)
        zeitraeume[zeitraum] = {
            "gv_eur": gv,
            "gv_prozent": gv / basis * 100 if basis else None,
            "ohne_daten": len(zeilen) - len(bekannt),
            "positionen": zeilen,
        }
    return {"zeitraeume": zeitraeume}
