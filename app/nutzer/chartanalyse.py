"""Persönlicher Teil der Chartanalyse: Auswahlliste, Preis-Alerts, IPO-Angaben und Trades.

Gespeichert im Ordner des Benutzers:
- chart_alerts.yaml: Preis-Alerts (Ticker, Preis, Richtung, Text)
- ipo.yaml: IPO-Preis und Termine (Ende Quiet Period, Lock-up-Ablauf, erste Zahlen) je Ticker
Die Trade-Schnappschüsse werden aus den Kursen bis zum Vortag jedes Trades gerechnet (kein Blick in
die Zukunft) und im Benutzer-Cache gehalten.
"""
import re
import uuid
from datetime import date, datetime, timedelta

import pandas as pd
import yaml

from ..benutzer import Benutzer
from ..cache import nutzer_lesen, nutzer_schreiben
from ..markt import chart_konfig as K
from ..markt.chartanalyse import analysiere, handelstag_schluessel, tageskurse
from ..markt.kurse import kurs_eur, ticker_fuer
from .tr_parser import lade_trades

TICKER_FORM = re.compile(r"^[A-Z0-9.^=\-]{1,15}$")
TERMIN_ARTEN = {"quiet": "Ende der Quiet Period", "lockup": "Lock-up-Ablauf", "zahlen": "Erste Quartalszahlen"}


class ChartFehler(Exception):
    """Eingabe passt nicht; der Text wird angezeigt."""


def _lesen(b: Benutzer, name: str, leer):
    datei = b.datei(name)
    if not datei.exists():
        return leer
    return yaml.safe_load(datei.read_text(encoding="utf-8")) or leer


def _schreiben(b: Benutzer, name: str, wert) -> None:
    datei = b.datei(name)
    datei.parent.mkdir(parents=True, exist_ok=True)
    zwischen = datei.with_suffix(".tmp")
    zwischen.write_text(yaml.safe_dump(wert, allow_unicode=True, sort_keys=False), encoding="utf-8")
    zwischen.replace(datei)


