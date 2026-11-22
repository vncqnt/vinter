"""Wochenbericht für den Thesen-Check: eine Markdown-Datei, die der Nutzer sonntags Claude gibt.

Enthält pro These Kernthese, Signale, Kursentwicklung, Abstand zu den Exit-Leveln und die
gefilterten News der letzten 7 Tage, dazu Marktumfeld und Termine der kommenden Woche.
Es gibt keine Verbindung zu Claude: Die Datei wird nur heruntergeladen.
"""
from datetime import date, datetime, timedelta, timezone

from ..benutzer import Benutzer
from ..markt.kurse import kurs_eur
from ..markt.maerkte import cockpit
from ..markt.sektoren import hole_veraenderung
from .kalender import kalender
from .thesen import alle_thesen
from .thesen_news import thesen_news

TENDENZ_GRENZE = 15  # wie auf der Seite: ab ±15 gilt eine Meldung als eher dafür oder dagegen
WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _zahl(wert, stellen: int = 2) -> str:
    """Deutsche Schreibweise, z. B. 1.234,56."""
    return f"{wert:,.{stellen}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _prozent(wert) -> str:
    if wert is None:
        return "–"
    return f"{'+' if wert > 0 else '−' if wert < 0 else '±'}{_zahl(abs(wert))} %"


def _euro(wert) -> str:
    return "–" if wert is None else f"{'−' if wert < 0 else ''}{_zahl(abs(wert))} €"


def _gv(wert) -> str:
    """Gewinn oder Verlust immer mit Vorzeichen."""
    return "–" if wert is None else f"{'+' if wert > 0 else '−' if wert < 0 else '±'}{_zahl(abs(wert))} €"


