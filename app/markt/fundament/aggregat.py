"""Indizes, ETFs und Sektoren von unten aus ihren Bestandteilen (Punkt 24c).

Gewichtet nach Börsenwert. Bewertungskennzahlen als gewichtetes harmonisches Mittel, das heißt Summe der
Börsenwerte geteilt durch die Summe der Gewinne (bzw. Umsätze, EBITDA): So zählt eine Firma mit KGV 200 nicht
übermäßig, und das Ergebnis entspricht dem, was man für den ganzen Korb zahlt. Beim KGV der letzten 12 Monate
gehen Verluste mit ein (Summe der Nettogewinne), beim Forward-KGV nur Firmen mit positiver Schätzung.
Margen = Summe der Gewinne / Summe der Umsätze. Wachstum und PEG: nach Börsenwert gewichteter Median (robust gegen
Ausreißer), Renditen: gewichteter Durchschnitt. Unplausible Einzelwerte (SPANNEN) bleiben außen vor.

Daten: Yahoo-Kennzahlen aller Mitglieder (indizes.kennzahlen, einmal am Tag).
"""
import numpy as np

from ..indizes import INDIZES, kennzahlen, mitglieder

ETFS = {  # ETF -> Index bzw. S&P-500-Sektor (GICS)
    "SPY": ("sp500", None, "S&P 500"), "QQQ": ("ndx", None, "Nasdaq 100"), "EXS1.DE": ("dax", None, "DAX"), "SX5E": ("sx5e", None, "Euro Stoxx 50"),
    "XLK": ("sp500", "Information Technology", "Technologie"), "XLC": ("sp500", "Communication Services", "Kommunikation"),
    "XLY": ("sp500", "Consumer Discretionary", "Zyklischer Konsum"), "XLP": ("sp500", "Consumer Staples", "Basiskonsum"),
    "XLV": ("sp500", "Health Care", "Gesundheit"), "XLF": ("sp500", "Financials", "Finanzen"), "XLI": ("sp500", "Industrials", "Industrie"),
    "XLE": ("sp500", "Energy", "Energie"), "XLB": ("sp500", "Materials", "Rohstoffe"), "XLU": ("sp500", "Utilities", "Versorger"),
    "XLRE": ("sp500", "Real Estate", "Immobilien"),
}


def _summe_teilen(werte: list[dict], zaehler: str, nenner: str) -> float | None:
    paare = [(w.get(zaehler), w.get(nenner)) for w in werte if w.get(zaehler) is not None and w.get(nenner) is not None]
    n = sum(b for _, b in paare)
    return sum(a for a, _ in paare) / n if paare and n > 0 else None


# Plausible Spannen je Firma. Werte außerhalb sind fast immer Datenfehler bei Yahoo (etwa Buchwert in Landeswährung,
# Preis in Dollar) oder Sonderfälle (fast kein Eigenkapital nach Rückkäufen) und würden den ganzen Korb verzerren.
SPANNEN = {"fwd": (0.5, 300), "ps": (0.02, 200), "ev_ebitda": (0.5, 300), "pb": (0.2, 100)}


def _median(paare: list[tuple[float, float]]) -> float | None:
    """Nach Gewicht (Börsenwert) gewichteter Median: robust gegen einzelne Ausreißer wie +13.000 % Quartalswachstum."""
    paare = sorted((w, g) for w, g in paare if w is not None and g and g > 0)
    gesamt = sum(g for _, g in paare)
    if not paare or gesamt <= 0:
        return None
    lauf = 0.0
    for w, g in paare:
        lauf += g
        if lauf >= gesamt / 2:
            return w
    return paare[-1][0]


