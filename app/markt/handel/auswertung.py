"""Auswertungen für die Oberfläche. Liest nur die neueste Version jedes Werts.

- fruehindikatoren(): neueste Werte mit Veränderung zum Vorjahr und Sigma (Vorjahresveränderung gegen die
  letzten 24 Monate), samt Quelle, Zeitraum, Veröffentlichung, Version.
- stroeme(): Chord-Diagramm und Partnertabelle eines Landes. Jeder Strom aus der eigenen Meldung des Landes
  (Hauptquelle), die Gegenmeldung des Partners steht nur daneben als Vergleich (Spiegeldaten).
- engstellen(): Durchfahrten je Meerenge, letzte Wochen gegen den Normalwert.
- status(): je Quelle Abrufe, Termine, Kontingent, Fehler, Quarantäne.
"""
from datetime import date, datetime, timedelta

import numpy as np

from . import speicher as S
from .laender import WELT, info
from .plan import in_warteschlange, laeuft, nachladen_comtrade
from .quellen import allgemein, erwartet, gruppen, konfig, naechster_termin

MONATSNAMEN = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]


def zeitraum_text(z: str, sonderfall: str | None = None) -> str:
    if len(z) == 7:
        j, m = int(z[:4]), int(z[5:])
        if sonderfall and "jan_feb_kombiniert" in sonderfall:
            return f"Januar–Februar {j}"
        return f"{MONATSNAMEN[m - 1]} {j}"
    if len(z) == 10:
        return f"Woche ab {z[8:]}.{z[5:7]}.{z[:4]}"
    return z


def _waren_bedingung(quelle: str, ware: str | None, gruppe: str | None) -> tuple[str, list]:
    if gruppe:
        g = gruppen().get(gruppe) or {}
        if quelle == "gacc":
            namen = g.get("china") or []
            if not namen:
                return "0", []
            return "(" + " OR ".join("LOWER(ware_text) LIKE ?" for _ in namen) + ")", [n.lower() + "%" for n in namen]
        codes = g.get("hs") or []
        if not codes:
            return "0", []
        return f"ware IN ({','.join('?' * len(codes))})", codes
    return "ware = ?", [ware or "TOTAL"]


def reihe(quelle: str, land: str | None, partner: str | None, richtung: str, ware: str | None = None, gruppe: str | None = None) -> list[dict]:
    """Monatsreihe (Summe über die Codes einer Gruppe), älteste zuerst."""
    bed, par = _waren_bedingung(quelle, ware, gruppe)
    wo = f"quelle = ? AND berichtsland IS ? AND partnerland IS ? AND richtung = ? AND {bed} AND LENGTH(zeitraum) = 7"
    zeilen = S.aktuelle(wo, [quelle, land, partner, richtung, *par])
    je = {}
    for z in zeilen:
        e = je.setdefault(z["zeitraum"], {"zeitraum": z["zeitraum"], "wert": 0.0, "teile": 0, "version": 1, "sonderfall": set(), "veroeffentlicht": None, "abgerufen": None})
        if z["wert_usd"] is not None:
            e["wert"] += z["wert_usd"]
            e["teile"] += 1
        e["version"] = max(e["version"], z["version"])
        e["sonderfall"] |= set(filter(None, (z["sonderfall"] or "").split(",")))
        e["veroeffentlicht"] = max(filter(None, [e["veroeffentlicht"], z["veroeffentlicht"]]), default=None)
        e["abgerufen"] = max(filter(None, [e["abgerufen"], z["abgerufen"]]), default=None)
    aus = []
    for k in sorted(je):
        e = je[k]
        if e["teile"]:
            aus.append({**e, "sonderfall": sorted(e["sonderfall"])})
    return aus


def _vorjahr(z: str) -> str:
    return f"{int(z[:4]) - 1}{z[4:]}"


