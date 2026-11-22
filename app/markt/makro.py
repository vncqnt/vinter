"""Liest die selbst gepflegten Makro-Termine aus daten/markt/makro_termine.yaml."""
from datetime import date

import yaml

from ..konfig import MARKT_DATEN

DATEI = MARKT_DATEN / "makro_termine.yaml"


def _text(wert):
    return None if wert is None else str(wert)


def _uhrzeit(wert):
    # Ohne Anführungszeichen liest YAML 14:30 als Zahl (14 * 60 + 30). Das fangen wir ab.
    if isinstance(wert, int):
        return f"{wert // 60:02d}:{wert % 60:02d}"
    return _text(wert)


def makro_termine() -> dict:
    if not DATEI.exists():
        return {"fehler": "Die Datei daten/markt/makro_termine.yaml fehlt."}
    try:
        eintraege = yaml.safe_load(DATEI.read_text(encoding="utf-8")) or []
    except yaml.YAMLError:
        return {"fehler": "In daten/markt/makro_termine.yaml ist ein Tippfehler. Bitte die zuletzt geänderte Zeile prüfen."}

    termine, meldungen = [], []
    for e in eintraege:
        if not (isinstance(e, dict) and isinstance(e.get("datum"), date) and e.get("name")):
            meldungen.append(f"Makro-Eintrag übersprungen, Datum oder Name fehlt: {e}")
            continue
        termine.append({
            "datum": e["datum"].isoformat(),
            "uhrzeit": _uhrzeit(e.get("uhrzeit")),
            "name": str(e["name"]),
            "wichtigkeit": _text(e.get("wichtigkeit")),
            "erwartung": _text(e.get("erwartung")),
            "tatsaechlich": _text(e.get("tatsaechlich")),
        })
    # Vergangene Termine ohne eigenen Eintrag: tatsächlicher Wert aus den gespeicherten Makro-Reihen (BLS, FRED, EZB)
    try:
        from .makrodaten import ergebnis, ergebnisse_aus_speicher
        bls, fed_z, ezb_z = ergebnisse_aus_speicher()
        heute = date.today().isoformat()
        for t in termine:
            if t["tatsaechlich"] is None and t["datum"] <= heute:
                e = ergebnis(t["name"], date.fromisoformat(t["datum"]), bls, fed_z, ezb_z)
                if e:
                    t["tatsaechlich"], t["automatisch"] = e["text"], e["quelle"]
    except Exception:
        pass  # ohne gespeicherte Reihen bleibt es beim eigenen Eintrag
    termine.sort(key=lambda t: (t["datum"], t["uhrzeit"] or ""))
    return {"termine": termine, "meldungen": meldungen}
