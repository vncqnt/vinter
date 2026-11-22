"""Bewertet die News für die Thesen dieses Nutzers."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np

from ..benutzer import Benutzer
from ..cache import nutzer_lesen, nutzer_schreiben
from ..markt.news import (
    GLEICHE_MELDUNG, GOOGLE_REGIONEN, GUELTIG, MAX_MELDUNGEN, SICHERES_SIGNAL, TAGE, VOLLE_TENDENZ,
    WARNUNG_ABSTAND, WARNUNG_AB, hole_google, hole_yahoo, lade_modell, mit_cache,
)
from .thesen import alle_thesen


def _sammeln(these: dict) -> tuple[list, int]:
    auftraege = [(f"news-y:{t}", lambda t=t: hole_yahoo(t)) for t in these["ticker"]]
    for thema in these["news_themen"]:
        for hl, gl, ceid in GOOGLE_REGIONEN:
            auftraege.append((f"news-g:{hl}:{thema}", lambda thema=thema, hl=hl, gl=gl, ceid=ceid: hole_google(thema, hl, gl, ceid)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        ergebnisse = list(pool.map(lambda a: mit_cache(*a), auftraege))
    fehler = sum(1 for _, f in ergebnisse if f)

    grenze = datetime.now(timezone.utc) - timedelta(days=TAGE)
    eindeutig = {}
    for meldungen, _ in ergebnisse:
        for m in meldungen:
            if datetime.fromisoformat(m["zeit"]) >= grenze:
                eindeutig.setdefault(" ".join(m["titel"].lower().split()), m)
    neueste = sorted(eindeutig.values(), key=lambda m: m["zeit"], reverse=True)
    return neueste[:MAX_MELDUNGEN], fehler


def _bewerten(these: dict, meldungen: list) -> list:
    if not meldungen:
        return []
    modell = lade_modell()

    def kodieren(texte: list) -> np.ndarray:
        return modell.encode(texte, normalize_embeddings=True) if texte else np.zeros((0, 384))

    def hoechste(vektoren: np.ndarray, bezug: np.ndarray) -> np.ndarray:
        return (vektoren @ bezug.T).max(axis=1) if len(bezug) else np.zeros(len(vektoren))

    vektoren = kodieren([m["titel"] for m in meldungen])
    best = hoechste(vektoren, kodieren(these["bestaetigung"]))
    bruch = hoechste(vektoren, kodieren(these["bruch"]))
    # Die Kernthese allein reicht nicht: "Waymo expandiert" ist ihr wenig ähnlich, aber sehr wichtig.
    bezuege = ([these["kernthese"]] if these["kernthese"] else []) + these["news_themen"] + these["bestaetigung"] + these["bruch"]
    namen = (["Kernthese"] if these["kernthese"] else []) + these["news_themen"] + these["bestaetigung"] + these["bruch"]
    aehnlichkeit = vektoren @ kodieren(bezuege).T
    relevanz = aehnlichkeit.max(axis=1)
    bester_bezug = aehnlichkeit.argmax(axis=1)

    reihenfolge = np.argsort(-relevanz)
    behalten: list[int] = []
    ergebnis: list[dict] = []
    for i in reihenfolge:
        doppelt = next((n for n, k in enumerate(behalten) if float(vektoren[i] @ vektoren[k]) >= GLEICHE_MELDUNG), None)
        if doppelt is not None:
            ziel = ergebnis[doppelt]
            ziel["weitere"] += 1
            continue
        b, x = float(best[i]), float(bruch[i])
        # Skala von -100 (ähnelt nur Bruch-Signalen) bis +100 (ähnelt nur Bestätigungs-Signalen)
        tendenz = 0 if max(b, x) < SICHERES_SIGNAL else round(max(-1.0, min(1.0, (b - x) / VOLLE_TENDENZ)) * 100)
        behalten.append(int(i))
        ergebnis.append({
            **meldungen[i],
            "relevanz": round(float(relevanz[i]), 3),
            "tendenz": tendenz,
            "warnung": x >= WARNUNG_AB and x - b >= WARNUNG_ABSTAND,
            "passt_zu": namen[int(bester_bezug[i])],
            "weitere": 0,
        })
    return ergebnis


def thesen_news(b: Benutzer) -> dict:
    daten = alle_thesen(b)
    meldungen = list(daten["meldungen"])
    ausgabe = []
    for these in daten["thesen"]:
        inhalt = json.dumps({k: these[k] for k in ("ticker", "kernthese", "bestaetigung", "bruch", "news_themen")}, sort_keys=True, ensure_ascii=False)
        schluessel = "news-these2:" + hashlib.sha1(inhalt.encode("utf-8")).hexdigest()
        fertig, alter = nutzer_lesen(b, schluessel)
        if fertig is None or alter >= GUELTIG:
            gesammelt, fehler = _sammeln(these)
            fertig = _bewerten(these, gesammelt)
            if fehler:  # unvollständig: beim nächsten Mal neu versuchen
                meldungen.append(f"{these['name']}: {fehler} News-Quellen haben gerade nicht geantwortet.")
            else:
                nutzer_schreiben(b, schluessel, fertig)
        ausgabe.append({"id": these["id"], "name": these["name"], "news": fertig})
    return {"thesen": ausgabe, "meldungen": meldungen}
