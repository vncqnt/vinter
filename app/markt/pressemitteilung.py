"""Zahlen aus einer eingefügten Quartals-Pressemitteilung herauslesen (Stufe 1: feste Stichwörter, ohne KI).

Eingabe ist der Text, den man von der Investor-Relations-Seite einer Firma kopiert (englisch oder deutsch).
Gesucht werden die üblichen Kennzahlen (Umsatz, Gewinn je Aktie, Margen, Cashflow …), die Prognose und
Signalwörter für den Ton. Zu jedem Wert wird die Fundstelle mitgegeben, damit man ihn im Text nachprüfen kann.
Was nicht sicher erkannt wird, fehlt lieber, statt geraten zu werden.

Gebaut und geprüft an echten Mitteilungen (8-K, Anlage 99.1) von NVIDIA, Apple, Microsoft, Micron, AMD,
Uber, Cisco, Applied Materials, Walmart, Target, Alphabet und Amazon. Typische Formulierungen:
- "Revenue of $115.8 billion, up 103% from a year ago" (Wert, dann Veränderung)
- "GAAP and non-GAAP earnings per diluted share were $2.70 and $2.68, respectively"
- "Earnings per Share: GAAP: $1.14; Non-GAAP: $1.40"
- "Diluted earnings per share was $4.92 … on a GAAP basis, and was $4.89 … on a non-GAAP basis"
- Abschnitte "Guidance" / "Outlook" mit Spannen ("$X to $Y", "$X, plus or minus 3%")
- Tabellenzeilen "Net income<Tab>$ 30,711<Tab>$ 27,466" mit "(In millions)" darüber

Der Ablauf:
1. Text in Sätze teilen (Aufzählungspunkte und Zeilen zählen auch als Satz). Tabellenzeilen (Tabs zwischen
   Zellen) und Sätze im Ausblick-Abschnitt kommen nicht in die Ist-Zahlen.
2. Für jede Kennzahl alle Sätze mit dem Stichwort suchen und bewerten: Segmente ("Data Center revenue",
   "Cloud-Umsatz") und Jahreszahlen abwerten, Highlights oben bevorzugen.
3. Im Abschnitt hinter dem Stichwort die Werte lesen und als GAAP oder bereinigt einordnen.
4. Fehlt eine Kennzahl im Fließtext, in den Tabellen nachsehen (nur mit bekannter Einheit, z. B. "in millions").
"""
import re
from collections import Counter

# ---- Zahlen ----

ZAHL = r"\d{1,3}(?:[.,']\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
WERT = re.compile(
    rf"(?P<neg>\((?=\s?(?:US\$|\$|€)?\s?\d[\d.,]*\))|[−–-](?=\s?(?:US\$|\$|€|£)\s?\d))?\s?(?P<w1>US\$|\$|€|£|EUR\b|USD\b|CHF\b)?\s?(?P<z>{ZAHL})"
    r"(?P<p>\s?(?:%|percent\b|per\s?cent\b|Prozent\b|Prozentpunkte\b|percentage points?\b|basis points?\b|Basispunkte\b|bps\b|pts\b))?"
    r"(?:\s?(?P<m>billion|million|thousand|bn\b|mn\b|mrd\.?|mio\.?|milliarden|millionen|tsd\.?|(?<=\d)B\b|(?<=\d)M\b))?"
    r"(?:\s?(?P<w2>€|EUR\b|Euro\b|USD\b|US-Dollar\b|Dollar\b|CHF\b))?",
    re.I,
)
FAKTOR = {"billion": 1e9, "bn": 1e9, "b": 1e9, "mrd": 1e9, "milliarden": 1e9,
          "million": 1e6, "mn": 1e6, "m": 1e6, "mio": 1e6, "millionen": 1e6,
          "thousand": 1e3, "tsd": 1e3}
WAEHRUNG = {"$": "USD", "us$": "USD", "usd": "USD", "dollar": "USD", "us-dollar": "USD", "€": "EUR", "eur": "EUR",
            "euro": "EUR", "£": "GBP", "chf": "CHF"}


def zahl(text: str, deutsch: bool) -> float:
    """'1,234.5' (englisch) oder '1.234,5' (deutsch) als Zahl."""
    t = text.replace("'", "")
    if deutsch:
        # Deutsch: Punkt trennt Tausender. Ein einzelner Punkt mit 1 bis 2 Nachkommastellen ist ein Dezimalpunkt.
        if t.count(".") == 1 and "," not in t and len(t.split(".")[1]) != 3:
            return float(t)
        return float(t.replace(".", "").replace(",", "."))
    if t.count(",") == 1 and "." not in t and len(t.split(",")[1]) != 3:
        return float(t.replace(",", "."))
    return float(t.replace(",", ""))


def werte_in(text: str, deutsch: bool) -> list[dict]:
    """Alle Zahlen mit Einheit in einem Textstück, in der Reihenfolge des Textes."""
    aus = []
    for m in WERT.finditer(text):
        try:
            z = zahl(m.group("z"), deutsch)
        except ValueError:
            continue
        einheit = (m.group("p") or "").strip().lower()
        faktor = FAKTOR.get((m.group("m") or "").lower().rstrip("."), 1)
        w = (m.group("w1") or m.group("w2") or "").lower()
        neg = bool(m.group("neg"))
        start = m.start("neg") if neg else m.start("w1") if m.group("w1") else m.start("z")
        aus.append({
            "start": start, "ende": m.end(), "roh": m.group(0).strip(), "zahl": z,
            "prozent": einheit in ("%", "percent", "per cent", "prozent"),
            "punkte": bool(einheit) and einheit not in ("%", "percent", "per cent", "prozent"),
            "faktor": faktor, "hat_faktor": bool(m.group("m")), "waehrung": WAEHRUNG.get(w), "negativ": neg,
            "jahr": not m.group("w1") and not m.group("w2") and not m.group("m") and not einheit
                    and re.fullmatch(r"(19|20)\d\d", m.group("z")) is not None,
        })
    return aus


# ---- Text vorbereiten ----

ABKUERZUNG = re.compile(r"(?:\b(?:Mrd|Mio|Tsd|bzw|ca|vs|Nr|Inc|Corp|Co|Ltd|No|approx|U\.S|z\.B|d\.h|u\.a|Q|St|Adj)\.|\b[A-Z]\.)$")
TRENNER = re.compile(r"\n|•|·|◦|▪|▪|•|◦")


def ist_tabellenzeile(zeile: str) -> bool:
    return "\t" in zeile.strip("\t ")


