"""Earnings Cheat Sheet: alle Zahlen, die man vor den Quartalszahlen einer Firma kennen sollte.

Quelle ist Yahoo (über yfinance), alles Marktdaten und für alle Nutzer gleich:
- Konsens für Gewinn je Aktie (EPS) und Umsatz: Durchschnitt, niedrigste und höchste Schätzung,
  Vorjahreswert, Wachstum, Zahl der Analysten. Für das laufende und nächste Quartal und die Geschäftsjahre.
- Revisionen: wie sich der EPS-Konsens in den letzten 90 Tagen verändert hat und wie viele Analysten
  ihre Schätzung in den letzten 7 und 30 Tagen erhöht oder gesenkt haben.
- Trackrecord: Erwartung, Ergebnis und Überraschung der letzten vier Quartale.
- Analysten: Kursziele, Empfehlung und die letzten Hoch- und Herabstufungen.
- Shortquote und Bewertung.
- Quartalszahlen der letzten fünf Quartale (Umsatz, Margen, Gewinn, Capex, Free Cashflow, Bilanz),
  Bewertungs- und Qualitätskennzahlen, Eigentümer, Empfehlungsverteilung und die Kursentwicklung
  vor den Zahlen im Vergleich zum S&P 500.
Kursreaktionen und die vom Optionsmarkt erwartete Bewegung kommen aus earnings_moves.
"""
import math
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

from ..cache import STUNDE, markt_lesen as cache_lesen, markt_schreiben as cache_schreiben

ZEITRAEUME = {"0q": "Laufendes Quartal", "+1q": "Nächstes Quartal", "0y": "Geschäftsjahr", "+1y": "Nächstes Geschäftsjahr"}


def _z(wert):
    try:
        z = float(wert)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(z) or math.isinf(z) else z


def _tabelle(df: pd.DataFrame | None, felder: dict[str, str]) -> dict:
    """{Zeitraum: {Feld: Zahl}} aus einer Yahoo-Tabelle mit den Zeilen 0q, +1q, 0y, +1y."""
    if df is None or getattr(df, "empty", True):
        return {}
    aus = {}
    for periode, zeile in df.iterrows():
        if periode in ZEITRAEUME:
            aus[periode] = {neu: _z(zeile.get(alt)) for alt, neu in felder.items()}
            if "currency" in zeile:
                aus[periode]["waehrung"] = zeile.get("currency")
    return aus


def _sicher(funktion):
    try:
        return funktion()
    except Exception:
        return None


def _termin(aktie) -> dict:
    kal = _sicher(lambda: aktie.calendar) or {}
    tage = [d for d in kal.get("Earnings Date", []) if isinstance(d, date)]
    if not tage:
        return {}
    # Yahoo nennt manchmal eine Spanne (zwei Tage), solange der Termin nicht bestätigt ist.
    return {"datum": min(tage).isoformat(), "bis": max(tage).isoformat() if len(tage) > 1 else None}


def _historie(aktie) -> list[dict]:
    df = _sicher(lambda: aktie.earnings_history)
    if df is None or df.empty:
        return []
    aus = []
    for quartal, z in df.sort_index().iterrows():
        aus.append({"quartal": pd.Timestamp(quartal).date().isoformat(), "erwartet": _z(z.get("epsEstimate")),
                    "ist": _z(z.get("epsActual")),
                    "ueberraschung": None if _z(z.get("surprisePercent")) is None else _z(z.get("surprisePercent")) * 100})
    return aus[-4:]


def _einstufungen(aktie) -> list[dict]:
    df = _sicher(lambda: aktie.upgrades_downgrades)
    if df is None or df.empty:
        return []
    grenze = pd.Timestamp.now() - pd.Timedelta(days=90)
    df = df[pd.to_datetime(df.index) >= grenze].sort_index(ascending=False)
    aus = []
    for zeit, z in df.head(10).iterrows():
        aus.append({"datum": pd.Timestamp(zeit).date().isoformat(), "haus": z.get("Firm"), "aktion": z.get("Action"),
                    "von": z.get("FromGrade") or None, "nach": z.get("ToGrade") or None,
                    "ziel": _z(z.get("currentPriceTarget")), "ziel_vorher": _z(z.get("priorPriceTarget"))})
    return aus


