"""Einleser: machen aus einer Lieferung (so wie sie aus dem Netz kam) Zeilen im gemeinsamen Schema.

Reine Funktionen ohne Netz, abgesichert durch tests/test_handel.py. Passt der Aufbau einer Lieferung nicht
mehr zu dem, was der Einleser erwartet (z. B. eine Webseite wurde umgebaut), wirft er StrukturFehler.
Dann landet die Lieferung in der Quarantäne und die Status-Seite warnt, statt falsche Zahlen zu speichern.

Jeder Einleser gibt (zeilen, probleme) zurück. Probleme sind Prüfungen, die nicht bestanden wurden
(Summen passen nicht, negative Werte …); eine Lieferung mit Problemen kommt ebenfalls in die Quarantäne.
"""
import io
import json
import re

import pandas as pd

from .laender import WELT, iso3, von_comtrade

MONATE = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}
REEXPORT = {"HKG", "SGP"}  # Drehkreuze: ein Teil der Ware geht weiter in andere Länder


class StrukturFehler(ValueError):
    """Die Lieferung sieht anders aus als erwartet."""


def _zahl(x) -> float | None:
    if x is None:
        return None
    s = str(x).replace(",", "").replace("—", "").replace("—", "").strip()
    if s in ("", "-", "nan", "None", "NaN", "…", "..."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _sonder(partner: str | None, *weitere) -> str | None:
    teile = [w for w in weitere if w]
    if partner in REEXPORT:
        teile.append("reexport_moeglich")
    return ",".join(sorted(set(teile))) or None


def summen_pruefen(zeilen: list[dict], toleranz: float, gruppe=("berichtsland", "richtung", "ware", "zeitraum"), extra: dict | None = None) -> list[str]:
    """Summe der Länder gegen den Gesamtwert (Partner WLD). extra: Werte nicht zugeordneter Partner je Gruppe,
    die in die Summe gehören, aber nicht gespeichert werden."""
    probleme = []
    summen, gesamt = {}, {}
    for z in zeilen:
        k = tuple(z.get(f) for f in gruppe)
        if z["partnerland"] == WELT:
            gesamt[k] = z["wert_usd"]
        elif z.get("wert_usd") is not None:
            summen[k] = summen.get(k, 0) + z["wert_usd"]
    for k, s in (extra or {}).items():
        summen[k] = summen.get(k, 0) + s
    for k, g in gesamt.items():
        if not g or k not in summen:
            continue
        abw = abs(summen[k] / g - 1) * 100
        if abw > toleranz:
            probleme.append(f"Summe der Länder weicht um {abw:.1f} % vom Gesamtwert ab ({' '.join(map(str, k))})")
    return probleme


def zusammenfassen(zeilen: list[dict]) -> list[dict]:
    """Zeigen zwei Namen einer Quelle auf dasselbe Land (z. B. Westjordanland und Gaza -> PSE), werden sie addiert."""
    je = {}
    for z in zeilen:
        k = (z["berichtsland"], z["partnerland"], z["richtung"], z["ware"], z["zeitraum"])
        if k in je:
            alt = je[k]
            for f in ("wert_usd", "menge"):
                if z.get(f) is not None:
                    alt[f] = (alt.get(f) or 0) + z[f]
        else:
            je[k] = dict(z)
    return list(je.values())


def grundpruefung(zeilen: list[dict]) -> list[str]:
    probleme = []
    for z in zeilen:
        fehlt = [f for f in ("quelle", "richtung", "ware", "zeitraum") if not z.get(f)]
        if fehlt:
            probleme.append(f"Pflichtfeld fehlt: {', '.join(fehlt)}")
            break
        if z.get("wert_usd") is not None and z["wert_usd"] < 0 and z["richtung"] in ("export", "import", "umsatz"):
            probleme.append(f"Negativer Wert: {z.get('berichtsland')} {z.get('partnerland')} {z['ware']} {z['zeitraum']}")
            break
    return probleme


# ---------------------------------------------------------------- Census (USA)
def census(daten: list, richtung: str, ware: str, ware_text: str, toleranz: float) -> tuple[list[dict], list[str], list[str]]:
    """Antwort der Census-API (Liste von Listen, erste Zeile = Spaltennamen). Gibt zusätzlich nicht
    zugeordnete Ländernamen zurück."""
    if not isinstance(daten, list) or not daten or not isinstance(daten[0], list):
        raise StrukturFehler("Census: keine Tabelle in der Antwort")
    kopf = daten[0]
    wert_feld = "ALL_VAL_MO" if richtung == "export" else "GEN_VAL_MO"
    menge_feld = "QTY_1_MO" if richtung == "export" else "GEN_QY1_MO"
    for f in ("CTY_CODE", "CTY_NAME", wert_feld, "time"):
        if f not in kopf:
            raise StrukturFehler(f"Census: Spalte {f} fehlt")
    i = {f: kopf.index(f) for f in kopf}
    zeilen, unbekannt, rest = [], set(), {}
    ebene = None if ware == "TOTAL" else str(len(ware))
    for r in daten[1:]:
        code, name = r[i["CTY_CODE"]], r[i["CTY_NAME"]] or ""  # in alten Jahren (vor 2017) fehlt der Name teils
        wert = _zahl(r[i[wert_feld]])
        zeitraum = r[i["time"]]
        if code != "-" and (code.startswith("0") or "X" in code):
            continue  # Ländergruppen (EU, ASEAN, Kontinente)
        land = WELT if code == "-" else (iso3(name) if name else None)
        if not land:
            unbekannt.add(name or f"Code {code}")
            k = ("USA", richtung, ware, zeitraum)
            rest[k] = rest.get(k, 0) + (wert or 0)
            continue
        menge = _zahl(r[i[menge_feld]]) if menge_feld in i else None
        zeilen.append({"quelle": "census", "berichtsland": "USA", "partnerland": land, "richtung": richtung, "ware": ware, "ware_text": ware_text,
                       "hs_ebene": ebene, "hs_version": "HS (Schedule B/HTS)", "zeitraum": zeitraum, "wert_usd": wert,
                       "menge": menge if menge else None, "einheit": (r[i["UNIT_QY1"]] if "UNIT_QY1" in i and menge else None),
                       "waehrung": "USD", "sonderfall": _sonder(land)})
    zeilen = zusammenfassen(zeilen)
    probleme = grundpruefung(zeilen) + summen_pruefen(zeilen, toleranz, extra=rest)
    return zeilen, probleme, sorted(unbekannt)


# ---------------------------------------------------------------- UN Comtrade
def comtrade(antwort: dict, toleranz: float) -> tuple[list[dict], list[str]]:
    if not isinstance(antwort, dict) or "data" not in antwort:
        raise StrukturFehler("Comtrade: Feld data fehlt")
    if antwort.get("error"):
        raise StrukturFehler(f"Comtrade meldet: {antwort['error']}")
    zeilen, rest = [], {}
    for d in antwort["data"]:
        for f in ("reporterCode", "partnerCode", "flowCode", "cmdCode", "period", "primaryValue"):
            if f not in d:
                raise StrukturFehler(f"Comtrade: Feld {f} fehlt")
        if d["flowCode"] not in ("X", "M") or d.get("partner2Code", 0) not in (0, None) or d.get("motCode", 0) not in (0, None) or d.get("customsCode", "C00") not in ("C00", None):
            continue
        berichtsland, partner = von_comtrade(d["reporterCode"]), von_comtrade(d["partnerCode"])
        richtung = "export" if d["flowCode"] == "X" else "import"
        p = str(d["period"])
        zeitraum = f"{p[:4]}-{p[4:6]}" if len(p) == 6 else p
        cmd = str(d["cmdCode"])
        if not berichtsland:
            continue
        if not partner:
            k = (berichtsland, richtung, cmd, zeitraum)
            rest[k] = rest.get(k, 0) + (d["primaryValue"] or 0)
            continue
        menge, einheit = (d.get("netWgt"), "kg") if d.get("netWgt") else (d.get("qty"), d.get("qtyUnitAbbr")) if d.get("qty") else (None, None)
        zeilen.append({"quelle": "comtrade", "berichtsland": berichtsland, "partnerland": partner, "richtung": richtung,
                       "ware": cmd, "ware_text": d.get("cmdDesc"), "hs_ebene": None if cmd == "TOTAL" else str(len(cmd)),
                       "hs_version": d.get("classificationCode"), "zeitraum": zeitraum, "wert_usd": d["primaryValue"],
                       "menge": menge, "einheit": einheit, "waehrung": "USD",
                       "sonderfall": _sonder(partner, "geschaetzt" if d.get("isAggregate") is False and d.get("isReported") is False else None)})
    probleme = grundpruefung(zeilen) + summen_pruefen(zeilen, toleranz, extra=rest)
    return zeilen, probleme


# ---------------------------------------------------------------- Chinesischer Zoll (GACC)
def _gacc_tabelle(html: str, erwartet: str) -> tuple[pd.DataFrame, int, int, bool]:
    """Tabelle, Jahr, Monat und ob es ein kombinierter Zeitraum (Jan.–Feb.) ist."""
    try:
        tabellen = pd.read_html(io.StringIO(html))
    except (ValueError, ImportError) as f:
        raise StrukturFehler(f"GACC: keine Tabelle ({f})")
    if not tabellen:
        raise StrukturFehler("GACC: keine Tabelle")
    t = max(tabellen, key=lambda x: x.size).astype(str)
    titel = re.sub(r"\s+", " ", t.iloc[0, 0])  # ältere Seiten haben doppelte Leerzeichen
    if erwartet not in titel or "USD" not in titel:
        raise StrukturFehler(f"GACC: unerwarteter Titel „{titel[:80]}“")
    # "Oct 2026", "Oct. 2026", "October 2024", "January-February 2024" (ältere Jahre schreiben Monate aus)
    m = re.search(r",\s*([A-Za-z]{3,9})\.?(?:\s*-\s*([A-Za-z]{3,9})\.?)?\s+(\d{4})", titel)
    if not m:
        raise StrukturFehler(f"GACC: Zeitraum nicht im Titel „{titel[:80]}“")
    kurz = lambda w: w.lower()[:4] if w.lower().startswith("sept") else w.lower()[:3]
    von, bis, jahr = kurz(m.group(1)), kurz(m.group(2) or ""), int(m.group(3))
    if von not in MONATE or (bis and bis not in MONATE):
        raise StrukturFehler(f"GACC: Monat „{m.group(0)}“ unbekannt")
    monat = MONATE[bis or von]
    if "million" not in " ".join(t.iloc[1].tolist()).lower():
        raise StrukturFehler("GACC: Einheit ist nicht USD1 Million")
    return t, jahr, monat, bool(bis)


def _gacc_kombiniert(spalten_text: str, monat: int) -> bool:
    # Steht in der Monatsspalte "1-2", gibt es nur Januar und Februar zusammen
    return monat == 2 and bool(re.search(r"1\s*-\s*(to\s*-?\s*)?2", spalten_text))


def _januar(monat: int, kombi: bool, feb: float | None, kumuliert: float | None) -> float | None:
    """In der Februar-Tabelle steht Februar einzeln und Januar bis Februar zusammen: Januar = Differenz."""
    if monat != 2 or kombi or feb is None or kumuliert is None or kumuliert < feb:
        return None
    return kumuliert - feb


def gacc_laender(html: str) -> tuple[list[dict], list[str], list[str]]:
    t, jahr, monat, kombi = _gacc_tabelle(html, "by Country/Region")
    kopf = [i for i in range(len(t)) if t.iloc[i, 0].strip().startswith("Import Source")]
    if not kopf:
        raise StrukturFehler("GACC Länder: Kopfzeile „Import Source“ fehlt")
    k = kopf[0]
    oben = [s.strip() for s in t.iloc[k].tolist()]
    # Spalten über die Überschriften finden: normal Monat und Summe seit Januar je Richtung,
    # in den kombinierten Januar-Februar-Tabellen nur eine Spalte je Richtung
    sp_ei = [i for i, v in enumerate(oben) if v.startswith("Export & Import")]
    sp_ex = [i for i, v in enumerate(oben) if v == "Export"]
    sp_im = [i for i, v in enumerate(oben) if v == "Import"]
    if not (sp_ei and sp_ex and sp_im):
        raise StrukturFehler(f"GACC Länder: Spalten passen nicht ({oben[:7]})")
    kombi = kombi or _gacc_kombiniert(t.iloc[k + 1, sp_ei[0]], monat)
    i_ei, i_ex, i_im = sp_ei[0], sp_ex[0], sp_im[0]
    k_ex, k_im = (sp_ex[1], sp_im[1]) if len(sp_ex) > 1 and len(sp_im) > 1 else (None, None)
    zeitraum = f"{jahr}-{monat:02d}"
    sonder = "jan_feb_kombiniert" if kombi else None
    zeilen, unbekannt, probleme = [], [], []
    gesamt_da = False
    for i in range(k + 2, len(t)):
        name = t.iloc[i, 0].strip()
        if name.lower().startswith("note") or not name:
            break
        ei, ex, im = _zahl(t.iloc[i, i_ei]), _zahl(t.iloc[i, i_ex]), _zahl(t.iloc[i, i_im])
        if ex is None or im is None:
            continue
        if ei is not None and abs(ei - ex - im) > max(1.0, ei * 0.002):
            probleme.append(f"{name}: Export + Import ({ex + im:.1f}) ≠ Summe ({ei:.1f})")
        land = WELT if name.lower() == "total" else iso3(name)
        gesamt_da = gesamt_da or land == WELT
        if not land:
            unbekannt.append(name)  # Regionen (EU, ASEAN …) oder unbekannte Namen
            continue
        for richtung, wert, kum in (("export", ex, _zahl(t.iloc[i, k_ex]) if k_ex else None), ("import", im, _zahl(t.iloc[i, k_im]) if k_im else None)):
            basis = {"quelle": "gacc", "berichtsland": "CHN", "partnerland": land, "richtung": richtung, "ware": "TOTAL",
                     "ware_text": "Alle Waren", "waehrung": "USD"}
            zeilen.append({**basis, "zeitraum": zeitraum, "wert_usd": wert * 1e6, "sonderfall": _sonder(land, sonder)})
            jan = _januar(monat, kombi, wert, kum)
            if jan is not None:
                zeilen.append({**basis, "zeitraum": f"{jahr}-01", "wert_usd": jan * 1e6, "sonderfall": _sonder(land, "januar_berechnet")})
    if not gesamt_da:
        probleme.append("GACC Länder: Zeile „Total“ fehlt")
    return zeilen, grundpruefung(zeilen) + probleme, unbekannt


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60]