def saetze(text: str) -> list[dict]:
    """Sätze mit Position, ob sie aus einer Tabellenzeile stammen und ob sie im Ausblick-Abschnitt stehen."""
    aus = []
    for zeile in text.split("\n"):
        tabelle = ist_tabellenzeile(zeile)
        for stueck in TRENNER.split(zeile.replace("\t", " ") if not tabelle else zeile):
            teile = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ\"„“(])", stueck) if not tabelle else [stueck]
            teil = ""
            for s in teile:
                teil = f"{teil} {s}".strip() if teil else s
                if ABKUERZUNG.search(teil):
                    continue
                if teil.strip():
                    aus.append({"text": teil.strip(), "tabelle": tabelle})
                teil = ""
            if teil.strip():
                aus.append({"text": teil.strip(), "tabelle": tabelle})
    # Ausblick-Abschnitt: nach einer kurzen Überschrift wie "Q2 FY 2027 Guidance:" oder "Outlook",
    # bis zur nächsten Überschrift ohne Ausblick-Wort
    ausblick, periode, skala = 0, None, None
    for i, s in enumerate(aus):
        s["pos"] = i / max(1, len(aus))
        t = s["text"]
        sk = SKALA.search(t)
        if sk and not re.search(r"\t\$?\s?\d", t):
            skala = _skala(sk.group(0))
        s["skala"] = skala
        zahlen = re.search(r"\d[\d.,]*\s?(%|billion|million|Mrd|Mio)|\$\s?\d", t)
        kopf = not zahlen and ((t.endswith(":") and len(t) < 200) or (len(t) < 90 and len(t.split()) <= 8))
        if kopf and re.search(r"(guidance|outlook|Ausblick|Prognose)\d?\b", t, re.I) and (t.endswith(":") or len(t.split()) <= 5) \
                and not (ausblick and len(t.split()) > 8) and not re.match(r"(rais|lower|reaffirm|cut|hebt|senkt|bestätigt)", t, re.I):
            ausblick, periode = 25, _periode(t)
            s["kopf"] = True
            continue
        if kopf and ausblick and not s["tabelle"] and len(t.split()) <= 6 and not t.startswith("(") and not re.search(r"(revenue|sales|margin|EPS|earnings|income|Umsatz|Marge|Ergebnis|Q[1-4]|FY|20\d\d|millions|thousands|billions|Mio|Mrd)", t, re.I):
            ausblick = 0
        # Ergebnistabellen (3 und mehr Zahlenspalten ohne Spanne) oder der rechtliche Hinweis beenden den Ausblick
        if ausblick and s["tabelle"] and len(re.findall(r"\t\(?\$?\s?\d", "\t" + t.split("\t", 1)[-1])) >= 3 and not re.search(r"\+/-|±|\bto\b|\d\s?[-–]\s?\$?\d", t):
            ausblick = 0
        if ausblick and (re.match(r"(?:forward-looking\s+statements|safe\s+harbor|this\s+(?:press\s+)?release\s+(?:\w+\s+){0,4}contains|zukunftsgerichtete)", t, re.I)
                         or re.search(r"\btoday\s+(?:reported|announced)|\bberichtete\s+heute", t, re.I)):
            ausblick = 0
        if ausblick and periode is None and not s["tabelle"]:
            periode = _periode(t)
        s["im_ausblick"] = ausblick > 0
        s["ausblick_periode"] = periode if ausblick else None
        ausblick = max(0, ausblick - 1)
    return aus


def ist_deutsch(text: str) -> bool:
    w = re.findall(r"\b(der|die|das|und|im|des|Umsatz|Quartal|Ergebnis|gegenüber)\b", text[:20000])
    e = re.findall(r"\b(the|and|of|revenue|quarter|income|compared)\b", text[:20000], re.I)
    return len(w) > len(e)


# ---- Kennzahlen ----

BEREINIGT = r"non-?GAAP|adjusted|adj\.|core|bereinigte?[sn]?|non-?IFRS"
# key: (Anzeige, Art, Stichwort-Muster, Variante). Variante "adj" = nur bereinigte Werte, "gaap" = keine bereinigten
EPS_MUSTER = (r"(?:(?:" + BEREINIGT + r")\s+)?(?:GAAP\s+)?(?:diluted\s+)?(?:net\s+)?(?:earnings|income|loss)\s+per\s+(?:diluted\s+)?(?:common\s+)?share"
              r"|(?:(?:" + BEREINIGT + r")\s+)?(?:GAAP\s+)?(?:diluted\s+)?EPS|earnings\s+per\s+diluted\s+share"
              r"|(?:(?:" + BEREINIGT + r")\s+)?(?:verwässerte[sn]?\s+|unverwässerte[sn]?\s+)?(?:Ergebnis|Gewinn)\s+je\s+Aktie")
KENNZAHLEN = {
    "umsatz": ("Umsatz", "geld", r"(?:total\s+|net\s+)?revenues?|net\s+sales|total\s+sales|(?:Konzern|Gesamt)?[Uu]msatz(?:erlöse)?|Umsatzerlöse|Erlöse", "gaap"),
    "eps_adj": ("EPS bereinigt", "eps", EPS_MUSTER, "adj"),
    "eps": ("EPS (GAAP)", "eps", EPS_MUSTER, "gaap"),
    "netto": ("Nettogewinn", "geld", r"(?:GAAP\s+)?net\s+(?:income|earnings|loss|profit)(?:\s+attributable\s+to\s+[\w\s.,]{1,40}?)?|Konzern(?:ergebnis|gewinn|überschuss)|Nettogewinn|Nettoverlust|Periodenergebnis|Gewinn\s+nach\s+Steuern|Quartalsgewinn", "gaap"),
    "operativ": ("Operatives Ergebnis", "geld", r"(?:GAAP\s+)?operating\s+(?:income|profit|loss)|(?:total\s+)?income\s+from\s+operations|operative[sn]?\s+Ergebnis|Betriebsergebnis|EBIT\b", "gaap"),
    "brutto_marge": ("Bruttomarge", "prozent", r"gross\s+margins?|Bruttomarge|Bruttogewinnmarge", "gaap_lieber"),
    "op_marge": ("Operative Marge", "prozent", r"operating\s+margins?|operative\s+Marge|EBIT-Marge|Betriebsmarge", "gaap_lieber"),
    "fcf": ("Free Cashflow", "geld", r"free\s+cash\s*flows?|Free\s+Cash\s*-?[Ff]low|freier\s+Cashflow", "egal"),
    "ocf": ("Operativer Cashflow", "geld", r"(?:net\s+)?cash\s+(?:provided\s+by|from|generated\s+(?:by|from))\s+operat\w+(?:\s+activities)?|operating\s+cash\s*flows?|operativer\s+Cashflow|Cashflow\s+aus\s+(?:der\s+)?(?:laufenden\s+)?(?:Geschäfts|betrieblicher)\w*", "egal"),
    "capex": ("Capex", "geld", r"capital\s+expenditures?|capex|Investitionsausgaben|Sachinvestitionen", "egal"),
    "dividende": ("Dividende je Aktie", "eps", r"(?:quarterly\s+)?(?:cash\s+)?dividends?\s+(?:of|per\s+share|payable)|Dividende\s+(?:von|je\s+Aktie|in\s+Höhe)", "egal"),
    "rueckkauf": ("Aktienrückkäufe", "geld", r"repurchased|share\s+repurchases?|stock\s+repurchases?|buybacks?|Aktienrückk(?:auf|äufe)|zurückgekauft", "egal"),
}
STICHWORT = {k: re.compile(rf"(?<![\w\-]){v[2]}(?![A-Za-zÄÖÜäöüß\-])", re.I) for k, v in KENNZAHLEN.items()}
ALLE_STICHWOERTER = re.compile("|".join(f"(?:{v[2]})" for v in KENNZAHLEN.values()), re.I)
IMMER_POSITIV = {"capex", "rueckkauf", "dividende"}

