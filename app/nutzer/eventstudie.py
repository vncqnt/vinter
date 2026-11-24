"""Event Study, Benutzerschicht: eigene Events, CSV-Import, Vorschläge aus dem Newsfeed, gespeicherte Studien.

Gespeichert pro Benutzer in events.json:
- events: eigene Ereignisse (beim ersten Öffnen die Zoll-Ereignisse 2025 aus vorlagen/markt/events_start.yaml,
  markiert mit "bitte prüfen")
- eigene_kategorien: selbst angelegte Kategorien mit Untertypen
- erledigt: Geschichten-IDs aus dem Newsfeed, die schon übernommen oder verworfen sind
- studien: gespeicherte Studien (Versionen mit Filtern, Reihen, Fenster); sie rechnen bei jedem Öffnen neu,
  neue Events kommen also automatisch dazu
- tests: wie viele verschiedene Varianten heute gerechnet wurden (Hinweis auf Zufallsmuster)
Automatische Events (nicht gespeichert, immer aktuell): CPI- und Arbeitsmarktberichte (BLS-Archiv, 8:30 New York)
und Fed-Entscheidungen (FOMC-Kalender, 14:00 New York) seit 2015, Überraschung aus der 2-jährigen Rendite am Tag.
"""
import csv
import hashlib
import re
import io
import json
import secrets
import threading
from datetime import date

import numpy as np
import pandas as pd
import yaml

from ..benutzer import Benutzer
from ..konfig import VORLAGEN
from ..markt import eventstudie as es

_sperre = threading.Lock()
MAX_EVENTS = 5000
MAX_STUDIEN = 50
LISTEN = ("region", "akteur")
EINZELN = ("richtung", "ueberraschung", "staerke", "status")
VIELE_TESTS = 20


# ---------- Speicher ----------

def _datei(b: Benutzer):
    return b.datei("events.json")


def _lesen(b: Benutzer) -> dict:
    d = _datei(b)
    if d.exists():
        try:
            return json.loads(d.read_text(encoding="utf-8"))
        except Exception:
            pass
    start = []
    vorlage = VORLAGEN / "markt" / "events_start.yaml"
    if vorlage.exists():
        for e in yaml.safe_load(vorlage.read_text(encoding="utf-8")) or []:
            try:
                start.append({**pruefen(e), "pruefen": True, "herkunft": "Startdatensatz"})
            except ValueError:
                continue
    daten = {"events": start, "eigene_kategorien": {}, "erledigt": [], "studien": [], "tests": {}}
    _schreiben(b, daten)
    return daten


def _schreiben(b: Benutzer, daten: dict) -> None:
    d = _datei(b)
    d.parent.mkdir(parents=True, exist_ok=True)
    zw = d.with_suffix(".tmp")
    zw.write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")
    zw.replace(d)


def alle_kategorien(b: Benutzer) -> dict:
    k = es.kategorien()
    kat = dict(k.get("kategorien") or {})
    for schl, v in (_lesen(b).get("eigene_kategorien") or {}).items():
        kat[schl] = {**v, "eigen": True}
    return {"kategorien": kat, "merkmale": k.get("merkmale") or {}, "marktlage": k.get("marktlage") or {}}


def _text(x, laenge: int) -> str:
    return str(x or "").strip()[:laenge]


def _liste(x) -> list[str]:
    if isinstance(x, str):
        x = [t for t in x.replace(";", ",").split(",")]
    return [_text(t, 40) for t in (x or []) if _text(t, 40)][:12]


