"""Ähnliche Werte zu einem Bezug (Arbeitsplätze im Dashboard): wer gehört dazu, wie sind sie gelaufen, wie
eng laufen sie mit, und wie haben sie zuletzt auf ihre eigenen Quartalszahlen reagiert.

Ähnlich heißt, der Reihe nach: eigene Peer-Gruppe aus peers.yaml, die das Papier enthält; Thema aus
themen.yaml; Vorschlag aus Branche und Größe (fundament/peers.py, wie im Peer-Vergleich). Für Indizes,
Futures, Devisen und Krypto feste Gruppen (FEST). Für eine Marktrichtung die größten Mitglieder bzw. die
anderen Richtungen derselben Gruppe. Nur Marktdaten; welche Werte im Depot liegen, markiert die Oberfläche.
"""

import numpy as np
import pandas as pd

from .abruf import gecacht, schlusskurse

TAG = 86400
HOECHSTENS = 8
KORB = "Ø Korb"
ZEITRAEUME = {"1m": ("3mo", 21), "3m": ("6mo", 63), "6m": ("1y", 126), "1j": ("2y", 252)}
FENSTER_VOR, FENSTER_NACH = 5, 20   # Verlauf um die Quartalszahlen: Handelstage davor und danach
FEST = [
    ["SPY", "QQQ", "IWM", "DIA", "^GDAXI", "^STOXX50E", "^N225", "^HSI"],
    ["^GSPC", "^NDX", "^RUT", "^DJI", "^GDAXI", "^STOXX50E", "^N225", "^HSI"],
    ["ES=F", "NQ=F", "RTY=F", "YM=F", "FDAX=F"],
    ["CL=F", "BZ=F", "NG=F", "RB=F", "HO=F"],
    ["GC=F", "SI=F", "HG=F", "PL=F", "PA=F"],
    ["ZN=F", "ZT=F", "ZB=F", "^TNX", "^FVX", "^TYX"],
    ["EURUSD=X", "DX-Y.NYB", "USDJPY=X", "GBPUSD=X", "USDCNH=X", "AUDUSD=X"],
    ["BTC-USD", "ETH-USD", "SOL-USD", "IBIT", "MSTR", "COIN"],
]


def _peers_gruppe(ticker: str) -> tuple[list[str], str] | None:
    from .fundament.peers import _lesen
    try:
        gruppen = _lesen()
    except Exception:  # noqa: BLE001
        return None
    for g in sorted(gruppen.values(), key=lambda g: g.get("fuer") != ticker):   # zuerst die Gruppe "für" diesen Wert
        if ticker in g["werte"]:
            return [t for t in g["werte"] if t != ticker], f"Peer-Gruppe „{g['name']}“"
    return None


def _thema(ticker: str) -> tuple[list[str], str] | None:
    try:
        from .themen import lade_themen
        for t in lade_themen():
            if ticker in t["werte"]:
                return [w for w in t["werte"] if w != ticker], f"Thema „{t['name']}“"
    except Exception:  # noqa: BLE001
        pass
    return None


def werte_fuer(bezug: dict) -> dict:
    """{werte: [Ticker], quelle: Text} zu einem aufgelösten Bezug (app/markt/bezug.py)."""
    if bezug["art"] == "richtung":
        if bezug.get("gruppe") == "Faktoren":
            return {"werte": bezug["mitglieder"], "quelle": "beide Seiten des Faktors"}
        if bezug.get("mitglieder"):
            return {"werte": bezug["mitglieder"][:HOECHSTENS], "quelle": "größte Werte" if bezug.get("gruppe") != "Themen" else "Werte des Themas"}
        from .bezug import richtungen
        gleiche = [r["id"] for r in richtungen() if r["gruppe"] == bezug.get("gruppe") and r["id"] != bezug["id"]]
        from .bezug import richtung
        tick = [t for t in (richtung(r).get("ticker") for r in gleiche) if t]
        return {"werte": tick[:HOECHSTENS], "quelle": f"andere {bezug.get('gruppe', 'Richtungen')}"}
    t = bezug["ticker"]
    for gruppe in FEST:
        if t in gruppe:
            return {"werte": [x for x in gruppe if x != t][:HOECHSTENS], "quelle": "feste Vergleichsgruppe"}
    for finden in (_peers_gruppe, _thema):
        r = finden(t)
        if r and r[0]:
            return {"werte": r[0][:HOECHSTENS], "quelle": r[1]}
    try:
        from .fundament.peers import vorschlag
        v = vorschlag(t, HOECHSTENS)
        if v:
            return {"werte": v, "quelle": "gleiche Branche, ähnliche Größe"}
    except Exception:  # noqa: BLE001
        pass
    return {"werte": [], "quelle": "keine ähnlichen Werte gefunden"}