AUSBLICK = re.compile(r"\b(outlook|guidance|expects?|expected\s+to|anticipates?|forecasts?|projects?|projected|will\s+be\s+(?:approximately|between|in\s+the\s+range)|plans?\s+to|Ausblick|Prognose|erwartet|erwarten|rechnet|rechnen|geht\s+.{0,40}aus|für\s+(?:das\s+)?(?:Gesamtjahr|Geschäftsjahr)\s+20\d\d)", re.I)
NICHT_AUSBLICK = re.compile(r"(than\s+expected|als\s+erwartet|expectations|Erwartungen|exceed\w*|übertroffen|high\s+end\s+of\s+(?:our|its|the)\s+(?:outlook|guidance)|above\s+(?:the\s+)?(?:high\s+end|top)|guidance\s+range|forward-looking)", re.I)
BASIS = re.compile(r"(is\s+based\s+on|based\s+on\s+the\s+following|basis\s+for|Basis\s+für|ausgehend\s+von)", re.I)
JAHR = re.compile(r"(full[- ]year|fiscal\s+(?:year\s+)?20\d\d(?!\s+(?:first|second|third|fourth))|fiscal\s+year|\bFY\s?'?\d\d\b|twelve\s+months|\bTTM\b|nine\s+months|six\s+months|year[- ]to[- ]date|\bYTD\b|first\s+half|Gesamtjahr|Geschäftsjahr|Neunmonats|neun\s+Monat|ersten\s+(?:neun|sechs)\s+Monat|Halbjahr|seit\s+Jahresbeginn|annual)", re.I)
QUARTALSWORT = re.compile(r"(quarter|quarterly|\bQ[1-4]\b|Quartal)", re.I)
VERLUST = re.compile(r"(loss|Verlust)", re.I)

ERLAUBT_DAVOR = re.compile(r"^(record|quarterly|total|net|gaap|non-gaap|consolidated|group|company|company's|reported|the|of|and|with|its|our|a|an|in|for|posted|delivered|achieved|generated|first|second|third|fourth|q[1-4]|fiscal|der|die|das|den|dem|ein|einen|einem|einer|konzern|rekord|im|auf|mit|und|bei|beim|diluted|verwässerte|unverwässerte|third-quarter|second-quarter|first-quarter|fourth-quarter|quartals|gesamter|gesamte|operating|q\d|\d{4}|adjusted|adj|basic|cash|reports?|reported|was|were|as|on|basis|non-ifrs|ifrs|share|per|eps|strong|higher|lower|bereinigte|bereinigtes|bereinigter)$", re.I)
SEGMENT = re.compile(r"(segment|division|geographic|by\s+region|business(?:es)?\b|Segment|Sparte|Bereich|Region|organic|organisch|constant\s+currenc|währungsbereinigt|cloud|services\b|products\b|advertising|subscription|eCommerce|data\s+center|gaming|automotive|software|hardware)", re.I)

AENDERUNG = re.compile(
    rf"(?P<r>\bup\b|\bdown\b|increased?|increasing|decreased?|decreasing|\brose\b|\bfell\b|\bgrew\b|grow(?:th|ing)?|declined?|declining|\bhigher\b|\blower\b|"
    rf"stieg|steigerte|sank|gestiegen|gesunken|wuchs|gewachsen|Anstieg|Rückgang|Wachstum|Zuwachs|Plus\b|Minus\b|verbesserte|verringerte|erhöhte|ging\s+.{{0,20}}zurück|legte\s+.{{0,20}}zu|nahm\s+.{{0,20}}(?:zu|ab))"
    rf"[^%\d$€]{{0,30}}?(?P<v>[+−–-])?\s?(?P<z>{ZAHL})\s?(?:%|percent|per\s?cent|Prozent)"
    rf"|(?P<v2>[+−–-])\s?(?P<z2>{ZAHL})\s?(?:%|Prozent)"
    rf"|(?P<z3>{ZAHL})\s?(?:%|percent|per\s?cent|Prozent)\s+(?P<r3>increase|decrease|growth|higher|lower|rise|decline|Anstieg|Rückgang|Wachstum|Zuwachs|mehr|weniger|über|unter)",
    re.I,
)
RUNTER = re.compile(r"(down|decreas|fell|declin|lower|sank|gesunken|Rückgang|Minus|verringerte|zurück|weniger|unter|\babn?\b|nahm\s+.{0,20}ab|[−–-])", re.I)
VQ = re.compile(r"(sequential|quarter[- ]over[- ]quarter|q/q|qoq|from\s+(?:the\s+)?(?:prior|previous|last)\s+quarter|from\s+Q[1-4]|than\s+(?:the\s+)?(?:prior|previous)\s+quarter|for\s+the\s+prior\s+quarter|Vorquartal|gegenüber\s+dem\s+(?:dritten|zweiten|ersten|vierten)\s+Quartal)", re.I)
VERGLEICH = re.compile(r"(compared\s+(?:with|to)|versus|\bvs\.?|against|gegenüber|Vorjahr(?:esquartal|eszeitraum)?\s*:?|im\s+Vorjahr|a\s+year\s+(?:ago|earlier)|prior[- ]year|year[- ]ago|same\s+(?:period|quarter)|prior\s+quarter|previous\s+quarter)", re.I)
ALS_AENDERUNG = re.compile(r"(\bup\b|\bdown\b|\bby\b|increase\s+of|decrease\s+of|increased|decreased|higher|lower|\bum\b|Plus\s+von|Minus\s+von)\s*$", re.I)
NICHT_GELD_DANACH = re.compile(r"^\s*(?:of\s+(?:its\s+|the\s+company's\s+)?)?(shares|Aktien|customers|employees|units|Kunden|Mitarbeiter)", re.I)
ALS_POSTEN = re.compile(r"\b(?:a|an|ein|eine|einen|einer)\s+(?:[\w-]+\s+)?$", re.I)
NEBENSATZ = re.compile(r",\s*defined\s+as[^,]{0,220},|,\s*(?:which|that)\s+(?:included|includes|reflect\w*)[^,]{0,160},|\((?=[^()]{0,160}(?:constant currency|\bcc\b|währungsbereinigt|including|excluding|inkl\.|exkl\.))[^()]{0,160}\)", re.I)


def _abschnitt(satz: str, ende_stichwort: int, start: int) -> str:
    """Vom Stichwort bis zum nächsten anderen Kennzahl-Stichwort (oder Satzende)."""
    naechstes = ALLE_STICHWOERTER.search(satz, ende_stichwort)
    while naechstes and naechstes.end() - naechstes.start() < 3:
        naechstes = ALLE_STICHWOERTER.search(satz, naechstes.end())
    return satz[start: naechstes.start() if naechstes else len(satz)]


def _wort_davor(satz: str, start: int) -> str:
    davor = satz[:start].rstrip()
    m = re.search(r"([\wÄÖÜäöüß\-'’.]+)$", davor)
    return m.group(1).strip(".").replace("’", "'") if m else ""


