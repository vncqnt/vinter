"""Szenario-Rechner, Benutzerschicht: gespeicherte Szenario-Sets, eigene Position in Euro, Kurs-Level der These.

Gespeichert pro Benutzer in szenarien.yaml (höchstens 30 Sets): Name, Ticker, Stichtag, 2–5 Szenarien
(Name, Wahrscheinlichkeit in %, Bewegung in %, Unsicherheit in %), welches Szenario das "große" ist.
Level der These (Teilverkauf, Komplett, Nachkauf, Stop) sind in Euro gespeichert; für die Simulation zählt nur der
Abstand in Prozent zum heutigen Euro-Kurs (der Wechselkurs bis dahin bleibt außen vor).
"""
import secrets
import threading

import yaml

from ..benutzer import Benutzer
from ..markt import szenario as sz
from .exits import LEVEL
from .thesen import alle_thesen

MAX_SETS = 30
_sperre = threading.Lock()
BEISPIEL = {"name": "NVIDIA: Bleibt Huang?", "ticker": "NVDA", "datum": None, "gross": 0,
            "szenarien": [{"name": "Huang wird entlassen", "p": 10, "bewegung": -15, "unsicherheit": 5},
                          {"name": "Huang bleibt", "p": 90, "bewegung": 2, "unsicherheit": 0}]}


def _datei(b: Benutzer):
    return b.datei("szenarien.yaml")


def sets(b: Benutzer) -> list[dict]:
    d = _datei(b)
    if not d.exists():
        return []
    try:
        return yaml.safe_load(d.read_text(encoding="utf-8")) or []
    except Exception:
        return []


def _zahl(x, lo, hi, standard=0.0) -> float:
    try:
        return max(lo, min(hi, float(str(x).replace(",", "."))))
    except (TypeError, ValueError):
        return standard


def pruefen(s: dict) -> dict:
    ticker = str(s.get("ticker") or "").strip().upper()[:20]
    if not ticker:
        raise ValueError("Ticker fehlt.")
    datum = str(s.get("datum") or "")[:10]
    if len(datum) != 10:
        raise ValueError("Stichtag fehlt.")
    szen = []
    for x in (s.get("szenarien") or [])[:5]:
        szen.append({"name": str(x.get("name") or "Szenario")[:60], "p": _zahl(x.get("p"), 0, 100),
                     "bewegung": _zahl(x.get("bewegung"), -95, 300), "unsicherheit": _zahl(x.get("unsicherheit"), 0, 90)})
    if len(szen) < 2:
        raise ValueError("Bitte mindestens 2 Szenarien.")
    gross = int(_zahl(s.get("gross"), 0, len(szen) - 1))
    return {"id": s.get("id") or secrets.token_hex(4), "name": str(s.get("name") or ticker)[:60], "ticker": ticker,
            "datum": datum, "gross": gross, "szenarien": szen}


def speichern(b: Benutzer, s: dict) -> dict:
    eintrag = pruefen(s)
    with _sperre:
        liste = [x for x in sets(b) if x.get("id") != eintrag["id"]]
        liste.append(eintrag)
        d = _datei(b)
        d.parent.mkdir(parents=True, exist_ok=True)
        zw = d.with_suffix(".tmp")
        zw.write_text(yaml.safe_dump(liste[-MAX_SETS:], allow_unicode=True, sort_keys=False), encoding="utf-8")
        zw.replace(d)
    return eintrag


def loeschen(b: Benutzer, sid: str) -> None:
    with _sperre:
        liste = [x for x in sets(b) if x.get("id") != sid]
        d = _datei(b)
        if d.exists():
            d.write_text(yaml.safe_dump(liste, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _level(b: Benutzer, ticker: str, kurs_eur: float | None) -> list[dict]:
    if not kurs_eur:
        return []
    aus = []
    for these in alle_thesen(b)["thesen"]:
        plan = (these.get("exits") or {}).get(ticker) or {}
        for art, name, feld, richtung in LEVEL:
            if plan.get(feld) is not None:
                aus.append({"art": art, "name": name, "eur": plan[feld], "abstand": plan[feld] / kurs_eur - 1, "these": these["name"]})
    return aus


def rechnen(b: Benutzer, s: dict, positionen: list[dict]) -> dict:
    e = pruefen(s)
    szen = [{"name": x["name"], "p": x["p"] / 100, "bewegung": x["bewegung"] / 100, "unsicherheit": x["unsicherheit"] / 100} for x in e["szenarien"]]
    pos = next((p for p in positionen if p.get("ticker") == e["ticker"] and not p.get("extra")), None)
    kurs_eur = pos.get("kurs_eur") if pos else None
    if not kurs_eur:
        try:
            from ..markt.kurse import kurs_eur as hole
            kurs_eur = (hole(e["ticker"]) or {}).get("preis_eur")
        except Exception:
            kurs_eur = None
    level = _level(b, e["ticker"], kurs_eur)
    aus = sz.rechnen(e["ticker"], e["datum"], szen, e["gross"], [l["abstand"] for l in level])
    aus["position_eur"] = pos.get("wert_eur") if pos else None
    aus["position_name"] = pos.get("name") if pos else None
    aus["level"] = [{**l, "beruehren": aus.get("simulation", {}).get("beruehren", [None] * len(level))[i]} for i, l in enumerate(level)]
    aus["eingabe"] = e
    return aus


def beispiel() -> dict:
    from datetime import date, timedelta
    return {**BEISPIEL, "datum": (date.today() + timedelta(days=30)).isoformat()}
