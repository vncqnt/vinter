"""Mini-Features (Roadmap Stufe 5): kleine Kennzahlen je Wert und Marktbreite je Thema.

- Bewegung heute in Sigma: letzte Tagesveränderung geteilt durch die Standardabweichung der Tagesrenditen der 20 Tage
  davor. Ab ±2 σ ungewöhnlich.
- Abstand zum 52-Wochen-Hoch und -Tief, Drawdown vom höchsten Schluss der letzten 5 Jahre.
- Vola-Kompression: Bollinger-Bandbreite (20 Tage, 2 σ, relativ zum Kurs) und ihr Perzentil im letzten Jahr; im
  unteren Zehntel liegt der Kurs ungewöhnlich ruhig, danach folgten oft größere Bewegungen (Richtung offen).
- Marktbreite je Thema: Anteil der Werte eines Themas über ihrer 50- und 200-Tage-Linie (Tageskurse, Yahoo).
"""
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from .abruf import STUNDE, gecacht, schlusskurse


def kennzahlen(c: pd.Series) -> dict:
    """Reine Funktion auf Tagesschlusskursen (mindestens rund ein Jahr)."""
    c = c.dropna()
    r = c.pct_change()
    s20 = float(r.iloc[-21:-1].std())
    jahr = c.iloc[-252:]
    hoch, tief = float(jahr.max()), float(jahr.min())
    gesamt_hoch = float(c.max())
    mittel = c.rolling(20).mean()
    breite = (4 * c.rolling(20).std() / mittel).dropna()
    b_jahr = breite.iloc[-252:]
    b_jetzt = float(breite.iloc[-1]) if len(breite) else None
    perz = float((b_jahr < b_jetzt).mean() * 100) if b_jetzt is not None and len(b_jahr) > 60 else None
    return {
        "sigma_heute": float(r.iloc[-1] / s20) if s20 > 0 else None, "tag": float(r.iloc[-1]) * 100,
        "abstand_hoch52": (float(c.iloc[-1]) / hoch - 1) * 100, "abstand_tief52": (float(c.iloc[-1]) / tief - 1) * 100,
        "drawdown": (float(c.iloc[-1]) / gesamt_hoch - 1) * 100, "hoch_datum": c.idxmax().strftime("%Y-%m-%d"),
        "bb_breite": b_jetzt * 100 if b_jetzt is not None else None, "bb_perzentil": perz, "kompression": perz is not None and perz <= 10,
        "stand": c.index[-1].strftime("%Y-%m-%d"),
    }


def mini(ticker: str) -> dict:
    c = schlusskurse(ticker, "5y")
    if len(c) < 120:
        return {"fehler": "zu wenig Kurse"}
    return {"ticker": ticker, **kennzahlen(c)}


def themen_breite() -> dict:
    """Je Thema der Anteil der Werte über der 50- und 200-Tage-Linie. Ein gebündelter Abruf, 12 Stunden gemerkt."""
    from .themen import BLOCK, lade_themen

    def holen():
        themen = lade_themen()
        alle = sorted({t for th in themen for t in th["werte"]})
        start = (date.today() - timedelta(days=420)).isoformat()
        schluss = {}
        for i in range(0, len(alle), BLOCK):
            block = alle[i:i + BLOCK]
            try:
                tab = yf.download(block, start=start, interval="1d", auto_adjust=True, progress=False, group_by="ticker", threads=True)
            except Exception:
                continue
            for t in block:
                try:
                    s = tab[t]["Close"].dropna()
                except KeyError:
                    continue
                if len(s) >= 210:
                    schluss[t] = s
        aus = []
        for th in themen:
            ueber50 = ueber200 = n = 0
            for t in th["werte"]:
                s = schluss.get(t)
                if s is None:
                    continue
                n += 1
                ueber50 += float(s.iloc[-1]) > float(s.iloc[-50:].mean())
                ueber200 += float(s.iloc[-1]) > float(s.iloc[-200:].mean())
            if n:
                aus.append({"name": th["name"], "n": n, "ueber50": ueber50 / n * 100, "ueber200": ueber200 / n * 100})
        return {"themen": sorted(aus, key=lambda x: -x["ueber200"]), "stand": date.today().isoformat()}
    return gecacht("themen:breite", 12 * STUNDE, holen)
