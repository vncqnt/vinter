"""Schnelle Auslöser-Quellen (RSS und Atom): ein Hintergrund-Thread fragt jede Quelle in ihrem Takt ab.

- Bedingte Anfragen: ETag und Last-Modified werden gemerkt; antwortet der Server "304 nicht geändert",
  wird nichts geladen.
- Backoff: nach Fehlern (auch 403/429) wartet Vinter doppelt so lange wie üblich, dann viermal so lange …,
  höchstens 30 Minuten. Ein Erfolg setzt das zurück.
- Je Server höchstens eine Anfrage alle 2 Sekunden (mehrere Quellen teilen sich z. B. sec.gov).
- Neue Meldungen kommen in die Datenbank (speicher.py); die weitere Verarbeitung hängt sich über
  nach_neuen() an (Extraktion, Geschichten, Bewertung).
"""
import calendar
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import requests

from ...einstellungen import key
from . import quellen, speicher

log = logging.getLogger(__name__)

TAKT = 5                  # Sekunden zwischen zwei Durchgängen des Planers
MAX_WARTEN = 30 * 60      # längste Pause nach Fehlerserien
PRO_SERVER = 2.0          # Sekunden zwischen zwei Anfragen an denselben Server
ZU_ALT = timedelta(days=2)  # ältere Einträge eines Feeds (z. B. beim ersten Abruf) werden nicht übernommen
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Vinter Newsfeed/2.0 (privat)"

_faellig: dict[str, float] = {}
_server: dict[str, float] = {}
_server_sperre = threading.Lock()
_nach_neuen: list = []
_gestartet = [False]


def nach_neuen(funktion) -> None:
    """Weiterverarbeitung anmelden: funktion(ids) wird mit den ids neuer Meldungen aufgerufen."""
    _nach_neuen.append(funktion)


def melden(ids: list[int]) -> None:
    for f in _nach_neuen:
        try:
            f(ids)
        except Exception as fehler:
            log.warning("Newsfeed-Verarbeitung: %s", fehler)


def _warten_auf_server(adresse: str) -> None:
    host = urlparse(adresse).netloc
    while True:
        with _server_sperre:
            frei_ab = _server.get(host, 0)
            if time.time() >= frei_ab:
                _server[host] = time.time() + PRO_SERVER
                return
        time.sleep(max(0.05, frei_ab - time.time()))


def _zeit(e) -> str | None:
    t = e.get("published_parsed") or e.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(t), timezone.utc).isoformat(timespec="seconds") if t else None


def _vorspann_weg(titel: str, vorspann: list[str]) -> str:
    for v in vorspann:
        if titel.startswith(v):
            return titel[len(v):].strip()
    return titel


def eintraege(q: dict, inhalt: bytes, einst: dict) -> list[dict]:
    """Feed-Inhalt in Meldungen umwandeln (reine Funktion, ohne Netz testbar)."""
    feed = feedparser.parse(inhalt)
    ausblenden = [w.lower() for w in einst.get("ausblenden") or []]
    grenze = (datetime.now(timezone.utc) - ZU_ALT).isoformat()
    aus = []
    for e in feed.entries:
        titel = speicher.sauber(e.get("title"), 500)
        if not titel:
            continue
        titel = _vorspann_weg(titel, einst.get("vorspann") or [])
        if any(w in titel.lower() for w in ausblenden):
            continue
        zeit = _zeit(e)
        if zeit and zeit < grenze:
            continue
        kennung = e.get("id") or e.get("link") or titel
        aus.append({
            "schluessel": speicher.schluessel(q["name"], kennung),
            "quelle": q["name"], "art": "rss", "stufe": int(q.get("stufe", 3)),
            "titel": titel, "text": speicher.sauber(e.get("summary"), 2000), "link": e.get("link") or "", "zeit": zeit,
        })
    return aus


def abrufen(q: dict) -> int:
    """Eine Quelle einmal abfragen. Gibt die Zahl neuer Meldungen zurück (Fehler werden gemeldet, nicht geworfen)."""
    name = q["name"]
    zustand = speicher.quelle_zustand(name)
    kopf = {"User-Agent": BROWSER, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"}
    if q.get("kontakt") == "sec":
        ua = key("SEC_USER_AGENT")
        if not ua:
            speicher.quelle_melden(name, False, fehler="SEC_USER_AGENT fehlt (Einstellungen)")
            return 0
        kopf["User-Agent"] = ua
    if zustand.get("etag"):
        kopf["If-None-Match"] = zustand["etag"]
    if zustand.get("geaendert"):
        kopf["If-Modified-Since"] = zustand["geaendert"]
    _warten_auf_server(q["adresse"])
    try:
        r = requests.get(q["adresse"], headers=kopf, timeout=20)
    except requests.RequestException as f:
        speicher.quelle_melden(name, False, fehler=type(f).__name__)
        return 0
    if r.status_code == 304:
        speicher.quelle_melden(name, True)
        return 0
    if r.status_code != 200:
        speicher.quelle_melden(name, False, fehler=f"HTTP {r.status_code}")
        return 0
    meldungen = eintraege(q, r.content, quellen.laden())
    if not zustand.get("letzter_ok"):
        # Erster Abruf: Vinter hat diese Meldungen nicht live gesehen. Als "gesehen" gilt die Zeit laut Quelle,
        # sonst sähe es später so aus, als hätte diese Quelle alles zuerst gehabt.
        for m in meldungen:
            m["gesehen"] = m["zeit"] or speicher.jetzt()
    if not meldungen and b"<html" in r.content[:500].lower():
        speicher.quelle_melden(name, False, fehler="Webseite statt Feed (gesperrt?)")
        return 0
    ids = speicher.einfuegen(meldungen)
    speicher.quelle_melden(name, True, neue=len(ids), etag=r.headers.get("ETag"), geaendert=r.headers.get("Last-Modified"))
    if ids:
        melden(ids)
    return len(ids)


def _naechster(q: dict) -> float:
    serie = speicher.quelle_zustand(q["name"]).get("fehler_serie") or 0
    basis = max(15, int(q.get("intervall_s", 120)))
    return time.time() + (basis if not serie else min(MAX_WARTEN, basis * 2 ** serie))


def _schleife():
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="newsfeed") as pool:
        laufend: dict[str, object] = {}
        while True:
            try:
                for q in quellen.quellen("rss"):
                    name = q["name"]
                    if name in laufend and not laufend[name].done():
                        continue
                    if time.time() >= _faellig.get(name, 0):
                        def auftrag(q=q):
                            try:
                                abrufen(q)
                            finally:
                                _faellig[q["name"]] = _naechster(q)
                        _faellig[name] = time.time() + 3600  # bis der Auftrag fertig ist
                        laufend[name] = pool.submit(auftrag)
            except Exception as fehler:
                log.warning("Newsfeed-Planer: %s", fehler)
            time.sleep(TAKT)


def starte() -> None:
    if _gestartet[0]:
        return
    _gestartet[0] = True
    threading.Thread(target=_schleife, name="newsfeed", daemon=True).start()
