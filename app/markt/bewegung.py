"""Punkt 29b: "Warum bewegt sich der Markt?" (Stufe 1, nur Daten, keine KI).

Für S&P 500, Nasdaq 100 und DAX:
- Beitrag jeder Aktie zur Indexbewegung in Indexpunkten: Gewicht mal Rendite mal Indexstand vom Vortag.
  Das Gewicht ist eine Näherung über den Börsenwert (Yahoo), nicht die offizielle Indexgewichtung
  (Streubesitz, Kappungen beim DAX). Deshalb steht die Summe der Beiträge neben der echten Veränderung.
- Beitrag der Sektoren und der Themen aus daten/markt/themen.yaml.
- Makro-Chips (Renditen, Dollar, Öl, Gold, VIX, Bitcoin) mit Sigma: heutige Veränderung geteilt durch die
  übliche Tagesschwankung des letzten Jahres.
- Heutige Termine (Makro-Kalender, Earnings mit Ergebnis gegen Erwartung), Schlagzeilen zu den größten Treibern.
- Fluss- und Positionierungs-Signale aus den vorhandenen Werkzeugen (Phase 6). Fehlt eins, entfällt es still.
- Ein regelbasierter Fazit-Satz. Lieber "Kein klarer Auslöser" als eine erzwungene Erklärung.

Takt: ein Hintergrund-Thread rechnet alle 30 Minuten, solange eine der Börsen offen ist, und einmal nach
Börsenschluss (Abschlussversion). Jeder Tag landet in historie.sqlite ("bewegung:<index>").
Die Kurse aller rund 600 Werte kommen gebündelt in einer Sammelanfrage, die Börsenwerte einmal am Tag.
"""
import re
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from . import earnings, indizes, ki, makro, positionierung, signale, squawk, themen
from .abruf import STUNDE, gecacht, historie_eintragen, historie_lesen, hole
from ..cache import markt_lesen, markt_schreiben

BERLIN = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")
TAKT_MINUTEN = 30
ABWEICHUNG_HINWEIS = 10.0      # Prozent: Summe der Beiträge weicht so stark von der echten Veränderung ab
ABWEICHUNG_MIN_BEWEGUNG = 0.2  # Prozent: kleinere Indexbewegungen gelten als Nenner mindestens so groß
UNGEWOEHNLICH = 2.0            # Sigma, ab dem eine Bewegung als ungewöhnlich gilt
TOP = 5

INDEX = {
    "sp500": {"name": "S&P 500", "kurs": "^GSPC", "etf": "SPY", "zone": NEW_YORK, "auf": (9, 30), "zu": (16, 0)},
    "ndx": {"name": "Nasdaq 100", "kurs": "^NDX", "etf": "QQQ", "zone": NEW_YORK, "auf": (9, 30), "zu": (16, 0)},
    "dax": {"name": "DAX", "kurs": "^GDAXI", "etf": "^GDAXI", "zone": BERLIN, "auf": (9, 0), "zu": (17, 30)},
}
# Art "bp": Veränderung in Basispunkten (Renditen), sonst in Prozent
CHIPS = [
    ("2YY=F", "US-Rendite 2 J.", "bp"),
    ("^TNX", "US-Rendite 10 J.", "bp"),
    ("DX-Y.NYB", "Dollar", "pct"),
    ("CL=F", "Öl WTI", "pct"),
    ("GC=F", "Gold", "pct"),
    ("^VIX", "VIX", "pct"),
    ("BTC-USD", "Bitcoin", "pct"),
]
SEKTOR_DE = {
    "Information Technology": "Technologie", "Communication Services": "Kommunikation",
    "Consumer Discretionary": "Zyklischer Konsum", "Consumer Staples": "Basiskonsum", "Health Care": "Gesundheit",
    "Financials": "Finanzen", "Industrials": "Industrie", "Energy": "Energie", "Materials": "Grundstoffe",
    "Utilities": "Versorger", "Real Estate": "Immobilien",
    # DAX (Wikipedia)
    "Aerospace & Defence": "Luftfahrt & Rüstung", "Apparel": "Bekleidung", "Automotive": "Auto", "Biotech": "Biotech",
    "Chemicals": "Chemie", "Construction Materials": "Baustoffe", "Consumer Goods": "Konsumgüter", "Consumer goods": "Konsumgüter",
    "Distribution": "Handel", "E-Commerce": "Onlinehandel", "Energy technology": "Energietechnik", "Financial Services": "Finanzen",
    "Healthcare": "Gesundheit", "Insurance": "Versicherungen", "Logistics": "Logistik", "Mechanical Engineering": "Maschinenbau",
    "Medical Equipment": "Medizintechnik", "Pharmaceuticals": "Pharma", "Technology": "Technologie", "Telecommunication": "Telekommunikation",
}
NAMENS_ENDE = re.compile(r",?\s+(Inc\.?|Corp\.?|Corporation|Company|Co\.|Ltd\.?|plc|AG|SE|N\.V\.|Holdings?|Group|Class [A-C]|\(The\)|Common Stock)+$", re.I)


def kurzname(name: str) -> str:
    kurz = name
    for _ in range(3):
        kurz = NAMENS_ENDE.sub("", kurz).strip()
    return kurz if len(kurz) >= 2 else name
TECH = re.compile(r"technolog|kommunikation|software|semiconductor|halbleiter|telecom|internet", re.I)
ENERGIE = re.compile(r"energie|energy|oil|öl", re.I)

_sperre = threading.Lock()
_zustand = {"rechnet": False, "letzte": 0.0, "fehler": None}


# ---------- Hilfen ----------

def gz(v: float, n: int = 1) -> str:
    """Zahl mit deutschem Komma."""
    return f"{v:.{n}f}".replace(".", ",").replace("-", "−")


def _f(x, n=2):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), n)


def _jetzt() -> datetime:
    return datetime.now(BERLIN)


def _boerse(index_id: str, jetzt: datetime | None = None) -> str:
    """'offen', 'nach' (heute nach Schluss), 'vor' (heute vor Beginn) oder 'zu' (Wochenende)."""
    i = INDEX[index_id]
    t = (jetzt or _jetzt()).astimezone(i["zone"])
    if t.weekday() >= 5:
        return "zu"
    auf = t.replace(hour=i["auf"][0], minute=i["auf"][1], second=0, microsecond=0)
    zu = t.replace(hour=i["zu"][0], minute=i["zu"][1], second=0, microsecond=0)
    return "vor" if t < auf else "offen" if t < zu else "nach"


def _lokales_datum(index_id: str) -> str:
    return _jetzt().astimezone(INDEX[index_id]["zone"]).date().isoformat()


