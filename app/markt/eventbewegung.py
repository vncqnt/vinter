"""Eingepreiste Event-Bewegung (Roadmap Edge 2): Wie stark soll sich der SPY an CPI-, Fed- und Arbeitsmarkt-Tagen
bewegen, und wie stark hat er sich an den letzten 12 Terminen tatsächlich bewegt?

Eingepreist: SPY hat tägliche Verfälle. Der Am-Geld-Straddle (Call + Put) eines Verfalls kostet ungefähr die
erwartete absolute Bewegung bis dahin. Die Bewegung genau am Ereignistag ergibt sich aus der Differenz der
Varianzen: sqrt(Straddle(Ereignistag)² − Straddle(Handelstag davor)²) geteilt durch den Kurs. Darin steckt auch
die normale Schwankung eines Tages; zum Vergleich steht deshalb die übliche Tagesbewegung daneben.
Tatsächlich: Schluss am Vortag bis Schluss am Ereignistag (CPI und Arbeitsmarkt kommen um 14:30 deutscher Zeit
vor der Eröffnung, die Fed um 20 Uhr vor dem Schluss).
Termine: veröffentlichte Berichte aus dem Archiv des US-Arbeitsministeriums (bls.gov, Datum im Dateinamen),
Fed aus dem FOMC-Kalender (fed_tage.py), künftige aus makro_termine.yaml.
"""
import math
import re
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from .abruf import BROWSER, gecacht, historie_eintragen, historie_lesen, hole, schlusskurse

ARTEN = {"cpi": "CPI", "nfp": "Arbeitsmarkt", "fed": "Fed"}
ARCHIV = {"cpi": "https://www.bls.gov/bls/news-release/cpi.htm", "nfp": "https://www.bls.gov/bls/news-release/empsit.htm"}
DATEI = {"cpi": "cpi", "nfp": "empsit"}
ANZAHL = 12


def termine_aus_archiv(seite: str, datei: str) -> list[str]:
    """Veröffentlichungstage aus einer BLS-Archivseite (auskommentierte, künftige Einträge zählen nicht). Reine Funktion."""
    ohne_kommentare = re.sub(r"<!--.*?-->", "", seite, flags=re.S)
    tage = set()
    for mm, tt, jjjj in re.findall(rf"archives/{datei}_(\d{{2}})(\d{{2}})(\d{{4}})\.htm", ohne_kommentare):
        tage.add(f"{jjjj}-{mm}-{tt}")
    return sorted(tage)


def vergangene_termine() -> dict[str, list[str]]:
    def holen():
        aus = {}
        for art, url in ARCHIV.items():
            aus[art] = termine_aus_archiv(hole(url).text, DATEI[art])
        from .fed_tage import entscheidungen
        aus["fed"] = [t["datum"] for t in entscheidungen() if not t["notsitzung"]]
        return aus
    return gecacht("event:termine", 7 * 86400, holen)


def kuenftige_termine() -> list[dict]:
    from .makro import makro_termine
    heute = date.today().isoformat()
    aus = []
    for t in makro_termine().get("termine", []):
        art = "cpi" if "CPI" in t["name"] else "nfp" if "Arbeitsmarkt" in t["name"] else "fed" if "Fed-Zinsentscheid" in t["name"] else None
        if art and t["datum"] >= heute:
            aus.append({"art": art, "datum": t["datum"], "name": t["name"]})
    return sorted(aus, key=lambda x: x["datum"])[:8]


def straddle(kette, kurs: float) -> float | None:
    """Preis des Am-Geld-Straddles (Mitte aus Geld und Brief, Strike am nächsten am Kurs). Reine Funktion."""
    c, p = kette.calls, kette.puts
    paare = c.merge(p, on="strike", suffixes=("_c", "_p"))
    paare = paare[(paare["bid_c"] > 0) & (paare["ask_c"] > 0) & (paare["bid_p"] > 0) & (paare["ask_p"] > 0)]
    if paare.empty:
        return None
    z = paare.iloc[(paare["strike"] - kurs).abs().argsort().iloc[0]]
    return float((z["bid_c"] + z["ask_c"]) / 2 + (z["bid_p"] + z["ask_p"]) / 2)


def event_bewegung(s_tag: float, s_davor: float | None, kurs: float) -> float | None:
    """Eingepreiste Bewegung am Ereignistag in Prozent. Reine Funktion."""
    if not s_tag:
        return None
    rest = s_tag ** 2 - (s_davor or 0) ** 2
    return math.sqrt(max(rest, 0)) / kurs * 100


