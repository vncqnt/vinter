"""Bezug eines Arbeitsplatzes im Dashboard: ein einzelner Wert oder eine Marktrichtung.

Ein Layout kann an einen Wert (jeder Yahoo-Ticker) oder an eine Marktrichtung (Gesamtmarkt, Sektor, Thema,
Anlageklasse, Faktor) gebunden sein. Die Widgets fragen hier, was das ist: welche Art von Wert, welcher Ticker
für Kurse, welcher für Optionen und Gamma (bei Futures und Indizes ein ETF als Stellvertreter), welcher
COT-Markt passt und welche Werte zu einer Richtung gehören.

Nur Marktdaten, nichts Persönliches (das ergänzt app/nutzer/bezug.py). Die Auflösung einer Richtung ruft
nichts aus dem Netz ab, die Mitglieder eines Sektors kommen aus dem gespeicherten Kennzahlen-Cache.
"""
import re

from .bewertung import SEKTOR_ETF
from .sektoren import SEKTOREN

# Optionen und Gamma: Für Futures, Indizes, Rohstoffe und Devisen gibt es bei Yahoo keine (US-)Optionen.
# Dafür der ETF, der dasselbe abbildet.
STELLVERTRETER = {
    "ES=F": "SPY", "MES=F": "SPY", "^GSPC": "SPY", "^SPX": "SPY", "NQ=F": "QQQ", "MNQ=F": "QQQ", "^NDX": "QQQ", "^IXIC": "QQQ",
    "RTY=F": "IWM", "^RUT": "IWM", "YM=F": "DIA", "^DJI": "DIA", "GC=F": "GLD", "SI=F": "SLV", "CL=F": "USO", "BZ=F": "BNO",
    "NG=F": "UNG", "HG=F": "CPER", "ZN=F": "IEF", "ZB=F": "TLT", "ZT=F": "SHY", "^TNX": "IEF", "^TYX": "TLT",
    "DX-Y.NYB": "UUP", "EURUSD=X": "FXE", "BTC-USD": "IBIT", "ETH-USD": "ETHA",
}
# Vola-Index je Leitwert (für den Gesamtmarkt ersetzt er die eigene IV-Historie)
VOLA_INDEX = {"SPY": "^VIX", "QQQ": "^VXN", "IWM": "^RVX", "DIA": "^VXD"}
# COT-Markt (Kennung aus cot.MAERKTE) je Ticker
COT = {
    "ES=F": "spx", "MES=F": "spx", "^GSPC": "spx", "SPY": "spx", "NQ=F": "ndx", "MNQ=F": "ndx", "^NDX": "ndx", "QQQ": "ndx",
    "ZN=F": "ust10", "^TNX": "ust10", "IEF": "ust10", "ZT=F": "ust2", "SHY": "ust2", "DX-Y.NYB": "usd", "UUP": "usd",
    "EURUSD=X": "eur", "6E=F": "eur", "^VIX": "vix", "BTC-USD": "btc", "IBIT": "btc", "CL=F": "oel", "USO": "oel",
    "GC=F": "gold", "GLD": "gold", "HG=F": "kupfer", "CPER": "kupfer",
}
TICKER_FORM = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,14}$")