def _dritter_freitag(jahr: int, monat: int) -> date:
    erster = date(jahr, monat, 1)
    return erster + timedelta(days=(4 - erster.weekday()) % 7 + 14)


def _handelstag_versatz(tag: date, n: int) -> date:
    schritt = 1 if n > 0 else -1
    for _ in range(abs(n)):
        tag += timedelta(days=schritt)
        while tag.weekday() >= 5:
            tag += timedelta(days=schritt)
    return tag


# ---------- Daten holen ----------

def _mitglieder() -> dict:
    """{index: [{ticker, name, sektor}]}; Nasdaq-100-Werte bekommen den Sektor aus der S&P-500-Liste."""
    aus = {i: indizes.mitglieder(i) for i in INDEX}
    sp_sektor = {m["ticker"]: m["sektor"] for m in aus["sp500"]}
    for i, liste in aus.items():
        aus[i] = [{"ticker": m["ticker"], "name": kurzname(m["name"]),
                   "sektor": SEKTOR_DE.get(m["sektor"] or sp_sektor.get(m["ticker"], ""), m["sektor"] or sp_sektor.get(m["ticker"]) or "Sonstige")}
                  for m in liste]
    return aus


def _gewichte(index_id: str, liste: list[dict]) -> dict:
    """Börsenwert je Ticker (einmal am Tag). Mehrere Aktiengattungen derselben Firma (GOOGL/GOOG) melden bei
    Yahoo oft denselben Gesamtwert: dann wird er geteilt, sonst zählte die Firma doppelt."""
    try:
        kz = indizes.kennzahlen(index_id)
    except Exception:
        kz = markt_lesen(f"kennzahlen:{index_id}")[0] or {}
    cap = {m["ticker"]: float((kz.get(m["ticker"]) or {}).get("cap") or 0) for m in liste}
    gruppen = {}
    for m in liste:
        if cap[m["ticker"]] > 0:
            gruppen.setdefault((m["name"].split()[0].lower(), round(cap[m["ticker"]], -6)), []).append(m["ticker"])
    for ts in gruppen.values():
        for t in ts:
            cap[t] /= len(ts)
    return {t: c for t, c in cap.items() if c > 0}


def _tageskurse(ticker: list[str], zeitraum: str) -> dict:
    """Sammelanfrage bei Yahoo: {Ticker: {"d": [Daten], "o": [Eröffnung], "c": [Schluss]}}."""
    roh = yf.download(ticker, period=zeitraum, interval="1d", auto_adjust=False, progress=False, threads=True, group_by="column")
    if roh is None or roh.empty:
        raise ValueError("keine Kurse")
    aus = {}
    for t in ticker:
        try:
            c, o = roh["Close"][t], roh["Open"][t]
        except KeyError:
            continue
        tab = pd.concat([o, c], axis=1, keys=["o", "c"]).dropna(subset=["c"])
        if len(tab) < 2:
            continue
        aus[t] = {"d": [d.date().isoformat() for d in pd.DatetimeIndex(tab.index)],
                  "o": [None if pd.isna(v) else float(v) for v in tab["o"]], "c": [float(v) for v in tab["c"]]}
    return aus


def _earnings_kalender(tag: date) -> list[dict]:
    """Earnings eines Tages von nasdaq.com mit Zeitpunkt (vor/nach Börse). Für vergangene Tage nennt nasdaq.com
    keinen Zeitpunkt mehr, deshalb bleibt der früher gesehene erhalten."""
    schluessel = f"bewegung:earnings:{tag.isoformat()}"
    alt = markt_lesen(schluessel)[0] or []

    def holen():
        zeilen = ((hole(f"https://api.nasdaq.com/api/calendar/earnings?date={tag.isoformat()}").json().get("data") or {}).get("rows")) or []
        frueher = {z["t"]: z["zeit"] for z in alt}
        aus = []
        for z in zeilen:
            t = z["symbol"].replace(".", "-")
            zeit = {"time-pre-market": "vor Börse", "time-after-hours": "nach Börse"}.get(z.get("time"))
            aus.append({"t": t, "name": z.get("name") or t, "zeit": zeit or frueher.get(t), "erwartung": z.get("epsForecast") or None})
        return aus
    return gecacht(schluessel, 2 * STUNDE, holen)


# ---------- Rechnen ----------

def _veraenderung(reihe: dict, tag: str, vortag: str):
    d = reihe["d"]
    if tag not in d or vortag not in d:
        return None
    return reihe["c"][d.index(tag)] / reihe["c"][d.index(vortag)] - 1


def _sigma(c: list[float], art: str) -> tuple[float | None, float | None]:
    """(heutige Veränderung, Sigma). Veränderung in Prozent bzw. Basispunkten."""
    s = pd.Series(c, dtype=float)
    diff = s.diff() * 100 if art == "bp" else s.pct_change() * 100
    diff = diff.dropna()
    if len(diff) < 30:
        return (_f(diff.iloc[-1]) if len(diff) else None), None
    heute, frueher = float(diff.iloc[-1]), diff.iloc[-251:-1]
    streu = float(frueher.std())
    return heute, (heute / streu if streu > 0 else None)


def _chips(lang: dict) -> list[dict]:
    aus = []
    for t, name, art in CHIPS:
        r = lang.get(t)
        if not r:
            continue
        heute, sig = _sigma(r["c"], art)
        aus.append({"ticker": t, "name": name, "art": art, "wert": _f(r["c"][-1], 3), "aenderung": _f(heute),
                    "sigma": _f(sig, 1), "ungewoehnlich": sig is not None and abs(sig) >= UNGEWOEHNLICH, "stand": r["d"][-1]})
    return aus


