"""Phase 5: Weltkarte, persönliche Ebenen.

- Geopolitik: Brennpunkte in karte/brennpunkte.yaml im Ordner des Benutzers (im Tool bearbeitbar).
  Warnsignal, wenn in den letzten 24 Stunden relevante News erschienen sind; betroffen sind eigene
  Positionen, die beim Brennpunkt stehen oder zu einem seiner Themen gehören.
  Zu jedem Brennpunkt die Marktwerte, die dort reagieren (Öl, Reedereien, Indizes …), und der Verlauf der Meldungen.
- Währungen: Kurse gegen den Euro und was ihre Bewegung für das eigene Depot bedeutet (Anteil je Währung).
"""
import hashlib
import json
import re

import yaml

from ..benutzer import Benutzer
from ..cache import nutzer_lesen, nutzer_schreiben
from ..markt import karte, themen

STANDARD_SCHWELLE = 0.45
STANDARD_PUNKTE = [
    {"name": "Straße von Hormus", "lat": 26.6, "lon": 56.3,
     "beschreibung": "Spannungen mit dem Iran, Blockade der Straße von Hormus, Angriffe auf Öltanker",
     "suchbegriffe": ["Strait of Hormuz", "Straße von Hormus", "Iran tanker"], "positionen": [], "themen": ["Öl & Gas"],
     "markt": ["BZ=F", "FRO", "GC=F"]},
    {"name": "Taiwan-Straße", "lat": 24.0, "lon": 119.6,
     "beschreibung": "Militärische Spannungen zwischen China und Taiwan, Manöver, Blockade, Chip-Lieferketten",
     "suchbegriffe": ["Taiwan Strait", "Taiwan China military", "Taiwan-Straße"], "positionen": ["MU", "NVDA"], "themen": ["Halbleiter"],
     "markt": ["^TWII", "TSM", "SOXX"]},
    {"name": "Rotes Meer", "lat": 15.5, "lon": 41.5,
     "beschreibung": "Angriffe der Huthi auf Handelsschiffe im Roten Meer, Umleitung der Containerschifffahrt",
     "suchbegriffe": ["Red Sea shipping attack", "Houthi ships", "Rotes Meer Schiffe"], "positionen": [], "themen": [],
     "markt": ["BZ=F", "ZIM", "MAERSK-B.CO"]},
    {"name": "Ukraine", "lat": 48.9, "lon": 33.0,
     "beschreibung": "Krieg zwischen Russland und der Ukraine, Frontverlauf, Waffenstillstand, Waffenlieferungen",
     "suchbegriffe": ["Ukraine Russia war", "Ukraine Krieg", "Ukraine ceasefire"], "positionen": ["RHM.DE", "LMT"], "themen": ["Rüstung"],
     "markt": ["TTF=F", "ZW=F", "RHM.DE"]},
    {"name": "Südchinesisches Meer", "lat": 12.0, "lon": 114.0,
     "beschreibung": "Konflikte im Südchinesischen Meer, China und die Philippinen, Zwischenfälle mit Schiffen",
     "suchbegriffe": ["South China Sea", "Südchinesisches Meer"], "positionen": [], "themen": [], "markt": ["^HSI", "PSEI.PS"]},
    {"name": "Israel und Nachbarn", "lat": 31.8, "lon": 35.1,
     "beschreibung": "Krieg in Gaza, Libanon und Hisbollah, Angriffe zwischen Israel und dem Iran",
     "suchbegriffe": ["Israel Gaza", "Israel Hezbollah Lebanon", "Israel Iran strike"], "positionen": [], "themen": [],
     "markt": ["TA35.TA", "BZ=F", "GC=F"]},
    {"name": "Koreanische Halbinsel", "lat": 38.3, "lon": 127.6,
     "beschreibung": "Raketentests Nordkoreas, Spannungen zwischen Nord- und Südkorea",
     "suchbegriffe": ["North Korea missile", "Nordkorea Rakete", "Korean peninsula tension"], "positionen": [], "themen": [],
     "markt": ["^KS11", "KRW=X"]},
    {"name": "Indien und Pakistan", "lat": 33.6, "lon": 74.6,
     "beschreibung": "Konflikt um Kaschmir, Anschläge, militärische Zusammenstöße zwischen Indien und Pakistan",
     "suchbegriffe": ["India Pakistan Kashmir", "Indien Pakistan Konflikt"], "positionen": [], "themen": [],
     "markt": ["^BSESN", "INR=X"]},
    {"name": "Panamakanal", "lat": 9.1, "lon": -79.7,
     "beschreibung": "Störungen am Panamakanal durch Dürre, Politik oder Streit um die Kontrolle, Staus der Schifffahrt",
     "suchbegriffe": ["Panama Canal", "Panamakanal"], "positionen": [], "themen": [], "markt": ["ZIM", "BDRY"]},
]


