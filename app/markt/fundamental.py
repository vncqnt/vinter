"""Bilanzzahlen für den Reverse-DCF: Free Cashflow, Börsenwert, Schulden, Cash. Einmal pro Tag von Yahoo.

Free Cashflow = operativer Cashflow minus Investitionen, aus der Kapitalflussrechnung.
- "ttm": Summe der letzten vier Quartale (falls Yahoo sie liefert)
- "jahr": letztes Geschäftsjahr
- "schnitt3": Durchschnitt der letzten drei Geschäftsjahre (glättet zyklische Firmen wie Micron)
Alle Beträge in der Währung des Börsenkurses (bei Bedarf umgerechnet).
"""
import yfinance as yf

from ..cache import TAG, markt_lesen as cache_lesen, markt_schreiben as cache_schreiben


def _zahl(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def _fcf_reihe(tabelle) -> list[tuple[str, float]]:
    if tabelle is None or tabelle.empty or "Free Cash Flow" not in tabelle.index:
        return []
    reihe = [(str(d.date()), _zahl(v)) for d, v in tabelle.loc["Free Cash Flow"].items()]
    return sorted([(d, v) for d, v in reihe if v is not None])


def dcf_daten(ticker: str) -> dict:
    schluessel = f"dcf:{ticker}"
    alt, alter = cache_lesen(schluessel)
    if alt and alter < TAG:
        return alt
    try:
        t = yf.Ticker(ticker)
        info = t.info
        jahre = _fcf_reihe(t.cashflow)
        quartale = _fcf_reihe(t.quarterly_cashflow)
    except Exception:
        return alt or {"fehler": "Yahoo liefert gerade keine Bilanzzahlen."}
    waehrung, bilanz = info.get("currency"), info.get("financialCurrency") or info.get("currency")
    fx = 1.0
    if bilanz and waehrung and bilanz != waehrung:  # Bilanz in anderer Währung als der Kurs
        try:
            fx = float(yf.Ticker(f"{bilanz}{waehrung}=X").fast_info["last_price"])
        except Exception:
            return alt or {"fehler": f"Wechselkurs {bilanz}/{waehrung} fehlt."}
    basis = {}
    if len(quartale) >= 4:
        basis["ttm"] = sum(v for _, v in quartale[-4:]) * fx
    if jahre:
        basis["jahr"] = jahre[-1][1] * fx
    if len(jahre) >= 3:
        basis["schnitt3"] = sum(v for _, v in jahre[-3:]) / 3 * fx
    neu = {
        "waehrung": waehrung,
        "boersenwert": _zahl(info.get("marketCap")),
        "aktien": _zahl(info.get("sharesOutstanding")),
        "schulden": (_zahl(info.get("totalDebt")) or 0.0) * fx,
        "cash": (_zahl(info.get("totalCash")) or 0.0) * fx,
        "fcf": basis,
        "fcf_jahre": [{"datum": d, "wert": v * fx} for d, v in jahre],
    }
    if not neu["boersenwert"] or not basis:
        return alt or {"fehler": "Für diesen Wert fehlen Börsenwert oder Free Cashflow."}
    cache_schreiben(schluessel, neu)
    return neu