def _datum(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{WOCHENTAGE[d.weekday()]} {d.day:02d}.{d.month:02d}."


def _tendenz_text(wert: int) -> str:
    if wert >= TENDENZ_GRENZE:
        return "eher Bestätigung"
    if wert <= -TENDENZ_GRENZE:
        return "eher Bruch"
    return "neutral"


def _abschnitt_markt() -> list[str]:
    try:
        gruppen = cockpit()["gruppen"]
    except Exception:
        return ["_Marktdaten gerade nicht verfügbar._"]
    zeilen = ["| Markt | Stand | Tag | Woche |", "|---|---:|---:|---:|"]
    for g in gruppen:
        for k in g["kacheln"]:
            if k.get("wert") is None:
                continue
            if k["art"] == "rendite":
                stand = f"{_zahl(k['wert'])} %"
                aend = lambda w: "–" if w is None else f"{'+' if w > 0 else '−' if w < 0 else '±'}{_zahl(abs(w), 0)} Bp"
            else:
                stand = _zahl(k["wert"], 4 if k["art"] == "fx" else 2)
                aend = _prozent
            zeilen.append(f"| {k['name']} | {stand} | {aend(k.get('tag'))} | {aend(k.get('woche'))} |")
    return zeilen


def _exit_zeilen(kurs: float | None, e: dict) -> list[str]:
    """Abstand vom aktuellen Kurs zu jedem gesetzten Exit-Level, positiv = Kurs muss steigen."""
    stufen = [
        ("Teilverkauf" + (f" ({_zahl(e['teilverkauf_anteil'], 0)} %)" if e.get("teilverkauf_anteil") else ""), e.get("teilverkauf_kurs"), "oben"),
        ("Komplett verkaufen", e.get("komplett_kurs"), "oben"),
        ("Nachkaufen unter", e.get("nachkauf_unter"), "unten"),
        ("Stop", e.get("stop"), "unten"),
    ]
    zeilen = []
    for name, level, richtung in stufen:
        if level is None:
            continue
        if kurs is None:
            zeilen.append(f"  - {name}: {_euro(level)} (kein aktueller Kurs)")
            continue
        abstand = (level / kurs - 1) * 100
        erreicht = kurs >= level if richtung == "oben" else kurs <= level
        zeilen.append(f"  - {name}: {_euro(level)} · {'**erreicht**' if erreicht else f'Abstand {_prozent(abstand)}'}")
    return zeilen or ["  - kein Exit-Plan eingetragen"]


def _abschnitt_these(these: dict, news: list, positionen: dict, schwelle: float) -> list[str]:
    z = [f"## {these['name']}", ""]
    z.append(f"**Ticker:** {', '.join(these['ticker'])}")
    z.append(f"**Kernthese:** {these['kernthese'] or '_nicht ausgefüllt_'}")
    for feld, titel in (("katalysatoren", "Katalysatoren"), ("bestaetigung", "Bestätigungs-Signale"),
                        ("bruch", "Bruch-Signale (wann liege ich falsch)"), ("news_themen", "News-Themen")):
        if these[feld]:
            z += ["", f"**{titel}:**", *[f"- {x}" for x in these[feld]]]

    z += ["", "### Kurse und Exit-Plan", ""]
    for t in these["ticker"]:
        pos = positionen.get(t)
        try:
            kurs = pos["kurs_eur"] if pos and pos.get("kurs_eur") is not None else kurs_eur(t)["preis_eur"]
        except Exception:
            kurs = None
        try:
            bewegung, _ = hole_veraenderung(t)
        except Exception:
            bewegung = None
        b = bewegung or {}
        z.append(f"- **{t}**: {_euro(kurs)} · Woche {_prozent(b.get('woche'))} · Monat {_prozent(b.get('monat'))} · seit Jahresbeginn {_prozent(b.get('ytd'))}")
        if pos:
            z.append(f"  - Im Depot: {_zahl(pos['stueck'], 4)} Stück, Einstand {_euro(pos['einstand_gesamt'])}, "
                     f"Wert {_euro(pos.get('wert_eur'))}, Gewinn/Verlust {_gv(pos.get('gv_eur'))} ({_prozent(pos.get('gv_prozent'))})")
        else:
            z.append("  - Nicht im Depot")
        z += _exit_zeilen(kurs, these["exits"].get(t) or {})

    grenze = datetime.now(timezone.utc) - timedelta(days=7)
    woche = [n for n in news if datetime.fromisoformat(n["zeit"]) >= grenze]
    warnungen = [n for n in woche if n.get("warnung")]
    passend = sorted((n for n in woche if not n.get("warnung") and n.get("relevanz", 0) >= schwelle),
                     key=lambda n: n["relevanz"], reverse=True)

    def news_zeile(n: dict) -> str:
        anzahl = n.get("weitere") or 0
        weitere = f", dazu {anzahl} ähnliche Meldung{'en' if anzahl > 1 else ''}" if anzahl else ""
        return (f"- {_datum(n['zeit'])} [{n['titel']}]({n['link']}) ({n.get('quelle', '')}) · "
                f"{_tendenz_text(n.get('tendenz', 0))} · passt zu: {n.get('passt_zu', '–')}{weitere}")

    z += ["", "### News der Woche", ""]
    if warnungen:
        z += ["**Warnungen (ähneln deinen Bruch-Signalen):**", *map(news_zeile, warnungen), ""]
    z += list(map(news_zeile, passend)) if passend else ["_Keine relevanten Meldungen über der eingestellten Schwelle._"]
    return z + [""]


def _abschnitt_termine(b: Benutzer, ticker: set) -> list[str]:
    try:
        termine = kalender(b)["termine"]
    except Exception:
        return ["_Termine gerade nicht verfügbar._"]
    heute = date.today()
    bis = (heute + timedelta(days=7)).isoformat()
    naechste = [t for t in termine if heute.isoformat() <= t["datum"] <= bis and (t["ticker"] in ticker or t.get("eigen"))]
    if not naechste:
        return ["_Keine Quartalszahlen deiner Werte in den nächsten 7 Tagen._"]
    return [f"- {_datum(t['datum'])} {t['name']} ({t['ticker']})" + (f", {t['zeit_de']}" if t.get("zeit_de") else "") for t in naechste]


def wochenbericht(b: Benutzer, positionen: list[dict], summen: dict | None, schwelle: float) -> str:
    daten = alle_thesen(b)
    thesen = daten["thesen"]
    try:
        news = {e["id"]: e["news"] for e in thesen_news(b)["thesen"]}
    except Exception:
        news = {}
    nach_ticker = {p["ticker"]: p for p in positionen if p.get("ticker")}
    heute = date.today()

    z = [f"# Vinter Wochenbericht – {heute.day:02d}.{heute.month:02d}.{heute.year}", ""]
    z += [
        "> **Auftrag für den Thesen-Check:** Prüfe bitte jede These einzeln. Stützen die News und die",
        "> Kursentwicklung der Woche die Kernthese, oder ist ein Bruch-Signal eingetreten? Wie nah sind",
        "> die Exit-Level? Was sollte ich nächste Woche beobachten? Sag mir ehrlich, wo ich falsch liegen",
        "> könnte. Die Tendenz-Angaben zu den News stammen aus einem einfachen Ähnlichkeitsfilter und",
        "> sind nur grob.",
        "",
    ]
    if summen:
        z += [f"**Depot:** Wert {_euro(summen.get('wert'))} · Gesamtergebnis {_gv(summen.get('gesamt'))} "
              f"(unrealisiert {_gv(summen.get('gv'))}, realisiert {_gv(summen.get('realisiert'))}, Dividenden {_gv(summen.get('dividenden'))})", ""]
    z += ["## Marktumfeld", "", *_abschnitt_markt(), ""]
    z += ["## Termine der kommenden Woche", "", *_abschnitt_termine(b, {t for th in thesen for t in th["ticker"]}), ""]
    if not thesen:
        z += ["_Noch keine Thesen angelegt._"]
    for th in thesen:
        z += _abschnitt_these(th, news.get(th["id"], []), nach_ticker, schwelle)
    for m in daten["meldungen"]:
        z.append(f"_Hinweis: {m}_")
    z += ["---", "_Erstellt von Vinter. Kurse von Yahoo Finance und Finnhub, teils verzögert. Keine Anlageberatung._", ""]
    return "\n".join(z)
