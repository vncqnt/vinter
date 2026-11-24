"""Extraktion: Was steht in einer Schlagzeile? Firmen und Ticker, Länder, Institutionen, Personen, Ereignistyp.

Regelbasiert und schnell (kein großes Sprachmodell, damit es auch auf dem NUC flott läuft):
- Firmen: Namen aller S&P-500- und Nasdaq-100-Mitglieder (ohne "Inc.", "Corp." usw.), eigene Aliase aus
  daten/markt/news_begriffe.yaml, Cashtags ($NVDA), Börsenangaben "(NASDAQ: XYZ)", bei der SEC die CIK
  aus dem Titel (offizielle Zuordnung CIK → Ticker der SEC).
- Länder, Institutionen, Personen, Ereignistypen: Wortlisten aus news_begriffe.yaml.
Firmennamen werden mit Groß-/Kleinschreibung gesucht ("Target" die Firma, nicht "target" das Wort).
"""
import re
import threading

import yaml

from ...konfig import MARKT_DATEN, VORLAGEN

DATEI = MARKT_DATEN / "news_begriffe.yaml"
VORLAGE = VORLAGEN / "markt" / "news_begriffe.yaml"
_ENDUNGEN = re.compile(r"(,?\s+(Inc\.?|Incorporated|Corporation|Corp\.?|Company|Co\.?|Ltd\.?|Limited|plc|PLC|N\.V\.|S\.A\.|AG|SE|"
                       r"Holdings?|Group|Class [A-C]|\(The\)|& Co\.?|Common Stock|Ordinary Shares))+\s*$")
# Namen, die nach dem Kürzen nur noch ein gewöhnliches Wort wären
_ZU_ALLGEMEIN = {"Block", "Ball", "Match", "Fox", "News", "Equity", "Public", "General", "International", "American", "United",
                 "First", "Global", "Digital", "Realty", "Energy", "Corning", "Gap", "Live", "Dollar", "Federal", "Southern",
                 "Eaton", "Edison", "Entergy", "Progressive", "Ventas", "Copart", "Target Hospitality"}
_CASHTAG = re.compile(r"\$([A-Z]{1,5}(?:\.[A-Z])?)\b")
_BOERSE = re.compile(r"\((?:NASDAQ|Nasdaq|NYSE(?: American| Arca)?|OTC(?:QX|QB)?|TSX|LSE|Xetra)\s*:\s*([A-Z][A-Z.\-]{0,6})\)")
_SEC_TITEL = re.compile(r"^([0-9A-Z/\-]+) - (.+?) \((\d{6,10})\)")
# 8-K-Punkte → Ereignistyp (Reihenfolge im Dict = Vorrang, wenn mehrere Punkte in einer Meldung stehen)
SEC_ITEMS = {"2.02": "geschaeftszahlen", "2.01": "uebernahme", "5.01": "uebernahme", "1.01": "vertrag", "2.06": "abschreibung",
             "4.02": "bilanzproblem", "2.04": "schulden", "2.05": "umbau", "5.02": "personalien", "3.02": "kapitalmassnahme",
             "2.03": "finanzierung", "1.03": "insolvenz", "3.01": "boersennotierung", "7.01": "mitteilung", "8.01": "pflichtmeldung"}
_sperre = threading.Lock()
_stand = {"mtime": None, "regeln": None, "firmen": None, "firmen_zeit": 0.0}


def _muster(woerter: list[str], gross: bool = False) -> re.Pattern | None:
    teile = []
    for w in woerter:
        w = str(w)
        if len(w) > 2 and w.startswith("/") and w.endswith("/"):
            teile.append(w[1:-1])
        else:
            teile.append(r"(?<![\w$])" + re.escape(w) + r"(?:s|es)?(?![\w])")
    if not teile:
        return None
    return re.compile("|".join(teile), 0 if gross else re.I)


def _begriffe() -> dict:
    """news_begriffe.yaml laden (bei Änderung neu)."""
    if not DATEI.exists():
        import shutil
        DATEI.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(VORLAGE, DATEI)
    mtime = DATEI.stat().st_mtime
    if _stand["mtime"] != mtime:
        roh = yaml.safe_load(DATEI.read_text(encoding="utf-8")) or {}
        regeln = {}
        for gruppe in ("laender", "institutionen", "personen", "ereignisse"):
            regeln[gruppe] = [(name, _muster(woerter)) for name, woerter in (roh.get(gruppe) or {}).items() if woerter]
        regeln["aliase"] = {str(k): str(v) for k, v in (roh.get("firmen_aliase") or {}).items()}
        _stand.update(mtime=mtime, regeln=regeln, firmen=None)
    return _stand["regeln"]


def kurzname(name: str) -> str:
    """'Micron Technology, Inc.' → 'Micron Technology'."""
    n = re.sub(r"^The\s+", "", name.strip())
    return _ENDUNGEN.sub("", n).strip(" ,.")


