"""ETF-Stammdaten: Länderanteile aus daten/markt/etf_liste.yaml, dazu von Yahoo (einmal am Tag)
die zehn größten Positionen, die Branchenverteilung und die Kosten (TER).
"""
import yaml
import yfinance as yf

from ..cache import TAG, markt_lesen as cache_lesen, markt_schreiben as cache_schreiben
from ..konfig import MARKT_DATEN

DATEI = MARKT_DATEN / "etf_liste.yaml"
REGIONEN = {"usa": "USA", "kanada": "Kanada", "europa": "Europa", "japan": "Japan", "pazifik": "Pazifik",
            "em_asien": "Schwellenländer Asien", "em_rest": "Übrige Schwellenländer"}


def etf_liste() -> dict:
    try:
        return yaml.safe_load(DATEI.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def yahoo_fonds(ticker: str) -> dict:
    """Top-10-Positionen, Branchen und TER von Yahoo. Leeres dict, wenn Yahoo nichts liefert (z. B. Gold-ETC)."""
    schluessel = f"etf3:{ticker}"
    alt, alter = cache_lesen(schluessel)
    if alt is not None and alter < TAG:
        return alt
    neu = {"top": [], "branchen": {}, "ter": None, "langname": None, "ausschuettend": None, "groesse": None, "auflage": None, "ausschuettungsrendite": None}
    try:
        t = yf.Ticker(ticker)
        info = t.info
        neu["langname"] = info.get("longName")
        name = (neu["langname"] or "").lower()
        if any(x in name for x in ("(dist)", "distributing", " dist", "1d")):
            neu["ausschuettend"] = True
        elif any(x in name for x in ("(acc)", "accumulating", " acc", "1c", "5c")):
            neu["ausschuettend"] = False
        if isinstance(info.get("totalAssets"), (int, float)) and info["totalAssets"] > 0:
            neu["groesse"] = float(info["totalAssets"])
        if isinstance(info.get("fundInceptionDate"), (int, float)):
            from datetime import datetime, timezone
            neu["auflage"] = datetime.fromtimestamp(info["fundInceptionDate"], tz=timezone.utc).year
        if isinstance(info.get("yield"), (int, float)) and info["yield"] > 0:
            neu["ausschuettungsrendite"] = float(info["yield"]) * 100
        if isinstance(info.get("netExpenseRatio"), (int, float)):
            neu["ter"] = float(info["netExpenseRatio"])
        try:
            f = t.funds_data
            top = f.top_holdings
            if top is not None:
                neu["top"] = [{"symbol": str(s), "name": str(r["Name"]), "anteil": float(r["Holding Percent"]) * 100} for s, r in top.iterrows()]
            neu["branchen"] = {k: float(v) * 100 for k, v in (f.sector_weightings or {}).items()}
            if neu["ter"] is None and f.fund_operations is not None:
                wert = f.fund_operations.iloc[:, 0].get("Annual Report Expense Ratio")
                if isinstance(wert, (int, float)) and wert == wert:
                    neu["ter"] = float(wert) * 100
        except Exception:
            pass  # z. B. Gold-ETC: keine Fondsdaten
    except Exception:
        return alt or neu
    cache_schreiben(schluessel, neu)
    return neu