def seite() -> dict:
    def holen():
        termine = vergangene_termine()
        spy = schlusskurse("SPY", "5y")
        r = spy.pct_change() * 100
        heute = pd.Timestamp(date.today())
        alle_ereignisse = set()
        tatsaechlich = {}
        for art, liste in termine.items():
            zeilen = []
            for d in sorted(liste, reverse=True):
                ts = pd.Timestamp(d)
                if ts > heute or ts not in r.index or pd.isna(r.get(ts)):
                    continue
                zeilen.append({"datum": d, "bewegung": round(float(r[ts]), 3)})
                if len(zeilen) == ANZAHL:
                    break
            alle_ereignisse.update(liste)
            betraege = [abs(z["bewegung"]) for z in zeilen]
            # Gespeicherte eingepreiste Bewegung vom Vortag des Termins (ab Beginn der Aufzeichnung)
            gemerkt = historie_lesen("event:eingepreist")
            for z in zeilen:
                if z["datum"] in gemerkt:
                    z["eingepreist"] = gemerkt[z["datum"]]["bewegung"]
            tatsaechlich[art] = {"zeilen": zeilen, "schnitt_betrag": round(sum(betraege) / len(betraege), 3) if betraege else None,
                                 "groesste": max(zeilen, key=lambda z: abs(z["bewegung"])) if zeilen else None}
        jahr = r[r.index >= heute - pd.Timedelta(days=365)]
        normal = jahr[~jahr.index.strftime("%Y-%m-%d").isin(alle_ereignisse)].abs()
        ueblich = round(float(normal.mean()), 3)

        # Eingepreist für die kommenden Termine
        t = yf.Ticker("SPY")
        verfaelle = list(t.options or [])
        kurs = float(spy.iloc[-1])
        ketten = {}

        def preis(v):
            if v not in ketten:
                try:
                    ketten[v] = straddle(t.option_chain(v), kurs)
                except Exception:
                    ketten[v] = None
            return ketten[v]
        kommend = []
        alle_kuenftig = kuenftige_termine()
        heute_iso = date.today().isoformat()
        # Was der Markt für einen gewöhnlichen Tag einpreist: aufeinanderfolgende Tagesverfälle ohne Termin dazwischen
        termin_tage = {x["datum"] for x in alle_kuenftig}
        normal_tage = []
        for a, b in zip(verfaelle, verfaelle[1:]):
            if b > (date.today() + timedelta(days=16)).isoformat():
                break
            if a >= heute_iso and np.busday_count(a, b) == 1 and b not in termin_tage:
                w = event_bewegung(preis(b), preis(a), kurs)
                if w:
                    normal_tage.append(w)
        normal_eingepreist = float(np.median(normal_tage)) if normal_tage else None
        for k in alle_kuenftig:
            frueher = [v for v in verfaelle if v < k["datum"] and v >= heute_iso]
            spaeter = [v for v in verfaelle if v >= k["datum"]]
            if not spaeter:
                continue
            ziel = spaeter[0]
            s_tag = preis(ziel)
            s_davor = preis(frueher[-1]) if frueher else None
            bew = event_bewegung(s_tag, s_davor, kurs)
            if bew is None:
                continue
            # Ohne eigenen Tagesverfall: Bewegung für den Zeitraum zwischen den Verfällen (mehrere Handelstage)
            tage = int(np.busday_count(frueher[-1], ziel)) if frueher else int(np.busday_count(heute_iso, ziel))
            mit = [x["name"] for x in alle_kuenftig if x is not k and (frueher[-1] if frueher else heute_iso) < x["datum"] <= ziel]
            eintrag = {**k, "bewegung": round(bew, 3), "straddle": round(s_tag, 2), "straddle_davor": round(s_davor, 2) if s_davor else None,
                       "verfall_davor": frueher[-1] if frueher else None, "verfall": ziel, "handelstage": tage, "zusammen_mit": mit,
                       "tagesgenau": ziel == k["datum"] and tage <= 1, "schnitt_zuletzt": tatsaechlich[k["art"]]["schnitt_betrag"]}
            if eintrag["tagesgenau"] and normal_eingepreist:
                eintrag["aufschlag"] = round(math.sqrt(max(bew ** 2 - normal_eingepreist ** 2, 0)), 3)
            if not eintrag["tagesgenau"]:
                kommend.append(eintrag)
                continue
            # am Vortag des Termins merken, um später eingepreist gegen tatsächlich zu vergleichen
            if frueher and frueher[-1] <= (date.today() + timedelta(days=1)).isoformat():
                historie_eintragen("event:eingepreist", k["datum"], {"art": k["art"], "bewegung": round(bew, 3)})
            kommend.append(eintrag)
        return {"kommend": kommend, "vergangen": tatsaechlich, "ueblich": ueblich, "normal_eingepreist": round(normal_eingepreist, 3) if normal_eingepreist else None,
                "normal_tage": len(normal_tage), "kurs": kurs, "anzahl": ANZAHL,
                "stand": date.today().isoformat(), "arten": ARTEN}
    return gecacht("event:seite", 60 * 60, holen)
