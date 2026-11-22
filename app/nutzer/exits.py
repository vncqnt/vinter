"""Exit-Alarme: Wie weit ist jeder Wert von den Exit-Leveln seiner These entfernt?

Für jeden Ticker mit Exit-Plan: Kursverlauf der letzten 6 Monate in Euro, alle Level mit Abstand
und Fortschritt (vom Einstandskurs aus gerechnet) und ein Trailing-Stop-Vorschlag aus der ATR.
Der Verlauf wird mit dem heutigen Wechselkurs in Euro umgerechnet; für das Bild reicht das.
"""
from ..benutzer import Benutzer
from ..markt.historie import CHANDELIER_FAKTOR, CHANDELIER_TAGE, hole_tageskurse
from ..markt.kurse import kurs_eur
from .thesen import alle_thesen

# art: "oben" = wird beim Steigen erreicht, "unten" = beim Fallen
LEVEL = [
    ("teilverkauf", "Teilverkauf", "teilverkauf_kurs", "oben"),
    ("komplett", "Komplett verkaufen", "komplett_kurs", "oben"),
    ("nachkauf", "Nachkaufen unter", "nachkauf_unter", "unten"),
    ("stop", "Stop", "stop", "unten"),
]


def _level(kurs: float, basis: float | None, plan: dict) -> list[dict]:
    ergebnis = []
    for art, name, feld, richtung in LEVEL:
        wert = plan.get(feld)
        if wert is None:
            continue
        erreicht = kurs >= wert if richtung == "oben" else kurs <= wert
        # Fortschritt: 0 = am Einstand, 1 = Level erreicht. Ohne Einstand (nicht im Depot) gibt es keinen.
        fortschritt = None
        if basis and abs(wert - basis) > 1e-9:
            fortschritt = max(0.0, min(1.0, (kurs - basis) / (wert - basis)))
        if art == "teilverkauf" and plan.get("teilverkauf_anteil"):
            name = f"Teilverkauf ({plan['teilverkauf_anteil']:g} %)"
        ergebnis.append({
            "art": art, "name": name, "richtung": richtung, "kurs": wert,
            "abstand": (wert / kurs - 1) * 100, "erreicht": erreicht, "fortschritt": fortschritt,
        })
    return ergebnis


def _stop_vorschlag(historie: dict, kurs: float) -> dict:
    """Trailing-Stop-Vorschlag in Euro, immer unter dem aktuellen Kurs.

    Normal: höchster Kurs der letzten Wochen minus 3 ATR. Liegt das schon über dem heutigen Kurs
    (nach einem starken Rückgang), wäre dieser Stop bereits ausgelöst. Dann gilt ein neuer Stop
    vom heutigen Kurs aus, ebenfalls 3 ATR darunter.
    """
    faktor = kurs / historie["schluss"]
    atr = historie["atr"] * faktor
    vom_hoch = historie["trailing"] * faktor
    unterschritten = vom_hoch >= kurs
    stop = kurs - CHANDELIER_FAKTOR * atr if unterschritten else vom_hoch
    return {"atr": atr, "atr_prozent": atr / kurs * 100, "trailing": stop,
            "trailing_abstand": (stop / kurs - 1) * 100, "hoch_stop_unterschritten": unterschritten}


def exit_alarme(b: Benutzer, positionen: list[dict]) -> dict:
    daten = alle_thesen(b)
    nach_ticker = {p["ticker"]: p for p in positionen if p.get("ticker")}
    werte, mit_plan = [], set()
    for these in daten["thesen"]:
        for ticker in these["ticker"]:
            plan = these["exits"].get(ticker) or {}
            if all(plan.get(feld) is None for _, _, feld, _ in LEVEL):
                continue
            mit_plan.add(ticker)
            pos = nach_ticker.get(ticker)
            kurs_info = {"preis_eur": pos["kurs_eur"], "aktualitaet": pos.get("kurs_aktualitaet")} if pos and pos.get("kurs_eur") else kurs_eur(ticker)
            kurs = kurs_info.get("preis_eur")
            if kurs is None:
                werte.append({"ticker": ticker, "these": these["name"], "fehler": "Gerade kein Kurs verfügbar."})
                continue
            einstand = pos["einstand_gesamt"] / pos["stueck"] if pos and pos.get("stueck") else None
            historie = hole_tageskurse(ticker)
            faktor = kurs / historie["schluss"] if historie and historie["schluss"] else None
            eintrag = {
                "ticker": ticker,
                "name": pos["name"] if pos else these["name"],
                "these": these["name"],
                "gehalten": pos is not None,
                "kurs": kurs,
                "aktualitaet": kurs_info.get("aktualitaet"),
                "einstand": einstand,
                "level": _level(kurs, einstand, plan),
                "verlauf": [{"d": t["d"], "c": round(t["c"] * faktor, 4)} for t in historie["tage"]] if faktor else [],
            }
            if faktor:
                eintrag.update(_stop_vorschlag(historie, kurs))
            werte.append(eintrag)
    # Positionen ohne Exit-Plan: wenigstens ein Stop-Vorschlag aus der ATR.
    ohne = []
    for p in positionen:
        if not p.get("ticker") or p["ticker"] in mit_plan:
            continue
        eintrag = {"ticker": p["ticker"], "name": p["name"],
                   "einstand": p["einstand_gesamt"] / p["stueck"] if p.get("stueck") else None}
        historie = hole_tageskurse(p["ticker"]) if p.get("kurs_eur") else None
        if historie and historie["schluss"]:
            eintrag.update(kurs=p["kurs_eur"], **_stop_vorschlag(historie, p["kurs_eur"]))
        ohne.append(eintrag)
    return {
        "werte": werte,
        "ohne_plan": ohne,
        "trailing_regel": (f"höchster Kurs der letzten {CHANDELIER_TAGE} Handelstage minus {CHANDELIER_FAKTOR} × ATR; "
                           f"liegt das schon über dem Kurs, heutiger Kurs minus {CHANDELIER_FAKTOR} × ATR"),
        "meldungen": daten["meldungen"],
    }
