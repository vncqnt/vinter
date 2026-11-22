"""Exit-Analyse (Roadmap Q1): Deine echten Verkäufe gegen einfache Ausstiegsregeln.

Jeder Verkauf wird per FIFO in die Kauf-Teile zerlegt (journal.rundreisen). Für jeden Teil startet jede Regel
am Kauftag (Schlusskurs) und mit demselben eingesetzten Betrag:
- ATR-Trailing-Stop: raus, sobald der Schluss mehr als k × ATR(14) unter dem höchsten Schluss seit Kauf liegt.
- Teilverkauf am Kursziel: die Hälfte beim ersten Schluss mindestens x % über dem Kauf, der Rest an deinem
  echten Verkaufstag (wird das Ziel vorher nicht erreicht: alles an deinem Verkaufstag).
- Feste Haltedauer: raus am ersten Handelstag nach n Kalendertagen.
- Kaufen und Halten: bis heute.
Deine eigenen Exits werden zum Vergleich auf derselben Grundlage gerechnet (Schluss am Kauftag bis Schluss am
Verkaufstag, Kurse in Handelswährung von Yahoo). So stecken Wechselkurs, Gebühren und Ausführungskurs bei allen
gleich drin; der echte Gewinn steht im Journal. Hat eine Regel bis heute nicht ausgelöst, zählt der heutige
Kurs (als "noch offen" markiert). Tagesgeschäfte (Kauf und Verkauf am selben Tag) lassen sich mit Tagesschlusskursen
nicht nachbilden und werden getrennt aufgeführt. Rückblick, kein Rat: Mit 10 Varianten findet sich fast immer eine, die
zufällig besser war.
"""
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from ..benutzer import Benutzer
from ..markt.chartanalyse import tageskurse
from ..markt.kurse import ticker_fuer
from ..markt.schwankung import atr
from .journal import MIN_STICHPROBE, rundreisen
from .tr_parser import lade_trades

REGELN = [
    {"id": "atr2", "art": "atr", "wert": 2, "name": "Trailing-Stop 2 × ATR"},
    {"id": "atr3", "art": "atr", "wert": 3, "name": "Trailing-Stop 3 × ATR"},
    {"id": "atr4", "art": "atr", "wert": 4, "name": "Trailing-Stop 4 × ATR"},
    {"id": "ziel15", "art": "ziel", "wert": 15, "name": "Hälfte bei +15 %, Rest wie du"},
    {"id": "ziel25", "art": "ziel", "wert": 25, "name": "Hälfte bei +25 %, Rest wie du"},
    {"id": "ziel50", "art": "ziel", "wert": 50, "name": "Hälfte bei +50 %, Rest wie du"},
    {"id": "halten30", "art": "halten", "wert": 30, "name": "Nach 30 Tagen verkaufen"},
    {"id": "halten90", "art": "halten", "wert": 90, "name": "Nach 90 Tagen verkaufen"},
    {"id": "halten180", "art": "halten", "wert": 180, "name": "Nach 180 Tagen verkaufen"},
    {"id": "halten_heute", "art": "heute", "wert": None, "name": "Kaufen und halten bis heute"},
]


def simuliere(df: pd.DataFrame, kauf: pd.Timestamp, verkauf: pd.Timestamp, regel: dict) -> dict | None:
    """Rendite eines Kauf-Teils nach einer Regel. Reine Funktion: df mit Close (und High/Low für die ATR)."""
    ab = df[df.index >= kauf.normalize()]
    if ab.empty:
        return None
    einstieg = float(ab["Close"].iloc[0])
    bis_verkauf = ab[ab.index <= verkauf.normalize()]
    eigener_ausstieg = float(bis_verkauf["Close"].iloc[-1]) if not bis_verkauf.empty else einstieg
    letzter = float(ab["Close"].iloc[-1])
    art, wert = regel["art"], regel["wert"]
    if art == "eigen":
        return {"rendite": eigener_ausstieg / einstieg - 1, "offen": False, "tag": bis_verkauf.index[-1] if not bis_verkauf.empty else ab.index[0]}
    if art == "heute":
        return {"rendite": letzter / einstieg - 1, "offen": True, "tag": ab.index[-1]}
    if art == "halten":
        spaeter = ab[ab.index >= kauf.normalize() + pd.Timedelta(days=wert)]
        if spaeter.empty:
            return {"rendite": letzter / einstieg - 1, "offen": True, "tag": ab.index[-1]}
        return {"rendite": float(spaeter["Close"].iloc[0]) / einstieg - 1, "offen": False, "tag": spaeter.index[0]}
    if art == "ziel":
        erreicht = bis_verkauf[bis_verkauf["Close"] >= einstieg * (1 + wert / 100)]
        if erreicht.empty:
            return {"rendite": eigener_ausstieg / einstieg - 1, "offen": False, "tag": bis_verkauf.index[-1] if not bis_verkauf.empty else ab.index[0]}
        halb = float(erreicht["Close"].iloc[0]) / einstieg - 1
        return {"rendite": (halb + (eigener_ausstieg / einstieg - 1)) / 2, "offen": False, "tag": erreicht.index[0]}
    if art == "atr":
        a = atr(df["High"], df["Low"], df["Close"]).reindex(ab.index)
        hoch = ab["Close"].cummax()
        ausgeloest = ab[(ab["Close"] < hoch - wert * a) & (ab.index > ab.index[0])]
        if ausgeloest.empty:
            return {"rendite": letzter / einstieg - 1, "offen": True, "tag": ab.index[-1]}
        return {"rendite": float(ausgeloest["Close"].iloc[0]) / einstieg - 1, "offen": False, "tag": ausgeloest.index[0]}
    raise ValueError(art)


