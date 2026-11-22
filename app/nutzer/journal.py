"""Trade-Journal (Punkt 12): jeder Kauf und Verkauf aus dem TR-Export mit eigener Begründung, dazu die Auswertung.

Verkäufe werden per FIFO den ältesten offenen Käufen zugeordnet (wie in portfolio.py und im Steuerrecht).
Je Verkauf: realisierter Gewinn in Euro und Prozent (nach Gebühren), Haltedauer (nach Stück gewichtet über
die verbrauchten Käufe) und "Geld liegen gelassen": wie sich der Kurs 1, 4 und 12 Wochen nach dem Verkauf
entwickelt hat, angewendet auf den Verkaufserlös (Kurse in Handelswährung, aus der Trade-Auswertung der
Chartanalyse). Positiv heißt: Hättest du gehalten, wäre es mehr gewesen.
Begründungen liegen pro Benutzer in journal.yaml (Schlüssel: Zeitpunkt, ISIN, Kauf/Verkauf).
"""
from collections import defaultdict
from statistics import median

import yaml

from ..benutzer import Benutzer
from .tr_parser import lade_trades

REST = 1e-6
WOCHEN = (1, 4, 12)
MIN_STICHPROBE = 20  # darunter: Hinweis auf kleine Stichprobe
MAX_GRUND = 500


class JournalFehler(Exception):
    pass


def kennung(t: dict) -> str:
    return f"{t['zeit']:%Y%m%d%H%M%S}-{t['isin']}-{'K' if t['typ'] == 'BUY' else 'V'}"


def _datei(b: Benutzer):
    return b.datei("journal.yaml")


def gruende(b: Benutzer) -> dict:
    d = _datei(b)
    if not d.exists():
        return {}
    try:
        return {str(k): str(v) for k, v in (yaml.safe_load(d.read_text(encoding="utf-8")) or {}).items()}
    except Exception:
        return {}


def grund_speichern(b: Benutzer, tid: str, text: str) -> dict:
    alle = gruende(b)
    if not any(kennung(t) == tid for t in lade_trades(b)):
        raise JournalFehler("Diesen Trade gibt es im Export nicht.")
    text = (text or "").strip()[:MAX_GRUND]
    if text:
        alle[tid] = text
    else:
        alle.pop(tid, None)
    d = _datei(b)
    d.parent.mkdir(parents=True, exist_ok=True)
    zw = d.with_suffix(".tmp")
    zw.write_text(yaml.safe_dump(alle, allow_unicode=True, sort_keys=True), encoding="utf-8")
    zw.replace(d)
    return {"ok": True, "grund": text}


def rundreisen(roh: list[dict]) -> tuple[list[dict], list[str]]:
    """Jeder Trade als Zeile; Verkäufe mit Einstand, Gewinn und Haltedauer (FIFO). Reine Funktion."""
    offen = defaultdict(list)  # ISIN -> [[Stück, Kosten je Stück, Zeit], ...]
    zeilen, warnungen = [], []
    for t in roh:
        stueck = abs(t["stueck"])
        z = {"id": kennung(t), "zeit": t["zeit"], "typ": "kauf" if t["typ"] == "BUY" else "verkauf", "name": t["name"],
             "isin": t["isin"], "art": t.get("art"), "stueck": stueck, "betrag": abs(t["betrag"]),
             "preis": abs(t["betrag"]) / stueck if stueck else None, "vorlaeufig": bool(t.get("vorlaeufig"))}
        if t["typ"] == "BUY":
            kosten = -t["betrag"] + t["gebuehr"]
            offen[t["isin"]].append([stueck, kosten / stueck if stueck else 0, t["zeit"]])
            zeilen.append(z)
            continue
        menge, einstand, tage, teile = stueck, 0.0, 0.0, []
        liste = offen[t["isin"]]
        while menge > REST and liste:
            teil = min(menge, liste[0][0])
            teile.append({"zeit": liste[0][2], "stueck": teil, "kosten": teil * liste[0][1]})
            einstand += teil * liste[0][1]
            tage += teil * (t["zeit"] - liste[0][2]).total_seconds() / 86400
            liste[0][0] -= teil
            menge -= teil
            if liste[0][0] <= REST:
                liste.pop(0)
        if menge > REST:
            warnungen.append(f"{t['name']}: Verkauf vom {t['zeit']:%d.%m.%Y} ohne passenden Kauf im Export, nicht ausgewertet.")
            z["ohne_kauf"] = True
            zeilen.append(z)
            continue
        erloes = t["betrag"] - t["gebuehr"]
        z.update(einstand=einstand, gewinn=erloes - einstand, gewinn_prozent=(erloes / einstand - 1) * 100 if einstand else None,
                 haltedauer=tage / stueck if stueck else None, teile=teile)
        zeilen.append(z)
    return zeilen, warnungen


