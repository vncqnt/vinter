"""Seite "Geldfluss": Fließt gerade Geld in Aktien hinein oder wird Liquidität abgezogen?

Kein einzelner Wert zeigt das direkt. Darum fünf unabhängige Messungen, jede mit langer Historie
und der Frage, was danach am Aktienmarkt geschah:
- Notenbank-Liquidität: Fed-Bilanz - Konto des Finanzministeriums (TGA) - Reverse Repo (FRED).
- Geparktes Bargeld: Vermögen der Geldmarktfonds für Privatanleger (FRED WRMFNS, seit 1980),
  dazu der aktuelle Stand aller Geldmarktfonds vom Fondsverband ICI.
- Kredit auf Aktien: Margin-Schulden der Kunden bei US-Brokern (FINRA, monatlich seit 1997).
- Kauf- und Verkaufsdruck: Chaikin Money Flow und Auf-/Ab-Volumen des SPY (Yahoo, seit 1993).
- Fondsflüsse: Zu- und Abflüsse aller US-Investmentfonds und ETFs nach Anlageklasse (ICI, nur
  rund zwei Jahre öffentlich; wird ab dem ersten Abruf weiter gesammelt).
Dazu ein Geldfluss-Index aus den vier Messungen mit langer Historie: jede als z-Wert gegenüber den
eigenen letzten drei Jahren (nur Daten, die damals schon bekannt waren), dann gemittelt.
"""
import io
from datetime import date

import numpy as np
import pandas as pd
import yfinance as yf

from .abruf import STUNDE, TAG, fred, gecacht, historie_eintragen, historie_lesen, hole, perzentil, schlusskurse

FINRA = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
ICI_FLUSS = "https://www.ici.org/combined_flows_data_{jahr}.xls"
ICI_MMF = "https://www.ici.org/mm_summary_data_{jahr}.xls"
FLUSS_SPALTEN = {1: "gesamt", 3: "aktien", 5: "aktien_us", 7: "aktien_welt", 9: "misch", 11: "anleihen", 15: "anleihen_kommunal", 17: "rohstoffe"}
RISIKO_PAARE = [
    {"id": "anleihen", "a": "SPY", "b": "TLT", "name": "Aktien gegen lange Staatsanleihen"},
    {"id": "gold", "a": "SPY", "b": "GLD", "name": "Aktien gegen Gold"},
    {"id": "bargeld", "a": "SPY", "b": "BIL", "name": "Aktien gegen Geldmarkt (T-Bills)"},
    {"id": "kredit", "a": "HYG", "b": "IEF", "name": "Hochzinsanleihen gegen Staatsanleihen"},
    {"id": "klein", "a": "IWM", "b": "SPY", "name": "Nebenwerte gegen große Werte"},
]


# ---------- Rohdaten ----------

def _ohlcv(ticker: str) -> pd.DataFrame:
    def holen():
        h = yf.Ticker(ticker).history(period="max", auto_adjust=True)[["High", "Low", "Close", "Volume"]].dropna()
        if h.empty:
            raise ValueError(f"keine Kurse für {ticker}")
        return {"daten": [d.strftime("%Y-%m-%d") for d in h.index], **{k: [float(v) for v in h[k]] for k in h.columns}}
    d = gecacht(f"ohlcv:{ticker}", 6 * STUNDE, holen)
    tab = pd.DataFrame({k: d[k] for k in ("High", "Low", "Close", "Volume")}, index=pd.to_datetime(d["daten"]))
    return tab


def _excel(url_vorlage: str, **lesen) -> pd.DataFrame:
    """ICI legt die Datei unter der Jahreszahl ab; am Jahresanfang gibt es die neue evtl. noch nicht."""
    for jahr in (date.today().year, date.today().year - 1):
        try:
            r = hole(url_vorlage.format(jahr=jahr))
            return pd.read_excel(io.BytesIO(r.content), header=None, **lesen)
        except Exception:
            continue
    raise ValueError("ICI-Datei nicht erreichbar")