def korb(werte: list[dict]) -> dict:
    cap = sum(w["cap"] for w in werte if w.get("cap"))
    if not cap:
        return {}
    def harmonisch(feld):
        lo, hi = SPANNEN[feld]
        mit = [w for w in werte if w.get(feld) and lo <= w[feld] <= hi and w.get("cap")]
        c = sum(w["cap"] for w in mit)
        s = sum(w["cap"] / w[feld] for w in mit)
        return (c / s if s > 0 else None), (c / cap * 100)
    def gewichtet(feld):
        mit = [w for w in werte if w.get(feld) is not None and w.get("cap")]
        c = sum(w["cap"] for w in mit)
        return (sum(w[feld] * w["cap"] for w in mit) / c if c else None), (c / cap * 100)
    def median(feld):
        return _median([(w.get(feld), w.get("cap")) for w in werte])
    gewinn = [w for w in werte if w.get("gewinn") is not None]
    kgv = (sum(w["cap"] for w in gewinn) / sum(w["gewinn"] for w in gewinn)) if gewinn and sum(w["gewinn"] for w in gewinn) > 0 else None
    fwd, abd_fwd = harmonisch("fwd")
    ps, _ = harmonisch("ps")
    ev_ebitda, _ = harmonisch("ev_ebitda")
    pb, _ = harmonisch("pb")
    roe, _ = gewichtet("roe")
    div, _ = gewichtet("div")
    # PEG je Firma: Yahoo (KGV / erwartetes Gewinnwachstum der nächsten Jahre), sonst Forward-KGV / Quartalswachstum,
    # nur zwischen 0 und 10; für den Korb der gewichtete Median
    def peg_firma(w):
        p = w.get("peg")
        if p is None and w.get("fwd") and w.get("gewinn_wachstum") and 0.02 <= w["gewinn_wachstum"] <= 0.6:
            p = w["fwd"] / (w["gewinn_wachstum"] * 100)
        return p if p is not None and 0 < p < 10 else None
    peg = _median([(peg_firma(w), w.get("cap")) for w in werte])
    return {"anzahl": len(werte), "boersenwert": cap, "kgv": kgv, "fwd_kgv": fwd, "abdeckung_fwd": abd_fwd, "kuv": ps, "ev_ebitda": ev_ebitda, "kbv": pb,
            "nettomarge": _summe_teilen(werte, "gewinn", "umsatz"), "operative_marge": _summe_teilen(werte, "op_gewinn", "umsatz"),
            "bruttomarge": _summe_teilen(werte, "brutto", "umsatz"), "fcf_rendite": _summe_teilen(werte, "fcf", "cap"),
            # Wachstum (Quartal zum Vorjahresquartal): gewichteter Median statt Durchschnitt, sonst bestimmt ein Ausreißer alles
            "umsatz_wachstum": median("umsatz_wachstum"), "gewinn_wachstum": median("gewinn_wachstum"), "roe": roe, "div_rendite": div,
            "peg": peg}


def uebersicht() -> dict:
    """Kennzahlen für die großen ETFs/Indizes und die S&P-500-Sektoren."""
    zeilen, fehlt = [], []
    daten = {}
    for etf, (index, sektor, name) in ETFS.items():
        if index not in daten:
            try:
                k = kennzahlen(index)
                sek = {m["ticker"]: m.get("sektor") for m in mitglieder(index)}
                daten[index] = [{**w, "ticker": t, "gics": sek.get(t)} for t, w in k.items()]
            except Exception:  # noqa: BLE001
                daten[index] = None
        werte = daten[index]
        if werte is None:
            fehlt.append(name); continue
        if sektor:
            werte = [w for w in werte if w.get("gics") == sektor]
        if not werte or "ps" not in werte[0]:
            if werte and "ps" not in werte[0]:
                fehlt.append(name)
            k = korb(werte) if werte else {}
            zeilen.append({"etf": etf, "name": name, "index": INDIZES[index]["name"], "sektor": sektor, **k, "unvollstaendig": True}); continue
        zeilen.append({"etf": etf, "name": name, "index": INDIZES[index]["name"], "sektor": sektor, **korb(werte)})
    return {"zeilen": zeilen, "fehlt": fehlt,
            "hinweis": "Gewichtet nach Börsenwert, Bewertungen als harmonisches Mittel (Summe Börsenwert / Summe Gewinn). Daten aller Mitglieder von Yahoo, einmal am Tag. Welt-ETF: Bestandteile (MSCI World) sind nicht frei verfügbar."}
