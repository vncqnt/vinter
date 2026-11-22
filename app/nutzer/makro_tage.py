"""Event-Studie Makro (Roadmap Q7): Wie reagierten deine Positionen an CPI-, Arbeitsmarkt- und Fed-Tagen, getrennt
nach Überraschung?

Termine: veröffentlichte CPI- und Arbeitsmarktberichte (BLS-Archiv) und Fed-Entscheidungen (FOMC-Kalender), seit 2019.
Überraschung: Konsensprognosen gibt es nicht kostenlos. Deshalb das übliche Marktmaß: Veränderung der 2-jährigen
US-Rendite am Ereignistag (FRED DGS2). Steigt sie um mindestens 5 Basispunkte, hat der Markt den Bericht als
"heißer" (mehr Inflation, stärkerer Arbeitsmarkt, strengere Fed) gelesen, fällt sie um mindestens 5, als "kühler".
Stehen Erwartung und Ergebnis in makro_termine.yaml, gelten diese.
Reaktion: wie bei den Fed-Tagen Schluss am Handelstag davor bis Schluss am Folgetag, heutige Positionen in Euro;
Positionen mit Quartalszahlen im Fenster zählen an dem Termin nicht mit.
"""
from datetime import date

import numpy as np
import pandas as pd

from ..benutzer import Benutzer
from ..markt.abruf import fred
from ..markt.euro_kurse import euro_kurse
from ..markt.eventbewegung import vergangene_termine
from ..markt.fed_tage import reaktionen
from .fed_tage import _im_fenster, _zahlen_tage

AB = "2019-01-01"
SCHWELLE = 0.05  # Prozentpunkte = 5 Basispunkte
ARTEN = {"cpi": "Inflation (CPI)", "nfp": "Arbeitsmarktbericht", "fed": "Fed-Zinsentscheid"}


def einstufen(aenderung: float | None, schwelle: float = SCHWELLE) -> str | None:
    """heißer / kühler / wie erwartet aus der Veränderung der 2-jährigen Rendite. Reine Funktion."""
    if aenderung is None or pd.isna(aenderung):
        return None
    return "heißer" if aenderung >= schwelle else "kühler" if aenderung <= -schwelle else "wie erwartet"


def _eigene_ueberraschungen() -> dict[str, str]:
    """Selbst eingetragene Erwartung/Ergebnis aus makro_termine.yaml (gilt vor dem Marktmaß)."""
    try:
        from ..markt.makro import makro_termine
        aus = {}
        for t in makro_termine().get("termine", []):
            e, ist = t.get("erwartung"), t.get("tatsaechlich")
            if e is None or ist is None:
                continue
            try:
                e, ist = float(str(e).replace("%", "").replace(",", ".")), float(str(ist).replace("%", "").replace(",", "."))
            except ValueError:
                continue
            aus[t["datum"]] = "heißer" if ist > e else "kühler" if ist < e else "wie erwartet"
        return aus
    except Exception:
        return {}


def auswertung(b: Benutzer, positionen: list[dict]) -> dict:
    termine = {art: [d for d in liste if d >= AB] for art, liste in vergangene_termine().items()}
    zwei = fred("DGS2").dropna()
    eigene = _eigene_ueberraschungen()

    def ueberraschung(d: str):
        if d in eigene:
            return eigene[d], "eigene Angabe", None
        ts = pd.Timestamp(d)
        vor = zwei[zwei.index < ts]
        am = zwei[zwei.index >= ts]
        if vor.empty or am.empty or (am.index[0] - ts).days > 3:
            return None, None, None
        aend = float(am.iloc[0] - vor.iloc[-1])
        return einstufen(aend), "2-jährige Rendite", aend

    pos = [p for p in positionen if p.get("ticker") and p.get("wert_eur") and not p.get("extra")]
    kurse = euro_kurse(sorted({p["ticker"] for p in pos}), start="2018-12-01")
    gewicht = {p["ticker"]: p["wert_eur"] for p in pos if p["ticker"] in kurse}
    gesamt = sum(p["wert_eur"] for p in pos)
    namen = {p["ticker"]: p["name"] for p in pos}
    alle_tage = sorted({d for liste in termine.values() for d in liste})
    je_wert = {tk: reaktionen(kurse[tk], alle_tage) for tk in gewicht}
    zahlen = _zahlen_tage(list(gewicht))

    ergebnis = {}
    for art, liste in termine.items():
        zeilen = []
        for d in liste:
            klasse, quelle, aend = ueberraschung(d)
            dabei = [tk for tk in gewicht if d in je_wert[tk] and je_wert[tk][d]["folge"] is not None and not _im_fenster(zahlen[tk], d)]
            summe = sum(gewicht[tk] for tk in dabei)
            if not dabei or summe / gesamt < 0.3 or klasse is None:
                continue
            depot = sum(je_wert[tk][d]["folge"] * gewicht[tk] for tk in dabei) / summe
            zeilen.append({"datum": d, "klasse": klasse, "quelle": quelle, "rendite_2j": aend, "depot": depot, "abdeckung": summe / gesamt})
        def schnitt(k):
            w = [z["depot"] for z in zeilen if z["klasse"] == k]
            return {"anzahl": len(w), "schnitt": float(np.mean(w)) * 100 if w else None, "plus": float(np.mean([x > 0 for x in w])) * 100 if w else None}
        werte = []
        for tk in gewicht:
            je = {}
            for k in ("heißer", "kühler", "wie erwartet"):
                w = [je_wert[tk][z["datum"]]["folge"] for z in zeilen if z["klasse"] == k and z["datum"] in je_wert[tk]
                     and je_wert[tk][z["datum"]]["folge"] is not None and not _im_fenster(zahlen[tk], z["datum"])]
                je[k] = {"anzahl": len(w), "schnitt": float(np.mean(w)) * 100 if w else None}
            h, c = je["heißer"]["schnitt"], je["kühler"]["schnitt"]
            werte.append({"ticker": tk, "name": namen[tk], "gewicht": gewicht[tk] / gesamt * 100, **je,
                          "unterschied": (h - c) if h is not None and c is not None else None})
        werte.sort(key=lambda w: (w["unterschied"] is None, w["unterschied"] or 0))
        ergebnis[art] = {"name": ARTEN[art], "anzahl": len(zeilen), "depot": {k: schnitt(k) for k in ("heißer", "kühler", "wie erwartet")},
                         "positionen": werte, "termine": [{**z, "depot": z["depot"] * 100} for z in zeilen[::-1][:40]]}
    return {"arten": ergebnis, "ab": AB, "schwelle_bp": SCHWELLE * 100, "eigene": len(eigene)}