def gacc_waren(html: str, richtung: str) -> tuple[list[dict], list[str]]:
    t, jahr, monat, kombi = _gacc_tabelle(html, "Major Exports" if richtung == "export" else "Major Imports")
    kopf = [i for i in range(len(t)) if t.iloc[i, 0].strip() == "Commodity"]
    if len(kopf) < 2:
        raise StrukturFehler("GACC Waren: Kopfzeilen „Commodity“ fehlen")
    unten = [s.strip().lower() for s in t.iloc[kopf[1]].tolist()]
    if unten[2] != "quantity" or unten[3] != "value":
        raise StrukturFehler(f"GACC Waren: Spalten passen nicht ({unten[:4]})")
    kombi = kombi or _gacc_kombiniert(t.iloc[kopf[0], 2], monat)
    zeitraum = f"{jahr}-{monat:02d}"
    zeilen = []
    for i in range(kopf[1] + 1, len(t)):
        name = t.iloc[i, 0].strip()
        if name.lower().startswith(("note", "2.", "3.")) or not name:
            break
        wert = _zahl(t.iloc[i, 3])
        if wert is None:
            continue
        einheit = t.iloc[i, 1].strip()
        name = re.sub(r"\s+", " ", name.replace(" ", " ").replace("（", " (").replace("）", ")")).rstrip("*").strip()
        basis = {"quelle": "gacc", "berichtsland": "CHN", "partnerland": WELT, "richtung": richtung, "ware": f"cn:{_slug(name)}",
                 "ware_text": name, "einheit": None if einheit in ("-", "—", "nan") else einheit, "waehrung": "USD"}
        menge = _zahl(t.iloc[i, 2])
        zeilen.append({**basis, "zeitraum": zeitraum, "wert_usd": wert * 1e6, "menge": menge, "sonderfall": "jan_feb_kombiniert" if kombi else None})
        jan = _januar(monat, kombi, wert, _zahl(t.iloc[i, 5]))
        if jan is not None:
            mk = _zahl(t.iloc[i, 4])
            zeilen.append({**basis, "zeitraum": f"{jahr}-01", "wert_usd": jan * 1e6, "menge": (mk - menge) if mk is not None and menge is not None else None,
                           "sonderfall": "januar_berechnet"})
    if len(zeilen) < 10:
        raise StrukturFehler(f"GACC Waren: nur {len(zeilen)} Zeilen erkannt")
    return zeilen, grundpruefung(zeilen)


