"""Makro-Daten mit Einordnung (Punkt 28): echte Zahlen aus offiziellen, kostenlosen Quellen, ohne Key.

Quellen (alle ohne Key, außer Census, dessen Gratis-Key schon für die Handelsdaten da ist):
- BLS Public API v1 (US-Arbeitsmarktbehörde): CPI, Kern-CPI, PPI, Arbeitslosenquote, Beschäftigte (Payrolls),
  Stundenlöhne. Höchstens 25 Abfragen am Tag ohne Key; Vinter braucht zwei je 12 Stunden.
- BEA (US-Wirtschaftsministerium), Sammeldateien NipaDataQ/NipaDataM: BIP-Wachstum, PCE- und Kern-PCE-Preisindex.
  Rund 70 MB, deshalb nur bei geänderter Datei (Last-Modified) neu geladen.
- Census (Einzelhandelsumsätze, MARTS).
- FRED (CSV ohne Key): Erstanträge auf Arbeitslosenhilfe, Fed-Zielspanne. Fällt FRED aus, gilt der letzte Stand.
- Eurostat: HVPI und Kern-HVPI (seit 2026 im Datensatz prc_hicp_minr nach ECOICOP 2).
- EZB Data Portal: Arbeitslosenquote, BIP-Wachstum, Einlagensatz.

Einordnung je Kennzahl (feste Regeln, keine KI): Wert, Vorwert, Verlauf, Normalbereich, Perzentil der letzten
10 Jahre und eine Ampel heiß/neutral/kalt aus Sicht der Zinspolitik.
"""
import io
import time
from datetime import date

import pandas as pd
import requests

from .abruf import STUNDE, fred, gecacht
from ..cache import markt_lesen, markt_schreiben

KOPF = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Vinter/1.0"}
BEA = "https://apps.bea.gov/national/Release/TXT/"


# ---------------------------------------------------------------- Einlesen (reine Funktionen, getestet)

def bls_lesen(antwort: dict) -> dict[str, pd.Series]:
    """BLS-JSON → je Reihe eine Monatsreihe. Perioden M01…M12, M13 (Jahresschnitt) entfällt."""
    aus = {}
    for s in antwort.get("Results", {}).get("series", []):
        werte = {}
        for d in s.get("data", []):
            p = d.get("period", "")
            if not (p.startswith("M") and p != "M13"):
                continue
            try:
                werte[pd.Timestamp(int(d["year"]), int(p[1:]), 1)] = float(d["value"])
            except (ValueError, KeyError):
                continue
        aus[s["seriesID"]] = pd.Series(werte, dtype=float).sort_index()
    return aus


def bea_lesen(text: str, codes: set[str]) -> dict[str, pd.Series]:
    """BEA-Sammeldatei (SeriesCode,Period,Value; Perioden 2026Q3 oder 2026M09, Werte mit Tausenderkomma)."""
    aus: dict[str, dict] = {c: {} for c in codes}
    for zeile in text.splitlines():
        code, _, rest = zeile.partition(",")
        if code not in codes:
            continue
        periode, _, wert = rest.partition(",")
        try:
            w = float(wert.strip().strip('"').replace(",", ""))
        except ValueError:
            continue
        if "Q" in periode:
            j, q = periode.split("Q")
            tag = pd.Timestamp(int(j), int(q) * 3 - 2, 1)
        elif "M" in periode:
            j, m = periode.split("M")
            tag = pd.Timestamp(int(j), int(m), 1)
        else:
            continue
        aus[code][tag] = w
    return {c: pd.Series(v, dtype=float).sort_index() for c, v in aus.items()}


def census_lesen(zeilen: list[list]) -> pd.Series:
    kopf = zeilen[0]
    iw, iz = kopf.index("cell_value"), kopf.index("time")
    return pd.Series({pd.Timestamp(z[iz] + "-01"): float(z[iw]) for z in zeilen[1:]}, dtype=float).sort_index()


def eurostat_lesen(d: dict) -> pd.Series:
    """JSON-stat mit genau einer Reihe: value ist nach der Position in der Zeit-Dimension geordnet."""
    zeiten = d["dimension"]["time"]["category"]["index"]
    werte = {}
    for monat, pos in zeiten.items():
        w = d["value"].get(str(pos))
        if w is not None:
            werte[pd.Timestamp(monat + "-01")] = float(w)
    return pd.Series(werte, dtype=float).sort_index()


def ezb_lesen(text: str) -> pd.Series:
    tab = pd.read_csv(io.StringIO(text))
    zeit = tab["TIME_PERIOD"].astype(str)
    zeit = zeit.str.replace(r"^(\d{4})-Q(\d)$", lambda m: f"{m.group(1)}-{int(m.group(2)) * 3 - 2:02d}", regex=True)
    return pd.Series(tab["OBS_VALUE"].astype(float).values, index=pd.to_datetime(zeit)).sort_index()


