"""Die Quellen und ihr Abruf.

Ablauf je Lieferung: holen -> unverändert als Rohdatei ablegen -> einlesen -> prüfen -> speichern
(neue Version nur bei Änderung) oder in Quarantäne.

Abruf mit wenig Last:
- Limit-Zähler je Quelle und Tag, höchstens limit_anteil (70 %) des Kontingents, dann Pause statt Fehler.
- Bei Fehlern oder Bremsung (429, 5xx, Zeitüberschreitung) bis zu 5 Versuche mit wachsender Wartezeit.
- Wo der Server es kann, wird mit ETag/Last-Modified gefragt, ob sich etwas geändert hat (Antwort 304 = nein).
  Sonst entscheidet die Prüfsumme der Rohdatei, ob neu eingelesen werden muss.
- Webseiten (Zoll China, TSMC) mit eigenem User-Agent samt Kontakt aus der .env und Pausen zwischen den Seiten.
"""
import json
import time
from datetime import date, datetime, timedelta
from functools import lru_cache

import pandas as pd
import requests
import yaml

from ...einstellungen import key
from ...konfig import MARKT_DATEN
from . import einlesen as E
from . import speicher as S
from .laender import comtrade_code

QUELLEN_DATEI = MARKT_DATEN / "handel_quellen.yaml"
GRUPPEN_DATEI = MARKT_DATEN / "warengruppen.yaml"


class Pausiert(RuntimeError):
    """Kontingent für heute ausgeschöpft."""


class AbrufFehler(RuntimeError):
    pass


def konfig() -> dict:
    try:
        return yaml.safe_load(QUELLEN_DATEI.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, yaml.YAMLError):
        return {}


def gruppen() -> dict:
    try:
        roh = yaml.safe_load(GRUPPEN_DATEI.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, yaml.YAMLError):
        return {}
    return {k: {"id": k, "name": v.get("name") or k, "hs": [str(c) for c in v.get("hs") or []], "china": list(v.get("china") or []),
                "hinweis": v.get("hinweis")} for k, v in roh.items() if isinstance(v, dict)}


def allgemein() -> dict:
    return {"limit_anteil": 0.7, "versuche": 5, "sprung_schwelle": 60, "auffaellig_sigma": 2, "kontakt_env": "HANDEL_KONTAKT",
            **(konfig().get("einstellungen") or {})}


def _kopf_webseite() -> dict:
    kontakt = key(allgemein()["kontakt_env"])  # nur, wenn ausdrücklich für den Handel eingetragen
    ua = f"Vinter/1.0 (privates Marktdashboard, seltene Abrufe{'; ' + kontakt if kontakt else ''})"
    return {"User-Agent": ua, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}


