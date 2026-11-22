"""Ansichten der Fundamentalanalyse (Punkt 24c, Teil C).

- kurs_gewinn(): Kurs gegen "Gewinn je Aktie mal Durchschnitts-KGV" (oder Umsatz / Free Cashflow je Aktie
  mal Durchschnitts-Vielfaches), Zukunft aus Analystenschätzungen.
- rendite_zerlegung(): Kursrendite = Gewinnwachstum + Bewertungsänderung (+ Dividende), je Jahr und gesamt.
- streu(): beliebige Kennzahlen zweier Achsen für eine Gruppe von Werten.
- zeitreihen(): mehrere Kennzahlen oder mehrere Firmen übereinander.
- gesamtmarkt(): S&P 500: KGV mit Perzentil-Bändern, Forward-KGV, Risikoprämie.
- schaetzungs_trend(): Anteil angehobener und gesenkter Schätzungen je Sektor (aus dem täglichen Speicher).
"""
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import numpy as np
import pandas as pd

from . import basis as B
from . import speicher as SP
from .kennzahlen import bibliothek, rechnen


def _je_aktie(z: dict, feld: str) -> float | None:
    v, a = z.get(feld), z.get("aktien")
    if feld == "eps":
        return v
    return v / a if v is not None and a else None


def kurs_gewinn(ticker: str, basis: str = "gewinn", jahre: int = 10, waehrung: str = "USD", bereinigt: bool = False) -> dict:
    feld = {"gewinn": "eps", "umsatz": "umsatz", "fcf": "fcf"}[basis]
    roh = B.rohdaten(ticker)
    d = B.aufbereiten(roh, waehrung, bereinigt)
    m = roh["markt"]
    kurse = B.kursverlauf(ticker, 11)
    if d["waehrung_kurs"] != waehrung:
        kurse = pd.Series({mo: v * (B.kurs(d["waehrung_kurs"], waehrung, mo) or np.nan) for mo, v in kurse.items()}).dropna()
    ab = f"{date.today().year - jahre}-{date.today().month:02d}"
    kurse = kurse[kurse.index >= ab]
    # Je-Aktie-Werte der letzten 12 Monate, je Quartalsende
    punkte = []
    for p, z in d["ttm"].items():
        w = _je_aktie(z, feld)
        if w is not None and z.get("_ende", "")[:7] >= ab:
            punkte.append((z["_ende"][:7], w))
    if len(punkte) < 2 and d["jahr"]:  # zu wenige Quartale (nicht US): Geschäftsjahre
        punkte = [(z["_ende"][:7], _je_aktie(z, feld)) for z in d["jahr"].values() if _je_aktie(z, feld) is not None and z["_ende"][:7] >= ab]
    if not punkte or not len(kurse):
        return {"fehler": "Zu wenige Daten für diesen Chart."}
    je = pd.Series(dict(punkte)).sort_index()
    monate = list(kurse.index)
    je_monat = je.reindex(sorted(set(monate) | set(je.index))).ffill().reindex(monate)
    vielfach = (kurse / je_monat).where(je_monat > 0)
    gueltig = vielfach.dropna()
    schnitt = float(gueltig.mean()) if len(gueltig) >= 6 else None
    median = float(gueltig.median()) if len(gueltig) >= 6 else None
    linie = (je_monat * schnitt).where(je_monat > 0) if schnitt else None
    # Zukunft: Schätzungen für das laufende und das nächste Geschäftsjahr
    zukunft = []
    if schnitt and d["jahr"] and basis in ("gewinn", "umsatz"):
        letzt_gj = pd.Timestamp(list(d["jahr"].values())[-1]["_ende"])
        f = B.kurs(d["waehrung_bilanz"] if basis == "umsatz" else (m.get("financialCurrency") or "USD"), waehrung, date.today().strftime("%Y-%m")) or 1
        aktien = (list(d["ttm"].values())[-1] if d["ttm"] else list(d["jahr"].values())[-1]).get("aktien")
        for k, n in (("0y", 1), ("+1y", 2)):
            if basis == "gewinn":
                w = ((m.get("eps_trend") or {}).get("current") or {}).get(k)
            else:
                avg = ((m.get("umsatz_schaetzung") or {}).get(k) or {}).get("avg")
                w = avg / aktien if avg and aktien else None
            if w is None:
                continue
            ende = (letzt_gj + pd.DateOffset(years=n)).strftime("%Y-%m")
            if ende <= je.index[-1]:
                continue
            zukunft.append({"monat": ende, "je_aktie": w * f, "wert": w * f * schnitt if w > 0 else None})
    jetzt_vielfach = float(vielfach.dropna().iloc[-1]) if len(vielfach.dropna()) else None
    perz = float((gueltig < jetzt_vielfach).mean() * 100) if jetzt_vielfach is not None and len(gueltig) else None
    name = {"gewinn": "KGV", "umsatz": "KUV", "fcf": "Kurs/FCF"}[basis]
    return {"ticker": ticker.upper(), "basis": basis, "vielfach_name": name, "waehrung": waehrung,
            "monate": [f"{mo}-01" for mo in monate], "kurs": [round(float(v), 2) for v in kurse],
            "linie": [None if linie is None or pd.isna(v) else round(float(v), 2) for v in (linie if linie is not None else [None] * len(monate))],
            "je_aktie": [{"monat": f"{mo}-01", "wert": round(float(v), 4)} for mo, v in je.items()],
            "zukunft": [{"monat": f"{z['monat']}-01", "je_aktie": z["je_aktie"], "wert": None if z["wert"] is None else round(z["wert"], 2)} for z in zukunft],
            "schnitt": schnitt, "median": median, "jetzt": jetzt_vielfach, "perzentil": perz, "jahre": jahre,
            "negativ_monate": int((je_monat <= 0).sum()),
            "quelle": "Kurse und Schätzungen: Yahoo; Zahlen: " + ("SEC und Yahoo" if any(z.get("_quelle", "").startswith(("SEC", "Yahoo +")) for z in d["ttm"].values()) else "Yahoo")}