# ---------------------------------------------------------------- Abruf

def _als(daten: dict) -> pd.Series:
    s = pd.Series(daten, dtype=float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def _ser(s: pd.Series) -> dict:
    return {d.strftime("%Y-%m-%d"): float(w) for d, w in s.dropna().items()}


# CUSR…/WPSFD4 saisonbereinigt (für den Verlauf), CUUR…/WPUFD4 unbereinigt: so melden BLS und Presse die Jahresrate
BLS_REIHEN = ["CUSR0000SA0", "CUSR0000SA0L1E", "WPSFD4", "LNS14000000", "CES0000000001", "CES0500000003", "CUUR0000SA0", "CUUR0000SA0L1E", "WPUFD4"]


def _bls() -> dict[str, pd.Series]:
    def holen():
        jahr = date.today().year
        gesamt: dict[str, pd.Series] = {}
        for von in (jahr - 19, jahr - 9):   # v1 erlaubt 10 Jahre je Abfrage
            r = requests.post("https://api.bls.gov/publicAPI/v1/timeseries/data/", headers=KOPF, timeout=60,
                              json={"seriesid": BLS_REIHEN, "startyear": str(von), "endyear": str(von + 9)})
            r.raise_for_status()
            d = r.json()
            if d.get("status") != "REQUEST_SUCCEEDED":
                raise RuntimeError("BLS: " + "; ".join(d.get("message", [])))
            for k, s in bls_lesen(d).items():
                gesamt[k] = pd.concat([gesamt[k], s]) if k in gesamt else s
        return {k: _ser(s[~s.index.duplicated(keep="last")]) for k, s in gesamt.items()}
    return {k: _als(v) for k, v in gecacht("makro:bls", 12 * STUNDE, holen).items()}


BEA_CODES = {"NipaDataQ.txt": {"A191RL"}, "NipaDataM.txt": {"DPCERG", "DPCCRG"}}


def _bea() -> dict[str, pd.Series]:
    """BEA-Dateien nur laden, wenn sie sich geändert haben (die Dateien sind groß)."""
    alt, alter = markt_lesen("makro:bea")
    if alt is not None and alter < 12 * STUNDE:
        return {k: _als(v) for k, v in alt["reihen"].items()}
    neu = {"reihen": dict(alt["reihen"]) if alt else {}, "stand": dict(alt["stand"]) if alt else {}}
    try:
        for datei, codes in BEA_CODES.items():
            kopf = dict(KOPF)
            if neu["stand"].get(datei) and all(c in neu["reihen"] for c in codes):
                kopf["If-Modified-Since"] = neu["stand"][datei]
            r = requests.get(BEA + datei, headers=kopf, timeout=120)
            if r.status_code == 304:
                continue
            r.raise_for_status()
            for c, s in bea_lesen(r.text, codes).items():
                neu["reihen"][c] = _ser(s)
            neu["stand"][datei] = r.headers.get("Last-Modified", "")
        markt_schreiben("makro:bea", neu)
    except Exception:
        if not alt:
            raise
    return {k: _als(v) for k, v in neu["reihen"].items()}


def _census_einzelhandel() -> pd.Series:
    from ..einstellungen import key

    def holen():
        k = key("CENSUS_API_KEY")
        if not k:
            raise LookupError("CENSUS_API_KEY fehlt")
        r = requests.get("https://api.census.gov/data/timeseries/eits/marts", headers=KOPF, timeout=60, params={
            "get": "cell_value,time_slot_id", "time": f"from {date.today().year - 12}", "category_code": "44X72",
            "data_type_code": "SM", "seasonally_adj": "yes", "key": k})
        r.raise_for_status()
        return _ser(census_lesen(r.json()))
    return _als(gecacht("makro:census_marts", 12 * STUNDE, holen))


def _eurostat(coicop: str) -> pd.Series:
    def holen():
        r = requests.get("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr", headers=KOPF, timeout=60,
                         params={"geo": "EA", "unit": "RCH_A", "coicop18": coicop, "sinceTimePeriod": f"{date.today().year - 12}-01"})
        r.raise_for_status()
        return _ser(eurostat_lesen(r.json()))
    return _als(gecacht(f"makro:eurostat:{coicop}", 12 * STUNDE, holen))


# Euroraum in verschiedenen Abgrenzungen (U2 = jeweilige Zusammensetzung, I9 = 20 Länder, I10 = 21 Länder ab 2026)
EURORAUM = {"U2", "I8", "I9", "I10", "I11"}


def _ezb(schluessel: str) -> pd.Series:
    """EZB-Reihe; bei mehreren Euroraum-Abgrenzungen (Erweiterung 2026) die mit dem neuesten Wert."""
    def holen():
        r = requests.get(f"https://data-api.ecb.europa.eu/service/data/{schluessel}", headers=KOPF, timeout=90,
                         params={"format": "csvdata", "startPeriod": f"{date.today().year - 12}", "detail": "dataonly"})
        r.raise_for_status()
        tab = pd.read_csv(io.StringIO(r.text))
        beste = None
        gruppen = list(tab.groupby("KEY"))
        for schl, g in gruppen:
            if len(gruppen) > 1 and not EURORAUM & set(str(schl).split(".")):
                continue  # Platzhalter in der Abfrage liefert auch einzelne Länder
            s = ezb_lesen(g.to_csv(index=False))
            if beste is None or s.index.max() > beste.index.max() or (s.index.max() == beste.index.max() and len(s) > len(beste)):
                beste = s
        return _ser(beste)
    return _als(gecacht(f"makro:ezb:{schluessel}", 12 * STUNDE, holen))


def _fred(serie: str) -> pd.Series:
    return fred(serie)


# ---------------------------------------------------------------- Kennzahlen

# art: jahr = Veränderung zum Vorjahr in % aus einem Index; stand = Wert wie gemeldet; diff = Veränderung zum Vormonat
# heiss: hoch = ein hoher Wert spricht für höhere Zinsen, tief = ein niedriger Wert; None = keine Ampel
KENNZAHLEN = [
    {"id": "cpi", "region": "USA", "gruppe": "Inflation", "name": "Inflation (CPI)", "quelle": "BLS, CUSR0000SA0", "holen": ("bls", "CUSR0000SA0"),
     "art": "jahr", "einheit": "%", "normal": (1.7, 2.7), "heiss": "hoch", "ziel": "Fed-Ziel 2 % (gilt für PCE; der CPI liegt meist 0,3 bis 0,5 Punkte darüber)",
     "info": "Verbraucherpreise in den USA gegenüber dem Vorjahresmonat, saisonbereinigt. Die bekannteste Inflationszahl; bewegt Zinsen und Aktien am Tag der Veröffentlichung oft stark."},
    {"id": "kern_cpi", "region": "USA", "gruppe": "Inflation", "name": "Kern-Inflation (CPI ohne Energie und Lebensmittel)", "quelle": "BLS, CUSR0000SA0L1E", "holen": ("bls", "CUSR0000SA0L1E"),
     "art": "jahr", "einheit": "%", "normal": (1.8, 2.8), "heiss": "hoch", "ziel": "Fed-Ziel 2 % (für PCE)",
     "info": "Wie der CPI, aber ohne die schwankenden Energie- und Lebensmittelpreise. Zeigt den hartnäckigen Teil der Inflation, auf den die Fed besonders schaut."},
    {"id": "pce", "region": "USA", "gruppe": "Inflation", "name": "PCE-Inflation", "quelle": "BEA, DPCERG", "holen": ("bea", "DPCERG"),
     "art": "jahr", "einheit": "%", "normal": (1.5, 2.5), "heiss": "hoch", "ziel": "Fed-Ziel 2 %",
     "info": "Preise der privaten Konsumausgaben gegenüber dem Vorjahr. Das ist die Zahl, an der die Fed ihr 2-%-Ziel misst; erscheint etwa zwei Wochen nach dem CPI."},
    {"id": "kern_pce", "region": "USA", "gruppe": "Inflation", "name": "Kern-PCE", "quelle": "BEA, DPCCRG", "holen": ("bea", "DPCCRG"),
     "art": "jahr", "einheit": "%", "normal": (1.5, 2.5), "heiss": "hoch", "ziel": "Fed-Ziel 2 %",
     "info": "PCE ohne Energie und Lebensmittel: die Lieblingszahl der Fed für den Inflationstrend."},
    {"id": "ppi", "region": "USA", "gruppe": "Inflation", "name": "Erzeugerpreise (PPI)", "quelle": "BLS, WPSFD4", "holen": ("bls", "WPSFD4"),
     "art": "jahr", "einheit": "%", "normal": (0.5, 3.0), "heiss": "hoch", "ziel": "kein offizielles Ziel",
     "info": "Preise, die Unternehmen für ihre Waren und Dienstleistungen bekommen (Endnachfrage), gegenüber dem Vorjahr. Läuft den Verbraucherpreisen oft etwas voraus."},
    {"id": "arbeitslos", "region": "USA", "gruppe": "Arbeitsmarkt", "name": "Arbeitslosenquote", "quelle": "BLS, LNS14000000", "holen": ("bls", "LNS14000000"),
     "art": "stand", "einheit": "%", "normal": (4.0, 4.6), "heiss": "tief", "ziel": "Normalbereich etwa 4 bis 4,6 % (geschätzte natürliche Quote)",
     "info": "Anteil der Arbeitssuchenden an allen Erwerbspersonen. Sehr niedrig heißt: Arbeitskräfte sind knapp, Löhne und Inflation steigen eher. Ein schneller Anstieg ging oft einer Rezession voraus (Sahm-Regel)."},
    {"id": "payrolls", "region": "USA", "gruppe": "Arbeitsmarkt", "name": "Neue Stellen (Payrolls)", "quelle": "BLS, CES0000000001", "holen": ("bls", "CES0000000001"),
     "art": "diff", "einheit": "Tsd.", "normal": (75, 200), "heiss": "hoch", "ziel": "Ampel nach dem 3-Monats-Schnitt; rund 75.000 bis 200.000 neue Stellen im Monat gelten als normal",
     "info": "Veränderung der Beschäftigtenzahl außerhalb der Landwirtschaft gegenüber dem Vormonat (Arbeitsmarktbericht, erster Freitag im Monat). Einzelne Monate werden oft deutlich revidiert, deshalb zählt der 3-Monats-Schnitt."},
    {"id": "loehne", "region": "USA", "gruppe": "Arbeitsmarkt", "name": "Stundenlöhne", "quelle": "BLS, CES0500000003", "holen": ("bls", "CES0500000003"),
     "art": "jahr", "einheit": "%", "normal": (3.0, 4.0), "heiss": "hoch", "ziel": "etwa 3 bis 3,5 % passen zu 2 % Inflation",
     "info": "Durchschnittlicher Stundenlohn in der Privatwirtschaft gegenüber dem Vorjahr. Steigen Löhne deutlich schneller als die Produktivität, drückt das auf die Preise."},
    {"id": "erstantraege", "region": "USA", "gruppe": "Arbeitsmarkt", "name": "Erstanträge auf Arbeitslosenhilfe", "quelle": "FRED, ICSA (Arbeitsministerium)", "holen": ("fred", "ICSA"),
     "art": "stand", "einheit": "Tsd.", "faktor": 0.001, "normal": (210, 260), "heiss": "tief", "ziel": "Ampel nach dem 4-Wochen-Schnitt",
     "info": "Neue Anträge auf Arbeitslosenhilfe je Woche (jeden Donnerstag). Die schnellste Zahl zum Arbeitsmarkt: Steigen die Anträge über Wochen, werden mehr Leute entlassen."},
    {"id": "bip", "region": "USA", "gruppe": "Wachstum", "name": "Wirtschaftswachstum (BIP)", "quelle": "BEA, A191RL", "holen": ("bea", "A191RL"),
     "art": "stand", "einheit": "%", "normal": (1.5, 3.0), "heiss": "hoch", "ziel": "Trendwachstum etwa 1,8 bis 2 %",
     "info": "Reales Bruttoinlandsprodukt, Veränderung zum Vorquartal auf das Jahr hochgerechnet (so meldet es die BEA). Erscheint quartalsweise und wird zweimal überarbeitet."},
    {"id": "einzelhandel", "region": "USA", "gruppe": "Wachstum", "name": "Einzelhandelsumsätze", "quelle": "Census, MARTS 44X72", "holen": ("census", None),
     "art": "jahr", "einheit": "%", "normal": (2.5, 5.5), "heiss": "hoch", "ziel": "nominal, also mit Inflation",
     "info": "Umsätze von Einzelhandel und Gastronomie gegenüber dem Vorjahr, saisonbereinigt, nicht inflationsbereinigt. Zeigt, wie viel die Verbraucher ausgeben (Konsum ist rund zwei Drittel der US-Wirtschaft)."},
    {"id": "leitzins", "region": "USA", "gruppe": "Zinsen", "name": "Fed-Leitzins (Obergrenze)", "quelle": "FRED, DFEDTARU", "holen": ("fred", "DFEDTARU"),
     "art": "stand", "einheit": "%", "normal": None, "heiss": None, "ziel": "Realzins = Leitzins minus Kern-PCE",
     "info": "Obergrenze der Zielspanne für den Tagesgeldsatz. Der Realzins (Leitzins minus Kern-Inflation) zeigt, wie stark die Geldpolitik bremst: über etwa 1 % gilt sie als straff."},
    {"id": "hvpi", "region": "Eurozone", "gruppe": "Inflation", "name": "Inflation (HVPI)", "quelle": "Eurostat, prc_hicp_minr TOTAL", "holen": ("eurostat", "TOTAL"),
     "art": "stand", "einheit": "%", "normal": (1.5, 2.5), "heiss": "hoch", "ziel": "EZB-Ziel 2 %",
     "info": "Harmonisierter Verbraucherpreisindex des Euroraums gegenüber dem Vorjahresmonat (Eurostat). An ihm misst die EZB ihr 2-%-Ziel."},
    {"id": "kern_hvpi", "region": "Eurozone", "gruppe": "Inflation", "name": "Kern-Inflation (ohne Energie, Lebensmittel, Alkohol, Tabak)", "quelle": "Eurostat, TOT_X_NRG_FOOD", "holen": ("eurostat", "TOT_X_NRG_FOOD"),
     "art": "stand", "einheit": "%", "normal": (1.5, 2.5), "heiss": "hoch", "ziel": "EZB-Ziel 2 %",
     "info": "HVPI ohne die schwankenden Teile: zeigt den Inflationstrend im Euroraum."},
    {"id": "ez_arbeitslos", "region": "Eurozone", "gruppe": "Arbeitsmarkt", "name": "Arbeitslosenquote", "quelle": "EZB, LFSI", "holen": ("ezb", "LFSI/M..S.UNEHRT.TOTAL0.15_74.T"),
     "art": "stand", "einheit": "%", "normal": (6.0, 7.0), "heiss": "tief", "ziel": "Normalbereich um 6 bis 7 % (Euroraum hat strukturell mehr Arbeitslose als die USA)",
     "info": "Arbeitslose im Alter von 15 bis 74 Jahren im Euroraum, saisonbereinigt."},
    {"id": "ez_bip", "region": "Eurozone", "gruppe": "Wachstum", "name": "Wirtschaftswachstum (BIP)", "quelle": "EZB, MNA", "holen": ("ezb", "MNA/Q.Y..W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.LR.GY"),
     "art": "stand", "einheit": "%", "normal": (0.5, 2.0), "heiss": "hoch", "ziel": "Trendwachstum etwa 1 bis 1,5 %",
     "info": "Reales BIP des Euroraums gegenüber dem Vorjahresquartal (Achtung: anders gerechnet als die US-Zahl, die aufs Jahr hochgerechnet wird)."},
    {"id": "ezb_zins", "region": "Eurozone", "gruppe": "Zinsen", "name": "EZB-Einlagensatz", "quelle": "EZB, FM DFR", "holen": ("ezb", "FM/D.U2.EUR.4F.KR.DFR.LEV"),
     "art": "stand", "einheit": "%", "normal": None, "heiss": None, "ziel": "Realzins = Einlagensatz minus Kern-Inflation",
     "info": "Zins, den Banken für Einlagen bei der EZB bekommen: seit 2022 der eigentliche Leitzins im Euroraum."},
]


def _rohreihe(k: dict, cache: dict) -> pd.Series:
    quelle, sid = k["holen"]
    if quelle == "bls":
        cache.setdefault("bls", None)
        if cache["bls"] is None:
            cache["bls"] = _bls()
        return cache["bls"][sid]
    if quelle == "bea":
        if cache.get("bea") is None:
            cache["bea"] = _bea()
        return cache["bea"][sid]
    if quelle == "census":
        return _census_einzelhandel()
    if quelle == "eurostat":
        return _eurostat(sid)
    if quelle == "ezb":
        return _ezb(sid)
    if quelle == "fred":
        return _fred(sid)
    raise ValueError(quelle)


def luecken_fuellen(s: pd.Series) -> pd.Series:
    """Einzelne fehlende Monate eines Monatsindex aus den Nachbarmonaten schätzen (geometrisches Mittel).
    Beispiel: Der US-CPI für Oktober 2025 wurde wegen des Shutdowns nie erhoben. Reine Funktion."""
    if len(s) < 3 or (s <= 0).any():
        return s
    voll = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="MS"))
    if voll.notna().all() or len(voll) > len(s) * 1.5:   # keine Monatsreihe oder zu viele Lücken
        return s
    import numpy as np
    geschaetzt = np.exp(np.log(voll).interpolate(limit=1, limit_area="inside"))
    return geschaetzt.dropna()


