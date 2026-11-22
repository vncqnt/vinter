"""Zahlen-Auswertung: eingefügte Quartalsmitteilungen auswerten, mit Erwartung und Vorperioden vergleichen
und in Quartals-Ordnern ablegen.

Ablauf beim Anlegen:
1. Stufe 1 (sofort, ohne KI): app/markt/pressemitteilung.py liest Kennzahlen, Ausblick und Ton aus dem Text.
2. Vergleich: Konsens (Yahoo) für genau dieses Quartal, Vorquartal und Vorjahresquartal aus den Yahoo-Quartalszahlen.
   Weil Yahoo nach dem Termin schon das nächste Quartal als "laufend" führt, wird der passende Konsens über den
   Vorjahreswert gesucht: Er muss zum Umsatz des Vorjahresquartals passen (siehe _konsens()).
   Der Vergleich wird beim Anlegen gespeichert, damit er später gleich bleibt.
3. Stufe 2 (nur wenn KI_ANBIETER gesetzt ist, z. B. Ollama auf dem NUC): ergänzt im Hintergrund fehlende Zahlen
   und Kernaussagen. Jede KI-Zahl braucht ein wörtliches Zitat aus dem Text, sonst wird sie verworfen.

Gespeichert pro Benutzer: zahlen.json (Liste der Auswertungen) und zahlen/<id>.txt (der eingefügte Text).
"""
import json
import queue
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta

from ..benutzer import Benutzer
from ..konfig import BENUTZER_DATEN
from ..markt import ki, pressemitteilung as pm
from ..markt.earnings_sheet import konsens_archiv, sheet

MAX_EINTRAEGE = 300
MAX_TEXT = 400_000
MIN_TEXT = 200
# Ab welcher Abweichung von der Erwartung eine Zahl als "darüber" oder "darunter" gilt (in Prozent)
SCHWELLE = {"umsatz": 1.0, "eps": 2.0, "ausblick": 1.0}
REIHENFOLGE = ["umsatz", "eps_adj", "eps", "netto", "operativ", "brutto_marge", "op_marge", "fcf", "ocf", "capex", "rueckkauf", "dividende"]
_sperre = threading.Lock()


class ZahlenFehler(Exception):
    """Die Eingabe passt nicht, der Text wird dem Nutzer angezeigt."""


# ---- Speicher ----

def _datei(b: Benutzer):
    return b.datei("zahlen.json")


def _text_datei(b: Benutzer, kennung: str):
    return b.datei("zahlen", f"{kennung}.txt")


def _lesen(b: Benutzer) -> list[dict]:
    d = _datei(b)
    if not d.exists():
        return []
    try:
        return json.loads(d.read_text(encoding="utf-8"))
    except Exception:
        return []


def _schreiben(b: Benutzer, liste: list[dict]) -> None:
    d = _datei(b)
    d.parent.mkdir(parents=True, exist_ok=True)
    zwischen = d.with_suffix(".tmp")  # erst fertig schreiben, dann ersetzen
    zwischen.write_text(json.dumps(liste, ensure_ascii=False), encoding="utf-8")
    zwischen.replace(d)


def _aendern(b: Benutzer, kennung: str, aenderung) -> dict:
    with _sperre:
        liste = _lesen(b)
        e = next((x for x in liste if x["id"] == kennung), None)
        if not e:
            raise ZahlenFehler("Diese Auswertung gibt es nicht mehr.")
        aenderung(e)
        _schreiben(b, liste)
        return e


