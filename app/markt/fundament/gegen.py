"""Kurs gegen Kennzahl (Punkt 24c, Wunsch 21.11.2026): zwei Linien in einem Chart, links der Kurs, rechts
eine Kennzahl desselben Werts (Gewinn, Umsatz, Capex, Free Cashflow …), wie "S&P 500 und Gewinne".

Werte
- eine Aktie: Kurs (Monatsschluss) und die Kennzahl aus den Geschäftsberichten (Yahoo, US-Firmen mit SEC-Historie).
- ein Korb (Thema, Peer-Gruppe, Depot, Sektor): Kurs = gleichgewichteter Index (Start 100, Monatsrenditen
  aller verfügbaren Werte gemittelt). Kennzahl = Summe über alle Werte, verkettet: Von Quartal zu Quartal zählt
  nur die Veränderung der Werte, die in beiden Quartalen Zahlen haben; am Ende steht die echte Summe aller
  Werte. So springt die Linie nicht, wenn ein Wert erst später Zahlen hat (z. B. nicht-US-Firmen mit kurzer
  Historie). Margen und Quoten: Summe Zähler / Summe Nenner der Werte mit Zahlen im Quartal.
- S&P 500: Kurs und Gewinne je Indexanteil der letzten 12 Monate von Robert Shiller (seit 1871, bis 2024),
  danach mit dem selbst gerechneten Gewinn aller Mitglieder verbunden (wie auf der Seite Smart Money).
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import numpy as np
import pandas as pd

from . import basis as B

# id: (Name, Feld oder (Zähler, Nenner), Einheit)
KENNZAHLEN = {
    "nettogewinn": ("Gewinn", "nettogewinn", "betrag"),
    "umsatz": ("Umsatz", "umsatz", "betrag"),
    "capex": ("Capex", "capex", "betrag"),
    "fcf": ("Free Cashflow", "fcf", "betrag"),
    "ocf": ("Operativer Cashflow", "ocf", "betrag"),
    "ebitda": ("EBITDA", "ebitda", "betrag"),
    "operatives_ergebnis": ("Operatives Ergebnis", "operatives_ergebnis", "betrag"),
    "bruttogewinn": ("Bruttogewinn", "bruttogewinn", "betrag"),
    "fue": ("F&E", "fue", "betrag"),
    "rueckkauf": ("Aktienrückkäufe", "rueckkauf", "betrag"),
    "dividenden": ("Dividenden", "dividenden", "betrag"),
    "eps": ("Gewinn je Aktie", "eps", "je_aktie"),
    "operative_marge": ("Operative Marge", ("operatives_ergebnis", "umsatz"), "prozent"),
    "nettomarge": ("Nettomarge", ("nettogewinn", "umsatz"), "prozent"),
    "fcf_marge": ("FCF-Marge", ("fcf", "umsatz"), "prozent"),
    "capex_umsatz": ("Capex / Umsatz", ("capex", "umsatz"), "prozent"),
}


def _kurse(ticker: str, waehrung: str, roh: dict | None) -> pd.Series:
    k = B.kursverlauf(ticker, 11)
    w = ((roh or {}).get("markt") or {}).get("currency") or waehrung
    if w.upper() != waehrung and len(k):
        k = pd.Series({mo: v * (B.kurs(w.upper(), waehrung, mo) or np.nan) for mo, v in k.items()}).dropna()
    return k


def _reihe(d: dict, art: str, kennzahl: str) -> dict:
    """{Kalenderquartal: (Ende, Wert)} bzw. bei Geschäftsjahren {GJ: (Ende, Wert)}."""
    _, feld, _ = KENNZAHLEN[kennzahl]
    aus = {}
    for p, z in d[art].items():
        if isinstance(feld, tuple):
            a, b = z.get(feld[0]), z.get(feld[1])
            v = (a, b) if a is not None and b else None
        else:
            v = z.get(feld)
        if v is not None:
            aus[p] = (z.get("_ende"), v)
    return aus


def _einzeln(ticker: str, kennzahl: str, art: str, waehrung: str) -> dict:
    roh = B.rohdaten(ticker)
    d = B.aufbereiten(roh, waehrung, False)
    reihe = _reihe(d, art, kennzahl)
    hinweis = None
    if len(reihe) < 6 and art != "jahr":  # wenige Quartale (meist nicht-US): Geschäftsjahre
        r2 = _reihe(d, "jahr", kennzahl)
        if len(r2) > len(reihe):
            reihe, hinweis = r2, "Für diesen Wert gibt es nur wenige Quartalszahlen; gezeigt werden Geschäftsjahre."
    punkte = [(e, (v[0] / v[1] * 100) if isinstance(v, tuple) else v) for e, v in reihe.values() if e]
    m = roh["markt"]
    quelle = "Yahoo" + (" und SEC" if any(z.get("_quelle", "").startswith(("SEC", "Yahoo +")) for z in d["quartal"].values()) else "")
    return {"name": m.get("longName") or m.get("shortName") or ticker, "kurs": _kurse(ticker, waehrung, roh), "punkte": sorted(punkte),
            "hinweis": hinweis, "quelle": f"Kurs und Zahlen: {quelle}", "art_echt": "jahr" if hinweis else art}


def _korb(tickers: list[str], kennzahl: str, art: str, waehrung: str) -> dict:
    tickers = list(dict.fromkeys(t.upper() for t in tickers))[:25]
    def laden(t):
        try:
            roh = B.rohdaten(t)
            d = B.aufbereiten(roh, waehrung, False)
            return t, roh, d
        except Exception:  # noqa: BLE001
            return t, None, None
    with ThreadPoolExecutor(4) as pool:
        geladen = [x for x in pool.map(laden, tickers) if x[1]]
    _, feld, einheit = KENNZAHLEN[kennzahl]
    je = {}  # Periode -> {Ticker: Wert}
    enden = {}
    for t, roh, d in geladen:
        for p, (e, v) in _reihe(d, art, kennzahl).items():
            je.setdefault(p, {})[t] = v
            enden.setdefault(p, e)
    perioden = sorted(je)
    # Einzelne fehlende Perioden einer Firma (zwischen ihrer ersten und letzten Meldung) mit dem Vorwert füllen,
    # damit eine Lücke sie nicht aus der Rechnung wirft
    for t in {x for w in je.values() for x in w}:
        da = [i for i, q in enumerate(perioden) if t in je[q]]
        for i in range(da[0] + 1, da[-1]):
            if t not in je[perioden[i]]:
                je[perioden[i]][t] = je[perioden[i - 1]][t]
    # Am Ende nur Perioden, in denen schon mindestens die Hälfte der Firmen gemeldet hat (das laufende Quartal fehlt
    # sonst fast ganz und wirkt wie ein Einbruch). Wer in den letzten ein, zwei Perioden noch nicht gemeldet hat,
    # zählt dort mit seinem letzten Wert, damit die Summe am Ende vollständig bleibt.
    if perioden:
        voll = max(len(je[q]) for q in perioden)
        while len(perioden) > 1 and len(je[perioden[-1]]) < 0.5 * voll:
            perioden.pop()
        for t in {x for w in je.values() for x in w}:
            da = [i for i, q in enumerate(perioden) if t in je[q]]
            if da and len(perioden) - 1 - da[-1] <= 2:
                for i in range(da[-1] + 1, len(perioden)):
                    je[perioden[i]][t] = je[perioden[i - 1]][t]
    punkte, abdeckung = [], []
    if isinstance(feld, tuple):
        for p in perioden:
            werte = je[p].values()
            a, b = sum(x[0] for x in werte), sum(x[1] for x in werte)
            if b:
                punkte.append((pd.Period(p, freq="Q").end_time.date().isoformat() if "Q" in p else enden[p], a / b * 100))
                abdeckung.append(len(je[p]))
    else:
        # Verkettete Summe: von Periode zu Periode zählt nur die Veränderung der Werte, die in beiden Perioden Zahlen
        # haben – prozentual, solange beide Summen positiv sind, sonst als Differenz. Am Ende auf die echte Summe gesetzt.
        dollar_kette = False
        if perioden:
            stufen = [1.0]
            for i in range(1, len(perioden)):
                alt, neu = je[perioden[i - 1]], je[perioden[i]]
                gemeinsam = set(alt) & set(neu)
                a_s, n_s = sum(alt[t] for t in gemeinsam), sum(neu[t] for t in gemeinsam)
                if gemeinsam and a_s > 0 and n_s > 0 and stufen[-1] > 0:
                    stufen.append(stufen[-1] * n_s / a_s)
                else:
                    # Verlust-Phase: Differenz, umgerechnet in die Einheit der Kette über die Summe der Vorperiode
                    bezug = abs(sum(alt.values())) or 1.0
                    stufen.append(stufen[-1] + (n_s - a_s) / bezug * (abs(stufen[-1]) or 1.0))
            schluss = sum(je[perioden[-1]].values())
            # Kette nur, wenn sie durchgehend positiv bleibt (Verlust-Phasen verzerren sie sonst stark)
            if stufen[-1] > 0 and schluss > 0 and min(stufen) > 0:
                faktor = schluss / stufen[-1]
                werte = [x * faktor for x in stufen]
            else:
                # Verlust-Phasen: Kette in Dollar statt in Prozent. Von hinten zurück zählt je Schritt nur die Veränderung
                # der Firmen, die in beiden Perioden Zahlen haben; spätere Datenanfänge erscheinen so nicht als Sprung.
                werte = [0.0] * len(perioden)
                werte[-1] = schluss
                for i in range(len(perioden) - 1, 0, -1):
                    alt, neu = je[perioden[i - 1]], je[perioden[i]]
                    gemeinsam = set(alt) & set(neu)
                    werte[i - 1] = werte[i] - (sum(neu[x] for x in gemeinsam) - sum(alt[x] for x in gemeinsam))
                dollar_kette = True
            for p, w in zip(perioden, werte):
                ende = pd.Period(p, freq="Q").end_time.date().isoformat() if "Q" in p else enden[p]
                punkte.append((ende, w))
                abdeckung.append(len(je[p]))
    # Kurs: gleichgewichteter Index aus Monatsrenditen
    kurse = {}
    for t, roh, _ in geladen:
        try:
            kurse[t] = _kurse(t, waehrung, roh)
        except Exception:  # noqa: BLE001
            continue
    tab = pd.DataFrame(kurse).sort_index()
    renditen = tab.pct_change(fill_method=None)
    index = (1 + renditen.mean(axis=1, skipna=True).fillna(0)).cumprod() * 100
    letzte = {t: (je[perioden[-1]].get(t) if perioden else None) for t, _, _ in geladen}
    mitglieder = []
    for t, roh, _ in geladen:
        k = kurse.get(t)
        v = letzte.get(t)
        mitglieder.append({"ticker": t, "name": (roh["markt"].get("shortName") or t), "wert": (v[0] / v[1] * 100 if isinstance(v, tuple) and v[1] else v),
                           "kurs_1j": (float(k.iloc[-1]) / float(k.iloc[-13]) - 1) * 100 if k is not None and len(k) > 13 else None,
                           "seit": min((p for p in perioden if t in je[p]), default=None)})
    return {"kurs": index, "punkte": punkte, "abdeckung": abdeckung, "mitglieder": mitglieder, "ohne_daten": [t for t in tickers if t not in {g[0] for g in geladen}],
            "quelle": "Kurs: gleichgewichteter Index (Yahoo), Kennzahl: Summe der Werte (Yahoo, SEC)", "art_echt": art, "korb": True,
            "einheit_hinweis": None if isinstance(feld, tuple) else (
                "Summe aller Werte; wegen Verlust-Phasen ältere Quartale über die Veränderung in Dollar der Werte verkettet, die jeweils in beiden Quartalen Zahlen haben"
                if dollar_kette else "Summe aller Werte; ältere Quartale über das Wachstum der Werte verkettet, die jeweils in beiden Quartalen Zahlen haben")}


def _sp500(waehrung: str) -> dict:
    from ..indizes import kennzahlen
    from ..stil import _index_kgv, _shiller_roh
    roh = _shiller_roh()
    tab = pd.DataFrame.from_dict(roh, orient="index")
    tab.index = pd.PeriodIndex(tab.index, freq="M")
    tab = tab.sort_index()
    spx = B.kursverlauf("^GSPC", 11)
    spx.index = pd.PeriodIndex(spx.index, freq="M")
    p = tab["p"].combine_first(spx)
    p = p.reindex(pd.period_range(p.index[0], spx.index[-1], freq="M")).ffill()
    e = tab["e"].dropna()
    try:
        ttm = _index_kgv(kennzahlen("sp500"))["ttm"]
        e_heute = float(spx.iloc[-1]) / ttm if ttm else None
    except Exception:  # noqa: BLE001
        e_heute = None
    if e_heute and e.index[-1] < spx.index[-1]:
        e = e.reindex(pd.period_range(e.index[0], spx.index[-1], freq="M"))
        e.iloc[-1] = e_heute
        e = e.interpolate()
    ab = pd.Period(f"{date.today().year - 60}-01", freq="M")
    p, e = p[p.index >= ab], e[e.index >= ab]
    return {"name": "S&P 500", "kurs": pd.Series(p.values, index=[str(i) for i in p.index]),
            "punkte": [(i.end_time.date().isoformat(), float(v)) for i, v in e.items()],
            "quelle": "Robert Shiller (Kurs und Gewinn je Indexanteil der letzten 12 Monate, bis 2024), danach Yahoo und selbst gerechneter Gewinn aller Mitglieder",
            "art_echt": "monat", "hinweis": "Gewinn je Indexanteil der letzten 12 Monate in US-Dollar. Ab 2025 linear zum heutigen Stand verbunden."}


def gegen(wert: str, kennzahl: str = "nettogewinn", art: str = "ttm", jahre: int = 10, waehrung: str = "USD", tickers: list[str] | None = None, name: str | None = None) -> dict:
    if kennzahl not in KENNZAHLEN:
        raise LookupError("Unbekannte Kennzahl.")
    if wert == "index:sp500":
        if kennzahl not in ("nettogewinn", "eps"):
            raise LookupError("Für den S&P 500 gibt es nur die Gewinne (Shiller-Daten).")
        d = _sp500(waehrung)
        kennzahl = "eps"
    elif tickers:
        d = _korb(tickers, kennzahl, art, waehrung)
        d["name"] = name or wert
    else:
        d = _einzeln(wert, kennzahl, art, waehrung)
    kn, _, einheit = KENNZAHLEN[kennzahl]
    ab = f"{date.today().year - jahre}-{date.today().month:02d}"
    kurs = d["kurs"]
    kurs = kurs[[str(i) >= ab for i in kurs.index]]
    punkte = [(e, v) for e, v in d["punkte"] if e and e[:7] >= ab]
    if len(kurs) < 3 or len(punkte) < 2:
        return {"fehler": f"Für {d.get('name', wert)} gibt es im gewählten Zeitraum zu wenige {kn}-Zahlen."}
    # Kennzahzahl am Monatsende fortschreiben, um sie mit dem Kurs zu vergleichen
    km = pd.Series({e[:7]: v for e, v in punkte}).sort_index()
    km_monat = km.reindex(sorted(set(km.index) | {str(i) for i in kurs.index})).ffill()
    paare = [(str(m), float(kurs[m]), km_monat.get(str(m))) for m in kurs.index if pd.notna(km_monat.get(str(m)))]
    vk = (paare[-1][1] / paare[0][1] - 1) * 100 if paare and paare[0][1] else None
    vz = (punkte[-1][1] / punkte[0][1] - 1) * 100 if punkte[0][1] and punkte[0][1] > 0 and punkte[-1][1] > 0 and einheit != "prozent" else None
    # Gleichlauf: Korrelation der Jahresveränderungen (Kurs und Kennzahl, je Quartalsende)
    korr = None
    if einheit != "prozent" and len(paare) > 30 and d.get("art_echt") != "jahr":
        s = pd.DataFrame(paare, columns=["m", "k", "z"]).set_index("m")
        s = s.iloc[::3]
        vj = s.pct_change(4).dropna()
        vj = vj[(s["z"].shift(4).reindex(vj.index) > 0)]
        if len(vj) >= 8:
            korr = float(vj["k"].corr(vj["z"]))
    jahre_echt = max(1e-9, (pd.Timestamp(punkte[-1][0]) - pd.Timestamp(punkte[0][0])).days / 365.25)
    return {"wert": wert, "name": d.get("name", wert), "kennzahl": kennzahl, "kennzahl_name": kn, "einheit": einheit, "waehrung": waehrung,
            "art": d.get("art_echt", art), "korb": d.get("korb", False),
            "kurs": {"daten": [f"{m}-01" for m in kurs.index.astype(str)], "werte": [round(float(v), 4) for v in kurs.values]},
            "kennzahl_reihe": {"daten": [e for e, _ in punkte], "werte": [float(v) for _, v in punkte]},
            "abdeckung": d.get("abdeckung"), "mitglieder": d.get("mitglieder"), "ohne_daten": d.get("ohne_daten"),
            "veraenderung_kurs": vk, "veraenderung_kennzahl": vz,
            "cagr_kennzahl": ((punkte[-1][1] / punkte[0][1]) ** (1 / jahre_echt) - 1) * 100 if vz is not None else None,
            "cagr_kurs": ((paare[-1][1] / paare[0][1]) ** (1 / jahre_echt) - 1) * 100 if paare and paare[0][1] else None,
            "korrelation": korr, "hinweis": d.get("hinweis"), "einheit_hinweis": d.get("einheit_hinweis"), "quelle": d["quelle"],
            "kennzahlen": [{"id": k, "name": v[0], "einheit": v[2]} for k, v in KENNZAHLEN.items()]}


# ---------------------------------------------------------------- Detail zu einem Quartal (Klick im Chart)
def _periode(datum: str, art: str) -> str:
    ende = pd.Timestamp(datum).date()
    return f"GJ{ende.year}" if art == "jahr" else B.kalenderquartal(ende)


def quartal_detail(wert: str, datum: str, art: str = "ttm", waehrung: str = "USD", tickers: list[str] | None = None, name: str | None = None) -> dict:
    """Alle Zahlen eines Werts in einer Periode (mit Vorjahr), bei einem Korb die Zahlen jedes Werts."""
    from .kennzahlen import rechnen
    if tickers:
        p = _periode(datum, art)
        felder = ("umsatz", "nettogewinn", "operatives_ergebnis", "capex", "fcf", "ocf", "fue", "rueckkauf")
        def einer(t):
            try:
                d = B.aufbereiten(B.rohdaten(t), waehrung, False)
                z = d[art].get(p)
                vj = d[art].get(str(pd.Period(p, freq="Q") - 4)) if "Q" in p else d[art].get(f"GJ{int(p[2:]) - 1}")
                return t, z, vj
            except Exception:  # noqa: BLE001
                return t, None, None
        with ThreadPoolExecutor(4) as pool:
            daten = list(pool.map(einer, list(dict.fromkeys(x.upper() for x in tickers))[:25]))
        zeilen = []
        for t, z, vj in daten:
            if not z:
                continue
            zeile = {"ticker": t, "ende": z.get("_ende"), "quelle": z.get("_quelle")}
            for f in felder:
                zeile[f] = z.get(f)
                a = (vj or {}).get(f)
                zeile[f"{f}_vj"] = (z[f] / a - 1) * 100 if z.get(f) is not None and a and a > 0 else None
            u = z.get("umsatz")
            zeile["operative_marge"] = z["operatives_ergebnis"] / u * 100 if u and z.get("operatives_ergebnis") is not None else None
            zeilen.append(zeile)
        summe = {f: sum(z[f] for z in zeilen if z.get(f) is not None) for f in felder}
        return {"korb": True, "name": name or wert, "periode": p, "art": art, "waehrung": waehrung, "zeilen": zeilen, "summe": summe,
                "ohne": [t for t, z, _ in daten if not z]}
    r = rechnen(wert, art, "absolut", waehrung, False, perioden_anzahl=80)
    p = _periode(datum, art)
    ids = [x["id"] for x in r["perioden"]]
    if p not in ids:
        raise LookupError(f"Für {p} gibt es keine Zahlen.")
    i = ids.index(p)
    vj_i = i - (1 if art == "jahr" else 4)
    gruppen = {}
    for k in r["kennzahlen"]:
        if k["aktuell"]:
            continue
        v = k["werte"][i]
        a = k["werte"][vj_i] if vj_i >= 0 else None
        if v is None and a is None:
            continue
        veraenderung = None
        if v is not None and a is not None:
            veraenderung = (v - a) if k["einheit"] == "prozent" else ((v / a - 1) * 100 if a > 0 and v >= 0 else None)
        gruppen.setdefault(k["kategorie_name"], []).append({"id": k["id"], "name": k["name"], "einheit": k["einheit"], "wert": v, "vorjahr": a,
                                                           "veraenderung": veraenderung, "pp": k["einheit"] == "prozent", "erklaerung": k["erklaerung"]})
    return {"korb": False, "name": r["name"], "ticker": r["ticker"], "periode": p, "art": art, "waehrung": waehrung, "ende": r["perioden"][i]["ende"],
            "quelle": r["perioden"][i]["quelle"], "vorjahr_periode": ids[vj_i] if vj_i >= 0 else None,
            "gruppen": [{"name": n, "zeilen": z} for n, z in gruppen.items()]}


# ---------------------------------------------------------------- Übersicht aller Gebiete (Themen und Sektoren)
import threading as _threading

_gebiete_status: dict = {}
_gebiete_sperre = _threading.Lock()
SEKTOREN = ["Information Technology", "Communication Services", "Consumer Discretionary", "Consumer Staples", "Health Care", "Financials",
            "Industrials", "Energy", "Materials", "Utilities", "Real Estate"]
SEKTOR_NAMEN = {"Information Technology": "Technologie", "Communication Services": "Kommunikation", "Consumer Discretionary": "Zyklischer Konsum",
                "Consumer Staples": "Basiskonsum", "Health Care": "Gesundheit", "Financials": "Finanzen", "Industrials": "Industrie", "Energy": "Energie",
                "Materials": "Rohstoffe", "Utilities": "Versorger", "Real Estate": "Immobilien"}


def koerbe() -> list[dict]:
    """Alle Themen aus themen.yaml und die S&P-500-Sektoren (je die 20 größten Werte)."""
    from ..indizes import kennzahlen, mitglieder
    from ..themen import lade_themen
    aus = []
    try:
        for t in lade_themen():
            aus.append({"id": f"thema:{t['name']}", "name": t["name"], "art": "thema", "werte": list(t["werte"])[:25]})
    except Exception:  # noqa: BLE001
        pass
    try:
        k = kennzahlen("sp500")
        m = mitglieder("sp500")
        for s in SEKTOREN:
            drin = sorted([x["ticker"] for x in m if x.get("sektor") == s and x["ticker"] in k], key=lambda t: -(k[t].get("cap") or 0))[:20]
            if drin:
                aus.append({"id": f"sektor:{s}", "name": SEKTOR_NAMEN.get(s, s), "art": "sektor", "werte": drin})
    except Exception:  # noqa: BLE001
        pass
    return aus


def _kurz(r: dict) -> dict:
    """Nur was eine Kachel braucht: Veränderungen und eine verkürzte Reihe."""
    def duenn(daten, werte, n):
        schritt = max(1, len(werte) // n)
        return {"daten": daten[::schritt] + ([daten[-1]] if (len(daten) - 1) % schritt else []), "werte": werte[::schritt] + ([werte[-1]] if (len(werte) - 1) % schritt else [])}
    return {k: r.get(k) for k in ("veraenderung_kurs", "veraenderung_kennzahl", "cagr_kurs", "cagr_kennzahl", "korrelation", "einheit", "kennzahl_name")} | {
        "kurs": duenn(r["kurs"]["daten"], r["kurs"]["werte"], 60), "kz": duenn(r["kennzahl_reihe"]["daten"], r["kennzahl_reihe"]["werte"], 60),
        "kz_start": r["kennzahl_reihe"]["werte"][0], "kz_ende": r["kennzahl_reihe"]["werte"][-1]}


def gebiete(kennzahl: str, art: str, jahre: int, waehrung: str) -> dict:
    """Alle Körbe mit Kurs und Kennzahl. Beim ersten Mal rechnet ein Hintergrund-Thread; bis dahin Zwischenstand."""
    from ..abruf import STUNDE
    from ...cache import markt_lesen, markt_schreiben
    if kennzahl == "eps":
        kennzahl = "nettogewinn"  # Gewinn je Aktie lässt sich über Firmen nicht addieren
    schluessel = f"fund:gebiete7:{kennzahl}:{art}:{jahre}:{waehrung}"
    alt, alter = markt_lesen(schluessel)
    if alt is not None and alter < 12 * STUNDE:
        return {**alt, "rechnet": False}
    with _gebiete_sperre:
        st = _gebiete_status.get(schluessel)
        if not st or not st["laeuft"]:
            liste = koerbe()
            st = _gebiete_status[schluessel] = {"laeuft": True, "fertig": 0, "gesamt": len(liste), "gebiete": [], "fehler": []}
            def lauf():
                try:
                    for k in liste:
                        try:
                            r = gegen(k["id"], kennzahl, art, jahre, waehrung, k["werte"], k["name"])
                            if r.get("fehler"):
                                st["fehler"].append(f"{k['name']}: {r['fehler']}")
                            else:
                                st["gebiete"].append({"id": k["id"], "name": k["name"], "art": k["art"], "anzahl": len(k["werte"]), **_kurz(r)})
                        except Exception as f:  # noqa: BLE001
                            st["fehler"].append(f"{k['name']}: {type(f).__name__}")
                        st["fertig"] += 1
                    markt_schreiben(schluessel, {"gebiete": st["gebiete"], "fehler": st["fehler"], "kennzahl": kennzahl})
                finally:
                    st["laeuft"] = False
            _threading.Thread(target=lauf, name="gebiete", daemon=True).start()
    if alt is not None:  # alter Stand, während neu gerechnet wird
        return {**alt, "rechnet": True, "fertig": st["fertig"], "gesamt": st["gesamt"]}
    return {"gebiete": list(st["gebiete"]), "fehler": list(st["fehler"]), "kennzahl": kennzahl, "rechnet": st["laeuft"], "fertig": st["fertig"], "gesamt": st["gesamt"]}