def umformen(roh: pd.Series, art: str, faktor: float = 1.0) -> pd.Series:
    """Reine Funktion: Rohreihe → angezeigte Reihe (Veränderung zum Vorjahr, Vormonat oder Wert wie gemeldet)."""
    s = roh.dropna().sort_index() * faktor
    if art == "jahr":
        s = luecken_fuellen(s)
        vorjahr = s.copy()
        vorjahr.index = vorjahr.index + pd.DateOffset(years=1)
        return ((s / vorjahr.reindex(s.index) - 1) * 100).dropna()
    if art == "diff":
        return s.diff().dropna()
    return s


def ampel(wert: float | None, normal, heiss) -> str | None:
    """heiß / neutral / kalt aus Sicht der Zinspolitik. Reine Funktion."""
    if wert is None or normal is None or heiss is None:
        return None
    lo, hi = normal
    if wert > hi:
        return "heiß" if heiss == "hoch" else "kalt"
    if wert < lo:
        return "kalt" if heiss == "hoch" else "heiß"
    return "neutral"


def sahm(quote: pd.Series) -> float | None:
    """Sahm-Regel: 3-Monats-Schnitt der Arbeitslosenquote minus Tief des 3-Monats-Schnitts der 12 Monate davor."""
    m3 = quote.rolling(3).mean().dropna()
    if len(m3) < 13:
        return None
    return round(float(m3.iloc[-1] - m3.iloc[-13:-1].min()), 2) + 0.0  # + 0.0: keine "−0,00"


