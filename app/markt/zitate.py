"""Reden und Zitate: Was Trump, Notenbanker, Minister und Chefs gerade sagen.

Eilmeldungsdienste wie FinancialJuice melden Aussagen im Format "Sprecher: Aussage", z. B.
"ECB's Lagarde: Inflation is moving towards target" oder "Trump on Nvidia: ...". Vinter erkennt
diese Meldungen, bündelt die Zitate einer Person, die kurz nacheinander kommen, zu einem Auftritt
(Rede, Interview, Pressekonferenz) und wählt daraus die Kernaussagen.

Die Kernaussagen schreibt keine KI neu: Das lokale Sprachmodell sucht unter den echten Zitaten die
heraus, die das Gesagte am besten vertreten (größte Ähnlichkeit zum Durchschnitt aller Zitate,
ohne Wiederholungen). Bei Notenbankern zählt Vinter zusätzlich Signalwörter für eine grobe
Tendenz: eher straff (gegen Zinssenkungen) oder eher locker (für Zinssenkungen).
"""
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import numpy as np

from ..cache import markt_lesen as cache_lesen, markt_schreiben as cache_schreiben

PAUSE = timedelta(minutes=60)       # längere Pause = neuer Auftritt
LIVE = timedelta(minutes=20)        # so frisch muss das letzte Zitat sein, damit "LIVE" dasteht
KERN = 3                            # so viele Kernaussagen
GLEICH = 0.85                       # ähnlicher als das gilt als dieselbe Aussage
GUELTIG = 5 * 60

_ZITAT = re.compile(r"^(?P<wer>[^:]{2,60}?):\s+(?P<text>\S.{3,})$")
# Was wie ein Sprecher aussieht, aber eine Kennzahl oder Grafik ist.
_KEIN_SPRECHER = re.compile(r"(?i)\b(index|chart|sentiment|matrix|volatility|probabilit|actual|forecast|data|yields?|futures)\b|\d")
_UEBER = re.compile(r"^(?P<wer>.+?)\s+on\s+(?P<thema>.+)$", re.IGNORECASE)   # "Trump on Nvidia"
_BESITZ = re.compile(r"['’]s\b")

KATEGORIEN = [
    ("Fed", re.compile(r"(?i)\b(fed|fomc|federal reserve|powell|warsh)\b")),
    ("EZB", re.compile(r"(?i)\b(ecb|ezb|lagarde|bundesbank)\b")),
    ("Notenbanken", re.compile(r"(?i)\b(boj|bank of japan|boe|bank of england|snb|rba|rbnz|boc|pboc|riksbank|norges)\b")),
    ("Trump & US-Regierung", re.compile(r"(?i)\b(trump|white house|vance|bessent|lutnick|rubio|hassett|leavitt|greer|navarro|us .*secretary|us trade representative|us treasury)\b")),
    ("Unternehmen", re.compile(r"(?i)\b(ceo|cfo|bofa|bank of america|citi(group)?|jpmorgan|goldman|tesla|nvidia|apple|microsoft|amazon|alphabet|google|meta|blackrock|crédit agricole)\b")),
    ("Geopolitik", re.compile(r"(?i)\b(kremlin|putin|russia|ukraine|zelensky|iran|israel|idf|china|xi|nato|eu|von der leyen|kallas|minister|army)\b")),
]
STRAFF = re.compile(r"(?i)\b(hike|hikes|hiking|raise rates|higher for longer|tighten\w*|restrictive|premature to cut|not (yet )?time to cut|upside risks? to inflation|inflation (is )?(too )?(high|sticky|persistent)|vigilant|second-round)\b")
LOCKER = re.compile(r"(?i)\b(cut|cuts|cutting|easing|ease|lower rates|accommodative|downside risks?|slowdown|weaken\w*|room to (cut|ease)|labor market (is )?(cooling|softening))\b")