def auswertung(b: Benutzer) -> dict:
    zeilen, _ = rundreisen(lade_trades(b))
    verkaeufe = [z for z in zeilen if z["typ"] == "verkauf" and z.get("teile")]
    if not verkaeufe:
        return {"fehler": "Noch keine abgeschlossenen Verkäufe im Export."}
    isins = {z["isin"]: z["art"] for z in verkaeufe}

    def laden(isin):
        try:
            tk = ticker_fuer(isin, isins[isin])
            return isin, (tk, tageskurse(tk)) if tk else (None, None)
        except Exception:
            return isin, (None, None)
    with ThreadPoolExecutor(max_workers=6) as pool:
        kurse = dict(pool.map(laden, isins))

    alle_regeln = [{"id": "eigen", "art": "eigen", "wert": None, "name": "Deine Exits"}, *REGELN]
    trades, ohne, tageshandel = [], [], []
    for v in verkaeufe:
        tk, df = kurse[v["isin"]]
        # Kauf und Verkauf am selben Tag: mit Tagesschlusskursen nicht nachzubilden
        if all(pd.Timestamp(t["zeit"]).date() == v["zeit"].date() for t in v["teile"]):
            tageshandel.append({"name": v["name"], "zeit": v["zeit"].isoformat(timespec="minutes"), "gewinn_echt": v["gewinn"]})
            continue
        if df is None or df.empty:
            ohne.append(v["name"])
            continue
        einsatz = sum(t["kosten"] for t in v["teile"])
        je = {}
        for r in alle_regeln:
            euro, offen = 0.0, False
            for teil in v["teile"]:
                s = simuliere(df, pd.Timestamp(teil["zeit"]).tz_localize(None), pd.Timestamp(v["zeit"]).tz_localize(None), r)
                if s is None:
                    euro = None
                    break
                euro += teil["kosten"] * s["rendite"]
                offen = offen or s["offen"]
            je[r["id"]] = None if euro is None else {"euro": euro, "prozent": euro / einsatz * 100 if einsatz else None, "offen": offen}
        if je["eigen"] is None:
            ohne.append(v["name"])
            continue
        trades.append({"zeit": v["zeit"].isoformat(timespec="minutes"), "name": v["name"], "ticker": tk, "einsatz": einsatz,
                       "gewinn_echt": v["gewinn"], "haltedauer": v["haltedauer"], "regeln": je})

    zusammen = []
    for r in alle_regeln:
        werte = [t["regeln"][r["id"]] for t in trades if t["regeln"].get(r["id"])]
        eigen = [t["regeln"]["eigen"]["euro"] for t in trades if t["regeln"].get(r["id"])]
        einsatz = sum(t["einsatz"] for t in trades if t["regeln"].get(r["id"]))
        if not werte:
            continue
        summe = sum(w["euro"] for w in werte)
        zusammen.append({**r, "summe": summe, "prozent": summe / einsatz * 100 if einsatz else None,
                         "besser_als_du": sum(w["euro"] > e + 0.005 for w, e in zip(werte, eigen)) / len(werte) * 100 if r["id"] != "eigen" else None,
                         "offen": sum(w["offen"] for w in werte), "anzahl": len(werte)})
    eigen = next(z for z in zusammen if z["id"] == "eigen")
    for z in zusammen:
        z["gegen_dich"] = z["summe"] - eigen["summe"]
    return {"regeln": sorted(zusammen, key=lambda z: -z["summe"]), "trades": list(reversed(trades)), "ohne": sorted(set(ohne)), "tageshandel": tageshandel,
            "anzahl": len(trades), "stichprobe_klein": len(trades) < MIN_STICHPROBE, "min_stichprobe": MIN_STICHPROBE,
            "varianten": len(REGELN)}
