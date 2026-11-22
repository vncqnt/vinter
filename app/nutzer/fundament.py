"""Punkt 24c, persönlicher Teil: eigene Kennzahl-Sets (kennzahl_sets.yaml im Ordner des Benutzers).

Die Start-Sets (KI-Capex-Check, Bilanz-Check, Qualität, Bewertung) stehen in daten/markt/kennzahlen.yaml
und gelten für alle; eigene Sets kommen dazu und können gelöscht werden.
"""
import re

import yaml

from ..benutzer import Benutzer
from ..markt.fundament.kennzahlen import bibliothek


class SetFehler(ValueError):
    pass


def _datei(b: Benutzer):
    return b.ordner / "kennzahl_sets.yaml"


def eigene(b: Benutzer) -> dict:
    try:
        roh = yaml.safe_load(_datei(b).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except yaml.YAMLError:
        raise SetFehler("kennzahl_sets.yaml ist nicht lesbar.")
    return {str(k): {"id": str(k), "name": str(v.get("name") or k), "kennzahlen": [str(x) for x in v.get("kennzahlen") or []], "eigen": True}
            for k, v in roh.items() if isinstance(v, dict)}


def alle_sets(b: Benutzer) -> list[dict]:
    bib = bibliothek()
    return [{**s, "eigen": False} for s in bib["sets"].values()] + list(eigene(b).values())


def set_speichern(b: Benutzer, name: str, kennzahlen: list) -> list[dict]:
    gueltig = {k["id"] for k in bibliothek()["kennzahlen"]} | {"umsatz_wachstum", "perf_6m"}
    name = str(name or "").strip()[:50]
    liste = [k for k in dict.fromkeys(str(x) for x in kennzahlen or []) if k in gueltig]
    if not name or not liste:
        raise SetFehler("Bitte einen Namen und mindestens eine Kennzahl angeben.")
    sets = eigene(b)
    kid = "eigen_" + (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "set")
    sets[kid] = {"name": name, "kennzahlen": liste}
    b.ordner.mkdir(parents=True, exist_ok=True)
    _datei(b).write_text(yaml.safe_dump({k: {"name": v["name"], "kennzahlen": v["kennzahlen"]} for k, v in sets.items()}, allow_unicode=True, sort_keys=True), encoding="utf-8")
    return alle_sets(b)


def set_loeschen(b: Benutzer, kid: str) -> list[dict]:
    sets = eigene(b)
    sets.pop(kid, None)
    if sets:
        _datei(b).write_text(yaml.safe_dump({k: {"name": v["name"], "kennzahlen": v["kennzahlen"]} for k, v in sets.items()}, allow_unicode=True, sort_keys=True), encoding="utf-8")
    elif _datei(b).exists():
        _datei(b).unlink()
    return alle_sets(b)
