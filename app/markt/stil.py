"""Seite "Stil & Bewertung": Value gegen Growth, Bewertung der großen Indizes, Kredit und Liquidität.

Alles Marktdaten, für alle Nutzer gleich:
- verhaeltnisse(): Kursverhältnis zweier ETFs (z. B. S&P 500 Growth / Value). Steigt die Linie,
  liegt der erste vorn.
- kgv(): Forward-KGV je Index, selbst gerechnet aus allen Mitgliedern: Summe der Börsenwerte geteilt
  durch die Summe der erwarteten Gewinne (= Börsenwert / Forward-KGV je Firma). Der Verlauf entsteht
  ab dem ersten Abruf, weil es dafür keine kostenlose Historie gibt.
- shiller(): KGV und CAPE (KGV mit 10 Jahren inflationsbereinigter Gewinne) seit 1881 aus der Datei von
  Robert Shiller. Seine Gewinne enden 2024; danach wird der Gewinn bis zum heutigen, selbst
  gerechneten Gewinn des S&P 500 verbunden (als Schätzung gekennzeichnet).
- kredit(): Risikoaufschlag von Hochzinsanleihen (FRED, ICE BofA) mit S&P 500.
- liquiditaet(): Netto-Liquidität der Fed = Bilanzsumme - Konto des Finanzministeriums - Reverse Repo.
"""
import io
from datetime import date

import pandas as pd

from ..cache import markt_lesen
from .abruf import STUNDE, TAG, als_chart, historie_eintragen, historie_lesen, fred, gecacht, hole, perzentil, schlusskurse
from .indizes import INDIZES, kennzahlen

PAARE = [
    {"id": "growth", "a": "IVW", "b": "IVE", "na": "S&P 500 Growth", "nb": "S&P 500 Value", "name": "Growth vs. Value"},
    {"id": "gleich", "a": "RSP", "b": "SPY", "na": "S&P 500 Equal Weight", "nb": "S&P 500", "name": "Equal Weight vs. Cap Weight"},
    {"id": "klein", "a": "IWM", "b": "SPY", "na": "Russell 2000", "nb": "S&P 500", "name": "Small Caps vs. Large Caps"},
    {"id": "nasdaq", "a": "QQQ", "b": "SPY", "na": "Nasdaq 100", "nb": "S&P 500", "name": "Nasdaq 100 vs. S&P 500"},
    {"id": "momentum", "a": "MTUM", "b": "SPY", "na": "MSCI USA Momentum", "nb": "S&P 500", "name": "Momentum vs. Markt"},
    {"id": "qualitaet", "a": "QUAL", "b": "SPY", "na": "MSCI USA Quality", "nb": "S&P 500", "name": "Quality vs. Markt"},
    {"id": "zyklisch", "a": "XLY", "b": "XLP", "na": "Zyklischer Konsum", "nb": "Basiskonsum", "name": "Zykliker vs. Defensive"},
    {"id": "europa", "a": "VGK", "b": "SPY", "na": "Europa", "nb": "S&P 500", "name": "Europa vs. USA"},
]
SHILLER = [
    "https://img1.wsimg.com/blobby/go/e5e77e0b-59d1-44d9-ab25-4763ac982e53/downloads/ie_data.xls",
    "http://www.econ.yale.edu/~shiller/data/ie_data.xls",
]