def _passend(w: dict, art: str) -> bool:
    if w["jahr"]:
        return False
    if art == "prozent":
        return w["prozent"] and 0 < w["zahl"] < 100
    if w["prozent"] or w["punkte"]:
        return False
    if art == "eps":
        return w["waehrung"] is not None and not w["hat_faktor"] and abs(w["zahl"]) < 2000
    return (w["waehrung"] is not None or w["hat_faktor"]) and w["zahl"] * w["faktor"] >= 1e4


def _beschriften(satz: str, m: re.Match, abschnitt_start: int, abschnitt: str, werte: list[dict]) -> list[str | None]:
    """Je Wert: 'adj' (bereinigt), 'gaap' oder None (unklar)."""
    labels = []
    stichwort = m.group(0)
    davor_satz = satz[max(0, m.start() - 60): m.start()]
    if re.search(rf"\bGAAP\s+and\s+(?:{BEREINIGT})\b", davor_satz + " " + stichwort, re.I) and "respectively" in abschnitt.lower():
        return ["gaap", "adj"] + [None] * (len(werte) - 2)
    if re.search(rf"\bGAAP\s+and\s+(?:{BEREINIGT})\b", davor_satz[-25:] + " " + stichwort, re.I):
        return ["beide"] * len(werte)
    grund = "adj" if re.search(rf"(?:{BEREINIGT})", stichwort + " " + satz[max(0, m.start() - 12): m.start()], re.I) else None
    if grund is None and re.search(r"(on\s+a\s+non-?GAAP\s+basis|on\s+an\s+adjusted\s+basis|auf\s+bereinigter\s+Basis)", satz[:m.start()], re.I) and not re.search(r"on\s+a\s+GAAP\s+basis", satz[:m.start()], re.I):
        grund = "adj"
    if grund is None and re.search(r"\bGAAP\b|\bIFRS\b", stichwort + satz[m.end(): m.end() + 10], re.I):
        grund = "gaap"
    vorher = 0
    for i, w in enumerate(werte):
        davor = abschnitt[vorher: w["start"]][-30:]
        naechster = werte[i + 1]["start"] if i + 1 < len(werte) else len(abschnitt)
        danach = abschnitt[w["ende"]: min(naechster, w["ende"] + 70)]
        if re.search(rf"(?:{BEREINIGT})\W*$", davor, re.I) or re.search(r"^\W*(?:\w+\W+){0,8}?on\s+(?:a|an)\s+(?:non-?GAAP|adjusted)\s+basis", danach, re.I):
            labels.append("adj")
        elif re.search(r"\b(?:GAAP|IFRS)\W*$", davor) or re.search(r"^\W*(?:\w+\W+){0,8}?on\s+a\s+GAAP\s+basis", danach, re.I):
            labels.append("gaap")
        else:
            labels.append(grund)
        vorher = w["ende"]
    return labels


def _aenderungen(abschnitt: str, ab: int) -> dict:
    """Genannte Veränderung zum Vorjahr (vj) und zum Vorquartal (vq) in Prozent."""
    aus = {}
    for m in AENDERUNG.finditer(abschnitt):
        if m.start() < ab - 120:
            continue
        z = m.group("z") or m.group("z2") or m.group("z3")
        try:
            p = float(z.replace(",", ".")) if re.fullmatch(r"\d+(?:[.,]\d{1,2})?", z) else zahl(z, False)
        except ValueError:
            continue
        richtung = m.group("r") or m.group("r3") or ""
        vz = m.group("v") or m.group("v2") or ""
        if (vz and vz != "+") or (not vz and RUNTER.search(richtung)):
            p = -p
        danach = abschnitt[m.end(): m.end() + 45]
        davor = abschnitt[max(0, m.start() - 25): m.start()]
        if re.match(r"\s*(?:\(|in\s+)?(?:constant\s+currency|cc\b|währungsbereinigt)", danach, re.I):
            continue
        art = "vq" if VQ.search(danach) or VQ.search(davor) else "vj"
        aus.setdefault(art, p)
    return aus


def _wahl(key: str, werte: list[dict], labels: list) -> int | None:
    variante = KENNZAHLEN[key][3]
    art = KENNZAHLEN[key][1]
    passend = [i for i, w in enumerate(werte) if _passend(w, art)]
    if variante == "adj":
        return next((i for i in passend if labels[i] in ("adj", "beide")), None)
    if variante == "gaap":
        return next((i for i in passend if labels[i] != "adj"), None)
    if variante == "gaap_lieber":
        return next((i for i in passend if labels[i] != "adj"), passend[0] if passend else None)
    return passend[0] if passend else None


def _kandidaten(sz: list[dict], key: str, deutsch: bool, firma: set[str]) -> list[dict]:
    name, art, muster, variante = KENNZAHLEN[key]
    aus = []
    for s in sz:
        satz = s["text"]
        if s["tabelle"] or s.get("im_ausblick") or s.get("kopf"):
            continue
        ohne = NICHT_AUSBLICK.sub(" ", satz)
        if AUSBLICK.search(ohne) and not re.search(r"(reported|posted|delivered|was|were|of\s+\$)", satz[:200], re.I):
            continue
        if AUSBLICK.search(ohne) and re.search(r"(expects?|expected\s+to\s+be|outlook|guidance|Ausblick|Prognose|erwartet\s+.{0,60}(?:Umsatz|Ergebnis|EPS))", ohne, re.I):
            continue
        jahr_satz = JAHR.search(satz) and not QUARTALSWORT.search(satz)
        if jahr_satz or re.search(r"geographic|by\s+region|by\s+segment|for\s+the\s+Americas", satz, re.I):
            continue
        sauber = NEBENSATZ.sub(lambda x: " " * len(x.group(0)), satz)
        for m in STICHWORT[key].finditer(sauber):
            abschnitt = _abschnitt(sauber, m.end(), m.start())
            alle = werte_in(abschnitt, deutsch)
            # Beträge, die eine Veränderung sind ("up $1.1 billion") oder Stückzahlen ("67 million shares"), zählen nicht
            alle = [w for w in alle if not ALS_AENDERUNG.search(abschnitt[:w["start"]][-20:]) and not NICHT_GELD_DANACH.match(abschnitt[w["ende"]:])
                    and not ALS_POSTEN.search(abschnitt[:w["start"]][-25:])]
            if not alle:
                continue
            labels = _beschriften(sauber, m, m.start(), abschnitt, alle)
            i = _wahl(key, alle, labels)
            if i is None:
                continue
            w = alle[i]
            wert = w["zahl"] * w["faktor"]
            if key in IMMER_POSITIV:
                wert = abs(wert)
            elif w["negativ"] or (art != "prozent" and VERLUST.search(m.group(0))):
                wert = -abs(wert)
            punkte = 10.0
            davor = _wort_davor(sauber, m.start())
            if davor and m.start() > 0 and not ERLAUBT_DAVOR.match(davor) and davor.lower() not in firma:
                punkte -= 6 if davor[:1].isupper() else 3
            if SEGMENT.search(sauber[max(0, m.start() - 45): m.start()]) or re.search(r"geographic|by\s+region|for\s+the\s+Americas", satz, re.I):
                punkte -= 5
            if JAHR.search(satz):
                punkte -= 2
            if re.search(r"(record|total|quarterly|consolidated|Rekord|Konzern|Gesamt|reported)", sauber[max(0, m.start() - 30): m.start() + 5], re.I):
                punkte += 2
            punkte -= 3 * s["pos"]  # weiter vorne (Highlights) ist besser
            vj_wert = vq_wert = None
            if art != "prozent":
                for w2 in alle[i + 1:]:
                    if not _passend(w2, art):
                        continue
                    zwischen = abschnitt[w["ende"]: w2["start"]]
                    nach = abschnitt[w2["ende"]: w2["ende"] + 45]
                    if VERGLEICH.search(zwischen) or VERGLEICH.search(nach):
                        v = w2["zahl"] * w2["faktor"] * (-1 if w2["negativ"] else 1)
                        nach_vq = re.match(r"[^$€\d]{0,40}?(prior\s+quarter|previous\s+quarter|sequential|Vorquartal)", nach, re.I)
                        nach_vj = re.match(r"[^$€\d]{0,40}?(same\s+(?:period|quarter)|last\s+year|prior[- ]year|year[- ]ago|a\s+year\s+(?:ago|earlier)|Vorjahr)", nach, re.I)
                        if nach_vq or (not nach_vj and VQ.search(zwischen[-40:])):
                            vq_wert = vq_wert if vq_wert is not None else v
                        elif vj_wert is None:
                            vj_wert = v
            aend = _aenderungen(abschnitt, w["start"]) if art != "prozent" else {}
            aus.append({
                "wert": wert, "waehrung": w["waehrung"], "punkte": punkte,
                "vj_prozent": aend.get("vj"), "vq_prozent": aend.get("vq"), "vj_wert": vj_wert, "vq_wert": vq_wert,
                "beleg": _kurz(satz), "quelle": "Text",
            })
            break
    return aus


