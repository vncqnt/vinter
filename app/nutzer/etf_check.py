"""ETF-Check: nur der ETF-Teil des Depots (Einzelaktien bleiben außen vor).

1. Was in jedem eigenen ETF steckt: Länder (aus daten/markt/etf_liste.yaml), Branchen und Top-10 (Yahoo).
2. Doppelungen je Paar, sichtbar gemacht: welche Länder beide abdecken (je Region der kleinere Anteil)
   und welche großen Firmen in beiden stecken; dazu gemessen, wie gleich sie sich bewegen (R²).
   Geschätzte Überschneidung = Summe der gemeinsamen Länderanteile × Faktor für die Art
   (große und kleine Firmen haben nichts gemeinsam, Stil- und Branchen-ETFs nur einen Teil).
3. Zutaten für den ETF-Finder: Für jeden Kandidaten Länder, Branchen, Kosten, Ausschüttung und die
   Kennzahlen, mit denen der Browser für jeden Betrag die neue Schwankung des ETF-Teils ausrechnet:
   Varianz(neu) = (1-a)²·V_teil + a²·V_kandidat + 2·a·(1-a)·Kov, a = Anteil des neuen Geldes.
Alle Kurse in Euro, Wochenrenditen der letzten 3 Jahre. Information, keine Anlageberatung.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..markt.etf_daten import REGIONEN, etf_liste, yahoo_fonds
from ..markt.euro_kurse import euro_kurse

JAHRE = 3
MIN_WOCHEN = 52
PAARE = {frozenset({"gross", "alle"}): 0.9, frozenset({"klein", "alle"}): 0.1, frozenset({"gross", "klein"}): 0.0,
         frozenset({"stil", "gross"}): 0.5, frozenset({"stil", "alle"}): 0.45, frozenset({"branche", "gross"}): 0.12,
         frozenset({"branche", "alle"}): 0.11, frozenset({"branche", "stil"}): 0.1, frozenset({"klein", "stil"}): 0.05,
         frozenset({"klein", "branche"}): 0.02, frozenset({"land", "gross"}): 0.9, frozenset({"land", "alle"}): 0.85,
         frozenset({"land", "stil"}): 0.4, frozenset({"land", "klein"}): 0.0, frozenset({"land", "branche"}): 0.1}


def _segment_faktor(a: dict, b: dict) -> float:
    sa, sb = a.get("segment", "gross"), b.get("segment", "gross")
    if a.get("index") and a.get("index") == b.get("index"):
        return 1.0
    if sa == sb:
        return {"stil": 0.35, "branche": 0.05, "land": 0.9}.get(sa, 1.0)
    return PAARE.get(frozenset({sa, sb}), 0.0)


def gemeinsame_regionen(a: dict, b: dict) -> dict:
    ra, rb = a.get("regionen") or {}, b.get("regionen") or {}
    return {r: min(ra.get(r, 0), rb.get(r, 0)) for r in REGIONEN if min(ra.get(r, 0), rb.get(r, 0)) > 0}


def ueberschneidung(a: dict, b: dict) -> float:
    """Geschätzter Anteil gleicher Firmen (0 bis 1). Nur innerhalb derselben Anlageklasse."""
    if a.get("klasse") != b.get("klasse"):
        return 0.0
    if a.get("klasse") in ("gold", "geldmarkt", "rohstoffe"):
        return 1.0 if a.get("index") == b.get("index") else 0.0
    return sum(gemeinsame_regionen(a, b).values()) / 100 * _segment_faktor(a, b)


def _verlauf(r: pd.Series) -> dict:
    """Rendite pro Jahr und größter Absturz über den Zeitraum, aus Wochenrenditen."""
    r = r.dropna()
    kurs = (1 + r).cumprod()
    jahre = len(r) / 52
    return {"rendite_jahr": float((kurs.iloc[-1] ** (1 / jahre) - 1) * 100) if jahre > 0 else None,
            "max_absturz": float((kurs / kurs.cummax() - 1).min() * 100)}


def etf_check(positionen: list[dict]) -> dict:
    liste = etf_liste()
    bekannt = liste.get("etfs") or {}
    welt = liste.get("welt") or {}
    pos = [p for p in positionen if p.get("ticker") and p.get("wert_eur")]
    eigene = [p for p in pos if p["ticker"] in bekannt]
    unbekannt = [p["name"] for p in pos if p.get("art") == "FUND" and p["ticker"] not in bekannt]
    if not eigene:
        return {"fehler": "In deinem Depot ist kein ETF aus daten/markt/etf_liste.yaml."}
    eigene_indizes = {bekannt[p["ticker"]].get("index") for p in eigene}
    kandidaten = [t for t, e in bekannt.items() if e.get("kandidat", True) and e.get("index") not in eigene_indizes]
    alle = [p["ticker"] for p in eigene] + kandidaten

    with ThreadPoolExecutor(max_workers=6) as pool:
        yahoo = dict(zip(alle, pool.map(yahoo_fonds, alle)))

    def ter(t: str):
        return yahoo.get(t, {}).get("ter") if yahoo.get(t, {}).get("ter") is not None else bekannt[t].get("ter")

    def steckbrief(t: str) -> dict:
        e, y = bekannt[t], yahoo.get(t, {})
        return {"ticker": t, "name": e["name"], "index": e.get("index"), "klasse": e.get("klasse", "aktien"), "segment": e.get("segment", "gross"),
                "regionen": e.get("regionen") or {}, "ter": ter(t),
                "ter_quelle": "Yahoo" if y.get("ter") is not None else ("eigener Eintrag" if e.get("ter") is not None else None),
                "ausschuettend": y.get("ausschuettend"), "langname": y.get("langname"), "text": e.get("text"),
                "tech": y.get("branchen", {}).get("technology"), "branchen": y.get("branchen", {}), "top": y.get("top", [])[:10],
                "inflation": bool(e.get("inflation")), "kursersatz": e.get("kursersatz"), "euro": bool(e.get("euro")),
                "groesse": y.get("groesse"), "auflage": y.get("auflage"), "ausschuettungsrendite": y.get("ausschuettungsrendite"),
                "top10": sum(h["anteil"] for h in y.get("top", [])[:10]) if y.get("top") else None}

    # Kurse (Euro, Wochen); für junge Fonds der Kursersatz mit demselben Index
    kurs_ticker = {t: bekannt[t].get("kursersatz") or t for t in alle}
    start = (date.today() - timedelta(days=365 * JAHRE + 14)).replace(day=1).isoformat()
    kurse = euro_kurse(sorted(set(kurs_ticker.values())), start=start)
    woche = pd.DataFrame({t: kurse[k].resample("W-FRI").last() for t, k in kurs_ticker.items() if k in kurse}).pct_change(fill_method=None).iloc[1:]
    woche = woche.where(woche.abs() < 0.5)
    hat_kurs = {t for t in woche if woche[t].notna().sum() >= MIN_WOCHEN}

    # Der eigene ETF-Teil
    teil_wert = sum(p["wert_eur"] for p in eigene)
    meine = []
    for p in eigene:
        s = steckbrief(p["ticker"])
        s.update({"wert": p["wert_eur"], "anteil": p["wert_eur"] / teil_wert * 100,
                  "kosten_jahr": p["wert_eur"] * (s["ter"] or 0) / 100, "position": p["name"]})
        s["vol"] = float(woche[p["ticker"]].std() * np.sqrt(52) * 100) if p["ticker"] in hat_kurs else None
        meine.append(s)
    mit = [m for m in meine if m["ticker"] in hat_kurs]
    gew = np.array([m["wert"] for m in mit])
    gew = gew / gew.sum() if len(gew) else gew
    teil_rendite = woche[[m["ticker"] for m in mit]].fillna(0.0).to_numpy() @ gew if mit else None
    teil_var = float(np.var(teil_rendite, ddof=1) * 52) if teil_rendite is not None else None

    def r2(a: str, b: str):
        if a not in hat_kurs or b not in hat_kurs:
            return None
        paar = woche[[a, b]].dropna()
        return float(paar.corr().iloc[0, 1] ** 2 * 100) if len(paar) >= MIN_WOCHEN else None

    # Doppelungen: durch welche Länder und welche Firmen
    paare = []
    for i, a in enumerate(meine):
        for b in meine[i + 1:]:
            if a["klasse"] != b["klasse"] or a["klasse"] not in ("aktien", "anleihen"):
                continue
            ea, eb = bekannt[a["ticker"]], bekannt[b["ticker"]]
            topb = {h["symbol"]: h for h in b["top"]}
            firmen = [{"name": h["name"], "a": h["anteil"], "b": topb[h["symbol"]]["anteil"]} for h in a["top"] if h["symbol"] in topb]
            anteil = ueberschneidung(ea, eb)
            paare.append({"a": a["ticker"], "b": b["ticker"], "geschaetzt": anteil * 100, "gemessen": r2(a["ticker"], b["ticker"]),
                          "regionen": gemeinsame_regionen(ea, eb), "faktor": _segment_faktor(ea, eb), "firmen": firmen,
                          "eur": min(a["wert"], b["wert"]) * anteil,
                          "kursersatz": [m for m in (a["kursersatz"], b["kursersatz"]) if m]})

    # Kandidaten mit allen Zutaten für den Finder
    kand = []
    for t in kandidaten:
        s = steckbrief(t)
        s["schon_drin"] = sum(m["wert"] / teil_wert * ueberschneidung(bekannt[t], bekannt[m["ticker"]]) for m in meine) * 100
        if t in hat_kurs and teil_rendite is not None:
            k = woche[t].fillna(0.0).to_numpy()
            s["var"] = float(np.var(k, ddof=1) * 52)
            s["kov"] = float(np.cov(teil_rendite, k)[0, 1] * 52)
            s["korrelation"] = float(np.corrcoef(teil_rendite, k)[0, 1])
            s["vol"] = float(np.sqrt(s["var"]) * 100)
            s.update(_verlauf(woche[t]))
        kand.append(s)

    return {
        "teil_wert": teil_wert, "teil_var": teil_var,
        "meine": meine, "paare": sorted(paare, key=lambda x: -x["geschaetzt"]), "kandidaten": kand,
        "welt": welt, "regionen_namen": REGIONEN, "unbekannte_etfs": unbekannt,
        "einzelaktien_wert": sum(p["wert_eur"] for p in pos if p["ticker"] not in bekannt),
        "zeitraum_jahre": JAHRE,
    }
