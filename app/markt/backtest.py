"""Backtest-Labor (Roadmap Q9): einfache eigene Regeln ehrlich testen. Eine Spielwiese, kein Handelssystem.

Pflichten, die immer gelten:
- Kosten: je Kauf oder Verkauf 1 € Ordergebühr (bezogen auf den eingesetzten Betrag) plus halber Spread.
- Walk-Forward: Der Zeitraum wird in einen Anlauf und 3 Testabschnitte geteilt. Für jeden Abschnitt wird die
  beste Einstellung NUR auf den Daten davor gesucht und dann auf dem ungesehenen Abschnitt gemessen. Zusammen
  ergeben die Testabschnitte das Ergebnis "außerhalb der Stichprobe", nur das zählt.
- Vergleich: Kaufen und Halten im selben Zeitraum, mit denselben Kosten für den einen Kauf.
- Überanpassung: Die Zahl der getesteten Varianten wird gezählt; je mehr, desto eher ist ein gutes Ergebnis
  im Training Zufall. Dazu die Streuung aller Varianten.
Signale nutzen nur den Schlusskurs bis zum jeweiligen Tag; gehandelt wird zum nächsten Schluss (kein Blick in
die Zukunft). Liegt kein Geld in der Aktie, bringt es nichts (keine Zinsen, vorsichtig gerechnet).
"""
import itertools
import math

import numpy as np
import pandas as pd

from .abruf import gecacht, schlusskurse

ABSCHNITTE = 3
ANLAUF = 0.4  # Anteil der Daten vor dem ersten Testabschnitt

REGELN = {
    "ruecksetzer": {"name": "Rücksetzer kaufen", "text": "Kaufen, wenn der Kurs mindestens X % unter seinem Hoch der letzten N Tage liegt; verkaufen nach H Tagen oder am neuen Hoch.",
                    "gitter": {"x": [5, 10, 15, 20], "n": [20, 60], "h": [20, 60]}},
    "trend": {"name": "Trend folgen", "text": "Halten, solange der Schlusskurs über seinem gleitenden Durchschnitt von N Tagen liegt, sonst draußen.",
              "gitter": {"n": [50, 100, 150, 200]}},
    "rsi": {"name": "RSI", "text": "Kaufen, wenn der RSI (14 Tage) unter X fällt; verkaufen, wenn er über Y steigt.",
            "gitter": {"x": [25, 30, 35], "y": [55, 65, 75]}},
    "momentum": {"name": "Momentum", "text": "Halten, solange der Kurs über N Tage gestiegen ist, sonst draußen.",
                 "gitter": {"n": [63, 126, 252]}},
    "angst": {"name": "Nach Angst-Spitze kaufen", "text": "Kaufen, wenn der VIX in den letzten 10 Tagen über L lag und seitdem um mindestens F % gefallen ist (Angst lässt nach); halten für H Tage.",
              "gitter": {"l": [25, 30, 35], "f": [15, 25], "h": [20, 60]}},
}


def _rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    auf = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    ab = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + auf / ab.replace(0, np.nan))


def position(regel: str, p: dict, c: pd.Series, vix: pd.Series | None = None) -> pd.Series:
    """0/1 je Tag (gehalten am Ende des Tages). Reine Funktion; nutzt nur Daten bis zum Tag selbst."""
    if regel == "trend":
        return (c > c.rolling(p["n"]).mean()).astype(float)
    if regel == "momentum":
        return (c / c.shift(p["n"]) > 1).astype(float)
    werte = c.to_numpy()
    aus = np.zeros(len(c))
    if regel == "rsi":
        r = _rsi(c).to_numpy()
        drin = False
        for i in range(len(c)):
            if not drin and r[i] < p["x"]:
                drin = True
            elif drin and r[i] > p["y"]:
                drin = False
            aus[i] = drin
        return pd.Series(aus, c.index)
    if regel == "ruecksetzer":
        hoch = c.rolling(p["n"]).max().to_numpy()
        seit = -1
        for i in range(len(c)):
            if seit < 0 and hoch[i] == hoch[i] and werte[i] <= hoch[i] * (1 - p["x"] / 100):
                seit, ziel = i, hoch[i]
            elif seit >= 0 and (i - seit >= p["h"] or werte[i] >= ziel):
                seit = -1
            aus[i] = seit >= 0
        return pd.Series(aus, c.index)
    if regel == "angst":
        v = vix.reindex(c.index).ffill().to_numpy()
        spitze = pd.Series(v, c.index).rolling(10).max().to_numpy()
        seit = -1
        for i in range(len(c)):
            if seit < 0 and spitze[i] >= p["l"] and v[i] <= spitze[i] * (1 - p["f"] / 100):
                seit = i
            elif seit >= 0 and i - seit >= p["h"]:
                seit = -1
            aus[i] = seit >= 0
        return pd.Series(aus, c.index)
    raise ValueError(regel)


