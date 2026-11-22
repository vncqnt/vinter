"""Fed-Tage: alle Zinsentscheidungen der US-Notenbank seit 2015 und wie Kurse an diesen Tagen reagierten.

Termine: offizieller FOMC-Kalender der Fed (federalreserve.gov, aktuelle Seite ab 2021, ältere Jahre auf
eigenen Seiten). Gezählt werden Sitzungen mit Entscheidung, auch Notsitzungen (März 2020); schriftliche
Abstimmungen ("notation vote"), Telefonkonferenzen und abgesagte Sitzungen nicht. Als Datum zählt die
Pressemitteilung, nicht der erste Sitzungstag (Notsitzung 2. März 2020, Senkung am 3. März).

Entscheidung: Obergrenze der Zielspanne (FRED DFEDTARU) vor und nach dem Termin. So ergibt sich
gesenkt / erhöht / gehalten mit der Schrittgröße in Basispunkten.

Reaktion: Die Fed entscheidet um 14 Uhr New Yorker Zeit (20 Uhr bei uns). Deshalb zwei Fenster:
"Tag" = Schluss am Handelstag davor bis Schluss am Tag der Entscheidung, "bis Folgetag" = bis zum
Schluss am Handelstag danach. Europäische Börsen haben um 20 Uhr schon zu, ihre Reaktion steckt erst
im zweiten Fenster.
"""
import html
import re
from datetime import date, timedelta

import pandas as pd
import requests

from ..cache import TAG, markt_lesen, markt_schreiben

KALENDER = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
HISTORISCH = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{jahr}.htm"
ZIEL_SERIE = "DFEDTARU"
AB_JAHR = 2015
KOPF = {"User-Agent": "Mozilla/5.0 (Vinter, privates Dashboard)"}

# Beginn eines Sitzungs-Eintrags: Zeile im aktuellen Kalender oder Überschrift auf den Jahresseiten
_EINTRAG = re.compile(
    r'fomc-meeting__month[^>]*><strong>(?P<monat>[^<]+)</strong></div>\s*<div class="fomc-meeting__date[^"]*">(?P<tag>[^<]+)</div>'
    r'|<h5[^>]*>(?P<kopf>.*?)</h5>', re.S)
_MITTEILUNG = re.compile(r"monetary(\d{8})a\.htm")
_OHNE = ("notation vote", "conference call", "cancelled")


def termine_lesen(seite: str) -> list[dict]:
    """Entscheidungstermine aus einer Kalenderseite der Fed (reine Funktion, getestet mit echten Seiten).

    Nur Einträge mit Pressemitteilung (also schon vorbei), ohne schriftliche Abstimmungen und Absagen.
    """
    treffer = list(_EINTRAG.finditer(seite))
    termine = []
    for i, m in enumerate(treffer):
        ende = treffer[i + 1].start() if i + 1 < len(treffer) else len(seite)
        if m.group("kopf") is not None:
            name = re.sub(r"<[^>]+>", "", html.unescape(m.group("kopf"))).strip()
            if "meeting" not in name.lower():
                continue
        else:
            name = f"{m.group('monat').strip()} {html.unescape(m.group('tag')).strip()}"
        if any(w in name.lower() for w in _OHNE):
            continue
        mitteilung = _MITTEILUNG.search(seite, m.end(), ende)
        if not mitteilung:
            continue
        d = mitteilung.group(1)
        termine.append({"datum": f"{d[:4]}-{d[4:6]}-{d[6:]}", "notsitzung": "unscheduled" in name.lower(),
                        "projektionen": "*" in name})
    return termine


def _abrufen(url: str) -> str:
    r = requests.get(url, timeout=30, headers=KOPF)
    r.raise_for_status()
    return r.text


def _zielspanne() -> pd.Series:
    """Obergrenze der Fed-Zielspanne je Tag (FRED DFEDTARU, ohne Key, über den gemeinsamen FRED-Abruf)."""
    from .abruf import fred
    return fred(ZIEL_SERIE)


def entscheidung(ziel: pd.Series, tag: date) -> tuple[str, int] | None:
    """gesenkt / erhöht / gehalten und Schritt in Basispunkten aus der Zielspanne vor und nach dem Termin."""
    ts = pd.Timestamp(tag)
    vor = ziel[ziel.index < ts]
    nach = ziel[ziel.index >= ts + pd.Timedelta(days=2)]
    if vor.empty or nach.empty:
        return None
    schritt = round((nach.iloc[0] - vor.iloc[-1]) * 100)
    return ("gesenkt" if schritt < 0 else "erhöht" if schritt > 0 else "gehalten"), schritt


def entscheidungen() -> list[dict]:
    """Alle Fed-Entscheidungen ab 2015, neueste zuletzt. Einen Tag im Cache (neue Termine gibt es alle ~6 Wochen)."""
    alt, alter = markt_lesen("fed-tage:termine")
    if alt and alter < TAG:
        return alt
    try:
        termine = {t["datum"]: t for t in termine_lesen(_abrufen(KALENDER))}
        bis = min([int(d[:4]) for d in termine] or [date.today().year])
        for jahr in range(AB_JAHR, bis):
            alt_jahr, _ = markt_lesen(f"fed-tage:jahr:{jahr}")  # vergangene Jahre ändern sich nicht mehr
            if alt_jahr is None:
                alt_jahr = termine_lesen(_abrufen(HISTORISCH.format(jahr=jahr)))
                markt_schreiben(f"fed-tage:jahr:{jahr}", alt_jahr)
            termine.update({t["datum"]: t for t in alt_jahr})
        ziel = _zielspanne()
    except Exception:
        if alt:
            return alt
        raise
    liste = []
    for d in sorted(termine):
        if d < f"{AB_JAHR}-01-01" or d > date.today().isoformat():
            continue
        e = entscheidung(ziel, date.fromisoformat(d))
        if not e:
            continue
        liste.append({**termine[d], "entscheidung": e[0], "schritt_bp": e[1]})
    markt_schreiben("fed-tage:termine", liste)
    return liste


def reaktionen(reihe: pd.Series, tage: list[str]) -> dict[str, dict]:
    """Je Termin: Veränderung am Tag (Schluss davor bis Schluss am ersten Handelstag ab dem Termin) und
    bis zum Folgetag. Fehlt der Kurs rund um den Termin (Lücke über 5 Tage, Reihe beginnt später), fällt er weg."""
    s = reihe.dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    idx = s.index
    aus = {}
    for d in tage:
        ts = pd.Timestamp(d)
        i = idx.searchsorted(ts)
        if i == 0 or i >= len(idx) or (idx[i] - ts).days > 4 or (ts - idx[i - 1]).days > 5:
            continue
        vor = s.iloc[i - 1]
        aus[d] = {"tag": float(s.iloc[i] / vor - 1),
                  "folge": float(s.iloc[i + 1] / vor - 1) if i + 1 < len(idx) else None}
    return aus


def normal(reihe: pd.Series, tage: list[str]) -> dict:
    """Übliche Bewegung an Tagen ohne Fed: Durchschnitt der Beträge über 1 und 2 Handelstage."""
    s = reihe.dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    eins, zwei = s.pct_change(), s / s.shift(2) - 1
    sperre = set()
    for d in tage:
        i = s.index.searchsorted(pd.Timestamp(d))
        sperre.update(range(max(0, i - 1), min(len(s), i + 3)))
    frei = [i for i in range(len(s)) if i not in sperre]
    return {"tag": float(eins.iloc[frei].abs().mean()), "folge": float(zwei.iloc[frei].abs().mean())}