def _kurz(satz: str) -> str:
    return satz if len(satz) <= 320 else satz[:320] + " …"


# ---- Tabellen ----

SKALA = re.compile(r"in\s+(millions|thousands|billions)|\(\s*\$\s?(M|B|K|mm)\s*\)|\$\s?in\s+(millions|thousands|billions)|in\s+(Mio|Tsd|Mrd)\.?|(?:€|EUR|\$|USD)\s+in\s+(Mio|Millionen|Tsd|Mrd|Milliarden)", re.I)


def _skala(t: str) -> float | None:
    t = t.lower()
    if re.search(r"billion|mrd|milliarden|\(\s*\$\s?b\s*\)", t):
        return 1e9
    if re.search(r"thousand|tsd|\(\s*\$\s?k\s*\)", t):
        return 1e3
    return 1e6


MONATE = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MONATE.update({"mär": 3, "mai": 5, "okt": 10, "dez": 12})


def _zeitpunkt(zelle: str) -> tuple[int, int] | None:
    """(Jahr, Monat) aus einer Kopfzelle wie "September 30, 2026", "Q3 FY27", "Q4 2025" oder "2026"."""
    j = re.search(r"(?<![\d$.,])(20\d\d)(?![\d.,])", zelle)
    jahr = int(j.group(1)) if j else None
    if jahr is None:
        j = re.search(r"\b(?:FY|GJ)\s?'?(\d\d)\b|'(\d\d)\b", zelle)
        jahr = 2000 + int(j.group(1) or j.group(2)) if j else None
    if jahr is None:
        return None
    q = re.search(r"\bQ([1-4])\b", zelle)
    if q:
        return (jahr, int(q.group(1)) * 3)
    mo = re.search(r"\b(jan|feb|mar|mär|apr|may|mai|jun|jul|aug|sep|oct|okt|nov|dec|dez)", zelle, re.I)
    return (jahr, MONATE[mo.group(1).lower()] if mo else 0)


def _tabelle(text: str, key: str, deutsch: bool, ausblick_zeilen: set[int]) -> dict | None:
    name, art, muster, variante = KENNZAHLEN[key]
    if key == "dividende":
        return None
    rx = re.compile(rf"^\s*(?:(?:{BEREINIGT})\s+)?(?:GAAP\s+)?(?:{muster})\b", re.I)
    skala = None
    abschnitt_bereinigt = False  # Zwischenüberschrift "Non-GAAP" / "GAAP" über einem Tabellenteil
    spalte, spalte_alter = 0, 99  # welche Zahlenspalte das aktuelle Quartal ist (aus der Kopfzeile mit Jahren)
    for nr, zeile in enumerate(text.split("\n")):
        spalte_alter += 1
        sk = SKALA.search(zeile)
        if sk and not ist_tabellenzeile(zeile.split(sk.group(0))[0] + " "):
            skala = _skala(sk.group(0))
        kurz = zeile.strip("\t ")
        if len(kurz) < 40 and not re.search(r"\d", kurz.replace("(1)", "").replace("(2)", "")):
            if re.match(rf"(?:{BEREINIGT})\b", kurz, re.I):
                abschnitt_bereinigt = True
            elif re.match(r"(?:GAAP|IFRS)\b", kurz):
                abschnitt_bereinigt = False
        if ist_tabellenzeile(zeile) and not re.search(r"\$\s?\d|\d,\d{3}(?!\s*\d)", zeile):
            zeitpunkte = [z for z in (_zeitpunkt(c) for c in zeile.strip("\t ").split("\t")) if z]
            if len(zeitpunkte) >= 2:
                # Spalte mit dem neuesten Zeitpunkt (bei Gleichstand die erste): "2025 2026" → 1, "Q3 FY27 Q2 FY27" → 0
                spalte, spalte_alter = zeitpunkte.index(max(zeitpunkte)), 0
        if not ist_tabellenzeile(zeile) or nr in ausblick_zeilen:
            continue
        zelle = zeile.strip("\t ").split("\t")
        m = rx.match(zelle[0])
        if not m:
            continue
        bereinigt = bool(re.match(rf"\s*(?:{BEREINIGT})", zelle[0], re.I)) or (abschnitt_bereinigt and not re.match(r"\s*GAAP\b", zelle[0]))
        if (variante == "adj") != bereinigt and variante in ("adj", "gaap"):
            continue
        if variante == "gaap" and re.search(r"(per\s+share|je\s+Aktie|\bEPS\b)", zelle[0], re.I) and key in ("netto", "operativ"):
            continue
        if re.search(r"\b(TTM|trailing|twelve\s+months|YTD|year[- ]to[- ]date)\b", zelle[0], re.I):
            continue
        zeilen_skala = _skala(SKALA.search(zelle[0]).group(0)) if SKALA.search(zelle[0]) else skala
        rest = "\t".join(zelle[1:])
        if re.search(r"\d\s*(?:to|-|–|bis)\s*\$?\s?\d", rest.split("\t")[0] + " " + (rest.split("\t")[1] if "\t" in rest else "")):
            continue  # Spanne = Prognose
        zellen = [z.strip() for z in rest.split("\t") if z.strip() not in ("", "$", "€", "%")]
        zahlen = []
        for z in zellen:
            mm = re.fullmatch(rf"\(?\s?[−–-]?\s?(?:\$|€)?\s?({ZAHL})\s?\)?\s?(%|pts)?", z)
            if mm:
                zahlen.append((z, mm.group(1), mm.group(2)))
        if len(zahlen) < 2:
            continue
        nimm = spalte if spalte_alter < 40 and spalte < len(zahlen) else 0
        roh, ziffern, einheit = zahlen[nimm]
        if (art == "prozent") != (einheit == "%"):
            continue
        if re.fullmatch(r"(19|20)\d\d", ziffern):
            continue
        try:
            z = zahl(ziffern, deutsch)
        except ValueError:
            continue
        neg = roh.startswith("(") or roh.lstrip("$€ ")[:1] in "−–-"
        if art == "geld":
            if not zeilen_skala:
                continue
            z *= zeilen_skala
        if art == "eps" and abs(z) > 2000:
            continue
        wert = abs(z) if key in IMMER_POSITIV else (-z if neg else z)
        return {"wert": wert, "waehrung": None, "vj_prozent": None, "vq_prozent": None,
                "vj_wert": None, "vq_wert": None, "beleg": re.sub(r"\t+", "  ", zeile.strip())[:240], "quelle": "Tabelle"}
    return None