def _gueltige_id(kennung: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{12}", kennung or ""):
        raise ZahlenFehler("Ungültige Auswertung.")
    return kennung


# ---- Ordner ----

def _ordner_name(o: str) -> str:
    m = re.fullmatch(r"(\d{4})-Q([1-4])", o or "")
    return f"Q{m.group(2)} {m.group(1)}" if m else (o or "Ohne Ordner")


def _ordner_aus_datum(quartalsende: date) -> str:
    """Kalenderquartal, in dem die meisten Monate des Firmenquartals liegen (Mitte = Ende minus 45 Tage).
    So landet NVIDIAs Quartal bis 25. Oktober (August bis Oktober) wie Apples Quartal bis 30. September in Q3."""
    mitte = quartalsende - timedelta(days=45)
    return f"{mitte.year}-Q{(mitte.month - 1) // 3 + 1}"


# ---- Vergleich mit Yahoo ----

def _nah(a, b, toleranz) -> bool:
    return a is not None and b is not None and b != 0 and abs(a / b - 1) <= toleranz


def _q(quartal: dict, key: str):
    """Ein Wert aus einem Yahoo-Quartal in derselben Form wie die Kennzahlen der Mitteilung."""
    if not quartal:
        return None
    u = quartal.get("umsatz")
    if key == "brutto_marge":
        return quartal["brutto"] / u * 100 if quartal.get("brutto") is not None and u else None
    if key == "op_marge":
        return quartal["operativ"] / u * 100 if quartal.get("operativ") is not None and u else None
    if key in ("eps", "eps_adj"):
        return quartal.get("eps")
    return quartal.get(key)


def _konsens(ticker: str, s: dict, werte: dict, vj_quartal: dict | None) -> dict | None:
    """Konsens-Stand, der zu diesem Quartal gehört: aus dem Archiv oder der laufende von Yahoo.

    Erkennung über den Vorjahreswert: Yahoo nennt beim Konsens auch den Umsatz des Vorjahresquartals.
    Passt der zum Vorjahresquartal der Mitteilung (aus den Yahoo-Quartalszahlen oder aus Umsatz und genanntem
    Wachstum zurückgerechnet), ist es das richtige Quartal. Sonst zählt der Termin (höchstens 12 Tage her)."""
    kandidaten = []
    termin = (s.get("termin") or {}).get("datum")
    aktuell = {"eps": s["eps"].get("0q"), "umsatz": s["umsatz"].get("0q"), "eps_naechstes": s["eps"].get("+1q"),
               "umsatz_naechstes": s["umsatz"].get("+1q"), "eps_jahr": s["eps"].get("0y"), "umsatz_jahr": s["umsatz"].get("0y"),
               "eps_folgejahr": s["eps"].get("+1y"), "umsatz_folgejahr": s["umsatz"].get("+1y"), "stand": s.get("stand")}
    if termin:
        kandidaten.append((termin, aktuell, "aktuell"))
    for tag, k in sorted(konsens_archiv(ticker).items(), reverse=True):
        kandidaten.append((tag, k, "archiv"))

    umsatz = (werte.get("umsatz") or {}).get("wert")
    vj_umsatz = _q(vj_quartal, "umsatz")
    if vj_umsatz is None and umsatz and (werte.get("umsatz") or {}).get("vj_prozent") is not None:
        vj_umsatz = umsatz / (1 + werte["umsatz"]["vj_prozent"] / 100)
    if vj_umsatz is None and (werte.get("umsatz") or {}).get("vj_wert"):
        vj_umsatz = werte["umsatz"]["vj_wert"]
    vj_eps = _q(vj_quartal, "eps")

    for tag, k, art in kandidaten:
        u = k.get("umsatz") or {}
        if _nah(u.get("vorjahr"), vj_umsatz, 0.03):
            return {**k, "termin": tag, "art": art, "erkannt": "Vorjahresumsatz passt"}
    for tag, k, art in kandidaten:
        e = k.get("eps") or {}
        if vj_umsatz is None and _nah(e.get("vorjahr"), vj_eps, 0.03):
            return {**k, "termin": tag, "art": art, "erkannt": "Vorjahres-EPS passt"}
    # Ohne Vorjahreswert zum Prüfen nur über den Termin: gespeicherter Stand eines Termins der letzten 12 Tage,
    # oder Yahoos laufendes Quartal, solange dessen Termin heute oder noch in der Zukunft liegt
    if vj_umsatz is None and vj_eps is None:
        heute = date.today()
        for tag, k, art in kandidaten:
            try:
                abstand = (heute - date.fromisoformat(tag)).days
            except ValueError:
                continue
            if (art == "archiv" and 0 <= abstand <= 12) or (art == "aktuell" and -4 <= abstand <= 0):
                return {**k, "termin": tag, "art": art, "erkannt": "Termin passt"}
    return None


def vergleich(ticker: str, ergebnis: dict) -> dict:
    """Erwartung, Vorquartal und Vorjahresquartal zur Mitteilung (wird beim Anlegen gespeichert)."""
    try:
        s = sheet(ticker)
    except Exception:
        return {"fehler": "Yahoo hat gerade keine Daten zu diesem Ticker geliefert. Die Zahlen aus dem Text sind trotzdem da."}
    werte = ergebnis.get("werte") or {}
    quartale = s.get("quartale") or []
    umsatz = (werte.get("umsatz") or {}).get("wert")
    eps_werte = [w["wert"] for k_, w in werte.items() if k_ in ("eps", "eps_adj")]
    q0 = quartale[0] if quartale else None
    # Hat Yahoo das gemeldete Quartal schon? Dann ist es das neueste, sonst kommt es gerade erst dazu.
    # Kurz nach dem Termin steht dort oft nur das EPS, der Umsatz fehlt noch.
    schon_da = bool(q0) and (_nah(q0.get("umsatz"), umsatz, 0.015)
                             or ((q0.get("umsatz") is None or umsatz is None) and any(_nah(q0.get("eps"), x, 0.02) for x in eps_werte)))
    if schon_da:
        ende = date.fromisoformat(q0["quartal"])
        vq, rest = (quartale[1] if len(quartale) > 1 else None), quartale[1:]
    else:
        ende = date.fromisoformat(q0["quartal"]) + timedelta(days=91) if q0 else None
        vq, rest = q0, quartale
    vj = None
    if ende:
        vj = next((q for q in rest if abs((ende - date.fromisoformat(q["quartal"])).days - 365) <= 25), None)
    k = _konsens(ticker, s, werte, vj)
    q4 = (ergebnis.get("quartal") or {}).get("q") == 4
    aus = {
        "name": s["info"].get("name") or ticker,
        "waehrung": s["info"].get("bilanz_waehrung") or s["info"].get("waehrung"),
        "kurs_waehrung": s["info"].get("waehrung"),
        "periode_ende": ende.isoformat() if ende else None,
        "yahoo_hat_quartal": schon_da,
        "vq": {key: _q(vq, key) for key in REIHENFOLGE} if vq else {},
        "vq_quartal": vq["quartal"] if vq else None,
        "vj": {key: _q(vj, key) for key in REIHENFOLGE} if vj else {},
        "vj_quartal": vj["quartal"] if vj else None,
        "konsens": None,
    }
    konsens_wert = lambda feld: ((k or {}).get(feld) or {}).get("konsens")  # noqa: E731
    if k:
        eps_k, ums_k = k.get("eps") or {}, k.get("umsatz") or {}
        aus["konsens"] = {
            "eps": eps_k.get("konsens"), "eps_low": eps_k.get("low"), "eps_high": eps_k.get("high"),
            "umsatz": ums_k.get("konsens"), "umsatz_low": ums_k.get("low"), "umsatz_high": ums_k.get("high"),
            "analysten": eps_k.get("analysten") or ums_k.get("analysten"),
            # Erwartung für den Ausblick: nächstes Quartal und das Geschäftsjahr, auf das sich eine Jahresprognose bezieht
            # (nach einem vierten Quartal ist das schon das folgende Geschäftsjahr)
            "naechstes_eps": konsens_wert("eps_naechstes"), "naechstes_umsatz": konsens_wert("umsatz_naechstes"),
            "jahr_eps": konsens_wert("eps_folgejahr" if q4 else "eps_jahr"), "jahr_umsatz": konsens_wert("umsatz_folgejahr" if q4 else "umsatz_jahr"),
            "stand": k.get("stand"), "termin": k.get("termin"), "erkannt": k.get("erkannt"), "aus_archiv": k.get("art") == "archiv",
        }
    # Trackrecord-Zeile von Yahoo, falls das Quartal dort schon steht (EPS-Erwartung kurz vor dem Termin)
    if ende:
        h = next((x for x in s.get("historie") or [] if abs((date.fromisoformat(x["quartal"]) - ende).days) <= 20), None)
        if h:
            aus["historie"] = h
            if aus["konsens"] is None and h.get("erwartet") is not None:
                # Yahoo ist schon aufs nächste Quartal umgeschaltet: dessen Erwartung ist die für den Ausblick
                aus["konsens"] = {"eps": h.get("erwartet"), "umsatz": None, "stand": None, "termin": None, "aus_archiv": False,
                                  "erkannt": "Yahoo-Trackrecord: nur die EPS-Erwartung, die Umsatz-Erwartung war vor dem Termin nicht gespeichert",
                                  "naechstes_eps": s["eps"].get("0q", {}).get("konsens"), "naechstes_umsatz": s["umsatz"].get("0q", {}).get("konsens"),
                                  "jahr_eps": s["eps"].get("0y", {}).get("konsens"), "jahr_umsatz": s["umsatz"].get("0y", {}).get("konsens")}
    return aus


# ---- Auswertung: Zeilen und Fazit ----

def _pz(a, b):
    return None if a is None or b in (None, 0) else (a / abs(b) - 1) * 100 if a * b > 0 or b > 0 else (a - b) / abs(b) * 100


def zeilen(e: dict) -> dict:
    """Tabelle der Kennzahlen mit Erwartung und Vorperioden, Ausblick mit Erwartung, Fazit. Wird bei jedem Abruf
    neu gerechnet (Korrekturen wirken sofort), die Vergleichswerte selbst bleiben gespeichert."""
    werte = {**(e["ergebnis"].get("werte") or {})}
    for key, w in (e.get("ki") or {}).get("werte", {}).items():
        werte.setdefault(key, w)
    for key, wert in (e.get("korrekturen") or {}).items():
        if wert is None:
            werte.pop(key, None)
        else:
            werte[key] = {**werte.get(key, {}), "wert": wert, "quelle": "Korrigiert", "vj_prozent": None, "vq_prozent": None}
    v = e.get("vergleich") or {}
    k = v.get("konsens") or {}
    # Der Yahoo-Konsens für den Gewinn je Aktie ist meist bereinigt: mit EPS bereinigt vergleichen, wenn vorhanden
    eps_konsens_bei = "eps_adj" if "eps_adj" in werte else "eps"
    tabelle = []
    for key in REIHENFOLGE:
        w = werte.get(key)
        if not w:
            continue
        prozent = pm.KENNZAHLEN[key][1] == "prozent"
        konsens = k.get("umsatz") if key == "umsatz" else k.get("eps") if key == eps_konsens_bei else None
        vq, vj = (v.get("vq") or {}).get(key), (v.get("vj") or {}).get(key)
        if key in ("eps_adj", "dividende"):
            vq = vj = None  # Yahoo führt nur das verwässerte EPS nach GAAP und die Dividende als Gesamtsumme
        if prozent:
            ggu_vq = None if vq is None else w["wert"] - vq
            ggu_vj = None if vj is None else w["wert"] - vj
        else:
            ggu_vq = _pz(w["wert"], vq) if vq is not None else w.get("vq_prozent")
            ggu_vj = _pz(w["wert"], vj) if vj is not None else w.get("vj_prozent")
        tabelle.append({
            "kennzahl": key, "name": pm.KENNZAHLEN[key][0], "art": pm.KENNZAHLEN[key][1], "wert": w["wert"],
            "waehrung": w.get("waehrung"), "quelle": w.get("quelle"), "beleg": w.get("beleg"),
            "konsens": konsens, "abweichung": _pz(w["wert"], konsens) if konsens is not None else None,
            "vq": vq, "ggu_vq": ggu_vq, "vj": vj, "ggu_vj": ggu_vj,
            "aenderung_aus_text": vj is None and w.get("vj_prozent") is not None,
        })
    fehlend = [pm.KENNZAHLEN[key][0] for key in ("umsatz", "eps", "netto", "operativ", "fcf") if key not in werte and not (key == "eps" and "eps_adj" in werte)]

    ausblick = []
    for a in e["ergebnis"].get("ausblick") or []:
        konsens = None
        if a["kennzahl"] == "umsatz":
            konsens = k.get("naechstes_umsatz") if a["periode"] == "Quartal" else k.get("jahr_umsatz") if a["periode"] == "Jahr" else None
        elif a["kennzahl"] == ("eps_adj" if any(x["kennzahl"] == "eps_adj" and x["periode"] == a["periode"] for x in e["ergebnis"]["ausblick"]) else "eps"):
            konsens = k.get("naechstes_eps") if a["periode"] == "Quartal" else k.get("jahr_eps") if a["periode"] == "Jahr" else None
        ausblick.append({**a, "konsens": konsens, "abweichung": _pz(a["mitte"], konsens) if konsens else None})

    return {"kennzahlen": tabelle, "fehlend": fehlend, "ausblick": ausblick, "fazit": _fazit(tabelle, ausblick, e["ergebnis"])}


def _fazit(tabelle: list[dict], ausblick: list[dict], ergebnis: dict) -> dict:
    """Regelbasiertes Fazit nach festen Textbausteinen (keine KI)."""
    teile, punkte, hat_konsens = [], [], False
    for key, name, schwelle in (("umsatz", "Umsatz", SCHWELLE["umsatz"]), ("eps", "Gewinn je Aktie", SCHWELLE["eps"])):
        z = next((x for x in tabelle if (x["kennzahl"] == key or (key == "eps" and x["kennzahl"] in ("eps_adj", "eps"))) and x["abweichung"] is not None), None)
        if not z:
            continue
        hat_konsens = True
        a = z["abweichung"]
        if a > schwelle:
            teile.append(f"{name} {_de(a)} % über der Erwartung"); punkte.append(1)
        elif a < -schwelle:
            teile.append(f"{name} {_de(-a)} % unter der Erwartung"); punkte.append(-1)
        else:
            teile.append(f"{name} im Rahmen der Erwartung"); punkte.append(0)
    aus = None
    for a in sorted(ausblick, key=lambda x: (x["kennzahl"] != "umsatz", x["periode"] != "Quartal")):
        if a["abweichung"] is not None:
            aus = a
            break
    ausblick_punkt = 0
    if aus:
        wann = "fürs nächste Quartal" if aus["periode"] == "Quartal" else "fürs Jahr"
        was = "Umsatzprognose" if aus["kennzahl"] == "umsatz" else "Gewinnprognose"
        if aus["abweichung"] > SCHWELLE["ausblick"]:
            teile.append(f"{was} {wann} (Mitte) {_de(aus['abweichung'])} % über dem Konsens"); ausblick_punkt = 1
        elif aus["abweichung"] < -SCHWELLE["ausblick"]:
            teile.append(f"{was} {wann} (Mitte) {_de(-aus['abweichung'])} % unter dem Konsens"); ausblick_punkt = -1
        else:
            teile.append(f"{was} {wann} im Rahmen des Konsens")
    p = ergebnis.get("prognose")
    if p:
        teile.append({"angehoben": "Prognose angehoben", "gesenkt": "Prognose gesenkt", "bestätigt": "Prognose bestätigt"}[p["art"]])
        ausblick_punkt = ausblick_punkt or {"angehoben": 1, "gesenkt": -1, "bestätigt": 0}[p["art"]]
    if not hat_konsens:
        art = "Ohne Erwartung"
    elif all(x > 0 for x in punkte) and ausblick_punkt > 0:
        art = "Übertroffen, Ausblick stark"
    elif all(x > 0 for x in punkte) and ausblick_punkt < 0:
        art = "Übertroffen, Ausblick schwach"
    elif all(x >= 0 for x in punkte) and any(x > 0 for x in punkte) and ausblick_punkt >= 0:
        art = "Übertroffen"
    elif all(x < 0 for x in punkte):
        art = "Verfehlt"
    elif all(x == 0 for x in punkte):
        art = "Wie erwartet"
    else:
        art = "Gemischt"
    satz = (". ".join(teile) + ".") if teile else "Für diese Mitteilung gibt es keine Erwartung zum Vergleich."
    return {"art": art, "satz": satz, "ton": (ergebnis.get("ton") or {}).get("urteil")}


def _de(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


# ---- Öffentliche Funktionen ----

def _kurz(e: dict) -> dict:
    z = zeilen(e)
    nach = {x["kennzahl"]: x for x in z["kennzahlen"]}
    eps = nach.get("eps_adj") if nach.get("eps_adj", {}).get("abweichung") is not None else nach.get("eps") or nach.get("eps_adj")
    return {
        "id": e["id"], "ticker": e["ticker"], "name": e.get("name") or e["ticker"], "ordner": e["ordner"],
        "quartal": (e["ergebnis"].get("quartal") or {}).get("text"), "erstellt": e["erstellt"],
        "umsatz": nach.get("umsatz"), "eps": eps, "fazit": z["fazit"], "ki_status": (e.get("ki") or {}).get("status", "aus"),
        "ausblick": next((a for a in z["ausblick"] if a["abweichung"] is not None), None),
        "prognose": (e["ergebnis"].get("prognose") or {}).get("art"),
    }


def uebersicht(b: Benutzer) -> dict:
    liste = _lesen(b)
    zaehler = {}
    for e in liste:
        zaehler[e["ordner"]] = zaehler.get(e["ordner"], 0) + 1
    ordner = [{"id": o, "name": _ordner_name(o), "anzahl": n} for o, n in sorted(zaehler.items(), reverse=True)]
    return {"ordner": ordner, "eintraege": [_kurz(e) for e in sorted(liste, key=lambda x: x["erstellt"], reverse=True)],
            "ki": ki.status()}


def eintrag(b: Benutzer, kennung: str) -> dict:
    _gueltige_id(kennung)
    e = next((x for x in _lesen(b) if x["id"] == kennung), None)
    if not e:
        raise ZahlenFehler("Diese Auswertung gibt es nicht mehr.")
    return {**e, **zeilen(e), "ordner_name": _ordner_name(e["ordner"]), "ki_an": ki.status()["an"]}


def anlegen(b: Benutzer, daten: dict) -> dict:
    text = str(daten.get("text") or "")
    if len(text) > MAX_TEXT:
        raise ZahlenFehler("Der Text ist zu lang (höchstens 400.000 Zeichen).")
    if len(text.strip()) < MIN_TEXT:
        raise ZahlenFehler("Bitte die ganze Mitteilung einfügen (der Text ist zu kurz).")
    ticker = str(daten.get("ticker") or "").strip().upper()
    ergebnis = pm.auswerten(text)
    ticker = ticker or (ergebnis.get("ticker") or "")
    if not re.fullmatch(r"[A-Z0-9.\-]{1,12}", ticker):
        raise ZahlenFehler("Bitte den Ticker angeben (z. B. NVDA oder SAP.DE). Im Text war keiner zu finden.")
    if not ergebnis["werte"] and not ergebnis["ausblick"]:
        raise ZahlenFehler("Im Text waren keine Quartalszahlen zu finden. Ist es die Pressemitteilung zu den Zahlen?")
    v = vergleich(ticker, ergebnis)
    name = v.get("name")
    if name:  # mit dem Firmennamen ein zweites Mal lesen: "Alphabet revenues" ist dann kein Segment
        ergebnis = pm.auswerten(text, name)
    ende = v.get("periode_ende")
    # Ohne Quartalsende: Zahlen kommen meist 3 bis 7 Wochen nach Quartalsende
    ordner = str(daten.get("ordner") or "") or _ordner_aus_datum(date.fromisoformat(ende) if ende else date.today() - timedelta(days=40))
    e = {"id": secrets.token_hex(6), "ticker": ticker, "name": name or ticker, "ordner": ordner[:40],
         "erstellt": datetime.now().isoformat(timespec="minutes"), "ergebnis": ergebnis, "vergleich": v,
         "korrekturen": {}, "ki": {"status": "wartet" if ki.status()["an"] else "aus"}}
    with _sperre:
        liste = _lesen(b)
        if len(liste) >= MAX_EINTRAEGE:
            raise ZahlenFehler(f"Es sind schon {MAX_EINTRAEGE} Auswertungen gespeichert. Bitte erst alte löschen.")
        t = _text_datei(b, e["id"])
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(text, encoding="utf-8")
        liste.append(e)
        _schreiben(b, liste)
    if e["ki"]["status"] == "wartet":
        _ki_warteschlange.put((b.name, e["id"]))
        _ki_starten()
    return eintrag(b, e["id"])


def aendern(b: Benutzer, kennung: str, daten: dict) -> dict:
    _gueltige_id(kennung)

    def tun(e):
        if "ordner" in daten:
            o = re.sub(r"\s+", " ", str(daten["ordner"] or "")).strip()[:40]
            if not o:
                raise ZahlenFehler("Bitte einen Ordnernamen angeben.")
            m = re.fullmatch(r"Q([1-4])\s+(\d{4})", o, re.I)
            e["ordner"] = f"{m.group(2)}-Q{m.group(1)}" if m else o
        if "korrektur" in daten:
            key, wert = (daten["korrektur"] or {}).get("kennzahl"), (daten["korrektur"] or {}).get("wert")
            if key not in pm.KENNZAHLEN:
                raise ZahlenFehler("Unbekannte Kennzahl.")
            if wert in (None, ""):
                e.setdefault("korrekturen", {}).pop(key, None)
            elif wert == "weg":
                e.setdefault("korrekturen", {})[key] = None
            else:
                try:
                    e.setdefault("korrekturen", {})[key] = float(wert)
                except (TypeError, ValueError):
                    raise ZahlenFehler("Bitte eine Zahl eingeben.")
    _aendern(b, kennung, tun)
    return eintrag(b, kennung)


def neu_auswerten(b: Benutzer, kennung: str) -> dict:
    """Text noch einmal mit dem aktuellen Leseprogramm lesen (der gespeicherte Vergleich bleibt)."""
    _gueltige_id(kennung)
    t = _text_datei(b, kennung)
    if not t.exists():
        raise ZahlenFehler("Der Text dieser Auswertung fehlt.")
    text = t.read_text(encoding="utf-8")

    def tun(e):
        e["ergebnis"] = pm.auswerten(text, e.get("name"))
    _aendern(b, kennung, tun)
    return eintrag(b, kennung)


def loeschen(b: Benutzer, kennung: str) -> dict:
    _gueltige_id(kennung)
    with _sperre:
        liste = [x for x in _lesen(b) if x["id"] != kennung]
        _schreiben(b, liste)
    try:
        _text_datei(b, kennung).unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True}


# ---- Stufe 2: KI-Ergänzung (nur wenn eingeschaltet), eine nach der anderen ----

_ki_warteschlange: "queue.Queue[tuple[str, str]]" = queue.Queue()
_ki_thread = None


def ki_anstossen(b: Benutzer, kennung: str) -> dict:
    _gueltige_id(kennung)
    if not ki.status()["an"]:
        raise ZahlenFehler("Die KI ist noch nicht eingerichtet (KI_ANBIETER in der .env).")
    _aendern(b, kennung, lambda e: e.update(ki={**(e.get("ki") or {}), "status": "wartet"}))
    _ki_warteschlange.put((b.name, kennung))
    _ki_starten()
    return eintrag(b, kennung)


def _ki_starten():
    global _ki_thread
    if _ki_thread and _ki_thread.is_alive():
        return
    _ki_thread = threading.Thread(target=_ki_arbeiten, name="zahlen-ki", daemon=True)
    _ki_thread.start()


def _ki_arbeiten():
    while True:
        try:
            name, kennung = _ki_warteschlange.get(timeout=60)
        except queue.Empty:
            return
        b = Benutzer(name)
        try:
            _aendern(b, kennung, lambda e: e.update(ki={**(e.get("ki") or {}), "status": "läuft"}))
            text = _text_datei(b, kennung).read_text(encoding="utf-8")
            e = next(x for x in _lesen(b) if x["id"] == kennung)
            antwort = ki.zahlen_ergaenzen(text, e["ergebnis"])
            if antwort is None:
                _aendern(b, kennung, lambda x: x.update(ki={"status": "fehler", "hinweis": "Das Sprachmodell war nicht erreichbar oder zu langsam."}))
                continue
            gepruefte = _ki_pruefen(text, antwort, e["ergebnis"])
            _aendern(b, kennung, lambda x: x.update(ki={"status": "fertig", **gepruefte,
                                                         "modell": antwort.get("modell"), "dauer": antwort.get("dauer"),
                                                         "stand": datetime.now().isoformat(timespec="minutes")}))
        except Exception:
            try:
                _aendern(b, kennung, lambda x: x.update(ki={"status": "fehler", "hinweis": "Die KI-Ergänzung ist fehlgeschlagen."}))
            except Exception:
                pass


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t.replace(" ", " ")).strip().lower()