def _beitraege(index_id: str, liste: list[dict], kurse: dict, idx: dict, tag: str, vortag: str) -> dict:
    caps = _gewichte(index_id, liste)
    zeilen = []
    for m in liste:
        r = kurse.get(m["ticker"])
        v = _veraenderung(r, tag, vortag) if r else None
        if v is None or m["ticker"] not in caps:
            continue
        zeilen.append({**m, "r": v, "cap": caps[m["ticker"]]})
    gesamt = sum(z["cap"] for z in zeilen) or 1
    basis = idx["c"][idx["d"].index(vortag)]
    for z in zeilen:
        z["gewicht"] = z["cap"] / gesamt
        z["punkte"] = z["gewicht"] * z["r"] * basis
    ist = idx["c"][idx["d"].index(tag)] - basis
    summe = sum(z["punkte"] for z in zeilen)
    nenner = max(abs(ist), basis * ABWEICHUNG_MIN_BEWEGUNG / 100)
    abweichung = abs(summe - ist) / nenner * 100
    zeilen.sort(key=lambda z: z["punkte"], reverse=True)
    zeile = lambda z: {"ticker": z["ticker"], "name": z["name"], "sektor": z["sektor"], "punkte": _f(z["punkte"]),
                       "prozent": _f(z["r"] * 100), "gewicht": _f(z["gewicht"] * 100)}
    oben = [zeile(z) for z in zeilen[:TOP] if z["punkte"] > 0]
    unten = [zeile(z) for z in reversed(zeilen[-TOP:]) if z["punkte"] < 0]
    rest = summe - sum(z["punkte"] or 0 for z in oben + unten)

    def gruppe(schluessel_von):
        g = {}
        for z in zeilen:
            for name in schluessel_von(z):
                e = g.setdefault(name, {"name": name, "punkte": 0.0, "cap": 0.0, "rcap": 0.0, "n": 0})
                e["punkte"] += z["punkte"]; e["cap"] += z["cap"]; e["rcap"] += z["r"] * z["cap"]; e["n"] += 1
        return sorted(({"name": e["name"], "punkte": _f(e["punkte"]), "prozent": _f(e["rcap"] / e["cap"] * 100) if e["cap"] else None,
                        "anteil": _f(e["cap"] / gesamt * 100, 1), "n": e["n"]} for e in g.values()), key=lambda e: -abs(e["punkte"] or 0))

    try:
        themen_liste = themen.lade_themen()
    except Exception:
        themen_liste = []
    thema_von = {}
    for th in themen_liste:
        for t in th["werte"]:
            thema_von.setdefault(t, []).append(th["name"])
    steigend = sum(1 for z in zeilen if z["r"] > 0)
    return {
        "zeilen": zeilen,  # nur intern, wird vor dem Speichern entfernt
        "beitraege": {"oben": oben, "unten": unten, "rest": _f(rest), "summe": _f(summe), "ist": _f(ist),
                      "abweichung": _f(abweichung, 1), "hinweis": abweichung > ABWEICHUNG_HINWEIS,
                      "n": len(zeilen), "n_gesamt": len(liste)},
        "sektoren": gruppe(lambda z: [z["sektor"]]),
        "themen": gruppe(lambda z: thema_von.get(z["ticker"], [])),
        "breite": {"steigend": steigend, "fallend": sum(1 for z in zeilen if z["r"] < 0), "n": len(zeilen),
                   "anteil": _f(steigend / len(zeilen) * 100, 0) if zeilen else None,
                   "gleich": _f(float(np.mean([z["r"] for z in zeilen])) * 100) if zeilen else None},
    }


def _termine(heute: date, mitglieder: set, namen: dict) -> dict:
    try:
        makro_heute = [t for t in makro.makro_termine().get("termine", []) if t["datum"] == heute.isoformat()]
    except Exception:
        makro_heute = []
    kandidaten = {}
    vortag = _handelstag_versatz(heute, -1)
    for tag, nur_nach in ((vortag, True), (heute, False)):
        try:
            zeilen = _earnings_kalender(tag)
        except Exception:
            continue
        for z in zeilen:
            if z["t"] in mitglieder and (not nur_nach or z["zeit"] in ("nach Börse", None)):
                kandidaten[z["t"]] = {**z, "tag": tag.isoformat()}
    # DAX: kein freier Kalender, Yahoo kennt die gemeldeten Zahlen
    dax = {t: namen[t] for t in namen if "." in t}
    us = {t: namen.get(t, kandidaten[t]["name"]) for t in kandidaten}
    try:
        gemeldet = earnings.frische_ergebnisse({**us, **dax}, stunden=30)
    except Exception:
        gemeldet = []
    aus, gesehen = [], set()
    for e in gemeldet:
        if e["ticker"] in gesehen:
            continue
        gesehen.add(e["ticker"])
        aus.append({"ticker": e["ticker"], "name": e["name"], "zeit": e.get("sitzung") or (kandidaten.get(e["ticker"]) or {}).get("zeit"),
                    "ist": e.get("ist"), "erwartet": e.get("erwartet"), "ueberraschung": _f(e.get("ueberraschung"), 1),
                    "umsatz_ueberraschung": _f(e.get("umsatz_ueberraschung"), 1), "gemeldet": True})
    for t, k in kandidaten.items():
        if t not in gesehen and k["tag"] == heute.isoformat():
            aus.append({"ticker": t, "name": namen.get(t, k["name"]), "zeit": k["zeit"], "erwartung_text": k["erwartung"], "gemeldet": False})
    return {"makro": makro_heute, "earnings": aus}


# Welche Schlagzeile erklärt die Bewegung? Bewertet nach Wörtern statt nur nach Aktualität:
# Kursbewegung in der richtigen Richtung, harte Nachrichten (Bericht, Ermittlung, Exportkontrolle ...),
# ein Thema, das mehrere Meldungen aufgreifen, und kein Meinungsartikel ("Is X a buy?").
RUNTER = re.compile(r"\b(fall|falls|fell|falling|drop|drops|dropped|dropping|slide|slides|slid|sliding|sink|sinks|sank|plunge|plunges|plunged|plunging|tumble|tumbles|tumbled|"
                    r"slump|slumps|slumped|sell-?off|selloff|decline|declines|declined|lower|down|crash|crashes|crashed|retreat|retreats|slip|slips|slipped|sheds|losses|weighs?|hit)\b", re.I)
HOCH = re.compile(r"\b(rise|rises|rose|rising|jump|jumps|jumped|surge|surges|surged|soar|soars|soared|rally|rallies|rallied|climb|climbs|climbed|gain|gains|gained|"
                  r"higher|up|pop|pops|popped|spike|spikes|spiked|boost|boosts|record high)\b", re.I)
HART = re.compile(r"\b(report|reports|probe|investigation|investigat\w*|export|china|chinese|tariff|sanction\w*|ban|bans|ban(ned)?|short seller|short report|hunterbrook|"
                  r"allegation\w*|alleg\w*|claims?|lawsuit|sues?|sued|sec|doj|ftc|antitrust|downgrade\w*|upgrade\w*|price target|guidance|forecast|outlook|warns?|warning|"
                  r"deal|acquisition|acquire\w*|merger|buyout|stake|offering|recall|halt\w*|ceo|resign\w*|layoffs?|denies|deny|responds?|statement|compliance|rumou?rs?|"
                  r"earnings|results|revenue|beats?|miss(es)?|after|amid|on concerns?|concerns?|fears?|why)\b", re.I)
MEINUNG = re.compile(r"(\?\s*$|\b(cheap|undervalued|overvalued|worth|buy now|should you|is it time|top \d+|best|motley|dividend stock|long-term|"
                     r"investment would|could be|here's|forecast:|prediction|millionaire|retire|watchlist)\b)", re.I)
