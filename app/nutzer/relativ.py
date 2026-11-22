"""Relative Stärke: Läuft jede meiner Aktien besser oder schlechter als ihre eigene Branche?

Vergleich in Euro (so, wie es im Depot ankommt) gegen einen Branchen-ETF, z. B. Micron gegen den
Halbleiter-ETF SOXX. Relative Stärke = (1 + Rendite der Aktie) / (1 + Rendite des ETFs) - 1:
"um wie viel Prozent wäre ich mit der Aktie besser gefahren als mit der ganzen Branche".
Positiv = die Aktie schlägt ihre Branche, die Bewegung kommt also von der Firma selbst.
"""
from datetime import timedelta

import pandas as pd

from ..markt.euro_kurse import euro_kurse
from ..markt.vergleich_etf import vergleich_etf

ZEITRAEUME = {"tag": None, "woche": 7, "monat": 30, "3m": 91, "jahr": 365}


def _rendite(reihe: pd.Series, tage: int | None) -> float | None:
    reihe = reihe.dropna()
    if len(reihe) < 2:
        return None
    if tage is None:  # Tag: letzter Kurs gegen den Schluss davor
        start = reihe.iloc[-2]
    else:
        frueher = reihe[reihe.index <= reihe.index[-1] - timedelta(days=tage)]
        if frueher.empty:
            return None
        start = frueher.iloc[-1]
    return float(reihe.iloc[-1] / start - 1) * 100


def relative_staerke(positionen: list[dict]) -> dict:
    aktien = [p for p in positionen if p.get("art") == "STOCK" and p.get("ticker") and (p.get("wert_eur") or p.get("extra"))]
    if not aktien:
        return {"fehler": "Keine Aktien im Depot gefunden."}
    zuordnung = vergleich_etf([p["ticker"] for p in aktien])
    # Werte aus dem Markt ohne Branche (Sektor-ETFs, Indizes) werden mit dem S&P 500 verglichen
    for p in aktien:
        if p.get("extra") and p["ticker"] not in zuordnung and p["ticker"] != "SPY":
            zuordnung[p["ticker"]] = {"etf": "SPY", "name": "S&P 500", "quelle": "Markt"}
    ohne = [p["name"] for p in aktien if p["ticker"] not in zuordnung]
    aktien = [p for p in aktien if p["ticker"] in zuordnung]
    if not aktien:
        return {"fehler": "Für keine Aktie ist eine Branche bekannt."}
    etfs = sorted({z["etf"] for z in zuordnung.values()})
    kurse = euro_kurse(sorted({p["ticker"] for p in aktien} | set(etfs)), tage=400)

    # Gemeinsame Tagesachse fürs Diagramm: alle Handelstage, Lücken (Feiertage) mit dem Vortag gefüllt
    tabelle = pd.DataFrame({t: r for t, r in kurse.items()}).sort_index().ffill()
    tabelle = tabelle[tabelle.index >= tabelle.index[-1] - timedelta(days=366)]

    zeilen = []
    for p in sorted(aktien, key=lambda x: (bool(x.get("extra")), -(x["wert_eur"] or 0))):
        t, z = p["ticker"], zuordnung[p["ticker"]]
        if t not in kurse or z["etf"] not in kurse:
            ohne.append(p["name"])
            continue
        werte = {}
        for name, tage in ZEITRAEUME.items():
            a, e = _rendite(kurse[t], tage), _rendite(kurse[z["etf"]], tage)
            werte[name] = None if a is None or e is None else {
                "aktie": a, "etf": e, "relativ": ((1 + a / 100) / (1 + e / 100) - 1) * 100}
        zeilen.append({"name": p["name"], "ticker": t, "wert_eur": p["wert_eur"], "etf": z["etf"], "extra": bool(p.get("extra")),
                       "name_branche": z["name"], "quelle": z["quelle"], "zeitraeume": werte})

    rund = lambda v: None if pd.isna(v) else round(float(v), 4)
    return {
        "aktien": zeilen,
        "ohne_vergleich": ohne,
        "daten": [d.date().isoformat() for d in tabelle.index],
        "kurse": {t: [rund(v) for v in tabelle[t]] for t in tabelle.columns},
    }