# Zeilen der Quartalstabellen von Yahoo: (Tabelle, Yahoo-Zeile(n), eigener Name, Vorzeichen umdrehen)
QUARTAL = [
    ("guv", ["Total Revenue", "Operating Revenue"], "umsatz", False),
    ("guv", ["Gross Profit"], "brutto", False),
    ("guv", ["Operating Income", "Total Operating Income As Reported"], "operativ", False),
    ("guv", ["EBITDA", "Normalized EBITDA"], "ebitda", False),
    ("guv", ["Net Income", "Net Income Common Stockholders"], "netto", False),
    ("guv", ["Diluted EPS", "Basic EPS"], "eps", False),
    ("guv", ["Research And Development"], "fue", False),
    ("guv", ["Selling General And Administration"], "vertrieb", False),
    ("guv", ["Diluted Average Shares", "Basic Average Shares"], "aktien", False),
    ("cf", ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"], "ocf", False),
    ("cf", ["Capital Expenditure", "Capital Expenditure Reported"], "capex", True),
    ("cf", ["Free Cash Flow"], "fcf", False),
    ("cf", ["Stock Based Compensation"], "sbc", False),
    ("cf", ["Repurchase Of Capital Stock", "Common Stock Payments"], "rueckkauf", True),
    ("cf", ["Cash Dividends Paid", "Common Stock Dividend Paid"], "dividende", True),
    ("cf", ["Depreciation And Amortization", "Depreciation Amortization Depletion"], "abschreibung", False),
    ("bil", ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"], "kasse", False),
    ("bil", ["Total Debt"], "schulden", False),
    ("bil", ["Inventory"], "lager", False),
    ("bil", ["Accounts Receivable", "Receivables"], "forderungen", False),
    ("bil", ["Stockholders Equity", "Common Stock Equity"], "eigenkapital", False),
]
KENNZAHLEN = {
    "grossMargins": "marge_brutto", "operatingMargins": "marge_operativ", "profitMargins": "marge_netto", "ebitdaMargins": "marge_ebitda",
    "revenueGrowth": "wachstum_umsatz", "earningsQuarterlyGrowth": "wachstum_gewinn", "returnOnEquity": "ek_rendite", "returnOnAssets": "gk_rendite",
    "freeCashflow": "fcf_ttm", "operatingCashflow": "ocf_ttm", "totalCash": "kasse", "totalDebt": "schulden", "debtToEquity": "verschuldung",
    "currentRatio": "liquiditaet", "beta": "beta", "trailingPegRatio": "peg", "priceToSalesTrailing12Months": "kuv", "priceToBook": "kbv",
    "enterpriseToEbitda": "ev_ebitda", "enterpriseToRevenue": "ev_umsatz", "enterpriseValue": "ev", "dividendYield": "dividende",
    "payoutRatio": "ausschuettung", "heldPercentInstitutions": "institutionen", "heldPercentInsiders": "insider",
    "sharesShort": "short_aktien", "sharesShortPriorMonth": "short_aktien_vormonat", "averageVolume": "volumen",
    "fiftyDayAverage": "schnitt50", "twoHundredDayAverage": "schnitt200", "totalRevenue": "umsatz_ttm", "ebitda": "ebitda_ttm",
    "fullTimeEmployees": "mitarbeiter",
}


def _quartale(aktie) -> list[dict]:
    tabellen = {"guv": _sicher(lambda: aktie.quarterly_income_stmt), "cf": _sicher(lambda: aktie.quarterly_cashflow),
                "bil": _sicher(lambda: aktie.quarterly_balance_sheet)}
    tage = sorted({pd.Timestamp(d) for t in tabellen.values() if t is not None and not t.empty for d in t.columns}, reverse=True)[:5]
    aus = []
    for tag in tage:
        z = {"quartal": tag.date().isoformat()}
        for tabelle, zeilen, name, umdrehen in QUARTAL:
            df = tabellen[tabelle]
            wert = None
            if df is not None and not df.empty and tag in df.columns:
                for zeile in zeilen:
                    if zeile in df.index and _z(df.at[zeile, tag]) is not None:
                        wert = _z(df.at[zeile, tag])
                        break
            z[name] = abs(wert) if (wert is not None and umdrehen) else wert
        if any(v is not None for k, v in z.items() if k != "quartal"):
            aus.append(z)
    return aus


def _empfehlungen(aktie) -> list[dict]:
    df = _sicher(lambda: aktie.recommendations)
    if df is None or df.empty:
        return []
    return [{"periode": z.get("period"), **{k: int(z.get(k) or 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")}} for _, z in df.iterrows()]


def _kursverlauf(aktie) -> dict:
    """Wertentwicklung der Aktie vor den Zahlen gegen den S&P 500."""
    try:
        a = aktie.history(period="1y", auto_adjust=True)["Close"].dropna()
        b = yf.Ticker("^GSPC").history(period="1y", auto_adjust=True)["Close"].dropna()
    except Exception:
        return {}
    if a.empty or b.empty:
        return {}
    a.index = a.index.tz_localize(None).normalize()
    b.index = b.index.tz_localize(None).normalize()

    def seit(reihe, tag):
        vorher = reihe[reihe.index <= tag]
        return None if vorher.empty else round(float(reihe.iloc[-1] / vorher.iloc[-1] - 1) * 100, 1)
    heute = a.index[-1]
    punkte = {"1W": heute - pd.Timedelta(days=7), "1M": heute - pd.Timedelta(days=30), "3M": heute - pd.Timedelta(days=91),
              "YTD": pd.Timestamp(heute.year, 1, 1) - pd.Timedelta(days=1), "1J": a.index[0]}
    return {k: {"aktie": seit(a, t), "spx": seit(b, t)} for k, t in punkte.items()}


def _wachstum_index(aktie) -> dict:
    """Erwartetes Gewinnwachstum der Firma gegen den S&P 500 je Zeitraum (Yahoo, in Prozent)."""
    df = _sicher(lambda: aktie.growth_estimates)
    if df is None or df.empty:
        return {}
    return {str(p): {"firma": None if _z(z.get("stockTrend")) is None else _z(z.get("stockTrend")) * 100,
                     "index": None if _z(z.get("indexTrend")) is None else _z(z.get("indexTrend")) * 100} for p, z in df.iterrows()}


def _fmp_jahre(ticker: str) -> list[dict]:
    """Jahres-Konsens von FMP (EBITDA, EBIT, Nettogewinn, Vertriebskosten …). Im Gratis-Tarif nur für
    große US-Werte; sonst leer. Der Key steht in der .env (FMP_API_KEY)."""
    from ..einstellungen import key
    k = key("FMP_API_KEY")
    if not k or "." in ticker:
        return []
    try:
        import json
        import urllib.request
        with urllib.request.urlopen(f"https://financialmodelingprep.com/stable/analyst-estimates?symbol={ticker}&period=annual&apikey={k}", timeout=20) as antwort:
            daten = json.loads(antwort.read())
    except Exception:
        return []
    heute = date.today().isoformat()
    zukunft = sorted((z for z in daten if z.get("date", "") >= heute), key=lambda z: z["date"])[:3]
    felder = ("revenue", "ebitda", "ebit", "netIncome", "sgaExpense", "eps")
    return [{"jahr_bis": z["date"], **{f: {"avg": _z(z.get(f"{f}Avg")), "low": _z(z.get(f"{f}Low")), "high": _z(z.get(f"{f}High"))} for f in felder},
             "analysten_umsatz": _z(z.get("numAnalystsRevenue")), "analysten_eps": _z(z.get("numAnalystsEps"))} for z in zukunft]


def sheet(ticker: str) -> dict:
    """Alle Daten für eine Firma, 6 Stunden im gemeinsamen Cache."""
    schluessel = f"earnings-sheet3:{ticker}"
    alt, alter = cache_lesen(schluessel)
    if alt is not None and alter < 6 * STUNDE:
        return alt
    aktie = yf.Ticker(ticker)
    info = _sicher(lambda: aktie.info) or {}
    neu = {
        "ticker": ticker,
        "termin": _termin(aktie),
        "eps": _tabelle(_sicher(lambda: aktie.earnings_estimate), {"avg": "konsens", "low": "low", "high": "high", "yearAgoEps": "vorjahr", "numberOfAnalysts": "analysten", "growth": "wachstum"}),
        "umsatz": _tabelle(_sicher(lambda: aktie.revenue_estimate), {"avg": "konsens", "low": "low", "high": "high", "yearAgoRevenue": "vorjahr", "numberOfAnalysts": "analysten", "growth": "wachstum"}),
        "trend": _tabelle(_sicher(lambda: aktie.eps_trend), {"current": "jetzt", "7daysAgo": "t7", "30daysAgo": "t30", "60daysAgo": "t60", "90daysAgo": "t90"}),
        "revisionen": _tabelle(_sicher(lambda: aktie.eps_revisions), {"upLast7days": "hoch7", "upLast30days": "hoch30", "downLast7Days": "runter7", "downLast30days": "runter30"}),
        "historie": _historie(aktie),
        "einstufungen": _einstufungen(aktie),
        "quartale": _quartale(aktie),
        "wachstum_index": _wachstum_index(aktie),
        "fmp": _fmp_jahre(ticker),
        "empfehlungen": _empfehlungen(aktie),
        "kursverlauf": _kursverlauf(aktie),
        "kennzahlen": {neu: _z(info.get(alt)) for alt, neu in KENNZAHLEN.items()},
        "info": {
            "name": info.get("shortName") or info.get("longName"),
            "kurs": _z(info.get("currentPrice") or info.get("regularMarketPrice")),
            "waehrung": info.get("currency"),
            "bilanz_waehrung": info.get("financialCurrency") or info.get("currency"),
            "sektor": info.get("sector"), "branche": info.get("industry"),
            "ziel_low": _z(info.get("targetLowPrice")), "ziel_mittel": _z(info.get("targetMeanPrice")),
            "ziel_median": _z(info.get("targetMedianPrice")), "ziel_high": _z(info.get("targetHighPrice")),
            "empfehlung": info.get("recommendationKey"), "empfehlung_wert": _z(info.get("recommendationMean")),
            "analysten": _z(info.get("numberOfAnalystOpinions")),
            "short_float": None if _z(info.get("shortPercentOfFloat")) is None else _z(info.get("shortPercentOfFloat")) * 100,
            "short_tage": _z(info.get("shortRatio")),
            "kgv_fwd": _z(info.get("forwardPE")), "kgv": _z(info.get("trailingPE")),
            "hoch52": _z(info.get("fiftyTwoWeekHigh")), "tief52": _z(info.get("fiftyTwoWeekLow")),
            "boersenwert": _z(info.get("marketCap")),
        },
        "stand": datetime.now().isoformat(timespec="minutes"),
    }
    cache_schreiben(schluessel, neu)
    _konsens_merken(ticker, neu)
    return neu


def _konsens_merken(ticker: str, s: dict) -> None:
    """Konsens für das anstehende Quartal dauerhaft merken (je Termin der neueste Stand).

    Yahoo schaltet kurz nach den Zahlen auf das nächste Quartal um. Damit die Zahlen-Auswertung die
    eingefügte Mitteilung trotzdem mit der Erwartung vergleichen kann, wird der letzte Stand vor dem Termin
    in historie.sqlite gesichert (nicht im Cache, der gelöscht werden darf)."""
    from .abruf import historie_eintragen
    datum = (s.get("termin") or {}).get("datum")
    # Nur vor dem Termin: danach kann "laufendes Quartal" schon das nächste sein, obwohl der alte Termin noch dasteht
    if not datum or datum < date.today().isoformat() or not (s["eps"].get("0q") or s["umsatz"].get("0q")):
        return
    try:
        historie_eintragen(f"konsens:{ticker}", datum, {
            "eps": s["eps"].get("0q"), "umsatz": s["umsatz"].get("0q"),
            "eps_naechstes": s["eps"].get("+1q"), "umsatz_naechstes": s["umsatz"].get("+1q"),
            "eps_jahr": s["eps"].get("0y"), "umsatz_jahr": s["umsatz"].get("0y"),
            "eps_folgejahr": s["eps"].get("+1y"), "umsatz_folgejahr": s["umsatz"].get("+1y"),
            "stand": s.get("stand"),
        })
    except Exception:
        pass


def konsens_archiv(ticker: str) -> dict:
    """{Termin: Konsens-Stand} aus konsens_merken."""
    from .abruf import historie_lesen
    try:
        return historie_lesen(f"konsens:{ticker}")
    except Exception:
        return {}
