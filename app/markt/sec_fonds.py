"""Seite "Fonds & Insider": Was große Fonds und Firmen-Insider der SEC melden müssen.

- fonds(): 13F-Meldungen: Jeder Fonds mit über 100 Mio. $ in US-Aktien meldet vierteljährlich seine
  Positionen (bis 45 Tage nach Quartalsende, also verzögert). Verglichen werden die letzten zwei Meldungen:
  neue Positionen, Ausstiege, Aufstockungen. Optionen (Puts/Calls) werden nicht mitgezählt.
- insider(ticker): Form 4: Vorstände, Aufsichtsräte und Großaktionäre melden jeden Kauf und Verkauf
  innerhalb von 2 Tagen. Käufe mit eigenem Geld ("P") sagen mehr als Verkäufe, die oft geplant sind
  (Plan nach Regel 10b5-1) oder Steuern auf Aktienvergütung bezahlen.
- grossaktionaere(ticker): Schedule 13D/13G: Wer über 5 % einer Firma hält, muss das melden.
  13D = will Einfluss nehmen (Aktivist), 13G = passiv.
Jede Meldung wird nur einmal geladen und dann dauerhaft gemerkt.
"""
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import yaml

from ..konfig import MARKT_DATEN
from .abruf import STUNDE, TAG, gecacht, hole
from .cashflow import cik_liste

FONDS_DATEI = MARKT_DATEN / "fonds.yaml"
FUER_IMMER = 3650 * TAG


def _fonds_liste() -> list[dict]:
    try:
        return yaml.safe_load(FONDS_DATEI.read_text(encoding="utf-8")) or []
    except Exception:
        return []