class Abruf:
    """Ein Lauf einer Quelle: zählt Anfragen, wiederholt bei Fehlern, merkt sich ETag/Last-Modified."""

    def __init__(self, qid: str, cfg: dict):
        self.qid, self.cfg, self.allg = qid, cfg, allgemein()
        self.anfragen = 0
        self.letzte = 0.0

    def frei(self) -> int:
        grenze = int((self.cfg.get("limit") or 100) * self.allg["limit_anteil"])
        return grenze - S.anfragen_heute(self.qid)

    def get(self, url: str, params: dict | None = None, kopf: dict | None = None, bedingt: bool = False, zeitlimit: int = 60) -> bytes | None:
        """Lädt eine Adresse. bedingt=True: fragt mit ETag/Last-Modified und gibt bei "unverändert" None zurück."""
        if self.frei() <= 0:
            raise Pausiert(f"Kontingent für heute erreicht ({int((self.cfg.get('limit') or 100) * self.allg['limit_anteil'])} Anfragen)")
        abstand = float(self.cfg.get("abstand_sekunden") or 0)
        warte = abstand - (time.time() - self.letzte)
        if warte > 0:
            time.sleep(warte)
        kopf = dict(kopf or _kopf_webseite())
        merk = "http:" + url + json.dumps(params or {}, sort_keys=True)
        if bedingt:
            alt = S.zustand(merk, {})
            if alt.get("etag"):
                kopf["If-None-Match"] = alt["etag"]
            if alt.get("lm"):
                kopf["If-Modified-Since"] = alt["lm"]
        letzter = ""
        for versuch in range(int(self.allg["versuche"])):
            if versuch:
                time.sleep(min(5 * 2 ** (versuch - 1), 120))  # 5, 10, 20, 40 Sekunden
            try:
                S.anfrage_zaehlen(self.qid)
                self.anfragen += 1
                r = requests.get(url, params=params, headers=kopf, timeout=zeitlimit)
                self.letzte = time.time()
            except (requests.ConnectionError, requests.Timeout) as f:
                letzter = type(f).__name__
                continue
            if r.status_code == 304:
                return None
            if r.status_code == 429 or r.status_code >= 500:
                letzter = f"HTTP {r.status_code}"
                continue
            if r.status_code >= 400:
                raise AbrufFehler(f"HTTP {r.status_code} bei {url.split('?')[0]}")
            if bedingt and (r.headers.get("ETag") or r.headers.get("Last-Modified")):
                S.zustand_setzen(merk, {"etag": r.headers.get("ETag"), "lm": r.headers.get("Last-Modified")})
            return r.content
        raise AbrufFehler(f"{url.split('?')[0]}: nach {self.allg['versuche']} Versuchen aufgegeben ({letzter})")


# ------------------------------------------------------------------ Einlesen je Art (auch für "neu einlesen")
@lru_cache(maxsize=4)
def _twd_monate(stand: str) -> dict:
    """Monatsdurchschnitt TWD je USD von Yahoo. stand = Tag, damit der Cache täglich frisch ist."""
    import yfinance as yf
    k = yf.Ticker("TWD=X").history(period="5y", interval="1d", auto_adjust=False)["Close"].dropna()
    k.index = k.index.tz_localize(None) if k.index.tz is not None else k.index
    return {p.strftime("%Y-%m"): float(v) for p, v in k.resample("ME").mean().dropna().items()}


def einlesen(art: str, meta: dict, inhalt: bytes, cfg: dict) -> tuple[list[dict], list[str], dict]:
    """Gibt (zeilen, probleme, zusatz) zurück; zusatz z. B. nicht zugeordnete Ländernamen."""
    tol = float(cfg.get("summen_toleranz") or 3)
    if art == "census":
        z, p, u = E.census(E.json_laden(inhalt), meta["richtung"], meta["ware"], meta["ware_text"], tol)
        return z, p, {"unbekannt": u}
    if art == "comtrade":
        z, p = E.comtrade(E.json_laden(inhalt), tol)
        return z, p, {}
    if art == "gacc_laender":
        z, p, u = E.gacc_laender(inhalt.decode("utf-8", "ignore"))
        return z, p, {"unbekannt": u}
    if art in ("gacc_export", "gacc_import"):
        z, p = E.gacc_waren(inhalt.decode("utf-8", "ignore"), art.split("_")[1])
        return z, p, {}
    if art == "tsmc":
        z, p = E.tsmc(inhalt.decode("utf-8", "ignore"), int(meta["jahr"]))
        kurse = _twd_monate(date.today().isoformat())
        for zeile in z:
            k = kurse.get(zeile["zeitraum"])
            if k:
                zeile.update({"wert_usd": zeile["wert_orig"] / k, "kurs": round(k, 4), "kurs_quelle": "Yahoo TWD=X, Monatsdurchschnitt"})
            else:
                p.append(f"TSMC: kein Wechselkurs für {zeile['zeitraum']}")
        return z, p, {}
    if art == "portwatch":
        from ..lage import ENGSTELLEN
        z, p = E.portwatch(E.json_laden(inhalt), ENGSTELLEN)
        return z, p, {}
    return [], [], {}