def kennzahlen(r: list[dict]) -> dict:
    """Vorjahresveränderung je Monat und Sigma der neuesten gegen die 24 Monate davor.
    Februar: Ist eine Seite nur als Januar+Februar gemeldet, werden beide Seiten als Januar+Februar verglichen."""
    werte = {e["zeitraum"]: e["wert"] for e in r}
    kombi = {e["zeitraum"] for e in r if "jan_feb_kombiniert" in e["sonderfall"]}
    vj = []
    for e in r:
        z = e["zeitraum"]
        alt_z = _vorjahr(z)
        jetzt, alt = e["wert"], werte.get(alt_z)
        if z.endswith("-02") and alt is not None and (z in kombi) != (alt_z in kombi):
            jan_jetzt, jan_alt = werte.get(z[:5] + "01"), werte.get(alt_z[:5] + "01")
            if z not in kombi:
                jetzt = jetzt + jan_jetzt if jan_jetzt is not None else None
            else:
                alt = alt + jan_alt if jan_alt is not None else None
            e["sonderfall"] = sorted(set(e["sonderfall"]) | {"vergleich_jan_feb"})
        if z.endswith("-01") and (alt_z[:5] + "02") in kombi:
            alt = None  # Vorjahres-Januar steckt im kombinierten Februar
        e["vorjahr"] = alt
        e["yoy"] = (jetzt / alt - 1) * 100 if alt and jetzt is not None else None
        if e["yoy"] is not None:
            vj.append(e["yoy"])
    letzte = r[-1] if r else None
    z = None
    if letzte and letzte["yoy"] is not None:
        frueher = [e["yoy"] for e in r[-25:-1] if e["yoy"] is not None]
        if len(frueher) >= 12 and np.std(frueher) > 0:
            z = (letzte["yoy"] - float(np.mean(frueher))) / float(np.std(frueher))
    return {"letzte": letzte, "sigma": z}


def fruehindikatoren() -> list[dict]:
    aus = []
    grenze = float(allgemein()["auffaellig_sigma"])
    namen = {k: v.get("name", k) for k, v in (konfig().get("quellen") or {}).items()}
    for i, spec in enumerate(konfig().get("fruehindikatoren") or []):
        try:
            r = reihe(spec["quelle"], spec.get("land"), spec.get("partner"), spec["richtung"], spec.get("ware"), spec.get("gruppe"))
        except Exception:  # noqa: BLE001
            r = []
        k = kennzahlen(r)
        l = k["letzte"]
        g = gruppen().get(spec.get("gruppe") or "") or {}
        aus.append({
            "id": f"i{i}", "name": spec["name"], "quelle": spec["quelle"], "quelle_name": namen.get(spec["quelle"], spec["quelle"]),
            "gruppe": spec.get("gruppe"), "gruppe_name": g.get("name"), "land": spec.get("land"), "partner": spec.get("partner"), "richtung": spec["richtung"],
            "zeitraum": l["zeitraum"] if l else None, "zeitraum_text": zeitraum_text(l["zeitraum"], ",".join(l["sonderfall"])) if l else None,
            "wert": l["wert"] if l else None, "vorjahr": l["vorjahr"] if l else None, "yoy": l["yoy"] if l else None,
            "sigma": k["sigma"], "auffaellig": k["sigma"] is not None and abs(k["sigma"]) >= grenze,
            "veroeffentlicht": l["veroeffentlicht"] if l else None, "abgerufen": l["abgerufen"] if l else None,
            "version": l["version"] if l else None, "sonderfall": l["sonderfall"] if l else [],
            "verlauf": [{"zeitraum": e["zeitraum"], "wert": e["wert"], "yoy": e["yoy"], "kombiniert": "jan_feb_kombiniert" in e["sonderfall"], "revidiert": "revidiert" in e["sonderfall"]} for e in r[-36:]],
        })
    return aus