def ausrichten(reihen: dict[str, pd.Series], basis: str | None, tage: int) -> pd.DataFrame:
    """Alle Reihen auf gemeinsame Tage (fehlende Tage mit dem Vortag gefüllt, z. B. andere Börsenfeiertage),
    die letzten `tage` Handelstage, Start = 100. Reine Funktion."""
    df = pd.DataFrame(reihen).sort_index().ffill()
    if basis and basis in df:
        df = df[df[basis].notna()]
    df = df.iloc[-(tage + 1):].dropna(axis=1, how="any")
    if df.empty:
        return df
    return df / df.iloc[0] * 100


def gleichlauf(reihen: dict[str, pd.Series], basis: str) -> dict:
    """Korrelation und Beta der Tagesrenditen jedes Werts mit dem Basiswert, letztes Jahr. Reine Funktion."""
    df = pd.DataFrame(reihen).sort_index().ffill().iloc[-253:]
    r = df.pct_change().iloc[1:]
    if basis not in r:
        return {}
    b = r[basis]
    aus = {}
    for t in r.columns:
        if t == basis:
            continue
        paar = pd.concat([b, r[t]], axis=1).dropna()
        if len(paar) < 60 or paar.iloc[:, 0].std() == 0:
            continue
        k = float(paar.iloc[:, 0].corr(paar.iloc[:, 1]))
        beta = float(np.cov(paar.iloc[:, 1], paar.iloc[:, 0])[0, 1] / paar.iloc[:, 0].var())
        aus[t] = {"korrelation": round(k, 2), "beta": round(beta, 2), "n": len(paar)}
    return aus


def um_termin(schluss: pd.Series, datum: str, sitzung: str | None) -> list[float] | None:
    """Kurs von 5 Handelstagen vor bis 20 nach dem Termin in Prozent zum Schluss vor der Reaktion. Reine Funktion.
    Nach Börsenschluss (oder unbekannt) reagiert der Kurs am nächsten Handelstag."""
    s = schluss.dropna()
    s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
    tag = pd.Timestamp(datum)
    vor_boerse = sitzung == "vor Börse"
    # Reaktionstag: der Termin selbst (vor Börse) bzw. der erste Handelstag danach
    pos = s.index.searchsorted(tag, side="left" if vor_boerse else "right")
    if pos < 1 or pos >= len(s):
        return None
    basis = float(s.iloc[pos - 1])
    von, bis = pos - 1 - FENSTER_VOR, pos - 1 + FENSTER_NACH
    if von < 0:
        return None
    teil = s.iloc[von:min(bis, len(s) - 1) + 1]
    return [round((float(v) / basis - 1) * 100, 2) for v in teil]


