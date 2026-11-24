"""Event Study: Wie haben Kursreihen um eine Gruppe von Ereignissen reagiert, verglichen mit zufälligen Tagen?

Rechenweg (für Tages- und Minutendaten gleich):
- Tag 0 ist der erste Handelstag, an dem der Markt das Ereignis handeln konnte. Bei Tagesdaten zählt die Uhrzeit in
  New York: bis 16 Uhr am Handelstag = derselbe Tag, danach oder am Wochenende = nächster Handelstag.
  Bei Minutendaten ist Tag 0 die erste Minute nach dem Ereignis.
- Basis ist der letzte Wert davor (Schluss am Handelstag vor Tag 0 bzw. letzte Minute vor dem Ereignis).
  Veränderung je Schritt k im Fenster: Kurse in Prozent, Renditen in Basispunkten, VIX in Punkten.
  Negative k zeigen, was vor dem Ereignis lief (gegen dieselbe Basis).
- Zufallsbasis: gleich viele zufällige Startpunkte aus derselben Reihe und demselben Zeitraum, 1.000 Ziehungen.
  Daraus der übliche Bereich des Durchschnitts (5 bis 95 %) und der p-Wert: Anteil der Ziehungen, deren Durchschnitt
  mindestens so weit vom Zufallsmittel entfernt ist wie der der Events (zweiseitig, Bootstrap).
- Vergleich zweier Event-Sets: Permutationstest der Differenz der Durchschnitte (Etiketten 2.000-mal vertauscht).
Alles reine Funktionen bis auf das Laden der Reihen (Yahoo, FRED, eigene Minutendaten).
"""
import math
from datetime import time as uhrzeit

import numpy as np
import pandas as pd
import yaml

from ..konfig import MARKT_DATEN, VORLAGEN

NY = "America/New_York"
ZIEHUNGEN = 1000
PERMUTATIONEN = 2000
WENIG = 10

# Auswählbare Reihen: Schlüssel → (Name, Gruppe, Quelle, Symbol, Einheit)
REIHEN = {
    "spx": ("S&P 500", "Indizes", "yahoo", "^GSPC", "prozent"),
    "ndx": ("Nasdaq 100", "Indizes", "yahoo", "^NDX", "prozent"),
    "rut": ("Russell 2000", "Indizes", "yahoo", "^RUT", "prozent"),
    "dax": ("DAX", "Indizes", "yahoo", "^GDAXI", "prozent"),
    "sx5e": ("Euro Stoxx 50", "Indizes", "yahoo", "^STOXX50E", "prozent"),
    "nikkei": ("Nikkei 225", "Indizes", "yahoo", "^N225", "prozent"),
    "hsi": ("Hang Seng", "Indizes", "yahoo", "^HSI", "prozent"),
    "us10": ("US-Rendite 10 J.", "Zinsen", "fred", "DGS10", "bp"),
    "us2": ("US-Rendite 2 J.", "Zinsen", "fred", "DGS2", "bp"),
    "us3m": ("US-Rendite 3 M.", "Zinsen", "fred", "DGS3MO", "bp"),
    "vix": ("VIX", "Volatilität", "yahoo", "^VIX", "punkte"),
    "gold": ("Gold", "Rohstoffe", "yahoo", "GC=F", "prozent"),
    "wti": ("Öl WTI", "Rohstoffe", "yahoo", "CL=F", "prozent"),
    "brent": ("Öl Brent", "Rohstoffe", "yahoo", "BZ=F", "prozent"),
    "kupfer": ("Kupfer", "Rohstoffe", "yahoo", "HG=F", "prozent"),
    "dxy": ("Dollar-Index", "Währungen", "yahoo", "DX-Y.NYB", "prozent"),
    "eurusd": ("EUR/USD", "Währungen", "yahoo", "EURUSD=X", "prozent"),
    "usdcnh": ("USD/CNH", "Währungen", "yahoo", "CNH=X", "prozent"),
    "usdjpy": ("USD/JPY", "Währungen", "yahoo", "JPY=X", "prozent"),
    "btc": ("Bitcoin", "Währungen", "yahoo", "BTC-USD", "prozent"),
}
for _k, _n in {"XLK": "Technologie", "XLF": "Finanzen", "XLE": "Energie", "XLV": "Gesundheit", "XLI": "Industrie",
               "XLY": "Zyklischer Konsum", "XLP": "Basiskonsum", "XLU": "Versorger", "XLB": "Grundstoffe",
               "XLRE": "Immobilien", "XLC": "Kommunikation", "SOXX": "Halbleiter"}.items():
    REIHEN[_k.lower()] = (f"{_n} ({_k})", "Sektoren", "yahoo", _k, "prozent")
