"""Reverse-DCF für meine Aktien: Zutaten je Position. Das Auflösen nach dem Wachstum passiert im
Browser, damit Diskontsatz, Endwachstum und Jahre sofort verstellbar sind.

Normaler DCF: Aus angenommenem Wachstum folgt ein fairer Wert. Reverse-DCF dreht das um: Welches
jährliche Free-Cashflow-Wachstum muss die Firma schaffen, damit der heutige Unternehmenswert
(Börsenwert + Schulden − Cash) genau gerechtfertigt ist?
"""
from concurrent.futures import ThreadPoolExecutor

from ..markt.fundamental import dcf_daten


def _historisch(jahre: list[dict]) -> float | None:
    """Jährliches Wachstum des Free Cashflows über die vorhandenen Jahre (nur wenn Anfang und Ende positiv)."""
    if len(jahre) < 3:
        return None
    a, e, n = jahre[0]["wert"], jahre[-1]["wert"], len(jahre) - 1
    if a <= 0 or e <= 0:
        return None
    return ((e / a) ** (1 / n) - 1) * 100


def zutaten(positionen: list[dict]) -> dict:
    aktien = [p for p in positionen if p.get("art") == "STOCK" and p.get("ticker") and (p.get("wert_eur") or p.get("extra"))]
    if not aktien:
        return {"fehler": "Keine Aktien im Depot gefunden."}
    aktien.sort(key=lambda p: (bool(p.get("extra")), -(p["wert_eur"] or 0)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        daten = list(pool.map(lambda p: dcf_daten(p["ticker"]), aktien))
    zeilen = []
    for p, d in zip(aktien, daten):
        zeile = {"name": p["name"], "ticker": p["ticker"], "wert_eur": p["wert_eur"], "extra": bool(p.get("extra"))}
        if "fehler" in d:
            zeile["fehler"] = d["fehler"]
        else:
            zeile.update(d)
            zeile["wachstum_hist"] = _historisch(d["fcf_jahre"])
        zeilen.append(zeile)
    return {"aktien": zeilen}
