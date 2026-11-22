"""Sektorrotation: welche Sektoren gerade gegenüber dem S&P 500 führen, nachlassen, hinterherlaufen oder aufholen.

- Relative Rotation Graph (RRG): Für jeden Sektor-ETF die relative Stärke zum S&P 500 (SPY) und deren
  Schwung. Die Originalmethode (JdK) ist nicht öffentlich; gerechnet wird eine offene Näherung mit Glättung,
  damit die Spuren ruhige Bögen statt Zickzack zeigen (ungeglättet sprang jeder Schritt so weit wie der
  ganze Abstand zur Mitte):
    RS          = Kurs Sektor / Kurs SPY, exponentiell geglättet (8 Wochen bzw. 10 Tage)
    RS-Ratio    = 100 + Z-Wert von RS über 26 Wochen bzw. 50 Tage      (über 100: stärker als üblich)
    RS-Momentum = 100 + Z-Wert der Veränderung des RS-Ratio über 4 Wochen bzw. 5 Tage
  Quadranten: führend (Ratio und Momentum über 100), abschwächend (Ratio über, Momentum unter 100),
  nachzügler (beide unter 100), erholend (Ratio unter, Momentum über 100). Sektoren wandern typischerweise
  im Uhrzeigersinn durch die Quadranten.
- Relative Wertentwicklung je Zeitraum und Trend (Kurs über 50- und 200-Tage-Linie).
- Konjunkturzyklus: welche Sektorgruppe in den letzten 3 Monaten vorne lag, und Offensiv gegen Defensiv.
- Breite je Sektor: Anteil der S&P-500-Mitglieder jedes Sektors über ihrem 20-, 50- und 200-Tage-Schnitt,
  Tag für Tag (für den Heat-Kalender).
"""
from datetime import date

import numpy as np
import pandas as pd

from .abruf import STUNDE, gecacht, schlusskurse
from .sektoren import BRANCHEN, SEKTOREN

# Je Takt: (Glättung, Fenster für die Z-Werte, Abstand für das Momentum)
EINSTELLUNG = {"woche": (8, 26, 4), "tag": (10, 50, 5)}
SPUR = 8        # so viele Punkte zeigt die Spur
PHASEN = [
    {"id": "frueh", "name": "Frühzyklus", "text": "Erholung nach einem Abschwung: Zinsen niedrig, Kredit und Konsum ziehen an", "etfs": ["XLF", "XLY", "XLRE", "XLI"]},
    {"id": "mitte", "name": "Mittelzyklus", "text": "Stabiles Wachstum: Technologie und Kommunikation führen", "etfs": ["XLK", "XLC"]},
    {"id": "spaet", "name": "Spätzyklus", "text": "Überhitzung, Inflation, steigende Rohstoffpreise", "etfs": ["XLE", "XLB"]},
    {"id": "abschwung", "name": "Abschwung", "text": "Wachstum lässt nach: defensive Sektoren mit stabilen Gewinnen", "etfs": ["XLP", "XLU", "XLV"]},
]
OFFENSIV, DEFENSIV = ["XLY", "XLK", "XLF", "XLI", "XLC"], ["XLP", "XLU", "XLV"]
ZEITRAEUME = {"1W": 5, "1M": 21, "3M": 63, "6M": 126, "1J": 252}


def _z(reihe: pd.Series, n: int) -> pd.Series:
    return (reihe - reihe.rolling(n).mean()) / reihe.rolling(n).std()


def _rrg(kurs: pd.Series, spy: pd.Series, takt: str) -> pd.DataFrame:
    glatt, n, abstand = EINSTELLUNG[takt]
    rs = (100 * kurs / spy).ewm(span=glatt).mean()
    ratio = 100 + _z(rs, n)
    momentum = 100 + _z(ratio.diff(abstand), n)
    return pd.concat([ratio, momentum], axis=1, keys=["ratio", "momentum"]).dropna()


def _quadrant(ratio: float, momentum: float) -> str:
    if ratio >= 100:
        return "fuehrend" if momentum >= 100 else "abschwaechend"
    return "erholend" if momentum >= 100 else "nachzuegler"


