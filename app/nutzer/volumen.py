"""Preis-Volumen-Analyse für die eigenen Aktien: Profil und Kerzen aus app/markt/volumen.py,
dazu Einstandskurs, Kaufdatum und die Exit-Level aus der These.

Alle Kurse werden mit dem heutigen Wechselkurs in Euro umgerechnet (wie bei den Exit-Alarmen).
So lassen sich die Euro-Level direkt neben das Profil legen.
"""
from ..benutzer import Benutzer
from ..markt.volumen import einordnen, preis_volumen, relatives_volumen
from .exits import LEVEL
from .perioden import REST
from .thesen import alle_thesen
from .tr_parser import lade_trades


def _aktien(positionen: list[dict]) -> list[dict]:
    aktien = [p for p in positionen if p.get("art") == "STOCK" and p.get("ticker") and (p.get("kurs_eur") or p.get("extra"))]
    aktien.sort(key=lambda p: (bool(p.get("extra")), -(p.get("wert_eur") or 0)))
    return aktien


def _seit_kauf(b: Benutzer, isin: str):
    """Beginn der aktuellen Haltedauer: der Kauf, nach dem der Bestand zuletzt von 0 anstieg."""
    beginn, stueck = None, 0.0
    for t in sorted((t for t in lade_trades(b) if t["isin"] == isin), key=lambda t: t["zeit"]):
        if stueck <= REST and t["stueck"] > 0:
            beginn = t["zeit"].date()
        stueck += t["stueck"]
    return beginn


def _skalieren(daten: dict, faktor: float) -> None:
    for k in daten["kerzen"]:
        for f in ("o", "h", "l", "c"):
            k[f] *= faktor
    p = daten["profil"]
    for f in ("tief", "hoch", "breite", "poc", "va_unten", "va_oben"):
        p[f] *= faktor
    for l in p["luecken"]:
        l["von"] *= faktor
        l["bis"] *= faktor


def analyse(b: Benutzer, positionen: list[dict], ticker: str | None, zeitraum: str) -> dict:
    aktien = _aktien(positionen)
    werte = [{"ticker": p["ticker"], "name": p["name"], "extra": bool(p.get("extra"))} for p in aktien]
    if not aktien:
        return {"werte": [], "fehler": "Im Depot sind gerade keine Aktien mit Kurs."}
    pos = next((p for p in aktien if p["ticker"] == ticker), aktien[0])
    if pos.get("extra") and not pos.get("kurs_eur"):
        # Wert aus dem Markt: heutiger Kurs in Euro für die Umrechnung (wie bei den Depotwerten)
        from ..markt.kurse import kurs_eur
        try:
            pos = {**pos, "kurs_eur": kurs_eur(pos["ticker"]).get("preis_eur")}
        except Exception:
            pos = {**pos, "kurs_eur": None}
    seit = _seit_kauf(b, pos["isin"]) if pos.get("isin") else None
    ergebnis = {"werte": werte, "ticker": pos["ticker"], "name": pos["name"], "zeitraum": zeitraum,
                "kurs": pos["kurs_eur"], "seit_kauf": seit.isoformat() if seit else None}

    try:
        daten = preis_volumen(pos["ticker"], zeitraum, seit)
    except Exception:
        return {**ergebnis, "fehler": "Für diesen Zeitraum gibt es gerade keine Kursdaten mit Volumen."}
    faktor = pos["kurs_eur"] / daten["schluss"] if daten["schluss"] and pos.get("kurs_eur") else 1.0
    _skalieren(daten, faktor)
    profil = daten["profil"]

    level = [{"art": "einstand", "name": "Mein Einstand", "kurs": pos["einstand_gesamt"] / pos["stueck"]}] if pos.get("stueck") else []
    for these in alle_thesen(b)["thesen"]:
        plan = these["exits"].get(pos["ticker"]) or {}
        for art, name, feld, _ in LEVEL:
            if plan.get(feld) is not None:
                level.append({"art": art, "name": name, "kurs": float(plan[feld])})
    for l in level:
        l.update(einordnen(profil, l["kurs"]))

    try:
        rvol = relatives_volumen(pos["ticker"])
    except Exception:
        rvol = {"fehler": "Das relative Volumen lässt sich gerade nicht berechnen."}
    return {**ergebnis, **daten, "level": level, "rvol": rvol, "faktor": faktor}
