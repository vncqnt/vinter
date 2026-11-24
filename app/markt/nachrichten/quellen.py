"""Konfiguration des Newsfeeds: daten/markt/news_quellen.yaml (Version 2).

Beim ersten Start nach dem Neuaufbau liegt dort noch die alte Datei (ohne "version: 2"). Sie wird nicht
gelöscht, sondern in news_quellen_alt.yaml umbenannt (zum Nachschlagen eigener Änderungen), und die
neue Vorlage kommt an ihre Stelle.
"""
import shutil
import threading

import yaml

from ...konfig import MARKT_DATEN, VORLAGEN

DATEI = MARKT_DATEN / "news_quellen.yaml"
ALT = MARKT_DATEN / "news_quellen_alt.yaml"
VORLAGE = VORLAGEN / "markt" / "news_quellen.yaml"
_sperre = threading.Lock()
_stand = {"mtime": None, "daten": None}


def _umstellen() -> None:
    """Alte Datei (Version 1) beiseitelegen und die neue Vorlage einsetzen."""
    if not DATEI.exists():
        DATEI.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(VORLAGE, DATEI)
        return
    try:
        roh = yaml.safe_load(DATEI.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return  # kaputte Datei nicht anfassen; laden() meldet den Fehler
    if roh.get("version") == 2:
        return
    if not ALT.exists():
        shutil.copy2(DATEI, ALT)
    shutil.copy2(VORLAGE, DATEI)


def laden() -> dict:
    """Die aktuelle Konfiguration (wird neu gelesen, sobald sich die Datei ändert)."""
    with _sperre:
        _umstellen()
        mtime = DATEI.stat().st_mtime
        if _stand["mtime"] != mtime:
            daten = yaml.safe_load(DATEI.read_text(encoding="utf-8")) or {}
            daten.setdefault("quellen", [])
            daten.setdefault("telegram", {})
            daten["telegram"].setdefault("kanaele", [])
            _stand.update(mtime=mtime, daten=daten)
        return _stand["daten"]


def quellen(art: str | None = None) -> list[dict]:
    return [q for q in laden()["quellen"] if q.get("aktiv", True) and (art is None or q.get("art") == art)]