# ------------------------------------------------------------------ Handelsströme
def _jahreswerte(laender: list[str], richtung: str, gruppe: str | None, jahr: str) -> list[dict]:
    bed, par = _waren_bedingung("comtrade", None, gruppe)
    platz = ",".join("?" * len(laender))
    return S.aktuelle(f"quelle = 'comtrade' AND zeitraum = ? AND richtung = ? AND berichtsland IN ({platz}) AND {bed}", [jahr, richtung, *laender, *par])


def berichtslaender() -> list[dict]:
    """Länder mit Jahreswerten (für die Auswahl)."""
    z = S.abfrage("SELECT berichtsland, MAX(zeitraum) AS jahr FROM werte WHERE quelle = 'comtrade' AND LENGTH(zeitraum) = 4 GROUP BY berichtsland")
    return [{**info(x["berichtsland"]), "jahr": x["jahr"]} for x in z]


def stroeme(land: str, richtung: str = "export", gruppe: str | None = None, partner_anzahl: int = 15) -> dict:
    land = land.upper()
    gegen = "import" if richtung == "export" else "export"
    jahre = S.abfrage("SELECT DISTINCT zeitraum FROM werte WHERE quelle = 'comtrade' AND berichtsland = ? AND LENGTH(zeitraum) = 4 ORDER BY zeitraum DESC", (land,))
    if not jahre:
        neu = nachladen_comtrade(land)
        return {"land": info(land), "fehlt": True, "nachladen": neu or land in in_warteschlange(),
                "text": "Für dieses Land sind noch keine Jahreswerte geladen. Sie werden im Hintergrund nachgeladen (UN Comtrade), bitte in ein paar Minuten neu öffnen."}
    # Jahr: das neueste mit vollständigen Daten des Landes (Gesamtwert gegen die Welt vorhanden)
    jahr = None
    for j in jahre:
        if _jahreswerte([land], richtung, gruppe, j["zeitraum"]):
            jahr = j["zeitraum"]
            break
    if not jahr:
        return {"land": info(land), "fehlt": True, "text": "Für diese Warengruppe meldet das Land keine Werte."}
    eigene = {}
    for z in _jahreswerte([land], richtung, gruppe, jahr):
        eigene[z["partnerland"]] = eigene.get(z["partnerland"], 0) + (z["wert_usd"] or 0)
    welt = eigene.pop(WELT, None) or sum(eigene.values())
    partner = [p for p, _ in sorted(eigene.items(), key=lambda x: -x[1])][:partner_anzahl]
    knoten = [land] + partner
    # Matrix aus den Meldungen jedes Knotens (Hauptquelle = eigene Meldung des Berichtslands)
    zeilen = _jahreswerte(knoten, richtung, gruppe, jahr)
    melden = {z["berichtsland"] for z in zeilen}
    pos = {k: i for i, k in enumerate(knoten)}
    matrix = [[0.0] * len(knoten) for _ in knoten]
    for z in zeilen:
        if z["partnerland"] in pos and z["berichtsland"] in pos and z["partnerland"] != z["berichtsland"]:
            matrix[pos[z["berichtsland"]]][pos[z["partnerland"]]] += z["wert_usd"] or 0
    fehlend = [k for k in knoten if k not in melden]
    nachgeladen = [k for k in fehlend if k != "TWN" and nachladen_comtrade(k)]
    # Spiegeldaten: was der Partner über denselben Strom meldet (nur zum Vergleich)
    spiegel = {}
    for z in _jahreswerte(partner, gegen, gruppe, jahr):
        if z["partnerland"] == land:
            spiegel[z["berichtsland"]] = spiegel.get(z["berichtsland"], 0) + (z["wert_usd"] or 0)
    tabelle = []
    for p in partner:
        s = spiegel.get(p)
        tabelle.append({**info(p), "wert": eigene[p], "anteil": eigene[p] / welt * 100 if welt else None, "spiegel": s,
                        "abweichung": (s / eigene[p] - 1) * 100 if s and eigene[p] else None, "reexport": p in ("HKG", "SGP")})
    g = gruppen().get(gruppe or "") or {}
    return {"land": info(land), "jahr": jahr, "richtung": richtung, "gruppe": gruppe, "gruppe_name": g.get("name"), "gruppe_hinweis": g.get("hinweis"),
            "welt": welt, "knoten": [info(k) for k in knoten], "matrix": matrix, "partner": tabelle,
            "fehlend": [info(k) for k in fehlend], "wird_nachgeladen": nachgeladen or [k for k in fehlend if k in in_warteschlange()],
            "frisch": _frisch(land, richtung, gruppe), "quelle": "UN Comtrade, Jahreswerte, Meldung des jeweiligen Berichtslands"}


