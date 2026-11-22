"""Grunddaten je Wert für die Fundamentalanalyse (Punkt 24c).

Quellen
- Yahoo (yfinance): Gewinn- und Verlustrechnung, Bilanz, Kapitalflussrechnung (ca. 5 Quartale und 4 bis 5
  Geschäftsjahre), Marktdaten und Analystenschätzungen. Für alle Börsen.
- SEC "companyfacts" (nur US-Firmen): Quartalszahlen seit etwa 2009 für die lange Historie
  (Kurs-Gewinn-Chart über 10 Jahre). Wo Yahoo und SEC dasselbe Quartal haben, gilt Yahoo.
- FMP: im Gratis-Tarif seit 2026 fast alles gesperrt (Abfrage am 20.11.2026: Bilanzen, Kennzahlen,
  Schätzungen 402), nur die Peer-Liste geht. Wird für Peer-Vorschläge genutzt.

Aufbereitung
- Perioden: Kalenderquartale ("2026Q3"). Firmen mit abweichendem Geschäftsjahr werden auf das
  Kalenderquartal gelegt, in dem ihr Quartal überwiegend liegt (Ende minus 20 Tage).
- Jahre: Geschäftsjahre, beschriftet mit dem Jahr, in dem sie enden.
- TTM (letzte 12 Monate): Flussgrößen als Summe von 4 Quartalen, Bestandsgrößen zum Quartalsende.
- Währung: Beträge werden mit dem Monatsdurchschnitt des Wechselkurses (Yahoo) in die Anzeigewährung
  umgerechnet (USD oder EUR), Kursquelle wird mitgegeben.
- Bereinigt gegen GAAP: Yahoo liefert "Normalized Income" und "Normalized EBITDA" (ohne Sondereffekte).
  Bereinigt ersetzt damit Nettogewinn, Gewinn je Aktie und EBITDA; alles andere bleibt berichtet.
"""
from datetime import date, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd
import yfinance as yf

from ..abruf import STUNDE, TAG, gecacht, hole

# Yahoo-Zeilen -> eigene Felder (erste vorhandene Zeile gilt)
YAHOO = {
    "umsatz": ["Total Revenue", "Operating Revenue"],
    "bruttogewinn": ["Gross Profit"],
    "ebitda": ["EBITDA"],
    "ebitda_ber": ["Normalized EBITDA"],
    "ebit": ["EBIT"],
    "operatives_ergebnis": ["Operating Income", "Total Operating Income As Reported"],
    "nettogewinn": ["Net Income Common Stockholders", "Net Income"],
    "nettogewinn_ber": ["Normalized Income"],
    "eps": ["Diluted EPS"],
    "aktien": ["Diluted Average Shares", "Basic Average Shares"],
    "fue": ["Research And Development"],
    "zinsaufwand": ["Interest Expense", "Interest Expense Non Operating"],
    "steuerquote": ["Tax Rate For Calcs"],
    "vorsteuer": ["Pretax Income"],
    "ocf": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "capex": ["Capital Expenditure", "Capital Expenditure Reported"],
    "fcf": ["Free Cash Flow"],
    "abschreibungen": ["Depreciation And Amortization", "Depreciation Amortization Depletion", "Reconciled Depreciation"],
    "sbc": ["Stock Based Compensation"],
    "dividenden": ["Cash Dividends Paid", "Common Stock Dividend Paid"],
    "rueckkauf": ["Repurchase Of Capital Stock", "Common Stock Payments"],
    "cash": ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents", "Cash Financial"],
    "schulden": ["Total Debt"],
    "eigenkapital": ["Stockholders Equity", "Common Stock Equity"],
    "bilanzsumme": ["Total Assets"],
    "umlaufvermoegen": ["Current Assets"],
    "kurzfr_verbindlichkeiten": ["Current Liabilities"],
    "verbindlichkeiten": ["Total Liabilities Net Minority Interest"],
    "goodwill": ["Goodwill And Other Intangible Assets", "Goodwill"],
    "lager": ["Inventory"],
    "forderungen": ["Accounts Receivable", "Receivables"],
    "gewinnruecklagen": ["Retained Earnings"],
    "working_capital": ["Working Capital"],
    "investiertes_kapital": ["Invested Capital"],
    "aktien_ausstehend": ["Ordinary Shares Number", "Share Issued"],
    "umsatzkosten": ["Cost Of Revenue", "Reconciled Cost Of Revenue"],
}
# SEC-Begriffe für die lange Historie (US-Firmen)
SEC = {
    "umsatz": (["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"], "USD"),
    "bruttogewinn": (["GrossProfit"], "USD"),
    "operatives_ergebnis": (["OperatingIncomeLoss"], "USD"),
    "nettogewinn": (["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"], "USD"),
    "eps": (["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"], "USD/shares"),
    "aktien": (["WeightedAverageNumberOfDilutedSharesOutstanding"], "shares"),
    "fue": (["ResearchAndDevelopmentExpense"], "USD"),
    "ocf": (["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"], "USD"),
    "capex": (["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"], "USD"),
    "sbc": (["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"], "USD"),
    "abschreibungen": (["DepreciationDepletionAndAmortization", "DepreciationAndAmortization"], "USD"),
    "dividenden": (["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"], "USD"),
    "rueckkauf": (["PaymentsForRepurchaseOfCommonStock"], "USD"),
}
# Flussgrößen (über einen Zeitraum) – alles andere ist ein Bestand zum Stichtag
FLUSS = {"umsatz", "bruttogewinn", "ebitda", "ebitda_ber", "ebit", "operatives_ergebnis", "nettogewinn", "nettogewinn_ber", "eps", "fue",
         "zinsaufwand", "vorsteuer", "ocf", "capex", "fcf", "abschreibungen", "sbc", "dividenden", "rueckkauf", "umsatzkosten"}