def gacc_verweise(html: str, jahr: int) -> dict:
    """Aus der Übersichtsseite: je Tabelle (1)–(6) in USD die Adressen je Monat. {"4": {"2026-10": url}}"""
    aus = {}
    for m in re.finditer(r"<tr>\s*<td>\s*\((\d+)\)([^<]*)</td>(.*?)</tr>", html, re.S):
        nr, titel, rest = m.group(1), m.group(2), m.group(3)
        if "USD" not in titel:
            continue
        for url, mon in re.findall(r"href=['\"]?([^'\" >]+)['\"]?\s*>\s*([A-Za-z]{3,4})\.?\s*</a>", rest):
            if mon.lower() in MONATE:
                aus.setdefault(nr, {})[f"{jahr}-{MONATE[mon.lower()]:02d}"] = url
    if not aus.get("4"):
        raise StrukturFehler("GACC: keine Verweise auf die Ländertabelle gefunden")
    return aus


# ---------------------------------------------------------------- TSMC-Monatsumsatz
def tsmc(html: str, jahr: int) -> tuple[list[dict], list[str]]:
    try:
        t = pd.read_html(io.StringIO(html))[0]
    except (ValueError, IndexError, ImportError) as f:
        raise StrukturFehler(f"TSMC: keine Tabelle ({f})")
    spalten = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns]
    if not any("Net Revenue" in s for s in spalten) or not any("Month" in s for s in spalten):
        raise StrukturFehler(f"TSMC: Spalten passen nicht ({spalten})")
    mon_i = next(i for i, s in enumerate(spalten) if "Month" in s)
    wert_i = next(i for i, s in enumerate(spalten) if "Net Revenue" in s)
    zeilen, summe, total = [], 0.0, None
    for _, r in t.iterrows():
        name = str(r.iloc[mon_i]).strip().rstrip(".").lower()
        wert = _zahl(r.iloc[wert_i])
        if name == "total":
            total = wert
            continue
        if name not in MONATE:
            continue
        if wert is None:
            continue
        summe += wert
        zeilen.append({"quelle": "tsmc", "berichtsland": "TWN", "partnerland": WELT, "richtung": "umsatz", "ware": "tsmc_umsatz",
                       "ware_text": "TSMC Konzernumsatz", "zeitraum": f"{jahr}-{MONATE[name]:02d}", "wert_orig": wert * 1e6, "waehrung": "TWD"})
    probleme = []
    if total and summe and abs(summe / total - 1) > 0.005:
        probleme.append(f"TSMC: Summe der Monate ({summe:.0f}) passt nicht zur Jahressumme ({total:.0f})")
    if not zeilen:
        raise StrukturFehler("TSMC: keine Monatswerte")
    return zeilen, grundpruefung(zeilen) + probleme


