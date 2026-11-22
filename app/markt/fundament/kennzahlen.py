"""Kennzahlen aus der Bibliothek daten/markt/kennzahlen.yaml berechnen (Punkt 24c).

Die Formeln werden mit einem kleinen, sicheren Rechner ausgewertet: erlaubt sind nur Zahlen, Feldnamen,
+ - * / **, Klammern und die Funktionen abs, min, max, mittel. Alles andere wird abgelehnt.
Fehlt ein Feld oder wird durch 0 geteilt, ist das Ergebnis "n. a." (None).
"""
import ast
import operator
from functools import lru_cache

import numpy as np
import pandas as pd
import yaml

from ...konfig import MARKT_DATEN
from . import basis as B

DATEI = MARKT_DATEN / "kennzahlen.yaml"
KATEGORIEN = {"wachstum": "Wachstum", "profitabilitaet": "Profitabilität", "cashflow": "Cashflow und Investitionen", "bilanz": "Bilanz",
              "effizienz": "Effizienz", "aktionaersrendite": "Aktionärsrendite", "bewertung": "Bewertung", "qualitaet": "Qualitäts-Scores",
              "markt": "Markt und Erwartungen"}
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow}
_FUNK = {"abs": abs, "min": min, "max": max}


class FormelFehler(ValueError):
    pass


@lru_cache(maxsize=256)
def _baum(formel: str):
    try:
        b = ast.parse(str(formel), mode="eval")
    except SyntaxError as f:
        raise FormelFehler(f"Formel nicht lesbar: {formel} ({f.msg})")
    for k in ast.walk(b):
        if isinstance(k, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.USub, ast.UAdd, *_OPS)):
            continue
        if isinstance(k, ast.Constant) and isinstance(k.value, (int, float)):
            continue
        if isinstance(k, ast.Call) and isinstance(k.func, ast.Name) and k.func.id in (*_FUNK, "mittel") and not k.keywords:
            continue
        raise FormelFehler(f"In Formeln nicht erlaubt: {type(k).__name__} ({formel})")
    return b.body


def rechne(formel: str, werte: dict):
    """Wert der Formel oder None (fehlendes Feld, Division durch 0)."""
    def w(k):
        if isinstance(k, ast.Constant):
            return float(k.value)
        if isinstance(k, ast.Name):
            v = werte.get(k.id)
            return None if v is None else float(v)
        if isinstance(k, ast.UnaryOp):
            x = w(k.operand)
            return None if x is None else (-x if isinstance(k.op, ast.USub) else x)
        if isinstance(k, ast.BinOp):
            a, b = w(k.left), w(k.right)
            if a is None or b is None:
                return None
            if isinstance(k.op, ast.Div) and b == 0:
                return None
            try:
                return _OPS[type(k.op)](a, b)
            except (OverflowError, ZeroDivisionError, ValueError):
                return None
        if isinstance(k, ast.Call):
            args = [w(a) for a in k.args]
            if k.func.id == "mittel":  # Durchschnitt; fehlt ein Wert, gilt der andere
                gueltig = [a for a in args if a is not None]
                return sum(gueltig) / len(gueltig) if gueltig else None
            if any(a is None for a in args):
                return None
            return _FUNK[k.func.id](*args)
        raise FormelFehler(type(k).__name__)
    erg = w(_baum(formel))
    if erg is None or isinstance(erg, complex) or erg != erg or abs(erg) == float("inf"):
        return None
    return erg


