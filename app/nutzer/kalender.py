"""Wählt aus, für welche Werte dieser Nutzer Earnings-Termine sehen möchte."""
from datetime import date, timedelta

import yaml

from ..markt.earnings import TAGE_VORAUS, TAGE_ZURUECK, frische_ergebnisse, meldungen_zum_termin, termine_fuer
from ..markt.kurse import ticker_fuer
from ..benutzer import Benutzer
from .portfolio import berechne
from .tr_parser import lade_trades


def _verbundene(b: Benutzer) -> dict:
    datei = b.datei("verbundene_werte.yaml")
    if not datei.exists():
        return {}
    return yaml.safe_load(datei.read_text(encoding="utf-8")) or {}


def _werte(b: Benutzer) -> tuple[dict, dict, dict, list[str]]:
    """Gibt (eigene Werte, alle Werte, Bezug fremder Werte, Meldungen) zurück."""
    meldungen: list[str] = []

    eigene = {}  # Ticker -> Name
    for p in berechne(lade_trades(b))["positionen"]:
        if p["art"] == "STOCK":
            ticker = ticker_fuer(p["isin"], p["art"])
            if ticker:
                eigene[ticker] = p["name"]
            else:
                meldungen.append(f"{p['name']}: kein Ticker gefunden, daher kein Termin.")

    # Fremde Werte nur, wenn der zugehörige eigene Wert noch im Depot liegt.
    namen, bezug = dict(eigene), {}
    for eigen, fremde in _verbundene(b).items():
        if eigen in eigene:
            for ticker, name in (fremde or {}).items():
                namen.setdefault(ticker, name)
                bezug.setdefault(ticker, []).append(eigene[eigen])
    return eigene, namen, bezug, meldungen


def frische_zahlen(b: Benutzer, stunden: int = 48) -> dict:
    """Gerade gemeldete Quartalszahlen, eigene Werte zuerst, dann die neuesten."""
    eigene, namen, bezug, _ = _werte(b)
    zahlen = frische_ergebnisse(namen, stunden)
    for z in zahlen:
        z.update(eigen=z["ticker"] in eigene, bezug=bezug.get(z["ticker"], []))
    zahlen.sort(key=lambda z: z["zeit"], reverse=True)
    zahlen.sort(key=lambda z: not z["eigen"])
    return {"zahlen": zahlen}


def kalender(b: Benutzer) -> dict:
    eigene, namen, bezug, meldungen = _werte(b)
    termine, markt_meldungen = termine_fuer(namen)
    meldungen += markt_meldungen

    # Gemeldete Zahlen an den Termin hängen. Yahoo entfernt vergangene Termine schnell,
    # deshalb bekommen übrig gebliebene Zahlen einen eigenen Eintrag an ihrem Tag.
    gemeldet = {(z["ticker"], z["zeit"][:10]): z for z in frische_ergebnisse(namen, TAGE_ZURUECK * 24)}
    for t in termine:
        z = gemeldet.pop((t["ticker"], t["datum"]), None)
        if z:
            t.update(ergebnis=z, sitzung=t.get("sitzung") or z.get("sitzung"))
        elif t["datum"] == date.today().isoformat():
            t["meldungen"] = meldungen_zum_termin(t)
    for (ticker, tag), z in gemeldet.items():
        termine.append({"ticker": ticker, "name": z["name"], "datum": tag, "sitzung": z.get("sitzung"), "ergebnis": z})

    # Selbst eingetragene IPO-Termine (Quiet Period, Lock-up, erste Zahlen) aus der Chartanalyse
    try:
        from .chartanalyse import ipo_termine
        termine += ipo_termine(b)
    except Exception:
        pass
    for t in termine:
        t.update(eigen=t["ticker"] in eigene or bool(t.get("ipo")), bezug=bezug.get(t["ticker"], []))
    termine.sort(key=lambda t: (t["datum"], not t["eigen"], t.get("zeit_de") or "99"))
    heute = date.today()
    return {"termine": termine, "meldungen": meldungen, "von": heute.isoformat(), "bis": (heute + timedelta(days=TAGE_VORAUS)).isoformat()}
