"""Deine Thesen-Level gegen die optionsimplizite Verteilung (Roadmap Q2).

Für jeden US-Wert mit Exit-Plan (Teilverkauf, Komplett, Nachkauf, Stop; in Euro in der These): Level mit dem
heutigen Verhältnis von Dollar- zu Euro-Kurs umgerechnet, dann je Verfall die Wahrscheinlichkeit, dass der Kurs
am Verfallstag darüber (Verkaufs-Level) bzw. darunter (Nachkauf, Stop) liegt. Der Wechselkurs bis dahin bleibt
außen vor. Zusätzlich grob: Chance, das Level vorher mindestens einmal zu berühren (etwa das Doppelte, gedeckelt
bei 100 %; gilt streng nur ohne Drift und Sprünge).
"""
from concurrent.futures import ThreadPoolExecutor

from ..benutzer import Benutzer
from ..markt.kurse import kurs_eur
from ..markt.wahrscheinlichkeit import p_bei, verteilung
from .exits import LEVEL
from .thesen import alle_thesen


def auswertung(b: Benutzer, positionen: list[dict]) -> dict:
    nach_ticker = {p["ticker"]: p for p in positionen if p.get("ticker")}
    plaene = []
    for these in alle_thesen(b)["thesen"]:
        for tk in these["ticker"]:
            plan = these["exits"].get(tk) or {}
            level = [(art, name, plan[feld], richtung) for art, name, feld, richtung in LEVEL if plan.get(feld) is not None]
            if level and "." not in tk and "-" not in tk:
                plaene.append((tk, these["name"], level))
    werte_mit_optionen = sorted({p["ticker"] for p in positionen if p.get("ticker") and "." not in p["ticker"] and "-" not in p["ticker"] and not p.get("extra")})
    alle = sorted({tk for tk, _, _ in plaene} | set(werte_mit_optionen))
    with ThreadPoolExecutor(max_workers=3) as pool:
        verteilungen = dict(zip(alle, pool.map(lambda tk: _sicher(tk), alle)))

    zeilen, ohne = [], []
    for tk, these, level in plaene:
        v = verteilungen.get(tk) or {}
        if v.get("fehler"):
            ohne.append({"ticker": tk, "grund": v["fehler"]})
            continue
        pos = nach_ticker.get(tk)
        eur = pos["kurs_eur"] if pos and pos.get("kurs_eur") else (kurs_eur(tk) or {}).get("preis_eur")
        if not eur:
            ohne.append({"ticker": tk, "grund": "kein Euro-Kurs"})
            continue
        faktor = v["kurs"] / eur  # Handelswährung je Euro-Kurs
        for art, name, wert_eur, richtung in level:
            lokal = wert_eur * faktor
            je = []
            for vf in v["verfaelle"]:
                p_ueber = p_bei(vf, lokal)
                p = p_ueber if richtung == "oben" else 1 - p_ueber
                je.append({"verfall": vf["verfall"], "tage": vf["tage"], "p": p, "beruehren": min(1.0, 2 * p) if (richtung == "oben") == (lokal > v["kurs"]) else 1.0})
            zeilen.append({"ticker": tk, "name": pos["name"] if pos else tk, "these": these, "art": art, "level_name": name, "richtung": richtung,
                           "level_eur": wert_eur, "level_lokal": lokal, "abstand": (lokal / v["kurs"] - 1) * 100, "waehrung": v["waehrung"],
                           "position_eur": pos.get("wert_eur") if pos else None, "verfaelle": je})
    werte = []
    for tk in alle:
        v, pos = verteilungen.get(tk) or {}, nach_ticker.get(tk) or {}
        eur = pos.get("kurs_eur")
        werte.append({"ticker": tk, "name": pos.get("name", tk), "faktor": v["kurs"] / eur if eur and v.get("kurs") else None})
    return {"level": zeilen, "ohne": ohne, "werte": werte}


def _sicher(tk):
    try:
        return verteilung(tk)
    except Exception:
        return {"fehler": "Optionsdaten nicht verfügbar."}