_MARKT = [
    {"id": "markt:sp500", "name": "S&P 500", "leit": "SPY", "index": "^GSPC", "mitglieder": "sp500"},
    {"id": "markt:ndx", "name": "Nasdaq 100", "leit": "QQQ", "index": "^NDX", "mitglieder": "ndx"},
    {"id": "markt:rut", "name": "Russell 2000", "leit": "IWM", "index": "^RUT"},
    {"id": "markt:dax", "name": "DAX", "leit": "^GDAXI", "index": "^GDAXI", "mitglieder": "dax"},
    {"id": "markt:sx5e", "name": "Euro Stoxx 50", "leit": "^STOXX50E", "index": "^STOXX50E", "mitglieder": "sx5e"},
]
_KLASSEN = [
    {"id": "klasse:zins10", "name": "US-Zinsen 10 Jahre", "leit": "^TNX", "einheit": "rendite"},
    {"id": "klasse:zins2", "name": "US-Zinsen 2 Jahre", "leit": "ZT=F", "einheit": "kurs", "hinweis": "Kurs des 2-jährigen Anleihe-Futures: steigt, wenn die Renditen fallen."},
    {"id": "klasse:dollar", "name": "Dollar-Index", "leit": "DX-Y.NYB"},
    {"id": "klasse:eurusd", "name": "Euro gegen Dollar", "leit": "EURUSD=X"},
    {"id": "klasse:oel", "name": "Öl (WTI)", "leit": "CL=F"},
    {"id": "klasse:gold", "name": "Gold", "leit": "GC=F"},
    {"id": "klasse:kupfer", "name": "Kupfer", "leit": "HG=F"},
    {"id": "klasse:bitcoin", "name": "Bitcoin", "leit": "BTC-USD"},
]
_FAKTOREN = [
    {"id": "faktor:growth_value", "name": "Growth gegen Value", "leit": "IVW", "gegen": "IVE"},
    {"id": "faktor:klein_gross", "name": "Kleine gegen große Firmen", "leit": "IWM", "gegen": "SPY"},
    {"id": "faktor:gleich", "name": "Gleichgewichtet gegen S&P 500", "leit": "RSP", "gegen": "SPY"},
    {"id": "faktor:nasdaq", "name": "Nasdaq 100 gegen S&P 500", "leit": "QQQ", "gegen": "SPY"},
    {"id": "faktor:momentum", "name": "Momentum gegen Markt", "leit": "MTUM", "gegen": "SPY"},
    {"id": "faktor:qualitaet", "name": "Qualität gegen Markt", "leit": "QUAL", "gegen": "SPY"},
]
_YAHOO_SEKTOR = {etf: sektor for sektor, etf in SEKTOR_ETF.items()}
# GICS-Namen wie in der Fundamentalanalyse ("Kurs & Kennzahl", Körbe sektor:<Name>)
_GICS = {"XLK": "Information Technology", "XLC": "Communication Services", "XLY": "Consumer Discretionary", "XLP": "Consumer Staples",
         "XLV": "Health Care", "XLF": "Financials", "XLI": "Industrials", "XLE": "Energy", "XLB": "Materials", "XLU": "Utilities", "XLRE": "Real Estate"}


def _themen() -> list[dict]:
    try:
        from .themen import lade_themen
        return lade_themen()
    except Exception:  # noqa: BLE001  (fehlt die Datei, gibt es eben keine Themen-Richtungen)
        return []


def richtungen() -> list[dict]:
    """Alle Marktrichtungen für das Auswahlfeld, gruppiert."""
    aus = [{"id": r["id"], "name": r["name"], "gruppe": "Gesamtmarkt"} for r in _MARKT]
    aus += [{"id": f"sektor:{etf}", "name": name, "gruppe": "Sektoren", "zusatz": etf} for etf, name in SEKTOREN.items()]
    aus += [{"id": f"thema:{t['name']}", "name": t["name"], "gruppe": "Themen", "zusatz": f"{len(t['werte'])} Werte"} for t in _themen()]
    aus += [{"id": r["id"], "name": r["name"], "gruppe": "Anlageklassen", "zusatz": r["leit"]} for r in _KLASSEN]
    aus += [{"id": r["id"], "name": r["name"], "gruppe": "Faktoren", "zusatz": f"{r['leit']} / {r['gegen']}"} for r in _FAKTOREN]
    return aus


def wert_art(t: str) -> str:
    if t.endswith("=F"):
        return "future"
    if t.endswith("=X") or t == "DX-Y.NYB":
        return "devise"
    if t.startswith("^"):
        return "index"
    if re.search(r"-(USD|EUR)$", t):
        return "krypto"
    return "aktie"


def optionen_fuer(t: str) -> tuple[str | None, bool]:
    """(Ticker für Optionen und Gamma, ob es ein Stellvertreter ist). US-Aktien und -ETFs: der Wert selbst
    (ob Yahoo wirklich Optionen hat, merkt das Widget). Werte mit Börsenkürzel (.DE, .PA …): keine."""
    if t in STELLVERTRETER:
        return STELLVERTRETER[t], True
    if wert_art(t) != "aktie" or "." in t:
        return None, False
    return t, False