def pruefen(e: dict, kategorien: dict | None = None) -> dict:
    """Event säubern. Fehler als ValueError mit verständlichem Text."""
    titel = _text(e.get("titel"), 200)
    if not titel:
        raise ValueError("Titel fehlt.")
    try:
        zeit = es.zeit_lesen(str(e.get("zeit")), e.get("zone"))
    except Exception:
        raise ValueError("Zeitpunkt nicht lesbar (Beispiel: 2025-04-02T16:00:00-04:00 oder 2025-04-02 16:00).")
    kat = _text(e.get("kategorie"), 40)
    if not kat:
        raise ValueError("Kategorie fehlt.")
    if kategorien is not None and kat not in kategorien:
        # auch der Anzeigename ist erlaubt
        treffer = [k for k, v in kategorien.items() if v.get("name", "").lower() == kat.lower()]
        if not treffer:
            raise ValueError(f"Unbekannte Kategorie „{kat}“.")
        kat = treffer[0]
    unter = _text(e.get("untertyp"), 40)
    if kategorien is not None and unter:
        u = kategorien[kat].get("untertypen") or {}
        if unter not in u:
            treffer = [k for k, v in u.items() if str(v).lower() == unter.lower()]
            unter = treffer[0] if treffer else unter
    m = e.get("merkmale") or {}
    merkmale = {k: _liste(m.get(k)) for k in LISTEN}
    merkmale.update({k: _text(m.get(k), 30) or None for k in EINZELN})
    return {"id": e.get("id") or secrets.token_hex(5), "zeit": zeit.isoformat(), "titel": titel, "kategorie": kat,
            "untertyp": unter or None, "merkmale": merkmale, "tags": _liste(e.get("tags")),
            "link": _text(e.get("link"), 500) or None, "notiz": _text(e.get("notiz"), 1000) or None,
            "pruefen": bool(e.get("pruefen")), "herkunft": _text(e.get("herkunft"), 40) or "manuell"}


def events(b: Benutzer) -> list[dict]:
    return _lesen(b)["events"]


def event_speichern(b: Benutzer, e: dict) -> dict:
    with _sperre:
        d = _lesen(b)
        sauber = pruefen(e, alle_kategorien(b)["kategorien"])
        liste = [x for x in d["events"] if x["id"] != sauber["id"]]
        alt = next((x for x in d["events"] if x["id"] == sauber["id"]), None)
        if alt and "herkunft" not in e:
            sauber["herkunft"] = alt.get("herkunft", "manuell")
        if len(liste) >= MAX_EVENTS:
            raise ValueError(f"Höchstens {MAX_EVENTS} Events.")
        liste.append(sauber)
        d["events"] = sorted(liste, key=lambda x: x["zeit"])
        _schreiben(b, d)
        return sauber


def event_loeschen(b: Benutzer, eid: str) -> bool:
    with _sperre:
        d = _lesen(b)
        vorher = len(d["events"])
        d["events"] = [x for x in d["events"] if x["id"] != eid]
        _schreiben(b, d)
        return len(d["events"]) < vorher


def kategorie_anlegen(b: Benutzer, name: str, untertypen: list[str]) -> dict:
    name = _text(name, 40)
    if not name:
        raise ValueError("Name fehlt.")
    schl = "eigen_" + "".join(c for c in name.lower() if c.isalnum())[:20]
    with _sperre:
        d = _lesen(b)
        u = {"".join(c for c in t.lower() if c.isalnum())[:20] or f"u{i}": _text(t, 40) for i, t in enumerate(untertypen or []) if _text(t, 40)}
        d.setdefault("eigene_kategorien", {})[schl] = {"name": name, "untertypen": u}
        _schreiben(b, d)
    return {"schluessel": schl}


CSV_SPALTEN = ["zeit", "titel", "kategorie", "untertyp", "richtung", "region", "akteur", "ueberraschung", "staerke",
               "status", "tags", "link", "notiz"]


def csv_import(b: Benutzer, text: str) -> dict:
    """CSV mit Kopfzeile (Spalten siehe CSV_SPALTEN, Trennzeichen , oder ;). Listen mit | trennen."""
    probe = text[:2000]
    trenner = ";" if probe.count(";") > probe.count(",") else ","
    leser = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=trenner)
    kat = alle_kategorien(b)["kategorien"]
    neu, fehler = [], []
    for nr, z in enumerate(leser, start=2):
        z = {(k or "").strip().lower(): (v or "").strip() for k, v in z.items()}
        try:
            e = pruefen({"zeit": z.get("zeit"), "titel": z.get("titel"), "kategorie": z.get("kategorie"), "untertyp": z.get("untertyp"),
                         "merkmale": {"richtung": z.get("richtung"), "region": z.get("region", "").replace("|", ","),
                                      "akteur": z.get("akteur", "").replace("|", ","), "ueberraschung": z.get("ueberraschung"),
                                      "staerke": z.get("staerke"), "status": z.get("status")},
                         "tags": z.get("tags", "").replace("|", ","), "link": z.get("link"), "notiz": z.get("notiz"),
                         "herkunft": "CSV", "pruefen": False}, kat)
            neu.append(e)
        except ValueError as f:
            fehler.append(f"Zeile {nr}: {f}")
        if nr > MAX_EVENTS:
            break
    if neu:
        with _sperre:
            d = _lesen(b)
            schon = {(x["zeit"], x["titel"]) for x in d["events"]}
            neu = [e for e in neu if (e["zeit"], e["titel"]) not in schon]
            d["events"] = sorted(d["events"] + neu, key=lambda x: x["zeit"])[-MAX_EVENTS:]
            _schreiben(b, d)
    return {"neu": len(neu), "fehler": fehler[:50]}


