"""Weltkarte, Ebene "Lage": was gerade an Engstellen, beim Wetter und in der Erde passiert, und was davon Märkte trifft.

- engstellen(): Durchfahrten an 28 Meerengen und Kanälen von IMF PortWatch (aus AIS-Schiffsdaten, täglich, ein
  paar Tage Verzug, frei). Vergleich der letzten 7 Tage mit dem Schnitt des Vorjahres.
- ereignisse(): Wirbelstürme (mit Zugbahn und Vorhersagekegel), Überschwemmungen, Vulkane, Dürren und Waldbrände
  von GDACS (Katastrophen-Warnsystem von EU und UN), Erdbeben ab Stärke 4,5 von USGS.
- infrastruktur(): Pipelines, LNG-Terminals, Ölhäfen, Raffinerien, Chipfabriken und Minen aus
  daten/markt/karte/infrastruktur.yaml (Auswahl mit Marktbezug).
- hinweise(): Ereignisse in der Nähe solcher Anlagen oder ungewöhnlich wenig Verkehr an einer Engstelle.
- Schiffe live (nur mit AISSTREAM_API_KEY in der .env): Positionen an den wichtigsten Engstellen über
  aisstream.io. Die Verbindung läuft nur, solange jemand die Ebene ansieht (danach 10 Minuten), um den Rechner zu schonen.
"""
import json
import math
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests
import yaml

from ..einstellungen import key
from ..konfig import MARKT_DATEN
from .abruf import gecacht

INFRA_DATEI = MARKT_DATEN / "karte" / "infrastruktur.yaml"
PORTWATCH = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query"
GDACS = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
USGS = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson"
KOPF = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Vinter/1.0"}