# ---- "Jahr bisher": laufendes Jahr aus Monatswerten je Partner ----
# USA: US-Zoll (Census), China: Zoll China (GACC), alle anderen: UN Comtrade (Monatswerte je Partner, soweit gemeldet).
# Verglichen wird mit denselben Monaten des Vorjahres aus derselben Quelle.
MONATS_QUELLE = {"USA": "census", "CHN": "gacc"}
MONATSNAMEN = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def _bisher_monate(land: str) -> tuple[str, list[str]] | None:
    """(Quelle, Monate des neuesten Jahres mit Partnerwerten) oder None."""
    q = MONATS_QUELLE.get(land, "comtrade")
    z = S.abfrage("SELECT DISTINCT zeitraum FROM werte WHERE quelle = ? AND berichtsland = ? AND LENGTH(zeitraum) = 7 AND partnerland != 'WLD' AND ware = 'TOTAL' ORDER BY zeitraum DESC", (q, land))
    if not z:
        return None
    jahr = z[0]["zeitraum"][:4]
    monate = sorted(x["zeitraum"] for x in z if x["zeitraum"][:4] == jahr)
    return q, monate


def _bisher_werte(quelle: str, land: str, monate: list[str], gruppe: str | None) -> dict:
    """{Partner: {export, import}} summiert über die Monate."""
    if not monate:
        return {}
    bed, par = _waren_bedingung(quelle, None, gruppe)
    platz = ",".join("?" * len(monate))
    werte = {}
    for z in S.aktuelle(f"quelle = ? AND berichtsland = ? AND zeitraum IN ({platz}) AND {bed}", [quelle, land, *monate, *par]):
        e = werte.setdefault(z["partnerland"], {"export": 0.0, "import": 0.0})
        e[z["richtung"]] = e.get(z["richtung"], 0.0) + (z["wert_usd"] or 0)
    return werte


def _bisher_name(monate: list[str]) -> str:
    m = [int(x[5:7]) for x in monate]
    return f"{monate[0][:4]} bisher ({MONATSNAMEN[m[0] - 1]}–{MONATSNAMEN[m[-1] - 1]})" if m else ""


