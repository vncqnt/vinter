"""Verteilung des Depots nach Thema, Sektor, Land und Währung."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import yaml

from ..markt.kurse import stammdaten
from ..markt.sektoren import hole_veraenderung
from ..benutzer import Benutzer

SEKTOR_DE = {
    "Technology": "Technologie",
    "Communication Services": "Kommunikation",
    "Consumer Cyclical": "Zyklischer Konsum",
    "Consumer Defensive": "Basiskonsum",
    "Energy": "Energie",
    "Financial Services": "Finanzen",
    "Healthcare": "Gesundheit",
    "Industrials": "Industrie",
    "Basic Materials": "Grundstoffe",
    "Real Estate": "Immobilien",
    "Utilities": "Versorger",
}
LAND_DE = {"United States": "USA", "Germany": "Deutschland", "Sweden": "Schweden"}


def einstellungen(b: Benutzer) -> tuple[dict, list, dict, list[str]]:
    datei = b.datei("verteilung.yaml")
    if not datei.exists():
        return {}, [], {}, ["Die Datei verteilung.yaml in deinem Datenordner fehlt. Themen werden nicht angezeigt."]
    try:
        roh = yaml.safe_load(datei.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}, [], {}, ["In deiner verteilung.yaml ist ein Tippfehler. Themen werden nicht angezeigt."]
    return roh.get("werte") or {}, roh.get("ohne_warnung") or [], roh.get("gruppen") or {}, []


def verteilung(b: Benutzer, positionen: list[dict]) -> dict:
    eigene, ohne_warnung, themen_gruppen, meldungen = einstellungen(b)
    bewertet = [p for p in positionen if p["wert_eur"] is not None]
    fehlend = [p["name"] for p in positionen if p["wert_eur"] is None]
    if fehlend:
        meldungen.append(f"Ohne aktuellen Kurs und daher nicht enthalten: {', '.join(fehlend)}.")

    with ThreadPoolExecutor(max_workers=4) as pool:
        infos = list(pool.map(stammdaten, (p["ticker"] for p in bewertet)))
        bewegungen = list(pool.map(hole_veraenderung, (p["ticker"] for p in bewertet)))

    summe = sum(p["wert_eur"] for p in bewertet)
    sektor_positionen = defaultdict(list)  # Sektor- oder Branchenname -> eigene Werte

    gruppen = {"Thema": defaultdict(float), "Sektor": defaultdict(float), "Land": defaultdict(float), "Währung": defaultdict(float)}
    for p, info, (bewegung, _) in zip(bewertet, infos, bewegungen):
        e = eigene.get(p["isin"]) or {}
        sektor = e.get("sektor") or SEKTOR_DE.get(info.get("sector"), info.get("sector"))
        land = e.get("land") or LAND_DE.get(info.get("country"), info.get("country"))
        wert = p["wert_eur"]
        gruppen["Thema"][e.get("thema") or "Ohne Thema"] += wert
        gruppen["Sektor"][sektor or "Unbekannt"] += wert
        gruppen["Land"][land or "Unbekannt"] += wert
        gruppen["Währung"][e.get("waehrung") or info.get("currency") or "Unbekannt"] += wert

        eintrag = {"name": p["name"], "ticker": p["ticker"], "anteil": wert / summe * 100, **(bewegung or {})}
        for ziel in {sektor, e.get("branche")} - {None}:
            sektor_positionen[ziel].append(eintrag)

    dimensionen = [
        {
            "titel": titel,
            "teile": [
                {"name": name, "wert": w, "anteil": w / summe * 100}
                for name, w in sorted(werte.items(), key=lambda x: -x[1])
            ],
        }
        for titel, werte in gruppen.items()
    ] if summe else []
    for liste in sektor_positionen.values():
        liste.sort(key=lambda x: -x["anteil"])
    return {
        "summe": summe,
        "dimensionen": dimensionen,
        "ohne_warnung": ohne_warnung,
        "themen_gruppen": themen_gruppen,
        "sektor_positionen": sektor_positionen,
        "meldungen": meldungen,
    }
