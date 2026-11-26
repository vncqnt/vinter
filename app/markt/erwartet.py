"""Erwartete Bewegung eines Werts laut Optionsmarkt: bis zum nächsten Verfall (je Handelstag), in rund 30 Tagen
und bis kurz nach den nächsten Quartalszahlen, jeweils gegen die tatsächliche Bewegung früher.

Grundlage ist der Preis des Am-Geld-Straddles (Call plus Put am Strike nächst dem Kurs) in Prozent vom Kurs.
Er entspricht ungefähr dem erwarteten Betrag der Bewegung bis zum Verfall (Richtung offen). Verglichen wird
deshalb mit dem durchschnittlichen Betrag der echten Bewegungen gleicher Länge (letzte 3 Jahre).
Nur US-Werte mit Optionen bei Yahoo; Futures und Indizes über ihren ETF (app/markt/bezug.py). 30 Minuten im Cache.
"""
import math
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

from .abruf import gecacht, schlusskurse

HALBE_STUNDE = 1800
KURVE_VERFAELLE = 8
ZAHLEN_AB_TAGEN = 14  # wie bei den Earnings-Moves: weiter vorher steckt im Straddle vor allem normale Schwankung


def straddle(kette, kurs: float) -> tuple[float, bool] | None:
    """(Preis des Am-Geld-Straddles, ob aus den letzten Handelskursen). Mitte aus Geld und Brief; fehlen die
    (Wochenende, Feiertag), die letzten gehandelten Kurse. Reine Funktion."""
    paare = kette.calls.merge(kette.puts, on="strike", suffixes=("_c", "_p"))
    if paare.empty:
        return None
    z = paare.iloc[(paare["strike"] - kurs).abs().argsort().iloc[0]]

    def preis(s):
        bid, ask, last = z.get(f"bid_{s}"), z.get(f"ask_{s}"), z.get(f"lastPrice_{s}")
        if bid and ask and bid > 0 and ask >= bid:
            return (bid + ask) / 2, False
        return (float(last), True) if last and last > 0 else (None, True)
    (c, lc), (p, lp) = preis("c"), preis("p")
    return (float(c + p), lc or lp) if c and p else None


def handelstage(von: date, bis: date) -> int:
    return max(1, int(np.busday_count(von, bis)))


def je_tag(prozent: float, tage: int) -> float:
    """Bewegung bis zum Verfall auf einen Handelstag umgelegt (Varianz wächst mit der Zeit). Reine Funktion."""
    return prozent / math.sqrt(max(1, tage))


def historisch(schluss: pd.Series, tage: int, jahre: int = 3) -> dict | None:
    """Durchschnittlicher Betrag der Kursbewegung über `tage` Handelstage (überlappend) und wie oft er größer
    war als ein Schwellwert (später im Widget). Reine Funktion."""
    s = schluss.dropna().iloc[-(252 * jahre + tage):]
    if len(s) < tage + 60:
        return None
    r = (s / s.shift(tage) - 1).dropna().abs() * 100
    return {"mittel": round(float(r.mean()), 2), "median": round(float(r.median()), 2), "n": int(len(r)), "werte": r.to_numpy()}


def anteil_groesser(werte: np.ndarray, schwelle: float) -> float:
    return round(float((werte > schwelle).mean() * 100), 1)


def rechnen(ticker: str) -> dict:
    ticker = ticker.upper()

    def holen():
        aktie = yf.Ticker(ticker)
        kurs = float(aktie.fast_info.get("lastPrice") or 0)
        if not kurs:
            return {"fehler": "Kein aktueller Kurs bei Yahoo."}
        heute = date.today()
        verfaelle = [date.fromisoformat(v) for v in (aktie.options or ())]
        verfaelle = [v for v in verfaelle if v > heute]
        if not verfaelle:
            return {"fehler": f"Für {ticker} gibt es bei Yahoo keine Optionen."}
        schluss = schlusskurse(ticker, "5y")
        kurve, aus_letzten = [], False
        ziel30 = min(verfaelle, key=lambda v: abs((v - heute).days - 30))
        auswahl = sorted(set(verfaelle[:KURVE_VERFAELLE]) | {ziel30})
        for v in auswahl:
            try:
                ergebnis = straddle(aktie.option_chain(v.isoformat()), kurs)
            except Exception:  # noqa: BLE001  (einzelne Verfälle liefert Yahoo manchmal leer)
                ergebnis = None
            if ergebnis:
                s, letzte = ergebnis
                aus_letzten = aus_letzten or letzte
                t = handelstage(heute, v)
                kurve.append({"verfall": v.isoformat(), "tage": t, "kalendertage": (v - heute).days,
                              "prozent": round(s / kurs * 100, 2), "je_tag": round(je_tag(s / kurs * 100, t), 2)})
        if not kurve:
            return {"fehler": "Yahoo liefert gerade keine brauchbaren Geld- und Briefkurse für die Optionen."}
        aus = {"ticker": ticker, "kurs": kurs, "kurve": kurve, "aus_letzten": aus_letzten, "stand": datetime.now(timezone.utc).isoformat()}
        erster = kurve[0]
        h1 = historisch(schluss, 1)
        aus["morgen"] = {"prozent": erster["je_tag"], "verfall": erster["verfall"], "tage": erster["tage"], "genau": erster["tage"] == 1,
                         "hist": h1 and {"mittel": h1["mittel"], "median": h1["median"], "n": h1["n"],
                                         "anteil_groesser": anteil_groesser(h1["werte"], erster["je_tag"])}}
        k30 = next((k for k in kurve if k["verfall"] == ziel30.isoformat()), None)
        if k30:
            h = historisch(schluss, k30["tage"])
            aus["monat"] = {**k30, "hist": h and {"mittel": h["mittel"], "median": h["median"], "n": h["n"],
                                                  "anteil_groesser": anteil_groesser(h["werte"], k30["prozent"])}}
        return aus

    try:
        aus = gecacht(f"erwartet:{ticker}", HALBE_STUNDE, holen)
    except Exception as f:  # noqa: BLE001
        return {"fehler": f"Die Optionsdaten sind gerade nicht abrufbar ({type(f).__name__})."}
    return aus


def mit_zahlen(ticker: str, aktie_ticker: str | None) -> dict:
    """Wie rechnen(), dazu die eingepreiste Bewegung um die nächsten Quartalszahlen (nur für Aktien)."""
    aus = dict(rechnen(ticker))
    if not aktie_ticker or aus.get("fehler"):
        return aus
    from .earnings_moves import chart_termine, erwartete_bewegung, reaktionen
    termine = chart_termine(aktie_ticker).get("termine", [])
    naechster = next((t for t in termine if t.get("zukunft")), None)
    if naechster:
        tage = (date.fromisoformat(naechster["datum"]) - date.today()).days
        e = erwartete_bewegung(aktie_ticker, naechster["datum"]) if 0 <= tage <= ZAHLEN_AB_TAGEN else None
        frueher = [abs(x["prozent"]) for x in reaktionen(aktie_ticker).get("reaktionen") or [] if x.get("prozent") is not None]
        aus["zahlen"] = {"datum": naechster["datum"], "sitzung": naechster.get("sitzung"), "tage": tage,
                         "prozent": e and e.get("prozent"), "bereinigt": bool(e and e.get("bereinigt")),
                         "hist_mittel": round(sum(frueher) / len(frueher), 2) if frueher else None, "hist_n": len(frueher)}
    return aus