# ---- Quartal, Ticker, Ausblick, Ton ----

ORDNUNG = {"first": 1, "second": 2, "third": 3, "fourth": 4, "ersten": 1, "zweiten": 2, "dritten": 3, "vierten": 4}


def quartal(text: str) -> dict | None:
    """Welches Quartal die Mitteilung meldet, z. B. {"q": 3, "jahr": 2026, "fiskal": True, "text": "Q3 GJ 2026"}.
    Es zählt die erste Nennung (Überschrift), nicht die häufigste: weiter unten stehen oft Vergleichsquartale."""
    kopf = text[:5000]
    funde = []
    for m in re.finditer(r"\b(first|second|third|fourth)[- ]quarter\b", kopf, re.I):
        rest = kopf[m.end(): m.end() + 40]
        rest = kopf[m.end(): m.end() + 60]
        j = re.match(r"[\s,]*(?:and\s+(?:the\s+)?full\s+(?:fiscal\s+)?year\s+)?(?:results\s+)?(?:of\s+|for\s+)?(?:the\s+)?(fiscal\s+(?:year\s+)?)?(?:FY\s?)?'?(20\d\d|\d\d)\b", rest, re.I)
        if not j:
            jd = re.match(r"\s*(?:of\s+\w+\s+)?(?:ended|ending)\s+\w+\s+\d{1,2},\s+(20\d\d)", kopf[m.end(): m.end() + 60], re.I)
            if jd:
                funde.append((m.start(), ORDNUNG[m.group(1).lower()], int(jd.group(1)), False))
        if j:
            jahr = int(j.group(2)) if len(j.group(2)) == 4 else 2000 + int(j.group(2))
            funde.append((m.start(), ORDNUNG[m.group(1).lower()], jahr, bool(j.group(1)) or "fy" in rest[:12].lower()))
    for m in re.finditer(r"\bQ([1-4])\s*(FY|GJ|fiscal)?\s*'?(20\d\d|\d\d)\b", kopf, re.I):
        j = int(m.group(3)) if len(m.group(3)) == 4 else 2000 + int(m.group(3))
        funde.append((m.start(), int(m.group(1)), j, bool(m.group(2))))
    for m in re.finditer(r"\b(?:FY|GJ)\s?'?(20\d\d|\d\d)\s*Q([1-4])\b", kopf, re.I):
        j = int(m.group(1)) if len(m.group(1)) == 4 else 2000 + int(m.group(1))
        funde.append((m.start(), int(m.group(2)), j, True))
    for m in re.finditer(r"\b(ersten|zweiten|dritten|vierten|([1-4])\.)\s*Quartal(?:s)?\s+(?:des\s+Geschäftsjahres\s+)?(20\d\d(?:/\d\d)?)", kopf, re.I):
        q = ORDNUNG.get(m.group(1).lower()) or int(m.group(2))
        funde.append((m.start(), q, int(m.group(3)[:4]), "geschäftsjahr" in m.group(0).lower()))
    for m in re.finditer(r"\bQ([1-4])\s*/\s*(20\d\d)", kopf):
        funde.append((m.start(), int(m.group(1)), int(m.group(2)), False))
    if not funde:
        return None
    _, q, j, f = min(funde)
    return {"q": q, "jahr": j, "fiskal": f, "text": f"Q{q} {'GJ ' if f else ''}{j}"}


def ticker(text: str) -> str | None:
    m = re.search(r"\((?:NASDAQ|Nasdaq|NYSE|NYSE American|Nasdaq GS|NASDAQ GS|TSX|LSE|Xetra|XETRA|FWB)\s*:\s*([A-Z][A-Z0-9.\-]{0,9})\)", text[:8000])
    if m:
        return m.group(1)
    m = re.search(r"\b(?:NASDAQ|Nasdaq|NYSE)\s*:\s*([A-Z][A-Z.\-]{0,6})\b", text[:8000])
    return m.group(1) if m else None


def _periode(t: str) -> str | None:
    if re.search(r"full[- ]year|fiscal\s+year|for\s+(?:fiscal\s+)?(?:year\s+)?20\d\d\b|^\s*FY\s?(?:20)?\d\d\b|Gesamtjahr|Geschäftsjahr|für\s+20\d\d", t, re.I):
        return "Jahr"
    if re.search(r"(fourth|third|second|first|next|current)\s+(?:fiscal\s+)?quarter|\bQ[1-4]\b|(?:vierten|dritten|zweiten|ersten|laufenden)\s+Quartal|quarter\s+ending", t, re.I):
        return "Quartal"
    if JAHR.search(t) or re.search(r"\b20\d\d\b|Jahr", t):
        return "Jahr"
    return None


AUSBLICK_KENNZAHLEN = {
    "umsatz": r"(?:total\s+|net\s+)?revenues?|net\s+sales|sales|[Uu]msatz(?:erlöse)?|Erlöse",
    "eps_adj": EPS_MUSTER,
    "eps": EPS_MUSTER,
    "brutto_marge": r"gross\s+margins?|Bruttomarge",
    "op_marge": r"operating\s+margins?|operative\s+Marge|EBIT-Marge",
    "operativ": r"operating\s+(?:income|profit)|operative[sn]?\s+Ergebnis|EBIT\b",
    "fcf": r"free\s+cash\s*flow|Free\s+Cash\s*-?[Ff]low|freier\s+Cashflow",
    "capex": r"capital\s+expenditures?|capex|Investitionen",
}
AUSBLICK_VARIANTE = {"eps_adj": "adj", "eps": "gaap", "umsatz": "gaap", "operativ": "gaap_lieber", "brutto_marge": "gaap_lieber", "op_marge": "gaap_lieber"}