def _finra() -> pd.DataFrame:
    def holen():
        tab = pd.read_excel(io.BytesIO(hole(FINRA).content), header=None)
        aus = {}
        for _, z in tab.iloc[1:].iterrows():
            try:
                aus[str(z[0])[:7]] = [float(z[1]), float(z[2]), None if pd.isna(z[3]) else float(z[3])]
            except (TypeError, ValueError):
                continue
        return aus
    d = gecacht("geldfluss:finra", 24 * STUNDE, holen)
    tab = pd.DataFrame.from_dict(d, orient="index", columns=["schulden", "bar_konto", "bar_marge"])
    tab.index = pd.to_datetime(tab.index) + pd.offsets.MonthEnd(0)
    return tab.sort_index()


def _ici_fluss() -> dict:
    """Monatliche und wöchentliche Fondsflüsse (Mio. $). Wochenwerte werden zusätzlich gesammelt."""
    def holen():
        tab = _excel(ICI_FLUSS)
        teil, aus = None, {"monat": {}, "woche": {}}
        for _, z in tab.iterrows():
            kopf = str(z[0]).strip().lower()
            if kopf.startswith("monthly"):
                teil = "monat"; continue
            if kopf.startswith("estimated weekly"):
                teil = "woche"; continue
            if not teil:
                continue
            try:
                tag = pd.to_datetime(str(z[0]).strip(), format="%m/%d/%Y").strftime("%Y-%m-%d")
            except Exception:
                continue
            aus[teil][tag] = {n: (None if pd.isna(z[i]) else float(z[i])) for i, n in FLUSS_SPALTEN.items()}
        if not aus["woche"]:
            raise ValueError("keine Wochenwerte")
        return aus
    d = gecacht("geldfluss:ici-fluss2", 12 * STUNDE, holen)
    for tag, w in d["woche"].items():
        historie_eintragen("ici:fluss:woche", tag, w)
    for tag, w in d["monat"].items():
        historie_eintragen("ici:fluss:monat", tag, w)
    return {"monat": historie_lesen("ici:fluss:monat"), "woche": historie_lesen("ici:fluss:woche")}


def _ici_mmf() -> dict:
    """Vermögen aller US-Geldmarktfonds je Woche (Mio. $), gesammelt."""
    def holen():
        tab = _excel(ICI_MMF)
        aus = {}
        for _, z in tab.iterrows():
            try:
                tag = pd.to_datetime(str(z[0]).strip(), format="%m/%d/%Y").strftime("%Y-%m-%d")
                aus[tag] = float(z[2])
            except Exception:
                continue
        # Die erste Zeile ist das Datum der Veröffentlichung ohne Werte
        aus = {k: v for k, v in aus.items() if v and v > 1e5}
        if not aus:
            raise ValueError("keine Geldmarktdaten")
        return aus
    d = gecacht("geldfluss:ici-mmf2", 12 * STUNDE, holen)
    for tag, w in d.items():
        historie_eintragen("ici:mmf", tag, w)
    return historie_lesen("ici:mmf")


# ---------- Statistik ----------

def _woche(s: pd.Series) -> pd.Series:
    return s.resample("W-FRI").last()


def _z(s: pd.Series, fenster: int = 156, mindest: int = 52) -> pd.Series:
    """z-Wert gegenüber dem eigenen Verlauf der letzten drei Jahre (nur Vergangenheit, kein Blick nach vorn)."""
    m = s.rolling(fenster, min_periods=mindest).mean()
    sd = s.rolling(fenster, min_periods=mindest).std()
    return (s - m) / sd


def _vorwaerts(spx_w: pd.Series) -> pd.DataFrame:
    """Veränderung des S&P 500 in den folgenden 4, 13 und 26 Wochen und der größte Rücksetzer in 13 Wochen."""
    aus = pd.DataFrame(index=spx_w.index)
    for n in (4, 13, 26):
        aus[f"r{n}"] = (spx_w.shift(-n) / spx_w - 1) * 100
    tief = spx_w[::-1].rolling(13, min_periods=13).min()[::-1].shift(-1)
    aus["dd13"] = (tief / spx_w - 1).clip(upper=0) * 100
    return aus