# PortWatch-Kennung: (Name, Länge, Breite, warum es zählt)
ENGSTELLEN = {
    "chokepoint6": ("Straße von Hormus", 56.4, 26.6, "Rund ein Fünftel des weltweiten Öls und viel LNG aus Katar fahren hier durch."),
    "chokepoint1": ("Suezkanal", 32.33, 30.6, "Kürzester Weg zwischen Asien und Europa, gut 10 % des Welthandels."),
    "chokepoint4": ("Bab al-Mandab", 43.4, 12.6, "Zugang zum Roten Meer und damit zum Suezkanal, Ziel der Huthi-Angriffe."),
    "chokepoint5": ("Straße von Malakka", 101.0, 2.8, "Wichtigste Route von Nahost-Öl nach China, Japan und Korea."),
    "chokepoint2": ("Panamakanal", -79.7, 9.1, "Verbindet Atlantik und Pazifik, wichtig für US-LNG und Getreide nach Asien; leidet unter Dürre."),
    "chokepoint11": ("Taiwan-Straße", 119.5, 24.2, "Hauptroute zwischen Nord- und Südostasien, mitten im Taiwan-Konflikt."),
    "chokepoint3": ("Bosporus", 29.05, 41.1, "Einziger Weg vom Schwarzen Meer (Getreide, russisches und kasachisches Öl) ins Mittelmeer."),
    "chokepoint7": ("Kap der Guten Hoffnung", 18.5, -34.4, "Ausweichroute, wenn das Rote Meer gemieden wird; mehr Verkehr hier heißt längere Wege."),
    "chokepoint8": ("Straße von Gibraltar", -5.6, 35.95, "Tor zwischen Mittelmeer und Atlantik."),
    "chokepoint9": ("Straße von Dover", 1.5, 51.0, "Eine der meistbefahrenen Schifffahrtsstraßen der Welt."),
    "chokepoint10": ("Öresund", 12.7, 55.9, "Zugang zur Ostsee, Route der russischen Schattenflotte."),
    "chokepoint28": ("Straße von Kertsch", 36.5, 45.3, "Zwischen Asowschem und Schwarzem Meer, im Kriegsgebiet."),
    "chokepoint12": ("Koreastraße", 129.3, 34.3, "Zwischen Korea und Japan."),
    "chokepoint13": ("Tsugaru-Straße", 140.5, 41.4, "Zwischen den japanischen Inseln Honshu und Hokkaido."),
    "chokepoint14": ("Luzonstraße", 121.5, 20.5, "Zwischen Taiwan und den Philippinen, Ausweichroute zur Taiwan-Straße."),
    "chokepoint17": ("Bohai-Straße", 121.2, 38.3, "Zugang zu den Häfen um Peking und Tianjin."),
    "chokepoint19": ("Sundastraße", 105.8, -6.0, "Ausweichroute zur Straße von Malakka."),
    "chokepoint15": ("Lombokstraße", 115.7, -8.7, "Route für große Tanker, die für Malakka zu tief liegen."),
    "chokepoint20": ("Makassarstraße", 118.0, -2.0, "Zwischen Borneo und Sulawesi."),
    "chokepoint16": ("Ombaistraße", 125.0, -8.4, "Tiefwasser-Route in Indonesien."),
    "chokepoint18": ("Torresstraße", 142.3, -10.5, "Zwischen Australien und Neuguinea."),
    "chokepoint25": ("Balabacstraße", 117.0, 7.8, "Zwischen den Philippinen und Borneo."),
    "chokepoint27": ("Mindorostraße", 120.5, 12.5, "Philippinen."),
    "chokepoint22": ("Yucatánstraße", -86.0, 21.6, "Zwischen Mexiko und Kuba, Zugang zum Golf von Mexiko."),
    "chokepoint23": ("Windward-Passage", -73.8, 20.0, "Zwischen Kuba und Haiti."),
    "chokepoint24": ("Monapassage", -67.8, 18.3, "Zwischen Hispaniola und Puerto Rico."),
    "chokepoint21": ("Magellanstraße", -70.5, -53.5, "Südspitze Südamerikas, Ausweichroute zum Panamakanal."),
    "chokepoint26": ("Beringstraße", -169.0, 65.8, "Zwischen Russland und Alaska, Arktisroute."),
}
# Rechtecke für die Live-Schiffe: [[Breite unten, Länge links], [Breite oben, Länge rechts]]
AIS_GEBIETE = {
    "chokepoint6": [[24.2, 54.0], [27.5, 58.6]], "chokepoint4": [[11.2, 42.4], [13.8, 44.4]], "chokepoint1": [[29.5, 31.9], [31.7, 33.0]],
    "chokepoint5": [[0.9, 100.5], [3.5, 104.6]], "chokepoint2": [[8.7, -80.2], [9.6, -79.3]], "chokepoint11": [[22.8, 118.3], [25.6, 121.2]],
    "chokepoint3": [[40.8, 28.8], [41.4, 29.4]], "chokepoint8": [[35.7, -6.2], [36.2, -5.0]], "chokepoint9": [[50.7, 0.9], [51.3, 2.1]],
}
ARTEN_EREIGNIS = {"TC": "Wirbelsturm", "FL": "Überschwemmung", "VO": "Vulkan", "DR": "Dürre", "WF": "Waldbrand", "EQ": "Erdbeben"}
# Umkreis in km, in dem ein Ereignis Anlagen gefährden kann
UMKREIS = {"TC": 600, "FL": 250, "VO": 150, "DR": 400, "WF": 100}


def _km(lon1, lat1, lon2, lat2) -> float:
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


# ---------- Engstellen ----------

