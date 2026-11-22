"""Holt Kurse von Yahoo Finance und merkt sie sich in einer kleinen Datenbank.

US-Werte kommen während der US-Handelszeit jede Minute von Finnhub, europäische Werte während
der Xetra-Handelszeit alle 5 Minuten gemeinsam von Yahoo, sonst stündlich. Fällt eine Quelle
aus, wird der letzte bekannte Kurs verwendet und als veraltet markiert.
"""
import time
from datetime import datetime, timezone

import yaml
import yfinance as yf

from ..cache import STUNDE, TAG, markt_lesen as cache_lesen, markt_schreiben as cache_schreiben
from ..konfig import MARKT_DATEN
from . import finnhub
from .boersen import EU, US, boerse_von, ist_offen

FUENF_MINUTEN = 5 * 60
EINE_MINUTE = 55  # knapp unter 60, damit der Minutentakt der Seite immer einen neuen Kurs bekommt
ECHTZEIT_MAX_ALTER = 5 * 60  # ein älterer letzter Handel gilt auch bei offener Börse als verzögert
MAPPING_DATEI = MARKT_DATEN / "ticker_mapping.yaml"
ZWEITBOERSE_DATEI = MARKT_DATEN / "zweitboerse.yaml"
HANDELSPLATZ = {".DE": "Xetra", ".F": "Frankfurt"}
BOERSENTYPEN = {"EQUITY", "ETF", "MUTUALFUND"}
MAPPING_KOPF = (
    "# Welche ISIN gehört zu welchem Yahoo-Ticker?\n"
    "# Fehlende Einträge ergänzt das Tool automatisch. Du kannst jeden Eintrag von Hand ändern.\n"
)


def zeit_des_abrufs(schluessel: str) -> str | None:
    """Wann dieser Wert zuletzt von der Datenquelle geholt wurde, als Zeitangabe in UTC."""
    _, alter = cache_lesen(schluessel)
    return None if alter is None else datetime.fromtimestamp(time.time() - alter, timezone.utc).isoformat()


def _max_alter(ticker: str) -> int:
    """Wie alt ein Kurs höchstens sein darf, bevor er neu geholt wird."""
    boerse = boerse_von(ticker)
    if boerse == EU and ist_offen(EU):
        return FUENF_MINUTEN
    if boerse == US and ist_offen(US) and finnhub.verfuegbar():
        return EINE_MINUTE
    return STUNDE


def vorladen(tickers: list[str]) -> None:
    """Holt alle fälligen Europa-Werte und Wechselkurse gemeinsam in einer Abfrage."""
    if not ist_offen(US):  # dann zählen auch die deutschen Notierungen der US-Werte
        zweit = _zweitboerse_lesen()
        tickers = list(tickers) + [zweit[t] for t in tickers if boerse_von(t) == US and zweit.get(t)]
    bekannt = {t: cache_lesen(f"kurs:{t}") for t in tickers}
    faellig = {}  # Ticker -> Währung, nur Werte mit bereits bekannter Währung
    for t, (daten, alter) in bekannt.items():
        if not daten:
            continue  # beim ersten Mal holt _rohkurs den Wert einzeln und merkt sich die Währung
        if boerse_von(t) == EU and alter >= _max_alter(t):
            faellig[t] = daten["waehrung"]
        waehrung = daten["waehrung"].upper().replace("GBX", "GBP")
        if waehrung != "EUR":
            fx = f"{waehrung}EUR=X"
            fx_daten, fx_alter = cache_lesen(f"kurs:{fx}")
            if fx_daten and fx_alter >= _max_alter(fx):
                faellig[fx] = fx_daten["waehrung"]
    if not faellig:
        return
    try:
        tabelle = yf.download(list(faellig), period="5d", auto_adjust=False, progress=False, group_by="ticker", threads=False)
    except Exception:
        return  # die Einzelabfrage in _rohkurs fängt das auf
    for t, waehrung in faellig.items():
        try:
            schluss = tabelle[t]["Close"].dropna()
            if schluss.empty:
                continue
            cache_schreiben(f"kurs:{t}", {
                "preis": float(schluss.iloc[-1]),
                "waehrung": waehrung,
                "stand": schluss.index[-1].date().isoformat(),
            })
        except Exception:
            continue


def aktualitaet(ticker: str, daten: dict) -> str:
    """'echtzeit', 'verzoegert' oder 'schluss' (Börse gerade geschlossen)."""
    boerse = boerse_von(ticker)
    if boerse and not ist_offen(boerse):
        return "schluss"
    if daten.get("quelle") == "finnhub" and time.time() - daten["zeitpunkt"] <= ECHTZEIT_MAX_ALTER:
        return "echtzeit"
    return "verzoegert"