NICHT_SUMMIEREN = {"aktien", "steuerquote"}  # Durchschnitt statt Summe
POSITIV = {"capex", "dividenden", "rueckkauf"}  # Yahoo zählt Abflüsse negativ: als Betrag speichern
KEINE_WAEHRUNG = {"aktien", "aktien_ausstehend", "steuerquote"}


def _z(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v != v or v in (float("inf"), float("-inf")) else v


def kalenderquartal(ende: date) -> str:
    return str(pd.Period(ende - timedelta(days=20), freq="Q"))


def _yahoo_tabelle(tab: pd.DataFrame | None, jahr: bool) -> dict:
    """{Periode: {feld: wert}} aus einer Yahoo-Tabelle (Spalten = Stichtage)."""
    aus = {}
    if tab is None or tab.empty:
        return aus
    for spalte in tab.columns:
        ende = pd.Timestamp(spalte).date()
        p = f"GJ{ende.year}" if jahr else kalenderquartal(ende)
        zeile = aus.setdefault(p, {"_ende": ende.isoformat()})
        for feld, namen in YAHOO.items():
            if feld in zeile:
                continue
            for n in namen:
                if n in tab.index:
                    v = _z(tab.at[n, spalte])
                    if v is not None:
                        zeile[feld] = abs(v) if feld in POSITIV else v
                        break
    return aus


def _sec_reihe(einheiten: list[dict], ist_aktien: bool) -> dict:
    """Quartale aus SEC-Angaben (Summen seit Geschäftsjahresbeginn werden in Quartale zerlegt)."""
    from ..cashflow import _quartale
    if ist_aktien:  # Aktienzahl ist ein Durchschnitt, nicht aufsummierbar: nur echte Quartalsangaben
        aus = {}
        for f in einheiten:
            if "start" in f and 80 <= (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days <= 100:
                aus.setdefault(kalenderquartal(date.fromisoformat(f["end"])), f["val"])
        return aus
    return _quartale(einheiten)


def _sec(ticker: str) -> dict:
    from ..cashflow import cik_liste
    info = cik_liste().get(ticker.upper())
    if not info:
        return {}
    d = hole(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{info['cik']:010d}.json", sec=True, zeitlimit=60).json()
    gaap = d.get("facts", {}).get("us-gaap", {})
    aus = {}
    for feld, (begriffe, einheit) in SEC.items():
        for b in begriffe:
            werte = gaap.get(b, {}).get("units", {}).get(einheit)
            if not werte:
                continue
            for q, v in _sec_reihe([w for w in werte if w.get("form") in ("10-Q", "10-K", "10-Q/A", "10-K/A")], feld == "aktien").items():
                aus.setdefault(q, {}).setdefault(feld, abs(v) if feld in POSITIV else v)
    return aus


def rohdaten(ticker: str) -> dict:
    """Berichtete Zahlen in Originalwährung, einmal am Tag neu."""
    def holen():
        a = yf.Ticker(ticker)
        info = a.info or {}
        if not info.get("quoteType"):
            raise LookupError(f"{ticker} bei Yahoo nicht gefunden")
        quartale, jahre = {}, {}
        for tab, ziel, jahr in ((a.quarterly_income_stmt, quartale, False), (a.quarterly_balance_sheet, quartale, False), (a.quarterly_cashflow, quartale, False),
                                (a.income_stmt, jahre, True), (a.balance_sheet, jahre, True), (a.cashflow, jahre, True)):
            try:
                for p, z in _yahoo_tabelle(tab, jahr).items():
                    ziel.setdefault(p, {}).update({k: v for k, v in z.items() if k not in ziel.get(p, {})})
            except Exception:  # noqa: BLE001 - eine fehlende Tabelle soll den Rest nicht verhindern
                pass
        for z in quartale.values():
            z["_quelle"] = "Yahoo"
        # Lange Historie aus der SEC (nur US-Firmen, nur Quartale, die Yahoo nicht hat)
        sec_fehler = None
        if info.get("country") == "United States" and "." not in ticker:
            try:
                for p, z in _sec(ticker).items():
                    ziel = quartale.setdefault(p, {"_quelle": "SEC"})
                    ergaenzt = [k for k in z if ziel.get(k) is None]
                    for k in ergaenzt:  # Yahoo hat Vorrang, die SEC füllt Lücken
                        ziel[k] = z[k]
                    if ergaenzt and ziel["_quelle"] == "Yahoo":
                        ziel["_quelle"] = "Yahoo + SEC"
            except Exception as f:  # noqa: BLE001
                sec_fehler = type(f).__name__
        # Fehlende Felder in Yahoo-Quartalen ergänzen, wo die SEC sie hat (z. B. ältere Quartale ohne FCF)
        # Fehlende Aktienzahl eines Quartals vom nächsten Nachbarquartal (ändert sich langsam)
        reihe = sorted(quartale)
        for i, p in enumerate(reihe):
            if quartale[p].get("aktien") is None:
                for j in (i - 1, i + 1, i - 2, i + 2):
                    if 0 <= j < len(reihe) and quartale[reihe[j]].get("aktien"):
                        quartale[p]["aktien"] = quartale[reihe[j]]["aktien"]
                        break
        for z in [*quartale.values(), *jahre.values()]:
            if z.get("fcf") is None and z.get("ocf") is not None and z.get("capex") is not None:
                z["fcf"] = z["ocf"] - z["capex"]
            if z.get("eps") is None and z.get("nettogewinn") is not None and z.get("aktien"):
                z["eps"] = z["nettogewinn"] / z["aktien"]  # fehlt der Gewinn je Aktie, aus Gewinn und Aktienzahl
        markt = _marktdaten(a, info)
        return {"ticker": ticker.upper(), "quartale": quartale, "jahre": jahre, "markt": markt, "sec_fehler": sec_fehler,
                "abgerufen": pd.Timestamp.now().isoformat(timespec="minutes")}
    return gecacht(f"fund:roh:{ticker.upper()}", TAG, holen)


def _df(f):
    try:
        x = f()
        return x if isinstance(x, pd.DataFrame) and not x.empty else None
    except Exception:  # noqa: BLE001
        return None


def _marktdaten(a, info: dict) -> dict:
    felder = ("shortName", "longName", "sector", "industry", "country", "currency", "financialCurrency", "quoteType", "marketCap", "enterpriseValue",
              "currentPrice", "regularMarketPrice", "forwardPE", "trailingPE", "forwardEps", "trailingEps", "pegRatio", "trailingPegRatio",
              "targetMeanPrice", "targetLowPrice", "targetHighPrice", "recommendationMean", "recommendationKey", "numberOfAnalystOpinions",
              "shortPercentOfFloat", "shortRatio", "sharesShort", "sharesShortPriorMonth", "heldPercentInstitutions", "heldPercentInsiders",
              "dividendRate", "dividendYield", "payoutRatio", "fullTimeEmployees", "beta", "sharesOutstanding", "priceToSalesTrailing12Months",
              "priceToBook", "enterpriseToEbitda", "enterpriseToRevenue", "bookValue", "totalRevenue", "ebitda", "revenueGrowth", "earningsGrowth")
    m = {k: info.get(k) for k in felder}
    m["kurs"] = info.get("currentPrice") or info.get("regularMarketPrice")
    trend = _df(lambda: a.eps_trend)
    if trend is not None:
        m["eps_trend"] = {spalte: {str(i): _z(trend.at[i, spalte]) for i in trend.index} for spalte in trend.columns if spalte in ("current", "7daysAgo", "30daysAgo", "60daysAgo", "90daysAgo")}
    umsatz = _df(lambda: a.revenue_estimate)
    if umsatz is not None:
        m["umsatz_schaetzung"] = {str(i): {"avg": _z(umsatz.at[i, "avg"]) if "avg" in umsatz.columns else None,
                                           "vorjahr": _z(umsatz.at[i, "yearAgoRevenue"]) if "yearAgoRevenue" in umsatz.columns else None,
                                           "wachstum": _z(umsatz.at[i, "growth"]) if "growth" in umsatz.columns else None} for i in umsatz.index}
    gewinn = _df(lambda: a.earnings_estimate)
    if gewinn is not None:
        m["eps_schaetzung"] = {str(i): {"avg": _z(gewinn.at[i, "avg"]) if "avg" in gewinn.columns else None,
                                        "vorjahr": _z(gewinn.at[i, "yearAgoEps"]) if "yearAgoEps" in gewinn.columns else None,
                                        "analysten": _z(gewinn.at[i, "numberOfAnalysts"]) if "numberOfAnalysts" in gewinn.columns else None} for i in gewinn.index}
    rev = _df(lambda: a.eps_revisions)
    if rev is not None:
        m["revisionen"] = {str(i): {c: _z(rev.at[i, c]) for c in rev.columns} for i in rev.index}
    ins = _df(lambda: a.insider_purchases)
    if ins is not None and "Shares" in ins.columns:
        zeilen = {str(r.iloc[0]): _z(r["Shares"]) for _, r in ins.iterrows()}
        m["insider"] = {"kauf": zeilen.get("Purchases"), "verkauf": zeilen.get("Sales"), "netto": zeilen.get("Net Shares Purchased (Sold)"),
                        "netto_prozent": zeilen.get("% Net Shares Purchased (Sold)")}
    return m


@lru_cache(maxsize=32)
def _wechselkurs_monate(von: str, nach: str, tag: str) -> dict:
    """Monatsdurchschnitt: 1 Einheit von = x Einheiten nach (Yahoo). tag sorgt für tägliche Erneuerung."""
    if von == nach:
        return {}
    k = yf.Ticker(f"{von}{nach}=X").history(period="max", interval="1mo", auto_adjust=False)["Close"].dropna()
    k.index = k.index.tz_localize(None) if k.index.tz is not None else k.index
    return {p.strftime("%Y-%m"): float(v) for p, v in k.items()}


def kurs(von: str | None, nach: str, monat: str) -> float | None:
    """Wechselkurs für einen Monat (YYYY-MM); fehlt er, der nächstältere."""
    if not von or von == nach:
        return 1.0
    try:
        k = _wechselkurs_monate(von.upper(), nach, date.today().isoformat())
    except Exception:  # noqa: BLE001
        return None
    if not k:
        return None
    if monat in k:
        return k[monat]
    frueher = [m for m in k if m <= monat]
    return k[max(frueher)] if frueher else k[min(k)]


def kursverlauf(ticker: str, jahre: int = 11) -> pd.Series:
    """Monatsschlusskurse (unbereinigt um Dividenden, damit KGV-Rechnungen stimmen)."""
    def holen():
        h = yf.Ticker(ticker).history(period=f"{jahre}y", interval="1mo", auto_adjust=False)
        s = h["Close"].dropna()
        return {d.strftime("%Y-%m"): float(v) for d, v in s.items()}
    d = gecacht(f"fund:kurse:{ticker.upper()}:{jahre}", 12 * STUNDE, holen)
    return pd.Series(d).sort_index()


def dividenden(ticker: str) -> pd.Series:
    def holen():
        s = yf.Ticker(ticker).dividends
        return {d.strftime("%Y-%m-%d"): float(v) for d, v in s.items()}
    try:
        return pd.Series(gecacht(f"fund:div:{ticker.upper()}", TAG, holen)).sort_index()
    except Exception:  # noqa: BLE001
        return pd.Series(dtype=float)


def aufbereiten(roh: dict, waehrung: str = "USD", bereinigt: bool = False) -> dict:
    """Perioden in Anzeigewährung, mit TTM. Gibt {quartal, jahr, ttm} -> {periode: {feld: wert, _ende, _quelle}}."""
    m = roh["markt"]
    fin = (m.get("financialCurrency") or m.get("currency") or "USD").upper()
    preis_w = (m.get("currency") or fin).upper()
    quellen = {"bilanz": fin, "kurs": preis_w}

    def umrechnen(z: dict) -> dict:
        monat = (z.get("_ende") or "")[:7]
        f = kurs(fin, waehrung, monat) if monat else None
        aus = {"_ende": z.get("_ende"), "_quelle": z.get("_quelle", "Yahoo"), "_kurs": f}
        for k, v in z.items():
            if k.startswith("_"):
                continue
            aus[k] = v if (k in KEINE_WAEHRUNG or v is None) else (v * f if f else None)
        if bereinigt:
            for feld, ber in (("nettogewinn", "nettogewinn_ber"), ("ebitda", "ebitda_ber")):
                if aus.get(ber) is not None:
                    aus[feld] = aus[ber]
            if aus.get("nettogewinn_ber") is not None and aus.get("aktien"):
                aus["eps"] = aus["nettogewinn"] / aus["aktien"]
        return aus

    # Quartalsende ergänzen, wo die SEC keins mitgibt (Ende des Kalenderquartals)
    roh_q = {p: (z if z.get("_ende") else {**z, "_ende": pd.Period(p, freq="Q").end_time.date().isoformat()}) for p, z in roh["quartale"].items()}
    q = {p: umrechnen(z) for p, z in roh_q.items()}
    j ={p: umrechnen(z) for p, z in roh["jahre"].items()}
    # TTM je Quartal: Flüsse = Summe der letzten 4 Quartale, Bestände = Quartalsende
    ttm = {}
    for p in sorted(q):
        per = pd.Period(p, freq="Q")
        vier = [str(per - i) for i in range(4)]
        if not all(v in q for v in vier):
            continue
        z = {"_ende": q[p]["_ende"], "_quelle": q[p]["_quelle"]}
        for feld in set().union(*(q[v].keys() for v in vier)):
            if feld.startswith("_"):
                continue
            werte = [q[v].get(feld) for v in vier]
            if feld in FLUSS:
                z[feld] = sum(werte) if all(w is not None for w in werte) else None
            elif feld in NICHT_SUMMIEREN:
                gueltig = [w for w in werte if w is not None]
                z[feld] = float(np.mean(gueltig)) if gueltig else None
            else:
                z[feld] = q[p].get(feld)
        ttm[p] = z
    return {"quartal": dict(sorted(q.items())), "jahr": dict(sorted(j.items())), "ttm": ttm, "waehrung": waehrung,
            "waehrung_bilanz": quellen["bilanz"], "waehrung_kurs": quellen["kurs"], "bereinigt": bereinigt,
            "kursquelle": f"Yahoo {fin}{waehrung}=X, Monatsdurchschnitt" if fin != waehrung else None}
