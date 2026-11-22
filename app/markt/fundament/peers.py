"""Peer-Vergleich (Punkt 24c, Teil D).

Vergleichsgruppen in daten/markt/peers.yaml (bearbeitbar, auch im Tool). Vorschlag für einen Wert:
FMP-Peer-Liste (gratis, nur US) plus Werte derselben Yahoo-Branche aus S&P 500, Nasdaq 100, DAX und
Euro Stoxx 50, jeweils mit ähnlicher Größe (Börsenwert zwischen einem Achtel und dem Achtfachen).
Die Themen aus daten/markt/themen.yaml sind direkt als Gruppen nutzbar.
"""
import json
import math
import re
import urllib.request
from datetime import date

import numpy as np
import pandas as pd
import yaml
import yfinance as yf

from ...einstellungen import key
from ...konfig import MARKT_DATEN
from ..abruf import STUNDE, TAG, gecacht
from . import basis as B
from .ansichten import viele
from .kennzahlen import bibliothek

DATEI = MARKT_DATEN / "peers.yaml"
STANDARD = ["fwd_kgv", "peg", "ev_ebitda", "wachstum_2j", "operative_marge", "roic", "fcf_marge", "netto_ebitda", "schaetzung_3m"]
# Radar: Dimension -> Kennzahlen (Rang innerhalb der Gruppe, 100 = bester Wert)
RADAR = {"Wachstum": ["wachstum_2j", "umsatz_wachstum"], "Profitabilität": ["operative_marge", "roic"], "Bewertung": ["fwd_kgv", "ev_ebitda"],
         "Bilanz": ["netto_ebitda", "zinsdeckung"], "Momentum": ["perf_6m", "schaetzung_3m"]}


class PeerFehler(ValueError):
    pass