def _rohkurs(ticker: str) -> tuple[dict | None, bool]:
    """Gibt (Kursdaten, veraltet?) zurück. Kursdaten sind None, wenn es gar keine gibt."""
    schluessel = f"kurs:{ticker}"
    daten, alter = cache_lesen(schluessel)
    if daten and alter < _max_alter(ticker):
        return daten, False
    if boerse_von(ticker) == US and finnhub.verfuegbar():
        neu = finnhub.kurs(ticker)
        if neu:
            cache_schreiben(schluessel, neu)
            return neu, False
        if daten:
            return daten, True  # kein Ausweichen auf Yahoo im Minutentakt
    try:
        t = yf.Ticker(ticker)
        schluss = t.history(period="5d", auto_adjust=False)["Close"].dropna()
        waehrung = t.history_metadata.get("currency")
        if schluss.empty or not waehrung:
            raise ValueError("keine Daten")
        neu = {
            "preis": float(schluss.iloc[-1]),
            "waehrung": waehrung,
            "stand": schluss.index[-1].date().isoformat(),
        }
    except Exception:
        return daten, True
    cache_schreiben(schluessel, neu)
    return neu, False


def kurs_eur(ticker: str, vorboerse: bool = True) -> dict:
    """Aktueller Kurs in Euro. Status: 'aktuell', 'veraltet' oder 'fehlt'.

    Ist die US-Börse zu, kann eine US-Aktie in Deutschland schon weitergehandelt worden sein,
    z. B. am Montagmorgen nach einem schwachen Wochenende. Dann gilt der neuere deutsche Kurs.
    vorboerse=False schaltet das ab: dann zählt nur der Handel in New York.
    """
    ergebnis = _kurs_eur(ticker)
    if not vorboerse or boerse_von(ticker) != US or ist_offen(US) or not ergebnis.get("stand"):
        return ergebnis
    zweit = zweitboerse(ticker)
    if not zweit:
        return ergebnis
    de = _kurs_eur(zweit)
    if not de.get("preis_eur") or not de.get("stand") or de["stand"] <= ergebnis["stand"]:
        return ergebnis  # nach US-Schluss ist der US-Kurs der neuere
    # "preis" bleibt in Dollar, damit Rechnungen ohne Währungseffekt weiter stimmen.
    fx = ergebnis["preis_eur"] / ergebnis["preis"] if ergebnis.get("preis") else None
    platz = next((n for endung, n in HANDELSPLATZ.items() if zweit.endswith(endung)), "Deutschland")
    return {**de, "preis": de["preis_eur"] / fx if fx else None, "handelsplatz": platz}


def _kurs_eur(ticker: str) -> dict:
    daten, veraltet = _rohkurs(ticker)
    if not daten:
        return {"preis_eur": None, "stand": None, "status": "fehlt"}

    waehrung, faktor = daten["waehrung"], 1.0
    if waehrung in ("GBp", "GBX"):  # London notiert oft in Pence
        waehrung, faktor = "GBP", 0.01

    if waehrung.upper() != "EUR":
        fx, fx_alt = _rohkurs(f"{waehrung.upper()}EUR=X")
        if not fx:
            return {"preis_eur": None, "stand": None, "status": "fehlt"}
        faktor *= fx["preis"]
        veraltet = veraltet or fx_alt

    return {
        "preis": daten["preis"],  # in der Handelswährung, für Veränderungen ohne Währungseffekt
        "preis_eur": daten["preis"] * faktor,
        "stand": daten["stand"],
        "status": "veraltet" if veraltet else "aktuell",
        "aktualitaet": aktualitaet(ticker, daten),
        "aktualisiert": zeit_des_abrufs(f"kurs:{ticker}"),
    }


def _zweitboerse_lesen() -> dict:
    if not ZWEITBOERSE_DATEI.exists():
        return {}
    return yaml.safe_load(ZWEITBOERSE_DATEI.read_text(encoding="utf-8")) or {}


