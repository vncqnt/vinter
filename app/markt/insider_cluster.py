"""Insider-Cluster-Käufe (Roadmap Edge 7): Wo kaufen mindestens 3 Insider innerhalb von 10 Tagen?

Quelle: SEC Form 4 (offiziell, kostenlos). Einmal am Tag durchsucht ein Hintergrund-Job alle Mitglieder von
S&P 500 und Nasdaq 100: je Firma die Liste der Einreichungen (data.sec.gov), dann die Form-4-Meldungen der
letzten 40 Tage (jede Meldung wird dauerhaft gemerkt und nie zweimal geladen). Gezählt werden nur Käufe an der
Börse (Transaktionscode P), keine Zuteilungen, Optionsausübungen oder Verkäufe. Ein Cluster: mindestens 3
verschiedene Personen kaufen innerhalb von 10 Kalendertagen. Die SEC erlaubt 10 Anfragen pro Sekunde; Vinter
bleibt mit einer Pause von 0,16 s je Anfrage darunter. Der erste Durchlauf dauert deshalb einige Minuten.
Cluster-Käufe gelten in Studien als stärkeres Zeichen als Einzelkäufe; eine Garantie sind sie nicht.
"""
import threading
import time
from datetime import date, datetime, timedelta, timezone

from .abruf import historie_eintragen, historie_lesen

MINDEST = 3
FENSTER = 10
TAGE = 40
_zustand = {"laeuft": False, "fortschritt": 0, "gesamt": 0}


def cluster_finden(kaeufe: list[dict], mindest: int = MINDEST, fenster: int = FENSTER) -> list[dict]:
    """Je Ticker das stärkste Fenster mit mindestens `mindest` verschiedenen Käufern. Reine Funktion.
    kaeufe: [{ticker, datum (ISO), wer, wert, rolle}]"""
    nach = {}
    for k in kaeufe:
        nach.setdefault(k["ticker"], []).append(k)
    aus = []
    for tk, liste in nach.items():
        liste.sort(key=lambda k: k["datum"])
        bestes = None
        for i, start in enumerate(liste):
            ende = date.fromisoformat(start["datum"]) + timedelta(days=fenster - 1)
            teil = [k for k in liste[i:] if date.fromisoformat(k["datum"]) <= ende]
            personen = {k["wer"] for k in teil}
            if len(personen) >= mindest and (not bestes or (len(personen), sum(k["wert"] for k in teil)) > (bestes["personen"], bestes["summe"])):
                bestes = {"ticker": tk, "von": teil[0]["datum"], "bis": teil[-1]["datum"], "personen": len(personen),
                          "summe": sum(k["wert"] for k in teil), "kaeufe": teil}
        if bestes:
            aus.append(bestes)
    return sorted(aus, key=lambda c: (c["bis"], c["personen"]), reverse=True)


def scan() -> dict:
    from .indizes import mitglieder
    from .sec_fonds import _cik, _einreichungen, _form4
    werte = {}
    for index in ("sp500", "ndx"):
        for m in mitglieder(index):
            werte.setdefault(m["ticker"], m.get("name", m["ticker"]))
    grenze = (date.today() - timedelta(days=TAGE)).isoformat()
    _zustand.update(laeuft=True, fortschritt=0, gesamt=len(werte))
    kaeufe, fehler = [], 0
    try:
        for tk, name in werte.items():
            _zustand["fortschritt"] += 1
            try:
                cik = _cik(tk)
                e = _einreichungen(cik)
                for i in range(len(e["form"])):
                    if e["form"][i] != "4" or e["filingDate"][i] < grenze:
                        continue
                    f = _form4(cik, e["accessionNumber"][i], e["primaryDocument"][i])
                    if f.get("emittent", cik) != cik:
                        continue
                    for g in f["geschaefte"]:
                        if g["art"] == "Kauf" and g["datum"] >= grenze:
                            kaeufe.append({"ticker": tk, "name": name, "datum": g["datum"], "wer": f["wer"], "rolle": f["rolle"],
                                           "wert": g["wert"], "stueck": g["stueck"], "preis": g["preis"]})
            except Exception:
                fehler += 1
    finally:
        _zustand["laeuft"] = False
    cluster = cluster_finden(kaeufe)
    # Vorstufe: zwei verschiedene Käufer in 10 Tagen (ohne die echten Cluster)
    knapp = [c for c in cluster_finden(kaeufe, mindest=2) if c["ticker"] not in {x["ticker"] for x in cluster}]
    namen = {k["ticker"]: k["name"] for k in kaeufe}
    for c in cluster + knapp:
        c["name"] = namen.get(c["ticker"], c["ticker"])
    ergebnis = {"stand": datetime.now(timezone.utc).isoformat(timespec="minutes"), "werte": len(werte), "fehler": fehler,
                "kaeufe": len(kaeufe), "firmen_mit_kaeufen": len({k["ticker"] for k in kaeufe}), "cluster": cluster, "knapp": knapp,
                "alle_kaeufe": sorted(kaeufe, key=lambda k: k["datum"], reverse=True),
                "einzeln": sorted(kaeufe, key=lambda k: -k["wert"])[:30], "regel": {"mindest": MINDEST, "fenster": FENSTER, "tage": TAGE}}
    historie_eintragen("insider:cluster", date.today().isoformat(), ergebnis)
    return ergebnis


def letztes() -> dict:
    verlauf = historie_lesen("insider:cluster")
    if not verlauf:
        return {"fehler": "Der erste Durchlauf läuft noch oder hat noch nicht begonnen.", **_zustand}
    tag = max(verlauf)
    return {**verlauf[tag], **_zustand}


def _schleife():
    from ..status import hinweis, job
    time.sleep(120)  # Server erst in Ruhe starten lassen
    while True:
        verlauf = historie_lesen("insider:cluster")
        if date.today().isoformat() not in verlauf:
            try:
                with job("Insider-Cluster"):
                    e = scan()
                hinweis("Insider-Cluster", f"{len(e['cluster'])} Cluster bei {e['werte']} Werten, {e['kaeufe']} Käufe")
            except Exception:
                pass
        time.sleep(3600)


def starte() -> None:
    threading.Thread(target=_schleife, name="insider-cluster", daemon=True).start()