EINHEIT = {"prozent": "%", "bp": "Bp.", "punkte": "Pkt."}


def katalog() -> list[dict]:
    return [{"schluessel": k, "name": v[0], "gruppe": v[1], "einheit": v[4]} for k, v in REIHEN.items()]


def beschreibung(schluessel: str) -> dict:
    """Name, Quelle, Symbol, Einheit; 't:XYZ' = beliebiger Yahoo-Ticker in Prozent."""
    if schluessel in REIHEN:
        n, g, q, s, e = REIHEN[schluessel]
        return {"schluessel": schluessel, "name": n, "gruppe": g, "quelle": q, "symbol": s, "einheit": e}
    if schluessel.startswith("t:") and 1 < len(schluessel) <= 22:
        s = schluessel[2:].upper()
        return {"schluessel": schluessel, "name": s, "gruppe": "Aktien", "quelle": "yahoo", "symbol": s, "einheit": "prozent"}
    raise ValueError(f"unbekannte Reihe {schluessel}")


def tagesreihe(schluessel: str) -> pd.Series:
    from .abruf import fred, schlusskurse
    b = beschreibung(schluessel)
    s = fred(b["symbol"]) if b["quelle"] == "fred" else schlusskurse(b["symbol"], "20y")
    s = s.dropna()
    s = s[s > 0] if b["einheit"] == "prozent" else s
    s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
    return s[~s.index.duplicated(keep="last")].sort_index()