def _richtung(s: pd.Series, art: str) -> str:
    if len(s) < 4:
        return "gleich"
    d = float(s.iloc[-1] - s.iloc[-4])
    schwelle = 15 if art == "diff" else 0.15
    return "steigend" if d > schwelle else "fallend" if d < -schwelle else "seitwärts"


FOLGE = {
    ("Inflation", "heiß"): "spricht eher für höhere oder länger hohe Zinsen; belastet tendenziell Anleihen und teure Wachstumsaktien",
    ("Inflation", "kalt"): "gibt der Notenbank Raum für Zinssenkungen",
    ("Inflation", "neutral"): "liegt im Zielbereich der Notenbank",
    ("Arbeitsmarkt", "heiß"): "Arbeitsmarkt angespannt: spricht eher für höhere Zinsen, stützt aber Konsum und Gewinne",
    ("Arbeitsmarkt", "kalt"): "Arbeitsmarkt kühlt ab: spricht eher für Zinssenkungen, erhöht aber das Rezessionsrisiko",
    ("Arbeitsmarkt", "neutral"): "Arbeitsmarkt im Gleichgewicht",
    ("Wachstum", "heiß"): "kräftiges Wachstum: gut für Gewinne, kann die Zinsen oben halten",
    ("Wachstum", "kalt"): "schwaches Wachstum: belastet Gewinnerwartungen, spricht eher für Zinssenkungen",
    ("Wachstum", "neutral"): "Wachstum im üblichen Bereich",
}


