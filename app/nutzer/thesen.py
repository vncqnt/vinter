"""Thesen lesen und speichern. Jede These ist eine YAML-Datei im Ordner thesen des Benutzers.

Eine These mit mehreren Tickern ist ein "Ordner" (z. B. KI-Strom). In der Datei steht
der Exit-Plan bei einem Ticker unter "exit", bei mehreren je Ticker unter "exits".
"""
import os
import re
from pathlib import Path

import yaml

from ..benutzer import Benutzer


def _ordner(b: Benutzer):
    return b.datei("thesen")


ID_MUSTER = re.compile(r"^[a-z0-9-]{1,80}$")
TICKER_MUSTER = re.compile(r"^[A-Z0-9.^=-]{1,15}$")
LISTEN = ["katalysatoren", "bestaetigung", "bruch", "news_themen"]
ZAHLEN = ["teilverkauf_kurs", "teilverkauf_anteil", "komplett_kurs", "nachkauf_unter", "stop"]


class ThesenFehler(Exception):
    """Eine Eingabe ist ungültig. Der Text wird dem Nutzer angezeigt."""


def _exit_fuer_seite(e: dict | None) -> dict:
    e = e or {}
    teil = e.get("teilverkauf") or {}
    anteil = teil.get("anteil")
    return {
        "teilverkauf_kurs": teil.get("kurs_eur"),
        "teilverkauf_anteil": None if anteil is None else round(anteil * 100, 4),  # auf der Seite in %
        "komplett_kurs": (e.get("komplett") or {}).get("kurs_eur"),
        "nachkauf_unter": e.get("nachkauf_unter_eur"),
        "stop": e.get("stop_eur"),
    }


def _exit_fuer_datei(e: dict) -> dict:
    roh = {}
    if e["teilverkauf_kurs"] is not None or e["teilverkauf_anteil"] is not None:
        teil = {}
        if e["teilverkauf_kurs"] is not None:
            teil["kurs_eur"] = e["teilverkauf_kurs"]
        if e["teilverkauf_anteil"] is not None:
            teil["anteil"] = round(e["teilverkauf_anteil"] / 100, 6)
        roh["teilverkauf"] = teil
    if e["komplett_kurs"] is not None:
        roh["komplett"] = {"kurs_eur": e["komplett_kurs"]}
    if e["nachkauf_unter"] is not None:
        roh["nachkauf_unter_eur"] = e["nachkauf_unter"]
    if e["stop"] is not None:
        roh["stop_eur"] = e["stop"]
    return roh


def _als_liste(wert) -> list[str]:
    if isinstance(wert, list):
        return [str(x) for x in wert]
    return [str(wert)] if wert else []


def _lese(datei: Path) -> dict:
    roh = yaml.safe_load(datei.read_text(encoding="utf-8"))
    if not isinstance(roh, dict) or not roh.get("name"):
        raise ValueError("kein gültiger Inhalt")
    ticker = [str(t) for t in _als_liste(roh.get("ticker"))]
    if len(ticker) == 1:
        exits = {ticker[0]: _exit_fuer_seite(roh.get("exit"))}
    else:
        alle = roh.get("exits") or {}
        exits = {t: _exit_fuer_seite(alle.get(t)) for t in ticker}
    return {
        "id": datei.stem,
        "name": str(roh["name"]),
        "ticker": ticker,
        "kernthese": str(roh.get("kernthese") or ""),
        **{feld: _als_liste(roh.get(feld)) for feld in LISTEN},
        "exits": exits,
    }


def alle_thesen(b: Benutzer) -> dict:
    thesen, meldungen = [], []
    _ordner(b).mkdir(parents=True, exist_ok=True)
    for datei in sorted(_ordner(b).glob("*.yaml")):
        try:
            thesen.append(_lese(datei))
        except Exception:
            meldungen.append(f"Die Datei {datei.name} konnte nicht gelesen werden und wird übersprungen.")
    thesen.sort(key=lambda t: t["name"].lower())
    return {"thesen": thesen, "meldungen": meldungen}


def _zahl(wert, beschreibung: str):
    if wert is None or wert == "":
        return None
    if isinstance(wert, bool):
        raise ThesenFehler(f"{beschreibung}: Bitte eine Zahl eingeben.")
    try:
        z = float(wert)
    except (TypeError, ValueError):
        raise ThesenFehler(f"{beschreibung}: Bitte eine Zahl eingeben.") from None
    if not 0 <= z < 1e9:
        raise ThesenFehler(f"{beschreibung}: Die Zahl liegt außerhalb des sinnvollen Bereichs.")
    return z