def verarbeiten(qid: str, cfg: dict, art: str, meta: dict, inhalt: bytes, endung: str) -> dict:
    roh_id, neu = S.roh_ablegen(qid, art, meta, inhalt, endung)
    if not neu:
        return {"unveraendert": 1}
    return _einlesen_und_speichern(qid, cfg, art, meta, inhalt, roh_id)


def _einlesen_und_speichern(qid, cfg, art, meta, inhalt, roh_id) -> dict:
    try:
        zeilen, probleme, zusatz = einlesen(art, meta, inhalt, cfg)
    except E.StrukturFehler as f:
        S.quarantaene(qid, art, f"Aufbau geändert: {f}", roh_id, 0)
        S.protokollieren(qid, False, f"Strukturwarnung ({art}): {f}")
        return {"quarantaene": 1, "struktur": 1}
    if zusatz.get("unbekannt"):
        S.zustand_setzen(f"unbekannt:{qid}:{art}", zusatz["unbekannt"][:40])
    if probleme:
        S.quarantaene(qid, art, "; ".join(probleme[:3]) + (f" (+{len(probleme) - 3})" if len(probleme) > 3 else ""), roh_id, len(zeilen))
        S.protokollieren(qid, False, f"Lieferung in Quarantäne ({art}): {probleme[0]}")
        return {"quarantaene": 1}
    # Keine Quelle nennt ein Datum: es gilt der Tag, an dem Vinter den Wert zuerst gesehen hat. Ältere Zeiträume
    # (beim ersten Laden der Historie) waren schon vorher erschienen und bekommen kein Datum.
    heute = date.today()
    grenze = erwartet(cfg) or ""
    woche = (heute - timedelta(days=16)).isoformat()
    for z in zeilen:
        neu = z["zeitraum"] >= woche if len(z["zeitraum"]) == 10 else bool(grenze) and z["zeitraum"] >= grenze
        z.setdefault("veroeffentlicht", heute.isoformat() if neu else None)
    erg = S.speichern(zeilen, roh_id, float(allgemein()["sprung_schwelle"]))
    frisch = {z["zeitraum"] for z in zeilen if z.get("veroeffentlicht")}
    for zr in erg["zeitraeume"]:
        if zr in frisch:  # nur wirklich neu erschienene Zeiträume, nicht die Historie beim ersten Laden
            S.neuigkeit(qid, zr, f"{cfg.get('name', qid)}: neue Daten für {zr}")
    return {k: v for k, v in erg.items() if k != "zeitraeume"} | {"zeitraeume": len(erg["zeitraeume"])}


def neu_einlesen(qid: str) -> dict:
    """Alle Rohdaten einer Quelle erneut einlesen, ohne etwas abzurufen (z. B. nach einer Formatänderung)."""
    cfg = (konfig().get("quellen") or {}).get(qid) or {}
    summe = {}
    for r in S.roh_liste(qid):
        if r["art"] in ("gacc_index",):
            continue
        erg = _einlesen_und_speichern(qid, cfg, r["art"], json.loads(r["meta"]), S.roh_lesen(r), r["id"])
        for k, v in erg.items():
            summe[k] = summe.get(k, 0) + v
    S.protokollieren(qid, True, f"Neu eingelesen: {summe}")
    return summe


# ------------------------------------------------------------------ die Quellen
def _monat(heute: date, minus: int) -> str:
    j, m = heute.year, heute.month - minus
    while m <= 0:
        j, m = j - 1, m + 12
    return f"{j}-{m:02d}"


def hat_zeitraum(qid: str, zeitraum: str) -> bool:
    return bool(S.abfrage("SELECT 1 FROM werte WHERE quelle = ? AND zeitraum = ? LIMIT 1", (qid, zeitraum)))


def hat_daten(qid: str) -> bool:
    return bool(S.abfrage("SELECT 1 FROM werte WHERE quelle = ? LIMIT 1", (qid,)))


def neuester(qid: str) -> str | None:
    z = S.abfrage("SELECT MAX(zeitraum) AS z FROM werte WHERE quelle = ?", (qid,))
    return z[0]["z"] if z else None


