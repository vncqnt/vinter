"""Phase 5: Weltkarte. Hier der Teil ohne persönliche Daten.

- maerkte(): Leitindizes aller Länder aus daten/markt/karte/indizes.yaml mit Stand, Veränderung
  Tag/Woche/Jahr und ob die Börse gerade offen ist (eine Sammelanfrage bei Yahoo, 10 Minuten im Speicher).
- waehrungen(): Kurs jeder Landeswährung gegen den Euro mit Veränderung (Ebene "Währungen").
- markt_reaktion(): wie sich die zu einem Brennpunkt passenden Marktwerte bewegen (Öl, Indizes, Reedereien …).
- brennpunkt_news(): Schlagzeilen zu einem Brennpunkt aus Google News und Yahoo, bewertet mit demselben
  Sprachmodell wie die Thesen-News (Ähnlichkeit zu Beschreibung und Suchbegriffen), doppelte zusammengefasst.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml
import yfinance as yf

from ..konfig import MARKT_DATEN
from .abruf import gecacht
from .news import GLEICHE_MELDUNG, GOOGLE_REGIONEN, hole_google, hole_yahoo, lade_modell, mit_cache

INDIZES_DATEI = MARKT_DATEN / "karte" / "indizes.yaml"


class KartenFehler(Exception):
    pass


def indizes_liste() -> list[dict]:
    try:
        roh = yaml.safe_load(INDIZES_DATEI.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise KartenFehler("Die Datei daten/markt/karte/indizes.yaml fehlt.")
    except yaml.YAMLError:
        raise KartenFehler("daten/markt/karte/indizes.yaml ist nicht lesbar. Bitte die zuletzt geänderte Zeile prüfen.")
    return [e for e in roh.get("indizes") or [] if isinstance(e, dict) and e.get("ticker") and e.get("land") is not None]


def _naechster_wechsel(e: dict, jetzt: datetime) -> dict | None:
    """Wann die Börse als Nächstes öffnet bzw. schließt (Minuten), ohne Feiertage."""
    try:
        zone = ZoneInfo(e.get("zone") or "UTC")
        a = [int(x) for x in str(e.get("auf")).split(":")]
        z = [int(x) for x in str(e.get("zu")).split(":")]
    except Exception:
        return None
    t = jetzt.astimezone(zone)
    tage = e.get("tage") or [0, 1, 2, 3, 4]
    for plus in range(0, 8):
        tag = (t + timedelta(days=plus)).date()
        if tag.weekday() not in tage:
            continue
        auf = datetime(tag.year, tag.month, tag.day, a[0], a[1], tzinfo=zone)
        zu = datetime(tag.year, tag.month, tag.day, z[0], z[1], tzinfo=zone)
        if t < auf:
            return {"art": "öffnet", "minuten": int((auf - t).total_seconds() // 60)}
        if t < zu:
            return {"art": "schließt", "minuten": int((zu - t).total_seconds() // 60)}
    return None


def _weltregion(land: int) -> str:
    if land in _NA or land in _LATAM:
        return "Amerika"
    if land in _APAC:
        return "Asien-Pazifik"
    return "Europa, Nahost & Afrika"


def _offen(e: dict, jetzt: datetime) -> bool:
    try:
        t = jetzt.astimezone(ZoneInfo(e.get("zone") or "UTC"))
    except Exception:
        return False
    if t.weekday() not in (e.get("tage") or [0, 1, 2, 3, 4]):
        return False
    auf, zu = (str(e.get(k) or "") for k in ("auf", "zu"))
    try:
        a, z = [int(x) for x in auf.split(":")], [int(x) for x in zu.split(":")]
    except ValueError:
        return False
    return (a[0], a[1]) <= (t.hour, t.minute) < (z[0], z[1])


def maerkte() -> dict:
    liste = indizes_liste()

    def holen():
        ticker = sorted({e["ticker"] for e in liste})
        roh = yf.download(ticker, period="13mo", interval="1d", auto_adjust=False, progress=False, threads=True, group_by="column")
        aus = {}
        for t in ticker:
            try:
                s = roh["Close"][t].dropna()
            except KeyError:
                continue
            if len(s) < 2:
                continue
            s.index = pd.DatetimeIndex(s.index).tz_localize(None)
            letzt = s.index[-1]
            vor = lambda tage: s[s.index <= letzt - pd.Timedelta(days=tage)]
            woche, jahr = vor(7), vor(365)
            jahresanfang = s[s.index < pd.Timestamp(letzt.year, 1, 1)]
            letztes_jahr = s[s.index > letzt - pd.Timedelta(days=365)]
            r = s.pct_change().dropna().iloc[-20:]
            aus[t] = {"wert": float(s.iloc[-1]), "tag": float(s.iloc[-1] / s.iloc[-2] - 1) * 100,
                      "woche": float(s.iloc[-1] / woche.iloc[-1] - 1) * 100 if len(woche) else None,
                      "jahr": float(s.iloc[-1] / jahr.iloc[-1] - 1) * 100 if len(jahr) else None,
                      "ytd": float(s.iloc[-1] / jahresanfang.iloc[-1] - 1) * 100 if len(jahresanfang) else None,
                      "vom_hoch": float(s.iloc[-1] / letztes_jahr.max() - 1) * 100, "vom_tief": float(s.iloc[-1] / letztes_jahr.min() - 1) * 100,
                      "vola": float(r.std() * (252 ** 0.5) * 100) if len(r) >= 10 else None,
                      "stand": letzt.date().isoformat(),
                      "verlauf": [round(float(v), 2) for v in s.iloc[-66:]]}
        if len(aus) < len(ticker) / 2:
            raise ValueError("zu wenige Kurse")
        return aus
    kurse = gecacht("karte:maerkte2", 10 * 60, holen)
    jetzt = datetime.now(timezone.utc)
    laender = []
    for e in liste:
        k = kurse.get(e["ticker"])
        laender.append({"land": str(int(e["land"])).zfill(3), "name": e.get("name"), "index": e.get("index"), "ticker": e["ticker"],
                        "zone": e.get("zone"), "auf": e.get("auf"), "zu": e.get("zu"), "offen": _offen(e, jetzt),
                        "wechsel": _naechster_wechsel(e, jetzt), "weltregion": _weltregion(int(e["land"])),
                        "ortszeit": jetzt.astimezone(ZoneInfo(e.get("zone") or "UTC")).strftime("%H:%M"),
                        **({k2: (round(v, 2) if isinstance(v, float) else v) for k2, v in k.items()} if k else {"fehlt": True})})
    return {"laender": laender, "aktualisiert": jetzt.isoformat(timespec="seconds")}


# ---- Weltregionen (Ländernummern nach ISO 3166) ----
_NA = [840, 124]
_LATAM = [484, 320, 84, 222, 340, 558, 188, 591, 192, 214, 332, 388, 780, 44, 52, 170, 862, 328, 740, 218, 604, 76, 68, 600, 858, 32, 152, 630]
_EUROPA = [8, 20, 40, 112, 56, 70, 100, 191, 196, 203, 208, 233, 246, 250, 276, 300, 348, 352, 372, 380, 428, 438, 440, 442, 470, 498, 492,
           499, 528, 807, 578, 616, 620, 642, 643, 674, 688, 703, 705, 724, 752, 756, 804, 826, 792, 268, 51, 31]
_NAHOST = [48, 364, 368, 376, 400, 414, 422, 512, 634, 682, 760, 784, 887, 275]
_AFRIKA = [12, 24, 204, 72, 854, 108, 120, 132, 140, 148, 174, 178, 180, 384, 262, 818, 226, 232, 748, 231, 266, 270, 288, 324, 624, 404, 426,
           430, 434, 450, 454, 466, 478, 480, 504, 508, 516, 562, 566, 646, 678, 686, 690, 694, 706, 710, 728, 729, 834, 768, 788, 800, 894, 716, 732]
_GROSS_CHINA = [156, 344, 446, 158]
_APAC = [4, 50, 64, 96, 116, 156, 344, 446, 356, 360, 392, 398, 408, 410, 417, 418, 458, 462, 496, 104, 524, 586, 608, 702, 144, 158, 762, 764,
         626, 795, 860, 704, 36, 554, 598, 242, 90, 548]
# ---- Brennpunkte: News bewerten ----
# Warnsignal nur bei Meldungen über Eskalation: Ein laufender Konflikt erzeugt jeden Tag Schlagzeilen
# (Gespräche, Analysen, Gipfel), rot soll ein Punkt aber nur werden, wenn dort etwas passiert.
import re as _re
ESKALATION = _re.compile(
    r"\b(attack\w*|strike[sd]?|struck|missile\w*|rocket\w*|drone attack\w*|blockade\w*|closed|closure|shut|seiz\w+|hijack\w*|hits?|"
    r"explosion\w*|blast|kill\w*|dead|casualt\w*|invasion|invade\w*|troops|warships?|navy|naval clash|drills?|exercises?|encircl\w*|fired|shot down|"
    r"mines?|sank|sunk|escalat\w*|mobiliz\w*|incursion\w*|airspace|clash\w*|shell\w*|bomb\w*|retaliat\w*|ultimatum|evacuat\w*|"
    r"angriff\w*|angegriffen|rakete\w*|blockade|gesperrt|sperrung|beschoss\w*|beschuss|explosion\w*|tote|getötet|man(ö|oe)ver|militärübung\w*|"
    r"eskal\w*|drohnenangriff\w*|minen|versenkt|gekapert|truppen|kriegsschiff\w*|vergeltung\w*|luftraum)\b", _re.I)

def brennpunkt_news(punkt: dict, schwelle: float) -> dict:
    """Relevante Schlagzeilen der letzten 7 Tage, neueste zuerst, dazu Zahl und Stärke der letzten 24 Stunden."""
    auftraege = []
    for begriff in punkt.get("suchbegriffe") or []:
        for hl, gl, ceid in GOOGLE_REGIONEN:
            auftraege.append((f"news-g:{hl}:{begriff}", lambda b=begriff, hl=hl, gl=gl, ceid=ceid: hole_google(b, hl, gl, ceid)))
    for t in punkt.get("positionen") or []:
        auftraege.append((f"news-y:{t}", lambda t=t: hole_yahoo(t)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        ergebnisse = list(pool.map(lambda a: mit_cache(*a), auftraege))
    fehler = sum(1 for _, f in ergebnisse if f)
    jetzt = datetime.now(timezone.utc)
    grenze = jetzt - timedelta(days=7)
    eindeutig = {}
    for meldungen, _ in ergebnisse:
        for m in meldungen:
            if datetime.fromisoformat(m["zeit"]) >= grenze:
                eindeutig.setdefault(" ".join(m["titel"].lower().split()), m)
    meldungen = sorted(eindeutig.values(), key=lambda m: m["zeit"], reverse=True)[:150]
    if not meldungen:
        return {"meldungen": [], "anzahl24": 0, "relevant24": 0, "stufe": 0, "fehler": fehler, "verlauf": []}
    bezug = [x for x in [punkt.get("beschreibung")] + list(punkt.get("suchbegriffe") or []) if x]
    modell = lade_modell()
    vektoren = modell.encode([m["titel"] for m in meldungen], normalize_embeddings=True)
    relevanz = (vektoren @ modell.encode(bezug, normalize_embeddings=True).T).max(axis=1)
    behalten, aus = [], []
    for i in np.argsort(-relevanz):
        if relevanz[i] < schwelle:
            break
        doppelt = next((n for n, k in enumerate(behalten) if float(vektoren[i] @ vektoren[k]) >= GLEICHE_MELDUNG), None)
        if doppelt is not None:
            aus[doppelt]["weitere"] += 1
            continue
        behalten.append(int(i))
        aus.append({**meldungen[i], "relevanz": round(float(relevanz[i]), 3), "weitere": 0, "eskalation": bool(ESKALATION.search(meldungen[i]["titel"]))})
    aus.sort(key=lambda m: m["zeit"], reverse=True)
    tag = [m for m in aus if datetime.fromisoformat(m["zeit"]) >= jetzt - timedelta(hours=24)]
    warn = [m for m in tag if m["eskalation"]]
    # Stärke: Zahl der Eskalations-Meldungen, dieselbe Geschichte aus mehreren Quellen zählt etwas mehr
    staerke = sum(1 + min(3, m["weitere"]) * 0.5 for m in warn)
    stufe = 0 if not warn else 2 if staerke >= 4 else 1
    verlauf = []
    for t in range(6, -1, -1):  # 7 Tage, ältester zuerst; jeder Tag = 24 Stunden rückwärts ab jetzt
        bis, von = jetzt - timedelta(hours=24 * t), jetzt - timedelta(hours=24 * (t + 1))
        im_tag = [m for m in aus if von <= datetime.fromisoformat(m["zeit"]) < bis]
        verlauf.append({"bis": bis.isoformat(timespec="minutes"), "eskalation": sum(1 for m in im_tag if m["eskalation"]), "alle": len(im_tag)})
    return {"meldungen": aus[:25], "anzahl24": len(warn), "relevant24": len(tag), "stufe": stufe, "fehler": fehler, "verlauf": verlauf}


# ---- Marktreaktion zu Brennpunkten ----
MARKT_NAMEN = {
    "BZ=F": "Öl Brent", "CL=F": "Öl WTI", "NG=F": "Erdgas USA", "TTF=F": "Erdgas Europa", "GC=F": "Gold", "ZW=F": "Weizen", "HG=F": "Kupfer",
    "FRO": "Frontline (Tanker)", "ZIM": "ZIM (Reederei)", "MAERSK-B.CO": "Maersk", "BDRY": "Frachtraten Massengut", "TSM": "TSMC", "SOXX": "Halbleiter-ETF",
    "^TWII": "TAIEX", "^HSI": "Hang Seng", "^KS11": "KOSPI", "^BSESN": "Sensex", "TA35.TA": "TA-35", "PSEI.PS": "PSEi", "RHM.DE": "Rheinmetall",
    "ITA": "Rüstungs-ETF USA", "LMT": "Lockheed Martin", "KRW=X": "Won je Dollar", "INR=X": "Rupie je Dollar", "^VIX": "VIX", "DX-Y.NYB": "Dollar-Index",
}


def markt_reaktion(ticker: list[str]) -> dict:
    """{Ticker: {name, wert, tag, woche}} für die Marktwerte der Brennpunkte (eine Sammelanfrage, 10 Minuten im Speicher)."""
    ticker = sorted({t for t in ticker if t})
    if not ticker:
        return {}

    def holen():
        roh = yf.download(ticker, period="1mo", interval="1d", auto_adjust=False, progress=False, threads=True, group_by="column")
        aus = {}
        for t in ticker:
            try:
                s2 = roh["Close"][t].dropna() if len(ticker) > 1 else roh["Close"].dropna().iloc[:, 0]
            except (KeyError, IndexError):
                continue
            if len(s2) < 2:
                continue
            s2.index = pd.DatetimeIndex(s2.index).tz_localize(None)
            woche = s2[s2.index <= s2.index[-1] - pd.Timedelta(days=7)]
            aus[t] = {"name": MARKT_NAMEN.get(t, t), "wert": round(float(s2.iloc[-1]), 4), "tag": round(float(s2.iloc[-1] / s2.iloc[-2] - 1) * 100, 2),
                      "woche": round(float(s2.iloc[-1] / woche.iloc[-1] - 1) * 100, 2) if len(woche) else None}
        return aus
    return gecacht("karte:reaktion:" + ",".join(ticker), 10 * 60, holen)


# ---- Währungen gegen den Euro ----
EURO = [40, 56, 100, 191, 196, 233, 246, 250, 276, 300, 372, 380, 428, 440, 442, 470, 528, 620, 703, 705, 724]
WAEHRUNG = {
    840: "USD", 124: "CAD", 484: "MXN", 76: "BRL", 152: "CLP", 170: "COP", 604: "PEN", 826: "GBP", 756: "CHF", 752: "SEK", 578: "NOK",
    208: "DKK", 616: "PLN", 348: "HUF", 203: "CZK", 642: "RON", 792: "TRY", 376: "ILS", 682: "SAR", 784: "AED", 634: "QAR", 414: "KWD",
    818: "EGP", 710: "ZAR", 566: "NGN", 404: "KES", 504: "MAD", 392: "JPY", 156: "CNY", 344: "HKD", 410: "KRW", 158: "TWD", 356: "INR",
    702: "SGD", 360: "IDR", 458: "MYR", 764: "THB", 608: "PHP", 704: "VND", 586: "PKR", 36: "AUD", 554: "NZD", 398: "KZT", 352: "ISK",
}
WAEHRUNG_NAME = {
    "USD": "US-Dollar", "CAD": "Kanadischer Dollar", "MXN": "Mexikanischer Peso", "BRL": "Brasilianischer Real", "CLP": "Chilenischer Peso",
    "COP": "Kolumbianischer Peso", "PEN": "Peruanischer Sol", "GBP": "Britisches Pfund", "CHF": "Schweizer Franken", "SEK": "Schwedische Krone",
    "NOK": "Norwegische Krone", "DKK": "Dänische Krone", "PLN": "Polnischer Złoty", "HUF": "Ungarischer Forint", "CZK": "Tschechische Krone",
    "RON": "Rumänischer Leu", "TRY": "Türkische Lira", "ILS": "Israelischer Schekel", "SAR": "Saudi-Riyal", "AED": "VAE-Dirham", "QAR": "Katar-Riyal",
    "KWD": "Kuwait-Dinar", "EGP": "Ägyptisches Pfund", "ZAR": "Südafrikanischer Rand", "NGN": "Nigerianischer Naira", "KES": "Kenia-Schilling",
    "MAD": "Marokkanischer Dirham", "JPY": "Japanischer Yen", "CNY": "Chinesischer Yuan", "HKD": "Hongkong-Dollar", "KRW": "Südkoreanischer Won",
    "TWD": "Taiwan-Dollar", "INR": "Indische Rupie", "SGD": "Singapur-Dollar", "IDR": "Indonesische Rupiah", "MYR": "Malaysischer Ringgit",
    "THB": "Thailändischer Baht", "PHP": "Philippinischer Peso", "VND": "Vietnamesischer Dong", "PKR": "Pakistanische Rupie",
    "AUD": "Australischer Dollar", "NZD": "Neuseeland-Dollar", "KZT": "Kasachischer Tenge", "ISK": "Isländische Krone",
}


def waehrungen() -> dict:
    """Jede Währung gegen den Euro. Veränderung aus Sicht eines Euro-Anlegers: + heißt, die Währung ist mehr Euro wert geworden."""
    codes = sorted(set(WAEHRUNG.values()))

    def holen():
        roh = yf.download([f"EUR{c}=X" for c in codes], period="13mo", interval="1d", auto_adjust=False, progress=False, threads=True, group_by="column")
        aus = {}
        for c in codes:
            try:
                s2 = roh["Close"][f"EUR{c}=X"].dropna()
            except KeyError:
                continue
            s2 = s2[s2 > 0]
            if len(s2) < 2:
                continue
            s2.index = pd.DatetimeIndex(s2.index).tz_localize(None)
            letzt = s2.index[-1]
            wert_eur = 1 / s2  # so viel Euro ist eine Einheit der Währung wert
            vor = lambda tage: wert_eur[wert_eur.index <= letzt - pd.Timedelta(days=tage)]
            jahresanfang = wert_eur[wert_eur.index < pd.Timestamp(letzt.year, 1, 1)]
            veraenderung = lambda alt: float(wert_eur.iloc[-1] / alt.iloc[-1] - 1) * 100 if len(alt) else None
            aus[c] = {"code": c, "name": WAEHRUNG_NAME.get(c, c), "kurs": float(s2.iloc[-1]), "tag": float(wert_eur.iloc[-1] / wert_eur.iloc[-2] - 1) * 100,
                      "woche": veraenderung(vor(7)), "monat": veraenderung(vor(30)), "ytd": veraenderung(jahresanfang), "jahr": veraenderung(vor(365)),
                      "stand": letzt.date().isoformat(), "verlauf": [float(v) for v in wert_eur.iloc[-66:]]}
        if len(aus) < len(codes) / 2:
            raise ValueError("zu wenige Kurse")
        return aus
    daten = gecacht("karte:waehrungen", 30 * 60, holen)
    return {"waehrungen": daten, "laender": {str(k).zfill(3): v for k, v in WAEHRUNG.items()}, "euro": [str(k).zfill(3) for k in EURO]}
