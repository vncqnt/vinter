"""Makro-Sensitivität: Wie reagieren meine Positionen auf Zinsen, Ölpreis und Dollar?

Für jede Position wird eine Regression gerechnet: Ihre Wochenrendite (in Euro) wird gleichzeitig
durch drei Größen erklärt, nämlich die Änderung der 10-jährigen US-Rendite, die Ölpreis-Rendite und
die Dollar-Rendite. Heraus kommt pro Größe eine Zahl: "Steigt der Ölpreis um 10 %, bewegte sich die
Position im Schnitt um x %". Wochen statt Tage, weil US- und Europa-Börsen zu anderen Zeiten schließen.

Wahlweise kommt der Gesamtmarkt (MSCI World) als vierte Größe dazu. Dann zeigt die Zahl, wie die
Position zusätzlich zur normalen Marktbewegung reagiert. Das Marktrauschen ist so herausgerechnet,
und die Ergebnisse werden deutlich belastbarer.

Wichtig: Das beschreibt den Zusammenhang der letzten Jahre, keine Garantie für die Zukunft.
Ist eine Zahl statistisch unsicher (t-Wert unter 2), wird sie so markiert. Die Spanne ist das
95-%-Vertrauensintervall: Mit dieser Unsicherheit liegt der wahre Wert ziemlich sicher darin.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..benutzer import Benutzer
from ..cache import TAG, markt_lesen as cache_lesen, markt_schreiben as cache_schreiben
from ..markt.euro_kurse import euro_kurse, tagesreihe as _reihe
from .risiko import MARKT
from .verteilung import einstellungen as verteilung_einstellungen

JAHRE = (3, 5)  # wählbar: kürzer = näher an heute, länger = mit der Zinswende 2022
MIN_WOCHEN = 52
T_SICHER = 2.0
SPANNE = 1.96  # 95-%-Vertrauensintervall
# Ticker, Name, wie die Wochenänderung gerechnet wird, und welcher Schock gezeigt wird.
FAKTOREN = {
    "zins": {"ticker": "^TNX", "name": "10-jährige US-Rendite", "art": "punkte", "schock": 0.25,
             "schock_text": "+0,25 %-Punkte", "frage": "Steigende Zinsen",
             "satz": "die 10-jährige US-Rendite um 0,25 Prozentpunkte steigt"},
    "oel": {"ticker": "BZ=F", "name": "Ölpreis (Brent)", "art": "rendite", "schock": 0.10,
            "schock_text": "+10 %", "frage": "Steigender Ölpreis",
            "satz": "der Ölpreis (Brent) um 10 % steigt"},
    "dollar": {"ticker": "DX-Y.NYB", "name": "Dollar-Index", "art": "rendite", "schock": 0.05,
               "schock_text": "+5 %", "frage": "Starker Dollar",
               "satz": "der Dollar-Index um 5 % zulegt"},
}


def _faktoren(start: str) -> pd.DataFrame:
    """Wochenänderungen der drei Größen, einen Tag im Cache."""
    schluessel = f"makro-faktoren:{start}"
    alt, alter = cache_lesen(schluessel)
    if alt and alter < TAG:
        return pd.DataFrame(alt["werte"], index=pd.to_datetime(alt["index"]), columns=alt["spalten"])
    with ThreadPoolExecutor(max_workers=3) as pool:
        reihen = dict(zip(FAKTOREN, pool.map(lambda f: _reihe(f["ticker"], start), FAKTOREN.values())))
    spalten = {}
    for name, r in reihen.items():
        if not r:
            raise ValueError(f"keine Daten für {name}")
        woche = r[0].resample("W-FRI").last()
        if FAKTOREN[name]["art"] == "punkte":
            if woche.median() > 20:  # manche Quellen liefern die Rendite mal zehn
                woche = woche / 10
            spalten[name] = woche.diff()
        else:
            spalten[name] = woche.pct_change()
    tabelle = pd.DataFrame(spalten).dropna()
    cache_schreiben(schluessel, {"index": [d.isoformat() for d in tabelle.index], "spalten": list(tabelle.columns), "werte": tabelle.to_numpy().tolist()})
    return tabelle


def _regression(rendite: pd.Series, faktoren: pd.DataFrame, wert: float, markt: pd.Series | None = None) -> dict | None:
    """Wochenrendite auf die drei Größen (und auf Wunsch den Markt). Gibt je Größe die Wirkung beim gezeigten Schock zurück."""
    teile = [rendite.rename("y"), faktoren] + ([markt.rename("markt")] if markt is not None else [])
    daten = pd.concat(teile, axis=1).dropna()
    daten = daten[daten["y"].abs() < 0.8]  # offensichtliche Datenfehler (z. B. Aktiensplit) raus
    if len(daten) < MIN_WOCHEN:
        return None
    namen = list(FAKTOREN)
    spalten = namen + (["markt"] if markt is not None else [])
    X = np.column_stack([np.ones(len(daten)), daten[spalten].to_numpy()])
    y = daten["y"].to_numpy()
    koeff, *_ = np.linalg.lstsq(X, y, rcond=None)
    rest = y - X @ koeff
    streuung = (rest @ rest) / (len(y) - X.shape[1])
    fehler = np.sqrt(np.diag(streuung * np.linalg.inv(X.T @ X)))
    wirkung = {}
    for i, f in enumerate(namen, start=1):
        effekt = koeff[i] * FAKTOREN[f]["schock"] * 100  # in Prozent beim gezeigten Schock
        t = float(koeff[i] / fehler[i]) if fehler[i] else 0.0
        breite = SPANNE * fehler[i] * FAKTOREN[f]["schock"] * 100
        wirkung[f] = {"prozent": float(effekt), "eur": float(effekt / 100 * wert), "t": t, "sicher": abs(t) >= T_SICHER,
                      "von": float(effekt - breite), "bis": float(effekt + breite)}
    r2 = 1 - (rest @ rest) / ((y - y.mean()) @ (y - y.mean()))
    beta = float(koeff[len(namen) + 1]) if markt is not None else None
    return {"wochen": len(daten), "r2": float(r2), "wirkung": wirkung, "beta": beta}


def makro_sensitivitaet(b: Benutzer, positionen: list[dict], jahre: int = 3, ohne_markt: bool = True) -> dict:
    jahre = jahre if jahre in JAHRE else JAHRE[0]
    # Werte aus dem Markt (extra=True) bekommen eine eigene Zeile, zählen aber nicht ins Depot
    pos = [p for p in positionen if p.get("ticker") and (p.get("wert_eur") or p.get("extra"))]
    if not pos:
        return {"fehler": "Keine Positionen mit Kurs gefunden."}
    start = (date.today() - timedelta(days=365 * jahre + 14)).replace(day=1).isoformat()  # fester Monatsanfang: Cache hält
    faktoren = _faktoren(start)
    kurse = euro_kurse(sorted({p["ticker"] for p in pos} | {MARKT}), start=start)
    markt = None
    if ohne_markt:
        if MARKT not in kurse:
            return {"fehler": "Die Kurse des MSCI World konnten gerade nicht geladen werden."}
        markt = kurse[MARKT].resample("W-FRI").last().pct_change()

    gesamt = sum(p["wert_eur"] for p in pos if not p.get("extra")) or 1
    zeilen, ohne, renditen = [], [], {}
    for p in pos:
        reihe = kurse.get(p["ticker"])
        rendite = reihe.resample("W-FRI").last().pct_change() if reihe is not None else None
        ergebnis = _regression(rendite, faktoren, p["wert_eur"] or 0, markt) if rendite is not None else None
        if not ergebnis:
            ohne.append(p["name"])
            continue
        if p.get("extra"):
            zeilen.append({"name": p["name"], "ticker": p["ticker"], "gewicht": None, "extra": True, **ergebnis})
            continue
        renditen[p["ticker"]] = (rendite, p["wert_eur"])
        zeilen.append({"name": p["name"], "ticker": p["ticker"], "gewicht": p["wert_eur"] / gesamt * 100, **ergebnis})
    if not zeilen:
        return {"fehler": "Für keine Position gibt es genug Kursdaten."}

    # Das Depot als Ganzes: heutige Gewichte, Wochenrendite als gewichtete Summe. So gibt es auch
    # für das Depot einen t-Wert. Werte ohne Kurs in einer Woche zählen dort mit 0 %.
    def buendel(ticker: list[str]) -> tuple[pd.Series, float]:
        summe = sum(renditen[t][1] for t in ticker)
        return sum(renditen[t][0].fillna(0.0) * (renditen[t][1] / summe) for t in ticker), summe

    depot = None
    if renditen:
        depot_rendite, erfasst = buendel(list(renditen))
        depot = _regression(depot_rendite, faktoren, erfasst, markt)["wirkung"]

    # Deine Themen aus deiner verteilung.yaml: Mehrere Aktien zusammen rauschen weniger als eine.
    eigene, _, gruppen, _ = verteilung_einstellungen(b)
    thema_von = {p["ticker"]: (eigene.get(p["isin"]) or {}).get("thema") for p in pos if p["ticker"] in renditen}
    themen = {}
    for t, th in thema_von.items():
        themen.setdefault(th or "Ohne Thema", []).append(t)
    for gruppe, teile in gruppen.items():
        mitglieder = [t for t, th in thema_von.items() if th in teile]
        if len({thema_von[t] for t in mitglieder}) > 1:  # nur wenn die Gruppe wirklich mehrere Themen bündelt
            themen[f"{gruppe} gesamt"] = mitglieder
    name_von = {z["ticker"]: z["name"] for z in zeilen}
    buendel_zeilen = []
    for name, ticker in themen.items():
        rendite, wert = buendel(ticker)
        ergebnis = _regression(rendite, faktoren, wert, markt)
        if ergebnis:
            buendel_zeilen.append({"name": name, "ticker": name, "gewicht": wert / gesamt * 100,
                                   "mitglieder": [name_von[t] for t in ticker], **ergebnis})

    return {
        "faktoren": [{"schluessel": f, **{k: v for k, v in FAKTOREN[f].items() if k != "ticker"}} for f in FAKTOREN],
        "positionen": zeilen,
        "themen": sorted(buendel_zeilen, key=lambda z: -z["gewicht"]),
        "depot": depot,
        "ohne_daten": ohne,
        "jahre": jahre,
        "ohne_markt": ohne_markt,
        "zeitraum": {"von": faktoren.index[0].date().isoformat(), "bis": faktoren.index[-1].date().isoformat(), "wochen": len(faktoren)},
    }
