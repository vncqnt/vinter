"""API-Keys, die über die Einstellungsseite im Tool eingetragen werden: verschlüsselt im Datenverzeichnis.

<Daten>/konten/schluessel.bin  zufälliger Schlüssel (beim ersten Speichern erzeugt)
<Daten>/konten/keys.enc        die Keys, verschlüsselt mit Fernet (AES-128 im CBC-Modus mit HMAC gegen Veränderung)

Ehrlich gesagt schützt das vor allem davor, dass Keys im Klartext in einer Sicherung, einem Screenshot eines
Ordners oder versehentlich in einem Repo landen. Wer den ganzen Datenordner hat, hat auch den Schlüssel.
Die Werte werden nie an den Browser zurückgegeben, nur ob ein Key gesetzt ist.
"""
import json
import os
import threading

from cryptography.fernet import Fernet, InvalidToken

from .konfig import DATEN

ORDNER = DATEN / "konten"
_sperre = threading.Lock()


def _fernet() -> Fernet:
    datei = ORDNER / "schluessel.bin"
    if not datei.exists():
        ORDNER.mkdir(parents=True, exist_ok=True)
        datei.write_bytes(Fernet.generate_key())
        try:
            os.chmod(datei, 0o600)  # unter Linux (NUC): nur der Besitzer darf lesen
        except OSError:
            pass
    return Fernet(datei.read_bytes().strip())


def alle() -> dict:
    datei = ORDNER / "keys.enc"
    if not datei.exists():
        return {}
    try:
        return json.loads(_fernet().decrypt(datei.read_bytes()))
    except (InvalidToken, ValueError, OSError):
        return {}


def setzen(name: str, wert: str | None) -> None:
    with _sperre:
        werte = alle()
        if wert:
            werte[name] = wert
        else:
            werte.pop(name, None)
        ORDNER.mkdir(parents=True, exist_ok=True)
        ziel = ORDNER / "keys.enc"
        zwischen = ziel.with_suffix(".tmp")
        zwischen.write_bytes(_fernet().encrypt(json.dumps(werte).encode()))
        zwischen.replace(ziel)
