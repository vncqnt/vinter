"""Seite "Positionierung": Schätzungen, wie große, regelbasierte Anleger gerade stehen.

Alles selbst gerechnet aus öffentlichen Kursen und Optionsdaten, deshalb Schätzungen:
- trendfolger(): Trendfolge-Fonds (CTAs) kaufen, wenn der Kurs über dem von vor 1, 3, 6 und 12 Monaten
  liegt, und verkaufen darunter. Dazu die Kurse, an denen diese Signale kippen würden.
- vola_fonds(): Fonds mit Volatilitätssteuerung halten mehr Aktien, wenn der Markt ruhig ist
  (Ziel-Schwankung 10 % geteilt durch die gemessene Schwankung, höchstens 150 %).
- optionen(): Put/Call-Verhältnis und Dealer-Gamma beim SPY aus den Optionsketten von Yahoo.
- rebalancing(): Wie weit ein 60/40-Depot (Aktien/Anleihen) seit Monats- und Quartalsbeginn abgedriftet
  ist. Große Pensionskassen bringen es zum Periodenende zurück.
- rueckkauf_sperre(): Firmen dürfen in den Wochen vor ihren Zahlen keine eigenen Aktien zurückkaufen.
  Anteil des S&P 500 (nach Börsenwert), der gerade gesperrt ist.
"""
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf

from ..cache import markt_lesen
from .abruf import STUNDE, TAG, als_chart, gecacht, hole, historie_eintragen, historie_lesen, schlusskurse
from .indizes import mitglieder

LOOKBACKS = [(21, "1 Monat"), (63, "3 Monate"), (126, "6 Monate"), (252, "12 Monate")]
ZIEL_VOLA = 0.10
ZINS = 0.04


def trendfolger() -> dict:
    p = schlusskurse("SPY", "max")
    r = np.log(p).diff()
    vol = r.rolling(60).std()
    signale = []
    for tage, _ in LOOKBACKS:
        s = (np.log(p) - np.log(p.shift(tage))) / (vol * math.sqrt(tage))
        signale.append(s.clip(-1, 1))
    roh = sum(signale) / len(signale)
    jahres_vola = vol * math.sqrt(252)
    expo = (roh * (0.15 / jahres_vola).clip(upper=1.5)).dropna()
    expo = expo[expo.index >= expo.index[-1] - pd.Timedelta(days=3 * 365)]
    letzter = float(p.iloc[-1])
    kipp = []
    for (tage, name), s in zip(LOOKBACKS, signale):
        # Morgen vergleicht das Signal mit dem Kurs von vor (tage - 1) Handelstagen
        stufe = float(p.iloc[-tage]) if len(p) > tage else None
        kipp.append({"name": name, "kurs": round(stufe, 2) if stufe else None, "abstand": round((stufe / letzter - 1) * 100, 2) if stufe else None,
                     "signal": round(float(s.iloc[-1]), 2), "richtung": "kaufen" if letzter > (stufe or 0) else "verkaufen"})
    chart = als_chart({"expo": expo * 100, "spy": p.reindex(expo.index)}, runden=2)
    return {**chart, "aktuell": round(float(expo.iloc[-1]) * 100), "vor_woche": round(float(expo.iloc[-6]) * 100), "kurs": round(letzter, 2), "kipp": kipp}


def vola_fonds() -> dict:
    p = schlusskurse("SPY", "max")
    r = np.log(p).diff()
    rv20 = r.rolling(20).std() * math.sqrt(252)
    rv60 = r.rolling(60).std() * math.sqrt(252)
    expo = (ZIEL_VOLA / pd.concat([rv20, rv60], axis=1).max(axis=1)).clip(upper=1.5).dropna()
    expo = expo[expo.index >= expo.index[-1] - pd.Timedelta(days=3 * 365)]
    chart = als_chart({"expo": expo * 100, "spy": p.reindex(expo.index)}, runden=2)
    return {**chart, "aktuell": round(float(expo.iloc[-1]) * 100), "vor_monat": round(float(expo.iloc[-22]) * 100),
            "vola20": round(float(rv20.iloc[-1]) * 100, 1), "vola60": round(float(rv60.iloc[-1]) * 100, 1)}


