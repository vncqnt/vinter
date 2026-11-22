"""Rechnet aus den Transaktionen Bestände, Einstand und realisierte Gewinne.

FIFO heißt "first in, first out": Ein Verkauf verbraucht immer zuerst die ältesten
Käufe. So rechnet auch das deutsche Steuerrecht bei Wertpapieren.
"""
from collections import defaultdict

REST = 1e-6  # kleinere Stückzahlen gelten als Rundungsrest


def berechne(trades: list[dict]) -> dict:
    kaeufe = defaultdict(list)  # ISIN -> Liste offener Käufe [Stück, Kosten je Stück]
    info = {}
    realisiert = defaultdict(lambda: {"gewinn": 0.0, "verkaeufe": 0})
    warnungen = []
    verkaeufe = []  # jeder Verkauf einzeln, für den Freibetrag-Tracker

    for t in trades:
        isin = t["isin"]
        info[isin] = {"name": t["name"], "art": t["art"]}
        if t.get("vorlaeufig"):
            warnungen.append(
                f"{t['name']}: Der {'Kauf' if t['typ'] == 'BUY' else 'Verkauf'} vom {t['zeit']:%d.%m.%Y} war beim Export "
                "noch nicht abgerechnet. Betrag geschätzt aus Stück × Kurs plus 1 € Gebühr. "
                "Lade morgen einen neuen Export hoch, dann steht der genaue Wert drin."
            )

        if t["typ"] == "BUY":
            kosten = -t["betrag"] + t["gebuehr"]
            kaeufe[isin].append([t["stueck"], kosten / t["stueck"]])
            continue

        menge = -t["stueck"]
        einstand = 0.0
        offen = kaeufe[isin]
        while menge > REST and offen:
            teil = min(menge, offen[0][0])
            einstand += teil * offen[0][1]
            offen[0][0] -= teil
            menge -= teil
            if offen[0][0] <= REST:
                offen.pop(0)

        if menge > REST:
            warnungen.append(
                f"{t['name']}: Verkauf vom {t['zeit']:%d.%m.%Y} ohne passenden Kauf im "
                "Export. Dieser Verkauf fehlt in den realisierten Gewinnen."
            )
            continue

        jahr = realisiert[t["zeit"].year]
        gewinn = t["betrag"] - t["gebuehr"] - einstand
        jahr["gewinn"] += gewinn
        jahr["verkaeufe"] += 1
        verkaeufe.append({"jahr": t["zeit"].year, "art": t["art"], "isin": isin, "gewinn": gewinn})

    positionen = []
    for isin, offen in kaeufe.items():
        stueck = sum(k[0] for k in offen)
        if stueck > REST:
            kosten = sum(k[0] * k[1] for k in offen)
            positionen.append({
                "isin": isin, **info[isin], "stueck": stueck,
                "einstand_gesamt": kosten, "einstand_stueck": kosten / stueck,
            })

    return {
        "positionen": positionen,
        "realisiert": [
            {"jahr": j, "gewinn": round(w["gewinn"], 2), "verkaeufe": w["verkaeufe"]}
            for j, w in sorted(realisiert.items(), reverse=True)
        ],
        "warnungen": warnungen,
        "verkaeufe": verkaeufe,
    }
