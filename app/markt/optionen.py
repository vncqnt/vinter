"""Optionen, Short und Spread einer Aktie (Punkt 24): Put/Call, Open Interest, Max Pain, auffällige Aktivität,
Geld/Brief mit Spread, Short Interest mit Days to Cover.

Quelle Yahoo (verzögert). Optionen nur für US-Werte. Volumen ist das des letzten Handelstags, Open Interest
wird von der Börse einmal täglich (morgens, Stand Vortag) gemeldet. Short Interest melden die US-Börsen
zweimal im Monat (Stand gut eine Woche vorher). Information, kein Signal.

Max Pain: der Kurs am Verfallstag, bei dem alle offenen Optionen zusammen am wenigsten wert wären (für die
Käufer). Manche glauben, der Kurs werde dorthin "gezogen"; belegt ist das kaum, deshalb nur als Information.
Auffällig: Kontrakte, deren Tagesvolumen das Open Interest übersteigt (es wurden mehr neu gehandelt, als
bisher offen waren) und mindestens MIN_VOLUMEN Kontrakte beträgt.
"""
import math
from datetime import date, datetime, timezone

import pandas as pd
import yfinance as yf

from ..cache import markt_lesen, markt_schreiben

MAX_TAGE = 45          # Verfälle bis so viele Tage voraus
MAX_VERFAELLE = 8
MIN_VOLUMEN = 500
SPANNE = 0.30          # Open Interest je Strike: ±30 % um den Kurs
CACHE_S = 30 * 60


# ---- Reine Rechnungen (getestet in tests/test_optionen.py) ----

def max_pain(calls: pd.DataFrame, puts: pd.DataFrame) -> float | None:
    """Strike, an dem der Gesamtwert aller offenen Calls und Puts am Verfall am kleinsten ist."""
    c = calls[["strike", "openInterest"]].fillna(0)
    p = puts[["strike", "openInterest"]].fillna(0)
    strikes = sorted(set(c["strike"]) | set(p["strike"]))
    if not strikes or (c["openInterest"].sum() + p["openInterest"].sum()) == 0:
        return None
    def wert(s):
        return float(((s - c["strike"]).clip(lower=0) * c["openInterest"]).sum() + ((p["strike"] - s).clip(lower=0) * p["openInterest"]).sum())
    return min(strikes, key=wert)


def spread(bid, ask, kurs, bid_groesse=None, ask_groesse=None) -> dict:
    """Geld/Brief mit Spread. Nicht aussagekräftig, wenn eine Seite fehlt, die Mitte mehr als 3 % vom Kurs weg
    liegt (alte Quotes außerhalb der Handelszeit) oder der Spread über 5 % beträgt."""
    if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
        return {"gueltig": False, "grund": "keine Geld- und Briefkurse"}
    mitte = (bid + ask) / 2
    proz = (ask - bid) / mitte * 100
    gueltig = (not kurs or abs(mitte / kurs - 1) <= 0.03) and proz <= 5
    return {"geld": bid, "brief": ask, "geld_stueck": bid_groesse, "brief_stueck": ask_groesse, "spread": ask - bid, "spread_prozent": proz,
            "gueltig": gueltig, "grund": None if gueltig else "außerhalb der Handelszeit oder alte Quotes, nicht aussagekräftig"}


def auffaellig(kette: pd.DataFrame, art: str, verfall: str) -> list[dict]:
    k = kette.fillna({"volume": 0, "openInterest": 0, "lastPrice": 0})
    k = k[(k["volume"] >= MIN_VOLUMEN) & (k["volume"] > k["openInterest"])]
    return [{"art": art, "verfall": verfall, "strike": float(z.strike), "volumen": int(z.volume), "oi": int(z.openInterest),
             "verhaeltnis": float(z.volume / z.openInterest) if z.openInterest else None,
             "praemie": float(z.volume * z.lastPrice * 100), "iv": float(z.impliedVolatility * 100) if not math.isnan(z.impliedVolatility) else None}
            for z in k.itertuples()]


# ---- Abruf ----