def bibliothek() -> dict:
    """{"kennzahlen": [..], "sets": {..}} aus der YAML-Datei, Formeln geprüft. Fehlerhafte Einträge werden mit Grund gemeldet."""
    try:
        roh = yaml.safe_load(DATEI.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, yaml.YAMLError) as f:
        return {"kennzahlen": [], "sets": {}, "fehler": [f"kennzahlen.yaml nicht lesbar ({type(f).__name__})"]}
    liste, fehler = [], []
    for kat, eintraege in roh.items():
        if kat == "sets" or not isinstance(eintraege, list):
            continue
        for e in eintraege:
            if not isinstance(e, dict) or not e.get("id") or not e.get("formel"):
                fehler.append(f"{kat}: Eintrag ohne id oder formel")
                continue
            try:
                _baum(str(e["formel"]))
            except FormelFehler as f:
                fehler.append(f"{e['id']}: {f}")
                continue
            liste.append({"id": str(e["id"]), "name": e.get("name") or e["id"], "kategorie": kat, "kategorie_name": KATEGORIEN.get(kat, kat),
                          "formel": str(e["formel"]), "einheit": e.get("einheit", "zahl"), "hoeher_besser": e.get("hoeher_besser"),
                          "erklaerung": e.get("erklaerung", ""), "positiv": list(e.get("positiv") or []), "nicht_fuer": list(e.get("nicht_fuer") or []),
                          "jahresbezug": bool(e.get("jahresbezug")), "aktuell": bool(e.get("aktuell"))})
    sets = {k: {"id": k, "name": v.get("name", k), "kennzahlen": [x for x in v.get("kennzahlen") or []]} for k, v in (roh.get("sets") or {}).items() if isinstance(v, dict)}
    return {"kennzahlen": liste, "sets": sets, "fehler": fehler}


# ---------------------------------------------------------------- Werte je Periode
def _vorjahr(art: str, p: str, perioden: dict) -> dict | None:
    if art == "jahr":
        return perioden.get(f"GJ{int(p[2:]) - 1}")
    try:
        return perioden.get(str(pd.Period(p, freq="Q") - 4))
    except ValueError:
        return None


def _markt_zum_ende(z: dict, kurse: pd.Series, f_kurs: float | None, aktien_heute: float | None) -> dict:
    """Kurs, Börsenwert und EV zum Periodenende (Kurs in Anzeigewährung)."""
    monat = (z.get("_ende") or "")[:7]
    k = None
    if monat and len(kurse):
        frueher = kurse[kurse.index <= monat]
        k = float(frueher.iloc[-1]) if len(frueher) else None
    if k is not None and f_kurs:
        k = k * f_kurs
    aktien = z.get("aktien_ausstehend") or z.get("aktien") or aktien_heute
    bw = k * aktien if k is not None and aktien else None
    ev = bw + (z.get("schulden") or 0) - (z.get("cash") or 0) if bw is not None else None
    return {"kurs": k, "boersenwert": bw, "ev": ev}


def _variablen(z: dict, vj: dict | None, markt: dict, jahresfaktor: float, mitarbeiter) -> dict:
    v = {}
    for k, x in z.items():
        if k.startswith("_"):
            continue
        v[k] = x * jahresfaktor if (x is not None and k in B.FLUSS and jahresfaktor != 1) else x
    for k, x in (vj or {}).items():
        if not k.startswith("_"):
            v[f"vj_{k}"] = x * jahresfaktor if (x is not None and k in B.FLUSS and jahresfaktor != 1) else x
    v.update(markt)
    v["mitarbeiter"] = mitarbeiter
    return v


def _wert(kz: dict, variablen: dict, sektor: str | None):
    if sektor and sektor in kz["nicht_fuer"]:
        return None, "branche"
    for feld in kz["positiv"]:
        x = variablen.get(feld)
        if x is not None and x <= 0:
            return None, "negativ"
    erg = rechne(kz["formel"], variablen)
    if erg is None:
        return None, "fehlt"
    return (erg * 100 if kz["einheit"] == "prozent" else erg), None


