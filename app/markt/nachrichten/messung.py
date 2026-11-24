"""Messung der Quellen und Lückenprüfung (für die Status-Seite).

quellen_messung(): Für jede Geschichte mit mindestens zwei Quellen wird festgehalten, welche Quelle sie wann zuerst
hatte (Zeitpunkt "zuerst gesehen"). Je Quelle: wie oft zuerst, mittlere Verspätung gegenüber der schnellsten Quelle,
Vollständigkeit (Anteil der wichtigen Geschichten, die die Quelle überhaupt hatte). Nur Quellen, die Vinter selbst
laufend abfragt (keine Nachrecherche, keine eigenen Meldungen).

ausfaelle(): Quellen mit Fehlerserie oder ungewöhnlich langer Stille (je Quelle eine erwartete Höchstpause,
an Wochenenden doppelt so lang).

luecken_markt(): Tage der letzten Woche, an denen S&P 500 oder Nasdaq 100 sich um mehr als 2 Sigma bewegt haben,
ohne dass es eine wichtige Geschichte gab (Score ab 45). Für eigene Positionen macht das die Benutzerschicht.
"""
import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import speicher

LAUFEND = ("rss", "telegram", "seite")
WICHTIG_AB = 40
STILLE_MIN = {"FinancialJuice": 120, "SEC 8-K": 180, "SEC 6-K": 360, "GlobeNewswire": 240, "PR Newswire": 240,
              "Business Wire": 360, "Telegram": 240}