def _einreichungen(cik: int) -> dict:
    return gecacht(f"sec:subm:{cik}", 6 * STUNDE, lambda: hole(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", sec=True).json()["filings"]["recent"])


def _ohne_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _infotable(cik: int, nummer: str) -> dict:
    """Positionen einer 13F-Meldung: {CUSIP: {name, wert, stueck}} (Werte in $)."""
    def holen():
        ordner = f"https://www.sec.gov/Archives/edgar/data/{cik}/{nummer.replace('-', '')}"
        dateien = [i["name"] for i in hole(f"{ordner}/index.json", sec=True).json()["directory"]["item"]]
        xml = next((n for n in dateien if n.lower().endswith(".xml") and n.lower() != "primary_doc.xml"), None)
        if not xml:
            raise ValueError("keine Positionsliste")
        wurzel = ET.fromstring(hole(f"{ordner}/{xml}", sec=True, zeitlimit=120).content)
        pos = {}
        for zeile in wurzel:
            f = {_ohne_ns(e.tag): e for e in zeile.iter()}
            if "putCall" in f and (f["putCall"].text or "").strip():
                continue
            cusip = (f["cusip"].text or "").strip().upper().zfill(9)
            wert = float(f["value"].text or 0)
            stueck = float(f["sshPrnamt"].text or 0) if "sshPrnamt" in f else 0.0
            p = pos.setdefault(cusip, {"name": (f["nameOfIssuer"].text or "").strip().title(), "wert": 0.0, "stueck": 0.0})
            p["wert"] += wert
            p["stueck"] += stueck
        # Manche Fonds melden noch in Tausend Dollar: dann ergäbe sich ein Preis von Cent-Bruchteilen je Aktie
        preise = sorted(p["wert"] / p["stueck"] for p in pos.values() if p["stueck"])
        if preise and preise[len(preise) // 2] < 0.5:
            for p in pos.values():
                p["wert"] *= 1000
        return pos
    return gecacht(f"sec:13f:{nummer}", FUER_IMMER, holen)


def _fonds_auswerten(f: dict) -> dict:
    e = _einreichungen(f["cik"])
    meldungen = [(e["accessionNumber"][i], e["reportDate"][i], e["filingDate"][i]) for i in range(len(e["form"])) if e["form"][i] == "13F-HR"]
    # Je Stichtag die erste Meldung (Nachträge "13F-HR/A" bleiben außen vor)
    gesehen, liste = set(), []
    for m in meldungen:
        if m[1] not in gesehen:
            gesehen.add(m[1])
            liste.append(m)
    if len(liste) < 2:
        raise ValueError("weniger als zwei Meldungen")
    (nr_neu, stichtag, gemeldet), (nr_alt, stichtag_alt, _) = liste[0], liste[1]
    neu, alt = _infotable(f["cik"], nr_neu), _infotable(f["cik"], nr_alt)
    gesamt = sum(p["wert"] for p in neu.values())
    zeilen = []
    for cusip in set(neu) | set(alt):
        n, a = neu.get(cusip), alt.get(cusip)
        preis = (n["wert"] / n["stueck"]) if n and n["stueck"] else ((a["wert"] / a["stueck"]) if a and a["stueck"] else 0)
        delta_stueck = (n["stueck"] if n else 0) - (a["stueck"] if a else 0)
        zeilen.append({"cusip": cusip, "name": (n or a)["name"], "wert": n["wert"] if n else 0.0, "alt": a["wert"] if a else 0.0,
                       "anteil": round((n["wert"] if n else 0) / gesamt * 100, 2) if gesamt else 0,
                       "aenderung": round(delta_stueck / a["stueck"] * 100, 1) if a and a["stueck"] else None,
                       "fluss": delta_stueck * preis, "neu": a is None, "raus": n is None})
    nach_wert = sorted((z for z in zeilen if not z["raus"]), key=lambda z: -z["wert"])
    kurz = lambda liste: [{k: (round(v / 1e6, 1) if k in ("wert", "alt", "fluss") else v) for k, v in z.items()} for z in liste]
    return {"cik": f["cik"], "name": f["name"], "art": f.get("art", ""), "stichtag": stichtag, "vorher": stichtag_alt, "gemeldet": gemeldet,
            "gesamt": round(gesamt / 1e9, 2), "anzahl": len(neu),
            "top": kurz(nach_wert[:10]),
            "neu": kurz(sorted((z for z in zeilen if z["neu"]), key=lambda z: -z["wert"])[:5]),
            "raus": kurz(sorted((z for z in zeilen if z["raus"]), key=lambda z: -z["alt"])[:5]),
            "gekauft": kurz(sorted((z for z in zeilen if not z["neu"] and z["fluss"] > 0), key=lambda z: -z["fluss"])[:5]),
            "verkauft": kurz(sorted((z for z in zeilen if not z["raus"] and z["fluss"] < 0), key=lambda z: z["fluss"])[:5]),
            "_alle": {z["cusip"]: z for z in zeilen}}


def fonds(cusips: list[str] | None = None) -> dict:
    """Auswertung aller Fonds. cusips: zusätzlich je dieser Wertpapiere, wer sie hält (für "meine Aktien")."""
    def sicher(f):
        try:
            return _fonds_auswerten(f)
        except Exception as fehler:
            return {"name": f["name"], "fehler": f"Keine Meldung lesbar ({type(fehler).__name__})."}
    with ThreadPoolExecutor(3) as pool:
        liste = list(pool.map(sicher, _fonds_liste()))
    gueltig = [f for f in liste if "fehler" not in f]
    # Über alle Fonds: wie viele halten die Aktie, und wohin floss das Geld (Kauf minus Verkauf)
    sammel = {}
    for f in gueltig:
        for cusip, z in f["_alle"].items():
            s = sammel.setdefault(cusip, {"name": z["name"], "halter": 0, "fluss": 0.0, "wert": 0.0, "neu": 0, "raus": 0})
            s["halter"] += 0 if z["raus"] else 1
            s["fluss"] += z["fluss"]
            s["wert"] += z["wert"]
            s["neu"] += z["neu"]
            s["raus"] += z["raus"]
    runden = lambda liste: [{**s, "fluss": round(s["fluss"] / 1e6, 1), "wert": round(s["wert"] / 1e6, 1)} for s in liste]
    beliebt = runden(sorted(sammel.values(), key=lambda s: (-s["halter"], -s["wert"]))[:15])
    zufluss = runden(sorted(sammel.values(), key=lambda s: -s["fluss"])[:10])
    abfluss = runden(sorted(sammel.values(), key=lambda s: s["fluss"])[:10])
    meine = {}
    for c in cusips or []:
        halter = []
        for f in gueltig:
            z = f["_alle"].get(c)
            if z:
                halter.append({"fonds": f["name"], "wert": round(z["wert"] / 1e6, 1), "anteil": z["anteil"], "aenderung": z["aenderung"],
                               "neu": z["neu"], "raus": z["raus"], "fluss": round(z["fluss"] / 1e6, 1)})
        meine[c] = sorted(halter, key=lambda h: -h["wert"])
    for f in gueltig:
        del f["_alle"]
    return {"fonds": liste, "beliebt": beliebt, "zufluss": zufluss, "abfluss": abfluss, "meine": meine}


# ---- Firmen-Insider (Form 4) und Großaktionäre (13D/13G) ----
def _cik(ticker: str) -> int:
    info = cik_liste().get(ticker.upper().replace("-", "."), cik_liste().get(ticker.upper()))
    if not info:
        raise LookupError(f"{ticker} nicht bei der SEC")
    return info["cik"]


def _text(e, pfad):
    x = e.find(pfad)
    return (x.text or "").strip() if x is not None and x.text else ""


def _form4(cik: int, nummer: str, datei: str) -> dict:
    def holen():
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{nummer.replace('-', '')}/{datei.split('/')[-1]}"
        w = ET.fromstring(hole(url, sec=True).content)
        rolle = w.find(".//reportingOwnerRelationship")
        titel = _text(rolle, "officerTitle") if rolle is not None else ""
        if not titel and rolle is not None:
            titel = "Aufsichtsrat" if _text(rolle, "isDirector") in ("1", "true") else ("Großaktionär (10 %)" if _text(rolle, "isTenPercentOwner") in ("1", "true") else "")
        geschaefte = []
        for t in w.findall(".//nonDerivativeTransaction"):
            code = _text(t, ".//transactionCoding/transactionCode")
            if code not in ("P", "S"):
                continue
            stueck = float(_text(t, ".//transactionShares/value") or 0)
            preis = float(_text(t, ".//transactionPricePerShare/value") or 0)
            geschaefte.append({"datum": _text(t, ".//transactionDate/value")[:10], "art": "Kauf" if code == "P" else "Verkauf",
                               "stueck": stueck, "preis": preis, "wert": round(stueck * preis)})
        return {"emittent": int(_text(w, ".//issuer/issuerCik") or 0), "wer": _text(w, ".//reportingOwner/reportingOwnerId/rptOwnerName").title(), "rolle": titel,
                "plan": _text(w, "aff10b5One") in ("1", "true"), "geschaefte": geschaefte}
    return gecacht(f"sec:f4:{nummer}", FUER_IMMER, holen)


def insider(ticker: str, tage: int = 180, hoechstens: int = 80) -> dict:
    cik = _cik(ticker)
    e = _einreichungen(cik)
    grenze = (date.today() - timedelta(days=tage)).isoformat()
    meldungen = [(e["accessionNumber"][i], e["primaryDocument"][i], e["filingDate"][i]) for i in range(len(e["form"]))
                 if e["form"][i] == "4" and e["filingDate"][i] >= grenze][:hoechstens]

    def sicher(m):
        try:
            return {**_form4(cik, m[0], m[1]), "gemeldet": m[2]}
        except Exception:
            return None
    # Nur Meldungen über diese Firma selbst (nicht solche, in denen die Firma als Käufer anderer Aktien auftritt)
    with ThreadPoolExecutor(3) as pool:
        formulare = [f for f in pool.map(sicher, meldungen) if f and f.get("emittent", cik) == cik]
    zeilen = []
    for f in formulare:
        for g in f["geschaefte"]:
            zeilen.append({**g, "wer": f["wer"], "rolle": f["rolle"], "plan": f["plan"]})
    zeilen.sort(key=lambda z: z["datum"], reverse=True)
    kaeufe = [z for z in zeilen if z["art"] == "Kauf"]
    verkaeufe = [z for z in zeilen if z["art"] == "Verkauf"]
    # Mehrere verschiedene Insider kaufen innerhalb von 30 Tagen: gilt als stärkeres Zeichen
    gruppe = False
    for k in kaeufe:
        d = date.fromisoformat(k["datum"])
        namen = {x["wer"] for x in kaeufe if abs((date.fromisoformat(x["datum"]) - d).days) <= 30}
        if len(namen) >= 2:
            gruppe = True
            break
    return {"ticker": ticker, "zeilen": zeilen[:60], "kauf_summe": round(sum(z["wert"] for z in kaeufe)),
            "verkauf_summe": round(sum(z["wert"] for z in verkaeufe)), "verkauf_geplant": round(sum(z["wert"] for z in verkaeufe if z["plan"])),
            "kaeufer": len({z["wer"] for z in kaeufe}), "gruppenkauf": gruppe, "tage": tage}


def _absender(cik: int, nummer: str) -> dict:
    """Wer die Meldung abgegeben hat und um welche Firma es geht (Kopfdaten der Meldung)."""
    def holen():
        html = hole(f"https://www.sec.gov/Archives/edgar/data/{cik}/{nummer.replace('-', '')}/{nummer}-index-headers.html", sec=True).text
        text = re.sub(r"<[^>]+>", " ", html)
        def abschnitt(titel):
            i = text.find(titel)
            if i < 0:
                return None, None
            teil = text[i:i + 1500]
            name = re.search(r"COMPANY CONFORMED NAME:\s*(.+?)\s{2,}", teil) or re.search(r"FULL NAME:\s*(.+?)\s{2,}", teil)
            nr = re.search(r"CENTRAL INDEX KEY:\s*(\d+)", teil)
            return (name.group(1).strip().title() if name else None), (int(nr.group(1)) if nr else None)
        wer, _ = abschnitt("FILED BY")
        _, ueber = abschnitt("SUBJECT COMPANY")
        return {"wer": wer or "unbekannt", "ueber": ueber}
    return gecacht(f"sec:abs2:{nummer}", FUER_IMMER, holen)


def grossaktionaere(ticker: str, tage: int = 400, hoechstens: int = 12) -> list[dict]:
    cik = _cik(ticker)
    e = _einreichungen(cik)
    grenze = (date.today() - timedelta(days=tage)).isoformat()
    aus = []
    for i in range(len(e["form"])):
        form = e["form"][i]
        if e["filingDate"][i] < grenze or not re.match(r"(SC |SCHEDULE )13[DG]", form):
            continue
        try:
            kopf = _absender(cik, e["accessionNumber"][i])
        except Exception:
            continue
        if kopf["ueber"] not in (None, cik):
            continue  # die Firma ist hier selbst der Käufer einer anderen Firma
        wer = kopf["wer"]
        aus.append({"datum": e["filingDate"][i], "form": form.replace("SCHEDULE ", "").replace("SC ", ""), "wer": wer,
                    "aktiv": "13D" in form, "nachtrag": form.endswith("/A")})
        if len(aus) >= hoechstens:
            break
    return aus
