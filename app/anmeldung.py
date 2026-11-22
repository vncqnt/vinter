"""Wer ist gerade angemeldet?

Jeder Endpunkt mit persönlichen Daten fragt über `Depends(aktueller_benutzer)` nach dem Benutzer.
- VINTER_ANMELDUNG=lokal (Vorgabe am PC): automatisch der Standard-Benutzer, mit Admin-Rechten.
- VINTER_ANMELDUNG=passwort (NUC): Sitzung aus dem Cookie, siehe app/konten.py. Ohne gültige Sitzung 401.
Die Seiten selbst schützt die Middleware in main.py (Weiterleitung zur Anmeldung).
"""
from fastapi import HTTPException, Request

from . import konten
from .benutzer import Benutzer
from .konfig import ANMELDUNG, STANDARD_BENUTZER

COOKIE = "vinter_sitzung"


def angemeldet(request: Request) -> dict | None:
    """{name, ordner, rolle} oder None."""
    if ANMELDUNG == "lokal":
        return {"name": STANDARD_BENUTZER, "ordner": STANDARD_BENUTZER, "rolle": "admin"}
    if ANMELDUNG == "passwort":
        return konten.sitzung(request.cookies.get(COOKIE))
    return None  # unbekannter Modus: lieber niemanden hineinlassen als alle


def aktueller_benutzer(request: Request) -> Benutzer:
    k = angemeldet(request)
    if not k:
        raise HTTPException(status_code=401, detail="Anmeldung erforderlich.")
    return Benutzer(k["ordner"])


def admin(request: Request) -> dict:
    k = angemeldet(request)
    if not k:
        raise HTTPException(status_code=401, detail="Anmeldung erforderlich.")
    if k["rolle"] != "admin":
        raise HTTPException(status_code=403, detail="Nur für den Admin.")
    return k