def ausblick(sz: list[dict], deutsch: bool, firma: set[str]) -> list[dict]:
    """Zahlen aus dem Ausblick: Spanne (von, bis) oder Punktwert mit Plus/Minus. Je Kennzahl der beste Satz."""
    kandidaten = {}
    for k, s in enumerate(sz):
        satz = s["text"]
        im_abschnitt = s.get("im_ausblick")
        if not im_abschnitt and not AUSBLICK.search(NICHT_AUSBLICK.sub(" ", satz)):
            continue
        if BASIS.search(satz) or s.get("kopf") or re.search(r"(?:outlook|guidance)\s+(?:for\s+[\w\s-]{0,40}?)?(?:excludes|includes|does\s+not)", satz, re.I):
            continue
        periode = _periode(satz) or s.get("ausblick_periode")
        if periode is None:
            for vor in sz[max(0, k - 3): k][::-1]:
                periode = _periode(vor["text"]) if AUSBLICK.search(vor["text"]) or vor.get("kopf") else None
                if periode:
                    break
        for key, muster in AUSBLICK_KENNZAHLEN.items():
            m = re.search(rf"(?<![\w\-]){muster}(?![A-Za-zÄÖÜäöüß\-])", satz, re.I)
            if not m:
                continue
            davor = _wort_davor(satz, m.start())
            if davor and davor[:1].isupper() and not ERLAUBT_DAVOR.match(davor) and m.start() > 0 and davor.lower() not in firma:
                continue  # Segment
            if SEGMENT.search(satz[max(0, m.start() - 40): m.start()]):
                continue
            art = "prozent" if key.endswith("marge") else "eps" if key.startswith("eps") else "geld"
            abschnitt = _abschnitt(satz, m.end(), m.start())
            alle = [w for w in werte_in(abschnitt, deutsch) if not w["jahr"]]
            for w in alle:
                # Tabellenzeile ohne Einheit ("$<Tab>11,100"): Einheit aus "(In millions …)" darüber
                if s["tabelle"] and art == "geld" and not w["hat_faktor"] and s.get("skala") and w["waehrung"]:
                    w["faktor"], w["hat_faktor"] = s["skala"], True
                # "$10.65 - $11.40": der Bindestrich ist hier kein Minus
                if w["negativ"] and abschnitt[w["start"]] in "-–":
                    w["negativ"], w["start"] = False, w["start"] + 1
            labels = _beschriften(satz, m, m.start(), abschnitt, alle) if alle else []
            variante = AUSBLICK_VARIANTE.get(key, "egal")
            ergebnis = None
            reihenfolge = list(enumerate(alle))
            if s["tabelle"] and len(re.findall(r"\d\s*(?:to|-|–)\s*\$?\s?\d", abschnitt)) >= 3:
                # Tabelle mit Prognose-Verlauf ("Original", "As of …"): die rechte Spalte ist die aktuelle
                letzte = [i for i, w in enumerate(alle) if i + 1 < len(alle) and re.fullmatch(r"\s*(?:to|-|–)\s*", abschnitt[w["ende"]: alle[i + 1]["start"]])]
                if letzte:
                    reihenfolge = reihenfolge[letzte[-1]:]
            for i, w in reihenfolge:
                if variante == "adj" and labels[i] not in ("adj", "beide"):
                    continue
                if variante == "gaap" and labels[i] == "adj":
                    continue
                nxt = alle[i + 1] if i + 1 < len(alle) else None
                zwischen = abschnitt[w["ende"]: nxt["start"]] if nxt else ""
                if nxt and re.fullmatch(r"\s*(?:to|and|-|–|bis|und)\s*", zwischen, re.I):
                    # Spanne: Einheit und Währung vom zweiten Wert übernehmen, wenn sie beim ersten fehlen
                    a = dict(w)
                    if not a["hat_faktor"]:
                        a["faktor"], a["hat_faktor"] = nxt["faktor"], nxt["hat_faktor"]
                    a["waehrung"] = a["waehrung"] or nxt["waehrung"]
                    a["prozent"] = a["prozent"] or nxt["prozent"]
                    b = {**nxt, "waehrung": nxt["waehrung"] or a["waehrung"]}
                    if _passend(a, art) and _passend(b, art):
                        von, bis = a["zahl"] * a["faktor"], b["zahl"] * b["faktor"]
                        ergebnis = {"von": min(von, bis), "bis": max(von, bis), "waehrung": a["waehrung"], "spanne": True}
                        break
                if _passend(w, art):
                    mitte = w["zahl"] * w["faktor"]
                    pm = re.match(rf"\s*,?\s*(?:plus\s+or\s+minus|\+/-|±|plus/minus)\s*(\$|€)?\s*({ZAHL})\s*(%|percent|basis\s+points?|bps|Prozent|Basispunkte|billion|million|Mrd\.?|Mio\.?)?", abschnitt[w["ende"]:], re.I) if not (w["prozent"] and art != "prozent") else None
                    if pm:
                        d = zahl(pm.group(2), deutsch)
                        einheit = pm.group(3) or ""
                        if not einheit or einheit[0].lower() in "bmM" and not re.match(r"basis", einheit, re.I):
                            # Plus/minus als Betrag ("± $1.5 billion", "+/- $500" in Millionen)
                            d *= FAKTOR.get(einheit.lower().rstrip("."), w["faktor"] if not einheit else 1)
                            ergebnis = {"von": mitte - d, "bis": mitte + d}
                        elif re.search(r"basis|bps", einheit, re.I):
                            d = d / 100
                            ergebnis = {"von": mitte - d, "bis": mitte + d}
                        elif art == "prozent":
                            ergebnis = {"von": mitte - d, "bis": mitte + d}
                        else:
                            ergebnis = {"von": mitte * (1 - d / 100), "bis": mitte * (1 + d / 100)}
                        ergebnis.update(waehrung=w["waehrung"], spanne=True)
                    else:
                        ergebnis = {"von": mitte, "bis": mitte, "waehrung": w["waehrung"], "spanne": False}
                    break
            if not ergebnis:
                continue
            punkte = (3 if ergebnis["spanne"] else 0) + (2 if m.start() < 40 else 0) + (2 if im_abschnitt else 0) \
                - (5 if re.search(r"[“”\"]", satz) else 0) - (2 if s["tabelle"] else 0)
            if (key, periode) not in kandidaten or punkte > kandidaten[(key, periode)]["punkte"]:
                kandidaten[(key, periode)] = {"kennzahl": key, "name": KENNZAHLEN[key][0], "periode": periode, **ergebnis,
                                   "mitte": (ergebnis["von"] + ergebnis["bis"]) / 2, "beleg": _kurz(re.sub(r"\t+", "  ", satz)), "punkte": punkte}
    # Dieselbe Spanne einmal mit und einmal ohne bekannte Periode (Text und Tabelle): nur die mit Periode behalten
    aus = [a for a in kandidaten.values()
           if a["periode"] or not any(b["periode"] and b["kennzahl"] == a["kennzahl"] and abs(b["mitte"] - a["mitte"]) < 1e-9 + abs(a["mitte"]) * 0.001 for b in kandidaten.values())]
    for a in aus:
        a.pop("punkte")
    return aus


