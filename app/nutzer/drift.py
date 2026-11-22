"""Post-Earnings-Drift für deine Werte und ihre Peer-Gruppen (Roadmap Q4), Rechnung in app/markt/drift.py.

Werte: deine Positionen (ohne Fonds und Krypto) und die Mitglieder ihrer gespeicherten Peer-Gruppen
(daten/markt/peers.yaml, es werden keine neuen Gruppen angelegt), zusammen höchstens 40.
"""
from concurrent.futures import ThreadPoolExecutor

from ..benutzer import Benutzer
from ..markt.drift import je_aktie

HOECHSTENS = 40


def auswertung(b: Benutzer, positionen: list[dict]) -> dict:
    eigene = [p["ticker"] for p in positionen if p.get("ticker") and p.get("art") == "STOCK" and not p.get("extra")]
    zusatz = [p["ticker"] for p in positionen if p.get("ticker") and p.get("extra")]
    peers = {}
    try:
        from ..markt.fundament.peers import _lesen
        for g in _lesen().values():
            if g.get("fuer") in eigene:
                for w in g.get("werte", []):
                    if w not in eigene:
                        peers.setdefault(w, g["fuer"])
    except Exception:
        pass
    alle = list(dict.fromkeys(eigene + zusatz + list(peers)))[:HOECHSTENS]
    namen = {p["ticker"]: p["name"] for p in positionen if p.get("ticker")}

    def sicher(tk):
        try:
            return je_aktie(tk)
        except Exception:
            return {"ticker": tk, "termine": [], "einordnung": {"anzahl": 0}}
    with ThreadPoolExecutor(4) as pool:
        ergebnisse = list(pool.map(sicher, alle))
    zeilen = []
    for e in ergebnisse:
        tk = e["ticker"]
        letzte = e["termine"][0] if e["termine"] else None
        zeilen.append({"ticker": tk, "name": namen.get(tk, tk), "rolle": "eigen" if tk in eigene else "markt" if tk in zusatz else "peer",
                       "peer_von": peers.get(tk), "einordnung": e["einordnung"], "letzte": letzte, "termine": e["termine"][:16], "markt": e.get("markt")})
    return {"werte": zeilen}