def kategorien() -> dict:
    """Kategorien und Merkmale: eigene Datei unter daten/markt, sonst die Vorlage."""
    for datei in (MARKT_DATEN / "event_kategorien.yaml", VORLAGEN / "markt" / "event_kategorien.yaml"):
        if datei.exists():
            try:
                return yaml.safe_load(datei.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
    return {}


# ---------- Zeitpunkte ----------

ZONEN = ("America/New_York", "Europe/Berlin", "UTC")


def zeit_lesen(text: str, zone: str | None = None) -> pd.Timestamp:
    """ISO-Zeit; ohne Zeitzone gilt die angegebene Zone (Standard New York)."""
    t = pd.Timestamp(text)
    return t.tz_localize(zone if zone in ZONEN else NY) if t.tzinfo is None else t


def zeitpunkt(zeit: pd.Timestamp) -> str:
    ny = zeit.tz_convert(NY)
    if ny.weekday() >= 5:
        return "Wochenende"
    if ny.time() < uhrzeit(9, 30):
        return "vorbörslich"
    if ny.time() < uhrzeit(16, 0):
        return "Handelszeit"
    return "nachbörslich"


def tag_null_datum(zeit: pd.Timestamp) -> pd.Timestamp:
    """Kalendertag, ab dem der erste Handelstag gesucht wird (nach 16 Uhr New York: Folgetag)."""
    ny = zeit.tz_convert(NY)
    tag = pd.Timestamp(ny.date())
    return tag + pd.Timedelta(days=1) if ny.time() >= uhrzeit(16, 0) else tag


def position_tag(index: pd.DatetimeIndex, zeit: pd.Timestamp) -> int | None:
    """Index von Tag 0 in einer Tagesreihe (oder None, wenn außerhalb)."""
    p = int(index.searchsorted(tag_null_datum(zeit)))
    if p <= 0 or p >= len(index) or (index[p] - tag_null_datum(zeit)).days > 6:
        return None
    return p


def position_minute(index: pd.DatetimeIndex, zeit: pd.Timestamp, luecke_min: int = 10) -> int | None:
    """Index der ersten Minute nach dem Ereignis; None, wenn die Daten um das Ereignis eine Lücke haben."""
    z = zeit.tz_convert("UTC")
    p = int(index.searchsorted(z, side="right"))
    if p <= 0 or p >= len(index):
        return None
    if (index[p] - z) > pd.Timedelta(minutes=luecke_min) or (z - index[p - 1]) > pd.Timedelta(minutes=luecke_min):
        return None
    return p


# ---------- Rechnen ----------

def veraenderung(werte: np.ndarray, basis: np.ndarray | float, einheit: str) -> np.ndarray:
    if einheit == "prozent":
        return (werte / basis - 1) * 100
    if einheit == "bp":
        return (werte - basis) * 100
    return werte - basis


def pfade(werte: np.ndarray, positionen: list[int], von: int, bis: int, einheit: str) -> np.ndarray:
    """Matrix (Events × Fenster) der Veränderungen gegen den Wert vor Tag 0; außerhalb der Reihe NaN."""
    k = np.arange(von, bis + 1)
    aus = np.full((len(positionen), len(k)), np.nan)
    for i, p in enumerate(positionen):
        idx = p + k
        gut = (idx >= 0) & (idx < len(werte))
        aus[i, gut] = veraenderung(werte[idx[gut]], werte[p - 1], einheit)
    return aus


def zufallsmittel(werte: np.ndarray, n: int, von: int, bis: int, einheit: str, bereich: tuple[int, int],
                  ziehungen: int = ZIEHUNGEN, seed: int = 7) -> np.ndarray:
    """Durchschnittspfade aus je n zufälligen Startpunkten (Ziehungen × Fenster)."""
    lo, hi = max(1, bereich[0], 1 - von), min(bereich[1], len(werte) - 1 - bis)
    if hi <= lo or n <= 0:
        return np.full((0, bis - von + 1), np.nan)
    rng = np.random.default_rng(seed)
    starts = rng.integers(lo, hi + 1, size=(ziehungen, n))
    k = np.arange(von, bis + 1)
    w = werte[starts[:, :, None] + k[None, None, :]]
    basis = werte[starts - 1][:, :, None]
    return np.nanmean(veraenderung(w, basis, einheit), axis=1)


def zufall_einzel(werte: np.ndarray, von: int, bis: int, einheit: str, bereich: tuple[int, int], stufe: int) -> np.ndarray:
    """Veränderung bis 'stufe' für alle möglichen Startpunkte (für die Trefferquote der Basis)."""
    lo, hi = max(1, bereich[0], 1 - von), min(bereich[1], len(werte) - 1 - bis)
    if hi <= lo:
        return np.array([])
    p = np.arange(lo, hi + 1)
    return veraenderung(werte[p + stufe], werte[p - 1], einheit)


def p_bootstrap(beobachtet: float, zufall: np.ndarray) -> float | None:
    z = zufall[np.isfinite(zufall)]
    if not len(z) or not math.isfinite(beobachtet):
        return None
    mitte = float(np.mean(z))
    return float((np.sum(np.abs(z - mitte) >= abs(beobachtet - mitte)) + 1) / (len(z) + 1))


def p_permutation(a: np.ndarray, b: np.ndarray, n: int = PERMUTATIONEN, seed: int = 11) -> float | None:
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if len(a) < 2 or len(b) < 2:
        return None
    alle = np.concatenate([a, b])
    beob = abs(a.mean() - b.mean())
    rng = np.random.default_rng(seed)
    treffer = 0
    for _ in range(n):
        rng.shuffle(alle)
        treffer += abs(alle[:len(a)].mean() - alle[len(a):].mean()) >= beob
    return float((treffer + 1) / (n + 1))


def kennzahlen(werte: np.ndarray) -> dict:
    w = werte[np.isfinite(werte)]
    if not len(w):
        return {"n": 0, "mittel": None, "median": None, "treffer": None, "streuung": None}
    return {"n": int(len(w)), "mittel": round(float(w.mean()), 3), "median": round(float(np.median(w)), 3),
            "treffer": round(float((w > 0).mean() * 100), 1), "streuung": round(float(w.std(ddof=1)), 3) if len(w) > 1 else None}


def marktlage(spx: pd.Series, vix: pd.Series, zeit: pd.Timestamp, regeln: dict) -> dict:
    """Lage am Handelstag vor Tag 0: Trend 20 Tage, VIX, Abstand zum Allzeithoch."""
    p = position_tag(spx.index, zeit)
    if p is None or p < 21:
        return {}
    basis = float(spx.iloc[p - 1])
    r20 = (basis / float(spx.iloc[p - 21]) - 1) * 100
    hoch = (basis / float(spx.iloc[:p].max()) - 1) * 100
    trend = "nach Rally" if r20 >= regeln.get("rally_ab", 5) else "nach Rückgang" if r20 <= regeln.get("rueckgang_ab", -5) else "seitwärts"
    abstand = ("nahe Hoch" if hoch >= regeln.get("nahe_hoch", -3) else
               "deutlich unter Hoch" if hoch <= regeln.get("weit_unter_hoch", -10) else "unter Hoch")
    aus = {"trend": trend, "trend_20t": round(r20, 2), "hoch": abstand, "abstand_hoch": round(hoch, 2)}
    v = vix[vix.index < spx.index[p]]
    if len(v):
        aus["vix_wert"] = round(float(v.iloc[-1]), 1)
        aus["vix"] = "VIX hoch" if aus["vix_wert"] >= regeln.get("vix_hoch_ab", 20) else "VIX niedrig"
    return aus


def auswerten(reihe: pd.Series, events: list[dict], von: int, bis: int, einheit: str, stufe: int,
              minuten: bool = False, mit_basis: bool = True) -> dict:
    """Eine Reihe gegen eine Event-Gruppe. events: [{id, zeit (Timestamp), …}]. stufe = Schritt für die Kennzahlen."""
    werte = reihe.to_numpy(dtype=float)
    finde = position_minute if minuten else position_tag
    pos, ids, fehlend = [], [], []
    for e in events:
        p = finde(reihe.index, e["zeit"])
        if p is None or p + stufe >= len(werte):  # Schritt für die Kennzahl noch nicht erreicht
            fehlend.append(e["id"])
            continue
        pos.append(p)
        ids.append(e["id"])
    k = list(range(von, bis + 1))
    s = k.index(stufe) if stufe in k else len(k) - 1
    aus = {"schritte": k, "stufe": k[s], "n": len(pos), "fehlend": fehlend, "einzel": [], "mittel": [], "median": [],
           "band": [], "basis": None, "werte": [], "kennzahlen": kennzahlen(np.array([]))}
    if not pos:
        return aus
    m = pfade(werte, pos, von, bis, einheit)
    rund = lambda x: None if not np.isfinite(x) else round(float(x), 3)  # noqa: E731
    aus["einzel"] = [{"id": i, "datum": reihe.index[p].isoformat(), "werte": [rund(x) for x in zeile]} for i, p, zeile in zip(ids, pos, m)]
    with np.errstate(all="ignore"):
        aus["mittel"] = [rund(x) for x in np.nanmean(m, axis=0)]
        aus["median"] = [rund(x) for x in np.nanmedian(m, axis=0)]
        # Unsicherheit des Durchschnitts: Bootstrap über die Events (90 %)
        rng = np.random.default_rng(3)
        boot = np.nanmean(m[rng.integers(0, len(pos), size=(500, len(pos)))], axis=1)
        aus["band"] = [[rund(a), rund(b)] for a, b in zip(np.nanpercentile(boot, 5, axis=0), np.nanpercentile(boot, 95, axis=0))]
    h = m[:, s]
    aus["werte"] = [rund(x) for x in h]
    aus["kennzahlen"] = kennzahlen(h)
    if mit_basis and len(pos) >= 2:
        # Zeitraum der Basis: ab zwei Jahre vor dem ersten Event bis heute (bei Minuten: alles Gespeicherte)
        if minuten:
            bereich = (1, len(werte) - 1)
        else:
            start = int(reihe.index.searchsorted(reihe.index[min(pos)] - pd.Timedelta(days=730)))
            bereich = (start, len(werte) - 1)
        z = zufallsmittel(werte, len(pos), von, bis, einheit, bereich)
        if len(z):
            with np.errstate(all="ignore"):
                einzel = zufall_einzel(werte, von, bis, einheit, bereich, k[s])
                # Beispiele normaler Tage für die Grafik: 80 zufällige Fenster als blasse Linien, 600 Werte für die Verteilung
                rng = np.random.default_rng(9)
                lo, hi = max(1, bereich[0], 1 - von), min(bereich[1], len(werte) - 1 - bis)
                beispiel = pfade(werte, list(rng.integers(lo, hi + 1, size=80)), von, bis, einheit)
                stich = rng.choice(einzel, size=min(600, len(einzel)), replace=False) if len(einzel) else np.array([])
                aus["basis"] = {
                    "beispiele": [[rund(x) for x in zeile] for zeile in beispiel],
                    "stichprobe": [rund(x) for x in stich],
                    "mittel": [rund(x) for x in np.nanmedian(z, axis=0)],
                    "band": [[rund(a), rund(b)] for a, b in zip(np.nanpercentile(z, 5, axis=0), np.nanpercentile(z, 95, axis=0))],
                    "treffer": round(float((einzel > 0).mean() * 100), 1) if len(einzel) else None,
                    "mittel_stufe": rund(float(np.nanmean(z[:, s]))),
                    "p": p_bootstrap(float(np.nanmean(h)), z[:, s]),
                }
    return aus