def _datum(sekunden):
    try:
        return datetime.fromtimestamp(int(sekunden), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def analyse(ticker: str) -> dict:
    ticker = ticker.strip().upper()
    schluessel = f"optionen:{ticker}"
    alt, alter = markt_lesen(schluessel)
    if alt and alter < CACHE_S:
        return alt
    t = yf.Ticker(ticker)
    try:
        info = t.info or {}
    except Exception:
        info = {}
    kurs = info.get("regularMarketPrice") or info.get("currentPrice")
    aus = {"ticker": ticker, "kurs": kurs, "waehrung": info.get("currency"), "stand": datetime.now(timezone.utc).isoformat(timespec="minutes"),
           "spread": spread(info.get("bid"), info.get("ask"), kurs, info.get("bidSize"), info.get("askSize"))}

    # Short Interest (nur US): Days to Cover = Short-Aktien / Durchschnittsvolumen
    if info.get("sharesShort"):
        vorher = info.get("sharesShortPriorMonth")
        aus["short"] = {
            "aktien": info["sharesShort"], "vormonat": vorher,
            "veraenderung": (info["sharesShort"] / vorher - 1) * 100 if vorher else None,
            "anteil_float": info["shortPercentOfFloat"] * 100 if info.get("shortPercentOfFloat") is not None else None,
            "days_to_cover": info.get("shortRatio"),
            "days_to_cover_10t": info["sharesShort"] / info["averageDailyVolume10Day"] if info.get("averageDailyVolume10Day") else None,
            "stand": _datum(info.get("dateShortInterest")), "stand_vormonat": _datum(info.get("sharesShortPreviousMonthDate")),
        }

    # Optionen (nur US-Werte mit Optionen bei Yahoo)
    if "." not in ticker:
        try:
            verfaelle = list(t.options or [])
        except Exception:
            verfaelle = []
        heute = date.today()
        nah = [v for v in verfaelle if 0 <= (date.fromisoformat(v) - heute).days <= MAX_TAGE][:MAX_VERFAELLE] or verfaelle[:2]
        je_verfall, alle_auffaellig, summe = [], [], {"call_vol": 0, "put_vol": 0, "call_oi": 0, "put_oi": 0}
        oi_strikes = None
        for v in nah:
            try:
                kette = t.option_chain(v)
            except Exception:
                continue
            c, p = kette.calls, kette.puts
            werte = {"call_vol": float(c["volume"].fillna(0).sum()), "put_vol": float(p["volume"].fillna(0).sum()),
                     "call_oi": float(c["openInterest"].fillna(0).sum()), "put_oi": float(p["openInterest"].fillna(0).sum())}
            for k in summe:
                summe[k] += werte[k]
            je_verfall.append({"verfall": v, "tage": (date.fromisoformat(v) - heute).days, **werte,
                               "pcr_vol": werte["put_vol"] / werte["call_vol"] if werte["call_vol"] else None,
                               "pcr_oi": werte["put_oi"] / werte["call_oi"] if werte["call_oi"] else None,
                               "max_pain": max_pain(c, p)})
            alle_auffaellig += auffaellig(c, "Call", v) + auffaellig(p, "Put", v)
            # Open Interest je Strike für den Verfall mit dem meisten offenen Interesse
            gesamt_oi = werte["call_oi"] + werte["put_oi"]
            if kurs and (oi_strikes is None or gesamt_oi > oi_strikes["gesamt"]):
                lo, hi = kurs * (1 - SPANNE), kurs * (1 + SPANNE)
                cs = c[(c["strike"] >= lo) & (c["strike"] <= hi)].set_index("strike")["openInterest"].fillna(0)
                ps = p[(p["strike"] >= lo) & (p["strike"] <= hi)].set_index("strike")["openInterest"].fillna(0)
                strikes = sorted(set(cs.index) | set(ps.index))
                oi_strikes = {"verfall": v, "gesamt": gesamt_oi, "strikes": strikes,
                              "calls": [float(cs.get(s, 0)) for s in strikes], "puts": [float(ps.get(s, 0)) for s in strikes],
                              "max_pain": je_verfall[-1]["max_pain"]}
        if je_verfall:
            aus["optionen"] = {
                "verfaelle": je_verfall, "summe": summe,
                "pcr_vol": summe["put_vol"] / summe["call_vol"] if summe["call_vol"] else None,
                "pcr_oi": summe["put_oi"] / summe["call_oi"] if summe["call_oi"] else None,
                "oi_strikes": oi_strikes,
                "auffaellig": sorted(alle_auffaellig, key=lambda x: -x["praemie"])[:12],
                "min_volumen": MIN_VOLUMEN, "max_tage": MAX_TAGE,
            }
    markt_schreiben(schluessel, aus)
    return aus