def engstellen() -> dict:
    def holen():
        seit = (date.today() - timedelta(days=430)).isoformat()
        zeilen, offset = [], 0
        while True:
            antwort = requests.get(PORTWATCH, params={"where": f"date >= DATE '{seit}'", "outFields": "date,portid,n_total,n_tanker,n_container,n_dry_bulk,capacity",
                                                       "orderByFields": "date", "resultOffset": offset, "resultRecordCount": 2000, "f": "json"}, headers=KOPF, timeout=60).json()
            teile = [f["attributes"] for f in antwort.get("features", [])]
            zeilen += teile
            if not antwort.get("exceededTransferLimit") or not teile:
                break
            offset += len(teile)
        if len(zeilen) < 1000:
            raise ValueError("zu wenige Daten")
        tab = pd.DataFrame(zeilen)
        tab["date"] = pd.to_datetime(tab["date"])
        aus = {}
        for pid, t in tab.groupby("portid"):
            if pid not in ENGSTELLEN:
                continue
            t = t.set_index("date").sort_index()
            t = t[~t.index.duplicated(keep="last")]
            t = t.drop(columns=["portid"])
            woche = t.rolling("7D").mean()
            letzt = t.index[-1]
            vorjahr = t[(t.index > letzt - pd.Timedelta(days=372)) & (t.index <= letzt - pd.Timedelta(days=7))]
            jetzt = t[t.index > letzt - pd.Timedelta(days=7)]
            def schnitt(feld, teil):
                return float(teil[feld].mean()) if len(teil) else None
            def veraenderung(feld):
                a, b = schnitt(feld, jetzt), schnitt(feld, vorjahr)
                return (a / b - 1) * 100 if a is not None and b else None
            name, lon, lat, notiz = ENGSTELLEN[pid]
            aus[pid] = {
                "id": pid, "name": name, "lon": lon, "lat": lat, "notiz": notiz, "stand": letzt.date().isoformat(),
                "schiffe": schnitt("n_total", jetzt), "tanker": schnitt("n_tanker", jetzt), "container": schnitt("n_container", jetzt),
                "normal": schnitt("n_total", vorjahr), "normal_tanker": schnitt("n_tanker", vorjahr),
                "veraenderung": veraenderung("n_total"), "veraenderung_tanker": veraenderung("n_tanker"),
                "verlauf": [round(float(v), 1) for v in woche["n_total"].iloc[-120:]],
                "verlauf_tanker": [round(float(v), 1) for v in woche["n_tanker"].iloc[-120:]],
            }
        return aus
    return gecacht("lage:engstellen", 6 * 3600, holen)


# ---------- Ereignisse ----------

