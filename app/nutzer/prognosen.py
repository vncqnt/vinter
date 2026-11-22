"""Die Prognosen, die ein Benutzer verfolgt, plus die Fed-Wahrscheinlichkeiten und was sie
für sein Depot bedeuten können.

Die Auswahl steht in prognosen.yaml im Ordner des Benutzers.
"""
import yaml

from ..benutzer import Benutzer
from ..markt import prognosen as manifold
from ..markt.fed_futures import fed_wahrscheinlichkeiten

MAX_AUSWAHL = 30


def _datei(b: Benutzer):
    return b.datei("prognosen.yaml")


def _auswahl(b: Benutzer) -> list[dict]:
    datei = _datei(b)
    return (yaml.safe_load(datei.read_text(encoding="utf-8")) or []) if datei.exists() else []


def _speichern(b: Benutzer, eintraege: list[dict]) -> None:
    datei = _datei(b)
    datei.parent.mkdir(parents=True, exist_ok=True)
    zwischen = datei.with_suffix(".tmp")
    zwischen.write_text(yaml.safe_dump(eintraege, allow_unicode=True, sort_keys=False), encoding="utf-8")
    zwischen.replace(datei)


def hinzufuegen(b: Benutzer, daten: dict) -> dict:
    markt_id = str(daten.get("id", "")).strip()
    eintraege = _auswahl(b)
    if any(e["id"] == markt_id for e in eintraege):
        return {"ok": True}
    if len(eintraege) >= MAX_AUSWAHL:
        raise manifold.PrognosenFehler(f"Mehr als {MAX_AUSWAHL} Prognosen gehen nicht.")
    m = manifold.markt(markt_id)  # prüft auch, dass es die Prognose gibt
    _speichern(b, [*eintraege, {"quelle": "manifold", "id": markt_id, "frage": m["frage"]}])
    return {"ok": True}


def entfernen(b: Benutzer, markt_id: str) -> dict:
    _speichern(b, [e for e in _auswahl(b) if e["id"] != markt_id])
    return {"ok": True}


def ansicht(b: Benutzer, zins_wirkung: dict | None) -> dict:
    """Alles für die Seite: Fed, die eigenen Prognosen mit Veränderung, die Themen zum Stöbern."""
    fed = fed_wahrscheinlichkeiten()
    if not fed.get("fehler"):
        for s in fed["sitzungen"]:
            erhoehung = sum(x["p"] for x in s["diese_sitzung"] if x["aenderung_bp"] > 0)
            senkung = sum(x["p"] for x in s["diese_sitzung"] if x["aenderung_bp"] < 0)
            schluessel = f"fed:{s['datum']}:erhoehung"
            manifold.merken(schluessel, erhoehung)
            manifold.merken(f"fed:{s['datum']}:senkung", senkung)
            s["erhoehung"], s["senkung"], s["halten"] = erhoehung, senkung, max(0.0, 1 - erhoehung - senkung)
            s["veraenderung_erhoehung"] = manifold.veraenderung(schluessel, erhoehung)
            s["verlauf_erhoehung"] = manifold.verlauf(schluessel)
            # Verlauf der letzten 90 Tage: Erhöhung, Senkung, erwarteter Zins nach der Sitzung (M4)
            reihen = {k: {v["tag"]: v["wert"] for v in manifold.verlauf(f"fed:{s['datum']}:{k}")} for k in ("erhoehung", "senkung", "satz")}
            tage = sorted(set(reihen["erhoehung"]) | set(reihen["senkung"]))
            s["verlauf"] = {"tage": tage, **{k: [reihen[k].get(d) for d in tage] for k in reihen}}

    eigene, meldungen = [], []
    for e in _auswahl(b):
        try:
            m = manifold.markt(e["id"])
        except manifold.PrognosenFehler:
            meldungen.append(f"„{e['frage']}“ ist gerade nicht erreichbar.")
            continue
        schluessel = f"manifold:{e['id']}"
        wert = m.get("p") if m["art"] == "ja_nein" else (m.get("antworten") or [{}])[0].get("p")
        manifold.merken(schluessel, wert)
        eigene.append({**m, "veraenderung": manifold.veraenderung(schluessel, wert), "verlauf": manifold.verlauf(schluessel)})

    return {"fed": fed, "eigene": eigene, "meldungen": meldungen, "themen": manifold.themen()["themen"], "zins_wirkung": zins_wirkung}
