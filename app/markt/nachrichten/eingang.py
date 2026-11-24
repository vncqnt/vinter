"""Manueller Eingang: eine Meldung selbst einspeisen, als Text oder Link.

Zwei Wege:
- im Tool (News-Seite, Eingabefeld): normale Anmeldung
- per iOS-Kurzbefehl im Teilen-Menü: POST an /api/nachrichten/eingang mit persönlichem Token im Kopf
  "Authorization: Bearer <token>". Der Token wird im Tool erzeugt und nur einmal angezeigt; gespeichert wird
  nur sein SHA-256-Wert (konten/eingang.json). Ein neuer Token macht den alten ungültig.

Solche Meldungen bekommen die Quelle "von mir", Stufe 2, und sind nur für ihren Benutzer sichtbar.
Bei einem Link ohne Text holt Vinter den Seitentitel (nur http/https, nur öffentliche Adressen, höchstens 1 MB).
"""
import hashlib
import ipaddress
import json
import re
import secrets
import socket
import threading
from urllib.parse import urlparse

import requests

from ...konfig import DATEN
from . import speicher

DATEI = DATEN / "konten" / "eingang.json"
QUELLE = "von mir"
_sperre = threading.Lock()


class EingangFehler(ValueError):
    pass


def _laden() -> dict:
    try:
        return json.loads(DATEI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_erzeugen(benutzer: str) -> str:
    """Neuer Token für den Benutzer; ein alter wird ungültig. Wird nur dieses eine Mal im Klartext zurückgegeben."""
    token = "vt_" + secrets.token_urlsafe(32)
    with _sperre:
        alle = {h: b for h, b in _laden().items() if b != benutzer}
        alle[_hash(token)] = benutzer
        DATEI.parent.mkdir(parents=True, exist_ok=True)
        zwischen = DATEI.with_suffix(".tmp")
        zwischen.write_text(json.dumps(alle), encoding="utf-8")
        zwischen.replace(DATEI)
    return token


def token_vorhanden(benutzer: str) -> bool:
    return benutzer in _laden().values()


def token_pruefen(kopf: str | None) -> str | None:
    """Benutzer zum Token aus "Authorization: Bearer …" oder None."""
    if not kopf:
        return None
    token = kopf.removeprefix("Bearer ").strip()
    if not token.startswith("vt_"):
        return None
    gesucht = _hash(token)
    for h, b in _laden().items():
        if secrets.compare_digest(h, gesucht):
            return b
    return None


def _oeffentlich(host: str) -> bool:
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except (socket.gaierror, ValueError):
        return False


def seitentitel(link: str) -> str:
    """Titel einer Webseite (og:title oder <title>), leer wenn nicht zu bekommen."""
    teile = urlparse(link)
    if teile.scheme not in ("http", "https") or not teile.hostname or not _oeffentlich(teile.hostname):
        return ""
    try:
        r = requests.get(link, headers={"User-Agent": "Mozilla/5.0 (Vinter)"}, timeout=10, stream=True, allow_redirects=True)
        roh = r.raw.read(1_000_000, decode_content=True)
    except requests.RequestException:
        return ""
    text = roh.decode(r.encoding or "utf-8", errors="replace")
    for muster in (r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', r"<title[^>]*>(.*?)</title>"):
        m = re.search(muster, text, re.I | re.S)
        if m:
            return speicher.sauber(m.group(1), 300)
    return ""


def aufnehmen(benutzer: str, text: str = "", link: str = "") -> int:
    """Meldung speichern. Gibt die id zurück."""
    text = speicher.sauber(text, 2000)
    link = (link or "").strip()
    if not link:
        m = re.search(r"https?://\S+", text)
        if m:
            link = m.group(0).rstrip(").,")
    if link and not re.match(r"^https?://", link):
        raise EingangFehler("Der Link muss mit http:// oder https:// beginnen.")
    titel = re.sub(r"https?://\S+", "", text).strip() or (seitentitel(link) if link else "")
    if not titel:
        titel = link
    if not titel:
        raise EingangFehler("Bitte einen Text oder einen Link angeben.")
    t = speicher.jetzt()
    meldung = {
        "schluessel": speicher.schluessel(f"{QUELLE}:{benutzer}", f"{t}|{titel}|{link}"),
        "quelle": QUELLE, "art": "manuell", "stufe": 2, "titel": titel[:500], "text": text if text != titel else "",
        "link": link, "zeit": t, "gesehen": t, "nur_fuer": benutzer,
    }
    ids = speicher.einfuegen([meldung])
    if not ids:
        raise EingangFehler("Diese Meldung gibt es schon.")
    from .abruf import melden
    melden(ids)
    return ids[0]