def pruefe_ticker(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if not TICKER_FORM.match(t):
        raise ChartFehler("Ungültiger Ticker.")
    return t


# ---------- IPO-Angaben ----------

def ipo_angaben(b: Benutzer, ticker: str) -> dict:
    return (_lesen(b, "ipo.yaml", {}) or {}).get(ticker, {})


def ipo_speichern(b: Benutzer, ticker: str, daten: dict) -> dict:
    ticker = pruefe_ticker(ticker)
    alle = _lesen(b, "ipo.yaml", {})
    eintrag = {}
    preis = daten.get("ipo_preis")
    if preis not in (None, ""):
        try:
            preis = float(str(preis).replace(",", "."))
        except ValueError:
            raise ChartFehler("Der IPO-Preis muss eine Zahl sein.")
        if not 0 < preis < 1e6:
            raise ChartFehler("Der IPO-Preis passt nicht.")
        eintrag["ipo_preis"] = preis
    termine = []
    for t in daten.get("termine") or []:
        if not t.get("datum"):
            continue
        if t.get("art") not in TERMIN_ARTEN:
            raise ChartFehler("Unbekannte Terminart.")
        try:
            tag = date.fromisoformat(t["datum"])
        except ValueError:
            raise ChartFehler("Datum im Format JJJJ-MM-TT.")
        termine.append({"art": t["art"], "datum": tag.isoformat()})
    if termine:
        eintrag["termine"] = termine
    if eintrag:
        alle[ticker] = eintrag
    else:
        alle.pop(ticker, None)
    _schreiben(b, "ipo.yaml", alle)
    return {"ok": True, "ipo": eintrag}


def ipo_termine(b: Benutzer) -> list[dict]:
    """Für den Kalender: alle eingetragenen IPO-Termine als eigene Termine."""
    aus = []
    for ticker, e in (_lesen(b, "ipo.yaml", {}) or {}).items():
        for t in e.get("termine", []):
            aus.append({"ticker": ticker, "name": f"{ticker}: {TERMIN_ARTEN[t['art']]}", "datum": t["datum"], "sitzung": "",
                        "ipo": TERMIN_ARTEN[t["art"]], "eps": None, "umsatz": None})
    return aus


# ---------- Preis-Alerts ----------

def alerts(b: Benutzer) -> dict:
    """Alle Alerts mit Live-Kurs (in Handelswährung) und ob sie erreicht sind."""
    liste = _lesen(b, "chart_alerts.yaml", [])
    kurse = {}
    for a in liste:
        if a["ticker"] not in kurse:
            try:
                kurse[a["ticker"]] = kurs_eur(a["ticker"]).get("preis")
            except Exception:
                kurse[a["ticker"]] = None
    # Einmal erreicht bleibt erreicht (auch wenn der Kurs danach zurückfällt): Zeitpunkt wird gespeichert.
    # "quittiert" = das Hinweis-Fenster wurde weggeklickt, es erscheint dann nicht wieder.
    from ..markt.namen import name as name_von
    aus, geaendert = [], False
    for a in liste:
        k = kurse.get(a["ticker"])
        jetzt_erreicht = None if k is None else (k >= a["preis"] if a["richtung"] == "ueber" else k <= a["preis"])
        if jetzt_erreicht and not a.get("erreicht_am"):
            a["erreicht_am"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            a["kurs_bei_erreichen"] = k
            geaendert = True
        try:
            name = name_von(a["ticker"])
        except Exception:
            name = a["ticker"]
        aus.append({**a, "name": name, "kurs": k, "erreicht": bool(a.get("erreicht_am")) or bool(jetzt_erreicht),
                    "abstand": None if not k else round((a["preis"] / k - 1) * 100, 2)})
    if geaendert:
        _schreiben(b, "chart_alerts.yaml", liste)
    return {"alerts": aus}


def alert_quittieren(b: Benutzer, kennung: str) -> dict:
    """Hinweis zu einem erreichten Alert weggeklickt: nicht noch einmal als Fenster zeigen."""
    liste = _lesen(b, "chart_alerts.yaml", [])
    for a in liste:
        if a["id"] == kennung:
            a["quittiert"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            _schreiben(b, "chart_alerts.yaml", liste)
            return {"ok": True}
    raise ChartFehler("Alert nicht gefunden.")


def alert_neu(b: Benutzer, daten: dict) -> dict:
    ticker = pruefe_ticker(daten.get("ticker", ""))
    try:
        preis = float(daten.get("preis"))
    except (TypeError, ValueError):
        raise ChartFehler("Der Preis fehlt.")
    if not 0 < preis < 1e7:
        raise ChartFehler("Der Preis passt nicht.")
    richtung = daten.get("richtung")
    if richtung not in ("ueber", "unter"):
        raise ChartFehler("Richtung muss 'ueber' oder 'unter' sein.")
    liste = _lesen(b, "chart_alerts.yaml", [])
    if len(liste) >= 100:
        raise ChartFehler("Höchstens 100 Alerts.")
    if any(a["ticker"] == ticker and abs(a["preis"] - preis) < 1e-9 and a["richtung"] == richtung for a in liste):
        raise ChartFehler("Diesen Alert gibt es schon.")
    text = str(daten.get("text") or "")[:120]
    liste.append({"id": uuid.uuid4().hex[:10], "ticker": ticker, "preis": round(preis, 4), "richtung": richtung, "text": text,
                  "erstellt": datetime.now().strftime("%Y-%m-%d %H:%M")})
    _schreiben(b, "chart_alerts.yaml", liste)
    return {"ok": True}


def alert_loeschen(b: Benutzer, kennung: str) -> dict:
    liste = _lesen(b, "chart_alerts.yaml", [])
    neu = [a for a in liste if a["id"] != kennung]
    if len(neu) == len(liste):
        raise ChartFehler("Alert nicht gefunden.")
    _schreiben(b, "chart_alerts.yaml", neu)
    return {"ok": True}


# ---------- Trades: Schnappschuss und Auswertung ----------

def _schnappschuss(df: pd.DataFrame, tag: pd.Timestamp) -> dict | None:
    """Chartanalyse mit den Kursen bis zum Vortag des Trades (der Trade-Tag selbst ist noch offen)."""
    vorher = df[df.index < tag]
    if len(vorher) < K.IPO_TAGE:
        return None
    a = analysiere(vorher, kompakt=True)
    kurs, atr_wert = a["kurs"], a.get("atr")
    atr_p = atr_wert / kurs * 100 if atr_wert else None
    w, u = a.get("naechster_widerstand"), a.get("naechste_unterstuetzung")
    im_widerstand = any(z["hochs"] > 0 for z in a.get("im_bereich", []))
    nah = im_widerstand or bool(w and atr_p and w["abstand"] <= K.TRADE_NAH_WIDERSTAND_ATR * atr_p)
    return {"regime": a["trend"].get("regime"), "abstand200": a["trend"].get("abstand200"), "struktur": a["struktur"].get("art"),
            "widerstand": None if not w else {"unten": round(w["unten"], 4), "oben": round(w["oben"], 4), "abstand": w["abstand"]},
            "unterstuetzung": None if not u else {"unten": round(u["unten"], 4), "oben": round(u["oben"], 4), "abstand": u["abstand"]},
            "atr_prozent": None if atr_p is None else round(atr_p, 2), "nah_widerstand": nah, "stand": a["stand"]}


def _danach(df: pd.DataFrame, tag: pd.Timestamp) -> dict:
    """Kurs 1, 4 und 12 Wochen nach dem Trade-Tag gegenüber dem Schluss am Trade-Tag (Handelswährung)."""
    ab = df[df.index >= tag]
    if ab.empty:
        return {}
    basis = float(ab["Close"].iloc[0])
    aus = {"basis": round(basis, 4)}
    for w in K.TRADE_WOCHEN:
        spaeter = df[df.index >= tag + timedelta(weeks=w)]
        aus[str(w)] = None if spaeter.empty else round((float(spaeter["Close"].iloc[0]) / basis - 1) * 100, 2)
    return aus


def _gruppe(trades: list[dict], name: str) -> dict:
    aus = {"name": name, "anzahl": len(trades)}
    for w in K.TRADE_WOCHEN:
        werte = [t["danach"][str(w)] for t in trades if t["danach"].get(str(w)) is not None]
        aus[f"w{w}"] = None if not werte else round(sum(werte) / len(werte), 2)
        aus[f"n{w}"] = len(werte)
    return aus


def trades(b: Benutzer) -> dict:
    roh = lade_trades(b)
    schluessel = f"chart:trades:{K.CACHE_VERSION}:{len(roh)}:{handelstag_schluessel()}"
    alt = nutzer_lesen(b, schluessel)
    if alt and alt[0]:
        return alt[0]
    liste, kurse, tickers = [], {}, {}
    for t in roh:
        isin = t["isin"]
        if isin not in tickers:
            try:
                tickers[isin] = ticker_fuer(isin, t["art"])
            except Exception:
                tickers[isin] = None
        ticker = tickers[isin]
        zeile = {"zeit": t["zeit"].strftime("%Y-%m-%d %H:%M"), "typ": "kauf" if t["typ"] == "BUY" else "verkauf", "name": t["name"],
                 "ticker": ticker, "stueck": t["stueck"], "betrag": round(abs(t["betrag"]), 2),
                 "preis_eur": round(abs(t["betrag"]) / t["stueck"], 4) if t["stueck"] else None, "schnappschuss": None, "danach": {}}
        if ticker:
            if ticker not in kurse:
                try:
                    kurse[ticker] = tageskurse(ticker)
                except Exception:
                    kurse[ticker] = None
            df = kurse[ticker]
            if df is not None:
                tag = pd.Timestamp(t["zeit"].date())
                try:
                    zeile["schnappschuss"] = _schnappschuss(df, tag)
                except Exception:
                    zeile["schnappschuss"] = None
                zeile["danach"] = _danach(df, tag)
        liste.append(zeile)
    mit = [t for t in liste if t["schnappschuss"]]
    verkauf = [t for t in mit if t["typ"] == "verkauf"]
    kauf = [t for t in mit if t["typ"] == "kauf" and t["schnappschuss"]["abstand200"] is not None]
    gruppen = {
        "exits": [_gruppe([t for t in verkauf if t["schnappschuss"]["nah_widerstand"]], "Verkauf nahe Widerstand"),
                  _gruppe([t for t in verkauf if not t["schnappschuss"]["nah_widerstand"]], "Übrige Verkäufe")],
        "kaeufe": [_gruppe([t for t in kauf if t["schnappschuss"]["abstand200"] > 0], "Kauf über der 200-Tage-Linie"),
                   _gruppe([t for t in kauf if t["schnappschuss"]["abstand200"] <= 0], "Kauf unter der 200-Tage-Linie")],
    }
    for k, g in gruppen.items():
        genug = all(x["n4"] >= K.TRADE_MIN for x in g)
        gruppen[k] = {"gruppen": g, "genug": genug, "mindest": K.TRADE_MIN}
    aus = {"trades": list(reversed(liste)), "auswertung": gruppen, "ohne_daten": len(liste) - len(mit), "wochen": list(K.TRADE_WOCHEN)}
    nutzer_schreiben(b, schluessel, aus)
    return aus
