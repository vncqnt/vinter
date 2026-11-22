"""Punkt 29b, Stufe 2: KI-Zusammenfassung der Marktbewegung (Schnittstelle, noch nicht eingeschaltet).

Wird erst benutzt, wenn das Tool auf dem NUC läuft. Bis dahin steht KI_ANBIETER nicht in der .env und
zusammenfassung() gibt None zurück: Die Seite zeigt dann nur die Daten aus Stufe 1.

Einstellungen (in der .env oder den Add-on-Optionen):
- KI_ANBIETER: "ollama" oder "claude" (leer = aus)
- OLLAMA_ADRESSE: z. B. http://homeassistant.local:11434 (nur im lokalen Netz)
- OLLAMA_MODELL: Standard qwen2.5:7b, Alternative llama3.2:3b
- KI_THREADS: Standard 4 (von 8), damit Home Assistant auf dem NUC genug Luft hat
- KI_ZEITLIMIT: Sekunden pro Anfrage, Standard 90

Regeln: Eingabe sind nur die Daten und Schlagzeilen aus Stufe 1. Erzeugt wird zentral für alle Nutzer
(einmal je Rechnung in bewegung.py), nie pro Nutzer. Ist das Modell nicht erreichbar oder zu langsam,
gibt es None und keinen Fehler.
"""
import json
import time
from datetime import datetime, timezone

import requests

from ..einstellungen import key

STANDARD_MODELL = "qwen2.5:7b"
SYSTEM = (
    "You write a short market comment (3 to 4 sentences, English) explaining why an index moved today. "
    "Strict rules: Use ONLY the facts in the provided data. Do not add any outside knowledge, numbers, names or events. "
    "Name the source of each fact in brackets (e.g. [contributions], [macro], [earnings], [headline: <source>]). "
    "Flow and positioning signals (options expiry, gamma, VIX, rebalancing, correlation, short volume) may only be "
    "phrased as a possible explanation, never as a fact. If the data shows no clear driver, say \"no clear catalyst\"."
)
_status = {"erreichbar": None, "dauer": None, "letzte_erfolgreich": None, "fehler": None}


def _einstellungen() -> dict:
    return {
        "anbieter": key("KI_ANBIETER").lower(),
        "adresse": (key("OLLAMA_ADRESSE") or "http://homeassistant.local:11434").rstrip("/"),
        "modell": key("OLLAMA_MODELL") or STANDARD_MODELL,
        "threads": int(key("KI_THREADS") or 4),
        "zeitlimit": float(key("KI_ZEITLIMIT") or 90),
    }


def eingabe(d: dict, chips: list[dict]) -> str:
    """Nur die Fakten aus Stufe 1, kompakt als JSON."""
    b = d.get("beitraege") or {}
    fakten = {
        "index": d["name"], "date": d.get("handelstag"), "status": d.get("status"),
        "change": d.get("index"),
        "top_contributors_points": b.get("oben"), "bottom_contributors_points": b.get("unten"),
        "sum_of_contributions_vs_actual": {"sum": b.get("summe"), "actual": b.get("ist")},
        "sectors": (d.get("sektoren") or [])[:5], "themes": (d.get("themen") or [])[:5],
        "breadth": d.get("breite"),
        "macro": [{k: c[k] for k in ("name", "aenderung", "art", "sigma")} for c in chips],
        "calendar": d.get("termine"),
        "headlines": [{"stock": n["name"], "items": [{"title": m["titel"], "source": m["quelle"]} for m in n["meldungen"]]} for n in d.get("news") or []],
        "flow_positioning_signals_possible_only": [s["titel"] for s in d.get("signale") or []],
        "rule_based_summary_de": (d.get("fazit") or {}).get("kategorie"),
    }
    return json.dumps(fakten, ensure_ascii=False)


def _ollama(e: dict, text: str) -> str:
    antwort = requests.post(f"{e['adresse']}/api/generate", timeout=e["zeitlimit"], json={
        "model": e["modell"], "system": SYSTEM, "prompt": f"Data:\n{text}\n\nWrite the comment now.", "stream": False,
        "keep_alive": "5m",  # Modell nur bei Bedarf geladen, danach wieder aus dem Speicher
        "options": {"num_thread": e["threads"], "temperature": 0.2},
    })
    antwort.raise_for_status()
    return antwort.json()["response"].strip()


