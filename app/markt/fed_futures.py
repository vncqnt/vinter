"""Fed-Zinswahrscheinlichkeiten, selbst gerechnet aus Fed-Funds-Futures (Yahoo) und dem
offiziellen Tagesgeldsatz der New York Fed. Gleiche Methode wie CME FedWatch, aber eigene Rechnung.

So funktioniert es: Ein Fed-Funds-Future für einen Monat kostet 100 minus den erwarteten
Durchschnittszins dieses Monats. Liegt eine Fed-Sitzung im Monat, ist der Durchschnitt eine
Mischung aus dem Zins vor und nach der Sitzung. Daraus lässt sich der erwartete Zins nach der
Sitzung zurückrechnen. Liegt er zum Beispiel 0,17 Prozentpunkte über dem alten, entspricht das
68 % Wahrscheinlichkeit für eine Erhöhung um 0,25 und 32 % für "unverändert".
"""
import calendar
import json
import ssl
import urllib.request
from datetime import date, timedelta

import certifi
import yaml
import yfinance as yf

from ..cache import markt_lesen as cache_lesen, markt_schreiben as cache_schreiben
from ..konfig import MARKT_DATEN

SCHRITT = 0.25          # die Fed bewegt den Zins in Schritten von 0,25 Prozentpunkten
MONATE = 12
GUELTIG = 15 * 60
MONATS_CODE = "FGHJKMNQUVXZ"   # Börsen-Kürzel der Liefermonate Januar bis Dezember
_SSL = ssl.create_default_context(cafile=certifi.where())


