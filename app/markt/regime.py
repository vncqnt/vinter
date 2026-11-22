"""Markt-Regime-Score (Roadmap Stufe 2): Risk-on, neutral oder Risk-off aus fünf Messungen.

Jede Messung wird auf eine Skala von −1 (Risk-off) bis +1 (Risk-on) gebracht, mit festen, offenen Regeln:
- VIX-Kurve: VIX geteilt durch VIX3M. Normal liegt kurzfristige Angst unter der langfristigen (unter 0,9);
  über 1 (Backwardation) ist akuter Stress. 0,88 → +1, 1,02 → −1.
- Skew: IV des 25-Delta-Puts minus 25-Delta-Call bei SPY (30 Tage, aus der Volatilitätsfläche). 5 → +1, 10 → −1.
- Kreditspreads: US-Hochzins-Aufschlag (FRED BAMLH0A0HYM2), Lage in 10 Jahren (niedrig → +1, hoch → −1);
  schnelle Ausweitung (+0,5 Prozentpunkte in einem Monat) zieht zusätzlich 0,5 ab, ab +1 Punkt 1.
- Marktbreite: Anteil der S&P-500-Werte über ihrer 200-Tage-Linie. 75 % → +1, 25 % → −1.
- Gamma: Abstand des SPY zum Gamma-Flip. Darüber dämpfen Händler-Absicherungen Bewegungen, darunter
  verstärken sie sie. +2 % → +1, −2 % → −1.
Gesamt = Durchschnitt der verfügbaren Messungen × 100. Ab +25 Risk-on, ab −25 Risk-off, dazwischen neutral.
Der Verlauf nutzt je Tag die Messungen, für die es an dem Tag Daten gibt (Skew und Gamma erst seit Beginn der
eigenen Aufzeichnung). Beschreibt die Lage, sagt nichts über die Zukunft.
"""
from datetime import date

import pandas as pd

from .abruf import fred, gecacht, historie_eintragen, historie_lesen, perzentil, schlusskurse

GRENZE = 25


def _klemme(x: float) -> float:
    return max(-1.0, min(1.0, x))


def linear(wert: float, plus_bei: float, minus_bei: float) -> float:
    """+1 bei plus_bei, −1 bei minus_bei, dazwischen linear, außerhalb begrenzt. Reine Funktion."""
    return _klemme(1 - 2 * (wert - plus_bei) / (minus_bei - plus_bei))


def s_vix(verhaeltnis: float) -> float:
    return linear(verhaeltnis, 0.88, 1.02)


def s_skew(skew: float) -> float:
    return linear(skew, 5, 10)


def s_kredit(perz: float, aenderung_1m: float) -> float:
    basis = 1 - 2 * perz / 100
    strafe = 1.0 if aenderung_1m >= 1.0 else 0.5 if aenderung_1m >= 0.5 else 0.0
    return _klemme(basis - strafe)


def s_breite(ueber200: float) -> float:
    return linear(ueber200, 75, 25)


def s_gamma(abstand_prozent: float) -> float:
    return _klemme(abstand_prozent / 2)


def einstufen(punkte: float) -> str:
    return "Risk-on" if punkte >= GRENZE else "Risk-off" if punkte <= -GRENZE else "neutral"


def _skew_verlauf() -> pd.Series:
    from .volaflaeche import _db
    db = _db()
    try:
        zeilen = db.execute("SELECT datum, json_extract(daten, '$.kennzahlen.skew25') FROM flaechen WHERE ticker = 'SPY' ORDER BY datum").fetchall()
    finally:
        db.close()
    return pd.Series({pd.Timestamp(d): float(s) for d, s in zeilen if s is not None}, dtype=float)