def quellen_messung(tage: int = 7) -> list[dict]:
    grenze = (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat()
    db = speicher.verbinden()
    try:
        zeilen = db.execute(
            f"SELECT m.story, m.quelle, MIN(m.gesehen) AS zuerst, s.score FROM meldungen m JOIN stories s ON s.id = m.story "
            f"WHERE m.gesehen >= ? AND m.art IN ({','.join('?' * len(LAUFEND))}) AND m.filter IS NULL GROUP BY m.story, m.quelle",
            (grenze, *LAUFEND)).fetchall()
    finally:
        db.close()
    je_story: dict[int, list] = defaultdict(list)
    for r in zeilen:
        je_story[r["story"]].append((r["quelle"], datetime.fromisoformat(r["zuerst"]), r["score"] or 0))
    stat = defaultdict(lambda: {"geschichten": 0, "zuerst": 0, "verzug": [], "wichtige": 0})
    wichtige_gesamt = 0
    for eintraege in je_story.values():
        if len({q for q, _, _ in eintraege}) < 2:
            continue
        erste = min(t for _, t, _ in eintraege)
        wichtig = eintraege[0][2] >= WICHTIG_AB
        wichtige_gesamt += wichtig
        for q, t, _ in eintraege:
            s = stat[q]
            s["geschichten"] += 1
            s["wichtige"] += wichtig
            verzug = (t - erste).total_seconds() / 60
            s["verzug"].append(verzug)
            s["zuerst"] += verzug < 0.5
    aus = []
    for q, s in stat.items():
        aus.append({"quelle": q, "geschichten": s["geschichten"], "anteil_zuerst": round(s["zuerst"] / s["geschichten"], 3),
                    "verzug_median_min": round(statistics.median(s["verzug"]), 1),
                    "vollstaendigkeit": round(s["wichtige"] / wichtige_gesamt, 3) if wichtige_gesamt else None})
    aus.sort(key=lambda x: (-x["geschichten"], x["verzug_median_min"]))
    return aus


def ausfaelle() -> list[dict]:
    jetzt = datetime.now(timezone.utc)
    wochenende = jetzt.weekday() >= 5
    aus = []
    for q in speicher.alle_quellen():
        name = q["name"]
        if (q.get("fehler_serie") or 0) >= 3:
            aus.append({"quelle": name, "grund": f"{q['fehler_serie']} Fehler hintereinander: {q.get('fehler_text') or 'unbekannt'}"})
            continue
        grenze = STILLE_MIN.get("Telegram" if name.startswith("Telegram") else name)
        if not grenze or not q.get("letzte_neue"):
            continue
        still = (jetzt - datetime.fromisoformat(q["letzte_neue"])).total_seconds() / 60
        if still > grenze * (2 if wochenende else 1):
            aus.append({"quelle": name, "grund": f"seit {still / 60:.1f} Std. keine neue Meldung (üblich höchstens {grenze / 60:.0f} Std.)"})
    return aus


def tagesbewegungen(ticker: str) -> list[dict]:
    """Tagesbewegungen der letzten 5 Handelstage mit Sigma (aus einem Jahr Tagesrenditen)."""
    import numpy as np
    from ..abruf import STUNDE, gecacht
    import yfinance as yf

    def holen():
        s = yf.Ticker(ticker).history(period="1y")["Close"].dropna()
        return [[d.strftime("%Y-%m-%d"), float(v)] for d, v in s.items()]
    reihe = gecacht(f"nachrichten:tage:{ticker}", 6 * STUNDE, holen)
    if len(reihe) < 60:
        return []
    werte = np.array([v for _, v in reihe])
    r = np.diff(np.log(werte))
    sigma = float(np.std(r[:-5])) or 1e-9
    return [{"datum": reihe[i + 1][0], "veraenderung": round(float(np.expm1(r[i])) * 100, 2), "sigma": round(float(r[i] / sigma), 1)}
            for i in range(len(r) - 5, len(r))]


def _geschichten_am(datum: str, benutzer: str = "") -> list[dict]:
    db = speicher.verbinden()
    try:
        return [dict(r) for r in db.execute(
            "SELECT id, titel, score, ticker FROM stories WHERE substr(erstes, 1, 10) <= ? AND substr(letztes, 1, 10) >= ? "
            "AND (nur_fuer IS NULL OR nur_fuer = ?)", (datum, datum, benutzer))]
    finally:
        db.close()


def _feed_seit() -> str:
    """Erster Tag mit gespeicherten Meldungen: davor kann es keine Geschichten geben."""
    db = speicher.verbinden()
    try:
        # Nur Feeds (RSS, Telegram): deren erster Abruf reicht höchstens 2 Tage zurück, Webseiten können ältere Einträge haben
        r = db.execute("SELECT MIN(gesehen) FROM meldungen WHERE art IN ('rss', 'telegram')").fetchone()
        return (r[0] or "9999")[:10]
    finally:
        db.close()


def luecken_markt() -> list[dict]:
    seit = _feed_seit()
    aus = []
    for ticker, name in (("SPY", "S&P 500"), ("QQQ", "Nasdaq 100")):
        try:
            tage = tagesbewegungen(ticker)
        except Exception:
            continue
        for t in tage:
            if t["datum"] > seit and abs(t["sigma"]) >= 2 and not any((g["score"] or 0) >= 45 for g in _geschichten_am(t["datum"])):
                aus.append({"wert": name, **t})
    return aus


def luecken_werte(werte: dict[str, str], benutzer: str) -> list[dict]:
    """werte: Ticker → Name. Kurssprünge über 2 Sigma ohne Geschichte, die den Ticker nennt."""
    seit = _feed_seit()
    aus = []
    for ticker, name in list(werte.items())[:30]:
        try:
            tage = tagesbewegungen(ticker)
        except Exception:
            continue
        for t in tage:
            if t["datum"] > seit and abs(t["sigma"]) >= 2:
                treffer = [g for g in _geschichten_am(t["datum"], benutzer) if ticker in json.loads(g["ticker"] or "[]")]
                if not treffer:
                    aus.append({"wert": name, "ticker": ticker, **t})
    return aus


def status() -> dict:
    """Für /api/status."""
    try:
        return {"quellen": quellen_messung(), "ausfaelle": ausfaelle(), "luecken": luecken_markt()}
    except Exception as f:
        return {"fehler": f"{type(f).__name__}: {str(f)[:100]}"}