def _ki_pruefen(text: str, antwort: dict, ergebnis: dict) -> dict:
    """KI-Zahlen nur übernehmen, wenn das Zitat wörtlich im Text steht und die Zahl im Zitat vorkommt."""
    voll = _norm(text)
    werte, verworfen = {}, 0
    for z in antwort.get("werte") or []:
        key, zitat = z.get("kennzahl"), str(z.get("zitat") or "")
        if key not in pm.KENNZAHLEN or key in (ergebnis.get("werte") or {}) or key in werte:
            continue
        try:
            wert = float(z.get("wert"))
        except (TypeError, ValueError):
            verworfen += 1
            continue
        roh = str(z.get("zahl_im_text") or "")
        if len(zitat) < 12 or _norm(zitat) not in voll or not roh or roh.replace(" ", "") not in zitat.replace(" ", ""):
            verworfen += 1
            continue
        werte[key] = {"wert": wert, "waehrung": z.get("waehrung"), "vj_prozent": None, "vq_prozent": None,
                      "beleg": zitat[:320], "quelle": "KI"}
    punkte = [str(p)[:300] for p in (antwort.get("kernaussagen") or [])][:6]
    return {"werte": werte, "kernaussagen": punkte, "ausblick_text": str(antwort.get("ausblick") or "")[:400] or None, "verworfen": verworfen}