class Census:
    BASIS = "https://api.census.gov/data/timeseries/intltrade/"

    def __init__(self, qid, cfg):
        self.qid, self.cfg = qid, cfg

    def bereit(self) -> str | None:
        return None if key(self.cfg.get("key_env", "CENSUS_API_KEY")) else f"{self.cfg.get('key_env')} fehlt in der .env"

    def _params(self, richtung, ware, zeit):
        wert = "ALL_VAL_MO" if richtung == "export" else "GEN_VAL_MO"
        menge = "QTY_1_MO" if richtung == "export" else "GEN_QY1_MO"
        felder = f"CTY_CODE,CTY_NAME,{wert}" + ("" if ware == "TOTAL" else f",{menge},UNIT_QY1")
        p = {"get": felder, "time": zeit, ("E_COMMODITY" if richtung == "export" else "I_COMMODITY"): "-" if ware == "TOTAL" else ware,
             "key": key(self.cfg.get("key_env", "CENSUS_API_KEY"))}
        if ware != "TOTAL":
            p["COMM_LVL"] = f"HS{len(ware)}"
        return self.BASIS + ("exports/hs" if richtung == "export" else "imports/hs"), p

    def neu_da(self, a: Abruf, erwartet: str) -> bool:
        url, p = self._params("export", "TOTAL", erwartet)
        p["CTY_CODE"] = "-"
        inhalt = a.get(url, p, kopf={"User-Agent": "Vinter/1.0"})
        return bool(inhalt and inhalt.strip().startswith(b"["))

    def lauf(self, a: Abruf, voll: bool) -> dict:
        waren = [("TOTAL", "Alle Waren")]
        for g in gruppen().values():
            waren += [(c, f"{g['name']} (HS {c})") for c in g["hs"]]
        summe = {}
        for richtung in ("export", "import"):
            for ware, text in dict(waren).items():
                seit = self.cfg.get('seit', '2023-01') if voll else _monat(date.today(), 14)
                url, p = self._params(richtung, ware, f"from {seit}")
                inhalt = a.get(url, p, kopf={"User-Agent": "Vinter/1.0"})
                if not inhalt:  # 204: keine Daten zu diesem Code
                    continue
                erg = verarbeiten(self.qid, self.cfg, "census", {"richtung": richtung, "ware": ware, "ware_text": text}, inhalt, "json")
                for k, v in erg.items():
                    summe[k] = summe.get(k, 0) + v
        return summe


