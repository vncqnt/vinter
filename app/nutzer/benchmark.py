"""Benchmark-Vergleich: das echte Depot gegen MSCI World und S&P 500, alles in Euro.

Das Depot ist die zeitgewichtete Rendite aus verlauf.py: Einzahlungen und Käufe verschieben
die Linie nicht, sie zeigt nur, wie gut die Auswahl war. So ist der Vergleich mit einem
Index fair. Die Kennzahlen je Zeitraum rechnet die Seite selbst aus den Tageswerten.
"""
import pandas as pd

from ..benutzer import Benutzer
from ..markt.euro_kurse import euro_kurse
from .verlauf import depot_verlauf

VERGLEICH = {
    "welt": ("EUNL.DE", "MSCI World"),
    "sp500": ("SXR8.DE", "S&P 500"),
}


def benchmark(b: Benutzer, extra: list[tuple[str, str]] | None = None) -> dict:
    """extra: weitere Vergleichswerte [(Ticker, Name)], z. B. Nasdaq 100 oder ein Branchen-ETF."""
    verlauf = depot_verlauf(b)
    if verlauf.get("fehler") or not verlauf.get("punkte"):
        return {"fehler": verlauf.get("fehler") or "Noch kein Depot-Verlauf vorhanden."}
    punkte = verlauf["punkte"]
    tage = pd.DatetimeIndex([p["datum"] for p in punkte])
    laenge = (pd.Timestamp.today() - tage[0]).days + 20
    vergleich = {**VERGLEICH, **{f"x_{t}": (t, n) for t, n in (extra or []) if t not in {v[0] for v in VERGLEICH.values()}}}
    kurse = euro_kurse([t for t, _ in vergleich.values()], tage=laenge)

    spalten = {}
    for schluessel, (ticker, _) in vergleich.items():
        s = kurse.get(ticker)
        if s is not None and len(s):
            spalten[schluessel] = s.reindex(s.index.union(tage)).ffill().reindex(tage)
    return {
        "namen": {"depot": "Dein Depot", **{k: n for k, (_, n) in vergleich.items() if k in spalten}},
        "ticker": {k: t for k, (t, _) in vergleich.items() if k in spalten},
        "punkte": [
            {"d": p["datum"], "depot": p["wert"], **{k: (round(float(s.iloc[i]), 4) if pd.notna(s.iloc[i]) else None) for k, s in spalten.items()}}
            for i, p in enumerate(punkte)
        ],
    }
