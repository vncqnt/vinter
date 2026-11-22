"""Welche Positionen schwanken gerade ungewöhnlich stark, gemessen an ihrem eigenen Normalwert?

Je Position: realisierte Vola der letzten 20 Tage gegen den Median der 20-Tage-Vola im letzten Jahr
(Verhältnis) und das Perzentil in den letzten 3 Jahren. Auffällig ab 1,5-fach oder 90. Perzentil,
ruhig unter 0,7-fach. Jede Aktie wird an sich selbst gemessen, nicht an anderen (Bitcoin ist immer
wilder als ein Versorger). Gerechnet in der Handelswährung des Werts.
Nebenbei werden die US-Werte für die eigene IV-Historie vorgemerkt (abends eine Fläche).
"""
from concurrent.futures import ThreadPoolExecutor

from ..benutzer import Benutzer
from ..markt.schwankung import analyse


def rangliste(b: Benutzer, positionen: list[dict]) -> dict:
    pos = [p for p in positionen if p.get("ticker") and (p.get("wert_eur") or p.get("extra"))]
    if not pos:
        return {"fehler": "Keine Positionen mit Kurs gefunden."}
    gesamt = sum(p["wert_eur"] or 0 for p in pos if not p.get("extra")) or 1
    with ThreadPoolExecutor(max_workers=6) as pool:
        ergebnisse = dict(zip([p["ticker"] for p in pos], pool.map(lambda p: analyse(p["ticker"], mit_iv=False), pos)))
    zeilen, ohne = [], []
    for p in pos:
        a = ergebnisse[p["ticker"]]
        k20 = next((k for k in a.get("kegel", []) if k["fenster"] == 20), None)
        if a.get("fehler") or not k20 or not a["rv"].get(20):
            ohne.append(p["name"])
            continue
        jahr = [v for v in a["verlauf"]["rv20"] if v is not None]
        median = sorted(jahr)[len(jahr) // 2] if jahr else None
        verhaeltnis = a["rv"][20] / median if median else None
        stufe = ("unruhig" if (verhaeltnis or 0) >= 1.5 or k20["perzentil"] >= 90
                 else "ruhig" if (verhaeltnis or 1) < 0.7 else "normal")
        zeilen.append({
            "name": p["name"], "ticker": p["ticker"], "extra": bool(p.get("extra")),
            "gewicht": None if p.get("extra") else (p["wert_eur"] or 0) / gesamt * 100,
            "rv20": a["rv"][20], "rv252": a["rv"][252], "median_jahr": median, "verhaeltnis": verhaeltnis,
            "perzentil": k20["perzentil"], "stufe": stufe, "atr_prozent": a["atr_prozent"],
            "verlauf": a["verlauf"]["rv20"][-60:],
        })
    zeilen.sort(key=lambda z: -(z["verhaeltnis"] or 0))
    try:
        from ..markt.volaflaeche import vormerken
        vormerken([z["ticker"] for z in zeilen if not z["extra"] and "." not in z["ticker"] and "-" not in z["ticker"]])
    except Exception:
        pass
    return {"positionen": zeilen, "ohne": ohne}