def _paar(p: dict) -> dict:
    a, b = schlusskurse(p["a"], "max"), schlusskurse(p["b"], "max")
    tab = pd.concat([a, b], axis=1, keys=["a", "b"]).dropna()
    tab = tab[tab.index >= pd.Timestamp(date.today().year - 10, 1, 1)]
    q = tab["a"] / tab["b"]
    ma = q.rolling(200).mean()
    # Seit wann liegt die relative Stärke auf derselben Seite ihres 200-Tage-Schnitts?
    seite = (q > ma)[ma.notna()]
    wechsel = seite[seite != seite.shift()].index
    seit = wechsel[-1] if len(wechsel) else seite.index[0]
    jahresanfang = tab[tab.index < pd.Timestamp(date.today().year, 1, 1)]

    def rendite(start):
        if start is None:
            return None
        ra, rb = tab.iloc[-1] / start - 1
        return {"a": round(float(ra) * 100, 1), "b": round(float(rb) * 100, 1), "diff": round(float(ra - rb) * 100, 1)}
    zeitraeume = {k: rendite(tab.iloc[-1 - t] if len(tab) > t else None) for k, t in (("1M", 21), ("3M", 63), ("6M", 126), ("1J", 252), ("3J", 756), ("5J", 1260))}
    zeitraeume["YTD"] = rendite(jahresanfang.iloc[-1] if len(jahresanfang) else None)
    return {**p, "daten": [d.strftime("%Y-%m-%d") for d in tab.index],
            "kurs_a": [round(float(v), 3) for v in tab["a"]], "kurs_b": [round(float(v), 3) for v in tab["b"]],
            "zeitraeume": zeitraeume,
            "lage": {"fuehrt": "a" if bool(seite.iloc[-1]) else "b", "seit": seit.strftime("%Y-%m-%d"),
                     "abstand_ma": round(float(q.iloc[-1] / ma.iloc[-1] - 1) * 100, 1),
                     "trend_ma": round(float(ma.iloc[-1] / ma.iloc[-21] - 1) * 100, 2)}}


def verhaeltnisse() -> dict:
    paare = []
    for p in PAARE:
        try:
            paare.append(_paar(p))
        except Exception:
            paare.append({**p, "fehler": "Keine Kurse von Yahoo."})
    return {"paare": paare}


# ---- Forward-KGV der Indizes ----
def _index_kgv(werte: dict) -> dict:
    gesamt = sum(w["cap"] for w in werte.values())
    ergebnis = {}
    for art in ("fwd", "ttm"):
        mit = [w for w in werte.values() if w.get(art)]
        cap = sum(w["cap"] for w in mit)
        gewinn = sum(w["cap"] / w[art] for w in mit)
        ergebnis[art] = round(cap / gewinn, 2) if gewinn > 0 else None
        ergebnis[f"abdeckung_{art}"] = round(cap / gesamt * 100, 1) if gesamt else None
    return ergebnis


def kgv_aktualisieren(index: str) -> dict | None:
    """Rechnet das KGV des Index neu und hängt es an den Verlauf an (ein Wert pro Tag). Läuft im Hintergrund."""
    wert = _index_kgv(kennzahlen(index))
    historie_eintragen(f"kgv:{index}", date.today().isoformat(), wert)
    return wert


def kgv() -> dict:
    zeilen = []
    for index, info in INDIZES.items():
        punkte = historie_lesen(f"kgv:{index}")
        tage = sorted(punkte)
        zeilen.append({"id": index, "name": info["name"], "aktuell": punkte[tage[-1]] if tage else None,
                       "stand": tage[-1] if tage else None, "daten": tage,
                       "fwd": [punkte[d]["fwd"] for d in tage], "ttm": [punkte[d]["ttm"] for d in tage]})
    return {"indizes": zeilen, "konzentration": _konzentration()}


def _konzentration() -> dict | None:
    """Die 10 größten Firmen des S&P 500 gegen die übrigen: Gewicht und Bewertung."""
    try:
        werte = markt_lesen("kennzahlen:sp500")[0]
    except Exception:
        return None
    if not werte:
        return None
    sortiert = sorted(werte.items(), key=lambda kv: kv[1]["cap"], reverse=True)
    gesamt = sum(w["cap"] for _, w in sortiert)
    top, rest = dict(sortiert[:10]), dict(sortiert[10:])
    a, b = _index_kgv(top), _index_kgv(rest)
    return {"gewicht_top": round(sum(w["cap"] for w in top.values()) / gesamt * 100, 1),
            "top": {"fwd": a["fwd"], "ttm": a["ttm"]}, "rest": {"fwd": b["fwd"], "ttm": b["ttm"]},
            "firmen": [{"ticker": t, "gewicht": round(w["cap"] / gesamt * 100, 2), "fwd": round(w["fwd"], 1) if w.get("fwd") else None} for t, w in sortiert[:10]]}