class KarteFehler(ValueError):
    pass


# ---------- Brennpunkte ----------

def _bp_datei(b: Benutzer):
    return b.datei("karte", "brennpunkte.yaml")


def _liste(wert) -> list[str]:
    if isinstance(wert, str):
        wert = wert.split(",")
    return [str(x).strip() for x in wert or [] if str(x).strip()][:20]


def _pruefe_punkt(p: dict) -> dict:
    name = str(p.get("name") or "").strip()[:60]
    try:
        lat, lon = float(p.get("lat")), float(p.get("lon"))
    except (TypeError, ValueError):
        raise KarteFehler(f"{name or 'Brennpunkt'}: Koordinaten fehlen.")
    if not name or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise KarteFehler("Name und gültige Koordinaten sind nötig.")
    begriffe = _liste(p.get("suchbegriffe"))
    if not begriffe:
        raise KarteFehler(f"{name}: Mindestens ein Suchbegriff für die News ist nötig.")
    return {"name": name, "lat": round(lat, 3), "lon": round(lon, 3), "beschreibung": str(p.get("beschreibung") or "").strip()[:300],
            "suchbegriffe": begriffe, "positionen": [t.upper() for t in _liste(p.get("positionen"))], "themen": _liste(p.get("themen")),
            "markt": [t.upper() for t in _liste(p.get("markt"))][:6]}