def satz(k: dict, wert: float, vorwert: float | None, richtung: str, stufe: str | None, ampelwert: float) -> str:
    """Regelbasierter Satz zur Einordnung (keine KI)."""
    f = (lambda x: f"{x:+,.0f} Tsd.".replace(",", ".")) if k["art"] == "diff" else (lambda x: f"{x:,.0f} Tsd.".replace(",", ".") if k["einheit"] == "Tsd." else f"{x:.1f} %".replace(".", ","))
    teile = [f"{k['name']} {f(wert)}"]
    if vorwert is not None:
        teile[0] += f" (zuvor {f(vorwert)})"
    if k["art"] == "diff" or k["id"] == "erstantraege":
        teile.append(f"im Schnitt zuletzt {f(ampelwert)}")
    if stufe:
        lo, hi = k["normal"]
        einh = " Tsd." if k["einheit"] == "Tsd." else " %"
        bereich = f"{lo:g}–{hi:g}".replace(".", ",") + einh
        lage = "über dem" if ampelwert > hi else "unter dem" if ampelwert < lo else "im"
        teile.append(f"{lage} Normalbereich ({bereich}), Tendenz {richtung}")
        teile.append(FOLGE[(k["gruppe"], stufe)])
    else:
        teile.append(f"Tendenz {richtung}")
    s = ", ".join(teile[:-1]) + (": " if stufe else ", ") + teile[-1] + "."
    return s[0].upper() + s[1:]