# ---- Shiller: KGV und CAPE seit 1881 ----
def _shiller_roh() -> dict:
    def holen():
        letzter_fehler = None
        for url in SHILLER:
            try:
                tab = pd.read_excel(io.BytesIO(hole(url, zeitlimit=90).content), sheet_name="Data", header=None)
                tab = tab[pd.to_numeric(tab[0], errors="coerce").notna()]
                aus = {}
                for _, z in tab.iterrows():
                    jahr, monat = str(f"{float(z[0]):.2f}").split(".")
                    aus[f"{jahr}-{monat}"] = {"p": z[1], "d": z[2], "e": z[3], "cpi": z[4], "gs10": z[6]}
                return {k: {f: (None if pd.isna(v) else float(v)) for f, v in w.items()} for k, w in aus.items()}
            except Exception as f:
                letzter_fehler = f
        raise letzter_fehler
    return gecacht("shiller2", 7 * TAG, holen)


BEREICHE = [(0, 10, "unter 10"), (10, 15, "10 bis 15"), (15, 20, "15 bis 20"), (20, 25, "20 bis 25"), (25, 30, "25 bis 30"), (30, 99, "über 30")]


def _folgerenditen(tab: pd.DataFrame) -> list[dict]:
    """Reale Gesamtrendite (mit Dividenden) der folgenden 10 Jahre je CAPE-Bereich, nur aus Shillers Originaldaten."""
    t = tab.dropna(subset=["p", "cpi"]).copy()
    t["d"] = t["d"].ffill()
    t = t[t["d"].notna()]
    wachstum = (t["p"] + t["d"] / 12) / t["p"].shift(1)
    real = wachstum.fillna(1).cumprod() / t["cpi"]
    folge = ((real.shift(-120) / real) ** (1 / 10) - 1) * 100
    real_e = t["e"] / t["cpi"]
    cape = (t["p"] / t["cpi"]) / real_e.rolling(120, min_periods=120).mean()
    paare = pd.concat([cape, folge], axis=1, keys=["cape", "folge"]).dropna()
    aus = []
    for unten, oben, name in BEREICHE:
        teil = paare[(paare["cape"] >= unten) & (paare["cape"] < oben)]["folge"]
        if len(teil) < 12:
            aus.append({"name": name, "unten": unten, "oben": oben, "anzahl": int(len(teil))})
            continue
        aus.append({"name": name, "unten": unten, "oben": oben, "anzahl": int(len(teil)),
                    "median": round(float(teil.median()), 1), "schlecht": round(float(teil.quantile(0.1)), 1),
                    "gut": round(float(teil.quantile(0.9)), 1), "negativ": round(float((teil < 0).mean() * 100))})
    return aus