FUELL = {"stock", "stocks", "shares", "share", "the", "and", "for", "with", "after", "over", "from", "that", "this", "its", "inc", "corp", "says", "said"}


def _woerter(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9-]{3,}", t.lower()) if w not in FUELL}


def _schlagzeile_punkte(m: dict, z: dict, muster: re.Pattern, alle: list[dict], jetzt: datetime) -> float:
    titel = m["titel"]
    punkte = 0.0
    treffer = muster.search(titel)
    # Die Firma ist Thema der Schlagzeile, wenn sie vorn steht ("AMD shares slide ..."), nicht nur nebenbei
    punkte += 2 if treffer and treffer.start() <= 30 else -1
    richtig, falsch = (RUNTER, HOCH) if (z["punkte"] or 0) < 0 else (HOCH, RUNTER)
    if richtig.search(titel):
        punkte += 3
    elif falsch.search(titel):
        punkte -= 3  # passt nicht zur heutigen Richtung (z. B. alte Meldung vom Vormittag)
    punkte += min(3, len(HART.findall(titel)))
    if MEINUNG.search(titel):
        punkte -= 4
    # Thema, das mehrere Meldungen aufgreifen (z. B. Bericht, Stellungnahme der Firma, Reaktion)
    w = _woerter(titel) - {x.lower() for x in muster.pattern.split("|")}
    andere = sum(1 for x in alle if x is not m and len(w & _woerter(x["titel"])) >= 1 and len(w & _woerter(x["titel"])) / max(1, len(w)) >= 0.2)
    punkte += min(3, andere)
    alter = (jetzt - datetime.fromisoformat(m["zeit"])).total_seconds() / 3600
    punkte -= max(0.0, alter - 12) / 6  # Meldungen von gestern zählen weniger
    return punkte


def _news(treiber: list[dict]) -> list[dict]:
    us = {z["ticker"]: {"name": z["name"], "aliase": _aliase(z["name"], z["ticker"])} for z in treiber if "." not in z["ticker"]}
    try:
        feed = squawk.feed(us)["meldungen"]
    except Exception:
        return []
    jetzt = datetime.now(BERLIN)
    grenze = jetzt - timedelta(hours=30)
    aus = []
    for z in treiber:
        muster = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(a) for a in _aliase(z["name"], z["ticker"])) + r")(?!\w)", re.I)
        treffer = [m for m in feed if datetime.fromisoformat(m["zeit"]) >= grenze and (z["ticker"] in m["ticker"] or muster.search(m["titel"]))]
        bewertet = sorted(((_schlagzeile_punkte(m, z, muster, treffer, jetzt), m) for m in treffer), key=lambda x: -x[0])
        gut = [m for p, m in bewertet if p >= 6]
        aus.append({"ticker": z["ticker"], "name": z["name"], "punkte": z["punkte"], "passend": bool(gut),
                    "meldungen": [{"titel": m["titel"], "link": m["link"], "quelle": m["quellen"][0], "zeit": m["zeit"]} for m in gut[:3]]})
    return aus


def _aliase(name: str, ticker: str) -> list[str]:
    kurz = kurzname(name)
    aliase = {kurz} if len(kurz) >= 3 else set()
    if "." not in ticker and "-" not in ticker and len(ticker) >= 3 and ticker not in {"NOW", "ALL", "ON", "IT", "ARE", "CAT", "KEY", "LOW", "BIG", "FAST", "META"}:
        aliase.add(ticker)
    return sorted(aliase) or [ticker]


def _signale(index_id: str, heute: date, chips: dict, breite: dict, idx_sigma, idx_pct, zeilen: list, oeffnung: dict) -> list[dict]:
    aus = []
    # Optionsverfall (3. Freitag; im März, Juni, September, Dezember großer Verfall)
    for versatz, wann in ((-1, "gestern"), (0, "heute"), (1, "morgen")):
        tag = _handelstag_versatz(heute, versatz) if versatz else heute
        if tag == _dritter_freitag(tag.year, tag.month):
            gross = tag.month in (3, 6, 9, 12)
            aus.append({"art": "verfall", "titel": f"{'Großer ' if gross else ''}Optionsverfall {wann}",
                        "text": "Rund um den Verfall schließen Händler Absicherungen, das kann Kurse ohne Nachricht bewegen."})
    if index_id in ("sp500", "ndx"):
        try:
            o = positionierung.optionen("SPY")
            if o.get("flip"):
                ueber = o["kurs"] >= o["flip"]
                aus.append({"art": "gamma", "titel": f"SPY {'über' if ueber else 'unter'} dem Gamma-Flip ({o['flip']:.0f})",
                            "text": "Darüber dämpfen Händler-Absicherungen eher die Schwankung, darunter können sie Bewegungen verstärken (Schätzung aus SPY-Optionen)."})
        except Exception:
            pass
    vix = chips.get("^VIX")
    if vix and vix.get("sigma") is not None and vix["sigma"] >= UNGEWOEHNLICH:
        aus.append({"art": "vix", "titel": f"VIX-Sprung +{gz(vix['aenderung'])} % ({gz(vix['sigma'])} σ)",
                    "text": "Steigt die Schwankung sprunghaft, verkaufen Fonds mit Volatilitätssteuerung und Trendfolger oft mechanisch nach. Mögliche Erklärung, kein Beleg."})
    if index_id in ("sp500", "ndx"):
        try:
            for p in positionierung.rebalancing()["perioden"]:
                ende = date.fromisoformat(p["ende"])
                tage_bis = sum(1 for d in range((ende - heute).days + 1) if (heute + timedelta(days=d)).weekday() < 5)
                if 0 <= (ende - heute).days and tage_bis <= 3 and abs(p["drift"]) >= 0.5:
                    aus.append({"art": "rebalancing", "titel": f"{p['name']}sende-Rebalancing: {p['richtung']}",
                                "text": f"Ein 60/40-Portfolio weicht um {gz(abs(p['drift']))} Prozentpunkte vom Ziel ab ({'zu viel' if p['drift'] > 0 else 'zu wenig'} Aktien). Große Fonds gleichen das zum Periodenende aus."})
        except Exception:
            pass
    if breite.get("anteil") is not None and idx_pct is not None and abs(idx_pct) >= 0.3:
        gleich = breite["anteil"] if idx_pct > 0 else 100 - breite["anteil"]
        art = "makrogetrieben" if gleich >= 80 else "einzelwertgetrieben" if gleich <= 55 else None
        if art:
            text = f"{gleich:.0f} % der Werte bewegen sich in Richtung des Index."
            try:
                k = signale.korrelation()
                text += f" Die Korrelation der letzten 20 Tage liegt bei {gz(k['k20'], 2)} (Perzentil {k['perzentil20']:.0f})."
            except Exception:
                pass
            aus.append({"art": "korrelation", "titel": f"Heute eher {art}", "text": text})
    if oeffnung.get("overnight") is not None and oeffnung.get("intraday") is not None:
        aus.append({"art": "overnight", "titel": f"Über Nacht {'+' if oeffnung['overnight'] >= 0 else ''}{gz(oeffnung['overnight'], 2)} %, im Handel {'+' if oeffnung['intraday'] >= 0 else ''}{gz(oeffnung['intraday'], 2)} %",
                    "text": f"Gemessen an {oeffnung['quelle']}: Eröffnung gegen Vortagesschluss und Kurs gegen Eröffnung. Kommt der Großteil über Nacht, lag der Auslöser meist außerhalb der US-Handelszeit."})
    if index_id in ("sp500", "ndx") and zeilen:
        beweger = sorted(zeilen, key=lambda z: -abs(z["punkte"]))[:3]
        try:
            for s in signale.short_quote([z["ticker"] for z in beweger]):
                if s.get("z") is not None and abs(s["z"]) >= UNGEWOEHNLICH:
                    aus.append({"art": "short", "titel": f"{s['ticker']}: auffälliges Short-Volumen ({s['aktuell']:.0f} %, z-Wert {gz(s['z'])})",
                                "text": "FINRA-Short-Volumen des letzten gemeldeten Tages (meist Vortag). Hoch heißt oft Market-Maker-Aktivität, nicht unbedingt Wetten auf fallende Kurse."})
        except Exception:
            pass
    return aus


