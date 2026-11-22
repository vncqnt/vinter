"""Live-Newsfeed im Squawk-Stil: kurze Schlagzeilen aus mehreren Quellen, neueste oben.

Welche Quellen und Schlüsselwörter gelten, steht in daten/markt/news_quellen.yaml.
Jede Quelle wird für sich abgefragt und zwischengespeichert. Fällt eine aus, bleiben ihre
letzten Meldungen stehen und die Seite bekommt einen Hinweis statt eines Fehlers.

Firmenbezogene Quellen (Finnhub, SEC) brauchen die Werte, um die es gehen soll. Die gibt der
Aufrufer mit; dieses Modul weiß selbst nichts über ein Depot.
"""
import calendar
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import certifi
import feedparser
import yaml

from ..einstellungen import key
from ..konfig import MARKT_DATEN
from . import finnhub
from .kurse import cache_lesen, cache_schreiben

_SEC_SPERRE = threading.Lock()  # die SEC antwortet langsam, wenn viele Anfragen gleichzeitig kommen

QUELLEN_DATEI = MARKT_DATEN / "news_quellen.yaml"
MAX_MELDUNGEN = 250
GLEICHE_MELDUNG = 0.6  # Anteil gemeinsamer Wörter, ab dem zwei Schlagzeilen dieselbe Nachricht sind
GLEICH_INNERHALB = timedelta(hours=12)
PAUSE_NACH_FEHLER = 5 * 60  # Sekunden
AUFBEWAHREN_TAGE = 7  # länger als jede sinnvolle Einstellung von "tage"
_SSL = ssl.create_default_context(cafile=certifi.where())  # manche Behörden-Server brauchen die aktuelle Zertifikatsliste
SEC_PUNKTE = {
    "1.01": "wichtiger Vertrag", "1.02": "Vertrag beendet", "2.01": "Kauf oder Verkauf abgeschlossen",
    "2.02": "Geschäftszahlen", "2.03": "neue Finanzverpflichtung", "2.04": "Schulden fällig gestellt",
    "2.05": "Umbau mit Kosten", "2.06": "Abschreibung", "3.01": "Börsennotierung", "3.02": "Verkauf neuer Aktien",
    "3.03": "Änderung der Aktionärsrechte", "4.01": "Wechsel des Wirtschaftsprüfers", "4.02": "frühere Zahlen nicht mehr verlässlich",
    "5.01": "Kontrollwechsel", "5.02": "Wechsel im Vorstand", "5.03": "Satzungsänderung", "5.07": "Hauptversammlung",
    "5.08": "Termin Hauptversammlung", "7.01": "Mitteilung an Investoren",
    "8.01": "sonstige Meldung", "9.01": "Anlagen",
}


def _einstellungen() -> dict:
    return yaml.safe_load(QUELLEN_DATEI.read_text(encoding="utf-8")) or {}


def _kontakt() -> str:
    """Absenderangabe für SEC und BLS. Beide verlangen Name und E-Mail im User-Agent."""
    return key("SEC_USER_AGENT")


def _hole(adresse: str, user_agent: str, zeitlimit: int = 15) -> bytes:
    anfrage = urllib.request.Request(adresse, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(anfrage, timeout=zeitlimit, context=_SSL) as antwort:
        return antwort.read()


def _zeit(eintrag) -> str | None:
    t = eintrag.get("published_parsed") or eintrag.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(t), timezone.utc).isoformat() if t else None


def _rss(quelle: dict) -> list:
    if quelle.get("kontakt"):
        if not _kontakt():
            raise LookupError("SEC_USER_AGENT fehlt in .env")
        agent = _kontakt()
    else:
        agent = "Mozilla/5.0"
    feed = feedparser.parse(_hole(quelle["adresse"], agent))
    meldungen = []
    for e in feed.entries:
        zeit = _zeit(e)
        if not (e.get("title") and zeit):
            continue
        titel = re.sub(r"^FinancialJuice:\s*", "", e.title.strip())
        meldungen.append({"titel": titel, "zeit": zeit, "link": e.get("link") or ""})
    return meldungen


def _finnhub(ticker: str, wert: dict) -> list:
    if not finnhub.verfuegbar():
        raise LookupError("FINNHUB_API_KEY fehlt in .env")
    heute = date.today()
    roh = finnhub._abruf("company-news", symbol=ticker, **{"from": str(heute - timedelta(days=3)), "to": str(heute)})
    # Finnhub ordnet einem Wert auch allgemeine Marktberichte zu. Nur Schlagzeilen, die ihn nennen, bleiben.
    muster = _muster(wert.get("aliase") or [ticker])
    return [
        {"titel": m["headline"], "zeit": datetime.fromtimestamp(m["datetime"], timezone.utc).isoformat(),
         "link": m.get("url") or "", "herkunft": m.get("source") or "", "ticker": [ticker]}
        for m in roh if m.get("headline") and m.get("datetime") and muster.search(m["headline"])
    ]