def _schnitt(w):
    return sum(w) / len(w) if w else None


def kennzahlen(verkaeufe: list[dict]) -> dict:
    """Trefferquote, Gewinn gegen Verlust, Haltedauer, liegen gelassen. Reine Funktion."""
    v = [z for z in verkaeufe if z.get("gewinn") is not None]
    gew = [z for z in v if z["gewinn"] > 0]
    verl = [z for z in v if z["gewinn"] <= 0]
    aus = {
        "anzahl": len(v), "gewinner": len(gew), "verlierer": len(verl),
        "trefferquote": len(gew) / len(v) * 100 if v else None,
        "summe": sum(z["gewinn"] for z in v),
        "schnitt_gewinn": _schnitt([z["gewinn"] for z in gew]), "schnitt_verlust": _schnitt([z["gewinn"] for z in verl]),
        "schnitt_gewinn_prozent": _schnitt([z["gewinn_prozent"] for z in gew if z["gewinn_prozent"] is not None]),
        "schnitt_verlust_prozent": _schnitt([z["gewinn_prozent"] for z in verl if z["gewinn_prozent"] is not None]),
        "erwartung": _schnitt([z["gewinn"] for z in v]),
        "haltedauer_median": median([z["haltedauer"] for z in v]) if v else None,
        "haltedauer_gewinner": _schnitt([z["haltedauer"] for z in gew]),
        "haltedauer_verlierer": _schnitt([z["haltedauer"] for z in verl]),
        "stichprobe_klein": len(v) < MIN_STICHPROBE, "min_stichprobe": MIN_STICHPROBE,
    }
    if aus["schnitt_gewinn"] and aus["schnitt_verlust"]:
        aus["verhaeltnis"] = aus["schnitt_gewinn"] / abs(aus["schnitt_verlust"]) if aus["schnitt_verlust"] else None
    aus["liegen_gelassen"] = {}
    for w in WOCHEN:
        mit = [z for z in v if (z.get("danach") or {}).get(str(w)) is not None]
        if not mit:
            continue
        euro = [z["betrag"] * z["danach"][str(w)] / 100 for z in mit]
        aus["liegen_gelassen"][str(w)] = {"anzahl": len(mit), "summe": sum(euro), "schnitt_prozent": _schnitt([z["danach"][str(w)] for z in mit]),
                                          "danach_hoeher": sum(e > 0 for e in euro) / len(mit) * 100}
    return aus


def auswertung(b: Benutzer) -> dict:
    roh = lade_trades(b)
    if not roh:
        return {"fehler": "Im Export sind keine Käufe oder Verkäufe."}
    zeilen, warnungen = rundreisen(roh)
    # Kurs danach aus der Trade-Auswertung der Chartanalyse (gleicher Export, gleiche Zeitstempel)
    try:
        from .chartanalyse import trades as ca_trades
        danach = {(t["zeit"], t["name"], t["typ"]): t["danach"] for t in ca_trades(b)["trades"]}
    except Exception:
        danach = {}
    begruendet = gruende(b)
    for z in zeilen:
        z["danach"] = danach.get((z["zeit"].strftime("%Y-%m-%d %H:%M"), z["name"], z["typ"]), {})
        z["grund"] = begruendet.get(z["id"], "")
    verkaeufe = [z for z in zeilen if z["typ"] == "verkauf"]
    jahre = sorted({z["zeit"].year for z in verkaeufe}, reverse=True)
    for z in zeilen:
        z["zeit"] = z["zeit"].isoformat(timespec="minutes")
        z.pop("teile", None)
    return {
        "trades": list(reversed(zeilen)),
        "gesamt": kennzahlen(verkaeufe),
        "jahre": [{"jahr": j, **kennzahlen([z for z in verkaeufe if z["zeit"].startswith(str(j))])} for j in jahre],
        "begruendet": sum(1 for z in zeilen if z["grund"]), "anzahl_trades": len(zeilen),
        "warnungen": warnungen, "wochen": list(WOCHEN),
    }