_W = r"(?:[\w\-']+\s+){0,7}?"
AENDERUNG_PROGNOSE = [
    ("angehoben", re.compile(rf"\b(rais\w*|increas\w*|lift\w*|boost\w*|upgrad\w*)\s+{_W}(outlook|guidance|forecast)|(outlook|guidance|forecast)\s+(?:is\s+|was\s+|has\s+been\s+)?(raised|increased|lifted)|\b(hebt|erhöht)\s+{_W}(Prognose|Ausblick|Jahresziel\w*)|(Prognose|Ausblick|Jahresziel\w*)\s+{_W}(angehoben|erhöht)", re.I)),
    ("gesenkt", re.compile(rf"\b(lower\w*|cut\w*|reduc\w*|trim\w*|downgrad\w*)\s+{_W}(outlook|guidance|forecast)|(outlook|guidance|forecast)\s+(?:is\s+|was\s+|has\s+been\s+)?(lowered|reduced|cut)|\b(senkt|reduziert|kappt)\s+{_W}(Prognose|Ausblick|Jahresziel\w*)|(Prognose|Ausblick|Jahresziel\w*)\s+{_W}(gesenkt|reduziert|gekappt)", re.I)),
    ("bestätigt", re.compile(rf"\b(reaffirm\w*|reiterat\w*|maintain\w*|confirm\w*)\s+{_W}(outlook|guidance|forecast)|\b(bestätigt|bekräftigt)\s+{_W}(Prognose|Ausblick|Jahresziel\w*)|(Prognose|Ausblick|Jahresziel\w*)\s+{_W}(bestätigt|bekräftigt)", re.I)),
]


def prognose_aenderung(sz: list[dict]) -> dict | None:
    """Ob die Firma ihre Prognose anhebt, senkt oder bestätigt (erste klare Aussage zählt)."""
    treffer = []
    for i, s in enumerate(sz):
        if re.search(r"forward-looking|safe\s+harbor|may\s+(?:raise|lower)|could|risk", s["text"], re.I):
            continue
        for art, rx in AENDERUNG_PROGNOSE:
            if rx.search(s["text"]):
                treffer.append((i, art, s["text"]))
                break
    if not treffer:
        return None
    i, art, text = treffer[0]
    return {"art": art, "beleg": _kurz(text)}


POSITIV = r"record|all-time\s+high|strong(?:er|est)?|robust|accelerat\w*|exceed\w*|outperform\w*|momentum|surpass\w*|exceptional|Rekord\w*|stark\w*|kräftig\w*|übertroffen|angehoben|Dynamik|beschleunig\w*|solide[sn]?"
NEGATIV = r"headwinds?|challeng\w*|weak\w*|softness|soft(?:er)?\s+demand|uncertaint\w*|declin\w*|impairment\w*|restructuring|layoffs?|cautious|slowdown|lowered|Gegenwind|schwach\w*|Rückgang|Unsicherheit\w*|Wertminderung\w*|Restrukturierung|Stellenabbau|gesenkt|Belastung\w*|verhalten\w*|Abschwächung"
RECHTLICHES = re.compile(r"(forward-looking|safe\s+harbor|risks?\s+and\s+uncertaint|could\s+cause|Risk\s+Factors|zukunftsgerichtete)", re.I)


def ton(sz: list[dict]) -> dict:
    """Signalwörter zählen, ohne den rechtlichen Hinweis am Ende (der klingt immer negativ). Grob, zeigt aber die Wortwahl."""
    pos, neg = [], []
    for s in sz:
        if s["tabelle"] or RECHTLICHES.search(s["text"]):
            continue
        pos += [m.group(0).lower() for m in re.finditer(rf"\b(?:{POSITIV})\b", s["text"], re.I)]
        neg += [m.group(0).lower() for m in re.finditer(rf"\b(?:{NEGATIV})\b", s["text"], re.I)]
    gesamt = len(pos) + len(neg)
    wert = (len(pos) - len(neg)) / gesamt if gesamt else 0
    return {"positiv": len(pos), "negativ": len(neg), "wert": round(wert, 2),
            "urteil": "eher positiv" if wert > 0.25 and gesamt >= 4 else "eher negativ" if wert < -0.15 and gesamt >= 4 else "gemischt",
            "top_positiv": Counter(pos).most_common(6), "top_negativ": Counter(neg).most_common(6)}


# ---- Gesamt ----

def vorbereiten(text: str) -> str:
    text = text.replace("\r", "").replace(" ", " ").replace(" ", " ").replace(" ", " ").replace("​", "")
    text = re.sub(r"[ ]*\t[ \t]*", "\t", text)
    return re.sub(r"[ ]+", " ", text)


def auswerten(text: str, firma: str | None = None) -> dict:
    """firma: Name laut Yahoo (z. B. "Alphabet Inc."), damit "Alphabet revenues" nicht als Segment gilt."""
    text = vorbereiten(text)
    deutsch = ist_deutsch(text)
    sz = saetze(text)
    namen = {w.lower() for w in re.findall(r"[\wÄÖÜäöüß]+", firma or "") if len(w) > 2}
    # Zeilennummern im Ausblick-Abschnitt (für Tabellen)
    ausblick_zeilen, rest = set(), 0
    for nr, zeile in enumerate(text.split("\n")):
        z = zeile.strip("\t ")
        if len(z) < 90 and re.search(r"\b(guidance|outlook|Ausblick|Prognose)\b", z, re.I) and not re.search(r"\d\s?%", z):
            rest = 15
        elif rest and ist_tabellenzeile(zeile):
            ausblick_zeilen.add(nr)
        rest = max(0, rest - 1)
    werte = {}
    for key in KENNZAHLEN:
        kandidaten = _kandidaten(sz, key, deutsch, namen)
        if kandidaten:
            bester = max(kandidaten, key=lambda k: k["punkte"])
            if bester["punkte"] > 0:
                werte[key] = bester
                continue
        t = _tabelle(text, key, deutsch, ausblick_zeilen)
        if t:
            werte[key] = t
    for w in werte.values():
        w["wert"] = round(w["wert"], 4)  # 8,47 × 1e9 ergibt sonst 8470000000.000001
        if w.get("vj_prozent") is None and w.get("vj_wert"):
            w["vj_prozent"] = (w["wert"] / w["vj_wert"] - 1) * 100 if w["vj_wert"] > 0 and w["wert"] > 0 else None
        if w.get("vq_prozent") is None and w.get("vq_wert"):
            w["vq_prozent"] = (w["wert"] / w["vq_wert"] - 1) * 100 if w["vq_wert"] > 0 and w["wert"] > 0 else None
        w.pop("punkte", None)
    waehrungen = Counter(w["waehrung"] for w in werte.values() if w.get("waehrung"))
    return {
        "sprache": "de" if deutsch else "en",
        "quartal": quartal(text),
        "ticker": ticker(text),
        "waehrung": waehrungen.most_common(1)[0][0] if waehrungen else None,
        "werte": werte,
        "ausblick": ausblick(sz, deutsch, namen),
        "prognose": prognose_aenderung(sz),
        "ton": ton(sz),
        "zeichen": len(text),
    }