def _sec(ticker: str, wert: dict) -> list:
    if not _kontakt():
        raise LookupError("SEC_USER_AGENT fehlt in .env")
    adresse = "https://www.sec.gov/cgi-bin/browse-edgar?" + urllib.parse.urlencode(
        {"action": "getcompany", "CIK": ticker, "type": "8-K", "owner": "include", "count": 10, "output": "atom"}
    )
    with _SEC_SPERRE:  # eine Anfrage nach der anderen, mit kurzer Pause
        roh = _hole(adresse, _kontakt(), zeitlimit=25)
        time.sleep(0.2)
    feed = feedparser.parse(roh)
    meldungen = []
    for e in feed.entries:
        zeit = _zeit(e)
        if not zeit or not (e.get("title") or "").startswith("8-K"):
            continue
        punkte = sorted(set(re.findall(r"Item (\d\.\d\d)", e.get("summary") or "")) - {"9.01"})
        inhalt = ", ".join(SEC_PUNKTE.get(p, f"Punkt {p}") for p in punkte)
        meldungen.append({
            "titel": f"{wert.get('name') or ticker}: Pflichtmeldung 8-K" + (f" ({inhalt})" if inhalt else ""),
            "zeit": zeit, "link": e.get("link") or "", "ticker": [ticker], "tags": ["Earnings"] if "2.02" in punkte else [],
        })
    return meldungen


def _abfrage(quelle: dict, ticker: str | None = None, wert: dict | None = None) -> tuple[list, str | None]:
    """Meldungen einer Quelle (für einen Wert) aus dem Speicher oder frisch. Gibt (Meldungen, Fehlertext) zurück."""
    schluessel = f"squawk:{quelle['name']}:{ticker or ''}"
    alt, alter = cache_lesen(schluessel)
    if alt is not None and alter < quelle.get("intervall_s", 300):
        return alt, None
    # Nach einem Fehlschlag eine Pause einlegen, statt die Quelle mit Anfragen zu bedrängen.
    _, seit_fehler = cache_lesen(f"{schluessel}:fehler")
    if seit_fehler is not None and seit_fehler < max(PAUSE_NACH_FEHLER, quelle.get("intervall_s", 300)):
        return alt or [], "gerade nicht erreichbar"
    try:
        if quelle["art"] == "rss":
            neu = _rss(quelle)
        elif quelle["art"] == "finnhub":
            neu = _finnhub(ticker, wert)
        else:
            neu = _sec(ticker, wert)
    except LookupError as fehlt:
        return alt or [], str(fehlt)
    except Exception:
        cache_schreiben(f"{schluessel}:fehler", True)
        return alt or [], "gerade nicht erreichbar"
    # Feeds zeigen oft nur die letzten 100 Einträge. Ältere bleiben hier, bis sie zu alt sind.
    grenze = (datetime.now(timezone.utc) - timedelta(days=AUFBEWAHREN_TAGE)).isoformat()
    bekannt = {(m["titel"], m["zeit"]) for m in neu}
    neu = neu + [m for m in alt or [] if (m["titel"], m["zeit"]) not in bekannt and m["zeit"] >= grenze]
    cache_schreiben(schluessel, neu)
    return neu, None


def _muster(woerter: list[str]) -> re.Pattern:
    """Findet jedes Wort nur als ganzes Wort, ein angehängtes s oder es ist erlaubt."""
    teile = sorted({re.escape(w.strip()) for w in woerter if w and w.strip()}, key=len, reverse=True)
    if not teile:
        return re.compile(r"(?!x)x")
    return re.compile(r"(?<!\w)(?:" + "|".join(teile) + r")(?:e?s)?(?!\w)", re.IGNORECASE)


# "fall over 4%", "jumps 5.2%", "tumbled more than 3%": ein Bewegungswort, danach eine Prozentzahl.
_BEWEGUNG = re.compile(
    r"\b(?:fall|fell|drop|plunge|tumble|slump|sink|sank|slide|slid|crash|plummet|surge|jump|soar|rall(?:y|ie)|spike|rise|rose|gain|climb|skyrocket)"
    r"\w*\s+(?:over\s+|more\s+than\s+|by\s+|nearly\s+|almost\s+|about\s+)?(\d+(?:[.,]\d+)?)\s?%",
    re.IGNORECASE,
)


def grosse_bewegung(titel: str, ab_prozent: float) -> bool:
    return any(float(z.replace(",", ".")) >= ab_prozent for z in _BEWEGUNG.findall(titel))


def muster_wichtig_eigene() -> re.Pattern:
    """Wörter, die eine Meldung zu einem eigenen Wert wichtig machen (für app/nutzer)."""
    return _muster(_einstellungen().get("wichtig_eigene") or [])


# Vorlagenwörter von Datenmeldungen ("... Actual 0.4% (Forecast 0.8%, Previous 1.1%)") zählen beim
# Vergleich nicht, sonst wären zwei verschiedene Kennzahlen desselben Landes "dieselbe Nachricht".
_VORLAGE = {"actual", "forecast", "previous", "yoy", "mom", "qoq", "ytd", "the", "and", "for", "with"}