def _folge(signal: pd.Series, vor: pd.DataFrame, gruppen: int, namen: list[str]) -> dict:
    """Wochen nach der Höhe des Signals in gleich große Gruppen teilen; je Gruppe, was danach geschah."""
    tab = pd.concat([signal.rename("s"), vor], axis=1).dropna(subset=["s"])
    bekannt = tab.dropna(subset=["r13"])
    if len(bekannt) < gruppen * 20:
        return {}
    grenzen = np.quantile(bekannt["s"], np.linspace(0, 1, gruppen + 1))
    grenzen[0], grenzen[-1] = -np.inf, np.inf
    tab["g"] = pd.cut(tab["s"], grenzen, labels=False, include_lowest=True)

    def zeile(t, name, von=None, bis=None):
        r = {"name": name, "anzahl": int(t["r13"].notna().sum())}
        for k in ("r4", "r13", "r26", "dd13"):
            v = t[k].dropna()
            r[k] = None if v.empty else round(float(v.mean()), 2)
        r13 = t["r13"].dropna()
        r["steigt13"] = None if r13.empty else round(float((r13 > 0).mean() * 100))
        if von is not None:
            r["von"], r["bis"] = von, bis
        return r
    zeilen = []
    for g in range(gruppen):
        t = tab[tab["g"] == g]
        werte = bekannt["s"][(bekannt["s"] >= grenzen[g]) & (bekannt["s"] <= grenzen[g + 1])]
        zeilen.append(zeile(t, namen[g], round(float(werte.min()), 2), round(float(werte.max()), 2)))
    alle = zeile(tab, "Alle Wochen")
    korr = bekannt["s"].corr(bekannt["r13"], method="spearman")
    aktuell = tab["g"].iloc[-1]
    return {"gruppen": zeilen, "alle": alle, "aktuell": None if pd.isna(aktuell) else int(aktuell),
            "korr13": round(float(korr), 2), "ab": bekannt.index[0].strftime("%Y"), "wochen": len(bekannt)}


def _reihe(s: pd.Series, runden: int = 2) -> list:
    return [None if pd.isna(v) else round(float(v), runden) for v in s]


def _baustein(name: str, signal: pd.Series, spx_w: pd.Series, vor: pd.DataFrame, extra: dict | None = None, jahre: int = 10) -> dict:
    """Ein Messwert: Verlauf (wöchentlich, mit S&P 500), Stand, Einordnung und Statistik danach."""
    signal = signal.dropna()
    z = _z(signal)
    sicht = signal[signal.index >= signal.index[-1] - pd.Timedelta(days=365 * jahre)]
    jetzt = float(signal.iloc[-1])
    return {"name": name, "daten": [d.strftime("%Y-%m-%d") for d in sicht.index], "werte": _reihe(sicht),
            "spx": _reihe(spx_w.reindex(sicht.index, method="ffill"), 0),
            "aktuell": round(jetzt, 2), "z": None if pd.isna(z.iloc[-1]) else round(float(z.iloc[-1]), 2),
            "perzentil": perzentil(signal, jetzt), "vor4": round(float(signal.iloc[-5]), 2) if len(signal) > 5 else None,
            "stand": min(signal.index[-1], pd.Timestamp.today().normalize()).strftime("%Y-%m-%d"),
            "folge": _folge(signal, vor, 3, ["Unteres Drittel", "Mittleres Drittel", "Oberes Drittel"]), **(extra or {})}


# ---------- Bausteine ----------

def _liquiditaet(spx_w, vor):
    bilanz = fred("WALCL") / 1000
    tga = fred("WTREGEN") / 1000
    rrp = fred("RRPONTSYD")
    tage = bilanz.index
    netto = (bilanz - tga.reindex(tage, method="ffill") - rrp.reindex(tage, method="ffill").fillna(0)).dropna()
    w = _woche(netto)
    signal = (w / w.shift(13) - 1) * 100
    return _baustein("Notenbank-Liquidität", signal, spx_w, vor, {
        "einheit": "13-Wochen-Veränderung in %", "stand_mrd": round(float(netto.iloc[-1]), 0),
        "mrd_13w": round(float(w.iloc[-1] - w.iloc[-14]), 0),
        "teile": {"bilanz": round(float(bilanz.iloc[-1])), "tga": round(float(tga.iloc[-1])), "rrp": round(float(rrp.iloc[-1]))},
        "niveau": {"daten": [d.strftime("%Y-%m-%d") for d in w.index[-520:]], "werte": _reihe(w.iloc[-520:], 0),
                   "spx": _reihe(spx_w.reindex(w.index[-520:], method="ffill"), 0)}})