def rendite_zerlegung(ticker: str, von: str, bis: str | None = None, waehrung: str = "USD") -> dict:
    """Rendite = Gewinnwachstum x Bewertungsänderung (+ Dividenden), aufgeteilt in Prozentpunkte über die Logarithmen."""
    roh = B.rohdaten(ticker)
    d = B.aufbereiten(roh, waehrung, False)
    kurse = B.kursverlauf(ticker, 11)
    if d["waehrung_kurs"] != waehrung:
        kurse = pd.Series({mo: v * (B.kurs(d["waehrung_kurs"], waehrung, mo) or np.nan) for mo, v in kurse.items()}).dropna()
    eps = pd.Series({z["_ende"][:7]: z.get("eps") for z in d["ttm"].values() if z.get("eps") is not None}).sort_index()
    if len(eps) < 2 and d["jahr"]:
        eps = pd.Series({z["_ende"][:7]: z.get("eps") for z in d["jahr"].values() if z.get("eps") is not None}).sort_index()
    div = B.dividenden(ticker)
    bis = bis or kurse.index[-1]
    def stand(monat):
        k = kurse[kurse.index <= monat]
        e = eps[eps.index <= monat]
        return (float(k.iloc[-1]) if len(k) else None, float(e.iloc[-1]) if len(e) else None, k.index[-1] if len(k) else None)
    def abschnitt(a, b):
        p0, e0, m0 = stand(a)
        p1, e1, m1 = stand(b)
        if not p0 or not p1 or m0 == m1:
            return None
        d_summe = float(div[(div.index > f"{m0}-31") & (div.index <= f"{m1}-31")].sum()) if len(div) else 0.0
        f = B.kurs(d["waehrung_kurs"], waehrung, m1) or 1
        gesamt = (p1 + d_summe * f) / p0 - 1
        kurs_r = p1 / p0 - 1
        aus = {"von": m0, "bis": m1, "gesamt": gesamt * 100, "kurs": kurs_r * 100, "dividende": d_summe * f / p0 * 100,
               "eps_von": e0, "eps_bis": e1, "kgv_von": p0 / e0 if e0 and e0 > 0 else None, "kgv_bis": p1 / e1 if e1 and e1 > 0 else None}
        if e0 and e1 and e0 > 0 and e1 > 0:
            lg, lb = math.log(e1 / e0), math.log((p1 / e1) / (p0 / e0))
            summe = lg + lb
            # Prozentpunkte der Kursrendite proportional zu den Log-Anteilen aufteilen
            aus["gewinn"] = kurs_r * 100 * (lg / summe) if summe else 0.0
            aus["bewertung"] = kurs_r * 100 * (lb / summe) if summe else 0.0
            aus["gewinn_wachstum"] = (e1 / e0 - 1) * 100
            aus["bewertung_aenderung"] = ((p1 / e1) / (p0 / e0) - 1) * 100
        else:
            aus["hinweis"] = "Gewinn negativ: Zerlegung n. a."
        return aus
    gesamt = abschnitt(von, bis)
    if not gesamt:
        return {"fehler": "Für diesen Zeitraum fehlen Kurse oder Gewinne."}
    jahre = []
    for j in range(int(von[:4]), int(bis[:4]) + 1):
        a, b = max(von, f"{j - 1}-12"), min(bis, f"{j}-12")
        if a < b:
            x = abschnitt(a, b)
            if x:
                jahre.append({"jahr": j, **x})
    return {"ticker": ticker.upper(), "gesamt": gesamt, "jahre": jahre, "waehrung": waehrung,
            "hinweis": "Gewinn je Aktie der letzten 12 Monate zum jeweiligen Monatsende. Gewinn- und Bewertungsanteil teilen die Kursrendite im Verhältnis ihrer Logarithmen auf; die Dividende kommt dazu."}