def _firmen() -> tuple[re.Pattern | None, dict]:
    """Ein großes Muster aller Firmennamen und die Zuordnung Name → Ticker (einmal am Tag neu)."""
    import time
    regeln = _begriffe()
    if _stand["firmen"] is not None and time.time() - _stand["firmen_zeit"] < 24 * 3600:
        return _stand["firmen"]
    namen: dict[str, str] = {}
    try:
        from ..indizes import mitglieder
        for index in ("sp500", "ndx"):
            for m in mitglieder(index):
                k = kurzname(m["name"])
                if len(k) >= 3 and k not in _ZU_ALLGEMEIN:
                    namen.setdefault(k, m["ticker"])
    except Exception:
        pass  # ohne Netz nur die Aliase
    namen.update(regeln["aliase"])
    sortiert = sorted(namen, key=len, reverse=True)  # längere Namen zuerst ("Goldman Sachs" vor "Goldman")
    muster = re.compile(r"(?<![\w])(" + "|".join(re.escape(n) for n in sortiert) + r")(?:'s)?(?![\w-])")  # nicht "Target-consistent" if sortiert else None
    _stand.update(firmen=(muster, namen), firmen_zeit=time.time())
    return muster, namen


def _sec_tickers() -> dict:
    """CIK → Ticker aus der offiziellen SEC-Liste (einmal am Tag)."""
    from ..abruf import TAG, gecacht, hole

    def holen():
        d = hole("https://www.sec.gov/files/company_tickers.json", sec=True).json()
        return {int(v["cik_str"]): v["ticker"].replace(".", "-") for v in d.values()}
    try:
        return gecacht("nachrichten:sec_tickers", TAG, holen)
    except Exception:
        return {}


def eilig(titel: str) -> bool:
    """Squawk-Stil: '*TRUMP SAYS …', 'BREAKING:' oder fast nur Großbuchstaben."""
    if titel.startswith("*") or re.match(r"^(BREAKING|EILMELDUNG|JUST IN|ALERT)\b", titel, re.I):
        return True
    buchstaben = [z for z in titel if z.isalpha()]
    return len(buchstaben) >= 15 and sum(z.isupper() for z in buchstaben) / len(buchstaben) > 0.7


def extrahieren(titel: str, text: str = "", quelle: str = "") -> dict:
    """Alles, was sich aus Schlagzeile (und Anfang des Textes) erkennen lässt."""
    with _sperre:
        regeln = _begriffe()
        firmen_muster, namen = _firmen()
    inhalt = f"{titel}\n{(text or '')[:400]}"
    ticker: list[str] = []
    firmen: list[dict] = []

    def dazu(name: str, t: str | None):
        if t and t not in ticker:
            ticker.append(t)
        if name and all(f["name"] != name for f in firmen):
            firmen.append({"name": name, "ticker": t})

    m = _SEC_TITEL.match(titel) if quelle.startswith("SEC") else None
    if m:
        name = kurzname(m.group(2).title() if m.group(2).isupper() else m.group(2))
        dazu(name, _sec_tickers().get(int(m.group(3))))
    for t in _CASHTAG.findall(inhalt) + _BOERSE.findall(inhalt):
        dazu("", t)
    # Firmennamen: im Squawk-Stil (alles groß) auch in Großbuchstaben suchen
    such_text = inhalt
    if firmen_muster:
        for treffer in firmen_muster.finditer(such_text):
            dazu(treffer.group(1), namen.get(treffer.group(1)))
        if eilig(titel):
            gross = {n.upper(): n for n in namen}
            for wort in re.findall(r"[A-Z][A-Z&.\- ]{2,40}[A-Z]", titel):
                for teil in re.split(r"\s{2,}|:|,", wort):
                    n = gross.get(teil.strip())
                    if n:
                        dazu(n, namen[n])
    firmen = [f for f in firmen if f["name"]] + [{"name": "", "ticker": t} for t in ticker if all(f["ticker"] != t for f in firmen if f["name"])]
    ergebnis = {"ticker": ticker, "firmen": [f for f in firmen if f["name"]]}
    for gruppe in ("laender", "institutionen", "personen"):
        ergebnis[gruppe] = [name for name, mu in regeln[gruppe] if mu and mu.search(inhalt)]
    if quelle.startswith("SEC"):
        # SEC: die offiziellen Item-Nummern der 8-K sagen genauer, worum es geht, als Wörter im Formulartext
        punkte = re.findall(r"Item (\d\.\d\d)", text or "")
        ereignisse = list(dict.fromkeys(SEC_ITEMS[p] for p in punkte if p in SEC_ITEMS)) or ["pflichtmeldung"]
    else:
        ereignisse = [name for name, mu in regeln["ereignisse"] if mu and mu.search(inhalt)]
    ergebnis["ereignisse"] = ereignisse
    ergebnis["ereignis"] = ereignisse[0] if ereignisse else "sonstiges"
    ergebnis["eil"] = eilig(titel)
    return ergebnis