def rechnen(bezug: dict, zeitraum: str = "3m") -> dict:
    periode, tage = ZEITRAEUME.get(zeitraum, ZEITRAEUME["3m"])
    w = werte_fuer(bezug)
    basis = bezug.get("ticker")
    alle = ([basis] if basis else []) + [t for t in w["werte"] if t != basis]

    def holen():
        from .namen import name
        reihen = {}
        for t in alle:
            try:
                s = schlusskurse(t, "2y")
                s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
                reihen[t] = s
            except Exception:  # noqa: BLE001
                continue
        return {"reihen": {t: {"daten": [d.strftime("%Y-%m-%d") for d in s.index], "werte": [round(float(v), 4) for v in s]} for t, s in reihen.items()},
                "namen": {t: name(t) for t in reihen}}
    roh = gecacht(f"aehnlich:kurse:{','.join(alle)}", 6 * 3600, holen)
    reihen = {t: pd.Series(r["werte"], index=pd.to_datetime(r["daten"])) for t, r in roh["reihen"].items()}
    if not basis and len(reihen) >= 2:
        # Korb ohne eigenen Kurs (Thema): der gleichgewichtete Korb ist die Bezugslinie
        from .richtung import korb
        basis = KORB
        reihen = {KORB: korb(pd.DataFrame(reihen), 600), **reihen}
        roh["namen"][KORB] = "Korb gleichgewichtet"
        alle = [KORB] + alle
    df = ausrichten(reihen, basis, tage)
    verlauf = {"daten": [d.strftime("%Y-%m-%d") for d in df.index], "reihen": {t: [round(float(v), 2) for v in df[t]] for t in df.columns}}
    lauf = gleichlauf(reihen, basis) if basis else {}
    werte = []
    for t in alle:
        if t not in df:
            continue
        werte.append({"ticker": t, "name": roh["namen"].get(t, t), "basis": t == basis, "veraenderung": round(float(df[t].iloc[-1] - 100), 2), **lauf.get(t, {})})
    return {"bezug": bezug["id"], "quelle": w["quelle"], "zeitraum": zeitraum, "werte": werte, "verlauf": verlauf}


def zahlen(bezug: dict, tage_zurueck: int = 100) -> dict:
    """Wie haben die ähnlichen Werte (und der Wert selbst) auf ihre letzten Quartalszahlen reagiert?
    Nur Aktien; je Wert der letzte Termin der letzten `tage_zurueck` Tage."""
    from .earnings_moves import chart_termine
    w = werte_fuer(bezug)
    basis = bezug.get("ticker") if bezug["art"] == "wert" else None
    alle = ([basis] if basis else []) + [t for t in w["werte"] if t != basis]
    grenze = (pd.Timestamp.now() - pd.Timedelta(days=tage_zurueck)).strftime("%Y-%m-%d")

    def holen():
        aus = []
        for t in alle:
            try:
                termine = [x for x in chart_termine(t).get("termine", []) if not x.get("zukunft") and x["datum"] >= grenze]
                if not termine:
                    continue
                x = termine[-1]
                pfad = um_termin(schlusskurse(t, "1y"), x["datum"], x.get("sitzung"))
                if pfad:
                    aus.append({"ticker": t, "basis": t == basis, "datum": x["datum"], "sitzung": x.get("sitzung"), "reaktion": x.get("reaktion"),
                                "ueberraschung": x.get("ueberraschung"), "pfad": pfad})
            except Exception:  # noqa: BLE001
                continue
        return aus
    liste = gecacht(f"aehnlich:zahlen:{','.join(alle)}", 6 * 3600, holen)
    naechste = None
    if basis:
        try:
            naechste = next((x["datum"] for x in chart_termine(basis).get("termine", []) if x.get("zukunft")), None)
        except Exception:  # noqa: BLE001
            pass
    mittel = None
    pfade = [x["pfad"] for x in liste if not x["basis"] and len(x["pfad"]) == FENSTER_VOR + FENSTER_NACH + 1]
    if len(pfade) >= 2:
        mittel = [round(float(v), 2) for v in np.mean(pfade, axis=0)]
    return {"bezug": bezug["id"], "quelle": w["quelle"], "vor": FENSTER_VOR, "nach": FENSTER_NACH, "werte": liste, "mittel": mittel,
            "naechster_termin": naechste, "fehlen": [t for t in alle if t not in {x["ticker"] for x in liste}]}
