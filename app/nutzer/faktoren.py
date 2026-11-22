"""Faktor-Analyse: Auf welche Anlagestile wettet mein Depot, ohne dass ich es merke?

Fachleute erklären Aktienrenditen neben dem Gesamtmarkt mit ein paar "Faktoren": kleine Firmen
(Size), Aktien, die zuletzt gut liefen (Momentum), günstig bewertete Firmen (Value) und solide,
profitable Firmen (Quality). Für jeden Faktor gibt es einen US-ETF. Als Faktor zählt nicht der
ETF selbst, sondern sein Vorsprung vor dem S&P 500 (ETF-Rendite minus SPY-Rendite). Sonst würden
alle Faktoren einfach den Markt messen.

Gerechnet wird eine Regression der Wochenrendite (in Euro) des Depots und jeder Position auf
den Weltmarkt (MSCI World) und die vier Faktor-Vorsprünge gleichzeitig. Die Zahl je Faktor heißt:
"Laufen Momentum-Aktien in einer Woche 1 % besser als der Markt, bewegt sich das Depot im Schnitt um
x % mit". Positiv = das Depot hängt an diesem Stil, negativ = es verhält sich wie das Gegenteil.

Zusätzlich die Zerlegung des letzten Jahres: Wie viel der Depotrendite der Markt, jeder Faktor und
der Rest (eigene Auswahl, Glück, alles andere) erklären. Vergangenheit, keine Vorhersage.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..markt.euro_kurse import euro_kurse
from .risiko import MARKT

JAHRE = (3, 5)
MIN_WOCHEN = 52
T_SICHER = 2.0
SPANNE = 1.96
VERGLEICH = "SPY"  # Faktor = Vorsprung des Faktor-ETFs vor dem S&P 500
FAKTOREN = {
    "size": {"ticker": "IWM", "name": "Kleine Firmen", "kurz": "Small Caps", "etf": "iShares Russell 2000 (IWM)",
             "plus": "Du hängst an kleinen Firmen", "minus": "Du hängst an großen Konzernen"},
    "momentum": {"ticker": "MTUM", "name": "Momentum", "kurz": "Momentum", "etf": "iShares MSCI USA Momentum (MTUM)",
                 "plus": "Du hängst an den Gewinnern der letzten Monate", "minus": "Du hältst eher Aktien, die zuletzt schwach liefen"},
    "value": {"ticker": "VLUE", "name": "Günstig bewertet", "kurz": "Value", "etf": "iShares MSCI USA Value (VLUE)",
              "plus": "Du hängst an günstig bewerteten Firmen", "minus": "Du hängst an teuren Wachstumsfirmen"},
    "quality": {"ticker": "QUAL", "name": "Qualität", "kurz": "Quality", "etf": "iShares MSCI USA Quality (QUAL)",
                "plus": "Du hängst an soliden, profitablen Firmen", "minus": "Du hältst eher riskantere Firmen mit schwächeren Bilanzen"},
}


def _woche(reihe: pd.Series) -> pd.Series:
    return reihe.resample("W-FRI").last().pct_change()


def _regression(y: pd.Series, X: pd.DataFrame) -> dict | None:
    daten = pd.concat([y.rename("y"), X], axis=1).dropna()
    daten = daten[daten["y"].abs() < 0.8]  # offensichtliche Datenfehler (Splits) raus
    if len(daten) < MIN_WOCHEN:
        return None
    spalten = list(X.columns)
    A = np.column_stack([np.ones(len(daten)), daten[spalten].to_numpy()])
    werte = daten["y"].to_numpy()
    koeff, *_ = np.linalg.lstsq(A, werte, rcond=None)
    rest = werte - A @ koeff
    streuung = (rest @ rest) / (len(werte) - A.shape[1])
    fehler = np.sqrt(np.diag(streuung * np.linalg.inv(A.T @ A)))
    gesamt = (werte - werte.mean()) @ (werte - werte.mean())
    ergebnis = {"wochen": len(daten), "r2": float(1 - (rest @ rest) / gesamt) if gesamt else 0.0, "beta": {}}
    for i, s in enumerate(spalten, start=1):
        t = float(koeff[i] / fehler[i]) if fehler[i] else 0.0
        ergebnis["beta"][s] = {"wert": float(koeff[i]), "t": t, "sicher": abs(t) >= T_SICHER,
                               "von": float(koeff[i] - SPANNE * fehler[i]), "bis": float(koeff[i] + SPANNE * fehler[i])}
    # Alpha: was übrig bleibt, aufs Jahr hochgerechnet (sehr unsicher, deshalb mit Spanne)
    ergebnis["alpha"] = {"wert": float(koeff[0] * 52 * 100), "von": float((koeff[0] - SPANNE * fehler[0]) * 52 * 100),
                         "bis": float((koeff[0] + SPANNE * fehler[0]) * 52 * 100), "sicher": bool(fehler[0] and abs(koeff[0] / fehler[0]) >= T_SICHER)}
    ergebnis["_koeff"] = koeff
    ergebnis["_spalten"] = spalten
    return ergebnis


def _zerlegung(y: pd.Series, X: pd.DataFrame, erg: dict, wert: float) -> dict:
    """Woher kam die Rendite der letzten 52 Wochen? Beta × Bewegung des Faktors, Rest = alles andere."""
    daten = pd.concat([y.rename("y"), X], axis=1).dropna().iloc[-52:]
    koeff, spalten = erg["_koeff"], erg["_spalten"]
    teile = {s: float(koeff[i] * daten[s].sum() * 100) for i, s in enumerate(spalten, start=1)}
    summe = float(daten["y"].sum() * 100)
    teile["rest"] = summe - sum(teile.values())
    return {"wochen": len(daten), "summe": summe, "teile": teile, "eur": {k: v / 100 * wert for k, v in teile.items()},
            "faktor_bewegung": {s: float(daten[s].sum() * 100) for s in spalten}}


def faktor_analyse(positionen: list[dict], jahre: int = 3) -> dict:
    jahre = jahre if jahre in JAHRE else JAHRE[0]
    pos = [p for p in positionen if p.get("ticker") and (p.get("wert_eur") or p.get("extra"))]
    if not pos or not any(not p.get("extra") for p in pos):
        return {"fehler": "Keine Positionen mit Kurs gefunden."}
    start = (date.today() - timedelta(days=365 * jahre + 14)).replace(day=1).isoformat()
    noetig = {MARKT, VERGLEICH} | {f["ticker"] for f in FAKTOREN.values()}
    kurse = euro_kurse(sorted({p["ticker"] for p in pos} | noetig), start=start)
    fehlt = [t for t in noetig if t not in kurse]
    if fehlt:
        return {"fehler": f"Die Kurse der Faktor-ETFs konnten gerade nicht geladen werden ({', '.join(sorted(fehlt))})."}

    vergleich = _woche(kurse[VERGLEICH])
    X = pd.DataFrame({"markt": _woche(kurse[MARKT]), **{f: _woche(kurse[d["ticker"]]) - vergleich for f, d in FAKTOREN.items()}}).dropna()

    gesamt = sum(p["wert_eur"] for p in pos if not p.get("extra")) or 1
    zeilen, ohne, renditen = [], [], {}
    for p in pos:
        reihe = kurse.get(p["ticker"])
        rendite = _woche(reihe) if reihe is not None else None
        erg = _regression(rendite, X) if rendite is not None else None
        if not erg:
            ohne.append(p["name"])
            continue
        if p.get("extra"):  # Wert aus dem Markt: eigene Zeile, nicht im Depot
            zeilen.append({"name": p["name"], "ticker": p["ticker"], "gewicht": None, "extra": True, "r2": erg["r2"], "beta": erg["beta"]})
            continue
        renditen[p["ticker"]] = (rendite, p["wert_eur"])
        zeilen.append({"name": p["name"], "ticker": p["ticker"], "gewicht": p["wert_eur"] / gesamt * 100,
                       "r2": erg["r2"], "beta": erg["beta"]})
    if not zeilen:
        return {"fehler": "Für keine Position gibt es genug Kursdaten."}

    # Depot mit heutigen Gewichten; Wochen ohne Kurs einer Position zählen dort mit 0 %
    erfasst = sum(w for _, w in renditen.values())
    depot_rendite = sum(r.fillna(0.0) * (w / erfasst) for r, w in renditen.values())
    depot = _regression(depot_rendite, X)
    zerlegung = _zerlegung(depot_rendite, X, depot, erfasst)
    for e in (depot,):
        e.pop("_koeff"), e.pop("_spalten")

    return {
        "faktoren": [{"schluessel": f, **{k: v for k, v in d.items()}} for f, d in FAKTOREN.items()],
        "markt_name": "Weltmarkt (MSCI World)",
        "depot": depot,
        "zerlegung": zerlegung,
        "positionen": sorted(zeilen, key=lambda z: (z.get("extra", False), -(z["gewicht"] or 0))),
        "ohne_daten": ohne,
        "jahre": jahre,
        "wert": erfasst,
        "zeitraum": {"von": X.index[0].date().isoformat(), "bis": min(X.index[-1].date(), date.today()).isoformat(), "wochen": len(X)},
    }