def _pz(v: float, n: int = 1) -> str:
    return f"{'+' if v > 0 else ''}{gz(v, n)} %"


def _liste(namen: list[str]) -> str:
    return namen[0] if len(namen) == 1 else ", ".join(namen[:-1]) + " und " + namen[-1]


def _gruende(name: str, d: dict, chips: dict) -> dict:
    """Die Antwort auf "Warum?": Gründe nach Gewicht, jeder mit Beleg aus den Daten, dazu was es nicht war.

    gewicht: grob der Anteil an der Tagesbewegung, den der Grund erklärt (0 bis 1). Das ist eine Einordnung,
    keine exakte Zerlegung; mehrere Gründe überlappen sich (ein Tech-Wert ist zugleich Teil des Sektors).
    """
    i, b, br = d["index"], d["beitraege"], d["breite"]
    pct, ist = i["prozent"], b["ist"] or 0
    zeilen = d["_zeilen"]
    if pct is None or not zeilen:
        return {"gruende": [], "nicht": None}
    richtung = 1 if ist >= 0 else -1
    wort = "steigen" if richtung > 0 else "fallen"
    gruende = []
    news = {n["ticker"]: n["meldungen"] for n in d.get("news") or []}
    gemeldet = {e["ticker"]: e for e in d["termine"]["earnings"] if e.get("gemeldet")}
    klein = abs(pct) < 0.25

    # 1. Wenige große Werte: die fünf stärksten Beiträge in Richtung des Index
    mit = sorted((z for z in zeilen if z["punkte"] * richtung > 0), key=lambda z: -abs(z["punkte"]))[:5]
    if mit and ist and not klein:
        summe = sum(z["punkte"] for z in mit)
        anteil = summe / ist
        belege = []
        for z in mit[:3]:
            if z["ticker"] in gemeldet:
                e = gemeldet[z["ticker"]]
                belege.append({"text": f"{z['name']} {_pz(z['r'] * 100)} nach Zahlen" + (f" (Gewinn {_pz(e['ueberraschung'], 0)} gegen Erwartung)" if e.get("ueberraschung") is not None else "")})
            elif news.get(z["ticker"]) and abs(z["r"]) < 0.03:
                m = news[z["ticker"]][0]
                belege.append({"text": f"{z['name']} {_pz(z['r'] * 100)}: {m['titel']}", "link": m["link"], "quelle": m["quelle"]})
        gruende.append({
            "art": "werte", "gewicht": min(1.0, abs(anteil)),
            "titel": f"{_liste([z['name'] for z in mit[:3]])} {'tragen' if richtung > 0 else 'drücken'} den Index",
            "text": f"Die fünf größten Beiträge ({_liste([z['ticker'].split('.')[0] for z in mit])}) machen zusammen {gz(abs(summe), 0)} Punkte aus, "
                    f"{gz(abs(anteil) * 100, 0)} % der Tagesbewegung.",
            "belege": belege,
        })

    # 1b. Einzelne Werte mit großer eigener Bewegung und einer Schlagzeile, die sie erklärt
    for z in sorted(zeilen, key=lambda z: -abs(z["punkte"]))[:5]:
        meldungen = news.get(z["ticker"]) or []
        if not meldungen or abs(z["r"]) < 0.03 or not ist or z["ticker"] in gemeldet:
            continue
        anteil = abs(z["punkte"] / ist)
        if anteil < 0.03:
            continue
        gruende.append({
            "art": "nachricht", "gewicht": min(1.0, anteil + 0.3),
            "titel": f"{z['name']} {_pz(z['r'] * 100)}: {meldungen[0]['titel']}",
            "text": f"Beitrag zum Index {'+' if z['punkte'] > 0 else '−'}{gz(abs(z['punkte']))} Punkte ({gz(anteil * 100, 0)} % der Bewegung)"
                    + ("." if z["punkte"] * ist > 0 else ", gegen den Trend."),
            "belege": [{"text": m["titel"], "link": m["link"], "quelle": m["quelle"]} for m in meldungen[:3]],
        })

    # 2. Breite: bewegt sich fast alles in dieselbe Richtung?
    if br.get("anteil") is not None and not klein:
        gleich = br["anteil"] if richtung > 0 else 100 - br["anteil"]
        if gleich >= 70:
            gruende.append({
                "art": "breite", "gewicht": gleich / 100 * 0.9,
                "titel": f"Breite Bewegung: {gz(gleich, 0)} % der Werte {wort}",
                "text": f"Gleichgewichtet liegt der Index bei {_pz(br['gleich'])}. Es sind also nicht nur ein paar Schwergewichte, sondern der ganze Markt; "
                        "das spricht eher für einen gemeinsamen Grund (Stimmung, Zinsen, Nachrichtenlage) als für Einzelmeldungen.",
                "belege": [],
            })
        elif gleich <= 55:
            gruende.append({
                "art": "breite", "gewicht": 0.5,
                "titel": f"Getragen von wenigen: nur {gz(gleich, 0)} % der Werte {wort}",
                "text": f"Gleichgewichtet liegt der Index bei {_pz(br['gleich'])}. Die Bewegung kommt vor allem von großen Einzelwerten.",
                "belege": [],
            })

    # 3. Sektor: einer zieht deutlich mehr als sein Gewicht
    for s in d["sektoren"][:2]:
        if not ist or s["punkte"] is None or s["punkte"] * richtung <= 0 or s["prozent"] is None:
            continue
        anteil = s["punkte"] / ist
        if anteil >= 0.25 and (s["prozent"] - pct) * richtung >= 0.2:
            gruende.append({
                "art": "sektor", "gewicht": min(1.0, anteil),
                "titel": f"{s['name']} {'vorn' if richtung > 0 else 'am schwächsten'} ({_pz(s['prozent'])})",
                "text": f"Der Sektor bringt {gz(abs(s['punkte']), 0)} Punkte, {gz(anteil * 100, 0)} % der Bewegung, bei {gz(s['anteil'], 0)} % Gewicht im Index.",
                "belege": [],
            })

    # 4. Earnings, die den Index spürbar bewegen (auch gegen die Richtung)
    for t, e in gemeldet.items():
        z = next((z for z in zeilen if z["ticker"] == t), None)
        if z and ist and (abs(z["punkte"]) >= 0.05 * abs(ist) or abs(z["r"]) >= 0.04) and not any(t == x["ticker"] for x in mit[:3]):
            gruende.append({
                "art": "earnings", "gewicht": min(1.0, abs(z["punkte"] / ist)),
                "titel": f"Zahlen: {z['name']} {_pz(z['r'] * 100)}",
                "text": (f"Gewinn je Aktie {_pz(e['ueberraschung'], 0)} gegen Erwartung. " if e.get("ueberraschung") is not None else "")
                        + f"Beitrag zum Index {'+' if z['punkte'] > 0 else '−'}{gz(abs(z['punkte']))} Punkte"
                        + ("." if z["punkte"] * ist > 0 else ", gegen den Trend."),
                "belege": [],
            })

    # 5. Verlauf: über Nacht oder im Handel, Umschwung?
    o, h = i.get("overnight"), i.get("intraday")
    if o is not None and h is not None and not klein:
        if o * h < 0 and abs(h) >= 0.5 and abs(o) >= 0.3:
            gruende.append({
                "art": "verlauf", "gewicht": 0.55,
                "titel": f"Umschwung im Handel: eröffnet {_pz(o)}, seitdem {_pz(h)}",
                "text": "Der Markt startete in die andere Richtung. Was den Tag dreht, passierte also während der Handelszeit, nicht über Nacht.",
                "belege": [],
            })
        elif abs(o) >= 0.6 * abs(o + h) and abs(o) >= 0.3:
            gruende.append({
                "art": "verlauf", "gewicht": 0.4,
                "titel": f"Schon zur Eröffnung {_pz(o)}",
                "text": "Der größte Teil der Bewegung kam über Nacht. Der Auslöser lag eher vor Handelsbeginn (Asien, Europa, Nachrichten am Morgen).",
                "belege": [],
            })

    # 6. Makro: ungewöhnliche Bewegungen bei Zinsen, Dollar, Öl, Gold, VIX, Bitcoin
    auffaellig = [c for c in chips.values() if c["sigma"] is not None and abs(c["sigma"]) >= 1.5]
    for c in sorted(auffaellig, key=lambda c: -abs(c["sigma"]))[:2]:
        wert = f"{'+' if c['aenderung'] > 0 else ''}{gz(c['aenderung'], 0 if c['art'] == 'bp' else 1)} {'Bp.' if c['art'] == 'bp' else '%'}"
        gruende.append({
            "art": "makro", "gewicht": min(0.8, abs(c["sigma"]) / 4),
            "titel": f"{c['name']} {wert}, ungewöhnlich stark ({gz(abs(c['sigma']))} σ)",
            "text": "Mehr als an einem üblichen Tag. Steigende Renditen und ein steigender VIX belasten Aktien meist, fallende stützen sie.",
            "belege": [],
        })

    # 7. Termine und mögliche Verstärker aus der Positionierung
    hoch = [t for t in d["termine"]["makro"] if (t.get("wichtigkeit") or "") == "hoch"]
    if hoch:
        t = hoch[0]
        gruende.append({"art": "termin", "gewicht": 0.5, "titel": f"Termin heute: {t['name']}",
                        "text": f"Um {t.get('uhrzeit') or '?'} Uhr." + (f" Ergebnis {t['tatsaechlich']}" + (f", erwartet {t['erwartung']}." if t.get("erwartung") else ".") if t.get("tatsaechlich") else ""),
                        "belege": []})
    verstaerker = [s for s in d.get("signale") or [] if s["art"] in ("vix", "rebalancing", "gamma") or (s["art"] == "verfall" and "morgen" not in s["titel"])]
    if verstaerker and not klein:
        gruende.append({"art": "positionierung", "gewicht": 0.2, "titel": "Mögliche Verstärker: " + "; ".join(s["titel"] for s in verstaerker[:2]),
                        "text": "Mechanische Käufe oder Verkäufe können eine Bewegung verstärken. Mögliche Erklärung, kein Beleg.", "belege": []})

    gruende.sort(key=lambda g: -g["gewicht"])
    # Was es nicht war: Zinsen, Dollar, Öl im üblichen Rahmen
    ruhig = [c["name"] for t, c in chips.items() if t in ("^TNX", "2YY=F", "DX-Y.NYB", "CL=F") and c["sigma"] is not None and abs(c["sigma"]) < 0.7]
    nicht = None
    if ruhig and not klein:
        nicht = f"{_liste(ruhig)} {'bewegen' if len(ruhig) > 1 else 'bewegt'} sich im üblichen Rahmen und {'erklären' if len(ruhig) > 1 else 'erklärt'} den Tag nicht."
        if not hoch:
            nicht += " Wichtige Makro-Termine gab es heute keine."
    if klein:
        nicht = "Ruhiger Tag: Die Bewegung liegt im Bereich des üblichen Rauschens, eine Erklärung braucht es dafür nicht."
    return {"gruende": gruende[:5], "nicht": nicht}


