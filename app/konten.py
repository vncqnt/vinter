"""Konten, Sitzungen und Einladungen (für den Betrieb auf dem NUC mit Freunden).

Nur aktiv mit VINTER_ANMELDUNG=passwort. Lokal am PC (Vorgabe "lokal") ist man automatisch angemeldet.

Gespeichert in <Daten>/konten/ (gehört wie alle Daten nicht ins Repo):
  konten.json      Konten: Name, Ordner, Rolle (admin/freund), Passwort-Hash, aktiv, letzte Anmeldung
  sitzungen.json   angemeldete Geräte: nur der SHA-256 des Sitzungs-Tokens, nie das Token selbst
  einladungen.json offene Einladungen: nur der Hash des Codes, gültig 7 Tage und einmal
  einrichtung.txt  Einrichtungscode für das erste Konto (steht auch im Log), wird danach gelöscht

Sicherheit:
  - Passwörter mit scrypt (hashlib, N=2^15, r=8, p=1) und eigenem Salz je Konto, Vergleich in konstanter Zeit
  - Sitzung: zufälliges Token (256 Bit) im Cookie, HttpOnly und SameSite=Strict, 30 Tage, verlängert sich bei Nutzung
  - Fehlversuche: nach 5 falschen Passwörtern je Name oder Adresse innerhalb von 15 Minuten 15 Minuten Sperre
  - Das erste Konto (Admin) braucht den Einrichtungscode, damit nicht der Erste, der die Seite öffnet, Admin wird
"""
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .konfig import DATEN, STANDARD_BENUTZER

ORDNER = DATEN / "konten"
SITZUNG_TAGE = 30
EINLADUNG_TAGE = 7
FEHLVERSUCHE = 5
SPERRE_MINUTEN = 15
MAX_KONTEN = 12
NAME_FORM = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
_sperre = threading.Lock()
_fehler: dict[str, list[float]] = {}  # Name oder Adresse -> Zeitpunkte der Fehlversuche (nur im Speicher)


class KontoFehler(Exception):
    """Text wird dem Nutzer angezeigt."""


# ---- Dateien ----

def _datei(name: str) -> Path:
    return ORDNER / name