def regime() -> dict:
    def holen():
        teile, reihen = [], {}
        # 1) VIX-Kurve
        try:
            vix, vix3m = schlusskurse("^VIX", "3y"), schlusskurse("^VIX3M", "3y")
            v = (vix / vix3m).dropna()
            reihen["vix"] = v.map(s_vix)
            teile.append({"id": "vix", "name": "VIX-Kurve", "wert": round(float(v.iloc[-1]), 3), "anzeige": f"VIX/VIX3M {float(v.iloc[-1]):.2f}".replace(".", ","),
                          "punkte": s_vix(float(v.iloc[-1])), "stand": v.index[-1].strftime("%Y-%m-%d"),
                          "text": "Backwardation: kurzfristige Angst größer als langfristige" if v.iloc[-1] > 1 else "Contango: normale Lage" if v.iloc[-1] < 0.92 else "Kurve fast flach"})
        except Exception:
            pass
        # 2) Skew
        try:
            sk = _skew_verlauf()
            if not sk.empty:
                reihen["skew"] = sk.map(s_skew)
                teile.append({"id": "skew", "name": "Skew (SPY)", "wert": round(float(sk.iloc[-1]), 2), "anzeige": f"{float(sk.iloc[-1]):.1f} Vol-Punkte".replace(".", ","),
                              "punkte": s_skew(float(sk.iloc[-1])), "stand": sk.index[-1].strftime("%Y-%m-%d"),
                              "text": "Absicherung gegen Kursrutsch sehr gefragt" if sk.iloc[-1] >= 9 else "Absicherung normal gefragt" if sk.iloc[-1] >= 6 else "wenig Nachfrage nach Absicherung",
                              "verlauf_tage": len(sk)})
        except Exception:
            pass
        # 3) Kreditspreads
        try:
            hy = fred("BAMLH0A0HYM2")
            hy = hy[hy.index >= pd.Timestamp.today() - pd.Timedelta(days=365 * 10)]
            monat = hy - hy.shift(21)
            # Lage je Tag gegen die 10 Jahre bis heute (für den Verlauf eine feste Vergleichsbasis)
            rang = hy.rank(pct=True) * 100
            reihen["kredit"] = pd.Series([s_kredit(r, m if pd.notna(m) else 0) for r, m in zip(rang, monat)], index=hy.index)
            jetzt, aend = float(hy.iloc[-1]), float(monat.iloc[-1])
            teile.append({"id": "kredit", "name": "Kreditspreads", "wert": jetzt, "anzeige": f"{jetzt:.2f} % (Monat {aend:+.2f})".replace(".", ","),
                          "punkte": s_kredit(perzentil(hy, jetzt), aend), "stand": hy.index[-1].strftime("%Y-%m-%d"),
                          "text": "weiten sich schnell aus" if aend >= 0.5 else "weiten sich aus" if aend > 0.15 else "ruhig" if abs(aend) <= 0.15 else "engen sich ein"})
        except Exception:
            pass
        # 4) Marktbreite
        try:
            from .signale import breite
            b = breite()
            reihe = pd.Series(b["werte"]["ueber200"], index=pd.to_datetime(b["daten"])).dropna()
            reihen["breite"] = reihe.map(s_breite)
            teile.append({"id": "breite", "name": "Marktbreite", "wert": b["ueber200"], "anzeige": f"{b['ueber200']:.0f} % über 200-Tage-Linie",
                          "punkte": s_breite(b["ueber200"]), "stand": b["daten"][-1],
                          "text": "breiter Markt" if b["ueber200"] >= 60 else "schmaler Markt" if b["ueber200"] <= 40 else "gemischt"})
        except Exception:
            pass
        # 5) Gamma
        try:
            from .positionierung import optionen
            o = optionen("SPY")
            if o.get("flip"):
                abstand = (o["kurs"] / o["flip"] - 1) * 100
                historie_eintragen("regime:gamma", date.today().isoformat(), round(abstand, 3))
                g = historie_lesen("regime:gamma")
                reihen["gamma"] = pd.Series({pd.Timestamp(k): s_gamma(v) for k, v in g.items()}, dtype=float)
                teile.append({"id": "gamma", "name": "Gamma (SPY)", "wert": round(abstand, 2), "anzeige": f"{abstand:+.1f} % zum Flip {o['flip']:.0f}".replace(".", ","),
                              "punkte": s_gamma(abstand), "stand": date.today().isoformat(),
                              "text": "über dem Flip: Händler dämpfen Bewegungen" if abstand > 0 else "unter dem Flip: Händler verstärken Bewegungen", "verlauf_tage": len(g)})
        except Exception:
            pass
        if not teile:
            raise ValueError("keine Daten")
        punkte = sum(t["punkte"] for t in teile) / len(teile) * 100
        for t in teile:
            t["punkte"] = round(t["punkte"], 3)
        # Verlauf: Börsentage der VIX-Reihe, je Tag der Schnitt der vorhandenen Teile
        tab = pd.DataFrame(reihen).sort_index()
        tab = tab[tab.index >= pd.Timestamp.today() - pd.Timedelta(days=730)]
        tab = tab.ffill(limit=5)
        if "vix" in reihen:
            tab = tab.reindex(reihen["vix"].index[reihen["vix"].index >= tab.index[0]]).ffill(limit=5)
        gesamt = tab.mean(axis=1) * 100
        verlauf = {"daten": [d.strftime("%Y-%m-%d") for d in gesamt.index], "gesamt": [None if pd.isna(v) else round(float(v), 1) for v in gesamt],
                   "teile": {k: [None if pd.isna(v) else round(float(v) * 100, 1) for v in tab[k]] for k in tab.columns},
                   "anzahl": [int(n) for n in tab.notna().sum(axis=1)]}
        heute = {"punkte": round(punkte, 1), "regime": einstufen(punkte), "teile": len(teile)}
        historie_eintragen("regime", date.today().isoformat(), heute)
        return {**heute, "messungen": sorted(teile, key=lambda t: t["punkte"]), "verlauf": verlauf, "grenze": GRENZE}
    return gecacht("regime:v1", 30 * 60, holen)
