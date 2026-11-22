"""Bezug der Seiten rund um "Großes Geld" zu meinem Depot: welche meiner Aktien Fonds halten,
was Insider bei meinen Firmen tun und wie viel Short-Volumen auf meinen Werten liegt.

Nur US-Aktien: Die Meldepflichten (SEC, FINRA) gelten in den USA. Die US-Wertpapierkennnummer (CUSIP)
steckt in der ISIN: US + 9 Zeichen CUSIP + Prüfziffer.
"""
from concurrent.futures import ThreadPoolExecutor

from ..markt.sec_fonds import fonds, grossaktionaere, insider
from ..markt.signale import short_quote


def us_aktien(positionen: list[dict]) -> list[dict]:
    aus = [p for p in positionen if p.get("art") == "STOCK" and p.get("ticker")
           and (str(p.get("isin") or "").startswith("US") or (p.get("extra") and "." not in p["ticker"] and "^" not in p["ticker"] and "=" not in p["ticker"]))]
    return sorted(aus, key=lambda p: -(p.get("wert_eur") or 0))


def andere(positionen: list[dict]) -> list[str]:
    return [p["name"] for p in positionen if p.get("art") == "STOCK" and not p.get("extra") and not str(p.get("isin") or "").startswith("US")]


def fonds_seite(positionen: list[dict]) -> dict:
    meine = us_aktien(positionen)
    # 13F-Meldungen nennen nur die CUSIP (steckt in der ISIN); Werte aus dem Markt haben keine ISIN,
    # bei ihnen gibt es deshalb nur Insider und Großaktionäre
    cusip = {p["isin"][2:11]: p for p in meine if p.get("isin")}
    d = fonds(list(cusip))
    d["meine"] = [{"ticker": p["ticker"], "name": p["name"], "halter": d["meine"].get(c, [])} for c, p in cusip.items()]

    def je_wert(p):
        aus = {"ticker": p["ticker"], "name": p["name"], "extra": bool(p.get("extra"))}
        try:
            aus["insider"] = insider(p["ticker"])
        except Exception as f:
            aus["insider"] = {"fehler": f"Keine Form-4-Daten ({type(f).__name__})."}
        try:
            aus["gross"] = grossaktionaere(p["ticker"])
        except Exception:
            aus["gross"] = []
        return aus
    with ThreadPoolExecutor(2) as pool:
        d["firmen"] = list(pool.map(je_wert, meine))
    d["ohne_us"] = andere(positionen)
    return d


def short_meine(positionen: list[dict]) -> dict:
    meine = us_aktien(positionen)
    namen = {p["ticker"]: p["name"] for p in meine}
    werte = short_quote(list(namen))
    extra = {p["ticker"] for p in meine if p.get("extra")}
    for w in werte:
        w["name"] = namen.get(w["ticker"], w["ticker"])
        w["extra"] = w["ticker"] in extra
    return {"werte": werte, "ohne_us": andere(positionen)}
