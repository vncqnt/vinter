"""Das selbst zusammengestellte Dashboard eines Benutzers (intern "Tafel"): welche Widgets wo und wie groß.

Gespeichert in tafel.yaml im Ordner des Benutzers. Es gibt mehrere Layouts (z. B. "Morgens",
"Earnings-Tag"), eines davon ist aktiv. Welche Kacheln es gibt, weiß nur die Oberfläche; hier wird
nur geprüft, dass die Angaben vernünftig aussehen.
"""
import re

import yaml

from ..benutzer import Benutzer

MAX_LAYOUTS = 12
MAX_KACHELN = 40
SPALTEN = 12
MAX_ZEILEN = 12
MAX_Y = 400

STANDARD = {
    "aktiv": "Mein Überblick",
    "layouts": [{
        "name": "Mein Überblick",
        "kacheln": [
            {"art": "bewegung", "w": 12, "h": 3},
            {"art": "depotwert", "w": 3, "h": 2},
            {"art": "heute", "w": 3, "h": 3},
            {"art": "tageschart", "w": 6, "h": 3},
            {"art": "maerkte", "w": 4, "h": 4},
            {"art": "sektoren", "w": 4, "h": 4},
            {"art": "news", "w": 4, "h": 4},
            {"art": "earnings", "w": 4, "h": 3},
            {"art": "makro", "w": 4, "h": 3},
            {"art": "fed", "w": 4, "h": 3},
        ],
    }],
}


class TafelFehler(Exception):
    pass


def _datei(b: Benutzer):
    return b.datei("tafel.yaml")


def lesen(b: Benutzer) -> dict:
    datei = _datei(b)
    if not datei.exists():
        return STANDARD
    try:
        daten = yaml.safe_load(datei.read_text(encoding="utf-8")) or {}
        return _pruefen(daten)
    except Exception:
        return STANDARD


def _pruefen(daten: dict) -> dict:
    if not isinstance(daten, dict):
        raise TafelFehler("Ungültiges Dashboard.")
    layouts = []
    namen = set()
    for lay in (daten.get("layouts") or [])[:MAX_LAYOUTS]:
        name = str(lay.get("name", "")).strip()[:40]
        if not name or name in namen:
            continue
        namen.add(name)
        kacheln = []
        for k in (lay.get("kacheln") or [])[:MAX_KACHELN]:
            art = str(k.get("art", ""))
            if not re.fullmatch(r"[a-z0-9-]{1,30}", art):
                continue
            w = min(SPALTEN, max(1, int(k.get("w", 3))))
            h = min(MAX_ZEILEN, max(1, int(k.get("h", 2))))
            kachel = {"art": art, "w": w, "h": h}
            # Lage im Raster (fehlt bei alten Layouts, dann ordnet die Oberfläche sie selbst ein)
            if isinstance(k.get("x"), int) and isinstance(k.get("y"), int):
                kachel["x"] = min(SPALTEN - w, max(0, k["x"]))
                kachel["y"] = min(MAX_Y, max(0, k["y"]))
            kacheln.append(kachel)
        eintrag = {"name": name, "kacheln": kacheln}
        # Eigene Reihenfolge fürs Handy (eine Spalte): Liste aus Widget und Höhe. Fehlt sie, zeigt das Handy
        # die Widgets des großen Bildschirms von oben links nach unten rechts.
        if isinstance(lay.get("handy"), list):
            handy, gesehen = [], set()
            for k in lay["handy"][:MAX_KACHELN]:
                art = str((k or {}).get("art", ""))
                if not re.fullmatch(r"[a-z0-9-]{1,30}", art) or art in gesehen:
                    continue
                gesehen.add(art)
                handy.append({"art": art, "h": min(MAX_ZEILEN, max(1, int(k.get("h", 3))))})
            eintrag["handy"] = handy
        layouts.append(eintrag)
    if not layouts:
        raise TafelFehler("Mindestens ein Layout wird gebraucht.")
    aktiv = str(daten.get("aktiv", ""))
    if aktiv not in namen:
        aktiv = layouts[0]["name"]
    return {"aktiv": aktiv, "layouts": layouts}


def speichern(b: Benutzer, daten: dict) -> dict:
    sauber = _pruefen(daten)
    datei = _datei(b)
    datei.parent.mkdir(parents=True, exist_ok=True)
    zwischen = datei.with_suffix(".tmp")
    zwischen.write_text(yaml.safe_dump(sauber, allow_unicode=True, sort_keys=False), encoding="utf-8")
    zwischen.replace(datei)
    return sauber