def kennzahl(k: dict, roh: pd.Series, heute: pd.Timestamp | None = None) -> dict:
    """Alles zu einer Kennzahl aus der Rohreihe. Reine Funktion (ohne Netz testbar)."""
    s = umformen(roh, k["art"], k.get("faktor", 1.0))
    if k["id"] in ("leitzins", "ezb_zins"):  # Tageswerte: je Monat der letzte Stand
        s = s.resample("MS").last().dropna()
    if s.empty:
        return {"id": k["id"], "fehler": "keine Daten"}
    wert = float(s.iloc[-1])
    vorwert = float(s.iloc[-2]) if len(s) > 1 else None
    # Ampel auf geglätteten Werten, wo einzelne Werte stark rauschen
    if k["art"] == "diff":
        ampelwert = float(s.iloc[-3:].mean())
    elif k["id"] == "erstantraege":
        ampelwert = float(s.iloc[-4:].mean())
    else:
        ampelwert = wert
    stufe = ampel(ampelwert, k["normal"], k["heiss"])
    heute = heute or s.index[-1]
    zehn = s[s.index > heute - pd.DateOffset(years=10)]
    perz = round(float((zehn < wert).mean() * 100)) if len(zehn) >= 8 else None
    richtung = _richtung(s, k["art"])
    woche = k["id"] == "erstantraege"
    spur = s.iloc[-(26 if woche else 13):]
    verlauf = s[s.index > heute - pd.DateOffset(years=10)]
    if woche:
        verlauf = verlauf.resample("W-SAT").last().dropna()
    geschaetzt = None
    if k["art"] == "jahr":
        vj = s.index[-1] - pd.DateOffset(years=1)
        if vj not in roh.index:
            geschaetzt = f"Vorjahresmonat {vj.strftime('%m/%Y')} fehlt in der Quelle und ist aus den Nachbarmonaten geschätzt"
    aus = {
        "geschaetzt": geschaetzt,
        "id": k["id"], "region": k["region"], "gruppe": k["gruppe"], "name": k["name"], "quelle": k["quelle"], "einheit": k["einheit"],
        "art": k["art"], "info": k["info"], "ziel": k["ziel"], "normal": list(k["normal"]) if k["normal"] else None,
        "wert": round(wert, 2), "vorwert": None if vorwert is None else round(vorwert, 2), "ampelwert": round(ampelwert, 2),
        "periode": s.index[-1].strftime("%Y-%m-%d"), "takt": "Woche" if woche else "Quartal" if k["id"] in ("bip", "ez_bip") else "Monat",
        "stufe": stufe, "perzentil": perz, "richtung": richtung,
        "spur": [round(float(v), 2) for v in spur], "spur_daten": [d.strftime("%Y-%m-%d") for d in spur.index],
        "verlauf": {"daten": [d.strftime("%Y-%m-%d") for d in verlauf.index], "werte": [round(float(v), 3) for v in verlauf]},
        "satz": satz(k, wert, vorwert, richtung, stufe, ampelwert),
    }
    return aus