def _gruppe(universum: dict[str, str]) -> dict:
    spy = schlusskurse("SPY", "2y")
    kurse = {}
    for t in universum:
        try:
            kurse[t] = schlusskurse(t, "2y")
        except Exception:
            pass
    tab = pd.DataFrame(kurse).join(spy.rename("SPY"), how="inner").dropna(how="all").ffill()
    # Letzter Handelstag jeder Woche (mit seinem echten Datum, auch für die laufende Woche)
    woche = tab.groupby(tab.index.to_period("W-FRI")).tail(1)
    heute = tab.index[-1]
    jahresanfang = tab[tab.index < pd.Timestamp(heute.year, 1, 1)]

    werte = []
    for t, name in universum.items():
        if t not in tab or tab[t].isna().all():
            continue
        p = tab[t].dropna()
        eintrag = {"ticker": t, "name": name}
        for art, daten in (("woche", woche), ("tag", tab)):
            r = _rrg(daten[t], daten["SPY"], art).tail(SPUR + 26)
            if r.empty:
                continue
            quadranten = [_quadrant(a, b) for a, b in zip(r["ratio"], r["momentum"])]
            seit = 1
            for q in reversed(quadranten[:-1]):
                if q != quadranten[-1]:
                    break
                seit += 1
            spur = r.tail(SPUR)
            eintrag[art] = {"spur": [{"datum": d.strftime("%Y-%m-%d"), "ratio": round(float(a), 2), "momentum": round(float(b), 2)} for d, (a, b) in spur.iterrows()],
                            "quadrant": quadranten[-1], "seit": seit, "vorher": next((q for q in reversed(quadranten) if q != quadranten[-1]), None)}
        rel = {}
        for k, tage in ZEITRAEUME.items():
            if len(p) > tage:
                a = float(p.iloc[-1] / p.iloc[-1 - tage] - 1) * 100
                b = float(tab["SPY"].iloc[-1] / tab["SPY"].iloc[-1 - tage] - 1) * 100
                rel[k] = {"absolut": round(a, 2), "relativ": round(a - b, 2)}
        if len(jahresanfang):
            a = float(p.iloc[-1] / jahresanfang[t].iloc[-1] - 1) * 100
            b = float(tab["SPY"].iloc[-1] / jahresanfang["SPY"].iloc[-1] - 1) * 100
            rel["YTD"] = {"absolut": round(a, 2), "relativ": round(a - b, 2)}
        eintrag["performance"] = rel
        s50, s200 = p.rolling(50).mean().iloc[-1], p.rolling(200).mean().iloc[-1]
        eintrag["trend"] = {"ueber50": bool(p.iloc[-1] > s50), "ueber200": bool(p.iloc[-1] > s200),
                            "abstand200": round(float(p.iloc[-1] / s200 - 1) * 100, 1) if pd.notna(s200) else None}
        werte.append(eintrag)
    return {"werte": werte, "stand": heute.strftime("%Y-%m-%d"), "spy": {k: round(float(tab["SPY"].iloc[-1] / tab["SPY"].iloc[-1 - t] - 1) * 100, 2) for k, t in ZEITRAEUME.items() if len(tab) > t}}