def _lesen() -> dict:
    try:
        roh = yaml.safe_load(DATEI.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except yaml.YAMLError:
        raise PeerFehler("peers.yaml ist nicht lesbar.")
    return {str(k): {"name": str(v.get("name") or k), "werte": [str(t).upper() for t in v.get("werte") or []], "quelle": v.get("quelle", "eigen"),
                     "fuer": v.get("fuer")} for k, v in (roh.get("gruppen") or {}).items() if isinstance(v, dict)}


def _schreiben(gruppen: dict) -> None:
    DATEI.parent.mkdir(parents=True, exist_ok=True)
    kopf = "# Vergleichsgruppen für die Fundamentalanalyse. quelle: auto = automatisch vorgeschlagen (darf geändert werden), eigen = selbst angelegt.\n"
    DATEI.write_text(kopf + yaml.safe_dump({"gruppen": gruppen}, allow_unicode=True, sort_keys=True), encoding="utf-8")


def _fmp(ticker: str) -> list[dict]:
    k = key("FMP_API_KEY")
    if not k or "." in ticker:
        return []
    def holen():
        with urllib.request.urlopen(f"https://financialmodelingprep.com/stable/stock-peers?symbol={ticker}&apikey={k}", timeout=20) as r:
            return json.loads(r.read())
    try:
        d = gecacht(f"fund:fmp_peers:{ticker}", 7 * TAG, holen)
        return [{"ticker": x["symbol"], "cap": x.get("mktCap")} for x in d if isinstance(x, dict) and x.get("symbol")]
    except Exception:  # noqa: BLE001
        return []


def vorschlag(ticker: str, anzahl: int = 10) -> list[str]:
    from ..indizes import kennzahlen
    ticker = ticker.upper()
    roh = B.rohdaten(ticker)
    m = roh["markt"]
    cap = B._z(m.get("marketCap")) or 0
    branche = m.get("industry")
    kandidaten = {}
    for x in _fmp(ticker):
        kandidaten[x["ticker"]] = (0, x.get("cap"))
    for index in ("sp500", "ndx", "dax", "sx5e"):
        try:
            for t, w in kennzahlen(index).items():
                if branche and w.get("branche") == branche and t != ticker:
                    kandidaten.setdefault(t, (1, w.get("cap")))
        except Exception:  # noqa: BLE001
            continue
    def passt(c):
        return not cap or not c or 1 / 8 <= c / cap <= 8
    sortiert = sorted((t for t, (q, c) in kandidaten.items() if passt(c) and t != ticker),
                      key=lambda t: (kandidaten[t][0], abs(math.log((kandidaten[t][1] or cap or 1) / (cap or 1)))))
    return sortiert[:anzahl]


def gruppen() -> list[dict]:
    from ..themen import lade_themen
    aus = [{"id": k, **v} for k, v in sorted(_lesen().items(), key=lambda x: x[1]["name"].lower())]
    try:
        for t in lade_themen():
            aus.append({"id": f"thema:{t['name']}", "name": f"Thema: {t['name']}", "werte": [w["ticker"] if isinstance(w, dict) else str(w) for w in t.get("werte") or t.get("aktien") or []],
                        "quelle": "thema"})
    except Exception:  # noqa: BLE001
        pass
    return aus


def gruppe_fuer(ticker: str) -> dict:
    """Die Vergleichsgruppe eines Werts; gibt es keine, wird ein Vorschlag angelegt und gespeichert."""
    ticker = ticker.upper()
    g = _lesen()
    for k, v in g.items():
        if v.get("fuer") == ticker:
            return {"id": k, **v}
    werte = [ticker] + vorschlag(ticker)
    k = f"auto_{re.sub(r'[^A-Z0-9]+', '_', ticker).lower()}"
    g[k] = {"name": f"Peers von {ticker}", "werte": werte, "quelle": "auto", "fuer": ticker}
    _schreiben(g)
    return {"id": k, **g[k]}


def speichern(gid: str | None, name: str, werte: list, fuer: str | None = None) -> dict:
    name = str(name or "").strip()[:60]
    werte = [str(t).strip().upper() for t in werte if re.fullmatch(r"[A-Za-z0-9.\-^=]{1,15}", str(t).strip())][:40]
    if not name or len(werte) < 2:
        raise PeerFehler("Bitte einen Namen und mindestens zwei Werte angeben.")
    g = _lesen()
    if not gid or gid.startswith("thema:"):
        gid = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "gruppe"
        while gid in g:
            gid += "_2"
    alt = g.get(gid, {})
    g[gid] = {"name": name, "werte": list(dict.fromkeys(werte)), "quelle": "eigen" if alt.get("quelle") != "auto" else "auto", "fuer": fuer or alt.get("fuer")}
    _schreiben(g)
    return {"id": gid, **g[gid]}


def loeschen(gid: str) -> None:
    g = _lesen()
    if gid in g:
        del g[gid]
        _schreiben(g)


def _perf(tickers: list[str], tage: int) -> dict:
    """Kursentwicklung in Prozent über die letzten Tage, für das Momentum im Radar."""
    try:
        d = kurse(tickers, "1y")
    except Exception:  # noqa: BLE001
        return {}
    aus = {}
    for t, reihe in d.items():
        s = pd.Series(reihe["werte"], index=pd.to_datetime(reihe["daten"])).dropna()
        alt = s[s.index <= s.index[-1] - pd.Timedelta(days=tage)] if len(s) else s
        aus[t] = (float(s.iloc[-1]) / float(alt.iloc[-1]) - 1) * 100 if len(alt) else None
    return aus


def kurse(tickers: list[str], zeitraum: str = "1y") -> dict:
    """Tagesschlusskurse je Wert (12 Stunden zwischengespeichert)."""
    tickers = sorted(set(tickers))
    def holen():
        roh = yf.download(tickers, period=zeitraum, auto_adjust=True, progress=False, threads=True, group_by="column")["Close"]
        if isinstance(roh, pd.Series):
            roh = roh.to_frame(tickers[0])
        return {t: {"daten": [d.strftime("%Y-%m-%d") for d in roh[t].dropna().index], "werte": [round(float(v), 4) for v in roh[t].dropna()]}
                for t in roh.columns if roh[t].notna().any()}
    return gecacht(f"fund:kurse_tag:{zeitraum}:{','.join(tickers)}", 12 * STUNDE, holen)


def vergleich(tickers: list[str], kennzahlen: list[str] | None = None, fokus: str | None = None, waehrung: str = "USD") -> dict:
    tickers = list(dict.fromkeys(t.upper() for t in tickers))[:30]
    kennzahlen = kennzahlen or STANDARD
    bib = {k["id"]: k for k in bibliothek()["kennzahlen"]}
    daten = viele(tickers, art="ttm", waehrung=waehrung)
    perf6 = _perf(tickers, 182)
    zeilen, fehlt = [], []
    for r in daten:
        if r.get("fehler"):
            fehlt.append(r["ticker"]); continue
        werte = {k["id"]: k["heute"] for k in r["kennzahlen"]}
        # Umsatzwachstum der letzten 12 Monate zum Vorjahr (für Radar und Tabelle)
        um = next((k for k in r["kennzahlen"] if k["id"] == "umsatz"), None)
        werte["umsatz_wachstum"], basis = None, None
        # letzter vollständiger 12-Monats-Wert (kurz nach dem Termin fehlt bei Yahoo der Umsatz des neuesten Quartals)
        reihe = um["werte"] if um else []
        i = next((i for i in range(len(reihe) - 1, 3, -1) if reihe[i] and reihe[i - 4] and reihe[i - 4] > 0), None)
        if i is not None and i >= len(reihe) - 2:
            werte["umsatz_wachstum"], basis = (reihe[i] / reihe[i - 4] - 1) * 100, "12 Monate"
        else:
            # Ersatz für Firmen mit kurzer Quartalshistorie (meist außerhalb der USA), siehe kennzahlen.umsatz_wachstum
            ersatz = r.get("umsatz_wachstum") or {}
            werte["umsatz_wachstum"], basis = ersatz.get("wert"), ersatz.get("basis")
        werte["perf_6m"] = perf6.get(r["ticker"])
        zeilen.append({"ticker": r["ticker"], "name": r["name"], "sektor": r["sektor"], "boersenwert": r["markt"].get("marketCap"), "werte": werte,
                       "hinweise": {"umsatz_wachstum": basis} if basis and basis != "12 Monate" else {},
                       "gruende": {k["id"]: k["grund_heute"] for k in r["kennzahlen"]}})
    spalten = []
    for kid in kennzahlen:
        kz = bib.get(kid) or {"id": kid, "name": {"umsatz_wachstum": "Umsatzwachstum (12 M.)", "perf_6m": "Kurs 6 Monate"}.get(kid, kid),
                              "einheit": "prozent" if kid in ("umsatz_wachstum", "perf_6m") else "zahl", "hoeher_besser": True}
        werte = [z["werte"].get(kid) for z in zeilen]
        gueltig = [w for w in werte if w is not None]
        median = float(np.median(gueltig)) if gueltig else None
        hb = kz.get("hoeher_besser")
        bester = schlechtester = None
        if len(gueltig) >= 2 and hb is not None:
            bester = max(gueltig) if hb else min(gueltig)
            schlechtester = min(gueltig) if hb else max(gueltig)
        spalten.append({"id": kid, "name": kz["name"], "einheit": kz["einheit"], "hoeher_besser": hb, "erklaerung": kz.get("erklaerung", ""),
                        "median": median, "bester": bester, "schlechtester": schlechtester})
    fokus = (fokus or tickers[0]).upper()
    f = next((z for z in zeilen if z["ticker"] == fokus), None)
    praemien = []
    if f:
        for s in spalten:
            v, med = f["werte"].get(s["id"]), s["median"]
            if v is None or med is None or med == 0:
                continue
            abw = (v / med - 1) * 100 if med > 0 and v > 0 else None
            praemien.append({"id": s["id"], "name": s["name"], "wert": v, "median": med, "abweichung": abw, "differenz": v - med, "einheit": s["einheit"],
                             "hoeher_besser": s["hoeher_besser"]})
    satz = _satz(f, spalten) if f else None
    return {"zeilen": zeilen, "spalten": spalten, "fokus": fokus, "praemien": praemien, "satz": satz, "ohne_daten": fehlt,
            "regression": _regression(zeilen), "radar": _radar(zeilen), "waehrung": waehrung}


def _satz(f: dict, spalten: list[dict]) -> str | None:
    """z. B. "KGV 18 % über Peer-Median bei 40 % mehr Wachstum"."""
    med = {s["id"]: s["median"] for s in spalten}
    kgv, wachs = f["werte"].get("fwd_kgv"), f["werte"].get("wachstum_2j")
    if kgv is None or not med.get("fwd_kgv"):
        return None
    k = (kgv / med["fwd_kgv"] - 1) * 100
    teil = f"Forward-KGV {abs(k):.0f} % {'über' if k >= 0 else 'unter'} dem Peer-Median".replace(".", ",")
    if wachs is not None and med.get("wachstum_2j"):
        diff = wachs - med["wachstum_2j"]
        teil += f" bei {abs(diff):.1f} Prozentpunkten {'mehr' if diff >= 0 else 'weniger'} erwartetem Gewinnwachstum pro Jahr".replace(".", ",")
    return teil + "."


def _regression(zeilen: list[dict]) -> dict | None:
    """Wachstum (x) gegen Forward-KGV (y): Gerade durch die Gruppe; Abstand je Wert in KGV-Punkten."""
    p = [(z["ticker"], z["werte"].get("wachstum_2j"), z["werte"].get("fwd_kgv")) for z in zeilen]
    p = [(t, x, y) for t, x, y in p if x is not None and y is not None and -50 < x < 300 and 0 < y < 200]
    if len(p) < 4:
        return None
    xs, ys = np.array([x for _, x, _ in p]), np.array([y for _, _, y in p])
    a, b = np.polyfit(xs, ys, 1)
    rest = {t: float(y - (a * x + b)) for t, x, y in p}
    r2 = float(np.corrcoef(xs, ys)[0, 1] ** 2) if len(p) > 2 else None
    return {"steigung": float(a), "achse": float(b), "r2": r2, "abstand": rest}


def _radar(zeilen: list[dict]) -> dict:
    """Je Dimension der Rang (0 bis 100) innerhalb der Gruppe; Bewertung und Verschuldung: niedriger = besser."""
    niedrig_besser = {"fwd_kgv", "ev_ebitda", "netto_ebitda"}
    raenge = {}
    for dim, ids in RADAR.items():
        teil = {}
        for kid in ids:
            werte = [(z["ticker"], z["werte"].get(kid)) for z in zeilen if z["werte"].get(kid) is not None]
            if len(werte) < 3:
                continue
            sortiert = sorted(werte, key=lambda x: x[1], reverse=kid not in niedrig_besser)
            n = len(sortiert)
            for i, (t, _) in enumerate(sortiert):
                teil.setdefault(t, []).append(100 * (n - 1 - i) / (n - 1))
        for t, r in teil.items():
            raenge.setdefault(t, {})[dim] = float(np.mean(r))
    return {"dimensionen": list(RADAR), "werte": raenge}


def normiert(tickers: list[str], zeitraum: str = "1y") -> dict:
    d = kurse(tickers, zeitraum)
    aus = []
    for t, r in d.items():
        if not r["werte"]:
            continue
        s = r["werte"][0]
        aus.append({"ticker": t, "daten": r["daten"], "werte": [round(v / s * 100, 2) for v in r["werte"]]})
    return {"reihen": aus, "zeitraum": zeitraum}


def kachel(ticker: str, waehrung: str = "USD") -> dict:
    """'Im Vergleich zu Peers': die 3 größten Abweichungen vom Median der Gruppe."""
    g = gruppe_fuer(ticker)
    v = vergleich(g["werte"], STANDARD + ["umsatz_wachstum"], ticker, waehrung)
    kandidaten = []
    for p in v["praemien"]:
        if p["einheit"] == "prozent":
            staerke = abs(p["differenz"]) / 10  # 10 Prozentpunkte zählen wie 100 % Abweichung eines Faktors
        elif p["abweichung"] is not None:
            staerke = abs(p["abweichung"]) / 100
        else:
            continue
        kandidaten.append((staerke, p))
    kandidaten.sort(key=lambda x: -x[0])
    return {"gruppe": {"id": g["id"], "name": g["name"], "werte": g["werte"]}, "satz": v["satz"], "top": [p for _, p in kandidaten[:3]],
            "anzahl": len(v["zeilen"])}