def _lesen(name: str, leer):
    d = _datei(name)
    if not d.exists():
        return leer
    try:
        return json.loads(d.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return leer


def _schreiben(name: str, wert) -> None:
    ORDNER.mkdir(parents=True, exist_ok=True)
    d = _datei(name)
    zwischen = d.with_suffix(".tmp")
    zwischen.write_text(json.dumps(wert, ensure_ascii=False, indent=1), encoding="utf-8")
    zwischen.replace(d)


def _jetzt() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---- Passwörter ----

def _passwort_hash(passwort: str, salz: bytes | None = None) -> str:
    salz = salz or secrets.token_bytes(16)
    h = hashlib.scrypt(passwort.encode(), salt=salz, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt$32768$8$1${salz.hex()}${h.hex()}"


def _passwort_pruefen(passwort: str, gespeichert: str) -> bool:
    try:
        _, n, r, p, salz, h = gespeichert.split("$")
        neu = hashlib.scrypt(passwort.encode(), salt=bytes.fromhex(salz), n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024, dklen=len(h) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(neu.hex(), h)


def _passwort_regeln(passwort: str) -> None:
    if len(passwort) < 10:
        raise KontoFehler("Das Passwort braucht mindestens 10 Zeichen.")
    if len(passwort) > 200:
        raise KontoFehler("Das Passwort ist zu lang.")
    if passwort.lower() in ("passwort123", "password123", "1234567890", "vinter12345"):
        raise KontoFehler("Dieses Passwort ist zu leicht zu erraten.")


# ---- Fehlversuche ----

def _gesperrt(*schluessel: str) -> int:
    """Sekunden bis zum Ende der Sperre (0 = frei)."""
    jetzt = time.time()
    rest = 0
    for s in schluessel:
        liste = [t for t in _fehler.get(s, []) if jetzt - t < SPERRE_MINUTEN * 60]
        _fehler[s] = liste
        if len(liste) >= FEHLVERSUCHE:
            rest = max(rest, int(SPERRE_MINUTEN * 60 - (jetzt - liste[-FEHLVERSUCHE])))
    return rest


def _fehlversuch(*schluessel: str) -> None:
    for s in schluessel:
        _fehler.setdefault(s, []).append(time.time())


# ---- Konten ----

def konten() -> dict:
    return _lesen("konten.json", {})


def hat_admin() -> bool:
    return any(k.get("rolle") == "admin" for k in konten().values())


def einrichtungscode() -> str | None:
    """Code für das erste Konto. Entsteht beim Start, solange es keinen Admin gibt; steht im Log und in einrichtung.txt."""
    if hat_admin():
        _datei("einrichtung.txt").unlink(missing_ok=True)
        return None
    d = _datei("einrichtung.txt")
    if d.exists():
        return d.read_text(encoding="utf-8").strip()
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    ORDNER.mkdir(parents=True, exist_ok=True)
    d.write_text(code, encoding="utf-8")
    return code


def _name_pruefen(name: str) -> str:
    name = (name or "").strip().lower()
    if not NAME_FORM.match(name):
        raise KontoFehler("Name: 2 bis 32 Zeichen, nur Kleinbuchstaben, Ziffern, - und _.")
    return name


def einrichten(code: str, name: str, passwort: str) -> dict:
    """Erstes Konto (Admin). Übernimmt den bisherigen Datenordner (lokaler Benutzer), damit dein Depot bleibt."""
    with _sperre:
        if hat_admin():
            raise KontoFehler("Vinter ist schon eingerichtet.")
        erwartet = einrichtungscode()
        if not erwartet or not hmac.compare_digest((code or "").strip().upper(), erwartet):
            _fehlversuch("einrichtung")
            raise KontoFehler("Der Einrichtungscode stimmt nicht. Er steht im Log des Add-ons bzw. des Servers.")
        name = _name_pruefen(name)
        _passwort_regeln(passwort)
        k = konten()
        k[name] = {"ordner": STANDARD_BENUTZER, "rolle": "admin", "hash": _passwort_hash(passwort), "aktiv": True, "erstellt": _jetzt(), "letzte_anmeldung": None}
        _schreiben("konten.json", k)
        _datei("einrichtung.txt").unlink(missing_ok=True)
    return {"ok": True}


def anmelden(name: str, passwort: str, adresse: str) -> str:
    """Gibt das Sitzungs-Token zurück (für den Cookie)."""
    name = (name or "").strip().lower()
    rest = _gesperrt(f"n:{name}", f"a:{adresse}")
    if rest:
        raise KontoFehler(f"Zu viele Fehlversuche. Bitte in {max(1, rest // 60)} Minuten noch einmal.")
    k = konten().get(name)
    # Auch ohne Konto einen Hash rechnen, damit die Antwortzeit nicht verrät, ob es den Namen gibt
    ok = _passwort_pruefen(passwort or "", k["hash"]) if k else (_passwort_pruefen(passwort or "", _passwort_hash("x")) and False)
    if not k or not ok or not k.get("aktiv", True):
        _fehlversuch(f"n:{name}", f"a:{adresse}")
        raise KontoFehler("Name oder Passwort stimmt nicht." if not k or not ok else "Dieses Konto ist gesperrt.")
    _fehler.pop(f"n:{name}", None)
    token = secrets.token_urlsafe(32)
    with _sperre:
        s = _sitzungen_aufraeumen(_lesen("sitzungen.json", {}))
        s[_hash_token(token)] = {"name": name, "erstellt": _jetzt(), "zuletzt": _jetzt(),
                                 "bis": (datetime.now() + timedelta(days=SITZUNG_TAGE)).isoformat(timespec="seconds")}
        _schreiben("sitzungen.json", s)
        alle = konten()
        alle[name]["letzte_anmeldung"] = _jetzt()
        _schreiben("konten.json", alle)
    return token


def _sitzungen_aufraeumen(s: dict) -> dict:
    jetzt = _jetzt()
    return {h: x for h, x in s.items() if x.get("bis", "") > jetzt}


def sitzung(token: str | None) -> dict | None:
    """Konto zur Sitzung (mit name, ordner, rolle) oder None. Verlängert die Sitzung höchstens einmal am Tag."""
    if not token or len(token) > 100:
        return None
    h = _hash_token(token)
    s = _lesen("sitzungen.json", {})
    x = s.get(h)
    if not x or x.get("bis", "") <= _jetzt():
        return None
    k = konten().get(x["name"])
    if not k or not k.get("aktiv", True):
        return None
    if x.get("zuletzt", "")[:10] != _jetzt()[:10]:
        with _sperre:
            s = _lesen("sitzungen.json", {})
            if h in s:
                s[h].update(zuletzt=_jetzt(), bis=(datetime.now() + timedelta(days=SITZUNG_TAGE)).isoformat(timespec="seconds"))
                _schreiben("sitzungen.json", s)
    return {"name": x["name"], "ordner": k["ordner"], "rolle": k.get("rolle", "freund")}


def abmelden(token: str | None, ueberall: bool = False) -> None:
    if not token:
        return
    with _sperre:
        s = _lesen("sitzungen.json", {})
        x = s.get(_hash_token(token))
        if not x:
            return
        if ueberall:
            s = {h: y for h, y in s.items() if y["name"] != x["name"]}
        else:
            s.pop(_hash_token(token), None)
        _schreiben("sitzungen.json", s)


def passwort_aendern(name: str, alt: str, neu: str) -> dict:
    k = konten().get(name)
    if not k or not _passwort_pruefen(alt or "", k["hash"]):
        raise KontoFehler("Das bisherige Passwort stimmt nicht.")
    _passwort_regeln(neu)
    with _sperre:
        alle = konten()
        alle[name]["hash"] = _passwort_hash(neu)
        _schreiben("konten.json", alle)
    return {"ok": True}


# ---- Einladungen und Verwaltung (nur Admin) ----

def einladung_neu(von: str, notiz: str = "") -> dict:
    """Einmal-Code, 7 Tage gültig. Gespeichert wird nur sein Hash."""
    if len(konten()) >= MAX_KONTEN:
        raise KontoFehler(f"Höchstens {MAX_KONTEN} Konten.")
    code = secrets.token_urlsafe(24)
    with _sperre:
        e = {h: x for h, x in _lesen("einladungen.json", {}).items() if x["bis"] > _jetzt()}
        kennung = secrets.token_hex(4)
        e[_hash_token(code)] = {"id": kennung, "von": von, "notiz": str(notiz or "")[:60], "erstellt": _jetzt(),
                                "bis": (datetime.now() + timedelta(days=EINLADUNG_TAGE)).isoformat(timespec="seconds")}
        _schreiben("einladungen.json", e)
    return {"code": code, "id": kennung, "bis": e[_hash_token(code)]["bis"]}


def einladung_gueltig(code: str) -> bool:
    x = _lesen("einladungen.json", {}).get(_hash_token(code or ""))
    return bool(x and x["bis"] > _jetzt())


def einladung_annehmen(code: str, name: str, passwort: str, adresse: str) -> dict:
    if _gesperrt(f"a:{adresse}"):
        raise KontoFehler("Zu viele Fehlversuche. Bitte später noch einmal.")
    name = _name_pruefen(name)
    _passwort_regeln(passwort)
    with _sperre:
        e = _lesen("einladungen.json", {})
        x = e.get(_hash_token(code or ""))
        if not x or x["bis"] <= _jetzt():
            _fehlversuch(f"a:{adresse}")
            raise KontoFehler("Diese Einladung ist abgelaufen oder wurde schon benutzt. Bitte um eine neue.")
        from .konfig import BENUTZER_DATEN
        k = konten()
        # Auch ein übrig gebliebener Datenordner zählt als belegt: sonst bekäme ein neues Konto fremde Daten
        if name in k or name == STANDARD_BENUTZER or (BENUTZER_DATEN / name).exists():
            raise KontoFehler("Diesen Namen gibt es schon. Bitte einen anderen wählen.")
        ordner = name
        k[name] = {"ordner": ordner, "rolle": "freund", "hash": _passwort_hash(passwort), "aktiv": True, "erstellt": _jetzt(),
                   "letzte_anmeldung": None, "eingeladen_von": x["von"]}
        _schreiben("konten.json", k)
        e.pop(_hash_token(code))
        _schreiben("einladungen.json", e)
    return {"ok": True}


def uebersicht() -> dict:
    """Für die Verwaltung: nur Name, Rolle, Status und seit wann. Bewusst nichts über die Nutzung
    (keine letzte Anmeldung, keine Geräte), der Admin verwaltet die Zugänge, nicht die Freunde."""
    return {
        "konten": [{"name": n, "rolle": k.get("rolle"), "aktiv": k.get("aktiv", True), "erstellt": k.get("erstellt")}
                   for n, k in sorted(konten().items())],
        "einladungen": [{"id": x["id"], "notiz": x.get("notiz"), "erstellt": x["erstellt"], "bis": x["bis"]}
                        for x in _lesen("einladungen.json", {}).values() if x["bis"] > _jetzt()],
    }


def einladung_loeschen(kennung: str) -> dict:
    with _sperre:
        e = _lesen("einladungen.json", {})
        e = {h: x for h, x in e.items() if x["id"] != kennung}
        _schreiben("einladungen.json", e)
    return {"ok": True}


def konto_entfernen(name: str, von: str) -> dict:
    """Rauswerfen: Konto, Sitzungen und alle persönlichen Daten des Freundes endgültig löschen.
    Der Admin sieht dabei nichts vom Inhalt; gelöscht wird nur der eigene Ordner des Freundes."""
    import shutil
    from .konfig import BENUTZER_DATEN
    if name == von:
        raise KontoFehler("Das eigene Konto lässt sich nicht entfernen.")
    with _sperre:
        k = konten()
        if name not in k:
            raise KontoFehler("Konto nicht gefunden.")
        if k[name].get("rolle") == "admin":
            raise KontoFehler("Ein Admin-Konto lässt sich hier nicht entfernen.")
        ordner = k[name].get("ordner") or ""
        del k[name]
        _schreiben("konten.json", k)
        _schreiben("sitzungen.json", {h: x for h, x in _lesen("sitzungen.json", {}).items() if x["name"] != name})
    # Nur einen eigenen Unterordner von benutzer/ löschen, nie den des Admins oder etwas außerhalb
    ziel = (BENUTZER_DATEN / ordner).resolve()
    if ordner and NAME_FORM.match(ordner) and ordner != STANDARD_BENUTZER and ziel.parent == BENUTZER_DATEN.resolve() and ziel.exists():
        shutil.rmtree(ziel, ignore_errors=True)
    return {"ok": True}


def konto_setzen(name: str, aktiv: bool, von: str) -> dict:
    """Sperren/Entsperren. Gesperrte werden sofort überall abgemeldet. Die Daten bleiben erhalten."""
    if name == von:
        raise KontoFehler("Das eigene Konto lässt sich nicht sperren.")
    with _sperre:
        k = konten()
        if name not in k:
            raise KontoFehler("Konto nicht gefunden.")
        if k[name].get("rolle") == "admin":
            raise KontoFehler("Ein Admin-Konto lässt sich hier nicht sperren.")
        k[name]["aktiv"] = bool(aktiv)
        _schreiben("konten.json", k)
        if not aktiv:
            s = {h: x for h, x in _lesen("sitzungen.json", {}).items() if x["name"] != name}
            _schreiben("sitzungen.json", s)
    return {"ok": True}