def marktwerte(m: dict, ttm: dict | None, vj_ttm: dict | None, f_kurs: float | None) -> dict:
    """Werte, die es nur heute gibt (Schätzungen, Kursziel, Scores)."""
    z = lambda x: B._z(x)
    kurs = z(m.get("kurs"))
    trend = (m.get("eps_trend") or {})
    jetzt, vor30, vor90 = trend.get("current", {}), trend.get("30daysAgo", {}), trend.get("90daysAgo", {})
    def veraenderung(a, b):
        return (a / b - 1) if a and b and b > 0 else None
    eps_ttm = z(m.get("trailingEps"))
    eps_naechstes = z(jetzt.get("+1y"))
    wachstum_2j = ((eps_naechstes / eps_ttm) ** 0.5 - 1) if eps_ttm and eps_naechstes and eps_ttm > 0 and eps_naechstes > 0 else None
    fwd = z(m.get("forwardPE"))
    peg = (fwd / (wachstum_2j * 100)) if fwd and fwd > 0 and wachstum_2j and wachstum_2j > 0 else None
    ziel = z(m.get("targetMeanPrice"))
    short_alt, short_neu = z(m.get("sharesShortPriorMonth")), z(m.get("sharesShort"))
    dy = z(m.get("dividendYield"))
    aus = {
        "fwd_kgv": fwd if fwd and fwd > 0 else None, "fwd_eps": z(m.get("forwardEps")), "peg": peg,
        "kursziel": ziel, "kursziel_abstand": (ziel / kurs - 1) if ziel and kurs else None, "rating": z(m.get("recommendationMean")),
        "analysten": z(m.get("numberOfAnalystOpinions")), "short_quote": z(m.get("shortPercentOfFloat")),
        "short_veraenderung": veraenderung(short_neu, short_alt), "institutionell": z(m.get("heldPercentInstitutions")),
        "insider_netto": z((m.get("insider") or {}).get("netto_prozent")),
        "schaetzung_1m": veraenderung(z(jetzt.get("+1y")), z(vor30.get("+1y"))), "schaetzung_3m": veraenderung(z(jetzt.get("+1y")), z(vor90.get("+1y"))),
        "wachstum_2j": wachstum_2j,
        "umsatz_wachstum_fwd": z(((m.get("umsatz_schaetzung") or {}).get("+1y") or {}).get("wachstum")),
        "div_rendite": dy / 100 if dy is not None else None,  # Yahoo liefert Prozent (0,79 = 0,79 %)
    }
    aus["piotroski"] = piotroski(ttm, vj_ttm)
    aus["altman_z"] = altman_z(ttm, m, f_kurs)
    return aus


def piotroski(t: dict | None, v: dict | None) -> int | None:
    """Piotroski F-Score aus TTM und TTM ein Jahr früher. Braucht beide Zeitpunkte."""
    if not t or not v:
        return None
    g = lambda d, k: d.get(k)
    def quote(d, a, b):
        x, y = g(d, a), g(d, b)
        return x / y if x is not None and y else None
    tests = []
    roa, roa_v = quote(t, "nettogewinn", "bilanzsumme"), quote(v, "nettogewinn", "bilanzsumme")
    tests.append(roa is not None and roa > 0)
    tests.append(g(t, "ocf") is not None and g(t, "ocf") > 0)
    tests.append(roa is not None and roa_v is not None and roa > roa_v)
    tests.append(g(t, "ocf") is not None and g(t, "nettogewinn") is not None and g(t, "ocf") > g(t, "nettogewinn"))
    hebel, hebel_v = quote(t, "schulden", "bilanzsumme"), quote(v, "schulden", "bilanzsumme")
    tests.append(hebel is not None and hebel_v is not None and hebel <= hebel_v)
    lq, lq_v = quote(t, "umlaufvermoegen", "kurzfr_verbindlichkeiten"), quote(v, "umlaufvermoegen", "kurzfr_verbindlichkeiten")
    tests.append(lq is not None and lq_v is not None and lq > lq_v)
    tests.append(g(t, "aktien") is not None and g(v, "aktien") is not None and g(t, "aktien") <= g(v, "aktien") * 1.005)
    bm, bm_v = quote(t, "bruttogewinn", "umsatz"), quote(v, "bruttogewinn", "umsatz")
    tests.append(bm is not None and bm_v is not None and bm > bm_v)
    um, um_v = quote(t, "umsatz", "bilanzsumme"), quote(v, "umsatz", "bilanzsumme")
    tests.append(um is not None and um_v is not None and um > um_v)
    bekannt = [roa, g(t, "ocf"), hebel, lq, bm, um]
    if sum(x is not None for x in bekannt) < 4:
        return None
    return int(sum(tests))


