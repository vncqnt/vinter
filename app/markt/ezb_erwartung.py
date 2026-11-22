"""Erwarteter EZB-Zinspfad als Näherung aus der Zinskurve (Punkt M5).

Freie Futures auf €STR oder Euribor gibt es nicht (Eurex und ICE verkaufen die Daten). Die EZB veröffentlicht aber
jeden Tag ihre Zinskurve der Euro-Staatsanleihen mit AAA-Rating, samt Terminzinsen (instantaneous forward rates).
Ein Terminzins in 6 Monaten ist grob der kurzfristige Zins, den der Markt für dann erwartet.

Rechnung: erwarteter Einlagensatz in h Monaten = Terminzins(h) − BASIS + (Einlagensatz heute − €STR heute).
BASIS: AAA-Staatsanleihen rentieren in ruhigen Zeiten etwas unter dem €STR (Median 2019–2025 ohne die Jahre mit
großen Zinsschritten etwa −0,08 Punkte). Dazu kommt eine Laufzeitprämie, die mit der Laufzeit wächst: Je weiter
in der Zukunft, desto unsicherer. Deshalb keine Wahrscheinlichkeiten je Sitzung wie bei der Fed, nur der Pfad.
"""
from datetime import date

import pandas as pd

from .makrodaten import _ezb

BASIS = -0.08
SCHRITT = 0.25
KURVE = "YC/B.U2.EUR.4F.G_N_A.SV_C_YM."
LAUFZEITEN = [("3M", 3), ("6M", 6), ("9M", 9), ("1Y", 12), ("2Y", 24)]


def pfad(termin: dict[int, float], estr: float, einlage: float) -> list[dict]:
    """Reine Funktion: Terminzinsen je Monat → erwarteter Einlagensatz und Veränderung gegenüber heute."""
    aus = []
    for monate, f in sorted(termin.items()):
        erwartet = f - BASIS + (einlage - estr)
        aus.append({"monate": monate, "einlage": round(erwartet, 3), "veraenderung": round(erwartet - einlage, 3),
                    "schritte": round((erwartet - einlage) / SCHRITT, 1)})
    return aus


def einordnen(p: list[dict]) -> str:
    """Satz nach festen Regeln."""
    if not p:
        return ""
    kurz = next((x for x in p if x["monate"] == 6), p[0])
    jahr = next((x for x in p if x["monate"] == 12), p[-1])
    def art(v):
        if v >= 0.15:
            return f"Erhöhungen um zusammen etwa {v:+.2f} Punkte (≈ {v / SCHRITT:.1f} Schritte zu 0,25)".replace(".", ",")
        if v <= -0.15:
            return f"Senkungen um zusammen etwa {v:+.2f} Punkte (≈ {abs(v) / SCHRITT:.1f} Schritte zu 0,25)".replace(".", ",")
        return "keine klare Änderung"
    return f"Die Zinskurve deutet für die nächsten 6 Monate auf {art(kurz['veraenderung'])} hin, auf 12 Monate auf {art(jahr['veraenderung'])}."


def erwartung() -> dict:
    estr_r = _ezb("EST/B.EU000A2X2A25.WT")
    einlage_r = _ezb("FM/D.U2.EUR.4F.KR.DFR.LEV")
    termin_r = {m: _ezb(KURVE + f"IF_{k}") for k, m in LAUFZEITEN}
    stand = min(s.index[-1] for s in termin_r.values())
    estr = float(estr_r[estr_r.index <= stand].iloc[-1])
    einlage = float(einlage_r[einlage_r.index <= stand].iloc[-1])
    p = pfad({m: float(s[s.index <= stand].iloc[-1]) for m, s in termin_r.items()}, estr, einlage)
    # Verlauf: in 12 Monaten eingepreiste Veränderung (2 Jahre, wöchentlich)
    tab = pd.DataFrame({"f": termin_r[12], "estr": estr_r, "einlage": einlage_r}).sort_index().ffill().dropna()
    tab = tab[tab.index >= tab.index[-1] - pd.DateOffset(years=2)].resample("W-FRI").last().dropna()
    verlauf = {"daten": [d.strftime("%Y-%m-%d") for d in tab.index],
               "werte": [round(float(r.f - BASIS - r.estr), 3) for r in tab.itertuples()]}
    return {"stand": stand.strftime("%Y-%m-%d"), "estr": estr, "einlage": einlage, "pfad": p, "satz": einordnen(p),
            "verlauf_12m": verlauf, "basis": BASIS, "heute": date.today().isoformat()}