# ---------- Automatische Events (Makro-Termine) ----------

def auto_events() -> list[dict]:
    from ..markt.abruf import fred
    from ..markt.eventbewegung import vergangene_termine
    from ..markt.fed_tage import entscheidungen
    aus = []
    try:
        zwei = fred("DGS2").dropna()
    except Exception:
        zwei = pd.Series(dtype=float)

    def zwei_j(tag: str):
        ts = pd.Timestamp(tag)
        vor, am = zwei[zwei.index < ts], zwei[zwei.index >= ts]
        if vor.empty or am.empty or (am.index[0] - ts).days > 3:
            return None
        return round(float(am.iloc[0] - vor.iloc[-1]) * 100, 1)

    def merkmale(bp, akteur):
        ueb = None if bp is None else "überraschend" if abs(bp) >= 5 else "erwartet"
        return {"richtung": "neutral", "region": ["USA"], "akteur": akteur, "ueberraschung": ueb, "staerke": None, "status": "bestätigt"}

    try:
        termine = vergangene_termine()
    except Exception:
        termine = {}
    for art, unter, titel in (("cpi", "cpi", "US-Inflation (CPI)"), ("nfp", "arbeitsmarkt", "US-Arbeitsmarktbericht")):
        for tag in termine.get(art, []):
            if tag < "2015-01-01":
                continue
            bp = zwei_j(tag)
            tags = [] if bp is None else ["heißer"] if bp >= 5 else ["kühler"] if bp <= -5 else ["wie erwartet"]
            aus.append({"id": f"auto-{art}-{tag}", "zeit": es.zeit_lesen(f"{tag}T08:30:00").isoformat(), "titel": titel,
                        "kategorie": "makrodaten", "untertyp": unter, "merkmale": merkmale(bp, ["BLS"]), "tags": tags,
                        "link": None, "notiz": None if bp is None else f"2-jährige Rendite am Tag {bp:+.0f} Bp.",
                        "pruefen": False, "herkunft": "automatisch"})
    try:
        fed = entscheidungen()
    except Exception:
        fed = []
    for t in fed:
        bp = zwei_j(t["datum"])
        aus.append({"id": f"auto-fed-{t['datum']}", "zeit": es.zeit_lesen(f"{t['datum']}T14:00:00").isoformat(),
                    "titel": f"Fed-Entscheid: {t['entscheidung']}", "kategorie": "geldpolitik", "untertyp": "fed_entscheid",
                    "merkmale": merkmale(bp, ["Fed"]), "tags": [t["entscheidung"]], "link": None,
                    "notiz": None if bp is None else f"2-jährige Rendite am Tag {bp:+.0f} Bp.", "pruefen": False, "herkunft": "automatisch"})
    return aus


# ---------- Vorschläge aus dem Newsfeed ----------

ZUORDNUNG = {"zoelle_handel": "handel", "notenbank": "geldpolitik", "konjunktur": "makrodaten", "geopolitik": "geopolitik",
             "uebernahme": "unternehmen", "prognose": "unternehmen", "geschaeftszahlen": "unternehmen"}
DEESKALATION = ("pause", "delay", "postpone", "exempt-", "suspend", "lower tariff", "cut tariff", "reduce", "deal", "agreement",
                "truce", "ceasefire", "extend", "roll back", "rollback", "waive", "ease", "eases", "lift", "lifts", "aussetz-", "abkommen",
                "waffenstillstand", "senkt")
ESKALATION = ("impose", "raise", "raises", "hike", "threat-", "new tariff", "retaliat-", "ban", "restrict-", "strike", "attack",
              "escalat-", "sanction", "invade", "missile", "100%", "50%", "droht", "verhängt", "erhöht")


