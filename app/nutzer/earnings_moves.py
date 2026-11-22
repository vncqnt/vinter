"""Earnings-Moves für die Aktien im Depot: Reaktionen an den letzten Zahlen und was der
Optionsmarkt für den nächsten Termin erwartet."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from ..markt.earnings_moves import erwartete_bewegung, reaktionen

OPTIONEN_AB_TAGE = 14


def earnings_moves(positionen: list[dict]) -> dict:
    aktien = [p for p in positionen if p.get("art") == "STOCK" and p.get("ticker")]
    if not aktien:
        return {"fehler": "Keine Aktien im Depot gefunden."}
    with ThreadPoolExecutor(max_workers=4) as pool:
        daten = list(pool.map(lambda p: reaktionen(p["ticker"]), aktien))

    zeilen, ohne = [], []
    heute = date.today().isoformat()
    for p, d in zip(aktien, daten):
        liste = d.get("reaktionen") or []
        if not liste:
            ohne.append(p["name"])
            continue
        werte = [r["prozent"] for r in liste]
        naechster = d.get("naechster") if d.get("naechster") and d["naechster"]["datum"] >= heute else None
        zeilen.append({
            "name": p["name"], "ticker": p["ticker"], "wert": p.get("wert_eur"), "extra": bool(p.get("extra")),
            "reaktionen": liste,
            "schnitt_betrag": sum(abs(w) for w in werte) / len(werte),
            "schnitt": sum(werte) / len(werte),
            "max": max(werte, key=abs),
            "hoch": sum(w > 0 for w in werte), "runter": sum(w < 0 for w in werte),
            "naechster": naechster,
        })

    # Optionen nur für Termine in den nächsten zwei Wochen: Weiter vorher steckt im Straddle vor allem
    # normale Schwankung, und der Termin-Anteil lässt sich nicht zuverlässig herausrechnen (getestet).
    def straddle(z):
        n = z["naechster"]
        if not n or (date.fromisoformat(n["datum"]) - date.today()).days > OPTIONEN_AB_TAGE:
            return None
        return erwartete_bewegung(z["ticker"], n["datum"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        for z, e in zip(zeilen, pool.map(straddle, zeilen)):
            z["erwartet"] = e

    zeilen.sort(key=lambda z: (z["naechster"] is None, z["naechster"]["datum"] if z["naechster"] else "", -z["schnitt_betrag"]))
    return {"werte": zeilen, "ohne_daten": ohne, "optionen_ab_tage": OPTIONEN_AB_TAGE}