def _sicher(ticker: str, **kw):
    try:
        SP.beobachten(ticker)
        return rechnen(ticker, **kw)
    except Exception as f:  # noqa: BLE001
        return {"ticker": ticker, "fehler": type(f).__name__}


def viele(tickers: list[str], **kw) -> list[dict]:
    with ThreadPoolExecutor(4) as pool:
        return list(pool.map(lambda t: _sicher(t, **kw), tickers))


def _ytd(ticker: str) -> float | None:
    try:
        k = B.kursverlauf(ticker, 2)
        vorjahr = k[k.index <= f"{date.today().year - 1}-12"]
        return (float(k.iloc[-1]) / float(vorjahr.iloc[-1]) - 1) * 100 if len(vorjahr) else None
    except Exception:  # noqa: BLE001
        return None


def streu(tickers: list[str], x: str = "wachstum_2j", y: str = "fwd_kgv", waehrung: str = "USD") -> dict:
    tickers = list(dict.fromkeys(t.upper() for t in tickers))[:60]
    daten = viele(tickers, art="ttm", waehrung=waehrung)
    bib = {k["id"]: k for k in bibliothek()["kennzahlen"]}
    with ThreadPoolExecutor(4) as pool:
        ytd = dict(zip(tickers, pool.map(_ytd, tickers)))
    punkte, fehlt = [], []
    for r in daten:
        if r.get("fehler"):
            fehlt.append(r["ticker"]); continue
        werte = {k["id"]: k["heute"] for k in r["kennzahlen"]}
        if werte.get(x) is None or werte.get(y) is None:
            fehlt.append(r["ticker"]); continue
        punkte.append({"ticker": r["ticker"], "name": r["name"], "x": werte[x], "y": werte[y], "boersenwert": werte.get("boersenwert") or (r["markt"].get("marketCap")),
                       "ytd": ytd.get(r["ticker"]), "sektor": r["sektor"]})
    return {"x": {k: bib[x][k] for k in ("id", "name", "einheit", "erklaerung")} if x in bib else {"id": x, "name": x},
            "y": {k: bib[y][k] for k in ("id", "name", "einheit", "erklaerung")} if y in bib else {"id": y, "name": y},
            "punkte": punkte, "ohne_wert": fehlt, "peg_linien": x == "wachstum_2j" and y == "fwd_kgv"}


def zeitreihen(tickers: list[str], kennzahlen: list[str], art: str = "ttm", modus: str = "absolut", waehrung: str = "USD", bereinigt: bool = False) -> dict:
    tickers = list(dict.fromkeys(t.upper() for t in tickers))[:6]
    daten = viele(tickers, art=art, modus=modus, waehrung=waehrung, bereinigt=bereinigt, perioden_anzahl=40)
    serien, perioden = [], set()
    for r in daten:
        if r.get("fehler"):
            continue
        for k in r["kennzahlen"]:
            if k["id"] in kennzahlen:
                ps = [p["id"] for p in r["perioden"]]
                perioden |= set(ps)
                serien.append({"ticker": r["ticker"], "kennzahl": k["id"], "name": f"{r['ticker']} · {k['name']}", "einheit": k["einheit"],
                               "werte": dict(zip(ps, k["werte"]))})
    achse = sorted(perioden, key=lambda p: (p[2:] + "Q9") if p.startswith("GJ") else p)
    return {"perioden": achse, "serien": [{**s, "werte": [s["werte"].get(p) for p in achse]} for s in serien]}