def _tagesgeld() -> dict:
    """Aktueller effektiver Fed-Funds-Satz und Zielspanne, von der New York Fed (ohne Key)."""
    adresse = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json"
    anfrage = urllib.request.Request(adresse, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(anfrage, timeout=15, context=_SSL) as antwort:
        r = json.loads(antwort.read())["refRates"][0]
    return {"satz": float(r["percentRate"]), "von": float(r["targetRateFrom"]), "bis": float(r["targetRateTo"]), "stand": r["effectiveDate"]}


def _sitzungen() -> list[date]:
    """Kommende Fed-Entscheidungen aus daten/markt/makro_termine.yaml (dort pflegst du die Termine)."""
    datei = MARKT_DATEN / "makro_termine.yaml"
    roh = yaml.safe_load(datei.read_text(encoding="utf-8")) or [] if datei.exists() else []
    eintraege = roh.get("termine", roh) if isinstance(roh, dict) else roh
    heute = date.today()
    tage = sorted({date.fromisoformat(str(t["datum"])) for t in eintraege
                   if "Fed-Zinsentscheid" in str(t.get("name", "")) and date.fromisoformat(str(t["datum"])) >= heute})
    return tage


def _futures() -> dict[tuple[int, int], float]:
    """Erwarteter Durchschnittszins je Monat (Jahr, Monat) für die nächsten zwölf Monate."""
    heute = date.today()
    monate = []
    j, m = heute.year, heute.month
    for _ in range(MONATE + 2):
        monate.append((j, m))
        j, m = (j + 1, 1) if m == 12 else (j, m + 1)
    ticker = {f"ZQ{MONATS_CODE[m - 1]}{str(j)[2:]}.CBT": (j, m) for j, m in monate}
    tabelle = yf.download(list(ticker), period="5d", auto_adjust=False, progress=False, group_by="ticker", threads=True)
    zinsen = {}
    for t, monat in ticker.items():
        try:
            schluss = tabelle[t]["Close"].dropna()
        except KeyError:
            continue
        if len(schluss):
            zinsen[monat] = round(100 - float(schluss.iloc[-1]), 4)
    return zinsen


def _verteilung(start: float, ende: float) -> list[dict]:
    """Zwei benachbarte Zinsschritte, die zusammen den erwarteten Zins ergeben."""
    schritte = (ende - start) / SCHRITT
    unten = int(schritte // 1)
    anteil = schritte - unten
    if anteil < 0.005:
        return [{"schritte": unten, "p": 1.0}]
    return [{"schritte": unten, "p": 1 - anteil}, {"schritte": unten + 1, "p": anteil}]


def rechnen(tagesgeld: dict, zinsen: dict[tuple[int, int], float], sitzungen: list[date]) -> list[dict]:
    """Wahrscheinlichkeiten je Sitzung aus Tagesgeld und Futures-Zinsen. Reine Funktion (auch für frühere Tage)."""
    mit_sitzung = {(s.year, s.month) for s in sitzungen}
    start = tagesgeld["satz"]
    ergebnisse = []
    for s in sitzungen:
        monat = (s.year, s.month)
        if monat not in zinsen:
            break
        tage_im_monat = calendar.monthrange(*monat)[1]
        danach = tage_im_monat - s.day  # der neue Zins gilt ab dem Tag nach der Entscheidung
        naechster = (s + timedelta(days=tage_im_monat - s.day + 1))
        naechster_monat = (naechster.year, naechster.month)
        if danach < 10 and naechster_monat in zinsen and naechster_monat not in mit_sitzung:
            # Nur wenige Tage nach der Sitzung im Monat: Der Folgemonat ohne Sitzung zeigt den neuen Zins direkt.
            ende = zinsen[naechster_monat]
        else:
            ende = (zinsen[monat] * tage_im_monat - start * (tage_im_monat - danach)) / max(danach, 1)
        diese = _verteilung(start, ende)            # Schritte bei dieser Sitzung
        gesamt = _verteilung(tagesgeld["satz"], ende)  # Schritte gegenüber heute, für die Zielspanne
        ergebnisse.append({
            "datum": s.isoformat(),
            "erwarteter_satz": round(ende, 3),
            "aenderung_bp": round((ende - start) * 100, 1),
            "diese_sitzung": [{"aenderung_bp": round(x["schritte"] * SCHRITT * 100), "p": round(x["p"], 4)} for x in diese],
            "zielspanne": [{"von": round(tagesgeld["von"] + x["schritte"] * SCHRITT, 2), "bis": round(tagesgeld["bis"] + x["schritte"] * SCHRITT, 2),
                            "p": round(x["p"], 4)} for x in gesamt],
        })
        start = ende
    return ergebnisse


def anteile(s: dict) -> dict:
    """Senkung, unverändert, Erhöhung bei dieser Sitzung."""
    erh = sum(x["p"] for x in s["diese_sitzung"] if x["aenderung_bp"] > 0)
    sen = sum(x["p"] for x in s["diese_sitzung"] if x["aenderung_bp"] < 0)
    return {"erhoehung": erh, "senkung": sen, "halten": max(0.0, 1 - erh - sen)}


def merken(fed: dict, tag: str | None = None, ueberschreiben: bool = True) -> None:
    """Je Sitzung Erhöhung, Senkung und erwarteten Zins im Verlauf speichern (prognosen_verlauf.sqlite)."""
    from .prognosen import merken_am
    tag = tag or date.today().isoformat()
    for s in fed.get("sitzungen", []):
        a = anteile(s)
        merken_am(f"fed:{s['datum']}:erhoehung", tag, a["erhoehung"], ueberschreiben)
        merken_am(f"fed:{s['datum']}:senkung", tag, a["senkung"], ueberschreiben)
        merken_am(f"fed:{s['datum']}:satz", tag, s["erwarteter_satz"], ueberschreiben)


def rueckblick(alle_sitzungen: list[date]) -> int:
    """Verlauf seit der letzten Sitzung nachrechnen, aus den Tageskursen der noch laufenden Futures (Yahoo liefert
    abgelaufene Kontrakte nicht mehr) und dem Tagesgeld-Verlauf der New York Fed. Vorhandene Tage bleiben."""
    heute = date.today()
    vorbei = [s for s in alle_sitzungen if s < heute]
    if not vorbei:
        return 0
    ab = max(vorbei) + timedelta(days=1)
    adresse = f"https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate={(ab - timedelta(days=10)).isoformat()}&endDate={heute.isoformat()}"
    anfrage = urllib.request.Request(adresse, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(anfrage, timeout=20, context=_SSL) as antwort:
        geld = sorted(({"satz": float(r["percentRate"]), "von": float(r["targetRateFrom"]), "bis": float(r["targetRateTo"]), "stand": r["effectiveDate"]}
                       for r in json.loads(antwort.read())["refRates"]), key=lambda r: r["stand"])
    monate, j, m = [], heute.year, heute.month
    for _ in range(MONATE + 2):
        monate.append((j, m))
        j, m = (j + 1, 1) if m == 12 else (j, m + 1)
    ticker = {f"ZQ{MONATS_CODE[mm - 1]}{str(jj)[2:]}.CBT": (jj, mm) for jj, mm in monate}
    tab = yf.download(list(ticker), period="3mo", auto_adjust=False, progress=False, group_by="ticker", threads=True)
    kurse = {}
    for tk, monat in ticker.items():
        try:
            kurse[monat] = tab[tk]["Close"].dropna()
        except KeyError:
            continue
    tage = sorted({d.date() for s in kurse.values() for d in s.index if ab <= d.date() < heute})
    n = 0
    for tag in tage:
        g = [x for x in geld if x["stand"] <= tag.isoformat()]
        if not g:
            continue
        zinsen = {mo: round(100 - float(s[s.index.date <= tag].iloc[-1]), 4) for mo, s in kurse.items() if (s.index.date <= tag).any() and mo >= (tag.year, tag.month)}
        if (tag.year, tag.month) not in zinsen:
            continue
        sitz = [s for s in alle_sitzungen if s > tag]
        merken({"sitzungen": rechnen(g[-1], zinsen, sitz)}, tag.isoformat(), ueberschreiben=False)
        n += 1
    return n


def fed_wahrscheinlichkeiten() -> dict:
    alt, alter = cache_lesen("fed-futures")
    if alt and alter < GUELTIG:
        return alt
    try:
        tagesgeld = _tagesgeld()
        zinsen = _futures()
    except Exception:
        return alt or {"fehler": "Die Fed-Futures konnten gerade nicht geladen werden."}
    if not zinsen:
        return alt or {"fehler": "Yahoo liefert gerade keine Fed-Funds-Futures."}

    ziel_mitte = (tagesgeld["von"] + tagesgeld["bis"]) / 2
    ergebnisse = rechnen(tagesgeld, zinsen, _sitzungen())
    neu = {
        "tagesgeld": tagesgeld,
        "zielspanne_mitte": ziel_mitte,
        "sitzungen": ergebnisse,
        "kurve": [{"monat": f"{j}-{m:02d}", "satz": z} for (j, m), z in sorted(zinsen.items())],
        "quelle": "Fed-Funds-Futures (CBOT, über Yahoo, verzögert) und New York Fed (Tagesgeldsatz)",
    }
    cache_schreiben("fed-futures", neu)
    return neu
