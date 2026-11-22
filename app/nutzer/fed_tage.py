"""Dein Depot an Fed-Tagen: Wie hätte sich das heutige Depot an früheren Zinsentscheidungen bewegt?

Gerechnet mit den heutigen Positionen und Gewichten (wie beim Stresstest), Kurse in Euro. Fehlt für eine
Position an einem Termin der Kurs (z. B. erst später an die Börse gegangen), zählen die übrigen
entsprechend stärker; die Abdeckung steht bei jedem Termin.
Quartalszahlen im selben Fenster (Tag vor bis zwei Tage nach der Entscheidung) würden die Fed-Reaktion
überdecken: Solche Positionen zählen an diesem Termin nicht mit (Termine von Yahoo, etwa 7 Jahre zurück).
Vergleich: dieselben Tage beim S&P 500 (in Dollar) und beim MSCI World (in Euro), dazu die übliche
Bewegung an Tagen ohne Fed. Information, keine Vorhersage.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd

from ..benutzer import Benutzer
from ..markt.euro_kurse import euro_kurse, tagesreihe
from ..markt.earnings_moves import chart_termine
from ..markt.fed_tage import AB_JAHR, entscheidungen, normal, reaktionen
from .risiko import MARKT

SP500 = "^GSPC"
GRUPPEN = ("gesenkt", "gehalten", "erhöht")


def _schnitt(werte: list) -> float | None:
    w = [x for x in werte if x is not None]
    return sum(w) / len(w) if w else None


def _zusammenfassung(zeilen: list[dict], feld: str, ueblich: float | None) -> dict:
    """Ø Veränderung, Ø Betrag, Anteil Plus und Faktor gegenüber normalen Tagen, gesamt und je Entscheidung."""
    def teil(auswahl):
        w = [z[feld] for z in auswahl if z.get(feld) is not None]
        if not w:
            return None
        betrag = sum(abs(x) for x in w) / len(w)
        return {"anzahl": len(w), "schnitt": sum(w) / len(w), "betrag": betrag,
                "plus": sum(x > 0 for x in w) / len(w), "faktor": betrag / ueblich if ueblich else None}
    return {"alle": teil(zeilen), **{g: teil([z for z in zeilen if z["entscheidung"] == g]) for g in GRUPPEN}}


def _zahlen_tage(ticker: list[str]) -> dict[str, set[str]]:
    """Fed-Termine, an denen eine Position im selben Fenster Quartalszahlen hatte."""
    def holen(tk):
        try:
            return tk, [t["datum"] for t in chart_termine(tk).get("termine", []) if not t.get("zukunft")]
        except Exception:
            return tk, []
    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(pool.map(holen, ticker))


def _im_fenster(zahlen: list[str], fed: str) -> bool:
    d = date.fromisoformat(fed)
    return any(d - timedelta(days=1) <= date.fromisoformat(z) <= d + timedelta(days=2) for z in zahlen)


def auswertung(b: Benutzer, positionen: list[dict]) -> dict:
    termine = entscheidungen()
    if not termine:
        return {"fehler": "Die Fed-Termine konnten gerade nicht geladen werden."}
    pos = [p for p in positionen if p.get("ticker") and p.get("wert_eur") and not p.get("extra")]
    if not pos:
        return {"fehler": "Keine Positionen mit Kurs gefunden."}
    start = f"{AB_JAHR - 1}-12-01"
    kurse = euro_kurse(sorted({p["ticker"] for p in pos} | {MARKT}), start=start)
    sp = tagesreihe(SP500, start)
    tage = [t["datum"] for t in termine]

    # Depot als Index: Tagesrendite = gewichteter Schnitt der Positionen, die an dem Tag einen Kurs haben
    gewicht = {p["ticker"]: p["wert_eur"] for p in pos if p["ticker"] in kurse}
    if not gewicht:
        return {"fehler": "Für keine Position gibt es Kurse."}
    gesamt = sum(p["wert_eur"] for p in pos)
    renditen = pd.DataFrame({t: kurse[t].pct_change() for t in gewicht})
    w = pd.Series(gewicht)
    vorhanden = renditen.notna().mul(w, axis=1).sum(axis=1)
    depot_r = renditen.fillna(0).mul(w, axis=1).sum(axis=1) / vorhanden.replace(0, float("nan"))
    depot = (1 + depot_r.fillna(0)).cumprod()

    reihen = {"depot": depot, "welt": kurse.get(MARKT), "sp500": sp[0] if sp else None}
    reak = {k: reaktionen(r, tage) if r is not None else {} for k, r in reihen.items() if k != "depot"}
    ueblich = {k: normal(r, tage) if r is not None else None for k, r in reihen.items()}
    je_wert = {tk: reaktionen(kurse[tk], tage) for tk in gewicht}
    zahlen = _zahlen_tage(list(gewicht))
    namen = {p["ticker"]: p["name"] for p in pos}

    # Depot je Termin: gewichteter Schnitt der Positionen mit Kurs und ohne Quartalszahlen im Fenster
    zeilen = []
    for t in termine:
        d = t["datum"]
        dabei = [tk for tk in gewicht if d in je_wert[tk] and not _im_fenster(zahlen[tk], d)]
        raus = [namen[tk] for tk in gewicht if d in je_wert[tk] and _im_fenster(zahlen[tk], d)]
        summe = sum(gewicht[tk] for tk in dabei)
        if not dabei or summe / gesamt < 0.3:
            continue
        def mittel(feld):
            teile = [(je_wert[tk][d][feld], gewicht[tk]) for tk in dabei if je_wert[tk][d][feld] is not None]
            g = sum(w for _, w in teile)
            return sum(x * w for x, w in teile) / g if g else None
        zeilen.append({
            **t,
            "depot_tag": mittel("tag"), "depot_folge": mittel("folge"),
            "welt_folge": (reak["welt"].get(d) or {}).get("folge"),
            "sp500_tag": (reak["sp500"].get(d) or {}).get("tag"), "sp500_folge": (reak["sp500"].get(d) or {}).get("folge"),
            "abdeckung": summe / gesamt, "ohne_zahlen": raus,
        })

    # Je Position: Ø bis Folgetag gesamt und je Entscheidung, Schwankung gegenüber normalen zwei Tagen
    art = {t["datum"]: t["entscheidung"] for t in termine}
    einzeln = []
    for tk in gewicht:
        folge = {d: x["folge"] for d, x in je_wert[tk].items() if x["folge"] is not None and not _im_fenster(zahlen[tk], d)}
        if len(folge) < 8:
            continue
        n = normal(kurse[tk], tage)["folge"]
        betrag = _schnitt([abs(x) for x in folge.values()])
        groesste = max(folge.items(), key=lambda kv: abs(kv[1]))
        einzeln.append({
            "ticker": tk, "name": namen[tk], "gewicht": gewicht[tk] / gesamt * 100, "anzahl": len(folge),
            "schnitt": _schnitt(list(folge.values())),
            **{g: _schnitt([x for d, x in folge.items() if art[d] == g]) for g in GRUPPEN},
            "faktor": betrag / n if n else None,
            "groesste": {"datum": groesste[0], "wert": groesste[1], "entscheidung": art[groesste[0]]},
            "ohne_zahlen": sum(_im_fenster(zahlen[tk], d) for d in je_wert[tk]),
        })
    einzeln.sort(key=lambda z: -(z["faktor"] or 0))

    return {
        "termine": zeilen[::-1],
        "depot": _zusammenfassung(zeilen, "depot_folge", ueblich["depot"]["folge"]),
        "depot_tag": _zusammenfassung(zeilen, "depot_tag", ueblich["depot"]["tag"]),
        "sp500": _zusammenfassung(zeilen, "sp500_folge", (ueblich["sp500"] or {}).get("folge")),
        "ueblich": ueblich,
        "positionen": einzeln,
        "ab": zeilen[0]["datum"] if zeilen else None,
        "ohne": [namen[t] for t in namen if t not in kurse],
    }
