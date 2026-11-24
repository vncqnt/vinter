"""Nachrecherche: weitere Berichte zu Firmen und wichtigen Geschichten (Stufe 3, Medien).

Teil 1: Firmen-News von Finnhub je Ticker.
Teil 2 (recherche_schleife): Zu jeder wichtigen Geschichte (Score ab 45, jünger als 6 Stunden) einmal bei Google News
(RSS) und GDELT (DOC-API, höchstens eine Anfrage je 6 Sekunden) nach weiteren Berichten suchen. Treffer werden
nur übernommen, wenn sie zur Geschichte passen (Ähnlichkeit), sonst landen sie unter "Ausgefiltert".
Die SEC-Volltextsuche ist bewusst nicht dabei: Die Pflichtmeldungen (8-K, 6-K) kommen schon live über den SEC-Feed.

Teil 1 im Detail:

Welche Ticker? Was gerade gebraucht wird:
- eigene US-Werte (Depot, Watchlist): die Benutzerschicht meldet sie mit beobachten() an
- die größten Indexbeweger: die Markttreiber fragen sie direkt mit firmen_news() ab
Jeder Ticker wird höchstens alle 5 Minuten abgefragt (Finnhub erlaubt 60 Anfragen je Minute, Vinter bleibt
mit höchstens einer Anfrage je Sekunde weit darunter). Nur Schlagzeilen, die die Firma nennen, werden übernommen
(Finnhub ordnet einem Wert auch allgemeine Marktberichte zu). Alles läuft durch dieselbe Pipeline wie die
anderen Quellen: Werbung und Klickköder werden dort aussortiert, Treffer zu Geschichten gruppiert.
"""
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

from .. import finnhub
from . import speicher

log = logging.getLogger(__name__)

QUELLE = "Finnhub"
ALLE = 5 * 60            # Sekunden je Ticker
BEOBACHTEN = 26 * 3600   # so lange bleibt ein angemeldeter Ticker in der Liste
HOECHSTENS = 40
_letzte: dict[str, float] = {}
_beobachtet: dict[str, tuple[float, list[str]]] = {}
_sperre = threading.Lock()
_takt = threading.Lock()
_gestartet = [False]


def beobachten(werte: dict[str, list[str]]) -> None:
    """werte: Ticker → Erkennungswörter (Name, Kurzname). Nur US-Ticker ohne Punkt/Bindestrich."""
    jetzt = time.time()
    with _sperre:
        for t, aliase in werte.items():
            if t and "." not in t and "-" not in t:
                _beobachtet[t] = (jetzt, aliase)


def firmen_news(ticker: str, aliase: list[str]) -> int:
    """Finnhub-News eines Tickers holen (höchstens alle 5 Minuten). Gibt die Zahl neuer Meldungen zurück."""
    if not finnhub.verfuegbar() or "." in ticker or "-" in ticker:
        return 0
    with _sperre:
        if time.time() - _letzte.get(ticker, 0) < ALLE:
            return 0
        _letzte[ticker] = time.time()
    with _takt:  # höchstens eine Anfrage je Sekunde
        time.sleep(1.0)
        heute = date.today()
        try:
            roh = finnhub._abruf("company-news", symbol=ticker, **{"from": str(heute - timedelta(days=2)), "to": str(heute)})
            speicher.quelle_melden(QUELLE, True)
        except Exception as f:
            speicher.quelle_melden(QUELLE, False, fehler=type(f).__name__)
            return 0
    muster = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(a) for a in (aliase or [ticker]) if a) + r")(?!\w)", re.I)
    meldungen = []
    for m in (roh or [])[:20]:
        titel = speicher.sauber(m.get("headline"), 500)
        if not titel or not m.get("datetime") or not muster.search(titel):
            continue
        zeit = datetime.fromtimestamp(m["datetime"], timezone.utc).isoformat(timespec="seconds")
        meldungen.append({
            "schluessel": speicher.schluessel(QUELLE, str(m.get("id") or m.get("url") or titel)),
            "quelle": f"{QUELLE} ({m.get('source') or 'Medien'})", "art": "finnhub", "stufe": 3,
            "titel": titel, "text": speicher.sauber(m.get("summary"), 1000), "link": m.get("url") or "", "zeit": zeit,
            # Ältere Artikel hat Vinter nicht live gesehen: gesehen = Zeit laut Quelle
            "gesehen": zeit if zeit < (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat() else None,
        })
    ids = speicher.einfuegen(meldungen)
    if ids:
        from .abruf import melden
        melden(ids)
    return len(ids)


RECHERCHE_AB = 45
RECHERCHE_FENSTER = timedelta(hours=6)
_recherchiert: set[int] = set()
_gdelt_zeit = [0.0]
_gdelt_pause = [0.0]   # nach "zu viele Anfragen" (429) 30 Minuten Ruhe
_STOPP = {"the", "and", "for", "with", "from", "that", "this", "will", "says", "said", "over", "after", "into", "amid", "about",
          "more", "than", "have", "has", "are", "was", "were", "its", "their", "his", "her", "new", "according", "person", "sources"}


def suchbegriffe(titel: str, ticker: list[str] | None = None) -> str:
    """Wenige tragende Wörter aus der Schlagzeile (für Google News und GDELT)."""
    t = re.sub(r"^[*\s]*|\$[A-Z]+\s*-\s*", "", titel)
    t = re.sub(r"\s+-\s+[A-Z][\w .]+$", "", t)  # " - WSJ", " - Reuters"
    woerter = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]{3,}", t) if w.lower() not in _STOPP]
    return " ".join(dict.fromkeys(w.lower() for w in woerter[:6]))