def _fazit(index_id: str, name: str, d: dict, chips: dict, termine: dict, signale_liste: list) -> dict:
    pct, sig = d["index"]["prozent"], d["index"]["sigma"]
    pz = lambda v: f"{v:+.1f} %".replace(".", ",").replace("-", "−")
    pkt = lambda v: f"{abs(v):.0f}"
    b = d["beitraege"]
    ist = b["ist"] or 0
    if pct is None:
        return {"kategorie": "Kein klarer Auslöser", "satz": "Noch keine Kurse für heute."}
    gross = sig is not None and abs(sig) >= 0.8
    # Ereignis-Tag
    hoch = [t for t in termine["makro"] if (t.get("wichtigkeit") or "") == "hoch"]
    if hoch and gross:
        return {"kategorie": "Ereignis-Tag",
                "satz": f"Ereignis-Tag: Heute steht {hoch[0]['name']} an, der {name} liegt {pz(pct)} ({gz(abs(sig))} σ, mehr als an einem üblichen Tag)."}
    # Earnings-Tag
    gemeldet = {e["ticker"] for e in termine["earnings"] if e.get("gemeldet")}
    for z in sorted(d["_zeilen"], key=lambda z: -abs(z["punkte"]))[:5]:
        if z["ticker"] in gemeldet and ist and z["punkte"] * ist > 0 and abs(z["punkte"]) >= 0.3 * abs(ist) and abs(pct) >= 0.2:
            return {"kategorie": "Earnings-Tag", "satz": f"Earnings-Tag: Nach seinen Zahlen bewegt {z['name']} ({pz(z['r'] * 100)}) den {name} um {pkt(z['punkte'])} Punkte, "
                                                         f"{abs(z['punkte']) / abs(ist) * 100:.0f} % der Tagesbewegung."}
    # Zins-Tag
    for t in ("^TNX", "2YY=F"):
        c = chips.get(t)
        if c and c["sigma"] is not None and abs(c["sigma"]) >= 1.5 and gross and c["aenderung"] * pct < 0:
            return {"kategorie": "Zins-Tag", "satz": f"Zins-Tag: Die {c['name'].replace('US-Rendite', 'US-Rendite über')} {'steigt' if c['aenderung'] > 0 else 'fällt'} um "
                                                     f"{abs(c['aenderung']):.0f} Basispunkte ({gz(abs(c['sigma']))} σ), der {name} liegt {pz(pct)}.".replace(" J.", " Jahre")}
    # Energie-Tag
    oel = chips.get("CL=F")
    energie = next((s for s in d["sektoren"][:3] if ENERGIE.search(s["name"])), None)
    if oel and oel["sigma"] is not None and abs(oel["sigma"]) >= 2 and energie and (gross or abs(energie["punkte"] or 0) >= 0.2 * abs(ist)):
        return {"kategorie": "Energie-Tag", "satz": f"Energie-Tag: Öl bewegt sich {pz(oel['aenderung'])} ({gz(abs(oel['sigma']))} σ), Energiewerte tragen "
                                                    f"{'+' if energie['punkte'] > 0 else '−'}{pkt(energie['punkte'])} Punkte zum {name} bei."}
    # Tech-Abverkauf
    tech = [z for z in d["_zeilen"] if TECH.search(z["sektor"])]
    rest = [z for z in d["_zeilen"] if not TECH.search(z["sektor"])]
    if pct < 0 and gross and tech and rest:
        tp = sum(z["punkte"] for z in tech)
        tr = sum(z["r"] * z["cap"] for z in tech) / sum(z["cap"] for z in tech) * 100
        rr = sum(z["r"] * z["cap"] for z in rest) / sum(z["cap"] for z in rest) * 100
        if ist < 0 and tp / ist >= 0.6 and tr <= rr - 0.5:
            namen = [z["name"] for z in sorted(tech, key=lambda z: z["punkte"])[:2]]
            return {"kategorie": "Tech-Abverkauf", "satz": f"Tech-Abverkauf: Technologie- und Kommunikationswerte ({pz(tr)}) kosten den {name} {pkt(tp)} Punkte, "
                                                           f"der Rest liegt bei {pz(rr)}. Am stärksten belasten {' und '.join(namen)}."}
    # Breite Rally
    if pct >= 0.5 and (d["breite"]["anteil"] or 0) >= 75:
        return {"kategorie": "Breite Rally", "satz": f"Breite Rally: {d['breite']['anteil']:.0f} % der Werte im {name} steigen, der Index liegt {pz(pct)}."}
    # Positionierungs-Tag
    pos = [s for s in signale_liste if s["art"] in ("vix", "rebalancing") or (s["art"] == "verfall" and "morgen" not in s["titel"])]
    if pos and sig is not None and abs(sig) >= 1:
        return {"kategorie": "Positionierungs-Tag", "satz": f"Möglicher Positionierungs-Tag: {pos[0]['titel']}. Einen klaren Nachrichten-Auslöser für die Bewegung von {pz(pct)} zeigen die Daten nicht."}
    if sig is not None and abs(sig) < 0.5:
        return {"kategorie": "Kein klarer Auslöser", "satz": f"Kein klarer Auslöser: ruhiger Tag, der {name} liegt {pz(pct)}."}
    br = d["breite"].get("anteil")
    breit = ""
    if br is not None and (br >= 75 or br <= 25):
        breit = f", breit getragen ({br:.0f} % der Werte steigen)" if pct > 0 else f", breit getragen ({100 - br:.0f} % der Werte fallen)"
    return {"kategorie": "Kein klarer Auslöser", "satz": f"Kein klarer Auslöser: Der {name} liegt {pz(pct)}{breit}, ohne dass Zinsen, Öl, Termine, Earnings oder ein Sektor als Treiber herausstechen."}


