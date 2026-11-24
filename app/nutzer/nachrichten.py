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
LERNEN_AB = 30         # so viele Bewertungen braucht das persönliche Modell
_modelle: dict[str, tuple] = {}
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
    gelernt = _anwenden(b, stories)
    weg = [s for s in stories if s.get("gelernt_werbung")]
    stories = [s for s in stories if not s.get("gelernt_werbung")]
    stories.sort(key=lambda s: -s["rang"])
    gefiltert = [{"id": f"s{s['id']}", "quelle": ", ".join(s["quellen"]), "titel": s["titel"], "link": s["meldungen"][0]["link"],
                  "gesehen": s["letztes"], "filter": "Werbung (gelernt aus deinen Bewertungen)"} for s in weg] + gefiltert
    markt = [s for s in stories if s["score"] >= MARKT_AB]
    meine = [s for s in stories if s["positionen"] or s["thesen"]]
    return {
        "markt": markt, "meine": meine, "alle": stories, "ausgefiltert": gefiltert,
        "quellen": [q for q in speicher.alle_quellen() if not q["name"].startswith("Telegram") or _telegram_sichtbar(b)],
        "stunden": stunden, "stand": speicher.jetzt(), "bewertungen": anzahl_bewertungen(b),
        "lernen": {"ab": LERNEN_AB, "aktiv": gelernt is not None},
    }


def live(b: Benutzer, stunden: int = 12, ausgefiltert: bool = False) -> dict:
    """Alle Meldungen einzeln und streng nach Zeit (wie ein Terminal-Ticker), neueste zuerst.
    Dazu je Meldung: eigene Werte, Ereignistyp, Größe und Score der zugehörigen Geschichte."""
    from datetime import timedelta
    stunden = max(1, min(int(stunden), 72))
    seit = (datetime.now(timezone.utc) - timedelta(hours=stunden)).isoformat()
    zeilen = speicher.neueste(b.name, seit, grenze=600)
    if not ausgefiltert:
        zeilen = [m for m in zeilen if not m.get("filter")]
    db = speicher.verbinden()
    try:
        ids = list({m["story"] for m in zeilen if m.get("story")})
        stories = {}
        if ids:
            for r in db.execute(f"SELECT id, anzahl, score, bestaetigt FROM stories WHERE id IN ({','.join('?' * len(ids))})", ids):
                stories[r["id"]] = dict(r)
    finally:
        db.close()
    eigene = _eigene(b)
    aus = []
    for m in zeilen:
        x = m.get("extrakt") or {}
        s = stories.get(m.get("story")) or {}
        aus.append({
            "id": m["id"], "zeit": m["gesehen"], "quelle": m["quelle"], "stufe": m["stufe"], "art": m["art"],
            "titel": m["titel"], "link": m["link"], "filter": m.get("filter"),
            "ereignis": x.get("ereignis"), "ticker": x.get("ticker", []), "eil": bool(x.get("eil")),
            "positionen": [w["ticker"] or w["name"] for w, muster in eigene
                           if (w["ticker"] and w["ticker"] in x.get("ticker", [])) or muster.search(m["titel"])],
            "story": m.get("story"), "story_anzahl": s.get("anzahl", 1), "story_score": s.get("score", 0),
            "bestaetigt": bool(s.get("bestaetigt")),
        })
    return {"meldungen": aus, "stunden": stunden, "stand": speicher.jetzt()}


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


# ---- Lernen aus den Bewertungen (Phase H) ----
# Logistische Regression auf dem Vektor der Geschichte (Sprachmodell) plus ein paar Merkmalen. Klein, schnell,
# läuft lokal. Ergebnis: Wahrscheinlichkeit für wichtig / unwichtig / Werbung je Geschichte.