def _ohne_akzente(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _sprecher(roh: str) -> tuple[str, str, str | None] | None:
    """(Anzeigename, Schlüssel, Thema) oder None, wenn es kein Sprecher ist."""
    # "Kremlin Aide, contradicting US Negotiators" ist derselbe Sprecher wie "Kremlin Aide".
    wer, thema = roh.split(",")[0].strip(), None
    if _KEIN_SPRECHER.search(wer):
        return None
    ueber = _UEBER.match(wer)
    if ueber:
        wer, thema = ueber.group("wer").strip(), ueber.group("thema").strip()
    worte = re.findall(r"[\w’'.-]+", _BESITZ.sub("", wer))
    if not worte or len(worte) > 7:
        return None
    # Der Nachname ist meist das letzte Wort: "ECB's Vujcic" und "ECB President ... Vujcic" landen zusammen.
    schluessel = _ohne_akzente(worte[-1]).lower().strip(".’'")
    return wer.replace("’", "'"), schluessel, thema


def _kategorie(wer: str, texte: list[str]) -> str:
    for name, muster in KATEGORIEN:
        if muster.search(wer):
            return name
    gesamt = " ".join(texte)
    for name, muster in KATEGORIEN[3:]:
        if muster.search(gesamt):
            return name
    return "Sonstige"


def _kernaussagen(texte: list[str]) -> list[int]:
    """Indizes der Zitate, die das Gesagte am besten vertreten, in zeitlicher Reihenfolge."""
    if len(texte) <= KERN:
        return list(range(len(texte)))
    try:
        from .news import lade_modell
        v = lade_modell().encode(texte, normalize_embeddings=True)
    except Exception:
        # Ohne Modell: die längsten Aussagen, sie enthalten meist am meisten.
        return sorted(sorted(range(len(texte)), key=lambda i: -len(texte[i]))[:KERN])
    mitte = v.mean(axis=0)
    mitte /= np.linalg.norm(mitte) or 1
    gewaehlt = []
    for i in np.argsort(-(v @ mitte)):
        if all(float(v[i] @ v[j]) < GLEICH for j in gewaehlt):
            gewaehlt.append(int(i))
        if len(gewaehlt) == KERN:
            break
    return sorted(gewaehlt)


def _tendenz(texte: list[str]) -> dict | None:
    straff = sum(len(STRAFF.findall(t)) for t in texte)
    locker = sum(len(LOCKER.findall(t)) for t in texte)
    if straff + locker < 2 or straff == locker:
        return None
    return {"richtung": "straff" if straff > locker else "locker", "straff": straff, "locker": locker}


def _wortlaut(meldungen: list[dict], schluessel: str, tag: str) -> str | None:
    """Offizielle Rede bei Fed oder EZB vom selben Tag, erkannt am Nachnamen im Titel."""
    for m in meldungen:
        if not any(q in ("Fed", "EZB") for q in m["quellen"]) or m["zeit"][:10] != tag:
            continue
        if re.search(rf"\b{re.escape(schluessel)}\b", _ohne_akzente(m["titel"]).lower()):
            return m["link"]
    return None


def auftritte() -> dict:
    alt, alter = cache_lesen("zitate:auftritte2")
    if alt and alter < GUELTIG:
        return alt
    from .nachrichten import kompatibel
    daten = {"meldungen": kompatibel.meldungen(72)}
    zitate = []
    for m in daten["meldungen"]:
        treffer = _ZITAT.match(m["titel"])
        sprecher = _sprecher(treffer.group("wer")) if treffer else None
        if sprecher:
            wer, schluessel, thema = sprecher
            zitate.append({"wer": wer, "schluessel": schluessel, "thema": thema, "text": treffer.group("text").strip(),
                           "zeit": m["zeit"], "link": m["link"], "quelle": m["quellen"][0]})
    zitate.sort(key=lambda z: z["zeit"])

    # Zitate derselben Person mit weniger als PAUSE Abstand gehören zu einem Auftritt.
    offen: dict[str, dict] = {}
    alle = []
    for z in zitate:
        zeit = datetime.fromisoformat(z["zeit"])
        a = offen.get(z["schluessel"])
        if a and zeit - datetime.fromisoformat(a["zitate"][-1]["zeit"]) <= PAUSE:
            a["zitate"].append(z)
        else:
            a = {"schluessel": z["schluessel"], "zitate": [z]}
            offen[z["schluessel"]] = a
            alle.append(a)

    jetzt = datetime.now(timezone.utc)
    ergebnis = []
    for a in alle:
        liste = a["zitate"]
        texte = [z["text"] for z in liste]
        namen = sorted({z["wer"] for z in liste}, key=len)
        wer = namen[-1] if len(namen[-1]) < 40 else namen[0]   # der ausführlichste, aber kurze Name
        kategorie = _kategorie(wer, texte)
        ende = datetime.fromisoformat(liste[-1]["zeit"])
        ergebnis.append({
            "wer": wer, "kategorie": kategorie,
            "start": liste[0]["zeit"], "ende": liste[-1]["zeit"],
            "live": jetzt - ende <= LIVE,
            "themen": sorted({z["thema"] for z in liste if z["thema"]}),
            "kern": _kernaussagen(texte),
            "tendenz": _tendenz(texte) if kategorie in ("Fed", "EZB", "Notenbanken") else None,
            "wortlaut": _wortlaut(daten["meldungen"], a["schluessel"], liste[0]["zeit"][:10]) if kategorie in ("Fed", "EZB") else None,
            "zitate": [{"text": z["text"], "zeit": z["zeit"], "link": z["link"], "thema": z["thema"]} for z in liste],
        })
    ergebnis.sort(key=lambda a: a["ende"], reverse=True)
    neu = {"auftritte": ergebnis, "aktualisiert": jetzt.isoformat(),
           "kategorien": [k for k, _ in KATEGORIEN] + ["Sonstige"]}
    cache_schreiben("zitate:auftritte2", neu)
    return neu