def _gamma(s, k, t, sigma):
    if t <= 0 or sigma <= 0 or k <= 0:
        return 0.0
    d1 = (math.log(s / k) + (ZINS + sigma * sigma / 2) * t) / (sigma * math.sqrt(t))
    return math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi) / (s * sigma * math.sqrt(t))


def _ketten(ticker: str) -> dict:
    def holen():
        t = yf.Ticker(ticker)
        kurs = float(t.history(period="5d")["Close"].dropna().iloc[-1])
        heute = date.today()
        zeilen = []
        for verfall in t.options:
            tage = (date.fromisoformat(verfall) - heute).days
            if tage < 0:
                continue
            if tage > 45:
                break
            kette = t.option_chain(verfall)
            for art, tab in (("C", kette.calls), ("P", kette.puts)):
                for _, z in tab.iterrows():
                    zeilen.append([verfall, art, float(z["strike"]), float(z.get("openInterest") or 0), float(z.get("volume") or 0) if not pd.isna(z.get("volume")) else 0.0,
                                   float(z.get("impliedVolatility") or 0)])
        return {"kurs": kurs, "zeilen": zeilen, "zeit": datetime.now(timezone.utc).isoformat()}
    return gecacht(f"ketten:{ticker}", 30 * 60, holen)


def optionen(ticker: str = "SPY") -> dict:
    d = _ketten(ticker)
    s = d["kurs"]
    jetzt = datetime.now(timezone.utc)
    tab = pd.DataFrame(d["zeilen"], columns=["verfall", "art", "strike", "oi", "vol", "iv"])
    # Yahoo liefert bei einzelnen Kontrakten kein Open Interest oder Volumen: als 0 zählen (sonst wird die Summe "nan")
    tab[["oi", "vol"]] = tab[["oi", "vol"]].apply(pd.to_numeric, errors="coerce").fillna(0)
    tab["iv"] = pd.to_numeric(tab["iv"], errors="coerce").fillna(0)
    # Vor der US-Eröffnung liefert Yahoo oft unbrauchbare Volatilitäten (0,00001). Dann den Mittelwert
    # der brauchbaren Werte desselben Verfalls nehmen, sonst den VIX.
    gut = (tab["iv"] > 0.03) & (tab["iv"] < 3)
    try:
        vix = float(schlusskurse("^VIX", "1y").iloc[-1]) / 100
    except Exception:
        vix = 0.18
    median = tab[gut].groupby("verfall")["iv"].median()
    tab.loc[~gut, "iv"] = tab.loc[~gut, "verfall"].map(median).fillna(vix)
    ersetzt = round(float((~gut).mean() * 100))
    # Restlaufzeit bis 22 Uhr deutscher Zeit (16 Uhr New York) am Verfallstag
    tab["t"] = [max(0.5 / 365, (datetime.fromisoformat(v).replace(hour=21, tzinfo=timezone.utc) - jetzt).total_seconds() / (365 * 86400)) for v in tab["verfall"]]
    vorzeichen = np.where(tab["art"] == "C", 1.0, -1.0)

    def gex(spot):
        g = np.array([_gamma(spot, k, t, iv) for k, t, iv in zip(tab["strike"], tab["t"], tab["iv"])])
        return g * tab["oi"].to_numpy() * 100 * spot * spot * 0.01 * vorzeichen

    jetzt_gex = gex(s)
    tab["gex"] = jetzt_gex
    nah = tab[(tab["strike"] > s * 0.93) & (tab["strike"] < s * 1.07)]
    je_strike = nah.groupby("strike")["gex"].sum()
    # Kurs, an dem das Gesamt-Gamma das Vorzeichen wechselt ("Gamma-Flip")
    gitter = np.linspace(s * 0.9, s * 1.1, 41)
    summen = [gex(x).sum() for x in gitter]
    flip = None
    for (a, ga), (b, gb) in zip(zip(gitter, summen), zip(gitter[1:], summen[1:])):
        if ga == 0 or (ga < 0) != (gb < 0):
            kand = a + (b - a) * (abs(ga) / (abs(ga) + abs(gb)))
            if flip is None or abs(kand - s) < abs(flip - s):
                flip = kand
    calls, puts = tab[tab["art"] == "C"], tab[tab["art"] == "P"]
    walls = waende(tab, s)
    pcr_vol = round(float(puts["vol"].sum() / calls["vol"].sum()), 2) if calls["vol"].sum() else None
    pcr_oi = round(float(puts["oi"].sum() / calls["oi"].sum()), 2) if calls["oi"].sum() else None
    gesamt = round(float(jetzt_gex.sum()) / 1e9, 2)
    heute = date.today().isoformat()
    historie_eintragen(f"optionen:{ticker}", heute, {"pcr_vol": pcr_vol, "pcr_oi": pcr_oi, "gex": gesamt})
    verlauf = historie_lesen(f"optionen:{ticker}")
    tage = sorted(verlauf)
    return {"ticker": ticker, "kurs": round(s, 2), "gex": gesamt, "flip": round(float(flip), 2) if flip else None,
            "strikes": [round(float(k), 1) for k in je_strike.index], "gex_strike": [round(float(v) / 1e9, 3) for v in je_strike.values],
            "pcr_vol": pcr_vol, "pcr_oi": pcr_oi, "zeit": d["zeit"], "iv_ersetzt": ersetzt, **walls,
            "verlauf": {"daten": tage, **{k: [_zahl(verlauf[t].get(k)) for t in tage] for k in ("pcr_vol", "pcr_oi", "gex")}}}