def gesamtmarkt() -> dict:
    from ..abruf import fred
    from ..stil import kgv, shiller
    sh = shiller()
    reihe = pd.Series([v if v is not None else np.nan for v in sh["kgv"]], index=pd.to_datetime(sh["daten"])).dropna()
    reihe = reihe[reihe > 0]
    seit = reihe[reihe.index >= "1960-01-01"]
    baender = {f"p{q}": round(float(np.percentile(seit, q)), 2) for q in (10, 25, 50, 75, 90)}
    letzte30 = reihe[reihe.index >= pd.Timestamp.today() - pd.DateOffset(years=30)]
    fwd = next((i for i in kgv()["indizes"] if i["id"] == "sp500"), None)
    fwd_jetzt = (fwd or {}).get("aktuell", {}) or {}
    zehn = fred("DGS10").dropna()
    zehn_jetzt = float(zehn.iloc[-1])
    erp = (100 / fwd_jetzt["fwd"] - zehn_jetzt) if fwd_jetzt.get("fwd") else None
    # Risikoprämie im Verlauf: Gewinnrendite (Shiller, 12 Monate) minus 10-jährige Rendite
    erp_reihe = [None if v is None else v for v in sh["erp"]]
    return {"kgv": {"daten": [d.strftime("%Y-%m-01") for d in letzte30.index], "werte": [round(float(v), 2) for v in letzte30],
                    "baender": baender, "jetzt": round(float(reihe.iloc[-1]), 2), "perzentil": round(float((seit < reihe.iloc[-1]).mean() * 100))},
            "fwd": {"jetzt": fwd_jetzt.get("fwd"), "daten": (fwd or {}).get("daten", []), "werte": (fwd or {}).get("fwd", []),
                    "abdeckung": fwd_jetzt.get("abdeckung_fwd")},
            "erp": {"jetzt": erp, "zehn": zehn_jetzt, "gewinnrendite": 100 / fwd_jetzt["fwd"] if fwd_jetzt.get("fwd") else None,
                    "daten": sh["daten"][-360:], "werte": erp_reihe[-360:], "perzentil": sh.get("erp_perzentil")},
            "quelle": "KGV der letzten 12 Monate: Robert Shiller (bis 2024) und selbst gerechnet; Perzentile seit 1960. Forward-KGV aus allen S&P-500-Werten (Yahoo), täglich seit Start gesammelt. 10-jährige Rendite: FRED (DGS10)."}


def schaetzungs_trend() -> dict:
    """Je Sektor: Anteil der Werte mit angehobener bzw. gesenkter Gewinnschätzung (nächstes Geschäftsjahr) über 1 und 3 Monate."""
    zeilen = SP.letzte_schaetzungen(index_nur=True)
    if not zeilen:
        return {"fehler": "Noch keine Schätzungen gespeichert. Der tägliche Abruf läuft im Hintergrund (gut 5 Minuten für alle S&P-500-Werte)."}
    je = {}
    for z in zeilen:
        for zeitraum, alt in (("1m", "eps1y_30"), ("3m", "eps1y_90")):
            if z["eps1y"] is None or z[alt] is None or not z[alt]:
                continue
            ver = z["eps1y"] / z[alt] - 1
            e = je.setdefault(z["sektor"] or "Sonstige", {}).setdefault(zeitraum, {"hoch": 0, "runter": 0, "gleich": 0, "werte": []})
            e["hoch" if ver > 0.005 else "runter" if ver < -0.005 else "gleich"] += 1
            e["werte"].append(ver * 100)
    aus = []
    for sektor, x in je.items():
        zeile = {"sektor": sektor}
        for zr, e in x.items():
            n = e["hoch"] + e["runter"] + e["gleich"]
            zeile[zr] = {"n": n, "hoch": e["hoch"] / n * 100, "runter": e["runter"] / n * 100, "median": float(np.median(e["werte"])), "saldo": (e["hoch"] - e["runter"]) / n * 100}
        aus.append(zeile)
    aus.sort(key=lambda z: -(z.get("3m", {}).get("saldo") or 0))
    gesamt = {}
    for zr in ("1m", "3m"):
        h = sum(z[zr]["hoch"] * z[zr]["n"] / 100 for z in aus if zr in z)
        r = sum(z[zr]["runter"] * z[zr]["n"] / 100 for z in aus if zr in z)
        n = sum(z[zr]["n"] for z in aus if zr in z)
        gesamt[zr] = {"hoch": h / n * 100 if n else None, "runter": r / n * 100 if n else None, "n": n}
    return {"sektoren": aus, "gesamt": gesamt, "stand": zeilen[0]["tag"], "quelle": "Yahoo, Gewinnschätzung je Aktie für das nächste Geschäftsjahr gegen vor 30 bzw. 90 Tagen, alle S&P-500-Werte"}
