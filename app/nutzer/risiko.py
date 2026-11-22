"""Risiko des heutigen Depots, gerechnet aus den Tagesrenditen des letzten Jahres.

Gerechnet wird mit den heutigen Gewichten, als hätte das Depot das ganze Jahr so ausgesehen.
Das zeigt das Risiko der jetzigen Zusammensetzung, nicht das der Vergangenheit.
Alle Kurse in Euro, Wechselkursschwankungen zählen also mit, wie für einen Anleger aus Deutschland.
"""
import math
from datetime import date

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.spatial.distance import squareform

from ..benutzer import Benutzer
from ..markt.euro_kurse import euro_kurse
from .tr_parser import lade_trades

MARKT = "EUNL.DE"          # MSCI World in Euro als "Markt" für das Beta
TAGE_JAHR = 252
VAR_NIVEAU = 5             # 95-%-Value-at-Risk = 5-%-Quantil der Tagesrenditen
FENSTER = {"3m": 92, "6m": 183, "1j": 365, "2j": 730}  # Kalendertage je Zeitraum
ROLLEND = 21               # Schwankung über die letzten rund 4 Wochen, für den Verlauf
KORR_FENSTER = (60, 20)    # rollierende Korrelation: Haupt-Fenster und schnelles Fenster (Handelstage)
KORR_WARN_PERZENTIL = 80   # Warnung, wenn der heutige Wert höher ist als an 80 % der Tage zuvor ...
KORR_WARN_ANSTIEG = 0.10   # ... und in den letzten 20 Handelstagen um mindestens 0,10 gestiegen ist
KORR_HOCH = 0.60           # ab hier bewegt sich das Depot fast wie eine einzige Position


def rollierende_korrelation(pos: list[dict], kurse: dict[str, pd.Series]) -> dict | None:
    """Gewichtete durchschnittliche Korrelation aller Positionspaare, rollierend über 60 und 20 Tage.

    Jedes Paar zählt mit dem Produkt seiner Gewichte: Zwei große Positionen, die sich gleich
    bewegen, sind wichtiger als zwei kleine. Steigt die Zahl, bewegt sich alles gleich und die
    Streuung im Depot schützt weniger (typisch in Krisen).
    """
    pos = [p for p in pos if p["ticker"] in kurse]
    if len(pos) < 3:
        return None
    tage = pd.bdate_range(pd.Timestamp(date.today()) - pd.Timedelta(days=760), pd.Timestamp(date.today()))
    preise = pd.DataFrame({p["ticker"]: kurse[p["ticker"]].reindex(kurse[p["ticker"]].index.union(tage)).ffill(limit=5).reindex(tage) for p in pos})
    R = preise.pct_change(fill_method=None).iloc[1:]
    R = R.where(R.abs() < 0.5)  # Datenfehler (Splits) raus
    namen = list(R.columns)
    w = np.array([p["wert_eur"] for p in pos], dtype=float)
    w = w / w.sum()
    paar_gewicht = np.outer(w, w)
    np.fill_diagonal(paar_gewicht, 0)

    def schnitt(fenster: int) -> pd.Series:
        rc = R.rolling(fenster, min_periods=int(fenster * 0.7)).corr()
        werte = {}
        for d in R.index[fenster:]:
            m = rc.loc[d].reindex(index=namen, columns=namen).to_numpy()
            ok = ~np.isnan(m) & (paar_gewicht > 0)
            if ok.sum() >= 2:
                werte[d] = float((m[ok] * paar_gewicht[ok]).sum() / paar_gewicht[ok].sum())
        return pd.Series(werte)

    lang, kurz = (schnitt(f) for f in KORR_FENSTER)
    if len(lang) < 40:
        return None
    jetzt = float(lang.iloc[-1])
    vor20 = float(lang.iloc[-21]) if len(lang) > 21 else float(lang.iloc[0])
    perzentil = float((lang.iloc[:-1] < jetzt).mean() * 100)
    warnung = (perzentil >= KORR_WARN_PERZENTIL and jetzt - vor20 >= KORR_WARN_ANSTIEG) or jetzt >= KORR_HOCH

    # Welche Paare rücken zusammen? Korrelation im letzten Fenster gegen das Fenster davor.
    f = KORR_FENSTER[0]
    jetzt_m, vorher_m = R.iloc[-f:].corr(min_periods=int(f * 0.7)), R.iloc[-2 * f:-f].corr(min_periods=int(f * 0.7))
    name_von = {p["ticker"]: p["name"] for p in pos}
    paare = []
    for i, a in enumerate(namen):
        for b in namen[i + 1:]:
            j, v = jetzt_m.loc[a, b], vorher_m.loc[a, b]
            if pd.notna(j) and pd.notna(v):
                paare.append({"a": name_von[a], "b": name_von[b], "jetzt": float(j), "vorher": float(v), "anstieg": float(j - v),
                              "gewicht": float(w[namen.index(a)] * w[namen.index(b)] * 100)})
    paare.sort(key=lambda x: -x["anstieg"] * (0.5 + min(1.0, x["gewicht"] * 20)))

    alle = lang.index.union(kurz.index)
    rund = lambda s: [None if pd.isna(v) else round(float(v), 3) for v in s.reindex(alle)]
    return {
        "daten": [d.date().isoformat() for d in alle],
        "lang": rund(lang), "kurz": rund(kurz), "fenster": list(KORR_FENSTER),
        "jetzt": jetzt, "vor20": vor20, "median": float(lang.median()), "perzentil": perzentil,
        "hoechst": float(lang.max()), "hoechst_am": lang.idxmax().date().isoformat(),
        "warnung": bool(warnung), "schwelle_hoch": KORR_HOCH,
        "paare": [x for x in paare if x["anstieg"] > 0.1][:5],
    }