# ---------------------------------------------------------------- IMF PortWatch
def portwatch(seiten: list[dict], engstellen: dict) -> tuple[list[dict], list[str]]:
    """Tägliche Durchfahrten je Meerenge -> Wochenschnitt (nur vollständige Wochen, Montag als Zeitraum)."""
    eintraege = []
    for s in seiten:
        if "features" not in s:
            raise StrukturFehler("PortWatch: Feld features fehlt")
        eintraege += [f["attributes"] for f in s["features"]]
    if not eintraege:
        return [], []
    tab = pd.DataFrame(eintraege)
    for f in ("date", "portid", "n_total", "n_tanker", "n_container"):
        if f not in tab.columns:
            raise StrukturFehler(f"PortWatch: Spalte {f} fehlt")
    tab["date"] = pd.to_datetime(tab["date"], unit="ms" if pd.api.types.is_numeric_dtype(tab["date"]) else None)
    tab = tab[tab["portid"].isin(engstellen)].drop_duplicates(["portid", "date"], keep="last")
    tab["woche"] = (tab["date"] - pd.to_timedelta(tab["date"].dt.weekday, unit="D")).dt.date
    zeilen = []
    for (pid, woche), g in tab.groupby(["portid", "woche"]):
        if len(g) < 7:
            continue
        for ware, feld in (("alle", "n_total"), ("tanker", "n_tanker"), ("container", "n_container")):
            zeilen.append({"quelle": "portwatch", "richtung": "durchfahrt", "ware": ware, "ware_text": {"alle": "Alle Schiffe", "tanker": "Tanker", "container": "Containerschiffe"}[ware],
                           "zeitraum": woche.isoformat(), "menge": round(float(g[feld].mean()), 2), "einheit": "Schiffe je Tag", "ort": pid})
    probleme = [f"PortWatch: negativer Wert bei {z['ort']}" for z in zeilen if z["menge"] < 0][:1]
    return zeilen, probleme


def json_laden(inhalt: bytes):
    try:
        return json.loads(inhalt)
    except ValueError as f:
        raise StrukturFehler(f"keine gültige JSON-Antwort ({f})")
