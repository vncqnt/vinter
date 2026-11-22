"""Schätzt, wie viel vom Sparerpauschbetrag im laufenden Jahr verbraucht ist.

Regeln, die hier abgebildet sind:
- Verluste aus Aktienverkäufen dürfen nur mit Gewinnen aus Aktienverkäufen verrechnet werden.
- Andere Verluste (z. B. Optionsscheine) dürfen mit allen Kapitalerträgen verrechnet werden.
- Bei Aktienfonds sind 30 % der Erträge steuerfrei (Teilfreistellung).
- Krypto fällt nicht unter den Sparerpauschbetrag und wird hier nicht mitgezählt.
"""
from datetime import date

import yaml

from ..benutzer import Benutzer
from .portfolio import berechne
from .tr_parser import lade_ertraege, lade_trades

TEILFREISTELLUNG = 0.7  # 70 % bleiben steuerpflichtig


def freibetrag(b: Benutzer) -> dict:
    datei = b.datei("steuer.yaml")
    einstellungen = (yaml.safe_load(datei.read_text(encoding="utf-8")) or {}) if datei.exists() else {}
    pauschbetrag = float(einstellungen.get("sparerpauschbetrag") or 1000)
    ohne_tf = set(einstellungen.get("ohne_teilfreistellung") or [])

    trades = lade_trades(b)
    ertraege = lade_ertraege(b)
    jahr = date.today().year
    verkaeufe = [v for v in berechne(trades)["verkaeufe"] if v["jahr"] == jahr]
    posten_ertraege = [e for e in ertraege if e["zeit"].year == jahr]

    def steuerpflichtig(eintrag_art: str, isin: str, wert: float) -> float:
        return wert * TEILFREISTELLUNG if eintrag_art == "FUND" and isin not in ohne_tf else wert

    aktien = [v["gewinn"] for v in verkaeufe if v["art"] == "STOCK"]
    aktien_gewinne = sum(g for g in aktien if g > 0)
    aktien_verluste = sum(g for g in aktien if g < 0)
    fonds = sum(steuerpflichtig("FUND", v["isin"], v["gewinn"]) for v in verkaeufe if v["art"] == "FUND")
    derivate = sum(v["gewinn"] for v in verkaeufe if v["art"] == "DERIVATIVE")
    krypto = [v for v in verkaeufe if v["art"] == "CRYPTO"]
    dividenden = sum(steuerpflichtig(e["art"], e["isin"], e["betrag"]) for e in posten_ertraege if e["typ"] == "DIVIDEND")
    zinsen = sum(e["betrag"] for e in posten_ertraege if e["typ"] == "INTEREST_PAYMENT")

    aktien_netto = aktien_gewinne + aktien_verluste
    allgemein = fonds + derivate + dividenden + zinsen
    if aktien_netto < 0:  # darf den allgemeinen Topf nicht mindern
        basis, aktien_vortrag = max(0.0, allgemein), -aktien_netto
    else:
        basis, aktien_vortrag = max(0.0, aktien_netto + allgemein), 0.0

    verbraucht = min(pauschbetrag, basis)
    letzte = max([t["zeit"] for t in trades] + [e["zeit"] for e in ertraege])
    return {
        "jahr": jahr,
        "pauschbetrag": pauschbetrag,
        "basis": basis,
        "verbraucht": verbraucht,
        "frei": pauschbetrag - verbraucht,
        "ueber": max(0.0, basis - pauschbetrag),
        "anteil": verbraucht / pauschbetrag * 100,
        "aktien_vortrag": aktien_vortrag,
        "krypto_verkaeufe": len(krypto),
        "quellensteuer": sum(e["steuer"] for e in posten_ertraege),
        "letzte_buchung": letzte.date().isoformat(),
        "posten": [
            {"name": "Aktien: Gewinne", "wert": aktien_gewinne, "detail": f"{sum(1 for g in aktien if g > 0)} Verkäufe"},
            {"name": "Aktien: Verluste", "wert": aktien_verluste, "detail": f"{sum(1 for g in aktien if g < 0)} Verkäufe, nur mit Aktiengewinnen verrechenbar"},
            {"name": "Fonds und ETFs", "wert": fonds, "detail": "nach 30 % Teilfreistellung"},
            {"name": "Optionsscheine", "wert": derivate, "detail": "Gewinne und Verluste"},
            {"name": "Dividenden", "wert": dividenden, "detail": "brutto"},
            {"name": "Zinsen", "wert": zinsen, "detail": "Guthabenzinsen"},
        ],
    }
