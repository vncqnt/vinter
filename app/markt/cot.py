"""CFTC Commitments of Traders: Wie große Gruppen in Terminkontrakten positioniert sind.

Die US-Aufsicht CFTC veröffentlicht jeden Freitag (Stand Dienstag) die offenen Positionen je
Händlergruppe. Kostenlos über publicreporting.cftc.gov.
- Finanz-Terminkontrakte (Bericht "Traders in Financial Futures"): Vermögensverwalter (Pensionskassen,
  Fonds), Hedgefonds ("Leveraged Funds") und Händler/Banken ("Dealer").
- Rohstoffe (Bericht "Disaggregated"): Spekulanten ("Managed Money", meist Hedgefonds/CTAs) und
  Produzenten/Verarbeiter.
Netto = Long minus Short in Kontrakten. Der COT-Index sagt, wo die Netto-Position im Bereich der letzten
3 Jahre liegt: 0 = so negativ wie nie, 100 = so positiv wie nie.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd

from .abruf import STUNDE, gecacht, hole, schlusskurse

TFF, DISAGG = "gpe5-46if", "72hh-3qpy"
MAERKTE = [
    {"id": "spx", "kurs": "ES=F", "name": "S&P 500", "satz": TFF, "code": "13874+"},
    {"id": "ndx", "kurs": "NQ=F", "name": "Nasdaq 100", "satz": TFF, "code": "20974+"},
    {"id": "ust10", "kurs": "ZN=F", "name": "US-Staatsanleihen 10 J.", "satz": TFF, "code": "043602"},
    {"id": "ust2", "kurs": "ZT=F", "name": "US-Staatsanleihen 2 J.", "satz": TFF, "code": "042601"},
    {"id": "usd", "kurs": "DX-Y.NYB", "name": "Dollar-Index", "satz": TFF, "code": "098662"},
    {"id": "eur", "kurs": "EURUSD=X", "name": "Euro", "satz": TFF, "code": "099741"},
    {"id": "vix", "kurs": "^VIX", "name": "VIX", "satz": TFF, "code": "1170E1"},
    {"id": "btc", "kurs": "BTC-USD", "name": "Bitcoin", "satz": TFF, "code": "133741"},
    {"id": "oel", "kurs": "CL=F", "name": "Öl (WTI)", "satz": DISAGG, "code": "067651"},
    {"id": "gold", "kurs": "GC=F", "name": "Gold", "satz": DISAGG, "code": "088691"},
    {"id": "kupfer", "kurs": "HG=F", "name": "Kupfer", "satz": DISAGG, "code": "085692"},
]
GRUPPEN = {
    TFF: [("asset", "Vermögensverwalter", "asset_mgr_positions_long", "asset_mgr_positions_short"),
          ("lev", "Hedgefonds", "lev_money_positions_long", "lev_money_positions_short"),
          ("dealer", "Händler & Banken", "dealer_positions_long_all", "dealer_positions_short_all")],
    DISAGG: [("mm", "Spekulanten (Managed Money)", "m_money_positions_long_all", "m_money_positions_short_all"),
             ("prod", "Produzenten", "prod_merc_positions_long", "prod_merc_positions_short")],
}
JAHRE = 10
FENSTER = 156   # COT-Index über 3 Jahre (156 Wochen)


def _markt(m: dict) -> dict:
    def holen():
        felder = ["report_date_as_yyyy_mm_dd", "open_interest_all"] + [f for g in GRUPPEN[m["satz"]] for f in g[2:]]
        ab = (date.today() - timedelta(days=365 * JAHRE)).isoformat()
        url = (f"https://publicreporting.cftc.gov/resource/{m['satz']}.json?$select={','.join(felder)}"
               f"&$where=cftc_contract_market_code='{m['code'].replace('+', '%2B')}' AND report_date_as_yyyy_mm_dd>'{ab}'"
               f"&$order=report_date_as_yyyy_mm_dd&$limit=2000")
        zeilen = hole(url).json()
        if not zeilen:
            raise ValueError("keine Daten")
        daten = [z["report_date_as_yyyy_mm_dd"][:10] for z in zeilen]
        gruppen = []
        for gid, name, lang, kurz in GRUPPEN[m["satz"]]:
            netto = [int(float(z.get(lang) or 0)) - int(float(z.get(kurz) or 0)) for z in zeilen]
            fenster = netto[-156:]
            tief, hoch = min(fenster), max(fenster)
            index = round((netto[-1] - tief) / (hoch - tief) * 100) if hoch > tief else 50
            oi = float(zeilen[-1].get("open_interest_all") or 0)
            gruppen.append({"id": gid, "name": name, "netto": netto, "index": index,
                            "woche": netto[-1] - netto[-2] if len(netto) > 1 else 0,
                            "anteil_oi": round(netto[-1] / oi * 100, 1) if oi else None})
        return {"daten": daten, "gruppen": gruppen}
    return gecacht(f"cot2:{m['code']}", 12 * STUNDE, holen)


def _auswerten(m: dict, roh: dict) -> dict:
    """COT-Index als Verlauf, Kurs des Markts zu jedem Stichtag und was danach geschah."""
    tage = pd.to_datetime(roh["daten"])
    try:
        k = schlusskurse(m["kurs"], "10y")
        kurs = k.reindex(k.index.union(tage)).ffill().reindex(tage)
    except Exception:
        kurs = pd.Series([None] * len(tage), index=tage, dtype=float)
    folge = (kurs.shift(-12) / kurs - 1) * 100       # Kursänderung der folgenden 12 Wochen
    gruppen = []
    for g in roh["gruppen"]:
        netto = pd.Series(g["netto"], index=tage, dtype=float)
        tief = netto.rolling(FENSTER, min_periods=52).min()
        hoch = netto.rolling(FENSTER, min_periods=52).max()
        index = ((netto - tief) / (hoch - tief) * 100).where(hoch > tief)
        statistik = None
        if kurs.notna().sum() > 60:
            paare = pd.concat([index, folge], axis=1, keys=["i", "f"]).dropna()
            def teil(maske):
                x = paare.loc[maske, "f"]
                return {"anzahl": int(len(x)), "schnitt": round(float(x.mean()), 1) if len(x) else None,
                        "steigt": round(float((x > 0).mean() * 100)) if len(x) else None}
            statistik = {"hoch": teil(paare["i"] >= 85), "tief": teil(paare["i"] <= 15), "alle": teil(paare["i"].notna())}
        gruppen.append({**g, "index_verlauf": [None if pd.isna(v) else round(float(v)) for v in index], "statistik": statistik})
    return {"daten": roh["daten"], "gruppen": gruppen, "kurs": [None if pd.isna(v) else round(float(v), 4) for v in kurs]}


def cot_seite() -> dict:
    def sicher(m):
        try:
            return {**{k: m[k] for k in ("id", "name")}, **_auswerten(m, _markt(m))}
        except Exception as f:
            return {"id": m["id"], "name": m["name"], "fehler": f"CFTC gerade nicht erreichbar ({type(f).__name__})."}
    with ThreadPoolExecutor(4) as pool:
        maerkte = list(pool.map(sicher, MAERKTE))
    stand = max((m["daten"][-1] for m in maerkte if "daten" in m), default=None)
    return {"maerkte": maerkte, "stand": stand}
