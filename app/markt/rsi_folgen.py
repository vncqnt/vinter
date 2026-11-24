"""RSI: Was geschah danach? Kursverlauf nach RSI-Signalen einer Aktie, verglichen mit normalen Tagen.

Signal = der RSI (14 Tage, Wilder) schließt zum ersten Mal über 70 bzw. unter 30, nachdem er vorher auf der anderen
Seite der Schwelle war. Ein neues Signal derselben Art zählt erst nach mindestens 10 Handelstagen Abstand, sonst
würden lange überkaufte Phasen viele fast gleiche Fälle liefern.
Gemessen wird ab dem Schluss am Signaltag (so wie man es frühestens wissen konnte) 5, 20 und 60 Handelstage weiter.
Vergleich: alle Tage derselben Aktie (Anteil im Plus, Durchschnitt) und eine Zufallsbasis wie in der Event Study
(gleich viele zufällige Tage, 1.000 Ziehungen, p-Wert). Kurse von Yahoo, bereinigt, bis 10 Jahre.
Beschreibt Vergangenheit, kein Kauf- oder Verkaufssignal.
"""
import numpy as np
import pandas as pd

from .chartanalyse import rsi
from .eventstudie import kennzahlen, p_bootstrap, veraenderung

HORIZONTE = (5, 20, 60)
ABSTAND = 10
VOR = 10


def signale(r: pd.Series, oben: float = 70, unten: float = 30, abstand: int = ABSTAND) -> dict[str, list[int]]:
    """Positionen der Signaltage (reine Funktion)."""
    w = r.to_numpy(dtype=float)
    aus = {"oben": [], "unten": []}
    for art, ueber in (("oben", True), ("unten", False)):
        letzte = -10 ** 9
        for i in range(1, len(w)):
            if np.isnan(w[i]) or np.isnan(w[i - 1]):
                continue
            kreuzt = (w[i] > oben and w[i - 1] <= oben) if ueber else (w[i] < unten and w[i - 1] >= unten)
            if kreuzt and i - letzte >= abstand:
                aus[art].append(i)
                letzte = i
    return aus


def ab_tag(werte: np.ndarray, tage: list[int], vor: int, bis: int) -> np.ndarray:
    """Matrix (Signale × Schritte -vor…bis): Veränderung in Prozent gegen den Schluss am Signaltag (Schritt 0 = 0 %)."""
    k = np.arange(-vor, bis + 1)
    aus = np.full((len(tage), len(k)), np.nan)
    for j, i in enumerate(tage):
        idx = i + k
        gut = (idx >= 0) & (idx < len(werte))
        aus[j, gut] = (werte[idx[gut]] / werte[i] - 1) * 100
    return aus


def zufall(werte: np.ndarray, anzahl: int, vor: int, bis: int, ziehungen: int = 1000, seed: int = 6) -> np.ndarray:
    """Durchschnittspfade aus je 'anzahl' zufälligen Tagen (Ziehungen × Schritte)."""
    lo, hi = vor, len(werte) - 1 - bis
    rng = np.random.default_rng(seed)
    starts = rng.integers(lo, hi + 1, size=(ziehungen, anzahl))
    k = np.arange(-vor, bis + 1)
    return np.mean(werte[starts[:, :, None] + k[None, None, :]] / werte[starts][:, :, None] - 1, axis=1) * 100


def auswerten(schluss: pd.Series, oben: float = 70, unten: float = 30, n: int = 14) -> dict:
    s = schluss.dropna()
    s = s[s > 0]
    if len(s) < 300:
        return {"fehler": "Zu wenig Kurshistorie (mindestens gut ein Jahr nötig)."}
    r = rsi(s, n)
    werte = s.to_numpy(dtype=float)
    bis = max(HORIZONTE)
    rund = lambda x: None if x is None or not np.isfinite(x) else round(float(x), 3)  # noqa: E731
    aus = {"rsi_heute": rund(r.iloc[-1]), "stand": s.index[-1].strftime("%Y-%m-%d"), "von": s.index[0].strftime("%Y-%m-%d"),
           "oben": oben, "unten": unten, "laenge": n, "horizonte": list(HORIZONTE), "schritte": list(range(-VOR, bis + 1)), "arten": {}}
    bereich = (n + VOR + 1, len(werte) - 1)
    # Alle Tage als Vergleich: Veränderung nach h Tagen ab jedem Schluss
    normal = {}
    for h in HORIZONTE:
        v = veraenderung(werte[h:], werte[:-h], "prozent")
        normal[h] = {"mittel": rund(np.mean(v)), "treffer": round(float((v > 0).mean() * 100), 1)}
    aus["normal"] = normal
    zufall_pfade = ab_tag(werte, list(np.random.default_rng(4).integers(bereich[0], len(werte) - bis, size=30)), VOR, bis)
    aus["zufall_beispiele"] = [[rund(x) for x in z] for z in zufall_pfade]
    for art, pos in signale(r, oben, unten).items():
        p1 = [p for p in pos if p >= max(VOR, n + 1)]
        m = ab_tag(werte, p1, VOR, bis)
        eintrag = {"n": len(p1), "signale": [], "horizonte": {}}
        if p1:
            with np.errstate(all="ignore"):
                eintrag["mittel"] = [rund(x) for x in np.nanmean(m, axis=0)]
                eintrag["median"] = [rund(x) for x in np.nanmedian(m, axis=0)]
            eintrag["signale"] = [{"datum": s.index[p].strftime("%Y-%m-%d"), "rsi": rund(r.iloc[p]), "werte": [rund(x) for x in zeile]}
                                  for p, zeile in zip(p1, m)]
            z = zufall(werte, len(p1), VOR, bis)
            with np.errstate(all="ignore"):
                eintrag["zufall_band"] = [[rund(a), rund(b)] for a, b in zip(np.nanpercentile(z, 5, axis=0), np.nanpercentile(z, 95, axis=0))]
                eintrag["zufall_mittel"] = [rund(x) for x in np.nanmedian(z, axis=0)]
            k = list(range(-VOR, bis + 1))
            for h in HORIZONTE:
                spalte = m[:, k.index(h)]
                kz = kennzahlen(spalte)
                kz["p"] = p_bootstrap(float(np.nanmean(spalte)), z[:, k.index(h)]) if kz["n"] >= 2 else None
                kz["werte"] = [rund(x) for x in spalte]
                eintrag["horizonte"][str(h)] = kz
        aus["arten"][art] = eintrag
    return aus


def analyse(ticker: str, oben: float = 70, unten: float = 30, n: int = 14) -> dict:
    from .abruf import schlusskurse
    oben, unten, n = min(95, max(55, oben)), max(5, min(45, unten)), max(2, min(50, n))
    d = auswerten(schlusskurse(ticker, "10y"), oben, unten, n)
    d["ticker"] = ticker.upper()
    return d
