"""Die Watchlist dieses Nutzers: Werte beobachten, ohne sie zu besitzen.

Gespeichert in watchlist.yaml im Ordner des Benutzers. Die Kurse kommen über denselben Weg wie die
Depotwerte, es gelten also dieselben Abruftakte je Börse.
"""
import re
from concurrent.futures import ThreadPoolExecutor

import yaml

from ..markt.auffaellig import auswerten
from ..markt.kurse import kurs_eur, vorladen
from ..benutzer import Benutzer

MAX_WERTE = 30  # Finnhub erlaubt 60 Abrufe pro Minute, das Depot braucht davon auch welche
TICKER_FORM = re.compile(r"^[A-Z0-9.^=\-]{1,15}$")


class WatchlistFehler(Exception):
    """Die Eingabe passt nicht, der Text wird dem Nutzer angezeigt."""


def _datei(b: Benutzer):
    return b.datei("watchlist.yaml")


def _lesen(b: Benutzer) -> list[dict]:
    datei = _datei(b)
    if not datei.exists():
        return []
    return yaml.safe_load(datei.read_text(encoding="utf-8")) or []


def _schreiben(b: Benutzer, eintraege: list[dict]) -> None:
    datei = _datei(b)
    datei.parent.mkdir(parents=True, exist_ok=True)
    zwischen = datei.with_suffix(".tmp")  # erst fertig schreiben, dann ersetzen
    zwischen.write_text(yaml.safe_dump(eintraege, allow_unicode=True, sort_keys=False), encoding="utf-8")
    zwischen.replace(datei)


def _zeile(eintrag: dict) -> dict:
    ticker = eintrag["ticker"]
    kurs = kurs_eur(ticker)
    zeile = {
        **eintrag, "kurs_eur": kurs["preis_eur"], "kurs_stand": kurs["stand"], "kurs_status": kurs["status"],
        "kurs_aktualitaet": kurs.get("aktualitaet"), "kurs_aktualisiert": kurs.get("aktualisiert"),
        "tag": None, "vielfaches": None, "aussergewoehnlich": False,
    }
    if kurs["preis_eur"] is not None:
        zeile.update(auswerten(ticker, kurs["preis"], kurs["stand"], kurs["status"] == "veraltet"))
    return zeile


def werte(b: Benutzer) -> dict:
    eintraege = _lesen(b)
    vorladen([e["ticker"] for e in eintraege])
    with ThreadPoolExecutor(max_workers=4) as pool:
        zeilen = list(pool.map(_zeile, eintraege))
    zeiten = [z["kurs_aktualisiert"] for z in zeilen if z["kurs_aktualisiert"]]
    return {"werte": zeilen, "aktualisiert": max(zeiten, default=None)}


def hinzufuegen(b: Benutzer, daten: dict) -> dict:
    ticker = str(daten.get("ticker", "")).strip().upper()
    name = " ".join(str(daten.get("name") or ticker).split())[:80]
    if not TICKER_FORM.match(ticker):
        raise WatchlistFehler("Dieses Kürzel ist ungültig.")
    eintraege = _lesen(b)
    if any(e["ticker"] == ticker for e in eintraege):
        raise WatchlistFehler(f"{name} steht schon auf deiner Watchlist.")
    if len(eintraege) >= MAX_WERTE:
        raise WatchlistFehler(f"Mehr als {MAX_WERTE} Werte gehen nicht, sonst reichen die Gratis-Abrufe nicht aus.")
    if kurs_eur(ticker)["preis_eur"] is None:
        raise WatchlistFehler(f"Für {name} gibt es gerade keinen Kurs.")
    _schreiben(b, [*eintraege, {"ticker": ticker, "name": name}])
    return {"ok": True}


def entfernen(b: Benutzer, ticker: str) -> dict:
    eintraege = _lesen(b)
    _schreiben(b, [e for e in eintraege if e["ticker"] != ticker])
    return {"ok": True}