def _untertyp(ereignis: str, titel: str) -> str | None:
    t = titel.lower()
    if ereignis == "zoelle_handel":
        return "exportkontrolle" if "export" in t else "sanktionen" if "sanction" in t else "handelsdeal" if ("deal" in t or "agreement" in t) else "zoelle"
    if ereignis == "notenbank":
        if not FED_WOERTER.search(titel):
            return "andere_notenbank"
        return "fed_entscheid" if any(w in t for w in ("fomc", "rate decision", "holds rates", "cuts rates", "raises rates")) else "fed_rede"
    if ereignis == "konjunktur":
        return "cpi" if any(w in t for w in ("cpi", "inflation")) else "arbeitsmarkt" if any(w in t for w in ("payroll", "jobless", "unemployment", "jobs")) else "bip" if ("gdp" in t or "bip" in t) else "pmi" if ("pmi" in t or "ism" in t) else None
    if ereignis == "geopolitik":
        return "waffenstillstand" if any(w in t for w in ("ceasefire", "truce", "waffenstillstand")) else "krieg"
    return {"uebernahme": "ma", "prognose": "guidance", "geschaeftszahlen": "earnings"}.get(ereignis)


def _woerter(liste) -> "re.Pattern":
    """Ganze Wörter (mit Endungen wie -s, -ed, -ing), damit "ban" nicht in "Bank" steckt und "lift" nicht in "uplift".
    Ein Eintrag mit "-" am Ende ist ein Wortstamm ("retaliat-" = retaliate, retaliation …)."""
    teile = [re.escape(w[:-1]) if w.endswith("-") else re.escape(w) + r"(?:s|es|d|ed|ned|ing|ning)?(?![a-zäöü])" for w in liste]
    return re.compile(r"(?<![a-zäöü])(?:" + "|".join(teile) + ")", re.I)


_DE, _ES = _woerter(DEESKALATION), _woerter(ESKALATION)
# Richtung ergibt nur bei Konflikt-Themen Sinn (Zölle, Krieg, Drohungen), bei Notenbanken oder Firmen nicht
MIT_RICHTUNG = ("handel", "geopolitik", "aussagen", "energie")
# Ohne eines dieser Wörter ist ein "Handel"-Treffer meist ein Irrtum ("fiduciary duties")
HANDEL_PFLICHT = _woerter(("tariff-", "trade-", "export-", "import-", "sanction-", "duties on", "duty on", "levy", "levies", "zoll-", "zöll-",
                           "handel-", "section 232", "section 301", "de minimis", "chip-", "embargo"))
WERBUNG = re.compile(r"market (?:report|size|research|outlook|analysis)|\bCAGR\b|webinar|forecast to 20\d\d|press release distribution", re.I)
FED_WOERTER = _woerter(("fed", "fomc", "powell", "federal reserve", "jefferson", "waller", "bowman", "barr", "cook", "goolsbee",
                        "kashkari", "logan", "hammack", "musalem", "daly", "bostic", "williams", "schmid", "miran"))


def richtung_raten(titel: str, kategorie: str = "handel") -> str:
    if kategorie not in MIT_RICHTUNG:
        return "neutral"
    d, e = len(_DE.findall(titel)), len(_ES.findall(titel))
    return "Deeskalation" if d > e else "Eskalation" if e > d else "neutral"


def vorschlaege(b: Benutzer, tage: int = 7, ab_score: int = 45) -> list[dict]:
    from ..markt.nachrichten import geschichten
    erledigt = set(_lesen(b).get("erledigt") or [])
    aus = []
    for s in geschichten.liste(b.name, stunden=tage * 24):
        kat = ZUORDNUNG.get(s.get("ereignis") or "")
        if not kat or (s.get("score") or 0) < ab_score or s["id"] in erledigt or WERBUNG.search(s["titel"]):
            continue
        if kat == "handel" and not HANDEL_PFLICHT.search(s["titel"]):
            continue
        if kat == "unternehmen" and not s.get("ticker"):
            continue  # Übernahmen und Zahlen nur bei börsennotierten Firmen, die der Feed erkannt hat
        erste = s["meldungen"][0]
        aus.append({"story": s["id"], "zeit": s["erstes"], "titel": s["titel"], "score": s["score"], "quellen": s["quellen"],
                    "link": erste.get("link"), "kategorie": kat, "untertyp": _untertyp(s["ereignis"], s["titel"]),
                    "merkmale": {"richtung": richtung_raten(s["titel"], kat), "region": s.get("laender") or [],
                                 "akteur": s.get("stellen") or [], "ueberraschung": None, "staerke": "groß" if s["score"] >= 70 else "mittel",
                                 "status": "bestätigt" if s.get("bestaetigt") else "unbestätigt"},
                    "tags": s.get("ticker") or []})
    aus.sort(key=lambda x: x["zeit"], reverse=True)
    return aus[:60]


