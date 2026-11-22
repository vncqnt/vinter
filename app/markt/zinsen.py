"""Zinsen und Assets (Roadmap Stufe 5): US-Zinskurve, Zinskurven-Gebirge (3D-1) und Cross-Asset-Heatmap.

Zinskurve: Renditen von US-Staatsanleihen je Laufzeit (FRED, ohne Key: DGS1MO … DGS30), heute, vor einem Monat
und vor einem Jahr; Spannen 10 Jahre minus 2 Jahre und 10 Jahre minus 3 Monate mit Verlauf. Invertiert heißt:
kurze Laufzeiten rentieren höher als lange.
Gebirge: eine Kurve je Monat über bis zu 10 Jahre, übereinander gestaffelt; Abschnitte, in denen die Kurve
fällt (Inversion), sind markiert.
Cross-Asset-Heatmap: Veränderung wichtiger Anlageklassen über 1 Tag bis 1 Jahr (Yahoo, Handelswährung).
"""
from datetime import date, timedelta

import pandas as pd

from .abruf import STUNDE, fred, gecacht, schlusskurse

LAUFZEITEN = [("DGS1MO", "1 M.", 1 / 12), ("DGS3MO", "3 M.", 0.25), ("DGS6MO", "6 M.", 0.5), ("DGS1", "1 J.", 1), ("DGS2", "2 J.", 2),
              ("DGS3", "3 J.", 3), ("DGS5", "5 J.", 5), ("DGS7", "7 J.", 7), ("DGS10", "10 J.", 10), ("DGS20", "20 J.", 20), ("DGS30", "30 J.", 30)]
ASSETS = [("^GSPC", "S&P 500", "Aktien"), ("^NDX", "Nasdaq 100", "Aktien"), ("^GDAXI", "DAX", "Aktien"), ("^STOXX50E", "Euro Stoxx 50", "Aktien"),
          ("^N225", "Nikkei 225", "Aktien"), ("EEM", "Schwellenländer", "Aktien"), ("IWM", "US-Nebenwerte", "Aktien"),
          ("TLT", "US-Anleihen 20+ J.", "Anleihen"), ("IEF", "US-Anleihen 7–10 J.", "Anleihen"), ("HYG", "Hochzinsanleihen", "Anleihen"),
          ("GC=F", "Gold", "Rohstoffe"), ("SI=F", "Silber", "Rohstoffe"), ("CL=F", "Öl (WTI)", "Rohstoffe"), ("HG=F", "Kupfer", "Rohstoffe"),
          ("BTC-USD", "Bitcoin", "Krypto"), ("EURUSD=X", "Euro in Dollar", "Währungen"), ("DX-Y.NYB", "Dollar-Index", "Währungen"), ("^VIX", "VIX", "Volatilität")]
FENSTER = [("1t", 1), ("1w", 5), ("1m", 21), ("3m", 63), ("ytd", None), ("1j", 252)]


def zinskurve() -> dict:
    def holen():
        reihen = {name: fred(serie) for serie, name, _ in LAUFZEITEN}
        tab = pd.DataFrame(reihen).dropna(how="all").ffill()
        tab = tab[tab.index >= pd.Timestamp.today() - pd.Timedelta(days=365 * 10 + 40)]
        heute = tab.index[-1]

        def am(tage):
            return tab[tab.index <= heute - pd.Timedelta(days=tage)].iloc[-1]
        kurven = {"heute": tab.iloc[-1], "vor 1 Monat": am(30), "vor 1 Jahr": am(365)}
        spanne = pd.DataFrame({"10J−2J": tab["10 J."] - tab["2 J."], "10J−3M": tab["10 J."] - tab["3 M."]}).dropna()
        spanne = spanne[spanne.index >= heute - pd.Timedelta(days=365 * 5)].resample("W-FRI").last()
        monat = tab.resample("ME").last().dropna()
        return {"laufzeiten": [n for _, n, _ in LAUFZEITEN], "jahre": [j for _, _, j in LAUFZEITEN],
                "kurven": {k: [round(float(v), 3) for v in s] for k, s in kurven.items()},
                "stand": heute.strftime("%Y-%m-%d"),
                "spannen": {"daten": [d.strftime("%Y-%m-%d") for d in spanne.index], **{k: [round(float(v), 3) for v in spanne[k]] for k in spanne.columns}},
                "aktuell": {"10J−2J": round(float(tab["10 J."].iloc[-1] - tab["2 J."].iloc[-1]), 3), "10J−3M": round(float(tab["10 J."].iloc[-1] - tab["3 M."].iloc[-1]), 3)},
                "gebirge": {"monate": [d.strftime("%Y-%m") for d in monat.index], "kurven": [[round(float(v), 3) for v in z] for z in monat.to_numpy()]}}
    return gecacht("zinsen:kurve", 6 * STUNDE, holen)


def veraenderungen(s: pd.Series) -> dict:
    """Veränderung in Prozent über die Fenster (Handelstage), YTD ab dem letzten Schluss des Vorjahres. Reine Funktion."""
    s = s.dropna()
    aus = {}
    for name, n in FENSTER:
        if n is None:
            vor = s[s.index < pd.Timestamp(date(s.index[-1].year, 1, 1))]
            aus[name] = float(s.iloc[-1] / vor.iloc[-1] - 1) * 100 if not vor.empty else None
        else:
            aus[name] = float(s.iloc[-1] / s.iloc[-1 - n] - 1) * 100 if len(s) > n else None
    return aus


def cross_asset() -> dict:
    def holen():
        zeilen = []
        for tk, name, klasse in ASSETS:
            try:
                s = schlusskurse(tk, "2y")
            except Exception:
                continue
            zeilen.append({"ticker": tk, "name": name, "klasse": klasse, "stand": s.index[-1].strftime("%Y-%m-%d"), **veraenderungen(s)})
        return {"zeilen": zeilen, "fenster": [f for f, _ in FENSTER]}
    return gecacht("zinsen:assets", STUNDE, holen)