class Comtrade:
    URL = "https://comtradeapi.un.org/data/v1/get/C/{frq}/HS"

    def __init__(self, qid, cfg):
        self.qid, self.cfg = qid, cfg

    def bereit(self) -> str | None:
        return None if key(self.cfg.get("key_env", "COMTRADE_API_KEY")) else f"{self.cfg.get('key_env')} fehlt in der .env"

    def _kopf(self):
        return {"Ocp-Apim-Subscription-Key": key(self.cfg.get("key_env", "COMTRADE_API_KEY")), "User-Agent": "Vinter/1.0"}

    def _codes(self) -> str:
        codes = ["TOTAL"] + [c for g in gruppen().values() for c in g["hs"]]
        return ",".join(dict.fromkeys(codes))

    def jahre(self) -> list[int]:
        j = date.today().year
        return list(range(j - int(self.cfg.get("jahre") or 2), j))

    def land_jahr(self, a: Abruf, iso: str, jahre: list[int] | None = None) -> dict:
        """Jahreswerte eines Berichtslands mit allen Partnern (für Chord-Diagramm und Spiegeldaten)."""
        code = comtrade_code(iso)
        if not code:
            return {}
        jahre = jahre or self.jahre()
        p = {"reporterCode": code, "period": ",".join(map(str, jahre)), "cmdCode": self._codes(), "flowCode": "X,M",
             "partner2Code": 0, "customsCode": "C00", "motCode": 0}
        inhalt = a.get(self.URL.format(frq="A"), p, kopf=self._kopf(), zeitlimit=180)
        time.sleep(1.5)
        return verarbeiten(self.qid, self.cfg, "comtrade", {"art": "jahr", "land": iso, "jahre": jahre}, inhalt, "json") if inhalt else {}

    def fehlende_jahre(self, iso: str) -> list[int]:
        """Jahre ab historie_ab, die für dieses Land noch nicht gespeichert sind (ohne die regelmäßig geholten)."""
        ab = int(self.cfg.get("historie_ab") or 0)
        if not ab:
            return []
        da = {r["zeitraum"] for r in S.abfrage("SELECT DISTINCT zeitraum FROM werte WHERE quelle = 'comtrade' AND berichtsland = ? AND LENGTH(zeitraum) = 4", (iso,))}
        versucht = set(S.zustand(f"historie_versucht:{iso}", []) or [])
        return [j for j in range(ab, min(self.jahre())) if str(j) not in da and j not in versucht]

    def historie(self, a: Abruf, iso: str) -> dict:
        """Ältere Jahre einmalig nachladen, 3 Jahre je Anfrage. Jahre ohne Meldung werden gemerkt und nicht erneut gefragt."""
        summe = {}
        fehlt = self.fehlende_jahre(iso)
        for i in range(0, len(fehlt), 3):
            teil = fehlt[i:i + 3]
            for k, v in self.land_jahr(a, iso, teil).items():
                summe[k] = summe.get(k, 0) + v
            S.zustand_setzen(f"historie_versucht:{iso}", sorted(set(S.zustand(f"historie_versucht:{iso}", []) or []) | set(teil)))
        return summe

    def monate_mit_partnern(self, a: Abruf, iso: str) -> dict:
        """Laufendes Jahr: Monatswerte je Partner (nur Gesamtwert), dazu dieselben Monate im Vorjahr zum Vergleich."""
        code = comtrade_code(iso)
        if not code:
            return {}
        heute = date.today()
        monate = [f"{heute.year}{m:02d}" for m in range(1, heute.month)] + [f"{heute.year - 1}{m:02d}" for m in range(1, heute.month)]
        summe = {}
        for i in range(0, len(monate), 12):
            p = {"reporterCode": code, "period": ",".join(monate[i:i + 12]), "cmdCode": "TOTAL", "flowCode": "X,M",
                 "partner2Code": 0, "customsCode": "C00", "motCode": 0}
            inhalt = a.get(self.URL.format(frq="M"), p, kopf=self._kopf(), zeitlimit=180)
            time.sleep(1.5)
            if inhalt:
                for k, v in verarbeiten(self.qid, self.cfg, "comtrade", {"art": "monat_partner", "land": iso, "perioden": monate[i:i + 12]}, inhalt, "json").items():
                    summe[k] = summe.get(k, 0) + v
        return summe

    def lauf(self, a: Abruf, voll: bool) -> dict:
        summe = {}
        def add(erg):
            for k, v in erg.items():
                summe[k] = summe.get(k, 0) + v
        laender = [l for l in self.cfg.get("berichtslaender") or [] if comtrade_code(l)]
        for iso in laender:
            add(self.land_jahr(a, iso))
            add(self.historie(a, iso))
            if self.cfg.get("monate_mit_partnern"):
                add(self.monate_mit_partnern(a, iso))
        # Monatswerte gegen die ganze Welt, 36 Monate in Blöcken zu 12
        heute = date.today()
        monate = [_monat(heute, m).replace("-", "") for m in range(36, 0, -1)]
        codes = ",".join(str(comtrade_code(l)) for l in laender)
        for i in range(0, 36, 12):
            p = {"reporterCode": codes, "period": ",".join(monate[i:i + 12]), "partnerCode": 0, "cmdCode": self._codes(), "flowCode": "X,M",
                 "partner2Code": 0, "customsCode": "C00", "motCode": 0}
            inhalt = a.get(self.URL.format(frq="M"), p, kopf=self._kopf(), zeitlimit=180)
            time.sleep(1.5)
            if inhalt:
                add(verarbeiten(self.qid, self.cfg, "comtrade", {"art": "monat", "perioden": monate[i:i + 12]}, inhalt, "json"))
        return summe