def shiller() -> dict:
    roh = _shiller_roh()
    tab = pd.DataFrame.from_dict(roh, orient="index")
    tab.index = pd.PeriodIndex(tab.index, freq="M")
    tab = tab.sort_index()
    folgerenditen = _folgerenditen(tab)
    letzte_e = tab["e"].last_valid_index()
    # Bis heute verlängern: Kurs (Monatsschnitt S&P 500), Inflation (FRED), Gewinn geschätzt
    spx = schlusskurse("^GSPC", "10y").resample("ME").mean()
    spx.index = spx.index.to_period("M")
    cpi = fred("CPIAUCSL")
    cpi.index = cpi.index.to_period("M")
    zehn = fred("DGS10").resample("ME").mean()
    zehn.index = zehn.index.to_period("M")
    monate = pd.period_range(tab.index[0], pd.Timestamp.today().to_period("M"), freq="M")
    tab = tab.reindex(monate)
    tab["p"] = tab["p"].combine_first(spx) if tab["p"].notna().any() else spx
    neu = tab.index > tab["p"].last_valid_index()
    tab.loc[neu, "p"] = spx.reindex(tab.index[neu]).values
    tab["cpi"] = tab["cpi"].where(tab["cpi"].notna(), cpi.reindex(tab.index))
    tab["gs10"] = tab["gs10"].where(tab["gs10"].notna(), zehn.reindex(tab.index))
    # Heutiger Gewinn des S&P 500 aus dem selbst gerechneten KGV der letzten 12 Monate
    e_heute = None
    try:
        werte = markt_lesen("kennzahlen:sp500")[0]
        ttm = _index_kgv(werte)["ttm"] if werte else None
        e_heute = float(spx.iloc[-1]) / ttm if ttm else None
    except Exception:
        pass
    geschaetzt_ab = None
    if e_heute and letzte_e is not None and letzte_e < tab.index[-1]:
        geschaetzt_ab = str(letzte_e + 1)
        teil = tab.loc[letzte_e:, "e"].copy()
        teil.iloc[-1] = e_heute
        tab.loc[letzte_e:, "e"] = teil.interpolate().values
    tab = tab.ffill(limit=3)
    real_e = tab["e"] / tab["cpi"]
    cape = (tab["p"] / tab["cpi"]) / real_e.rolling(120, min_periods=120).mean()
    kgv_ttm = tab["p"] / tab["e"]
    erp = 100 * tab["e"] / tab["p"] - tab["gs10"]
    daten = [f"{m}-01" for m in tab.index]
    gueltig = cape.notna()
    return {
        "daten": [d for d, g in zip(daten, gueltig) if g],
        "cape": [round(float(v), 2) for v in cape[gueltig]],
        "kgv": [None if pd.isna(v) else round(float(v), 2) for v in kgv_ttm[gueltig]],
        "erp": [None if pd.isna(v) else round(float(v), 2) for v in erp[gueltig]],
        "geschaetzt_ab": geschaetzt_ab,
        "cape_perzentil": perzentil(cape, float(cape.dropna().iloc[-1])),
        "folgerenditen": folgerenditen,
        "erp_perzentil": perzentil(erp[erp.index >= pd.Period("1960-01", freq="M")], float(erp.dropna().iloc[-1])),
        "cape_schnitt": round(float(cape.mean()), 1),
        "quelle_bis": str(letzte_e) if letzte_e is not None else None,
        "zehn": round(float(fred("DGS10").dropna().iloc[-1]), 2),
    }


def ausweitung(hy: pd.Series) -> dict:
    """Warnung bei schneller Ausweitung des Hochzins-Aufschlags (Roadmap Edge 6). Reine Funktion.
    Schnell: in 21 Handelstagen (rund ein Monat) um mindestens 0,5 Prozentpunkte oder 20 %; sehr schnell ab 1 Punkt.
    Dazu, wie selten eine solche Monatsveränderung in den gelieferten Jahren war (Perzentil)."""
    monat = (hy - hy.shift(21)).dropna()
    relativ = (hy / hy.shift(21) - 1).dropna() * 100
    if monat.empty:
        return {"hy_1m": None, "warnung": None}
    m, r = float(monat.iloc[-1]), float(relativ.iloc[-1])
    stufe = "sehr schnell" if m >= 1.0 else "schnell" if m >= 0.5 or r >= 20 else None
    return {"hy_1m": round(m, 2), "hy_1m_relativ": round(r, 1), "hy_1m_perzentil": perzentil(monat, m), "warnung": stufe}