def _woerter(titel: str) -> set[str]:
    return {w for w in re.findall(r"[a-zäöüß]+", titel.lower()) if len(w) > 2 and w not in _VORLAGE}


def _zusammenfassen(meldungen: list) -> list:
    """Dieselbe Nachricht aus mehreren Quellen wird eine Zeile; die früheste Meldung bleibt stehen."""
    ergebnis = []
    for m in sorted(meldungen, key=lambda m: m["zeit"]):
        w = _woerter(m["titel"])
        zeit = datetime.fromisoformat(m["zeit"])
        for e in ergebnis:
            if abs(zeit - datetime.fromisoformat(e["zeit"])) > GLEICH_INNERHALB or not w or not e["_w"]:
                continue
            if len(w & e["_w"]) / len(w | e["_w"]) >= GLEICHE_MELDUNG:
                e["quellen"] = sorted(set(e["quellen"]) | set(m["quellen"]))
                e["ticker"] = sorted(set(e["ticker"]) | set(m["ticker"]))
                e["tags"] = sorted(set(e["tags"]) | set(m["tags"]))
                break
        else:
            ergebnis.append({**m, "_w": w})
    for e in ergebnis:
        del e["_w"]
    return ergebnis


def feed(werte: dict[str, dict] | None = None) -> dict:
    """Alle Meldungen, neueste zuerst.

    werte: US-Ticker, für die Finnhub und SEC abgefragt werden, je mit "name" und "aliase"
    (Wörter, an denen man den Wert in einer Schlagzeile erkennt).
    """
    werte = werte or {}
    einst = _einstellungen()
    aufgaben = []
    for q in einst.get("quellen") or []:
        if q.get("aktiv") is False:
            continue
        if q["art"] == "rss":
            aufgaben.append((q, None, None))
        else:
            aufgaben += [(q, t, w) for t, w in werte.items()]

    with ThreadPoolExecutor(max_workers=8) as pool:
        antworten = list(pool.map(lambda a: _abfrage(*a), aufgaben))

    grenze = datetime.now(timezone.utc) - timedelta(days=einst.get("tage", 3))
    ausblenden = _muster(einst.get("ausblenden") or [])
    tag_muster = {tag: _muster(woerter) for tag, woerter in (einst.get("tags") or {}).items()}
    wichtig = _muster(einst.get("wichtig") or [])
    ab_prozent = float(einst.get("wichtig_ab_prozent") or 1000)

    alle, status = [], {}
    for (q, _, _), (meldungen, fehler) in zip(aufgaben, antworten):
        s = status.setdefault(q["name"], {"name": q["name"], "fehler": None, "hinweis": q.get("hinweis"), "_ok": False})
        s["_ok"] = s["_ok"] or not fehler
        if fehler and not s["fehler"]:
            s["fehler"] = fehler
        if q.get("max_pro_wert"):  # sonst füllt ein Wert mit vielen Artikeln den ganzen Feed
            meldungen = sorted(meldungen, key=lambda m: m["zeit"], reverse=True)[: q["max_pro_wert"]]
        for m in meldungen:
            if datetime.fromisoformat(m["zeit"]) < grenze or ausblenden.search(m["titel"]):
                continue
            tags = set(q.get("tags") or []) | set(m.get("tags") or [])
            tags |= {tag for tag, muster in tag_muster.items() if muster.search(m["titel"])}
            alle.append({
                "titel": m["titel"], "zeit": m["zeit"], "link": m["link"],
                "quellen": [q["name"] if not m.get("herkunft") else f"{m['herkunft']} via {q['name']}"],
                "ticker": m.get("ticker") or [], "tags": sorted(tags),
                "wichtig": bool(wichtig.search(m["titel"])) or grosse_bewegung(m["titel"], ab_prozent),
            })

    meldungen = sorted(_zusammenfassen(alle), key=lambda m: m["zeit"], reverse=True)[:MAX_MELDUNGEN]
    return {
        "meldungen": meldungen,
        # Ein Hinweis nur, wenn eine Quelle ganz ausfällt. Fehlt nur ein Wert, bleiben seine alten Meldungen stehen.
        "quellen": [{k: v for k, v in s.items() if k != "_ok"} | ({"fehler": None} if s["_ok"] else {}) for s in status.values()],
        "aktualisiert": datetime.now(timezone.utc).isoformat(),
    }


def schlagzeile() -> dict | None:
    """Für die Leiste oben: die neueste wichtige Meldung der letzten 18 Stunden,
    sonst die neueste Marktmeldung der letzten 6 Stunden mit einem Tag."""
    jetzt = datetime.now(timezone.utc)
    meldungen = feed()["meldungen"]
    alter = lambda m: jetzt - datetime.fromisoformat(m["zeit"])
    m = next((m for m in meldungen if m["wichtig"] and alter(m) <= timedelta(hours=18)), None)
    m = m or next((m for m in meldungen if m["tags"] and alter(m) <= timedelta(hours=6)), None)
    return m and {"titel": m["titel"], "quelle": m["quellen"][0], "zeit": m["zeit"], "link": m["link"]}