class Gacc:
    def __init__(self, qid, cfg):
        self.qid, self.cfg = qid, cfg

    def bereit(self):
        return None

    def _index_url(self, jahr: int) -> str:
        return "http://english.customs.gov.cn/statics/report/preliminary.html" if jahr == date.today().year else \
            f"http://english.customs.gov.cn/statics/report/preliminary{jahr}.html"

    def verweise(self, a: Abruf, jahr: int) -> dict:
        inhalt = a.get(self._index_url(jahr))
        S.roh_ablegen(self.qid, "gacc_index", {"jahr": jahr}, inhalt, "html")
        try:
            return E.gacc_verweise(inhalt.decode("utf-8", "ignore"), jahr)
        except E.StrukturFehler as f:
            S.quarantaene(self.qid, "gacc_index", f"Aufbau geändert: {f}", None, 0)
            raise

    def neu_da(self, a: Abruf, erwartet: str) -> bool:
        return erwartet in self.verweise(a, int(erwartet[:4])).get("4", {})

    def lauf(self, a: Abruf, voll: bool) -> dict:
        heute = date.today()
        jahre = [int(j) for j in self.cfg.get("jahre") or [heute.year]] if voll else sorted({heute.year, (heute - timedelta(days=75)).year})
        summe = {}
        for jahr in jahre:
            v = self.verweise(a, jahr)
            for zeitraum in sorted(v.get("4", {})):
                if hat_zeitraum(self.qid, zeitraum):
                    continue
                for nr, art in (("4", "gacc_laender"), ("5", "gacc_export"), ("6", "gacc_import")):
                    url = v.get(nr, {}).get(zeitraum)
                    if not url:
                        continue
                    inhalt = a.get(url)
                    for k, w in verarbeiten(self.qid, self.cfg, art, {"zeitraum": zeitraum}, inhalt, "html").items():
                        summe[k] = summe.get(k, 0) + w
        return summe


class Tsmc:
    def __init__(self, qid, cfg):
        self.qid, self.cfg = qid, cfg

    def bereit(self):
        return None

    def _seite(self, a: Abruf, jahr: int) -> bytes:
        return a.get(self.cfg["url"].format(jahr=jahr))

    def neu_da(self, a: Abruf, erwartet: str) -> bool:
        zeilen, _ = E.tsmc(self._seite(a, int(erwartet[:4])).decode("utf-8", "ignore"), int(erwartet[:4]))
        return any(z["zeitraum"] == erwartet for z in zeilen)

    def lauf(self, a: Abruf, voll: bool) -> dict:
        heute = date.today()
        jahre = range(heute.year - int(self.cfg.get("jahre") or 3) + 1, heute.year + 1) if voll else sorted({heute.year, (heute - timedelta(days=45)).year})
        summe = {}
        for jahr in jahre:
            for k, w in verarbeiten(self.qid, self.cfg, "tsmc", {"jahr": jahr}, self._seite(a, jahr), "html").items():
                summe[k] = summe.get(k, 0) + w
        return summe


