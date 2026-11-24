"""API-Keys und andere geheime Einstellungen an einer Stelle.

Die Module fragen nur `key("FINNHUB_API_KEY")` und wissen nicht, woher der Wert kommt.
Reihenfolge der Quellen, die erste mit einem Wert gewinnt:
  1. Umgebungsvariable
  2. .env im Datenverzeichnis, dann .env im Programmordner (lokal am PC)
  3. options.json im Datenverzeichnis (so legt Home Assistant die Add-on-Optionen ab)
  4. verschlüsselte Ablage, gefüllt über die Einstellungsseite im Tool (app/geheimnisse.py)
Werte werden nie ausgegeben oder geloggt.
"""
import json
import os

from dotenv import dotenv_values

from .konfig import DATEN, PROGRAMM

BEKANNT = ("FINNHUB_API_KEY", "FMP_API_KEY", "ALPHAVANTAGE_API_KEY", "FRED_API_KEY", "SEC_USER_AGENT")
# Für die Einstellungsseite: alle Einstellungen, die Vinter liest, mit Erklärung. geheim = Wert nie anzeigen.
EINSTELLUNGEN = [
    {"name": "FINNHUB_API_KEY", "geheim": True, "text": "Finnhub: US-Kurse in Echtzeit und gemeldete Quartalszahlen (Gratis-Key auf finnhub.io)"},
    {"name": "FMP_API_KEY", "geheim": True, "text": "Financial Modeling Prep: Earnings-Kalender und Peer-Listen (Gratis-Key)"},
    {"name": "ALPHAVANTAGE_API_KEY", "geheim": True, "text": "Alpha Vantage (Gratis-Key, derzeit kaum genutzt)"},
    {"name": "FRED_API_KEY", "geheim": True, "text": "FRED der St. Louis Fed: US-Makrodaten (Gratis-Key)"},
    {"name": "CENSUS_API_KEY", "geheim": True, "text": "US Census: US-Handelsdaten (Gratis-Key)"},
    {"name": "COMTRADE_API_KEY", "geheim": True, "text": "UN Comtrade: weltweite Handelsdaten (Gratis-Key)"},
    {"name": "TELEGRAM_API_ID", "geheim": True, "text": "Telegram: API-ID von my.telegram.org (Newsfeed liest öffentliche Kanäle)"},
    {"name": "TELEGRAM_API_HASH", "geheim": True, "text": "Telegram: API-Hash von my.telegram.org"},
    {"name": "AISSTREAM_API_KEY", "geheim": True, "text": "aisstream.io: Live-Schiffe auf der Weltkarte (Gratis-Key)"},
    {"name": "SEC_USER_AGENT", "geheim": True, "text": "Kontakt für die SEC (Pflicht bei EDGAR), z. B. 'Vorname Nachname name@example.de'"},
    {"name": "HANDEL_KONTAKT", "geheim": True, "text": "Kontakt im User-Agent beim Abruf von Webseiten (Handelsdaten), leer = ohne"},
    {"name": "KI_ANBIETER", "geheim": False, "text": "KI für Zusammenfassungen: 'ollama' (auf dem NUC) oder leer = aus"},
    {"name": "OLLAMA_ADRESSE", "geheim": False, "text": "Adresse von Ollama, z. B. http://homeassistant.local:11434"},
    {"name": "OLLAMA_MODELL", "geheim": False, "text": "Sprachmodell, Standard qwen2.5:7b"},
    {"name": "VINTER_ADRESSE", "geheim": False, "text": "Adresse, unter der Freunde Vinter erreichen (für Einladungslinks), z. B. http://vinter.tailnet-name.ts.net:8099"},
]


def _env_dateien() -> dict:
    werte = {}
    for datei in (PROGRAMM / ".env", DATEN / ".env"):  # spätere überschreibt frühere
        if datei.exists():
            werte.update({k: v for k, v in dotenv_values(datei).items() if v})
    return werte


def _addon_optionen() -> dict:
    datei = DATEN / "options.json"
    if not datei.exists():
        return {}
    try:
        roh = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    # Add-on-Optionen sind meist klein geschrieben (finnhub_api_key). Beides gilt.
    return {str(k).upper(): str(v) for k, v in roh.items() if v not in (None, "")}


def _im_tool() -> dict:
    try:
        from .geheimnisse import alle
        return alle()
    except Exception:
        return {}


def key(name: str) -> str:
    """Wert einer Einstellung, leerer Text wenn sie nirgends steht. Wird bei jedem Aufruf frisch
    gelesen, damit ein neu eingetragener Key ohne Neustart wirkt."""
    for quelle in (os.environ, _env_dateien(), _addon_optionen(), _im_tool()):
        wert = (quelle.get(name) or "").strip()
        if wert:
            return wert
    return ""


def herkunft(name: str) -> str | None:
    """Woher der wirksame Wert kommt (umgebung, env_datei, addon, tool) oder None. Ohne den Wert zu verraten."""
    for art, quelle in (("umgebung", os.environ), ("env_datei", _env_dateien()), ("addon", _addon_optionen()), ("tool", _im_tool())):
        if (quelle.get(name) or "").strip():
            return art
    return None


def uebersicht() -> list[dict]:
    """Für die Einstellungsseite: je Einstellung, ob gesetzt und woher; nicht geheime Werte mit Wert."""
    im_tool = _im_tool()
    aus = []
    for e in EINSTELLUNGEN:
        h = herkunft(e["name"])
        aus.append({**e, "gesetzt": bool(h), "herkunft": h, "im_tool": e["name"] in im_tool,
                    "wert": None if e["geheim"] or not h else key(e["name"])})
    return aus


def vorhanden() -> dict[str, bool]:
    """Welche Keys gesetzt sind, ohne die Werte zu verraten. Für die spätere Status-Seite."""
    return {name: bool(key(name)) for name in BEKANNT}
