"""Stresstest: Was wäre mit dem heutigen Depot in früheren Krisen passiert?

Gerechnet wird mit den heutigen Positionen und ihrem heutigen Wert. Jede Position macht die
Kursbewegung mit, die sie im Krisenzeitraum wirklich hatte (in Euro, also mit Wechselkurseffekt).
Gab es eine Aktie damals noch nicht, wird ihr Verlust über ihr Beta zum MSCI World geschätzt:
Beta 1,5 heißt, sie schwankt typischerweise anderthalbmal so stark wie der Markt.
"""
from datetime import date

import numpy as np
import pandas as pd
import yaml

from ..konfig import MARKT_DATEN
from ..markt.euro_kurse import euro_kurse
from .risiko import MARKT

SZENARIEN_DATEI = MARKT_DATEN / "stress_szenarien.yaml"
KURSE_AB = "2019-12-01"  # deckt alle mitgelieferten Szenarien ab; eigene Zeiträume davor laden nach
BETA_WOCHEN = 104        # Beta aus den Wochenrenditen der letzten zwei Jahre
MIN_WOCHEN = 26
TOLERANZ = pd.Timedelta(days=7)  # so weit darf der letzte Kurs vor dem Start zurückliegen


class StressFehler(Exception):
    pass


def szenarien() -> list[dict]:
    roh = yaml.safe_load(SZENARIEN_DATEI.read_text(encoding="utf-8")) or []
    return [{"name": s["name"], "von": str(s["von"]), "bis": str(s["bis"]), "beschreibung": s.get("beschreibung", "")} for s in roh]


def _wochen_beta(reihe: pd.Series, markt: pd.Series) -> float | None:
    """Beta aus Wochenrenditen. Wochen gleichen aus, dass US- und Europa-Börsen zu anderen Zeiten schließen."""
    woche = pd.concat([reihe, markt], axis=1).resample("W-FRI").last().pct_change().dropna().tail(BETA_WOCHEN)
    if len(woche) < MIN_WOCHEN:
        return None
    return float(np.cov(woche.iloc[:, 0], woche.iloc[:, 1])[0, 1] / woche.iloc[:, 1].var(ddof=1))


def _pfad(reihe: pd.Series, von: pd.Timestamp, tage: pd.DatetimeIndex) -> pd.Series | None:
    """Kursverlauf im Zeitraum relativ zum Schlusskurs am Starttag (1,0 = unverändert)."""
    vorher = reihe[reihe.index <= von]
    if vorher.empty or von - vorher.index[-1] > TOLERANZ:
        return None
    im_zeitraum = reihe.reindex(reihe.index.union(tage)).ffill().reindex(tage)
    return im_zeitraum / vorher.iloc[-1]


def _rechne(szenario: dict, pos: list[dict], kurse: dict, betas: dict) -> dict:
    von, bis = pd.Timestamp(szenario["von"]), pd.Timestamp(szenario["bis"])
    tage = pd.bdate_range(von, bis)
    markt = _pfad(kurse[MARKT], von, tage) if MARKT in kurse else None
    if markt is None or markt.dropna().empty:
        raise StressFehler(f"Für {szenario['name']} fehlen die Kurse des MSCI World.")
    markt = markt.ffill()

    gesamt = sum(p["wert_eur"] for p in pos)
    markt_ende = float(markt.iloc[-1] - 1)
    depot = pd.Series(0.0, index=tage)
    zeilen = []
    for p in pos:
        pfad = _pfad(kurse[p["ticker"]], von, tage) if p["ticker"] in kurse else None
        geschaetzt = pfad is None or bool(pfad.isna().all())
        beta = betas.get(p["ticker"])
        if geschaetzt:
            # Ohne Kurse im Zeitraum: Marktbewegung mal Beta. Fehlt auch das Beta, zählt der Markt 1:1.
            pfad = 1 + (beta if beta is not None else 1.0) * (markt - 1)
        pfad = pfad.ffill().fillna(1.0)
        anteil = p["wert_eur"] / gesamt
        depot += anteil * pfad
        ende = float(pfad.iloc[-1] - 1)
        zeilen.append({
            "name": p["name"], "ticker": p["ticker"], "gewicht": anteil * 100,
            "prozent": ende * 100, "eur": ende * p["wert_eur"],
            "tief_prozent": float(pfad.min() - 1) * 100,
            "geschaetzt": geschaetzt, "beta": beta,
            # Anteil am Abstand zum MSCI World in Prozentpunkten: Gewicht mal (eigene Bewegung minus Markt).
            # Alle Anteile zusammen ergeben genau "Depot minus MSCI World".
            "abstand_pp": anteil * (ende - markt_ende) * 100,
            "wert": p["wert_eur"],
            # Tag für Tag, damit die Seite eigene Gewichte durchrechnen kann (Was-wäre-wenn).
            "verlauf": [round(float(v - 1) * 100, 3) for v in pfad],
        })
    zeilen.sort(key=lambda z: z["eur"])
    return {
        **szenario,
        "tage": [d.date().isoformat() for d in tage],
        "depot_verlauf": [round(float(v - 1) * 100, 3) for v in depot],
        "markt_verlauf": [round(float(v - 1) * 100, 3) for v in markt],
        "depot_prozent": float(depot.iloc[-1] - 1) * 100,
        "depot_eur": float(depot.iloc[-1] - 1) * gesamt,
        "depot_tief_prozent": float(depot.min() - 1) * 100,
        "depot_tief_eur": float(depot.min() - 1) * gesamt,
        "markt_prozent": float(markt.iloc[-1] - 1) * 100,
        "positionen": zeilen,
        "geschaetzt": [z["name"] for z in zeilen if z["geschaetzt"]],
    }


def stresstest(positionen: list[dict], von: str | None = None, bis: str | None = None) -> dict:
    pos = [p for p in positionen if p.get("ticker") and p.get("wert_eur")]
    if not pos:
        return {"fehler": "Keine Positionen mit Kurs gefunden."}
    liste = szenarien()
    if von and bis:
        try:
            a, b = date.fromisoformat(von), date.fromisoformat(bis)
        except ValueError:
            return {"fehler": "Das Datum ist ungültig."}
        if not (date(2000, 1, 1) <= a < b <= date.today()):
            return {"fehler": "Der Zeitraum muss in der Vergangenheit liegen und \"von\" vor \"bis\"."}
        liste.append({"name": "Eigener Zeitraum", "von": a.isoformat(), "bis": b.isoformat(), "beschreibung": "Von dir gewählt.", "eigen": True})

    start = min([KURSE_AB] + [s["von"] for s in liste])
    start = (pd.Timestamp(start) - pd.Timedelta(days=14)).date().isoformat()
    kurse = euro_kurse(sorted({p["ticker"] for p in pos} | {MARKT}), start=start)
    if MARKT not in kurse:
        return {"fehler": "Die Kurse des MSCI World konnten gerade nicht geladen werden."}
    betas = {p["ticker"]: _wochen_beta(kurse[p["ticker"]], kurse[MARKT]) for p in pos if p["ticker"] in kurse}

    ergebnisse, fehler = [], []
    for s in liste:
        try:
            ergebnisse.append(_rechne(s, pos, kurse, betas))
        except StressFehler as f:
            fehler.append(str(f))
    return {
        "szenarien": ergebnisse,
        "hinweise": fehler,
        "depotwert": sum(p["wert_eur"] for p in pos),
        "markt": "MSCI World (iShares, EUNL, in Euro)",
    }