def berechnen() -> dict:
    """Ein kompletter Durchlauf für alle drei Indizes. Dauert je nach Yahoo 30 bis 90 Sekunden."""
    jetzt = _jetzt()
    mitglieder = _mitglieder()
    alle = sorted({m["ticker"] for liste in mitglieder.values() for m in liste})
    etfs = ["SPY", "QQQ"]
    kurz = _tageskurse(alle + etfs, "7d")
    lang = _tageskurse([i["kurs"] for i in INDEX.values()] + [c[0] for c in CHIPS], "1y")
    chips = _chips(lang)
    chip_von = {c["ticker"]: c for c in chips}
    namen = {m["ticker"]: m["name"] for liste in mitglieder.values() for m in liste}
    heute = jetzt.date()
    termine = _termine(heute, {m["ticker"] for i in ("sp500", "ndx") for m in mitglieder[i]}, {m["ticker"]: m["name"] for m in mitglieder["dax"]} | namen)
    ergebnis = []
    for index_id, info in INDEX.items():
        idx = lang.get(info["kurs"])
        if not idx or len(idx["d"]) < 2:
            ergebnis.append({"id": index_id, "name": info["name"], "fehler": "Keine Indexkurse."})
            continue
        tag, vortag = idx["d"][-1], idx["d"][-2]
        pct, sig = _sigma(idx["c"], "pct")
        lage = _boerse(index_id, jetzt)
        status = "live" if lage == "offen" and tag == _lokales_datum(index_id) else "schluss" if tag == _lokales_datum(index_id) else "vortag"
        b = _beitraege(index_id, mitglieder[index_id], kurz, idx, tag, vortag)
        # Über Nacht gegen im Handel: für die US-Indizes über das ETF (dessen Eröffnung ist ein echter Handelskurs)
        e = kurz.get(info["etf"]) or idx
        oeffnung = {}
        if tag in e["d"] and vortag in e["d"] and e["o"][e["d"].index(tag)]:
            o, c, v = e["o"][e["d"].index(tag)], e["c"][e["d"].index(tag)], e["c"][e["d"].index(vortag)]
            oeffnung = {"overnight": (o / v - 1) * 100, "intraday": (c / o - 1) * 100, "quelle": info["etf"] if info["etf"] != info["kurs"] else info["name"]}
        d = {"id": index_id, "name": info["name"], "status": status, "handelstag": tag, "vortag": vortag,
             "index": {"wert": _f(idx["c"][-1]), "vortag": _f(idx["c"][-2]), "punkte": _f(idx["c"][-1] - idx["c"][-2]), "prozent": _f(pct), "sigma": _f(sig, 1),
                       "overnight": _f(oeffnung.get("overnight")), "intraday": _f(oeffnung.get("intraday"))},
             **{k: v for k, v in b.items() if k != "zeilen"}, "_zeilen": b["zeilen"]}
        mitglieds_set = {m["ticker"] for m in mitglieder[index_id]}
        d["termine"] = {"makro": termine["makro"], "earnings": [x for x in termine["earnings"] if x["ticker"] in mitglieds_set]}
        bewegung = {z["ticker"]: z for z in b["zeilen"]}
        for x in d["termine"]["earnings"]:
            z = bewegung.get(x["ticker"])
            x["prozent"], x["punkte"] = (_f(z["r"] * 100), _f(z["punkte"])) if z else (None, None)
        treiber = sorted(b["zeilen"], key=lambda z: -abs(z["punkte"]))[:5]
        d["news"] = _news([{"ticker": z["ticker"], "name": z["name"], "punkte": _f(z["punkte"])} for z in treiber])
        d["signale"] = _signale(index_id, date.fromisoformat(tag), chip_von, d["breite"], sig, pct, b["zeilen"], oeffnung)
        d["fazit"] = _fazit(index_id, info["name"], d, chip_von, d["termine"], d["signale"])
        d.update(_gruende(info["name"], d, chip_von))
        del d["_zeilen"]
        d["abschluss"] = status == "schluss"
        d["ki"] = ki.zusammenfassung(d, chips)  # Stufe 2: liefert None, solange keine KI eingestellt ist
        ergebnis.append(d)
    return {"indizes": ergebnis, "chips": chips, "aktualisiert": jetzt.isoformat(timespec="seconds")}


