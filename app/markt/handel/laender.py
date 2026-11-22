"""Länder auf einen Standard bringen (ISO 3166 Alpha-3).

Grundlage ist die Länderliste von UN Comtrade (laender.json, einmal aus deren Referenzdateien erzeugt):
ISO-Codes, Comtrade-Nummer und ISO-Nummer (für die Weltkarte). Census schreibt Länder in Großbuchstaben
("KOREA, SOUTH"), der chinesische Zoll auf Englisch ("R. O. Korea"). Beides läuft über einen Namensvergleich,
Sonderfälle stehen in ALIAS. Was sich nicht zuordnen lässt, gibt None zurück und wird auf der Status-Seite gezählt.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

WELT = "WLD"  # Gesamtwert über alle Länder

# Kleinbuchstaben, nur Buchstaben und Leerzeichen -> ISO3
ALIAS = {
    "usa": "USA", "united states": "USA", "united states us": "USA", "united states of america": "USA",
    "korea south": "KOR", "r o korea": "KOR", "rep of korea": "KOR", "republic of korea": "KOR", "south korea": "KOR",
    "korea north": "PRK", "dem peoples rep of korea": "PRK",
    "taiwan": "TWN", "taiwan china": "TWN", "other asia nes": "TWN",
    "hong kong": "HKG", "hong kong china": "HKG", "china hong kong sar": "HKG",
    "macau": "MAC", "macao china": "MAC", "china macao sar": "MAC",
    "united kingdom": "GBR", "united kingdom uk": "GBR",
    "russia": "RUS", "russian federation": "RUS",
    "vietnam": "VNM", "viet nam": "VNM", "burma": "MMR", "myanmar": "MMR",
    "france": "FRA", "metropolitan france": "FRA", "india": "IND", "switzerland": "CHE",
    "norway": "NOR", "norway excluding svalbard and jan mayen": "NOR",
    "czech republic": "CZE", "czechia": "CZE", "turkey": "TUR", "turkiye": "TUR", "trkiye": "TUR",
    "iran": "IRN", "syria": "SYR", "laos": "LAO", "bolivia": "BOL", "venezuela": "VEN", "tanzania": "TZA",
    "moldova": "MDA", "brunei": "BRN", "cote divoire": "CIV", "ivory coast": "CIV",
    "congo democratic republic of the": "COD", "congo republic of the": "COG", "congo brazzaville": "COG", "congo kinshasa": "COD",
    "netherlands": "NLD", "saudi arabia": "SAU", "united arab emirates": "ARE", "south africa": "ZAF",
    "slovakia": "SVK", "macedonia": "MKD", "north macedonia": "MKD", "bosnia and herzegovina": "BIH",
    "micronesia": "FSM", "bahamas": "BHS", "gambia": "GMB", "eswatini": "SWZ", "swaziland": "SWZ",
    "cabo verde": "CPV", "cape verde": "CPV", "timor leste": "TLS", "east timor": "TLS",
    "west bank administered by israel": "PSE", "gaza strip administered by israel": "PSE",
    "kyrgyzstan": "KGZ", "dominican republic": "DOM", "st kitts and nevis": "KNA", "saint kitts and nevis": "KNA", "curacao": "CUW", "sint maarten": "SXM", "reunion": "REU", "central african republic": "CAF", "monaco": "MCO",
}


@lru_cache
def liste() -> dict:
    return json.loads((Path(__file__).parent / "laender.json").read_text(encoding="utf-8"))


def _schluessel(name: str) -> str:
    name = re.sub(r"^of which:\s*", "", str(name).strip(), flags=re.I)
    name = name.replace("’", "").replace("'", "")
    k = re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", name.lower())).strip()
    return re.sub(r"^st ", "saint ", k)


@lru_cache
def _namen() -> dict:
    aus = {_schluessel(v["name"]): k for k, v in liste().items()}
    aus.update(ALIAS)
    return aus


@lru_cache(maxsize=4096)
def iso3(name: str) -> str | None:
    """ISO3 zu einem Ländernamen aus einer beliebigen Quelle, None wenn unbekannt."""
    if not name:
        return None
    s = str(name).strip()
    if len(s) == 3 and s.upper() in liste():
        return s.upper()
    return _namen().get(_schluessel(s))


def von_comtrade(code: int) -> str | None:
    if int(code) == 0:
        return WELT
    for k, v in liste().items():
        if v["comtrade"] == int(code):
            return k
    return None


def comtrade_code(iso: str) -> int | None:
    return (liste().get(iso) or {}).get("comtrade")


def info(iso: str) -> dict:
    """Für die Oberfläche: ISO2 (Name übersetzt der Browser), ISO-Nummer (Weltkarte)."""
    if iso == WELT:
        return {"iso3": WELT, "iso2": None, "num": None, "name": "Welt"}
    v = liste().get(iso) or {}
    return {"iso3": iso, "iso2": v.get("iso2"), "num": v.get("num"), "name": v.get("name") or iso}