def fluss(land: str, jahr: str | None = None, gruppe: str | None = None, ordnung: str = "gesamt", anzahl: int = 15) -> dict:
    """Handelsfluss eines Landes: je Partner Exporte und Importe aus der eigenen Meldung (Comtrade, Jahreswerte),
    dazu die Gegenmeldungen des Partners als Vergleich. ordnung: gesamt, export, import."""
    land = land.upper()
    jahre = [j["zeitraum"] for j in S.abfrage("SELECT DISTINCT zeitraum FROM werte WHERE quelle = 'comtrade' AND berichtsland = ? AND LENGTH(zeitraum) = 4 ORDER BY zeitraum DESC", (land,))]
    if not jahre:
        neu = nachladen_comtrade(land)
        return {"land": info(land), "fehlt": True, "nachladen": neu or land in in_warteschlange(),
                "text": "Für dieses Land sind noch keine Jahreswerte geladen. Sie werden im Hintergrund nachgeladen (UN Comtrade), bitte in ein paar Minuten neu öffnen."}
    mit_daten = [j for j in jahre if _jahreswerte([land], "export", gruppe, j) or _jahreswerte([land], "import", gruppe, j)]
    # Laufendes Jahr aus Monatswerten, wenn es neuer ist als das neueste volle Jahr
    namen = {j: j for j in mit_daten}
    bisher = _bisher_monate(land)
    bisher_id = None
    if bisher and (not mit_daten or bisher[1][0][:4] > mit_daten[0]):
        q_b, monate_b = bisher
        if _bisher_werte(q_b, land, monate_b, gruppe):
            bisher_id = f"{monate_b[0][:4]}-bisher"
            namen = {bisher_id: _bisher_name(monate_b), **namen}
            mit_daten = [bisher_id] + mit_daten
    if not mit_daten:
        return {"land": info(land), "fehlt": True, "text": "Für diese Warengruppe meldet das Land keine Werte."}
    # Standard: USA und China (vollständige Monatsquellen) mit dem laufenden Jahr, sonst das neueste volle Jahr
    standard = bisher_id if bisher_id and land in MONATS_QUELLE else next((j for j in mit_daten if j != bisher_id), mit_daten[0])
    jahr = jahr if jahr in mit_daten else standard
    vorjahr = {}
    if jahr == bisher_id:
        q_b, monate_b = bisher
        werte = _bisher_werte(q_b, land, monate_b, gruppe)
        vorjahr = _bisher_werte(q_b, land, [f"{int(m[:4]) - 1}{m[4:]}" for m in monate_b], gruppe)
        vorjahr.pop(WELT, None)
    else:
        werte = {}
        for richtung in ("export", "import"):
            for z in _jahreswerte([land], richtung, gruppe, jahr):
                e = werte.setdefault(z["partnerland"], {"export": 0.0, "import": 0.0})
                e[richtung] += z["wert_usd"] or 0
    welt = werte.pop(WELT, None) or {"export": sum(v["export"] for v in werte.values()), "import": sum(v["import"] for v in werte.values())}
    for v in werte.values():
        v["gesamt"] = v["export"] + v["import"]
    schluessel = ordnung if ordnung in ("export", "import") else "gesamt"
    partner = sorted(werte, key=lambda k: -werte[k][schluessel])[:anzahl]
    spiegel = {}
    if jahr != bisher_id:  # Gegenmeldungen gibt es nur für volle Jahre
        for richtung, gegen in (("export", "import"), ("import", "export")):
            for z in _jahreswerte(partner, gegen, gruppe, jahr):
                if z["partnerland"] == land:
                    spiegel.setdefault(z["berichtsland"], {}).setdefault(richtung, 0.0)
                    spiegel[z["berichtsland"]][richtung] += z["wert_usd"] or 0
    gesamt_welt = (welt.get("export") or 0) + (welt.get("import") or 0)
    g = gruppen().get(gruppe or "") or {}
    def veraenderung(p):
        v = vorjahr.get(p)
        alt = (v["export"] + v["import"]) if v else 0
        return (werte[p]["gesamt"] / alt - 1) * 100 if alt else None
    quelle_bisher = {"census": "US-Zoll (Census), Monatswerte", "gacc": "Zoll China (GACC), vorläufige Monatswerte", "comtrade": "UN Comtrade, Monatswerte, soweit gemeldet"}
    return {"land": info(land), "jahr": jahr, "jahr_name": namen.get(jahr, jahr), "jahre": mit_daten, "jahre_namen": namen, "bisher": jahr == bisher_id,
            "gruppe": gruppe, "gruppe_name": g.get("name"), "gruppe_hinweis": g.get("hinweis"),
            "ordnung": schluessel, "welt": welt,
            "partner": [{**info(p), **werte[p], "saldo": werte[p]["export"] - werte[p]["import"],
                         "anteil": werte[p]["gesamt"] / gesamt_welt * 100 if gesamt_welt else None,
                         "spiegel_export": spiegel.get(p, {}).get("export"), "spiegel_import": spiegel.get(p, {}).get("import"),
                         "vorjahr_prozent": veraenderung(p) if jahr == bisher_id else None,
                         "reexport": p in ("HKG", "SGP")} for p in partner],
            "quelle": (quelle_bisher[bisher[0]] + ", verglichen mit denselben Monaten des Vorjahres") if jahr == bisher_id else "UN Comtrade, Jahreswerte, Meldung des gewählten Landes"}