def _zahl(x):
    """nan (z. B. aus Tagen vor der Korrektur vom 22.11.2026) als leer ausgeben, sonst lässt sich nichts als JSON senden."""
    return None if x is None or (isinstance(x, float) and x != x) else x


def waende(tab: pd.DataFrame, kurs: float, spanne: float = 0.15) -> dict:
    """Call- und Put-Wall (Roadmap Edge 1): Basispreis mit dem größten Call-Gamma bzw. Put-Gamma (über alle Verfälle
    der nächsten 45 Tage, ±15 % um den Kurs). Die Call-Wall wirkt oft wie ein Deckel, die Put-Wall wie ein Boden,
    weil sich dort die Absicherungen der Händler bündeln. Reine Funktion (Spalten: art, strike, gex)."""
    nah = tab[(tab["strike"] > kurs * (1 - spanne)) & (tab["strike"] < kurs * (1 + spanne))]
    c = nah[nah["art"] == "C"].groupby("strike")["gex"].sum()
    p = nah[nah["art"] == "P"].groupby("strike")["gex"].sum()
    return {"call_wall": float(c.idxmax()) if not c.empty and c.max() > 0 else None,
            "put_wall": float(p.idxmin()) if not p.empty and p.min() < 0 else None}


def rebalancing() -> dict:
    aktien, anleihen = schlusskurse("SPY", "max"), schlusskurse("AGG", "max")
    tab = pd.concat([aktien, anleihen], axis=1, keys=["a", "b"]).dropna()
    tab = tab[tab.index >= tab.index[-1] - pd.Timedelta(days=4 * 365)]

    def drift(von, bis):
        ra = tab["a"].loc[bis] / tab["a"].loc[von] - 1
        rb = tab["b"].loc[bis] / tab["b"].loc[von] - 1
        gewicht = 0.6 * (1 + ra) / (0.6 * (1 + ra) + 0.4 * (1 + rb))
        return round(float((gewicht - 0.6) * 100), 2), round(float(ra * 100), 2), round(float(rb * 100), 2)

    letzter = tab.index[-1]
    perioden = []
    for name, freq in (("Monat", "M"), ("Quartal", "Q")):
        anfang = tab.index[tab.index < letzter.to_period(freq).start_time]
        if len(anfang):
            d, ra, rb = drift(anfang[-1], letzter)
            ende = letzter.to_period(freq).end_time.date()
            perioden.append({"name": name, "drift": d, "aktien": ra, "anleihen": rb, "ende": ende.isoformat(),
                             "richtung": "Aktien verkaufen, Anleihen kaufen" if d > 0 else "Aktien kaufen, Anleihen verkaufen"})
    # Verlauf: Drift am Ende jedes Monats
    monatsenden = tab.groupby(tab.index.to_period("M")).tail(1).index
    verlauf = []
    for vor, nach in zip(monatsenden[:-1], monatsenden[1:]):
        verlauf.append({"monat": nach.strftime("%Y-%m"), "drift": drift(vor, nach)[0]})
    return {"perioden": perioden, "verlauf": verlauf[-36:]}