def vorschlag_erledigen(b: Benutzer, story: int, event: dict | None) -> dict | None:
    gespeichert = event_speichern(b, {**event, "herkunft": "Newsfeed"}) if event else None
    with _sperre:
        d = _lesen(b)
        d["erledigt"] = (d.get("erledigt") or [])[-2000:] + [int(story)]
        _schreiben(b, d)
    return gespeichert


# ---------- Studien ----------

def studien(b: Benutzer) -> list[dict]:
    return _lesen(b).get("studien") or []


def studie_speichern(b: Benutzer, s: dict) -> dict:
    name = _text(s.get("name"), 60)
    if not name:
        raise ValueError("Name fehlt.")
    eintrag = {"id": s.get("id") or secrets.token_hex(4), "name": name, "anfrage": s.get("anfrage") or {}}
    with _sperre:
        d = _lesen(b)
        liste = [x for x in d.get("studien") or [] if x["id"] != eintrag["id"]]
        d["studien"] = (liste + [eintrag])[-MAX_STUDIEN:]
        _schreiben(b, d)
    return eintrag


def studie_loeschen(b: Benutzer, sid: str) -> None:
    with _sperre:
        d = _lesen(b)
        d["studien"] = [x for x in d.get("studien") or [] if x["id"] != sid]
        _schreiben(b, d)


def _test_zaehlen(b: Benutzer, anfrage: dict) -> int:
    schluessel = hashlib.sha1(json.dumps([anfrage.get("versionen"), anfrage.get("reihen"), anfrage.get("stufe"), anfrage.get("modus")],
                                         sort_keys=True).encode()).hexdigest()[:12]
    heute = date.today().isoformat()
    with _sperre:
        d = _lesen(b)
        t = d.get("tests") or {}
        liste = t.get(heute) or []
        if schluessel not in liste:
            liste.append(schluessel)
        d["tests"] = {heute: liste[-500:]}
        _schreiben(b, d)
    return len(liste)


# ---------- Filtern und Auswerten ----------

def _trifft(wert, auswahl: list) -> bool:
    if not auswahl:
        return True
    werte = wert if isinstance(wert, list) else [wert]
    return any(w in auswahl for w in werte if w)


def filtern(liste: list[dict], f: dict) -> list[dict]:
    """f: Listen je Merkmal (leer = alle), von/bis (Datum), nur_geprueft, mit_auto."""
    aus = []
    for e in liste:
        m, lage = e.get("merkmale") or {}, e.get("lage") or {}
        if f.get("von") and e["zeit"][:10] < f["von"] or f.get("bis") and e["zeit"][:10] > f["bis"]:
            continue
        if f.get("nur_geprueft") and e.get("pruefen"):
            continue
        if f.get("ohne_auto") and e.get("herkunft") == "automatisch":
            continue
        if f.get("ids") and e["id"] not in f["ids"]:
            continue
        if not (_trifft(e["kategorie"], f.get("kategorien")) and _trifft(f"{e['kategorie']}.{e.get('untertyp')}", f.get("untertypen"))
                and all(_trifft(m.get(k), f.get(k)) for k in (*LISTEN, *EINZELN))
                and _trifft(e.get("zeitpunkt"), f.get("zeitpunkt")) and _trifft(e.get("tags"), f.get("tags"))
                and _trifft(lage.get("trend"), f.get("trend")) and _trifft(lage.get("vix"), f.get("vix")) and _trifft(lage.get("hoch"), f.get("hoch"))):
            continue
        aus.append(e)
    return aus


def pool(b: Benutzer) -> list[dict]:
    """Eigene und automatische Events mit Zeitpunkt-Art und Marktlage."""
    from ..markt.abruf import gecacht, STUNDE
    liste = events(b) + gecacht("eventstudie:auto", 6 * STUNDE, auto_events)
    try:
        spx, vix = es.tagesreihe("spx"), es.tagesreihe("vix")
    except Exception:
        spx = vix = None
    regeln = es.kategorien().get("marktlage") or {}
    aus = []
    for e in liste:
        z = es.zeit_lesen(e["zeit"])
        aus.append({**e, "zeitpunkt": es.zeitpunkt(z), "lage": es.marktlage(spx, vix, z, regeln) if spx is not None else {}})
    return sorted(aus, key=lambda x: x["zeit"])