# ---------- Speicher und Takt ----------

def _speichern(daten: dict) -> None:
    markt_schreiben("bewegung:aktuell", daten)
    for d in daten["indizes"]:
        if d.get("fehler"):
            continue
        schluessel = f"bewegung:{d['id']}"
        alt = historie_lesen(schluessel).get(d["handelstag"])
        # Eine Abschlussversion wird nicht von einer späteren Rechnung ohne neue Kurse überschrieben
        if alt and alt.get("abschluss") and d["status"] == "vortag":
            continue
        historie_eintragen(schluessel, d["handelstag"], {**d, "chips": daten["chips"], "aktualisiert": daten["aktualisiert"]})


def aktualisieren() -> None:
    if not _sperre.acquire(blocking=False):
        return
    _zustand["rechnet"] = True
    try:
        _speichern(berechnen())
        _zustand["fehler"] = None
    except Exception as fehler:
        _zustand["fehler"] = f"{type(fehler).__name__}: {str(fehler)[:120]}"
    finally:
        _zustand["letzte"] = time.time()
        _zustand["rechnet"] = False
        _sperre.release()


def _faellig(jetzt: datetime) -> bool:
    aktuell, alter = markt_lesen("bewegung:aktuell")
    if aktuell is None:
        return True
    lagen = {i: _boerse(i, jetzt) for i in INDEX}
    if any(l == "offen" for l in lagen.values()):
        return alter >= TAKT_MINUTEN * 60 - 30
    # Abschlussversion: 15 Minuten nach Schluss einmal, wenn der Stand noch nicht nach Schluss gerechnet wurde
    for i, l in lagen.items():
        if l == "nach":
            zone = INDEX[i]["zone"]
            schluss = jetzt.astimezone(zone).replace(hour=INDEX[i]["zu"][0], minute=INDEX[i]["zu"][1], second=0, microsecond=0)
            gerechnet = datetime.fromisoformat(aktuell["aktualisiert"]).astimezone(zone)
            if jetzt.astimezone(zone) >= schluss + timedelta(minutes=15) and gerechnet < schluss + timedelta(minutes=15):
                return True
    return False


def _schleife() -> None:
    time.sleep(40)
    while True:
        try:
            if _faellig(_jetzt()):
                from ..status import job
                with job("Markttreiber"):
                    aktualisieren()
        except Exception:
            pass
        time.sleep(120)


_gestartet = [False]


def starte() -> None:
    if not _gestartet[0]:
        _gestartet[0] = True
        threading.Thread(target=_schleife, daemon=True, name="marktbewegung").start()


# ---------- Für die Seite ----------

def seite(datum: str | None = None) -> dict:
    """Aktueller Stand oder ein gespeicherter Tag. Gibt es noch nichts, startet die Rechnung im Hintergrund."""
    tage = sorted({t for i in INDEX for t in historie_lesen(f"bewegung:{i}")}, reverse=True)
    if datum:
        eintraege = [historie_lesen(f"bewegung:{i}").get(datum) for i in INDEX]
        eintraege = [e for e in eintraege if e]
        if not eintraege:
            return {"fehler": f"Für {datum} ist nichts gespeichert.", "tage": tage}
        return {"indizes": eintraege, "chips": eintraege[0].get("chips", []), "aktualisiert": max(e["aktualisiert"] for e in eintraege),
                "tage": tage, "archiv": True}
    aktuell = markt_lesen("bewegung:aktuell")[0]
    if aktuell is None:
        threading.Thread(target=aktualisieren, daemon=True).start()
        return {"rechnet": True, "tage": tage}
    return {**aktuell, "tage": tage, "rechnet": _zustand["rechnet"], "takt": TAKT_MINUTEN, "fehler_letzte": _zustand["fehler"]}