def _mitglieder_sektor(etf: str, anzahl: int = 20) -> list[str]:
    from ..cache import markt_lesen
    kennz = markt_lesen("kennzahlen:sp500")[0] or {}
    sektor = _YAHOO_SEKTOR.get(etf)
    werte = [(t, w.get("cap") or 0) for t, w in kennz.items() if sektor and w.get("yahoo_sektor") == sektor]
    return [t for t, _ in sorted(werte, key=lambda x: -x[1])[:anzahl]]


def _mitglieder_index(index: str, anzahl: int = 30) -> list[str]:
    from ..cache import markt_lesen
    kennz = markt_lesen(f"kennzahlen:{index}")[0] or {}
    return [t for t, _ in sorted(((t, w.get("cap") or 0) for t, w in kennz.items()), key=lambda x: -x[1])[:anzahl]]


def wert(t: str) -> dict:
    t = t.strip().upper()
    if not TICKER_FORM.match(t):
        raise ValueError("Unbekannter Wert.")
    from .namen import name
    opt, vertreten = optionen_fuer(t)
    return {"art": "wert", "id": t, "ticker": t, "name": name(t), "typ": wert_art(t), "optionen": opt, "optionen_vertreten": vertreten,
            "vola_index": VOLA_INDEX.get(opt) if vertreten else VOLA_INDEX.get(t), "cot": COT.get(t)}


def richtung(rid: str) -> dict:
    basis = {"art": "richtung", "id": rid, "mitglieder": []}
    for r in _MARKT:
        if r["id"] == rid:
            aus = {**basis, "gruppe": "Gesamtmarkt", "name": r["name"], "ticker": r["leit"], "index": r["index"], "optionen": optionen_fuer(r["leit"])[0],
                   "vola_index": VOLA_INDEX.get(optionen_fuer(r["leit"])[0] or ""), "cot": COT.get(r["leit"]) or COT.get(r["index"])}
            if r.get("mitglieder"):
                aus["mitglieder"] = _mitglieder_index(r["mitglieder"])
                aus["mitglieder_index"] = r["mitglieder"]
            if r["id"] == "markt:sp500":
                aus["fund_id"] = "index:sp500"
            return aus
    if rid.startswith("sektor:"):
        etf = rid.split(":", 1)[1]
        if etf not in SEKTOREN:
            raise ValueError("Unbekannter Sektor.")
        return {**basis, "gruppe": "Sektoren", "name": SEKTOREN[etf], "ticker": etf, "optionen": etf, "cot": None,
                "yahoo_sektor": _YAHOO_SEKTOR.get(etf), "mitglieder": _mitglieder_sektor(etf), "fund_id": f"sektor:{_GICS[etf]}"}
    if rid.startswith("thema:"):
        name = rid.split(":", 1)[1]
        t = next((x for x in _themen() if x["name"] == name), None)
        if not t:
            raise ValueError("Unbekanntes Thema.")
        werte = list(t["werte"])
        return {**basis, "gruppe": "Themen", "name": name, "ticker": None, "korb": True, "optionen": None, "cot": None,
                "mitglieder": werte, "mitglieder_namen": t["werte"], "fund_id": f"thema:{name}"}
    for r in _KLASSEN:
        if r["id"] == rid:
            opt, _ = optionen_fuer(r["leit"])
            return {**basis, "gruppe": "Anlageklassen", "name": r["name"], "ticker": r["leit"], "optionen": opt, "optionen_vertreten": True,
                    "cot": COT.get(r["leit"]), "hinweis": r.get("hinweis")}
    for r in _FAKTOREN:
        if r["id"] == rid:
            return {**basis, "gruppe": "Faktoren", "name": r["name"], "ticker": r["leit"], "gegen": r["gegen"], "optionen": None, "cot": None,
                    "mitglieder": [r["leit"], r["gegen"]]}
    raise ValueError("Unbekannte Marktrichtung.")


def aufloesen(bid: str) -> dict:
    bid = (bid or "").strip()
    if not bid:
        raise ValueError("Kein Bezug gewählt.")
    if ":" in bid:
        return richtung(bid)
    return wert(bid)