def analyse(b: Benutzer, anfrage: dict) -> dict:
    versionen = (anfrage.get("versionen") or [{"name": "Alle", "filter": {}}])[:4]
    reihen = [r for r in (anfrage.get("reihen") or ["spx"])][:4]
    minuten = anfrage.get("modus") == "minute"
    von, bis = int(anfrage.get("von", -30 if minuten else -5)), int(anfrage.get("bis", 120 if minuten else 10))
    grenze = 600 if minuten else 120
    von, bis = max(-grenze, min(0, von)), min(grenze, max(1, bis))
    stufe = max(von, min(bis, int(anfrage.get("stufe", 5 if minuten else 1))))
    alle = pool(b)
    gruppen = [{"name": _text(v.get("name"), 40) or f"Version {i + 1}", "events": filtern(alle, v.get("filter") or {})} for i, v in enumerate(versionen)]
    for g in gruppen:
        for e in g["events"]:
            e["zeit_ts"] = es.zeit_lesen(e["zeit"])
    ergebnis = []
    for schl in reihen:
        try:
            info = es.beschreibung(schl)
            if minuten:
                from ..markt import minuten as mdaten
                reihe = mdaten.reihe(info)
            else:
                reihe = es.tagesreihe(schl)
        except Exception as f:
            ergebnis.append({"schluessel": schl, "name": schl, "fehler": f"Reihe nicht verfügbar ({type(f).__name__})."})
            continue
        if reihe is None or len(reihe) < 30:
            ergebnis.append({**info, "fehler": "Für diese Reihe gibt es noch zu wenige Daten." + (" Minutendaten sammelt Vinter erst seit dem Einbau der Event Study." if minuten else "")})
            continue
        teile = []
        for g in gruppen:
            r = es.auswerten(reihe, [{"id": e["id"], "zeit": e["zeit_ts"]} for e in g["events"]], von, bis, info["einheit"], stufe, minuten)
            teile.append({"name": g["name"], **r})
        # Vergleich jeder weiteren Version mit Version 1
        for t in teile[1:]:
            a = np.array([x for x in teile[0]["werte"] if x is not None], dtype=float)
            c = np.array([x for x in t["werte"] if x is not None], dtype=float)
            t["gegen_erste"] = {"differenz": None if not len(a) or not len(c) else round(float(c.mean() - a.mean()), 3),
                                "p": es.p_permutation(c, a)}
        ergebnis.append({**info, "einheit_text": es.EINHEIT[info["einheit"]], "versionen": teile,
                         "stand": reihe.index[-1].isoformat()})
    anzeigen = {e["id"]: {k: e.get(k) for k in ("titel", "zeit", "kategorie", "untertyp", "merkmale", "tags", "link", "notiz", "pruefen", "herkunft", "zeitpunkt", "lage")}
                for g in gruppen for e in g["events"]}
    tests = _test_zaehlen(b, anfrage)
    return {"reihen": ergebnis, "versionen": [{"name": g["name"], "n": len(g["events"])} for g in gruppen], "events": anzeigen,
            "von": von, "bis": bis, "stufe": stufe, "modus": "minute" if minuten else "tag", "tests_heute": tests,
            "viele_tests": tests >= VIELE_TESTS, "wenig": es.WENIG, "ziehungen": es.ZIEHUNGEN}


def uebersicht(b: Benutzer) -> dict:
    alle = pool(b)
    return {"events": [{k: e.get(k) for k in ("id", "zeit", "titel", "kategorie", "untertyp", "merkmale", "tags", "link", "notiz", "pruefen", "herkunft", "zeitpunkt", "lage")} for e in alle],
            **alle_kategorien(b), "studien": studien(b), "reihen": es.katalog(),
            "auto": sum(e.get("herkunft") == "automatisch" for e in alle)}


def marker(b: Benutzer, von: str | None = None) -> list[dict]:
    """Für die Charts: eigene Events (ohne automatische Makro-Termine) mit Datum von Tag 0."""
    aus = []
    for e in events(b):
        z = es.zeit_lesen(e["zeit"])
        if von and e["zeit"][:10] < von:
            continue
        aus.append({"id": e["id"], "zeit": e["zeit"], "tag0": es.tag_null_datum(z).strftime("%Y-%m-%d"), "titel": e["titel"],
                    "kategorie": e["kategorie"], "richtung": (e.get("merkmale") or {}).get("richtung")})
    return aus