def _zusatz(werte: dict[str, dict]) -> dict:
    """Realzinsen und Sahm-Regel."""
    z = {}
    for zins, kern, name in (("leitzins", "kern_pce", "USA"), ("ezb_zins", "kern_hvpi", "Eurozone")):
        a, b = werte.get(zins), werte.get(kern)
        if a and b and "wert" in a and "wert" in b:
            z[f"realzins_{name}"] = round(a["wert"] - b["wert"], 2)
    return z


def _lage(werte: dict[str, dict]) -> dict:
    """Je Region und Gruppe eine zusammengefasste Stufe (Mehrheit der Ampeln)."""
    aus = {}
    for region in ("USA", "Eurozone"):
        for gruppe in ("Inflation", "Arbeitsmarkt", "Wachstum"):
            st = [w["stufe"] for w in werte.values() if w.get("region") == region and w.get("gruppe") == gruppe and w.get("stufe")]
            if st:
                aus[f"{region}:{gruppe}"] = max(set(st), key=lambda x: (st.count(x), x == "neutral"))
    return aus


def makrodaten() -> dict:
    """Alle Kennzahlen; eine ausgefallene Quelle betrifft nur ihre Kennzahlen. 3 Stunden gemerkt."""
    def holen():
        cache: dict = {}
        werte, fehler = {}, []
        for k in KENNZAHLEN:
            try:
                roh = _rohreihe(k, cache)
                werte[k["id"]] = kennzahl(k, roh)
            except Exception as e:  # Quelle aus: nur diese Kennzahl fehlt
                quelle = k["quelle"].split(",")[0]
                fehler.append({"id": k["id"], "name": k["name"], "region": k["region"], "quelle": quelle, "grund": type(e).__name__})
                if k["holen"][0] in ("bls", "bea"):
                    cache[k["holen"][0]] = None
        sahm_wert = None
        try:
            if "arbeitslos" in werte:
                bls = cache.get("bls") or _bls()
                sahm_wert = sahm(bls["LNS14000000"])
        except Exception:
            pass
        return {"werte": werte, "fehler": fehler, "zusatz": {**_zusatz(werte), "sahm": sahm_wert}, "lage": _lage(werte),
                "abgerufen": time.strftime("%Y-%m-%d %H:%M")}
    # Vollständig: 3 Stunden gemerkt; fehlt eine Quelle, nach 30 Minuten neuer Versuch (alter Stand bleibt sonst stehen)
    alt, alter = markt_lesen("makro:daten")
    if alt is not None and alter < (30 * 60 if alt.get("fehler") else 3 * STUNDE):
        d = alt
    else:
        try:
            d = holen()
            if alt is not None:  # Kennzahlen, die gerade ausfallen, mit dem letzten Stand füllen
                for k, w in alt.get("werte", {}).items():
                    if k not in d["werte"]:
                        d["werte"][k] = {**w, "alt": True}
                d["lage"] = _lage(d["werte"])
            markt_schreiben("makro:daten", d)
        except Exception:
            if alt is None:
                raise
            d = alt
    if not d["werte"]:
        return {"fehler": "Keine Makro-Quelle ist gerade erreichbar."}
    return d


# ---------------------------------------------------------------- Ergebnisse für den Makro-Kalender (M3)

MONATE = {"jan": 1, "feb": 2, "mär": 3, "märz": 3, "apr": 4, "mai": 5, "jun": 6, "juni": 6, "jul": 7, "juli": 7, "aug": 8,
          "sep": 9, "sept": 9, "okt": 10, "nov": 11, "dez": 12}