def _bargeld(spx_w, vor):
    retail = fred("WRMFNS")
    # FRED-Daten erscheinen gut eine Woche nach dem Stichtag: um 2 Wochen versetzt, damit die
    # Statistik nur mit Werten rechnet, die damals schon bekannt waren.
    w = _woche(retail).ffill()
    signal = ((w / w.shift(13) - 1) * 100).shift(2)
    gesamt = pd.Series(_ici_mmf()).sort_index()
    gesamt.index = pd.to_datetime(gesamt.index)
    gesamt = gesamt / 1000  # Mrd. $
    extra = {"einheit": "13-Wochen-Veränderung in %", "retail_13w": round(float((w.iloc[-1] / w.iloc[-14] - 1) * 100), 2),
             "retail_stand": round(float(w.iloc[-1])), "retail_datum": retail.index[-1].strftime("%Y-%m-%d")}
    if len(gesamt) >= 2:
        extra["ici"] = {"daten": [d.strftime("%Y-%m-%d") for d in gesamt.index], "werte": _reihe(gesamt, 1),
                        "stand": round(float(gesamt.iloc[-1])), "woche": round(float(gesamt.iloc[-1] - gesamt.iloc[-2]), 1),
                        "vier": round(float(gesamt.iloc[-1] - gesamt.iloc[-5]), 1) if len(gesamt) >= 5 else None,
                        "datum": gesamt.index[-1].strftime("%Y-%m-%d")}
    # Bargeld im Verhältnis zum Aktienmarkt: Geldmarktfonds der Privatanleger geteilt durch den S&P 500
    quote = (w / spx_w.reindex(w.index, method="ffill")).dropna()
    extra["quote_perzentil"] = perzentil(quote, float(quote.iloc[-1]))
    return _baustein("Geparktes Bargeld", signal, spx_w, vor, extra)


def _marge(spx_w, vor):
    m = _finra()
    schulden = m["schulden"]
    jahr = (schulden / schulden.shift(12) - 1) * 100
    # FINRA meldet etwa drei Wochen nach Monatsende: einen Monat später einsetzen.
    bekannt = jahr.copy()
    bekannt.index = bekannt.index + pd.offsets.MonthEnd(1)
    w = bekannt.resample("W-FRI").last().ffill()
    w = w[w.index <= spx_w.index[-1]]
    netto = (m["schulden"] - m["bar_konto"] - m["bar_marge"].fillna(0)) / 1000
    real = schulden / 1000
    spx_m = spx_w.resample("ME").last()
    anteil = (real / spx_m.reindex(real.index, method="ffill")).dropna()
    return _baustein("Kredit auf Aktien", w, spx_w, vor, {
        "einheit": "Veränderung zum Vorjahr in %", "monat": m.index[-1].strftime("%Y-%m"),
        "stand_mrd": round(float(real.iloc[-1])), "jahr": round(float(jahr.iloc[-1]), 1),
        "monat_veraenderung": round(float((schulden.iloc[-1] / schulden.iloc[-2] - 1) * 100), 1),
        "hoch_mrd": round(float(real.max())), "hoch_monat": real.idxmax().strftime("%Y-%m"),
        "netto_mrd": round(float(netto.iloc[-1])),
        "monate": {"daten": [d.strftime("%Y-%m") for d in real.index[-120:]], "schulden": _reihe(real.iloc[-120:], 0),
                   "jahr": _reihe(jahr.iloc[-120:], 1), "spx": _reihe(spx_m.reindex(real.index[-120:], method="ffill"), 0)},
        "anteil_perzentil": perzentil(anteil, float(anteil.iloc[-1]))})