def _claude(e: dict, text: str) -> str:
    raise NotImplementedError("Claude-API noch nicht eingerichtet")


def zusammenfassung(d: dict, chips: list[dict]) -> dict | None:
    e = _einstellungen()
    if e["anbieter"] not in ("ollama", "claude"):
        return None
    start = time.time()
    try:
        text = (_ollama if e["anbieter"] == "ollama" else _claude)(e, eingabe(d, chips))
    except Exception as fehler:
        _status.update(erreichbar=False, fehler=type(fehler).__name__)
        return None
    dauer = round(time.time() - start, 1)
    _status.update(erreichbar=True, dauer=dauer, letzte_erfolgreich=datetime.now(timezone.utc).isoformat(timespec="seconds"), fehler=None)
    return {"text": text, "modell": e["modell"] if e["anbieter"] == "ollama" else "claude", "dauer": dauer}


ZAHLEN_SYSTEM = (
    "You extract facts from a company's quarterly earnings press release. Use ONLY the given text, never outside knowledge. "
    "Return JSON with exactly these keys: "
    "\"werte\": list of metrics that are MISSING from the already-found list, each {\"kennzahl\": one of %s, "
    "\"wert\": number in full units (e.g. 35.1 billion -> 35100000000; EPS and margins as written), \"waehrung\": \"USD\"/\"EUR\"/... or null, "
    "\"zahl_im_text\": the number exactly as written in the text (e.g. \"$35.1 billion\"), \"zitat\": the exact sentence from the text}. "
    "Only quarterly figures for the reported quarter, not full-year, not prior year, not segments. Leave the list empty if unsure. "
    "\"kernaussagen\": up to 5 short points in German about what matters beyond the headline numbers "
    "(segments, one-off effects, demand, margins, buybacks). "
    "\"ausblick\": one German sentence summarizing the outlook/guidance, or null if there is none."
)


def zahlen_ergaenzen(text: str, gefunden: dict) -> dict | None:
    """Stufe 2 der Zahlen-Auswertung: fehlende Kennzahlen und Kernaussagen aus der Mitteilung.

    Bekommt nur den Fließtext (Tabellen weg, gekürzt), damit kleine Modelle auf dem NUC durchkommen.
    Jede Zahl muss mit wörtlichem Zitat kommen; das prüft app/nutzer/zahlen.py, bevor etwas angezeigt wird."""
    e = _einstellungen()
    if e["anbieter"] != "ollama":
        return None
    from .pressemitteilung import KENNZAHLEN
    prosa = "\n".join(z for z in text.replace("\r", "").split("\n") if "\t" not in z.strip("\t ") and len(z.strip()) > 2)[:14000]
    schon = {k: v.get("wert") for k, v in (gefunden.get("werte") or {}).items()}
    start = time.time()
    try:
        antwort = requests.post(f"{e['adresse']}/api/generate", timeout=e["zeitlimit"] * 3, json={
            "model": e["modell"], "system": ZAHLEN_SYSTEM % json.dumps(list(KENNZAHLEN)), "stream": False, "format": "json",
            "prompt": f"Already found (do not repeat): {json.dumps(schon)}\n\nPress release:\n{prosa}\n\nReturn the JSON now.",
            "keep_alive": "5m", "options": {"num_thread": e["threads"], "temperature": 0.1, "num_ctx": 8192},
        })
        antwort.raise_for_status()
        daten = json.loads(antwort.json()["response"])
    except Exception as fehler:
        _status.update(erreichbar=False, fehler=type(fehler).__name__)
        return None
    _status.update(erreichbar=True, dauer=round(time.time() - start, 1), fehler=None,
                   letzte_erfolgreich=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if not isinstance(daten, dict):
        return None
    return {**daten, "modell": e["modell"], "dauer": round(time.time() - start, 1)}


def status() -> dict:
    """Für die Status-Seite: eingeschaltet, erreichbar, Dauer und Zeitpunkt der letzten Zusammenfassung."""
    e = _einstellungen()
    return {"an": e["anbieter"] in ("ollama", "claude"), "anbieter": e["anbieter"] or None, "modell": e["modell"], **_status}
