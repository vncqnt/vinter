"""Punkt 32: Über Nacht gegen im Handel – wo entsteht die Rendite?

Jeder Handelstag wird in zwei Teile zerlegt:
- über Nacht: vom Schluss des Vortags bis zur Eröffnung (Eröffnung / Vortagesschluss − 1).
  Bei US-Werten steckt hier der nach- und vorbörsliche Handel drin, also auch fast alle Reaktionen auf
  Quartalszahlen (die meist nach Börsenschluss kommen) und auf Nachrichten aus Asien und Europa.
- im Handel: von der Eröffnung bis zum Schluss (Schluss / Eröffnung − 1).
Beide Teile multipliziert ergeben genau die Tagesrendite. Aufsummiert (verkettet) über einen Zeitraum zeigt sich, ob
ein Wert vor allem nachts oder tagsüber steigt. Ein bekanntes Muster: Steigt ein Index über Wochen nur nachts und
fällt tagsüber, verkaufen große Anleger im Handel in die Stärke hinein. Das ist eine Beobachtung, kein Signal.

Kurse von Yahoo, bereinigt um Splits und Dividenden (sonst sähe jeder Dividendenabschlag wie ein Verlust über
Nacht aus). Manche Werte haben bei Yahoo keine echten Eröffnungskurse (Eröffnung = Vortagesschluss); das wird
erkannt und angezeigt statt falsch gerechnet.
"""
import math

import pandas as pd
import yfinance as yf

from .abruf import STUNDE, gecacht

INDIZES = [
    {"ticker": "SPY", "name": "S&P 500 (SPY)"},
    {"ticker": "QQQ", "name": "Nasdaq 100 (QQQ)"},
    {"ticker": "IWM", "name": "Russell 2000 (IWM)"},
    {"ticker": "^GDAXI", "name": "DAX"},
]
TAGE = 3 * 252 + 30         # rund drei Jahre Handelstage
DIVERGENZ_TAGE = 20         # Fenster für die Markierung
DIVERGENZ_PP = 3.0          # Abstand in Prozentpunkten bei gegensätzlicher Richtung
ECHTE_EROEFFNUNG = 0.6      # Anteil der Tage mit eigener Eröffnung, ab dem die Zerlegung gilt


def _reihe(df_open: pd.Series, df_close: pd.Series) -> dict | None:
    d = pd.DataFrame({"o": df_open, "c": df_close}).dropna()
    d = d[(d["o"] > 0) & (d["c"] > 0)]
    if len(d) < 30:
        return None
    vortag = d["c"].shift(1)
    nacht = (d["o"] / vortag - 1).iloc[1:]
    handel = (d["c"] / d["o"] - 1).iloc[1:]
    # Eröffnung = Vortagesschluss an fast jedem Tag: Yahoo hat keine echten Eröffnungskurse
    eigene = float((nacht.abs() > 1e-7).mean())
    nacht, handel = nacht.tail(TAGE), handel.tail(TAGE)
    return {
        "daten": [t.strftime("%Y-%m-%d") for t in nacht.index],
        "nacht": [round(float(x), 6) for x in nacht],
        "handel": [round(float(x), 6) for x in handel],
        "echte_eroeffnung": eigene >= ECHTE_EROEFFNUNG,
        "anteil_eigene_eroeffnung": round(eigene, 3),
    }


def tagesreihen(tickers: list[str]) -> dict:
    """{Ticker: Tagesrenditen über Nacht und im Handel}, 6 Stunden zwischengespeichert."""
    tickers = sorted(set(tickers))
    if not tickers:
        return {}

    def holen():
        roh = yf.download(tickers, period="4y", interval="1d", auto_adjust=True, progress=False, threads=True, group_by="column")
        aus = {}
        for t in tickers:
            try:
                o = roh["Open"][t] if isinstance(roh.columns, pd.MultiIndex) else roh["Open"]
                c = roh["Close"][t] if isinstance(roh.columns, pd.MultiIndex) else roh["Close"]
            except KeyError:
                continue
            r = _reihe(o, c)
            if r:
                aus[t] = r
        return aus
    return gecacht(f"nacht:{','.join(tickers)}", 6 * STUNDE, holen)


def _kette(renditen: list[float]) -> float:
    """Verkettete Rendite in Prozent."""
    return (math.exp(sum(math.log1p(r) for r in renditen)) - 1) * 100 if renditen else 0.0


def kennzahlen(r: dict, tage: int) -> dict:
    """Über Nacht, im Handel und gesamt für die letzten `tage` Handelstage, dazu die Divergenz der letzten 20 Tage."""
    n, h = r["nacht"][-tage:], r["handel"][-tage:]
    n20, h20 = r["nacht"][-DIVERGENZ_TAGE:], r["handel"][-DIVERGENZ_TAGE:]
    nacht20, handel20 = _kette(n20), _kette(h20)
    divergenz = None
    if r["echte_eroeffnung"] and nacht20 * handel20 < 0 and abs(nacht20 - handel20) >= DIVERGENZ_PP:
        divergenz = "nachts hoch, tagsüber runter" if nacht20 > 0 else "nachts runter, tagsüber hoch"
    positiv_nacht = sum(1 for x in n if x > 0) / len(n) * 100 if n else None
    return {"nacht": _kette(n), "handel": _kette(h), "gesamt": _kette([(1 + a) * (1 + b) - 1 for a, b in zip(n, h)]),
            "tage": len(n), "nacht_positiv_anteil": positiv_nacht,
            "nacht20": nacht20, "handel20": handel20, "divergenz": divergenz}