def _druck(spx_w, vor):
    t = _ohlcv("SPY")
    spanne = (t["High"] - t["Low"]).replace(0, np.nan)
    mfm = ((t["Close"] - t["Low"]) - (t["High"] - t["Close"])) / spanne
    cmf = (mfm * t["Volume"]).rolling(20).sum() / t["Volume"].rolling(20).sum()
    auf = t["Close"].diff() > 0
    vol_auf = t["Volume"].where(auf, 0).rolling(20).sum()
    vol_ab = t["Volume"].where(~auf, 0).rolling(20).sum()
    verhaeltnis = (vol_auf / vol_ab)
    signal = _woche(cmf * 100)
    tage = t.index[-260:]
    return _baustein("Kauf- und Verkaufsdruck", signal, spx_w, vor, {
        "einheit": "Chaikin Money Flow (20 Tage) × 100",
        "auf_ab": round(float(verhaeltnis.iloc[-1]), 2), "auf_ab_perzentil": perzentil(np.log(verhaeltnis.dropna()), float(np.log(verhaeltnis.iloc[-1]))),
        "auf_ab_folge": _folge(np.log(_woche(verhaeltnis)), vor, 3, ["Unteres Drittel", "Mittleres Drittel", "Oberes Drittel"]),
        "tage": {"daten": [d.strftime("%Y-%m-%d") for d in tage], "cmf": _reihe(cmf.reindex(tage) * 100, 1),
                 "auf_ab": _reihe(verhaeltnis.reindex(tage), 2), "kurs": _reihe(t["Close"].reindex(tage), 2)}})


def _fonds() -> dict:
    roh = _ici_fluss()

    def tabelle(d):
        tab = pd.DataFrame.from_dict(d, orient="index").sort_index()
        tab.index = pd.to_datetime(tab.index)
        return tab
    woche, monat = tabelle(roh["woche"]), tabelle(roh["monat"])
    # Sondereffekte: Umschichtungen zwischen Fondsklassen (z. B. Fonds wird zu ETF) erscheinen als
    # riesiger Abfluss. Werte, die mehr als das Achtfache des typischen Betrags ausmachen, werden markiert.
    def markieren(tab, spalte):
        typisch = tab[spalte].abs().median()
        return (tab[spalte].abs() > 8 * typisch) if typisch else pd.Series(False, index=tab.index)
    aus = {"woche": {"daten": [d.strftime("%Y-%m-%d") for d in woche.index], **{k: _reihe(woche[k] / 1000, 1) for k in woche.columns},
                     "sonder": [bool(x) for x in (markieren(woche, "aktien") | markieren(woche, "gesamt"))]},
           "monat": {"daten": [d.strftime("%Y-%m") for d in monat.index], **{k: _reihe(monat[k] / 1000, 1) for k in monat.columns},
                     "sonder": [bool(x) for x in (markieren(monat, "aktien") | markieren(monat, "gesamt"))]}}
    sauber = woche[~(markieren(woche, "aktien") | markieren(woche, "gesamt"))]
    summen = {}
    for n in (4, 8):
        teil = sauber[sauber.index > woche.index[-1] - pd.Timedelta(weeks=n)]
        summen[str(n)] = {k: round(float(teil[k].sum()) / 1000, 1) for k in ("aktien", "aktien_us", "aktien_welt", "anleihen", "rohstoffe", "gesamt")}
    msauber = monat[~(markieren(monat, "aktien") | markieren(monat, "gesamt"))]
    aus["summen"] = summen
    aus["monat_schnitt"] = {k: round(float(msauber[k].mean()) / 1000, 1) for k in ("aktien", "aktien_us", "aktien_welt", "anleihen", "rohstoffe")}
    aus["stand"] = woche.index[-1].strftime("%Y-%m-%d")
    aus["ab"] = monat.index[0].strftime("%Y-%m") if len(monat) else None
    return aus


def _risiko() -> list[dict]:
    aus = []
    for p in RISIKO_PAARE:
        a, b = schlusskurse(p["a"], "max"), schlusskurse(p["b"], "max")
        tab = pd.concat([a, b], axis=1, keys=["a", "b"]).dropna()
        tab = tab[tab.index >= tab.index[-1] - pd.Timedelta(days=365 * 15)]
        q = tab["a"] / tab["b"]

        def rel(n):
            return round(float((q.iloc[-1] / q.iloc[-1 - n] - 1) * 100), 2) if len(q) > n else None
        vier = (q / q.shift(20) - 1) * 100
        sicht = q.iloc[-260:]
        aus.append({**p, "w1": rel(5), "w4": rel(20), "w13": rel(63), "w52": rel(252),
                    "perzentil4": perzentil(vier, float(vier.iloc[-1])),
                    "ueber200": bool(q.iloc[-1] > q.rolling(200).mean().iloc[-1]),
                    "daten": [d.strftime("%Y-%m-%d") for d in sicht.index], "werte": _reihe(sicht / sicht.iloc[0] * 100, 2)})
    return aus