def _zyklus() -> dict:
    tab = pd.DataFrame({t: schlusskurse(t, "2y") for t in SEKTOREN}).join(schlusskurse("SPY", "2y").rename("SPY"), how="inner").ffill().dropna()
    rel3m = (tab.iloc[-1] / tab.iloc[-64] - 1) * 100
    phasen = []
    for ph in PHASEN:
        relativ = float(np.mean([rel3m[t] - rel3m["SPY"] for t in ph["etfs"] if t in rel3m]))
        rel1m = float(np.mean([(tab[t].iloc[-1] / tab[t].iloc[-22] - 1) * 100 - (tab["SPY"].iloc[-1] / tab["SPY"].iloc[-22] - 1) * 100 for t in ph["etfs"]]))
        phasen.append({**ph, "relativ_3m": round(relativ, 2), "relativ_1m": round(rel1m, 2)})
    # Offensiv gegen Defensiv: gleich gewichtete Körbe, auf 100 am Anfang des letzten Jahres
    letzte = tab.iloc[-252:]
    off = (letzte[OFFENSIV] / letzte[OFFENSIV].iloc[0]).mean(axis=1)
    deff = (letzte[DEFENSIV] / letzte[DEFENSIV].iloc[0]).mean(axis=1)
    quote = off / deff * 100
    schnitt = quote.rolling(50).mean()
    return {"phasen": phasen,
            "off_def": {"daten": [d.strftime("%Y-%m-%d") for d in quote.index], "quote": [round(float(v), 2) for v in quote],
                        "schnitt50": [None if pd.isna(v) else round(float(v), 2) for v in schnitt],
                        "spy": [round(float(v), 2) for v in letzte["SPY"]],
                        "veraenderung_1m": round(float(quote.iloc[-1] / quote.iloc[-22] - 1) * 100, 2),
                        "veraenderung_3m": round(float(quote.iloc[-1] / quote.iloc[-64] - 1) * 100, 2),
                        "ueber_schnitt": bool(quote.iloc[-1] > schnitt.iloc[-1])}}


GICS = {
    "Information Technology": "Technologie", "Communication Services": "Kommunikation", "Consumer Discretionary": "Zyklischer Konsum",
    "Consumer Staples": "Basiskonsum", "Energy": "Energie", "Financials": "Finanzen", "Health Care": "Gesundheit",
    "Industrials": "Industrie", "Materials": "Grundstoffe", "Real Estate": "Immobilien", "Utilities": "Versorger",
}


def breite_sektoren() -> dict:
    """{daten, reihen: {Fenster: {Sektor: [Prozent]}}, anzahl: {Sektor: Mitglieder}}, einmal am Tag."""
    def holen():
        from .indizes import mitglieder, sp500_kurse
        kurse = sp500_kurse()
        sektor_von = {m["ticker"]: GICS.get(m["sektor"]) for m in mitglieder("sp500")}
        gruppen = {}
        for t in kurse.columns:
            if sektor_von.get(t):
                gruppen.setdefault(sektor_von[t], []).append(t)
        fenster_daten = {}
        for fenster in (20, 50, 200):
            schnitt = kurse.rolling(fenster, min_periods=int(fenster * 0.9)).mean()
            ueber = (kurse > schnitt).where(schnitt.notna())
            je = {"S&P 500": ueber.mean(axis=1) * 100}
            for name, ticker in gruppen.items():
                je[name] = ueber[ticker].mean(axis=1) * 100
            # Jedes Fenster so weit zurück, wie die Kurse (2 Jahre) es erlauben
            tab = pd.DataFrame(je).iloc[fenster:].dropna(how="all")
            fenster_daten[str(fenster)] = {"daten": [d.strftime("%Y-%m-%d") for d in tab.index],
                                           "reihen": {n: [None if pd.isna(v) else int(round(v)) for v in tab[n]] for n in tab.columns}}
        return {"fenster": fenster_daten,
                "anzahl": {n: len(t) for n, t in gruppen.items()} | {"S&P 500": sum(len(t) for t in gruppen.values())}}
    return gecacht("rotation:breite:2", 12 * STUNDE, holen)