def zweitboerse(ticker: str) -> str | None:
    """Deutsche Notierung einer US-Aktie. Fehlt sie in der Datei, wird einmal am Tag danach gesucht."""
    eintraege = _zweitboerse_lesen()
    if ticker in eintraege:
        return eintraege[ticker] or None
    schluessel = f"zweitboerse:{ticker}"
    treffer, alter = cache_lesen(schluessel)
    if alter is not None and alter < TAG:
        return treffer
    treffer = None
    try:
        name = (yf.Ticker(ticker).info.get("longName") or "").split()
        if name:
            quotes = yf.Search(" ".join(name[:2]), max_results=25, news_count=0).quotes
            # nur Xetra, und der Name muss mit demselben Wort beginnen, sonst ist es ein anderer Wert
            treffer = next((q["symbol"] for q in quotes if q.get("exchange") == "GER"
                            and (q.get("shortname") or "").upper().startswith(name[0].upper())), None)
    except Exception:
        treffer = None  # auch einen Fehlschlag einen Tag merken, sonst bremst jede Abfrage
    cache_schreiben(schluessel, treffer)
    if treffer:
        with ZWEITBOERSE_DATEI.open("a", encoding="utf-8") as datei:
            datei.write(f"{ticker}: {treffer}\n")
    return treffer


def _mapping_lesen() -> dict:
    if not MAPPING_DATEI.exists():
        MAPPING_DATEI.write_text(MAPPING_KOPF, encoding="utf-8")
    return yaml.safe_load(MAPPING_DATEI.read_text(encoding="utf-8")) or {}


def _suche_ticker(isin: str) -> str | None:
    schluessel = f"isin:{isin}"
    treffer, alter = cache_lesen(schluessel)
    if alter is not None and alter < TAG:  # auch "nichts gefunden" einen Tag merken
        return treffer
    try:
        ergebnisse = yf.Search(isin, max_results=5, news_count=0, enable_fuzzy_query=False).quotes
    except Exception:
        return None
    passend = [
        q["symbol"] for q in ergebnisse
        if q.get("quoteType") in BOERSENTYPEN and not q["symbol"].startswith(isin)
    ]
    # Deutsche Notierung bevorzugen, sonst den ersten Treffer nehmen.
    ticker = next((s for s in passend if s.endswith(".DE")), passend[0] if passend else None)
    cache_schreiben(schluessel, ticker)
    return ticker


def ticker_fuer(isin: str, art: str) -> str | None:
    """Findet den Yahoo-Ticker. Eigene Einträge in ticker_mapping.yaml haben Vorrang."""
    if art == "CRYPTO":  # Trade Republic schreibt hier nur das Kürzel, z. B. BTC
        return f"{isin}-EUR"
    mapping = _mapping_lesen()
    if mapping.get(isin):
        return str(mapping[isin])
    ticker = _suche_ticker(isin)
    if ticker:
        with MAPPING_DATEI.open("a", encoding="utf-8") as datei:
            datei.write(f"{isin}: {ticker}\n")
    return ticker


def suche_werte(anfrage: str) -> list[dict]:
    """Findet handelbare Werte zu einem Namen oder Kürzel."""
    schluessel = f"suche:{anfrage.casefold()}"
    alt, alter = cache_lesen(schluessel)
    if alt is not None and alter < TAG:
        return alt
    try:
        roh = yf.Search(anfrage, max_results=8, news_count=0, enable_fuzzy_query=True).quotes
    except Exception:
        return alt or []
    neu = [
        {"ticker": q["symbol"], "name": q.get("longname") or q.get("shortname") or q["symbol"], "boerse": q.get("exchDisp", ""), "art": q.get("typeDisp", "")}
        for q in roh if q.get("quoteType") in (*BOERSENTYPEN, "CRYPTOCURRENCY") and q.get("symbol")
    ]
    cache_schreiben(schluessel, neu)
    return neu


def waehrung_von(ticker: str) -> str | None:
    """Die Währung, die beim letzten Kursabruf für diesen Wert gemeldet wurde."""
    daten, _ = cache_lesen(f"kurs:{ticker}")
    return daten.get("waehrung") if daten else None


def minutenkurse(tickers: list[str]) -> dict:
    """1-Minuten-Schlusskurse der letzten Tage je Ticker, in deutscher Zeit."""
    tabelle = yf.download(tickers, period="5d", interval="1m", auto_adjust=False, progress=False, group_by="ticker", threads=True)
    ergebnis = {}
    for ticker in tickers:
        try:
            reihe = tabelle[ticker]["Close"].dropna()
        except KeyError:
            continue
        if len(reihe):
            ergebnis[ticker] = reihe.tz_convert("Europe/Berlin")
    return ergebnis


def stammdaten(ticker: str) -> dict:
    """Sektor, Land und Währung von Yahoo, einmal pro Tag."""
    schluessel = f"info:{ticker}"
    alt, alter = cache_lesen(schluessel)
    if alt is not None and alter < TAG:
        return alt
    try:
        roh = yf.Ticker(ticker).info
        neu = {k: roh.get(k) for k in ("sector", "country", "currency")}
    except Exception:
        return alt or {}
    cache_schreiben(schluessel, neu)
    return neu