def kredit() -> dict:
    hy = fred("BAMLH0A0HYM2")
    hy = hy[hy.index >= pd.Timestamp.today() - pd.Timedelta(days=365 * 10)]
    spx = schlusskurse("^GSPC", "10y")
    spx = spx[spx.index >= hy.index[0]]
    chart = als_chart({"hy": hy, "spx": spx})
    letzter = float(hy.iloc[-1])
    vor_monat = float(hy[hy.index <= hy.index[-1] - pd.Timedelta(days=30)].iloc[-1])
    hoch = float(spx.iloc[-252:].max())
    baa = fred("BAA10Y")
    baa_w = baa.resample("W-FRI").mean().dropna()
    lang = {"daten": [d.strftime("%Y-%m-%d") for d in baa_w.index], "werte": [round(float(v), 2) for v in baa_w]}
    baa_jetzt = float(baa.iloc[-1])
    return {**chart, "aktuell": letzter, "vor_monat": vor_monat, "perzentil": perzentil(hy, letzter),
            "baa": {**lang, "aktuell": round(baa_jetzt, 2), "perzentil": perzentil(baa, baa_jetzt), "schnitt": round(float(baa.mean()), 2), "ab": baa.index[0].strftime("%Y")},
            "hy_3m": round(letzter - float(hy[hy.index <= hy.index[-1] - pd.Timedelta(days=91)].iloc[-1]), 2),
            **ausweitung(hy),
            "spx_abstand_hoch": round((float(spx.iloc[-1]) / hoch - 1) * 100, 2), "stand": hy.index[-1].strftime("%Y-%m-%d")}


def liquiditaet() -> dict:
    bilanz = fred("WALCL") / 1000          # Mio. $ -> Mrd. $
    tga = fred("WTREGEN") / 1000           # Mio. $ -> Mrd. $
    rrp = fred("RRPONTSYD")                # Mrd. $
    tage = bilanz.index
    netto = bilanz - tga.reindex(tage, method="ffill") - rrp.reindex(tage, method="ffill").fillna(0)
    netto = netto.dropna()
    netto = netto[netto.index >= pd.Timestamp.today() - pd.Timedelta(days=365 * 6)]
    spx = schlusskurse("^GSPC", "10y").reindex(netto.index, method="ffill")
    ndx = schlusskurse("^NDX", "10y").reindex(netto.index, method="ffill")  # Roadmap Edge 5: auch gegen den Nasdaq 100
    chart = als_chart({"netto": netto, "spx": spx, "ndx": ndx}, runden=2)
    w = pd.concat([netto, spx, ndx], axis=1, keys=["n", "s", "q"]).resample("W-FRI").last().dropna()
    veraenderung = w.pct_change(13).dropna()

    def korr(jahre, spalte="s"):
        teil = veraenderung[veraenderung.index >= veraenderung.index[-1] - pd.Timedelta(days=365 * jahre)]
        return round(float(teil["n"].corr(teil[spalte])), 2) if len(teil) > 20 else None
    return {**chart, "korr_1j": korr(1), "korr_3j": korr(3), "korr_6j": korr(6),
            "korr_ndx_1j": korr(1, "q"), "korr_ndx_3j": korr(3, "q"), "korr_ndx_6j": korr(6, "q"), "ndx_13w": round(float(veraenderung["q"].iloc[-1]) * 100, 1),
            "netto_13w": round(float(veraenderung["n"].iloc[-1]) * 100, 1), "spx_13w": round(float(veraenderung["s"].iloc[-1]) * 100, 1), "aktuell": round(float(netto.iloc[-1]), 1), "vor_4w": round(float(netto.iloc[-5]), 1) if len(netto) > 5 else None,
            "teile": {"bilanz": round(float(bilanz.iloc[-1]), 1), "tga": round(float(tga.iloc[-1]), 1), "rrp": round(float(rrp.iloc[-1]), 1)},
            "stand": netto.index[-1].strftime("%Y-%m-%d")}


def stil_seite() -> dict:
    aus = {}
    for name, f in (("paare", verhaeltnisse), ("kgv", kgv), ("shiller", shiller), ("kredit", kredit), ("liquiditaet", liquiditaet)):
        try:
            aus[name] = f()
        except Exception as fehler:
            aus[name] = {"fehler": f"Gerade nicht verfügbar ({type(fehler).__name__})."}
    return aus