def _kennzahlen_wert(p: pd.Series, spy: pd.Series | None = None) -> dict:
    """Kursentwicklung, Trend und Abstand zum Hoch einer Aktie aus ihren Schlusskursen."""
    p = p.dropna()
    if len(p) < 70:
        return {}

    def seit(tage):
        return round(float(p.iloc[-1] / p.iloc[-1 - tage] - 1) * 100, 2) if len(p) > tage else None
    jahresanfang = p[p.index < pd.Timestamp(p.index[-1].year, 1, 1)]
    schnitte = {f: p.rolling(f).mean().iloc[-1] for f in (20, 50, 200)}
    letzte = p.iloc[-252:]
    aus = {"kurs": round(float(p.iloc[-1]), 2), "d1": seit(1), "w1": seit(5), "m1": seit(21), "m3": seit(63), "m6": seit(126), "j1": seit(251),
           "ytd": round(float(p.iloc[-1] / jahresanfang.iloc[-1] - 1) * 100, 2) if len(jahresanfang) else None,
           "ueber": {str(f): (None if pd.isna(v) else bool(p.iloc[-1] > v)) for f, v in schnitte.items()},
           "abstand200": None if pd.isna(schnitte[200]) else round(float(p.iloc[-1] / schnitte[200] - 1) * 100, 1),
           "zum_hoch": round(float(p.iloc[-1] / letzte.max() - 1) * 100, 1),
           "spur": [round(float(v), 1) for v in (p.iloc[-63:] / p.iloc[-63] * 100)]}
    if spy is not None:
        s = spy.reindex(p.index).ffill()
        if s.iloc[-64:].notna().all():
            aus["rel3m"] = round(float((p.iloc[-1] / p.iloc[-64]) / (s.iloc[-1] / s.iloc[-64]) - 1) * 100, 2)
    return aus


def sektor_werte(name: str | None = None, etf: str | None = None) -> dict:
    """Einzelwerte eines Sektors (alle S&P-500-Mitglieder) oder einer Branche (größte Werte des ETFs),
    dazu die relative Stärke des Sektor-ETFs zum S&P 500 über ein Jahr."""
    def holen():
        from ..cache import markt_lesen
        from .indizes import mitglieder, sp500_kurse
        from .sektoren import _top_werte
        spy = schlusskurse("SPY", "2y")
        kennz = markt_lesen("kennzahlen:sp500")[0] or {}
        zeilen = []
        if name:
            kurse = sp500_kurse()
            ziel_etf = next((t for t, n in SEKTOREN.items() if n == name), None)
            for m in mitglieder("sp500"):
                if GICS.get(m["sektor"]) == name and m["ticker"] in kurse:
                    k = kennz.get(m["ticker"], {})
                    zeilen.append({"ticker": m["ticker"], "name": m["name"], "cap": k.get("cap"), "fwd": k.get("fwd"),
                                   **_kennzahlen_wert(kurse[m["ticker"]], spy)})
        else:
            ziel_etf = etf
            for h in _top_werte(etf) or []:
                try:
                    preis = schlusskurse(h["ticker"], "2y")
                except Exception:
                    continue
                k = kennz.get(h["ticker"], {})
                zeilen.append({"ticker": h["ticker"], "name": h["name"], "gewicht_etf": round(h["gewicht"], 2), "cap": k.get("cap"), "fwd": k.get("fwd"),
                               **_kennzahlen_wert(preis, spy)})
        summe = sum(z.get("cap") or 0 for z in zeilen)
        for z in zeilen:
            z["gewicht"] = z.pop("gewicht_etf", None) or (round(z["cap"] / summe * 100, 2) if summe and z.get("cap") else None)
        rs = None
        if ziel_etf:
            e = schlusskurse(ziel_etf, "2y")
            tab = pd.concat([e, spy], axis=1, keys=["e", "s"]).dropna().iloc[-252:]
            q = tab["e"] / tab["s"]
            rs = {"daten": [d.strftime("%Y-%m-%d") for d in tab.index], "rs": [round(float(v / q.iloc[0] * 100 - 100), 2) for v in q],
                  "etf": [round(float(v / tab["e"].iloc[0] * 100 - 100), 2) for v in tab["e"]],
                  "spy": [round(float(v / tab["s"].iloc[0] * 100 - 100), 2) for v in tab["s"]]}
        zeilen.sort(key=lambda z: -(z.get("gewicht") or 0))
        return {"name": name or BRANCHEN.get(etf, etf), "etf": ziel_etf, "werte": zeilen, "rs": rs, "quelle": "sp500" if name else "etf"}
    return gecacht(f"rotation:sektor:{name or etf}", STUNDE, holen)


def rotation() -> dict:
    def holen():
        return {"sektoren": _gruppe(SEKTOREN), "branchen": _gruppe(BRANCHEN), "zyklus": _zyklus(), "berechnet": date.today().isoformat()}
    return gecacht("rotation:3", STUNDE, holen)
