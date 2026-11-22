"""Punkt 35: Earnings-Reaktions-Barometer.

Wie reagieren Aktien des S&P 500 in der laufenden Berichtssaison auf übertroffene oder verfehlte Erwartungen,
verglichen mit den Saisons davor?

- Termine, Gewinn je Aktie und Überraschung: Earnings-Kalender von nasdaq.com je Tag (vergangene Tage bleiben
  gespeichert, beim ersten Aufruf werden gut 15 Monate geladen).
- Kursreaktion: Schluss am Tag nach dem Termin gegen Schluss am Tag davor (zwei Tage, damit Meldungen vor
  und nach Börsenschluss gleich behandelt werden), abzüglich der Bewegung aller S&P-500-Werte im selben
  Fenster (gleich gewichtet). Kurse aus der Tabelle aller S&P-500-Werte (indizes.sp500_kurse).
- Saison: nach dem Monat der Meldung. Januar bis März berichten über das 4. Quartal, April bis Juni über das 1. usw.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd

from .abruf import STUNDE, TAG, gecacht, hole
from .indizes import mitglieder, sp500_kurse

SCHLAGEN_AB = 1.0     # Überraschung in Prozent: ab hier "übertroffen", bis -1 "verfehlt", dazwischen "wie erwartet"
SAISONS = 5


def _zahl(text) -> float | None:
    try:
        return float(str(text).replace("$", "").replace(",", "").replace("(", "-").replace(")", ""))
    except (TypeError, ValueError):
        return None


def _tag(tag: date) -> list[dict]:
    def holen():
        zeilen = ((hole(f"https://api.nasdaq.com/api/calendar/earnings?date={tag.isoformat()}").json().get("data") or {}).get("rows")) or []
        return [{"t": z["symbol"].replace(".", "-"), "eps": _zahl(z.get("eps")), "erwartet": _zahl(z.get("epsForecast")), "ueberraschung": _zahl(z.get("surprise"))}
                for z in zeilen]
    alt = tag < date.today() - timedelta(days=2)
    return gecacht(f"earnings_tag2:{tag.isoformat()}", 3650 * TAG if alt else 6 * STUNDE, holen)


def _tag_sicher(tag: date) -> list[dict]:
    try:
        return _tag(tag)
    except Exception:
        return []  # ein fehlender Tag soll nicht alles verhindern


def _saison(tag: date) -> tuple[int, int]:
    """(Jahr, Quartal) der berichteten Zahlen."""
    q = (tag.month - 1) // 3  # 0 = Jan-Mär
    return (tag.year - 1, 4) if q == 0 else (tag.year, q)


def barometer() -> dict:
    def holen():
        kurse = sp500_kurse()
        sp = {m["ticker"] for m in mitglieder("sp500")}
        heute = date.today()
        tage = [heute - timedelta(days=d) for d in range(1, 470) if (heute - timedelta(days=d)).weekday() < 5]
        with ThreadPoolExecutor(3) as pool:  # nasdaq.com bremst sonst
            je_tag = dict(zip(tage, pool.map(_tag_sicher, tage)))
        idx = kurse.index
        zeilen = []
        for tag, liste in je_tag.items():
            pos = idx.searchsorted(pd.Timestamp(tag))
            if pos < 1 or pos + 1 >= len(idx):
                continue
            vor, nach = idx[pos - 1], idx[pos + 1]
            markt_r = float((kurse.loc[nach] / kurse.loc[vor] - 1).mean()) * 100
            for z in liste:
                t = z["t"]
                if t not in sp or t not in kurse.columns or z["ueberraschung"] is None:
                    continue
                a, b = kurse.at[vor, t], kurse.at[nach, t]
                if pd.isna(a) or pd.isna(b) or not a:
                    continue
                r = (b / a - 1) * 100
                u = z["ueberraschung"]
                zeilen.append({"t": t, "tag": tag.isoformat(), "saison": _saison(tag), "ueberraschung": u, "reaktion": r - markt_r, "roh": r,
                               "art": "schlagen" if u >= SCHLAGEN_AB else "verfehlen" if u <= -SCHLAGEN_AB else "erwartet"})
        if len(zeilen) < 200:
            raise ValueError("zu wenige Daten")
        tab = pd.DataFrame(zeilen)
        namen = {m["ticker"]: m["name"] for m in mitglieder("sp500")}
        saisons = []
        for (jahr, q), g in sorted(tab.groupby("saison"), key=lambda x: x[0], reverse=True)[:SAISONS]:
            def mittel(art):
                teil = g[g["art"] == art]["reaktion"]
                return {"n": int(len(teil)), "schnitt": round(float(teil.mean()), 2) if len(teil) else None, "median": round(float(teil.median()), 2) if len(teil) else None,
                        "steigt": round(float((teil > 0).mean() * 100)) if len(teil) else None}
            saisons.append({"name": f"Q{q} {jahr}", "jahr": int(jahr), "quartal": int(q), "n": int(len(g)),
                            "von": g["tag"].min(), "bis": g["tag"].max(),
                            "anteil_schlagen": round(float((g["art"] == "schlagen").mean() * 100)),
                            "ueberraschung_median": round(float(g["ueberraschung"].median()), 1),
                            "schlagen": mittel("schlagen"), "verfehlen": mittel("verfehlen"), "erwartet": mittel("erwartet"),
                            "ausschlag": round(float(g["reaktion"].abs().mean()), 2)})
        groesste = max(x["n"] for x in saisons)
        for x in saisons:
            x["vollstaendig"] = x["n"] >= 0.6 * groesste or (x["jahr"], x["quartal"]) == _saison(heute)
        aktuell = tab[tab["saison"] == _saison(heute)] if (tab["saison"] == _saison(heute)).any() else tab[tab["saison"] == tab["saison"].max()]
        auffaellig = aktuell[(aktuell["art"] == "schlagen") & (aktuell["reaktion"] <= -5)].sort_values("reaktion").head(8)
        belohnt = aktuell[(aktuell["art"] == "verfehlen") & (aktuell["reaktion"] >= 5)].sort_values("reaktion", ascending=False).head(8)
        liste = lambda t: [{"ticker": z.t, "name": namen.get(z.t, z.t), "tag": z.tag, "ueberraschung": round(z.ueberraschung, 1), "reaktion": round(z.reaktion, 1)} for z in t.itertuples()]
        return {"saisons": saisons, "geschlagen_gefallen": liste(auffaellig), "verfehlt_gestiegen": liste(belohnt), "stand": heute.isoformat()}
    return gecacht("earnings_barometer2", 6 * STUNDE, holen)