# ---------- Seite ----------

def geldfluss_seite() -> dict:
    def holen():
        spx = schlusskurse("^GSPC", "max")
        spx_w = _woche(spx).dropna()
        vor = _vorwaerts(spx_w)
        aus = {}
        for name, f in (("liquiditaet", _liquiditaet), ("bargeld", _bargeld), ("marge", _marge), ("druck", _druck)):
            try:
                aus[name] = f(spx_w, vor)
            except Exception as fehler:
                aus[name] = {"fehler": f"Gerade nicht verfügbar ({type(fehler).__name__})."}
        # Geldfluss-Index: Signale erneut als volle Reihe holen (die Bausteine zeigen nur 10 Jahre)
        reihen = {}
        try:
            bilanz, tga, rrp = fred("WALCL") / 1000, fred("WTREGEN") / 1000, fred("RRPONTSYD")
            n = (bilanz - tga.reindex(bilanz.index, method="ffill") - rrp.reindex(bilanz.index, method="ffill").fillna(0)).dropna()
            w = _woche(n)
            reihen["liquiditaet"] = (w / w.shift(13) - 1) * 100
        except Exception:
            pass
        try:
            w = _woche(fred("WRMFNS")).ffill()
            reihen["bargeld"] = -((w / w.shift(13) - 1) * 100).shift(2)
        except Exception:
            pass
        try:
            s = _finra()["schulden"]
            j = (s / s.shift(12) - 1) * 100
            j.index = j.index + pd.offsets.MonthEnd(1)
            reihen["marge"] = j.resample("W-FRI").last().ffill()
        except Exception:
            pass
        try:
            t = _ohlcv("SPY")
            mfm = ((t["Close"] - t["Low"]) - (t["High"] - t["Close"])) / (t["High"] - t["Low"]).replace(0, np.nan)
            reihen["druck"] = _woche((mfm * t["Volume"]).rolling(20).sum() / t["Volume"].rolling(20).sum() * 100)
        except Exception:
            pass
        zs = pd.DataFrame({k: _z(v.dropna()) for k, v in reihen.items()}).sort_index().ffill(limit=8).reindex(spx_w.index)
        zs = zs.clip(-3, 3)
        index = zs.mean(axis=1).where(zs.notna().sum(axis=1) >= 3).dropna()
        index_glatt = index.rolling(4, min_periods=1).mean()
        sicht = index_glatt[index_glatt.index >= index_glatt.index[-1] - pd.Timedelta(days=365 * 20)]
        jetzt = zs.dropna(how="all").iloc[-1]
        aus["index"] = {
            "daten": [d.strftime("%Y-%m-%d") for d in sicht.index], "werte": _reihe(sicht), "spx": _reihe(spx_w.reindex(sicht.index), 0),
            "aktuell": round(float(index_glatt.iloc[-1]), 2), "vor4": round(float(index_glatt.iloc[-5]), 2), "vor13": round(float(index_glatt.iloc[-14]), 2),
            "perzentil": perzentil(index_glatt, float(index_glatt.iloc[-1])),
            "teile": {k: (None if pd.isna(v) else round(float(v), 2)) for k, v in jetzt.items()},
            "folge": _folge(index_glatt, vor, 5, ["Starker Abfluss", "Abfluss", "Neutral", "Zufluss", "Starker Zufluss"]),
            "ab": index.index[0].strftime("%Y")}
        try:
            aus["fonds"] = _fonds()
        except Exception as fehler:
            aus["fonds"] = {"fehler": f"Die ICI-Fondsflüsse sind gerade nicht verfügbar ({type(fehler).__name__})."}
        try:
            aus["risiko"] = _risiko()
        except Exception as fehler:
            aus["risiko"] = {"fehler": f"Gerade nicht verfügbar ({type(fehler).__name__})."}
        aus["spx_stand"] = spx_w.index[-1].strftime("%Y-%m-%d")
        return aus
    return gecacht("geldfluss:4", 6 * STUNDE, holen)