def berichtsmonat(name: str, datum: date) -> pd.Timestamp | None:
    """'US-Inflation CPI (Okt.)' am 10.11.2026 → Oktober 2026. Reine Funktion."""
    import re
    m = re.search(r"\(([A-Za-zäÄ]+)\.?\)", name)
    if not m or m.group(1).lower() not in MONATE:
        return None
    monat = MONATE[m.group(1).lower()]
    jahr = datum.year if monat < datum.month else datum.year - 1
    return pd.Timestamp(jahr, monat, 1)


def _wert_am(reihe: dict, tag: str) -> float | None:
    return reihe.get(tag) if reihe else None


def _jahresrate(reihe: dict, monat: pd.Timestamp) -> float | None:
    if reihe:
        reihe = _ser(luecken_fuellen(_als(reihe)))
    jetzt, vorjahr = _wert_am(reihe, monat.strftime("%Y-%m-%d")), _wert_am(reihe, (monat - pd.DateOffset(years=1)).strftime("%Y-%m-%d"))
    return None if jetzt is None or not vorjahr else (jetzt / vorjahr - 1) * 100


def _pz(x: float) -> str:
    return f"{x:.1f} %".replace(".", ",")


def _zinsschritt(reihe: dict, datum: date, nach_tagen: int) -> str | None:
    """Zinsstand nach einer Entscheidung gegen den Stand davor (Tagesreihe als {Datum: Wert})."""
    if not reihe:
        return None
    s = _als(reihe)
    vor = s[s.index < pd.Timestamp(datum)]
    nach = s[s.index >= pd.Timestamp(datum) + pd.Timedelta(days=nach_tagen)]
    if vor.empty or nach.empty:
        return None
    a, b = float(vor.iloc[-1]), float(nach.iloc[0])
    zahl = lambda x: f"{x:.2f}".replace(".", ",")
    schritt = "unverändert" if abs(b - a) < 0.001 else f"{'+' if b > a else '−'}{zahl(abs(b - a))} Pkt."
    return f"{zahl(b)} % ({schritt})"


def ergebnis(name: str, datum: date, bls: dict, fed_zins: dict, ezb_zins: dict) -> dict | None:
    """Tatsächlicher Wert eines vergangenen Makro-Termins aus den gespeicherten Reihen. Reine Funktion.
    bls: {Reihe: {Datum: Wert}}; fed_zins/ezb_zins: Tagesreihen der Zinsen."""
    n = name.lower()
    if "fed" in n and "zins" in n:
        t = _zinsschritt(fed_zins, datum, 1)
        return {"text": f"Obergrenze {t}", "quelle": "FRED DFEDTARU"} if t else None
    if "ezb" in n and "zins" in n:
        t = _zinsschritt(ezb_zins, datum, 7)   # wirksam meist am Mittwoch der folgenden Woche
        return {"text": f"Einlagensatz {t}", "quelle": "EZB"} if t else None
    monat = berichtsmonat(name, datum)
    if monat is None:
        return None
    tag = monat.strftime("%Y-%m-%d")
    if "cpi" in n:
        a, k = _jahresrate(bls.get("CUUR0000SA0"), monat), _jahresrate(bls.get("CUUR0000SA0L1E"), monat)
        vj = (monat - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
        hinweis = " (Vorjahresmonat geschätzt)" if vj not in (bls.get("CUUR0000SA0") or {}) else ""
        return {"text": f"{_pz(a)} ggü. Vorjahr" + (f", Kern {_pz(k)}" if k is not None else "") + hinweis, "quelle": "BLS"} if a is not None else None
    if "ppi" in n:
        a = _jahresrate(bls.get("WPUFD4"), monat)
        return {"text": f"{_pz(a)} ggü. Vorjahr", "quelle": "BLS"} if a is not None else None
    if "arbeitsmarkt" in n:
        p = bls.get("CES0000000001") or {}
        vor = (monat - pd.DateOffset(months=1)).strftime("%Y-%m-%d")
        if tag not in p or vor not in p:
            return None
        q = _wert_am(bls.get("LNS14000000"), tag)
        diff = p[tag] - p[vor]
        return {"text": f"{diff:+,.0f} Tsd. Stellen".replace(",", ".") + (f", Quote {_pz(q)}" if q is not None else ""), "quelle": "BLS"}
    return None


def ergebnisse_aus_speicher() -> tuple[dict, dict, dict]:
    """Nur gespeicherte Reihen lesen (kein Abruf), damit der Kalender nie wartet."""
    bls = (markt_lesen("makro:bls")[0]) or {}
    fed_z = (markt_lesen("fred:DFEDTARU")[0]) or {}
    ezb_z = (markt_lesen("makro:ezb:FM/D.U2.EUR.4F.KR.DFR.LEV")[0]) or {}
    return bls, fed_z, ezb_z


__all__ = ["ergebnis", "berichtsmonat", "ergebnisse_aus_speicher", "makrodaten", "kennzahl", "umformen", "ampel", "sahm", "satz", "bls_lesen", "bea_lesen", "census_lesen", "eurostat_lesen", "ezb_lesen", "KENNZAHLEN"]