def paar(land: str, partner: str) -> dict:
    """Detail zu einem Handelspaar: Jahre, Warengruppen, Monatswerte (wo es sie gibt) und Spiegeldaten."""
    land, partner = land.upper(), partner.upper()
    jahre = {}
    for z in S.aktuelle("quelle = 'comtrade' AND LENGTH(zeitraum) = 4 AND ware = 'TOTAL' AND ((berichtsland = ? AND partnerland = ?) OR (berichtsland = ? AND partnerland = ?))",
                        [land, partner, partner, land]):
        e = jahre.setdefault(z["zeitraum"], {"jahr": z["zeitraum"], "export": None, "import": None, "spiegel_export": None, "spiegel_import": None})
        if z["berichtsland"] == land:
            e[z["richtung"]] = z["wert_usd"]
        else:  # Meldung des Partners: seine Importe = unsere Exporte laut Partner
            e["spiegel_export" if z["richtung"] == "import" else "spiegel_import"] = z["wert_usd"]
    # Anteil am Handel des Landes
    welt = {(z["zeitraum"], z["richtung"]): z["wert_usd"] for z in S.aktuelle("quelle = 'comtrade' AND LENGTH(zeitraum) = 4 AND ware = 'TOTAL' AND berichtsland = ? AND partnerland = 'WLD'", [land])}
    for e in jahre.values():
        for r in ("export", "import"):
            w = welt.get((e["jahr"], r))
            e[f"anteil_{r}"] = e[r] / w * 100 if e[r] and w else None
    # Warengruppen (eigene Meldung), neuestes und vorheriges Jahr
    gruppen_aus = []
    js = sorted(jahre)
    for gid, g in gruppen().items():
        zeile = {"id": gid, "name": g["name"]}
        for r in ("export", "import"):
            for j in js[-2:]:
                bed, par = _waren_bedingung("comtrade", None, gid)
                w = S.aktuelle(f"quelle = 'comtrade' AND berichtsland = ? AND partnerland = ? AND richtung = ? AND zeitraum = ? AND {bed}", [land, partner, r, j, *par])
                zeile[f"{r}_{j}"] = sum(x["wert_usd"] or 0 for x in w) if w else None
        gruppen_aus.append(zeile)
    # Monatswerte: eigene Meldung (USA: Census, China: Zoll), sonst die Meldung des Partners als Spiegel
    monate, monat_quelle, monat_spiegel = [], None, False
    for quelle, berichter, gegen, spiegel in (("census", "USA", partner, False), ("gacc", "CHN", partner, False), ("census", "USA", land, True), ("gacc", "CHN", land, True)):
        if (not spiegel and land == berichter) or (spiegel and partner == berichter):
            je = {}
            for z in S.aktuelle(f"quelle = ? AND berichtsland = ? AND partnerland = ? AND ware = 'TOTAL' AND LENGTH(zeitraum) = 7", [quelle, berichter, gegen]):
                r = z["richtung"] if not spiegel else ("import" if z["richtung"] == "export" else "export")
                je.setdefault(z["zeitraum"], {"monat": z["zeitraum"], "export": None, "import": None, "sonderfall": z["sonderfall"]})[r] = z["wert_usd"]
            if je:
                monate, monat_quelle, monat_spiegel = [je[k] for k in sorted(je)][-36:], quelle, spiegel
                break
    namen = {k: v.get("name", k) for k, v in (konfig().get("quellen") or {}).items()}
    return {"land": info(land), "partner": info(partner), "jahre": [jahre[j] for j in js], "gruppen": gruppen_aus, "gruppen_jahre": js[-2:],
            "monate": monate, "monat_quelle": namen.get(monat_quelle, monat_quelle), "monat_spiegel": monat_spiegel,
            "reexport": partner in ("HKG", "SGP")}


