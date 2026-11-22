"""Post-Earnings-Drift (Roadmap Q4): Laufen Aktien nach großen Überraschungen in dieselbe Richtung weiter?

Zwei Teile:
1. Tracker (S&P 500, Meldungen der letzten 75 Tage): Überraschung (EPS gegen Erwartung, nasdaq.com) und
   Kursreaktion (Schluss vor dem Termin bis Schluss danach, gegen den gleichgewichteten S&P 500). "Groß" heißt
   Reaktion ab ±5 % oder Überraschung ab ±15 %. Drift = Entwicklung seit dem Schluss nach der Reaktion, gegen
   den Markt, höchstens 60 Handelstage. Vergleich: dieselbe Rechnung für die Meldungen davor (rund 1,5 Jahre).
2. Je Aktie (deine Werte und ihre Peer-Gruppen): Quartalstermine der letzten Jahre (Yahoo, earnings_moves),
   Drift 20/40/60 Handelstage nach der Reaktion gegen SPY (US) bzw. DAX (deutsche Werte). Fortsetzung = Drift
   in Richtung der Reaktion. Ab 8 Terminen eine Einordnung: typisch ab 60 % Fortsetzung, eher Umkehr bis 40 %.
Post-Earnings-Drift ist einer der bekanntesten Effekte der Finanzforschung, bei großen, viel beachteten Werten
aber deutlich schwächer geworden. Information, kein Signal.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .abruf import STUNDE, TAG, gecacht, schlusskurse
from .earnings_barometer import _tag_sicher
from .indizes import mitglieder, sp500_kurse

GROSS_REAKTION = 5.0
GROSS_UEBERRASCHUNG = 15.0
FENSTER = (20, 40, 60)
MIN_TERMINE = 8


def drift_reihe(kurse: pd.Series, markt: pd.Series, ab: pd.Timestamp, fenster=FENSTER) -> dict:
    """Überrendite gegen den Markt ab dem Schluss am Tag `ab` nach n Handelstagen (soweit vorhanden). Reine Funktion."""
    k = kurse.dropna()
    i = k.index.searchsorted(ab)
    if i >= len(k):
        return {}
    basis_t = k.index[i]
    m = markt.reindex(k.index).ffill()
    aus = {}
    for n in fenster:
        j = min(i + n, len(k) - 1)
        if j == i:
            continue
        aus[str(n)] = float((k.iloc[j] / k.iloc[i] - 1) - (m.iloc[j] / m.loc[basis_t] - 1)) * 100
        if i + n >= len(k):
            aus["bisher"] = int(j - i)  # Fenster noch nicht voll
            break
    return aus


def einordnen(termine: list[dict], n: str = "60") -> dict:
    """Fortsetzung, Ø Drift nach positiven und negativen Reaktionen. Reine Funktion."""
    voll = [t for t in termine if t.get("drift", {}).get(n) is not None and "bisher" not in t["drift"] and t.get("reaktion")]
    if not voll:
        return {"anzahl": 0}
    fort = [np.sign(t["drift"][n]) == np.sign(t["reaktion"]) for t in voll]
    pos = [t["drift"][n] for t in voll if t["reaktion"] > 0]
    neg = [t["drift"][n] for t in voll if t["reaktion"] < 0]
    anteil = sum(fort) / len(fort) * 100
    urteil = None
    if len(voll) >= MIN_TERMINE:
        urteil = "typisch" if anteil >= 60 else "eher Umkehr" if anteil <= 40 else "kein klares Muster"
    return {"anzahl": len(voll), "fortsetzung": anteil, "nach_positiv": float(np.mean(pos)) if pos else None,
            "nach_negativ": float(np.mean(neg)) if neg else None, "urteil": urteil}


def tracker() -> dict:
    def holen():
        kurse = sp500_kurse()
        markt = kurse.pct_change(fill_method=None).mean(axis=1).fillna(0).add(1).cumprod()  # gleichgewichtet
        sp = {m["ticker"]: m["name"] for m in mitglieder("sp500")}
        heute = date.today()
        tage = [heute - timedelta(days=d) for d in range(1, 470) if (heute - timedelta(days=d)).weekday() < 5]
        with ThreadPoolExecutor(3) as pool:
            je_tag = dict(zip(tage, pool.map(_tag_sicher, tage)))
        idx = kurse.index
        zeilen = []
        for tag, liste in je_tag.items():
            pos = idx.searchsorted(pd.Timestamp(tag))
            if pos < 1 or pos + 1 >= len(idx):
                continue
            vor, nach = idx[pos - 1], idx[pos + 1]
            m_r = float(markt.loc[nach] / markt.loc[vor] - 1) * 100
            for z in liste:
                t = z["t"]
                if t not in sp or t not in kurse.columns:
                    continue
                a, b = kurse.at[vor, t], kurse.at[nach, t]
                if pd.isna(a) or pd.isna(b) or not a:
                    continue
                r = float(b / a - 1) * 100 - m_r
                u = z["ueberraschung"]
                if abs(r) < GROSS_REAKTION and (u is None or abs(u) < GROSS_UEBERRASCHUNG):
                    continue
                zeilen.append({"ticker": t, "name": sp[t], "tag": tag.isoformat(), "ueberraschung": u, "reaktion": round(r, 2),
                               "drift": drift_reihe(kurse[t], markt, nach)})
        grenze = (heute - timedelta(days=75)).isoformat()
        aktuell = sorted([z for z in zeilen if z["tag"] >= grenze], key=lambda z: -abs(z["reaktion"]))
        frueher = [z for z in zeilen if z["tag"] < grenze]
        # Vergleich: frühere Meldungen mit vollen 60 Tagen, getrennt nach Richtung der Reaktion
        def schnitt(liste, richtung, n="60"):
            w = [z["drift"][n] for z in liste if z["drift"].get(n) is not None and "bisher" not in z["drift"] and np.sign(z["reaktion"]) == richtung]
            return {"anzahl": len(w), "schnitt": float(np.mean(w)) if w else None, "fortsetzung": float(np.mean([np.sign(x) == richtung for x in w]) * 100) if w else None}
        def bisher(liste, richtung):
            w = [list(v for k, v in z["drift"].items() if k != "bisher")[-1] for z in liste if np.sign(z["reaktion"]) == richtung and z["drift"]]
            return {"anzahl": len(w), "schnitt": float(np.mean(w)) if w else None}
        return {"aktuell": aktuell[:60], "frueher": {"positiv": schnitt(frueher, 1), "negativ": schnitt(frueher, -1), "anzahl": len(frueher)},
                "jetzt": {"positiv": bisher(aktuell, 1), "negativ": bisher(aktuell, -1)},
                "regel": {"reaktion": GROSS_REAKTION, "ueberraschung": GROSS_UEBERRASCHUNG}, "stand": heute.isoformat()}
    return gecacht("drift:tracker", 6 * STUNDE, holen)


def je_aktie(ticker: str) -> dict:
    """Drift nach allen bekannten Quartalsterminen eines Werts."""
    def holen():
        from .earnings_moves import chart_termine
        termine = [t for t in chart_termine(ticker).get("termine", []) if not t.get("zukunft") and t.get("reaktion") is not None]
        if not termine:
            return {"ticker": ticker, "termine": [], "einordnung": {"anzahl": 0}}
        kurse = schlusskurse(ticker, "10y")
        markt = schlusskurse("^GDAXI" if ticker.endswith(".DE") else "SPY", "10y")
        aus = []
        for t in termine:
            # Reaktionstag: nach Börse/unbekannt = nächster Handelstag, sonst der Tag selbst
            ab = pd.Timestamp(t["datum"]) + (pd.Timedelta(days=1) if t.get("sitzung") in ("nach Börse", "fenster", None) else pd.Timedelta(0))
            aus.append({"datum": t["datum"], "ueberraschung": t.get("ueberraschung"), "reaktion": round(t["reaktion"], 2), "drift": drift_reihe(kurse, markt, ab)})
        return {"ticker": ticker, "termine": aus[::-1], "einordnung": einordnen(aus), "markt": "DAX" if ticker.endswith(".DE") else "SPY"}
    return gecacht(f"drift:aktie:{ticker}", TAG, holen)