def brennpunkte_lesen(b: Benutzer) -> dict:
    datei = _bp_datei(b)
    if not datei.exists():
        return {"schwelle": STANDARD_SCHWELLE, "punkte": STANDARD_PUNKTE}
    try:
        roh = yaml.safe_load(datei.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        raise KarteFehler("brennpunkte.yaml ist nicht lesbar.")
    punkte = []
    for p in roh.get("punkte") or []:
        try:
            punkte.append(_pruefe_punkt(p))
        except KarteFehler:
            continue
    return {"schwelle": float(roh.get("schwelle") or STANDARD_SCHWELLE), "punkte": punkte}


def brennpunkte_speichern(b: Benutzer, daten: dict) -> dict:
    punkte = [_pruefe_punkt(p) for p in (daten.get("punkte") or [])[:40]]
    schwelle = float(daten.get("schwelle") or STANDARD_SCHWELLE)
    if not 0.2 <= schwelle <= 0.8:
        raise KarteFehler("Die Schwelle liegt zwischen 0,2 und 0,8.")
    datei = _bp_datei(b)
    datei.parent.mkdir(parents=True, exist_ok=True)
    kopf = ("# Brennpunkte für die Weltkarte (Ebene Geopolitik). Im Tool bearbeitbar.\n"
            "# schwelle: ab welcher Ähnlichkeit (0,2 bis 0,8) eine Schlagzeile als relevant gilt. Höher = weniger, aber sicherere Warnungen.\n")
    datei.write_text(kopf + yaml.safe_dump({"schwelle": schwelle, "punkte": punkte}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return brennpunkte_lesen(b)


def _themen_werte() -> dict[str, set]:
    try:
        return {t["name"]: set(t["werte"]) for t in themen.lade_themen()}
    except Exception:
        return {}


def geopolitik(b: Benutzer, positionen: list[dict]) -> dict:
    daten = brennpunkte_lesen(b)
    schwelle = daten["schwelle"]
    depot = {p["ticker"]: p["name"] for p in positionen if p.get("ticker")}
    themen_werte = _themen_werte()
    aus = []
    for p in daten["punkte"]:
        inhalt = json.dumps({**p, "schwelle": schwelle}, sort_keys=True, ensure_ascii=False)
        schluessel = "karte-bp2:" + hashlib.sha1(inhalt.encode("utf-8")).hexdigest()
        news, alter = nutzer_lesen(b, schluessel)
        if news is None or alter >= 30 * 60:
            try:
                news = karte.brennpunkt_news(p, schwelle)
                if not news.get("fehler"):
                    nutzer_schreiben(b, schluessel, news)
            except Exception as fehler:
                news = news or {"meldungen": [], "anzahl24": 0, "stufe": 0, "fehler": 1, "text": type(fehler).__name__}
        betroffen = {t for t in p["positionen"] if t in depot}
        for thema in p["themen"]:
            betroffen |= {t for t in themen_werte.get(thema, set()) if t in depot}
        aus.append({**p, **news, "betroffen": sorted(depot[t] for t in betroffen)})
    try:
        reaktion = karte.markt_reaktion([t for p in aus for t in p.get("markt") or []])
    except Exception:
        reaktion = {}
    for p in aus:
        p["reaktion"] = [{"ticker": t, **reaktion[t]} for t in p.get("markt") or [] if t in reaktion]
    # Punkt 31b: Meerengen in der Nähe, Durchfahrten der letzten 4 Wochen gegen den Normalwert (IMF PortWatch)
    try:
        from ..markt.handel.auswertung import engstellen
        enge = [e for e in engstellen().values() if e.get("lat") is not None and e.get("alle")]
    except Exception:
        enge = []
    for p in aus:
        nah = sorted(((_km(p["lat"], p["lon"], e["lat"], e["lon"]), e) for e in enge), key=lambda x: x[0])
        p["engstellen"] = [{"name": e["name"], "stand": e["stand"], "km": round(d), "jetzt": e["alle"]["jetzt"], "normal": e["alle"]["normal"],
                            "veraenderung": e["alle"]["veraenderung"], "tanker": (e.get("tanker") or {}).get("veraenderung")}
                           for d, e in nah if d <= 1200][:3]
    return {"schwelle": schwelle, "punkte": aus, "themen": sorted(themen_werte)}


def _km(lat1, lon1, lat2, lon2) -> float:
    from math import asin, cos, radians, sin, sqrt
    a = sin(radians(lat2 - lat1) / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(radians(lon2 - lon1) / 2) ** 2
    return 12742 * asin(sqrt(a))


# ---------- Währungen ----------

def waehrungen(b: Benutzer, positionen: list[dict]) -> dict:
    """Kurse gegen den Euro, dazu dein Depot je Währung und was die Bewegung für das Depot bedeutet."""
    from .verteilung import verteilung
    daten = karte.waehrungen()
    anteile = {}
    try:
        v = verteilung(b, positionen)
        teile = next((d["teile"] for d in v.get("dimensionen") or [] if d["titel"] == "Währung"), [])
        anteile = {t["name"]: t["anteil"] for t in teile}
    except Exception:
        pass
    kurse = daten["waehrungen"]
    effekt = {}
    for k in ("tag", "woche", "monat", "ytd", "jahr"):
        effekt[k] = round(sum(a / 100 * (kurse[c][k] or 0) for c, a in anteile.items() if c in kurse and kurse[c].get(k) is not None), 3)
    return {**daten, "depot": anteile, "effekt": effekt}