class Portwatch:
    def __init__(self, qid, cfg):
        self.qid, self.cfg = qid, cfg

    def bereit(self):
        return None

    def _letzter_tag(self, a: Abruf) -> str | None:
        inhalt = a.get(self.cfg["url"], {"where": "1=1", "outFields": "date", "orderByFields": "date DESC", "resultRecordCount": 1, "f": "json"},
                       kopf={"User-Agent": "Vinter/1.0"}, bedingt=True)
        if inhalt is None:
            return None
        f = (json.loads(inhalt).get("features") or [{}])[0].get("attributes", {})
        d = f.get("date")
        return pd.to_datetime(d, unit="ms").date().isoformat() if isinstance(d, (int, float)) else str(d)[:10] if d else None

    def neu_da(self, a: Abruf, erwartet: str | None) -> bool:
        letzt = self._letzter_tag(a)
        if not letzt:
            return False
        stand = neuester(self.qid)
        # eine neue vollständige Woche ist da, wenn der letzte Tag mindestens 6 Tage nach dem Start der gespeicherten Woche + 7 liegt
        return not stand or date.fromisoformat(letzt) >= date.fromisoformat(stand) + timedelta(days=13)

    def lauf(self, a: Abruf, voll: bool) -> dict:
        stand = None if voll else neuester(self.qid)
        seit = (date.fromisoformat(stand) - timedelta(days=14)) if stand else date.today() - timedelta(days=int(self.cfg.get("seit_tage") or 760))
        seiten, offset = [], 0
        while True:
            inhalt = a.get(self.cfg["url"], {"where": f"date >= DATE '{seit.isoformat()}'", "outFields": "date,portid,n_total,n_tanker,n_container",
                                             "orderByFields": "date", "resultOffset": offset, "resultRecordCount": 2000, "f": "json"}, kopf={"User-Agent": "Vinter/1.0"})
            s = json.loads(inhalt)
            seiten.append(s)
            teile = s.get("features") or []
            if not s.get("exceededTransferLimit") or not teile:
                break
            offset += len(teile)
        return verarbeiten(self.qid, self.cfg, "portwatch", {"seit": seit.isoformat()}, json.dumps(seiten).encode(), "json")


ARTEN = {"census": Census, "comtrade": Comtrade, "gacc": Gacc, "tsmc": Tsmc, "portwatch": Portwatch}


def quelle(qid: str):
    cfg = (konfig().get("quellen") or {}).get(qid) or {}
    k = ARTEN.get(cfg.get("art"))
    return (k(qid, cfg), cfg) if k and cfg.get("aktiv", True) else (None, cfg)


def erwartet(cfg: dict, heute: date | None = None) -> str | None:
    """Welcher Zeitraum im aktuellen Veröffentlichungsfenster kommen sollte."""
    heute = heute or date.today()
    if "verzug_monate" not in cfg:
        return None
    if heute.month in (cfg.get("auslassen_monate") or []):
        return None
    return _monat(heute, int(cfg["verzug_monate"]))


def im_fenster(cfg: dict, jetzt: datetime | None = None) -> bool:
    jetzt = jetzt or datetime.now()
    k = cfg.get("kalender") or {}
    von, bis = (k.get("uhrzeit") or ["00:00", "23:59"])
    if not (von <= jetzt.strftime("%H:%M") <= bis):
        return False
    if "tage" in k:
        return k["tage"][0] <= jetzt.day <= k["tage"][1] and jetzt.month not in (cfg.get("auslassen_monate") or [])
    if "wochentag" in k:
        return jetzt.weekday() == int(k["wochentag"])
    if "monatlich" in k:
        return jetzt.day >= int(k["monatlich"])
    return False


def naechster_termin(cfg: dict, qid: str, heute: date | None = None) -> str | None:
    """Beginn des nächsten Veröffentlichungsfensters, in dem noch etwas erwartet wird."""
    heute = heute or date.today()
    k = cfg.get("kalender") or {}
    for d in range(0, 70):
        tag = heute + timedelta(days=d)
        if "tage" in k:
            if not (k["tage"][0] <= tag.day <= k["tage"][1]) or tag.month in (cfg.get("auslassen_monate") or []):
                continue
            e = erwartet(cfg, tag)
            if e and hat_zeitraum(qid, e):
                continue
        elif "wochentag" in k:
            if tag.weekday() != int(k["wochentag"]) or (d == 0 and datetime.now().strftime("%H:%M") > k["uhrzeit"][1]):
                continue
        elif "monatlich" in k:
            if tag.day != int(k["monatlich"]) and d:
                continue
            letzt = S.zustand(f"plan:{qid}", {}).get("letzter_erfolg")
            if letzt and letzt[:7] == tag.strftime("%Y-%m"):
                continue
        return f"{tag.isoformat()} {(k.get('uhrzeit') or ['00:00'])[0]}"
    return None