def _frisch(land: str, richtung: str, gruppe: str | None) -> dict | None:
    """Für USA und China gibt es Monatswerte nach Ländern: Summe der letzten 12 Monate."""
    quelle = {"USA": "census", "CHN": "gacc"}.get(land)
    if not quelle or (quelle == "gacc" and gruppe):
        return None
    bed, par = _waren_bedingung(quelle, None, gruppe)
    letzt = S.abfrage(f"SELECT MAX(zeitraum) AS z FROM werte WHERE quelle = ? AND berichtsland = ? AND richtung = ? AND {bed}", [quelle, land, richtung, *par])
    if not letzt or not letzt[0]["z"]:
        return None
    bis = letzt[0]["z"]
    j, m = int(bis[:4]), int(bis[5:])
    von = f"{j - 1}-{m + 1:02d}" if m < 12 else f"{j}-01"
    zeilen = S.aktuelle(f"quelle = ? AND berichtsland = ? AND richtung = ? AND {bed} AND zeitraum >= ? AND zeitraum <= ? AND LENGTH(zeitraum) = 7",
                        [quelle, land, richtung, *par, von, bis])
    je = {}
    for z in zeilen:
        je[z["partnerland"]] = je.get(z["partnerland"], 0) + (z["wert_usd"] or 0)
    welt = je.pop(WELT, None) or sum(je.values())
    top = sorted(je.items(), key=lambda x: -x[1])[:15]
    return {"quelle": quelle, "von": von, "bis": bis, "text": f"{zeitraum_text(von)} bis {zeitraum_text(bis)}", "welt": welt,
            "partner": [{**info(p), "wert": w, "anteil": w / welt * 100 if welt else None} for p, w in top]}


# ------------------------------------------------------------------ Engstellen (PortWatch)
def engstellen() -> dict:
    """Je Meerenge: Schnitt der letzten 4 Wochen gegen dieselben Wochen im Vorjahr und gegen 52 Wochen."""
    from ..lage import ENGSTELLEN
    zeilen = S.aktuelle("quelle = 'portwatch' AND ware IN ('alle', 'tanker')")
    je = {}
    for z in zeilen:
        je.setdefault((z["ort"], z["ware"]), []).append((z["zeitraum"], z["menge"]))
    aus = {}
    for (ort, ware), liste in je.items():
        liste.sort()
        if len(liste) < 8:
            continue
        wochen = [w for _, w in liste]
        letzt = date.fromisoformat(liste[-1][0])
        jetzt = float(np.mean(wochen[-4:]))
        vj = [w for d, w in liste if letzt - timedelta(days=372 + 21) <= date.fromisoformat(d) <= letzt - timedelta(days=358)]
        normal = float(np.mean(vj)) if vj else float(np.mean(wochen[-56:-4])) if len(wochen) > 8 else None
        name, lon, lat, notiz = ENGSTELLEN.get(ort, (ort, None, None, None))
        e = aus.setdefault(ort, {"id": ort, "name": name, "lon": lon, "lat": lat, "stand": liste[-1][0]})
        e[ware] = {"jetzt": jetzt, "normal": normal, "veraenderung": (jetzt / normal - 1) * 100 if normal else None,
                   "verlauf": [{"woche": d, "wert": w} for d, w in liste[-60:]]}
    return aus