def altman_z(t: dict | None, m: dict, f_kurs: float | None) -> float | None:
    if not t:
        return None
    ta, tl = t.get("bilanzsumme"), t.get("verbindlichkeiten")
    bw = B._z(m.get("marketCap"))
    if bw and f_kurs:
        bw *= f_kurs
    teile = [t.get("working_capital"), t.get("gewinnruecklagen"), t.get("ebit") or t.get("operatives_ergebnis"), bw, t.get("umsatz")]
    if not ta or not tl or any(x is None for x in teile):
        return None
    wc, re_, ebit, mv, umsatz = teile
    return 1.2 * wc / ta + 1.4 * re_ / ta + 3.3 * ebit / ta + 0.6 * mv / tl + umsatz / ta


def rechnen(ticker: str, art: str = "ttm", modus: str = "absolut", waehrung: str = "USD", bereinigt: bool = False, perioden_anzahl: int = 12) -> dict:
    """Alle Kennzahlen eines Werts als Zeitreihe (art: quartal, jahr, ttm; modus: absolut, je_aktie, wachstum) und heute."""
    roh = B.rohdaten(ticker)
    d = B.aufbereiten(roh, waehrung, bereinigt)
    m = roh["markt"]
    sektor = m.get("sector")
    f_kurs = B.kurs(d["waehrung_kurs"], waehrung, pd.Timestamp.now().strftime("%Y-%m"))
    try:
        kurse = B.kursverlauf(ticker)
    except Exception:  # noqa: BLE001
        kurse = pd.Series(dtype=float)
    # Kursverlauf in Anzeigewährung je Monat
    if d["waehrung_kurs"] != waehrung and len(kurse):
        kurse = pd.Series({mo: v * (B.kurs(d["waehrung_kurs"], waehrung, mo) or np.nan) for mo, v in kurse.items()}).dropna()
    f_ende = 1.0
    perioden = d[art]
    schluessel = list(perioden)[-perioden_anzahl:]
    bib = bibliothek()
    mitarbeiter = B._z(m.get("fullTimeEmployees"))
    faktor = 4.0 if art == "quartal" else 1.0
    aktien_heute = B._z(m.get("sharesOutstanding"))
    # Heute: letzte TTM (sonst letztes Geschäftsjahr) mit heutigem Kurs
    ttm = d["ttm"]
    letzt_p = list(ttm)[-1] if ttm else None
    letzt = ttm[letzt_p] if letzt_p else (list(d["jahr"].values())[-1] if d["jahr"] else None)
    vj_letzt = _vorjahr("quartal", letzt_p, ttm) if letzt_p else (_vorjahr("jahr", list(d["jahr"])[-1], d["jahr"]) if d["jahr"] else None)
    vj_basis = "12 Monate" if letzt_p else "Geschäftsjahr"
    if letzt and not vj_letzt and d["jahr"]:
        # Zu wenige Quartale: das Geschäftsjahr, das etwa ein Jahr vor dem Stand endet, als Vorjahr
        ziel = pd.Timestamp(letzt["_ende"]) - pd.Timedelta(days=365)
        naechstes = min(d["jahr"].values(), key=lambda z: abs(pd.Timestamp(z["_ende"]) - ziel))
        if abs(pd.Timestamp(naechstes["_ende"]) - ziel).days <= 120:
            vj_letzt = naechstes
            vj_basis = "12 Monate gegen das Geschäftsjahr davor"
    kurs_heute = B._z(m.get("kurs"))
    kurs_heute = kurs_heute * f_kurs if kurs_heute is not None and f_kurs else None
    bw_heute = B._z(m.get("marketCap"))
    bw_heute = bw_heute * f_kurs if bw_heute and f_kurs else None
    markt_heute = {"kurs": kurs_heute, "boersenwert": bw_heute,
                   "ev": bw_heute + ((letzt or {}).get("schulden") or 0) - ((letzt or {}).get("cash") or 0) if bw_heute else None}
    extra = marktwerte(m, letzt, vj_letzt, f_kurs)
    aus = []
    for kz in bib["kennzahlen"]:
        werte, gruende = [], []
        for p in schluessel:
            z = perioden[p]
            vj = _vorjahr(art, p, perioden)
            mk = _markt_zum_ende(z, kurse, f_ende, aktien_heute)
            fak = faktor if kz["jahresbezug"] else 1.0
            if kz["aktuell"]:
                werte.append(None); gruende.append("nur_heute")
                continue
            w, grund = _wert(kz, _variablen(z, vj, mk, fak, mitarbeiter), sektor)
            if modus == "je_aktie" and kz["einheit"] == "betrag" and w is not None:
                w = w / z["aktien"] if z.get("aktien") else None
            if modus == "wachstum" and kz["einheit"] in ("betrag", "je_aktie") and w is not None:
                v_alt, _ = _wert(kz, _variablen(vj, _vorjahr(art, _vorjahr_schluessel(art, p), perioden), _markt_zum_ende(vj, kurse, f_ende, aktien_heute), fak, mitarbeiter), sektor) if vj else (None, None)
                w = (w / v_alt - 1) * 100 if v_alt and v_alt > 0 else None
            werte.append(w); gruende.append(grund)
        heute, grund_heute = (None, None)
        if letzt:
            heute, grund_heute = _wert(kz, _variablen(letzt, vj_letzt, {**markt_heute, **extra}, 1.0, mitarbeiter), sektor)
        aus.append({**{k: kz[k] for k in ("id", "name", "kategorie", "kategorie_name", "einheit", "hoeher_besser", "erklaerung", "aktuell")},
                    "werte": werte, "gruende": gruende, "heute": heute, "grund_heute": grund_heute,
                    "wachstum_moeglich": kz["einheit"] in ("betrag", "je_aktie")})
    return {"ticker": ticker.upper(), "name": m.get("longName") or m.get("shortName") or ticker, "sektor": sektor, "branche": m.get("industry"),
            "umsatz_wachstum": umsatz_wachstum(letzt, vj_letzt, m, vj_basis),
            "land": m.get("country"), "art": art, "modus": modus, "waehrung": waehrung, "bereinigt": bereinigt,
            "waehrung_bilanz": d["waehrung_bilanz"], "kursquelle": d["kursquelle"],
            "perioden": [{"id": p, "ende": perioden[p].get("_ende"), "quelle": perioden[p].get("_quelle", "Yahoo")} for p in schluessel],
            "stand_heute": letzt_p or (list(d["jahr"])[-1] if d["jahr"] else None), "abgerufen": roh["abgerufen"],
            "kennzahlen": aus, "markt": {k: m.get(k) for k in ("kurs", "currency", "marketCap", "recommendationKey", "numberOfAnalystOpinions", "targetMeanPrice",
                                                                  "targetLowPrice", "targetHighPrice", "sharesShort", "shortPercentOfFloat", "heldPercentInstitutions")},
            "schaetzungen": {"eps_trend": m.get("eps_trend"), "eps": m.get("eps_schaetzung"), "umsatz": m.get("umsatz_schaetzung"), "revisionen": m.get("revisionen")},
            "insider": m.get("insider"), "fehler_bibliothek": bib["fehler"]}


def umsatz_wachstum(letzt: dict | None, vj: dict | None, m: dict, basis: str) -> dict:
    """Umsatzwachstum zum Vorjahr in Prozent, mit Herkunft. Für Nicht-US-Firmen hat Yahoo oft nur 5 Quartale oder
    nur Halbjahre: dann das letzte Geschäftsjahr gegen das davor, zuletzt Yahoos Quartalswachstum zum Vorjahresquartal."""
    u, u_vj = (letzt or {}).get("umsatz"), (vj or {}).get("umsatz")
    if u is not None and u_vj and u_vj > 0:
        return {"wert": (u / u_vj - 1) * 100, "basis": basis}
    g = B._z(m.get("revenueGrowth"))
    if g is not None:
        return {"wert": g * 100, "basis": "letztes Quartal zum Vorjahresquartal (Yahoo)"}
    return {"wert": None, "basis": None}


def _vorjahr_schluessel(art: str, p: str) -> str:
    return f"GJ{int(p[2:]) - 1}" if art == "jahr" else str(pd.Period(p, freq="Q") - 4)