def _pruefe(d) -> dict:
    if not isinstance(d, dict):
        raise ThesenFehler("Die Eingabe ist ungültig.")
    name = str(d.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ThesenFehler("Bitte einen Namen mit höchstens 80 Zeichen eingeben.")
    kernthese = str(d.get("kernthese") or "").strip()
    if len(kernthese) > 2000:
        raise ThesenFehler("Die Kernthese ist zu lang, bitte kürzen.")

    roh_ticker = d.get("ticker")
    if not isinstance(roh_ticker, list):
        raise ThesenFehler("Die Ticker sind ungültig.")
    ticker = list(dict.fromkeys(str(t).strip().upper() for t in roh_ticker if str(t).strip()))
    if not ticker:
        raise ThesenFehler("Bitte mindestens einen Ticker eingeben, z. B. UBER oder ENR.DE.")
    falsch = [t for t in ticker if not TICKER_MUSTER.match(t)]
    if falsch:
        raise ThesenFehler(f"Ungültiger Ticker: {', '.join(falsch)}")

    sauber = {"name": name, "ticker": ticker, "kernthese": kernthese}
    for feld in LISTEN:
        werte = d.get(feld) or []
        if not isinstance(werte, list) or len(werte) > 50:
            raise ThesenFehler("Eine der Listen ist ungültig oder zu lang.")
        sauber[feld] = [str(x).strip()[:300] for x in werte if str(x).strip()]

    exits = d.get("exits") if isinstance(d.get("exits"), dict) else {}
    sauber["exits"] = {}
    for t in ticker:
        e = exits.get(t) if isinstance(exits.get(t), dict) else {}
        werte = {feld: _zahl(e.get(feld), t) for feld in ZAHLEN}
        anteil = werte["teilverkauf_anteil"]
        if anteil is not None and not 0 < anteil <= 100:
            raise ThesenFehler(f"{t}: Der Anteil beim Teilverkauf muss zwischen 0 und 100 % liegen.")
        sauber["exits"][t] = werte
    return sauber


def _schreibe(b: Benutzer, thesen_id: str, d: dict) -> None:
    inhalt = {"name": d["name"], "ticker": d["ticker"], "kernthese": d["kernthese"]}
    inhalt.update({feld: d[feld] for feld in LISTEN})
    if len(d["ticker"]) == 1:
        inhalt["exit"] = _exit_fuer_datei(d["exits"][d["ticker"][0]])
    else:
        inhalt["exits"] = {t: _exit_fuer_datei(e) for t, e in d["exits"].items()}
    ordner = _ordner(b)
    ordner.mkdir(parents=True, exist_ok=True)
    ziel = ordner / f"{thesen_id}.yaml"
    zwischen = ordner / f"{thesen_id}.tmp"  # erst komplett schreiben, dann austauschen
    zwischen.write_text(yaml.safe_dump(inhalt, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(zwischen, ziel)


def _neue_id(b: Benutzer, name: str) -> str:
    text = name.lower()
    for alt, neu in {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}.items():
        text = text.replace(alt, neu)
    basis = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:70] or "these"
    kandidat, nummer = basis, 2
    while (_ordner(b) / f"{kandidat}.yaml").exists():
        kandidat, nummer = f"{basis}-{nummer}", nummer + 1
    return kandidat


def _pfad(b: Benutzer, thesen_id: str) -> Path:
    if not ID_MUSTER.match(thesen_id):
        raise ThesenFehler("Diese These gibt es nicht.")
    pfad = _ordner(b) / f"{thesen_id}.yaml"
    if not pfad.exists():
        raise ThesenFehler("Diese These gibt es nicht mehr.")
    return pfad


def anlegen(b: Benutzer, d) -> dict:
    sauber = _pruefe(d)
    thesen_id = _neue_id(b, sauber["name"])
    _schreibe(b, thesen_id, sauber)
    return {"id": thesen_id}


def aendern(b: Benutzer, thesen_id: str, d) -> dict:
    _pfad(b, thesen_id)
    _schreibe(b, thesen_id, _pruefe(d))
    return {"id": thesen_id}


def loeschen(b: Benutzer, thesen_id: str) -> dict:
    """Verschiebt die Datei in den Papierkorb, damit nichts endgültig verloren geht."""
    pfad = _pfad(b, thesen_id)
    korb = _ordner(b) / "papierkorb"
    korb.mkdir(exist_ok=True)
    ziel, nummer = korb / pfad.name, 2
    while ziel.exists():
        ziel, nummer = korb / f"{pfad.stem}-{nummer}.yaml", nummer + 1
    os.replace(pfad, ziel)
    return {"id": thesen_id}
