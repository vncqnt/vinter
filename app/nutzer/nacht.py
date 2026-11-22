"""Punkt 32 für das eigene Depot: Über Nacht gegen im Handel für Leitindizes und die eigenen Aktien."""
from ..markt.nacht import DIVERGENZ_PP, DIVERGENZ_TAGE, INDIZES, kennzahlen, tagesreihen

ZEITRAEUME = {"1M": 21, "3M": 63, "6M": 126, "1J": 252, "3J": 756}


def nacht_seite(positionen: list[dict]) -> dict:
    eigene = [p for p in sorted(positionen, key=lambda p: -(p.get("wert_eur") or 0))
              if p.get("ticker") and not p.get("verkauft") and not str(p["ticker"]).endswith(("-USD", "-EUR")) and p.get("art", "STOCK") in ("STOCK", None)]
    werte = [{**i, "gruppe": "index"} for i in INDIZES] + \
            [{"ticker": p["ticker"], "name": p.get("name") or p["ticker"], "gruppe": "markt" if p.get("extra") else "depot", "wert_eur": p.get("wert_eur")}
             for p in eigene if p["ticker"] not in {i["ticker"] for i in INDIZES}]
    reihen = tagesreihen([w["ticker"] for w in werte])
    zeilen, ohne = [], []
    for w in werte:
        r = reihen.get(w["ticker"])
        if not r:
            ohne.append(w["name"])
            continue
        zeilen.append({**w, "daten": r["daten"], "nacht_r": r["nacht"], "handel_r": r["handel"],
                       "echte_eroeffnung": r["echte_eroeffnung"],
                       "zeitraeume": {k: kennzahlen(r, t) for k, t in ZEITRAEUME.items()}})
    divergenzen = [z for z in zeilen if z["zeitraeume"]["1M"]["divergenz"]]
    return {"zeilen": zeilen, "ohne_daten": ohne, "zeitraeume": list(ZEITRAEUME), "divergenz_tage": DIVERGENZ_TAGE,
            "divergenz_pp": DIVERGENZ_PP, "divergenzen": [{"ticker": z["ticker"], "name": z["name"], **{k: z["zeitraeume"]["1M"][k] for k in ("nacht20", "handel20", "divergenz")}} for z in divergenzen]}
