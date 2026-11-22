"""Sammelt News zu den Thesen und bewertet sie mit einem lokalen Sprachmodell.

Das Modell wandelt jeden Satz in eine Zahlenreihe um. Sätze mit ähnlicher Bedeutung
bekommen ähnliche Zahlen, auch über Sprachen hinweg. So findet es "Robotaxi" zu "autonomes Fahren".
"""
import calendar
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import feedparser
import yfinance as yf

from .kurse import STUNDE, cache_lesen, cache_schreiben

MODELLNAME = "paraphrase-multilingual-MiniLM-L12-v2"
GUELTIG = 3 * STUNDE
TAGE = 7
MAX_MELDUNGEN = 120
GLEICHE_MELDUNG = 0.82  # ab dieser Ähnlichkeit gelten zwei Schlagzeilen als dieselbe Nachricht
SICHERES_SIGNAL = 0.30  # darunter ähnelt die Meldung keinem Signal: Tendenz 0
VOLLE_TENDENZ = 0.30  # ab diesem Ähnlichkeits-Abstand zeigt die Skala 100 %
WARNUNG_AB = 0.50  # so ähnlich muss eine Meldung einem Bruch-Signal sein
WARNUNG_ABSTAND = 0.05
GOOGLE_REGIONEN = [("de", "DE", "DE:de"), ("en-US", "US", "US:en")]

_modell = None
_sperre = threading.Lock()


def lade_modell():
    """Lädt das Modell einmal pro Serverstart, erst wenn es gebraucht wird."""
    global _modell
    with _sperre:
        if _modell is None:
            from sentence_transformers import SentenceTransformer  # langsamer Import, daher hier
            try:
                _modell = SentenceTransformer(MODELLNAME, local_files_only=True)  # läuft offline
            except Exception:
                _modell = SentenceTransformer(MODELLNAME)  # erster Start: einmalig herunterladen
        return _modell


def mit_cache(schluessel: str, holen) -> tuple[list, bool]:
    """Gibt (Meldungen, Abruf fehlgeschlagen?) zurück."""
    alt, alter = cache_lesen(schluessel)
    if alt is not None and alter < GUELTIG:
        return alt, False
    try:
        neu = holen()
    except Exception:
        return alt or [], True
    cache_schreiben(schluessel, neu)
    return neu, False


def hole_yahoo(ticker: str) -> list:
    meldungen = []
    for roh in yf.Ticker(ticker).news:
        c = roh.get("content") or {}
        link = (c.get("clickThroughUrl") or {}).get("url") or (c.get("canonicalUrl") or {}).get("url")
        if c.get("title") and c.get("pubDate") and link and c.get("contentType") == "STORY":
            meldungen.append({
                "titel": c["title"],
                "quelle": (c.get("provider") or {}).get("displayName") or "Yahoo",
                "zeit": c["pubDate"].replace("Z", "+00:00"),
                "link": link,
            })
    return meldungen


def hole_google(thema: str, hl: str, gl: str, ceid: str) -> list:
    return _lies_feed("https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{thema} when:{TAGE}d", "hl": hl, "gl": gl, "ceid": ceid}
    ))


def _lies_feed(adresse: str) -> list:
    anfrage = urllib.request.Request(adresse, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(anfrage, timeout=20) as antwort:
        feed = feedparser.parse(antwort.read())
    meldungen = []
    for e in feed.entries:
        if not (e.get("title") and e.get("link") and e.get("published_parsed")):
            continue
        quelle = (e.get("source") or {}).get("title") or "Google News"
        titel = e.title
        if titel.endswith(f" - {quelle}"):  # Google hängt die Quelle an den Titel
            titel = titel[: -len(quelle) - 3]
        zeit = datetime.fromtimestamp(calendar.timegm(e.published_parsed), timezone.utc)
        meldungen.append({"titel": titel, "quelle": quelle, "zeit": zeit.isoformat(), "link": e.link})
    return meldungen
