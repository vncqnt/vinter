"""Welcher Branchen-ETF passt als Vergleich zu einer Aktie? (für die Relative Stärke)

Zuerst die eigene Zuordnung aus daten/markt/vergleich_etf.yaml, sonst der US-Sektor-ETF zu der
Branche, die Yahoo für die Aktie meldet.
"""
import yaml

from ..konfig import MARKT_DATEN
from .bewertung import SEKTOR_ETF
from .kurse import stammdaten
from .sektoren import SEKTOREN

DATEI = MARKT_DATEN / "vergleich_etf.yaml"


def _datei() -> dict:
    try:
        return yaml.safe_load(DATEI.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def vergleich_etf(tickers: list[str]) -> dict[str, dict]:
    """Je Ticker {etf, name, quelle} oder nichts, wenn keine Branche bekannt ist."""
    daten = _datei()
    eigen = {str(k): str(v) for k, v in (daten.get("aktien") or {}).items()}
    namen = {**SEKTOREN, **{str(k): str(v) for k, v in (daten.get("namen") or {}).items()}}
    ergebnis = {}
    for t in tickers:
        if t in eigen:
            etf, quelle = eigen[t], "eigene Zuordnung"
        else:
            etf, quelle = SEKTOR_ETF.get(stammdaten(t).get("sector")), "Branche laut Yahoo"
        if etf:
            ergebnis[t] = {"etf": etf, "name": namen.get(etf, etf), "quelle": quelle}
    return ergebnis
