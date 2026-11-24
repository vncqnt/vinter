"""Newsfeed für einen Benutzer: Geschichten aus app/markt/nachrichten plus der Bezug zu Depot, Watchlist und Thesen.

Zwei Bereiche:
- "Markt bewegend": alle Geschichten ab einem Mindest-Score, nie nach Thesen gefiltert (nur Werbung fliegt raus)
- "Meine Positionen": Geschichten, die eine eigene Position oder einen Watchlist-Wert nennen oder ein
  News-Thema einer These treffen, unabhängig vom Score

Bewertungen (wichtig / unwichtig / Werbung) landen je Benutzer in news_bewertungen.jsonl. Daraus lernt später
ein kleines Modell (Phase H).
"""
import json
import re
from datetime import datetime, timezone

from ..benutzer import Benutzer
from ..markt.nachrichten import geschichten, speicher
from ..markt.kurse import ticker_fuer
from ..markt.nachrichten import nachrecherche
from . import thesen, watchlist
from .portfolio import berechne
from .tr_parser import lade_trades

MARKT_AB = 30          # Mindest-Score für "Markt bewegend"
# Erste Wörter, die allein zu viel treffen würden ("Siemens" ist nicht Siemens Energy).
ZU_ALLGEMEIN = {"siemens", "sharon", "core", "physical", "ftse", "general", "american", "first"}
# Ticker, die auch normale englische Wörter sind, werden nicht als Erkennungswort benutzt.
TICKER_WOERTER = {"NOW", "ALL", "ON", "IT", "ARE", "CAT", "KEY", "LOW", "BIG", "FAST"}


def _aliase(name: str, ticker: str | None) -> list[str]:
    aliase = [name]
    erstes = name.split()[0]
    if len(name.split()) > 1 and len(erstes) >= 4 and erstes.lower() not in ZU_ALLGEMEIN:
        aliase.append(erstes)
    if ticker and "." not in ticker and "-" not in ticker and len(ticker) >= 3 and ticker not in TICKER_WOERTER:
        aliase.append(ticker)
    return aliase


def _depot(b: Benutzer) -> list[dict]:
    """Aktien und Krypto im Depot mit Ticker und Erkennungswörtern. ETFs bleiben außen vor."""
    try:
        positionen = berechne(lade_trades(b))["positionen"]
    except Exception:
        return []
    werte = []
    for p in positionen:
        if p["art"] not in ("STOCK", "CRYPTO"):
            continue
        ticker = ticker_fuer(p["isin"], p["art"])
        werte.append({"name": p["name"], "ticker": ticker, "aliase": _aliase(p["name"], ticker)})
    return werte


def _themen_muster(b: Benutzer) -> list[tuple[str, re.Pattern]]:
    """News-Themen aus den Thesen. Ein Thema trifft, wenn alle seine längeren Wörter in der Schlagzeile stehen."""
    muster = []
    for t in thesen.alle_thesen(b).get("thesen", []):
        for thema in t.get("news_themen") or []:
            woerter = [w for w in re.findall(r"\w+", thema) if len(w) >= 4]
            if woerter:
                muster.append((thema, [re.compile(rf"(?<!\w){re.escape(w)}", re.IGNORECASE) for w in woerter]))
    return muster

URTEILE = {"wichtig", "unwichtig", "werbung"}


def _eigene(b: Benutzer) -> list[tuple[dict, re.Pattern]]:
    werte = [{**w, "quelle": "Depot"} for w in _depot(b)]
    bekannt = {w["ticker"] for w in werte}
    try:
        for w in watchlist._lesen(b):  # nur die Liste, ohne Kurse abzurufen
            t = w.get("ticker")
            if t and t not in bekannt:
                name = w.get("name") or t
                werte.append({"name": name, "ticker": t, "aliase": [name] + ([t] if len(t) >= 3 and t.isalpha() else []), "quelle": "Watchlist"})
                bekannt.add(t)
    except Exception:
        pass
    return [(w, re.compile(r"(?<!\w)(?:" + "|".join(re.escape(a) for a in w["aliase"] if a) + r")(?!\w)", re.I)) for w in werte if w.get("aliase")]


def uebersicht(b: Benutzer, stunden: int = 24) -> dict:
    stunden = max(1, min(int(stunden), 72))
    stories, gefiltert = geschichten.liste(b.name, stunden, ausgefiltert=True)
    eigene = _eigene(b)
    themen = _themen_muster(b)
    # Firmen-News eigener US-Werte im Hintergrund mitlesen (Finnhub, je Wert höchstens alle 5 Minuten)
    nachrecherche.beobachten({w["ticker"]: w["aliase"] for w, _ in eigene if w.get("ticker")})
    for s in stories:
        titel = " \n".join(m["titel"] for m in s["meldungen"])
        s["positionen"] = [{"name": w["name"], "ticker": w["ticker"], "quelle": w["quelle"]} for w, muster in eigene
                           if (w["ticker"] and w["ticker"] in s["ticker"]) or muster.search(titel)]
        s["thesen"] = [thema for thema, teile in themen if all(t.search(titel) for t in teile)]
    markt = [s for s in stories if s["score"] >= MARKT_AB]
    meine = [s for s in stories if s["positionen"] or s["thesen"]]
    return {
        "markt": markt, "meine": meine, "alle": stories, "ausgefiltert": gefiltert,
        "quellen": [q for q in speicher.alle_quellen() if not q["name"].startswith("Telegram") or _telegram_sichtbar(b)],
        "stunden": stunden, "stand": speicher.jetzt(), "bewertungen": anzahl_bewertungen(b),
    }


def _telegram_sichtbar(b: Benutzer) -> bool:
    from ..einstellungen import key
    return key("TELEGRAM_BENUTZER") == b.name


def bewerten(b: Benutzer, story_id: int, urteil: str) -> dict:
    if urteil not in URTEILE:
        raise ValueError("Unbekannte Bewertung.")
    stories = {s["id"]: s for s in geschichten.liste(b.name, 72)}
    s = stories.get(int(story_id))
    if not s:
        raise ValueError("Diese Geschichte gibt es nicht (mehr).")
    zeile = {"zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"), "story": s["id"], "urteil": urteil,
             "titel": s["titel"], "ereignis": s["ereignis"], "score": s["score"], "quellen": s["quellen"],
             "anzahl": s["anzahl"], "bestaetigt": s["bestaetigt"], "ticker": s["ticker"]}
    datei = b.datei("news_bewertungen.jsonl")
    datei.parent.mkdir(parents=True, exist_ok=True)
    with open(datei, "a", encoding="utf-8") as f:
        f.write(json.dumps(zeile, ensure_ascii=False) + "\n")
    return {"ok": True, "bewertungen": anzahl_bewertungen(b)}


def anzahl_bewertungen(b: Benutzer) -> int:
    datei = b.datei("news_bewertungen.jsonl")
    if not datei.exists():
        return 0
    with open(datei, encoding="utf-8") as f:
        return sum(1 for z in f if z.strip())