def _bewertungen(b: Benutzer) -> dict[int, str]:
    datei = b.datei("news_bewertungen.jsonl")
    if not datei.exists():
        return {}
    letzte = {}
    with open(datei, encoding="utf-8") as f:
        for z in f:
            try:
                d = json.loads(z)
                letzte[int(d["story"])] = d["urteil"]  # spätere Bewertung derselben Geschichte gilt
            except (ValueError, KeyError):
                continue
    return letzte


def _merkmale(ids: list[int]) -> dict[int, "object"]:
    import numpy as np
    from ..markt.nachrichten.geschichten import GEWICHT, _aus_bytes
    if not ids:
        return {}
    arten = sorted(GEWICHT)
    db = speicher.verbinden()
    try:
        zeilen = db.execute(f"SELECT id, vektor, score, anzahl, quellen, bestaetigt, ereignis FROM stories WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    finally:
        db.close()
    aus = {}
    for r in zeilen:
        v = _aus_bytes(r["vektor"])
        if v is None:
            continue
        extra = [(r["score"] or 0) / 100, min(r["anzahl"] or 1, 10) / 10, float(r["bestaetigt"] or 0),
                 min(len(json.loads(r["quellen"] or "[]")), 5) / 5] + [1.0 if r["ereignis"] == a else 0.0 for a in arten]
        aus[r["id"]] = np.concatenate([v, np.array(extra, dtype=np.float32)])
    return aus


def _modell(b: Benutzer):
    urteile = _bewertungen(b)
    if len(urteile) < LERNEN_AB:
        return None
    zwischen = _modelle.get(b.name)
    if zwischen and zwischen[0] == len(urteile):
        return zwischen[1]
    import numpy as np
    from collections import Counter
    from sklearn.linear_model import LogisticRegression
    merkmale = _merkmale(list(urteile))
    paare = [(merkmale[i], u) for i, u in urteile.items() if i in merkmale]
    zaehler = Counter(u for _, u in paare)
    if len(zaehler) < 2 or min(zaehler.values()) < 3:
        _modelle[b.name] = (len(urteile), None)
        return None
    modell = LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced")
    modell.fit(np.stack([x for x, _ in paare]), [u for _, u in paare])
    _modelle[b.name] = (len(urteile), modell)
    return modell


def _anwenden(b: Benutzer, stories: list[dict]):
    """Score persönlich anpassen (±25 Punkte) und sicher erkannte Werbung markieren. None = noch kein Modell."""
    try:
        modell = _modell(b)
    except Exception:
        return None
    if modell is None or not stories:
        return None
    import numpy as np
    from ..markt.nachrichten.geschichten import rang
    merkmale = _merkmale([s["id"] for s in stories])
    mit = [s for s in stories if s["id"] in merkmale]
    if not mit:
        return modell
    wahrsch = modell.predict_proba(np.stack([merkmale[s["id"]] for s in mit]))
    for s, p in zip(mit, wahrsch):
        d = dict(zip(modell.classes_, p))
        s["gelernt"] = {str(k): round(float(v), 2) for k, v in d.items()}
        s["score_basis"] = s["score"]
        s["score"] = float(max(0, min(100, s["score"] + 25 * (d.get("wichtig", 0) - d.get("unwichtig", 0)))))
        s["rang"] = round(rang(s["score"], s["letztes"]), 2)
        s["gelernt_werbung"] = d.get("werbung", 0) > 0.7
    return modell


def luecken(b: Benutzer) -> dict:
    """Kurssprünge über 2 Sigma (eigene Werte, S&P 500, Nasdaq 100) ohne passende Geschichte."""
    from ..markt.nachrichten import messung
    werte = {w["ticker"]: w["name"] for w, _ in _eigene(b) if w.get("ticker")}
    return {"markt": messung.luecken_markt(), "werte": messung.luecken_werte(werte, b.name)}


def anzahl_bewertungen(b: Benutzer) -> int:
    datei = b.datei("news_bewertungen.jsonl")
    if not datei.exists():
        return 0
    with open(datei, encoding="utf-8") as f:
        return sum(1 for z in f if z.strip())