# ------------------------------------------------------------------ Status
def status() -> dict:
    quellen = []
    allg = allgemein()
    for qid, cfg in (konfig().get("quellen") or {}).items():
        st = S.zustand(f"plan:{qid}", {})
        aktiv = cfg.get("aktiv", True) and cfg.get("art")
        zaehl = S.abfrage("SELECT COUNT(*) AS n, MAX(zeitraum) AS z FROM werte WHERE quelle = ?", (qid,))[0]
        quar = S.abfrage("SELECT COUNT(*) AS n FROM quarantaene WHERE quelle = ? AND erledigt = 0", (qid,))[0]["n"]
        fehler = S.abfrage("SELECT zeit, text FROM protokoll WHERE quelle = ? AND ok = 0 ORDER BY id DESC LIMIT 3", (qid,))
        letzte = S.abfrage("SELECT zeit, text FROM protokoll WHERE quelle = ? AND ok = 1 ORDER BY id DESC LIMIT 1", (qid,))
        rev = S.abfrage("SELECT COUNT(*) AS n FROM werte WHERE quelle = ? AND version > 1", (qid,))[0]["n"]
        unbekannt = sorted({n for k in ("census", "gacc_laender") for n in S.zustand(f"unbekannt:{qid}:{k}", []) or []})
        grenze = int((cfg.get("limit") or 100) * allg["limit_anteil"]) if aktiv else None
        quellen.append({
            "id": qid, "name": cfg.get("name", qid), "aktiv": bool(aktiv), "grund": cfg.get("grund"), "url": cfg.get("url"), "format": cfg.get("format"),
            "rhythmus": cfg.get("rhythmus"), "werte_hinweis": cfg.get("werte"),
            "letzter_erfolg": st.get("letzter_erfolg"), "letzter_versuch": st.get("letzter_versuch"), "letzte_pruefung": st.get("letzte_pruefung"),
            "fehler_jetzt": st.get("fehler"), "pause_bis": st.get("pause_bis"),
            "naechster_termin": (termin := naechster_termin(cfg, qid) if aktiv else None),
            "erwartet": erwartet(cfg, date.fromisoformat(termin[:10])) if termin else None,
            "kontingent": {"heute": S.anfragen_heute(qid), "grenze": grenze, "offiziell": cfg.get("limit")} if aktiv else None,
            "datensaetze": zaehl["n"], "neuester": zaehl["z"], "revisionen": rev, "quarantaene": quar,
            "letzte_fehler": fehler, "letzte_meldung": letzte[0] if letzte else None, "nicht_zugeordnet": unbekannt[:12],
            "laeuft": laeuft().get("quelle") == qid,
        })
    q = S.abfrage("SELECT id, quelle, zeit, art, grund, zeilen FROM quarantaene WHERE erledigt = 0 ORDER BY id DESC LIMIT 20")
    return {"quellen": quellen, "quarantaene": q, "warteschlange": in_warteschlange(), "einstellungen": allg}


def neuigkeiten(stunden: int = 48) -> list[dict]:
    seit = (datetime.utcnow() - timedelta(hours=stunden)).isoformat()
    z = S.abfrage("SELECT quelle, zeit, zeitraum, text FROM neuigkeiten WHERE zeit >= ? ORDER BY id DESC LIMIT 20", (seit,))
    # Erstbefüllung (viele Zeiträume auf einmal) nicht als Neuigkeit melden: nur den neuesten Zeitraum je Quelle
    je = {}
    for x in z:
        if x["quelle"] not in je or x["zeitraum"] > je[x["quelle"]]["zeitraum"]:
            je[x["quelle"]] = x
    namen = {k: v.get("name", k) for k, v in (konfig().get("quellen") or {}).items()}
    return [{**x, "quelle_name": namen.get(x["quelle"], x["quelle"]), "zeitraum_text": zeitraum_text(x["zeitraum"])} for x in je.values()]