def _google(begriffe: str) -> list[dict]:
    import feedparser
    import requests
    from urllib.parse import quote_plus
    r = requests.get(f"https://news.google.com/rss/search?q={quote_plus(begriffe)}+when:6h&hl=en-US&gl=US&ceid=US:en",
                     headers={"User-Agent": "Mozilla/5.0 (Vinter Newsfeed)"}, timeout=15)
    r.raise_for_status()
    aus = []
    for e in feedparser.parse(r.content).entries[:12]:
        verlag = (e.get("source") or {}).get("title") or "Medien"
        titel = speicher.sauber(e.get("title"), 500)
        titel = re.sub(rf"\s+-\s+{re.escape(verlag)}$", "", titel)
        t = e.get("published_parsed")
        zeit = datetime(*t[:6], tzinfo=timezone.utc).isoformat(timespec="seconds") if t else None
        aus.append({"quelle": f"Google News ({verlag})", "titel": titel, "link": e.get("link") or "", "zeit": zeit})
    return aus


def _gdelt(begriffe: str) -> list[dict]:
    import requests
    from urllib.parse import quote_plus
    if time.time() < _gdelt_pause[0]:
        return []  # gedrosselt: still aussetzen statt erneut anfragen
    warte = 6 - (time.time() - _gdelt_zeit[0])  # GDELT bittet um höchstens eine Anfrage je 5 Sekunden
    if warte > 0:
        time.sleep(warte)
    _gdelt_zeit[0] = time.time()
    r = requests.get(f"https://api.gdeltproject.org/api/v2/doc/doc?query={quote_plus(begriffe + ' sourcelang:english')}"
                     "&mode=artlist&maxrecords=15&format=json&timespan=6h&sort=datedesc", timeout=40)
    if r.status_code == 429:
        _gdelt_pause[0] = time.time() + 1800
    r.raise_for_status()
    try:
        artikel = r.json().get("articles", [])
    except ValueError:
        return []
    aus = []
    for a in artikel:
        z = a.get("seendate")  # 20261124T170000Z
        zeit = datetime.strptime(z, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat(timespec="seconds") if z else None
        aus.append({"quelle": f"GDELT ({a.get('domain') or 'Medien'})", "titel": speicher.sauber(a.get("title"), 500), "link": a.get("url") or "", "zeit": zeit})
    return aus


def recherchieren(story: dict) -> int:
    """Eine Geschichte nachrecherchieren. Treffer bekommen den Hinweis auf die Geschichte (hinweis = story id)."""
    begriffe = suchbegriffe(story["titel"], story.get("ticker"))
    if len(begriffe.split()) < 2:
        return 0
    funde = []
    for name, holen in (("Google News", _google), ("GDELT", _gdelt)):
        try:
            funde += holen(begriffe)
            speicher.quelle_melden(name, True)
        except Exception as f:
            speicher.quelle_melden(name, False, fehler=type(f).__name__)
    meldungen = [{"schluessel": speicher.schluessel("recherche", m["link"] or m["titel"]), "quelle": m["quelle"], "art": "recherche",
                  "stufe": 3, "titel": m["titel"], "link": m["link"], "zeit": m["zeit"]} for m in funde if m["titel"]]
    ids = speicher.einfuegen(meldungen)
    if ids:
        db = speicher.verbinden()
        try:
            with db:
                if "hinweis" not in {r[1] for r in db.execute("PRAGMA table_info(meldungen)")}:
                    db.execute("ALTER TABLE meldungen ADD COLUMN hinweis INTEGER")
                db.executemany("UPDATE meldungen SET hinweis=? WHERE id=?", [(story["id"], i) for i in ids])
        finally:
            db.close()
        from .abruf import melden
        melden(ids)
    return len(ids)


def _recherche_schleife():
    from . import geschichten
    while True:
        try:
            grenze = (datetime.now(timezone.utc) - RECHERCHE_FENSTER).isoformat()
            for s in geschichten.liste("", 6):
                if s["id"] in _recherchiert or s["score"] < RECHERCHE_AB or s["erstes"] < grenze:
                    continue
                _recherchiert.add(s["id"])
                recherchieren(s)
        except Exception as f:
            log.warning("Nachrecherche: %s", f)
        time.sleep(60)


def _schleife():
    while True:
        try:
            grenze = time.time() - BEOBACHTEN
            with _sperre:
                for t in [t for t, (z, _) in _beobachtet.items() if z < grenze]:
                    _beobachtet.pop(t, None)
                liste = sorted(_beobachtet.items(), key=lambda x: -x[1][0])[:HOECHSTENS]
            for t, (_, aliase) in liste:
                firmen_news(t, aliase)
        except Exception as f:
            log.warning("Finnhub-Nachrecherche: %s", f)
        time.sleep(30)


def starte() -> None:
    if _gestartet[0]:
        return
    _gestartet[0] = True
    threading.Thread(target=_schleife, name="newsfeed-finnhub", daemon=True).start()
    threading.Thread(target=_recherche_schleife, name="newsfeed-recherche", daemon=True).start()