def ergebnis(pos: pd.Series, r: pd.Series, kosten: float) -> pd.Series:
    """Tägliche Rendite der Regel nach Kosten: Position von gestern auf die Rendite von heute. Reine Funktion."""
    vorher = pos.shift(1).fillna(0)
    wechsel = vorher.diff().abs().fillna(vorher.abs())
    return vorher * r - wechsel * kosten


def kennzahlen(t: pd.Series) -> dict:
    t = t.dropna()
    if t.empty:
        return {}
    kurve = (1 + t).cumprod()
    jahre = len(t) / 252
    gesamt = float(kurve.iloc[-1] - 1)
    dd = float((kurve / kurve.cummax() - 1).min())
    vol = float(t.std() * math.sqrt(252))
    return {"gesamt": gesamt * 100, "pro_jahr": ((1 + gesamt) ** (1 / jahre) - 1) * 100 if jahre > 0 and gesamt > -1 else None,
            "vol": vol * 100, "sharpe": float(t.mean() * 252 / (t.std() * math.sqrt(252))) if t.std() > 0 else None, "max_dd": dd * 100}


def labor(ticker: str, regel: str, einsatz: float = 1000.0, spread: float = 0.1) -> dict:
    ticker = ticker.strip().upper()
    if regel not in REGELN:
        return {"fehler": "Unbekannte Regel."}

    def holen():
        c = schlusskurse(ticker, "max").dropna()
        c = c[c.index >= c.index[-1] - pd.Timedelta(days=365 * 15)]
        if len(c) < 1000:
            raise ValueError("zu wenig Kurse")
        vix = schlusskurse("^VIX", "max") if regel == "angst" else None
        r = c.pct_change().fillna(0)
        kosten = 1 / max(einsatz, 50) + spread / 200
        g = REGELN[regel]["gitter"]
        varianten = [dict(zip(g, w)) for w in itertools.product(*g.values())]
        renditen = {i: ergebnis(position(regel, v, c, vix), r, kosten) for i, v in enumerate(varianten)}
        start = int(len(c) * ANLAUF)
        grenzen = np.linspace(start, len(c), ABSCHNITTE + 1).astype(int)
        oos, abschnitte = [], []
        for a, b in zip(grenzen, grenzen[1:]):
            # beste Variante nur nach dem Training davor (Sharpe), dann ungesehen testen
            def guete(i):
                k = kennzahlen(renditen[i].iloc[:a])
                return k.get("sharpe") or -9
            beste = max(range(len(varianten)), key=guete)
            test = renditen[beste].iloc[a:b]
            halten = r.iloc[a:b].copy()
            halten.iloc[0] -= kosten
            oos.append(test)
            abschnitte.append({"von": c.index[a].strftime("%Y-%m-%d"), "bis": c.index[b - 1].strftime("%Y-%m-%d"), "einstellung": varianten[beste],
                               "training": kennzahlen(renditen[beste].iloc[:a]), "test": kennzahlen(test), "halten": kennzahlen(halten),
                               "investiert": float(position(regel, varianten[beste], c, vix).iloc[a:b].mean() * 100)})
        oos = pd.concat(oos)
        halten = r.loc[oos.index].copy()
        halten.iloc[0] -= kosten
        alle_training = [kennzahlen(renditen[i].iloc[:grenzen[0]]) for i in range(len(varianten))]
        im_training_beste = max(alle_training, key=lambda k: k.get("sharpe") or -9)
        kurve = lambda t: [round(float(x), 4) for x in (1 + t).cumprod()]
        schritt = max(1, len(oos) // 600)
        return {
            "ticker": ticker, "regel": regel, "name": REGELN[regel]["name"], "text": REGELN[regel]["text"], "varianten": len(varianten),
            "kosten_prozent": kosten * 100, "einsatz": einsatz, "spread": spread,
            "test": kennzahlen(oos), "halten": kennzahlen(halten), "abschnitte": abschnitte,
            "training_beste": im_training_beste, "training_streuung": {
                "sharpe_min": min((k.get("sharpe") or 0) for k in alle_training), "sharpe_max": max((k.get("sharpe") or 0) for k in alle_training),
                "pro_jahr": sorted(round(k.get("pro_jahr") or 0, 1) for k in alle_training)},
            "wechsel": int((position(regel, abschnitte[-1]["einstellung"], c, vix).diff().abs() > 0).sum()),
            "verlauf": {"daten": [d.strftime("%Y-%m-%d") for d in oos.index[::schritt]], "regel": kurve(oos)[::schritt], "halten": kurve(halten)[::schritt]},
            "zeitraum": {"von": c.index[0].strftime("%Y-%m-%d"), "test_ab": c.index[grenzen[0]].strftime("%Y-%m-%d"), "bis": c.index[-1].strftime("%Y-%m-%d")},
        }
    try:
        return gecacht(f"backtest:{ticker}:{regel}:{einsatz:.0f}:{spread:.2f}", 6 * 3600, holen)
    except ValueError:
        return {"fehler": f"Für {ticker} gibt es zu wenige Kurse (mindestens rund 4 Jahre)."}