# ---- Konsens rechtzeitig sichern ----

def _konsens_sichern_einmal():
    """Für alle eigenen, verbundenen und Watchlist-Werte den Konsens zum nächsten Termin abrufen.
    sheet() merkt ihn sich dabei (earnings_sheet._konsens_merken)."""
    from .kalender import _werte
    from .watchlist import _lesen as watchlist_lesen
    if not BENUTZER_DATEN.exists():
        return
    gesehen = set()
    for ordner in BENUTZER_DATEN.iterdir():
        try:
            b = Benutzer(ordner.name)
        except Exception:
            continue
        try:
            _, namen, _, _ = _werte(b)
        except Exception:
            namen = {}
        tickers = set(namen) | {w.get("ticker") for w in (watchlist_lesen(b) or []) if w.get("ticker")}
        for t in sorted(tickers - gesehen):
            gesehen.add(t)
            try:
                sheet(t)  # merkt sich den Konsens zum nächsten Termin
            except Exception:
                pass
            time.sleep(1)


def starte_konsens_sicherung():
    """Hintergrund: 10 Minuten nach dem Start und dann alle 6 Stunden (sheet() ist 6 Stunden im Cache)."""
    def schleife():
        time.sleep(600)
        while True:
            try:
                from ..status import job
                with job("Konsens sichern"):
                    _konsens_sichern_einmal()
            except Exception:
                pass
            time.sleep(6 * 3600)
    threading.Thread(target=schleife, name="konsens-sichern", daemon=True).start()