def _earnings_tag(tag: date) -> list[dict]:
    """Alle Earnings eines Tages vom Kalender von nasdaq.com (mit Börsenwert). Vergangene Tage bleiben gespeichert."""
    def holen():
        d = hole(f"https://api.nasdaq.com/api/calendar/earnings?date={tag.isoformat()}").json()
        zeilen = ((d.get("data") or {}).get("rows")) or []
        aus = []
        for z in zeilen:
            try:
                cap = float(str(z.get("marketCap") or "0").replace("$", "").replace(",", "") or 0)
            except ValueError:
                cap = 0.0
            aus.append({"t": z["symbol"].replace(".", "-"), "cap": cap})
        return aus
    alt = tag < date.today() - timedelta(days=1)
    return gecacht(f"earnings_tag:{tag.isoformat()}", 3650 * TAG if alt else 12 * STUNDE, holen)


def rueckkauf_sperre(vorher: int = 30, nachher: int = 2) -> dict:
    """Je Börsentag: Anteil des S&P 500 nach Börsenwert, dessen Zahlen in den nächsten 30 Tagen kommen
    (oder vor höchstens 2 Tagen kamen). Übliche Sperrfrist, die genauen Regeln legt jede Firma selbst fest."""
    sp = {m["ticker"] for m in mitglieder("sp500")}
    heute = date.today()
    kalender = [heute + timedelta(days=d) for d in range(-35 - nachher, 46 + vorher) if (heute + timedelta(days=d)).weekday() < 5]
    with ThreadPoolExecutor(4) as pool:
        tage_listen = dict(zip(kalender, pool.map(_earnings_tag, kalender)))
    termine, caps = {}, {}
    for tag, zeilen in tage_listen.items():
        for z in zeilen:
            if z["t"] in sp:
                termine.setdefault(z["t"], []).append(tag)
                caps[z["t"]] = max(caps.get(z["t"], 0), z["cap"])
    # Künftige Termine stehen oft noch nicht im Kalender: dann etwa ein Quartal nach dem letzten Termin
    geschaetzt = 0
    for t, ds in termine.items():
        letzter = max(ds)
        if letzter < heute + timedelta(days=60):
            naechster = letzter + timedelta(days=91)
            if not any(d > letzter + timedelta(days=60) for d in ds):
                ds.append(naechster)
                geschaetzt += 1
    kapital = markt_lesen("kennzahlen:sp500")[0] or {}
    gesamt = sum((kapital.get(t) or {}).get("cap") or 0 for t in sp) or sum(caps.values())
    tage, anteil = [], []
    for tag in (heute + timedelta(days=d) for d in range(-35, 46)):
        if tag.weekday() >= 5:
            continue
        drin = sum(caps[t] for t, ds in termine.items() if any(-nachher <= (e - tag).days <= vorher for e in ds))
        tage.append(tag.isoformat())
        anteil.append(round(min(100.0, drin / gesamt * 100), 1) if gesamt else None)
    jetzt = next((a for t, a in zip(tage, anteil) if t >= heute.isoformat()), None)
    return {"daten": tage, "anteil": anteil, "aktuell": jetzt, "vorher": vorher, "abdeckung": len(termine), "geschaetzt": geschaetzt, "heute": heute.isoformat()}


def positionierung_seite() -> dict:
    aus = {}
    for name, f in (("trend", trendfolger), ("vola", vola_fonds), ("optionen", optionen), ("optionen_qqq", lambda: optionen("QQQ")),
                    ("rebalancing", rebalancing), ("sperre", rueckkauf_sperre)):
        try:
            aus[name] = f()
        except Exception as fehler:
            aus[name] = {"fehler": f"Gerade nicht verfügbar ({type(fehler).__name__}: {str(fehler)[:80]})."}
    return aus