def _drawdown(preise: pd.Series) -> pd.Series:
    return preise / preise.cummax() - 1


def risiko(b: Benutzer, positionen: list[dict], zeitraum: str = "1j") -> dict:
    pos = [p for p in positionen if p.get("ticker") and p.get("wert_eur") and not p.get("extra")]
    # Werte aus dem Markt (extra): nur in der Korrelation und den Kursverläufen, nicht im Depot-Risiko
    zusatz = [p for p in positionen if p.get("extra") and p.get("ticker") and p["ticker"] not in {q["ticker"] for q in pos}]
    if not pos:
        return {"fehler": "Keine Positionen mit Kurs gefunden."}
    zeitraum = zeitraum if zeitraum in FENSTER else "1j"
    kurse = euro_kurse(sorted({p["ticker"] for p in pos + zusatz} | {MARKT}), tage=800)
    heute = pd.Timestamp(date.today())
    tage = pd.bdate_range(heute - pd.Timedelta(days=FENSTER[zeitraum] + 7), heute)
    preise = pd.DataFrame({t: s.reindex(s.index.union(tage)).ffill().reindex(tage) for t, s in kurse.items()})
    rendite = preise.pct_change().iloc[1:]
    min_tage = max(40, int(len(rendite) * 0.6))  # zu wenig Kurstage geben keine sinnvolle Schätzung

    mit = [p for p in pos if p["ticker"] in rendite and rendite[p["ticker"]].notna().sum() >= min_tage]
    ohne = [p["name"] for p in pos if p not in mit]
    if len(mit) < 2 or MARKT not in rendite:
        return {"fehler": "Für die Risiko-Rechnung fehlen gerade Kursdaten."}
    namen = [p["ticker"] for p in mit]
    R = rendite[namen].fillna(0.0)
    markt = rendite[MARKT].fillna(0.0)
    werte = np.array([p["wert_eur"] for p in mit])
    w = werte / werte.sum()

    # Depot als gewichtete Summe der Tagesrenditen.
    depot = R.to_numpy() @ w
    depot_s = pd.Series(depot, index=R.index)
    vol_depot = depot.std(ddof=1) * math.sqrt(TAGE_JAHR)
    var = -np.percentile(depot, VAR_NIVEAU)
    es = -depot[depot <= -var].mean()
    index = (1 + depot_s).cumprod()
    unter_wasser = _drawdown(index)

    # Zeitverläufe erst ab dem ersten Kauf zeigen: Davor gab es das Depot noch nicht.
    trades = lade_trades(b)
    seit = trades[0]["zeit"].tz_convert("Europe/Berlin").tz_localize(None).normalize() if trades else R.index[0]
    ab = max(seit, R.index[0])
    kurve = _drawdown((1 + depot_s[depot_s.index >= ab]).cumprod())
    beta_depot = float(np.cov(depot, markt)[0, 1] / markt.var(ddof=1))

    # Risikobeitrag: Welcher Anteil der Depot-Schwankung geht auf jede Position zurück?
    kov = R.cov().to_numpy() * TAGE_JAHR
    varianz = float(w @ kov @ w)
    beitrag = w * (kov @ w) / varianz

    vol = R.std(ddof=1).to_numpy() * math.sqrt(TAGE_JAHR)
    einzeln = []
    for i, p in enumerate(mit):
        t = p["ticker"]
        reihe = preise[t].dropna()
        dd = _drawdown(reihe)
        einzeln.append({
            "ticker": t, "name": p["name"], "gewicht": float(w[i] * 100),
            "vol": float(vol[i] * 100),
            "beta": float(np.cov(R[t], markt)[0, 1] / markt.var(ddof=1)),
            "max_dd": float(dd.min() * 100), "dd_jetzt": float(dd.iloc[-1] * 100),
            "beitrag": float(beitrag[i] * 100),
            "rendite": float((reihe.iloc[-1] / reihe.iloc[0] - 1) * 100),
            "schlechtester_tag": float(R[t].min() * 100), "bester_tag": float(R[t].max() * 100),
            "wert": float(werte[i]),
        })

    # Korrelationen, so sortiert, dass ähnliche Werte nebeneinander stehen (hierarchisches Clustering).
    # Der Durchschnitt (obere) zählt nur das Depot, die Matrix zeigt auch die Werte aus dem Markt.
    obere = R.corr().fillna(0.0).to_numpy()[np.triu_indices(len(namen), 1)]
    zusatz_mit = [p for p in zusatz if p["ticker"] in rendite and rendite[p["ticker"]].notna().sum() >= min_tage]
    alle_namen = namen + [p["ticker"] for p in zusatz_mit]
    korr = rendite[alle_namen].fillna(0.0).corr().fillna(0.0)
    abstand = np.clip(1 - korr.to_numpy(), 0, 2)
    np.fill_diagonal(abstand, 0)
    reihenfolge = leaves_list(linkage(squareform(abstand, checks=False), method="average")) if len(alle_namen) > 2 else list(range(len(alle_namen)))
    geordnet = [alle_namen[i] for i in reihenfolge]

    # Verteilung der Tagesrenditen für das Histogramm.
    zaehler, kanten = np.histogram(depot * 100, bins=36)
    depotwert = float(werte.sum())

    # Für die Detail-Fenster: Kursverläufe aller Positionen und des Markts, auf 100 normiert.
    def normiert(reihe: pd.Series) -> list:
        basis = reihe.dropna()
        if basis.empty:
            return []
        return [None if pd.isna(v) else round(float(v / basis.iloc[0] * 100), 3) for v in reihe]

    # Schwankung über die Zeit: rollend über rund 4 Wochen, Depot gegen Markt.
    # Gerechnet wird über den ganzen Zeitraum (auch vor dem ersten Kauf), gezeigt erst ab dem Kauf.
    def rollend(s: pd.Series) -> list:
        werte = s.rolling(ROLLEND).std()
        return [None if pd.isna(v) else round(float(v) * math.sqrt(TAGE_JAHR) * 100, 2) for v in werte[werte.index >= ab]]

    try:
        korr_rollend = rollierende_korrelation(pos, kurse)
    except Exception:
        korr_rollend = None

    return {
        "zeitraum": {"von": R.index[0].date().isoformat(), "bis": R.index[-1].date().isoformat(), "tage": len(R), "schluessel": zeitraum},
        "depot_seit": seit.date().isoformat(),
        "daten": [d.date().isoformat() for d in preise.index],
        "verlaeufe": {t: normiert(preise[t]) for t in alle_namen + [MARKT]},
        "extra": [{"ticker": p["ticker"], "name": p["name"]} for p in zusatz_mit],
        "extra_ohne_daten": [p["name"] for p in zusatz if p not in zusatz_mit],
        "rollend": {"daten": [d.date().isoformat() for d in R.index if d >= ab], "depot": rollend(depot_s), "markt": rollend(markt)},
        "markt_ticker": MARKT,
        "depot": {
            "wert": depotwert, "vol": vol_depot * 100, "beta": beta_depot,
            "var_prozent": var * 100, "var_eur": var * depotwert,
            "es_prozent": es * 100, "es_eur": es * depotwert,
            "max_dd": float(unter_wasser.min() * 100), "dd_jetzt": float(unter_wasser.iloc[-1] * 100),
            "diversifikation": float((w * vol).sum() / vol_depot),
            "korrelation_schnitt": float(obere.mean()) if len(obere) else None,
            "schlechtester_tag": float(depot.min() * 100), "bester_tag": float(depot.max() * 100),
        },
        "positionen": einzeln,
        "korrelation": {"ticker": geordnet, "werte": [[round(float(korr.loc[a, b]), 3) for b in geordnet] for a in geordnet]},
        "histogramm": {"kanten": [round(float(k), 3) for k in kanten], "anzahl": [int(z) for z in zaehler]},
        "unter_wasser": [{"d": d.date().isoformat(), "w": round(float(v) * 100, 3)} for d, v in kurve.items()],
        "korr_rollend": korr_rollend,
        "ohne_daten": ohne,
        "markt": "MSCI World (iShares, EUNL, in Euro)",
    }