def _vereinfachen(ring: list, hoechstens: int = 120) -> list:
    schritt = max(1, len(ring) // hoechstens)
    kurz = ring[::schritt]
    return kurz + [ring[0]] if kurz[-1] != ring[0] else kurz


def _sturm_geometrie(url: str) -> dict:
    """Zugbahn (Linien) und Vorhersagekegel eines Wirbelsturms, stark vereinfacht."""
    j = requests.get(url, headers=KOPF, timeout=30).json()
    linien, kegel = [], None
    for f in j.get("features", []):
        klasse = f["properties"].get("Class") or ""
        g = f["geometry"]
        if klasse.startswith("Line_Line") and g["type"] == "LineString":
            linien.append([[round(x, 2), round(y, 2)] for x, y in g["coordinates"]])
        elif klasse == "Poly_Cones" and g["type"] in ("Polygon", "MultiPolygon"):
            ringe = g["coordinates"] if g["type"] == "Polygon" else [r for p in g["coordinates"] for r in p]
            kegel = [[[round(x, 2), round(y, 2)] for x, y in _vereinfachen(r)] for r in ringe[:3]]
    return {"bahn": linien, "kegel": kegel}


def ereignisse() -> dict:
    def holen():
        heute = date.today()
        # Wirbelstürme, Vulkane und Überschwemmungen auf allen Stufen; Dürren und Waldbrände (sehr viele kleine) erst ab Orange
        features = []
        for liste, stufen in (("TC;VO;FL", "Green;Orange;Red"), ("DR;WF", "Orange;Red")):
            r = requests.get(GDACS, params={"eventlist": liste, "fromdate": (heute - timedelta(days=21)).isoformat(),
                                            "todate": heute.isoformat(), "alertlevel": stufen}, headers=KOPF, timeout=40).json()
            features += r.get("features", [])
        aus = []
        for f in features:
            p = f["properties"]
            if str(p.get("iscurrent")).lower() != "true" or f["geometry"]["type"] != "Point":
                continue
            if p["eventtype"] in ("WF", "DR") and (p.get("alertlevel") or "Green") == "Green":
                continue  # sehr viele kleine Brände und Dürren, die GDACS trotz Filter liefert
            lon, lat = f["geometry"]["coordinates"][:2]
            e = {"art": p["eventtype"], "art_name": ARTEN_EREIGNIS.get(p["eventtype"], p["eventtype"]), "id": f"{p['eventtype']}{p['eventid']}",
                 "name": p.get("eventname") or p.get("name"), "titel": p.get("name"), "stufe": (p.get("alertlevel") or "Green").lower(),
                 "land": p.get("country"), "von": p.get("fromdate"), "bis": p.get("todate"), "lon": lon, "lat": lat,
                 "schwere": None if re.fullmatch(r"\s*Magnitude\s+0(\.0+)?\s*", (p.get("severitydata") or {}).get("severitytext") or "") else (p.get("severitydata") or {}).get("severitytext"),
                 "link": (p.get("url") or {}).get("report")}
            if p["eventtype"] == "TC" and (p.get("url") or {}).get("geometry"):
                try:
                    e.update(_sturm_geometrie(p["url"]["geometry"]))
                except Exception:
                    pass
            aus.append(e)
        beben = []
        for f in requests.get(USGS, headers=KOPF, timeout=40).json().get("features", []):
            p = f["properties"]; lon, lat, tiefe = f["geometry"]["coordinates"]
            beben.append({"art": "EQ", "art_name": "Erdbeben", "id": f["id"], "name": p.get("place"), "staerke": p.get("mag"), "tiefe": tiefe,
                          "zeit": datetime.fromtimestamp(p["time"] / 1000, timezone.utc).isoformat(timespec="minutes"), "lon": lon, "lat": lat,
                          "tsunami": bool(p.get("tsunami")), "stufe": {"red": "red", "orange": "orange", "yellow": "orange"}.get(p.get("alert") or "", "green"),
                          "link": p.get("url")})
        beben.sort(key=lambda b: -(b["staerke"] or 0))
        return {"ereignisse": aus, "beben": beben[:120]}
    return gecacht("lage:ereignisse3", 30 * 60, holen)


# ---------- Infrastruktur ----------

def infrastruktur() -> list[dict]:
    try:
        roh = yaml.safe_load(INFRA_DATEI.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, yaml.YAMLError):
        return []
    aus = []
    for a in roh.get("anlagen") or []:
        if not isinstance(a, dict) or not a.get("name") or not (a.get("punkt") or a.get("verlauf")):
            continue
        aus.append({"name": str(a["name"]), "art": str(a.get("art") or "sonstiges"), "punkt": a.get("punkt"), "verlauf": a.get("verlauf"),
                    "notiz": a.get("notiz"), "ticker": [str(t) for t in a.get("ticker") or []]})
    return aus


def _lage_von(a: dict) -> list:
    return [a["punkt"]] if a.get("punkt") else a.get("verlauf") or []


def hinweise(anlagen: list[dict], ev: dict, eng: dict) -> list[dict]:
    """Was gerade Märkte betreffen könnte: Ereignisse nahe an Anlagen oder Engstellen, auffällig wenig Verkehr."""
    aus = []
    ziele = [(a["name"], a["art"], _lage_von(a), a.get("ticker") or []) for a in anlagen]
    ziele += [(e["name"], "engstelle", [[e["lon"], e["lat"]]], []) for e in eng.values()]
    def naehe(lon, lat, km):
        treffer = []
        for name, art, punkte, ticker in ziele:
            d = min((_km(lon, lat, x, y) for x, y in punkte), default=1e9)
            if d <= km:
                treffer.append({"name": name, "art": art, "km": round(d), "ticker": ticker})
        return sorted(treffer, key=lambda t: t["km"])
    for e in ev.get("ereignisse", []):
        km = UMKREIS.get(e["art"], 200) * (1.5 if e["stufe"] == "red" else 1)
        treffer = naehe(e["lon"], e["lat"], km)
        if treffer or e["stufe"] in ("orange", "red"):
            aus.append({"art": e["art"], "id": e["id"], "titel": f"{e['art_name']} {e['name'] or ''}".strip(), "stufe": e["stufe"], "naehe": treffer[:6],
                        "text": e.get("schwere") or e.get("land")})
    for b in ev.get("beben", []):
        if (b["staerke"] or 0) < 5.5:
            continue
        km = 120 if b["staerke"] < 6.5 else 300
        treffer = naehe(b["lon"], b["lat"], km)
        if treffer or b["staerke"] >= 6.5:
            aus.append({"art": "EQ", "id": b["id"], "titel": f"Erdbeben Stärke {b['staerke']:.1f}".replace(".", ","), "stufe": b["stufe"] if b["staerke"] < 7 else "red",
                        "naehe": treffer[:6], "text": b["name"]})
    for e in eng.values():
        if e["veraenderung"] is not None and e["veraenderung"] <= -25 and (e["normal"] or 0) >= 5:
            aus.append({"art": "ENG", "id": e["id"], "titel": f"{e['name']}: {abs(e['veraenderung']):.0f} % weniger Schiffe als üblich".replace(".", ","),
                        "stufe": "red" if e["veraenderung"] <= -50 else "orange", "naehe": [], "text": e["notiz"]})
    rang = {"red": 0, "orange": 1, "green": 2}
    return sorted(aus, key=lambda h: (rang.get(h["stufe"], 3), -len(h["naehe"])))


# ---------- Schiffe live (aisstream.io) ----------

class _Ais:
    def __init__(self):
        self.schiffe: dict[int, dict] = {}
        self.typen: dict[int, int] = {}
        self.sperre = threading.Lock()
        self.thread = None
        self.angefragt = 0.0
        self.fehler = None
        self.verbunden = False
        self.sicht = None  # [[Breite unten, Länge links], [Breite oben, Länge rechts]] des gerade angezeigten Ausschnitts
        self.ws = None
        self.schluessel = ""

    def _abo(self) -> str:
        gebiete = list(AIS_GEBIETE.values()) + ([self.sicht] if self.sicht else [])
        return json.dumps({"APIKey": self.schluessel, "BoundingBoxes": gebiete,
                           "FilterMessageTypes": ["PositionReport", "ShipStaticData", "StandardClassBPositionReport"]})

    def setze_sicht(self, box):
        """Neuer Ausschnitt, höchstens 25 Grad groß. Ändert das Abo nur bei spürbarer Verschiebung."""
        if box:
            (la1, lo1), (la2, lo2) = box
            if la2 - la1 > 25 or lo2 - lo1 > 25 or la2 <= la1 or lo2 <= lo1:
                box = None
        if box == self.sicht or (box and self.sicht and all(abs(a - b) < 0.3 for x, y in zip(box, self.sicht) for a, b in zip(x, y))):
            return
        self.sicht = box
        ws = self.ws
        if ws is not None:
            try:
                ws.send(self._abo())  # aisstream nimmt ein neues Abo auf derselben Verbindung an
            except Exception:
                pass

    def _laufen(self, schluessel: str):
        from websockets.sync.client import connect
        self.schluessel = schluessel
        while time.time() - self.angefragt < 600:
            try:
                with connect("wss://stream.aisstream.io/v0/stream", open_timeout=15, close_timeout=3) as ws:
                    ws.send(self._abo())
                    self.ws = ws
                    self.verbunden, self.fehler = True, None
                    while time.time() - self.angefragt < 600:
                        try:
                            roh = ws.recv(timeout=30)
                        except TimeoutError:
                            continue
                        self._nachricht(json.loads(roh))
            except Exception as fehler:
                self.verbunden, self.ws = False, None
                self.fehler = type(fehler).__name__  # nie den Key ausgeben
                time.sleep(20)
        self.verbunden, self.ws = False, None

    def _nachricht(self, m: dict):
        meta = m.get("MetaData") or {}
        mmsi = meta.get("MMSI")
        if not mmsi:
            return
        art = m.get("MessageType")
        inhalt = (m.get("Message") or {}).get(art) or {}
        if art == "ShipStaticData":
            if inhalt.get("Type") is not None:
                self.typen[mmsi] = int(inhalt["Type"])
            return
        lat, lon = meta.get("latitude"), meta.get("longitude")
        if lat is None or lon is None:
            return
        with self.sperre:
            self.schiffe[mmsi] = {"lon": round(lon, 4), "lat": round(lat, 4), "kurs": inhalt.get("Cog"), "fahrt": inhalt.get("Sog"),
                                  "name": (meta.get("ShipName") or "").strip(), "t": time.time()}
            if len(self.schiffe) > 6000:  # Speicher begrenzen: die ältesten Meldungen fallen raus
                for k in sorted(self.schiffe, key=lambda k: self.schiffe[k]["t"])[:1000]:
                    del self.schiffe[k]

    def stand(self, sicht=None) -> dict:
        schluessel = key("AISSTREAM_API_KEY")
        if not schluessel:
            return {"an": False}
        self.angefragt = time.time()
        self.setze_sicht(sicht)
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._laufen, args=(schluessel,), daemon=True, name="ais")
            self.thread.start()
        grenze = time.time() - 30 * 60
        with self.sperre:
            aktuell = [(k, s) for k, s in self.schiffe.items() if s["t"] >= grenze]
        def typ(mmsi):
            t = self.typen.get(mmsi)
            return "tanker" if t and 80 <= t <= 89 else "fracht" if t and 70 <= t <= 79 else "andere" if t else "unbekannt"
        liste = [{**s, "typ": typ(k)} for k, s in aktuell]
        zaehlung = {}
        for pid, ((la1, lo1), (la2, lo2)) in AIS_GEBIETE.items():
            drin = [s for s in liste if la1 <= s["lat"] <= la2 and lo1 <= s["lon"] <= lo2]
            zaehlung[pid] = {"alle": len(drin), "tanker": sum(1 for s in drin if s["typ"] == "tanker"), "fahrt": sum(1 for s in drin if (s["fahrt"] or 0) >= 2)}
        if self.sicht:
            (la1, lo1), (la2, lo2) = self.sicht
            zaehlung["sicht"] = {"alle": sum(1 for s in liste if la1 <= s["lat"] <= la2 and lo1 <= s["lon"] <= lo2)}
        return {"an": True, "verbunden": self.verbunden, "fehler": self.fehler, "schiffe": liste, "zaehlung": zaehlung, "gebiete": AIS_GEBIETE, "sicht": self.sicht}


AIS = _Ais()


def lage() -> dict:
    aus = {}
    for name, f in (("engstellen", engstellen), ("ereignisse", ereignisse)):
        try:
            aus[name] = f()
        except Exception:
            aus[name] = None
    anlagen = infrastruktur()
    eng = aus["engstellen"] or {}
    ev = aus["ereignisse"] or {"ereignisse": [], "beben": []}
    return {"engstellen": list(eng.values()), "ereignisse": ev["ereignisse"], "beben": ev["beben"], "infrastruktur": anlagen,
            "hinweise": hinweise(anlagen, ev, eng), "ais": bool(key("AISSTREAM_API_KEY")),
            "fehler": [n for n in ("engstellen", "ereignisse") if aus[n] is None]}
