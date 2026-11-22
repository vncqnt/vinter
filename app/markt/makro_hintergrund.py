"""Hintergrund für Makro-Daten und Fed-Wahrscheinlichkeiten (Punkte M4 und M6).

- Fed: alle 2 Stunden die Wahrscheinlichkeiten je Sitzung speichern (der letzte Wert des Tages bleibt), unabhängig
  davon, ob jemand die Seite öffnet. Beim ersten Start einmal den Verlauf seit der letzten Sitzung nachrechnen.
- Makro: an Tagen mit einem Termin aus makro_termine.yaml 20 Minuten nach der Uhrzeit die passenden Quellen neu
  laden (BLS für CPI, PPI, Arbeitsmarkt; FRED für die Fed; EZB für die EZB), damit Kalender, Markttreiber und die
  Seite Makro-Daten das Ergebnis zeigen. Sonst hält der Abruf die Daten höchstens 3 Stunden warm.
"""
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..cache import markt_lesen, markt_schreiben, markt_veraltet

BERLIN = ZoneInfo("Europe/Berlin")
_gestartet = False


def quellen_fuer(name: str) -> list[str]:
    """Welche Cache-Einträge nach einem Termin neu geladen werden. Reine Funktion."""
    n = name.lower()
    if "fed" in n and "zins" in n:
        return ["fred:DFEDTARU"]
    if "ezb" in n and "zins" in n:
        return ["makro:ezb:FM/D.U2.EUR.4F.KR.DFR.LEV"]
    if any(w in n for w in ("cpi", "ppi", "arbeitsmarkt", "inflation", "erzeugerpreise")):
        return ["makro:bls"]
    if any(w in n for w in ("pce", "bip", "gdp")):
        return ["makro:bea"]
    return []


def faellige(termine: list[dict], jetzt: datetime, erledigt: set[str]) -> list[dict]:
    """Termine von heute, deren Uhrzeit mindestens 20 Minuten vorbei ist und die noch nicht abgeholt wurden."""
    aus = []
    for t in termine:
        if t["datum"] != jetzt.date().isoformat() or not t.get("uhrzeit"):
            continue
        try:
            h, m = (int(x) for x in t["uhrzeit"].split(":"))
        except ValueError:
            continue
        zeit = datetime(jetzt.year, jetzt.month, jetzt.day, h, m, tzinfo=BERLIN) + timedelta(minutes=20)
        schluessel = f"{t['datum']}:{t['name']}"
        if jetzt >= zeit and schluessel not in erledigt and quellen_fuer(t["name"]):
            aus.append({**t, "schluessel": schluessel})
    return aus


def _fed_runde() -> None:
    from .fed_futures import fed_wahrscheinlichkeiten, merken
    fed = fed_wahrscheinlichkeiten()
    if not fed.get("fehler"):
        merken(fed)


def _rueckblick_einmal() -> None:
    erledigt, _ = markt_lesen("fed:rueckblick")
    if erledigt == date.today().isoformat():
        return
    from .fed_futures import _sitzungen, rueckblick
    from .fed_tage import entscheidungen
    alle = sorted({date.fromisoformat(e["datum"]) for e in entscheidungen()} | set(_sitzungen()))
    rueckblick(alle)
    markt_schreiben("fed:rueckblick", date.today().isoformat())


def _makro_runde() -> None:
    from .makro import makro_termine
    from .makrodaten import makrodaten
    erledigt = set((markt_lesen("makro:abgeholt")[0]) or [])
    neu = faellige(makro_termine().get("termine", []), datetime.now(BERLIN), erledigt)
    if neu:
        markt_veraltet(*{q for t in neu for q in quellen_fuer(t["name"])}, "makro:daten")
    makrodaten()  # hält die Daten warm (lädt nur, wenn veraltet)
    if neu:
        # Nur als erledigt merken, wenn der neue Monat wirklich da ist; sonst beim nächsten Durchlauf wieder versuchen
        from .makro import makro_termine as neu_lesen
        fertig = {f"{t['datum']}:{t['name']}" for t in neu_lesen().get("termine", []) if t.get("tatsaechlich")}
        erledigt |= {t["schluessel"] for t in neu if t["schluessel"] in fertig}
        markt_schreiben("makro:abgeholt", sorted(erledigt)[-200:])


def _schleife() -> None:
    time.sleep(90)
    from ..status import job
    letzte_fed = 0.0
    while True:
        try:
            with job("Makro & Fed"):
                try:
                    _rueckblick_einmal()
                except Exception:
                    pass
                if time.time() - letzte_fed > 2 * 3600:
                    _fed_runde()
                    letzte_fed = time.time()
                _makro_runde()
        except Exception:
            pass
        time.sleep(10 * 60)


def starte() -> None:
    global _gestartet
    if not _gestartet:
        _gestartet = True
        threading.Thread(target=_schleife, daemon=True, name="makro").start()
