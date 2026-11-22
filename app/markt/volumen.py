"""Preis-Volumen-Analyse: Bei welchen Kursen wurde wie viel gehandelt?

Volumenprofil: Das Volumen jeder Kerze wird gleichmäßig auf die Preisstufen zwischen ihrem Tief
und Hoch verteilt. Daraus ergeben sich
- POC (Point of Control): die Preisstufe mit dem meisten Volumen,
- Value Area: der Bereich um den POC, in dem 70 % des Volumens liegen,
- Volumen-Lücken: Preisbereiche innerhalb der Spanne, in denen kaum gehandelt wurde.
Normalvolumen einer Kerze: Median derselben Uhrzeit an den bis zu 20 Handelstagen davor
(morgens und abends wird immer mehr gehandelt als mittags, das gleicht die Uhrzeit aus).
Kauf/Verkauf ist nur eine Schätzung aus der Kerzenrichtung: steigende Kerze = Kaufvolumen.

Alle Kurse in Handelswährung; die Umrechnung in Euro macht der Aufrufer über `faktor`.
"""
import math
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

from ..cache import STUNDE, markt_lesen as cache_lesen, markt_schreiben as cache_schreiben
from .boersen import boerse_von, ist_offen

STUFEN = 72            # Preisstufen im Profil
VALUE_AREA = 0.70
AUSREISSER = 3.0       # ab 3-fachem Normalvolumen
RVOL_HOCH = 2.0        # relatives Volumen ab 2-fach hervorheben
LUECKE = 0.25          # Lücke: weniger als ein Viertel des Durchschnitts je Stufe
MAX_KERZEN = 360       # mehr Kerzen passen nicht sinnvoll in den Chart, dann werden sie zusammengefasst
FRISCH = {"1m": 120, "5m": 300, "1h": 900, "1d": STUNDE}

# Zeitraum -> (Intervall, Abrufzeitraum bei Yahoo, Anzahl Handelstage oder Kalendertage)
ZEITRAEUME = {
    "heute": ("1m", "7d", 1),
    "5t": ("1m", "7d", 5),
    "1m": ("5m", "60d", 31),
    "3m": ("1h", "1y", 92),
}


def _hole(ticker: str, intervall: str, periode: str | None = None, start: date | None = None) -> pd.DataFrame:
    """Kerzen von Yahoo, im Zwischenspeicher je nach Intervall und Handelszeit 2 Minuten bis 1 Stunde."""
    schluessel = f"volumen:{ticker}:{intervall}:{periode or start}"
    alt, alter = cache_lesen(schluessel)
    offen = ist_offen(boerse_von(ticker))
    if alt and alter < (FRISCH[intervall] if offen else STUNDE):
        return _tabelle(alt)
    try:
        roh = yf.Ticker(ticker).history(period=periode, start=start, interval=intervall, auto_adjust=False, prepost=False)
        roh = roh[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Open", "High", "Low", "Close"])
        if roh.empty:
            raise ValueError("keine Kerzen")
    except Exception:
        if alt:
            return _tabelle(alt)
        raise
    roh = roh[~roh.index.duplicated(keep="last")]
    daten = {"tz": str(roh.index.tz), "t": [t.isoformat() for t in roh.index],
             **{k: [float(x) for x in roh[k]] for k in ("Open", "High", "Low", "Close", "Volume")}}
    cache_schreiben(schluessel, daten)
    return _tabelle(daten)


def _tabelle(daten: dict) -> pd.DataFrame:
    index = pd.DatetimeIndex(pd.to_datetime(daten["t"], utc=True)).tz_convert(daten["tz"])
    return pd.DataFrame({k: daten[k] for k in ("Open", "High", "Low", "Close", "Volume")}, index=index)


def _normalvolumen(df: pd.DataFrame, intervall: str) -> pd.Series:
    """Übliches Volumen je Kerze: Median derselben Uhrzeit an den bis zu 20 Handelstagen davor."""
    v = df["Volume"]
    if intervall == "1d":
        return v.shift(1).rolling(20, min_periods=5).median()
    tag = pd.Index(df.index.date, name="tag")
    slot = pd.Index(df.index.strftime("%H:%M"), name="slot")
    tabelle = v.groupby([tag, slot]).sum().unstack()
    median = tabelle.shift(1).rolling(20, min_periods=3).median()
    gestapelt = median.stack()
    normal = pd.Series([gestapelt.get((t, s), np.nan) for t, s in zip(tag, slot)], index=df.index)
    # Zu wenige Vortage (z. B. ganz am Anfang): Median der 60 Kerzen davor
    ersatz = v.shift(1).rolling(60, min_periods=10).median()
    return normal.fillna(ersatz)


def _profil(df: pd.DataFrame) -> dict:
    tief, hoch = float(df["Low"].min()), float(df["High"].max())
    if not hoch > tief:
        hoch = tief * 1.001 + 1e-9
    breite = (hoch - tief) / STUFEN
    lo = np.clip(((df["Low"].to_numpy() - tief) / breite).astype(int), 0, STUFEN - 1)
    hi = np.clip(((df["High"].to_numpy() - tief) / breite).astype(int), 0, STUFEN - 1)
    vol = df["Volume"].to_numpy()
    anteil = vol / (hi - lo + 1)
    richtung = np.sign(df["Close"].to_numpy() - df["Open"].to_numpy())
    kauf_anteil = np.where(richtung > 0, 1.0, np.where(richtung < 0, 0.0, 0.5))

    def verteilen(gewicht: np.ndarray) -> np.ndarray:
        diff = np.zeros(STUFEN + 1)
        np.add.at(diff, lo, gewicht)
        np.add.at(diff, hi + 1, -gewicht)
        return np.cumsum(diff)[:STUFEN]

    gesamt = verteilen(anteil)
    kauf = verteilen(anteil * kauf_anteil)
    summe = float(gesamt.sum())

    poc = int(np.argmax(gesamt))
    # Value Area: vom POC aus jeweils die volumenstärkere Nachbarstufe dazunehmen, bis 70 % erreicht sind.
    unten = oben = poc
    drin = gesamt[poc]
    while drin < VALUE_AREA * summe and (unten > 0 or oben < STUFEN - 1):
        runter = gesamt[unten - 1] if unten > 0 else -1
        rauf = gesamt[oben + 1] if oben < STUFEN - 1 else -1
        if rauf >= runter:
            oben += 1
            drin += rauf
        else:
            unten -= 1
            drin += runter

    gehandelt = np.nonzero(gesamt > 0)[0]
    schnitt = float(gesamt[gehandelt[0]:gehandelt[-1] + 1].mean()) if len(gehandelt) else 0.0
    luecken, start = [], None
    for i in range(STUFEN + 1):
        leer = i < STUFEN and 0 < i < STUFEN - 1 and gesamt[i] < LUECKE * schnitt
        if leer and start is None:
            start = i
        elif not leer and start is not None:
            if i - start >= 2:  # mindestens zwei Stufen breit, sonst ist es Rauschen
                luecken.append({"von": tief + start * breite, "bis": tief + i * breite})
            start = None

    return {
        "tief": tief, "hoch": hoch, "breite": breite,
        "stufen": [{"v": float(g), "kauf": float(k)} for g, k in zip(gesamt, kauf)],
        "poc": tief + (poc + 0.5) * breite,
        "va_unten": tief + unten * breite, "va_oben": tief + (oben + 1) * breite,
        "schnitt": schnitt, "luecken": luecken, "summe": summe,
    }


def einordnen(profil: dict, preis: float) -> dict:
    """Liegt ein Preis in einer Zone mit viel, mittel oder wenig Volumen?"""
    if preis < profil["tief"] or preis > profil["hoch"]:
        return {"zone": "ausserhalb", "faktor": None, "in_value_area": False, "in_luecke": False}
    i = min(int((preis - profil["tief"]) / profil["breite"]), STUFEN - 1)
    faktor = profil["stufen"][i]["v"] / profil["schnitt"] if profil["schnitt"] else None
    zone = "mittel" if faktor is None else "viel" if faktor >= 1.3 else "wenig" if faktor <= 0.5 else "mittel"
    return {
        "zone": zone, "faktor": faktor,
        "in_value_area": profil["va_unten"] <= preis <= profil["va_oben"],
        "in_luecke": any(l["von"] <= preis <= l["bis"] for l in profil["luecken"]),
    }


def _anzeige_kerzen(df: pd.DataFrame, verhaeltnis: pd.Series) -> list[dict]:
    """Höchstens MAX_KERZEN Kerzen: aufeinanderfolgende Kerzen eines Tages werden zusammengefasst.
    Eine zusammengefasste Kerze gilt als Ausreißer, wenn eine ihrer Kerzen einer war."""
    k = max(1, math.ceil(len(df) / MAX_KERZEN))
    tag = pd.Series(df.index.date, index=df.index)
    nummer = df.groupby(tag.values).cumcount().to_numpy() // k
    gruppe = pd.Series(list(zip(tag.values, nummer)), index=df.index)
    teile = df.assign(r=verhaeltnis.to_numpy(), g=gruppe.values).groupby("g", sort=False)
    ergebnis = []
    for _, t in teile:
        r = t["r"].max()
        ergebnis.append({
            "t": t.index[0].tz_convert("Europe/Berlin").isoformat(),
            "o": float(t["Open"].iloc[0]), "h": float(t["High"].max()), "l": float(t["Low"].min()),
            "c": float(t["Close"].iloc[-1]), "v": float(t["Volume"].sum()),
            "x": None if pd.isna(r) else round(float(r), 2),
        })
    return ergebnis


def preis_volumen(ticker: str, zeitraum: str, seit: date | None = None) -> dict:
    """Kerzen, Volumenprofil und Ausreißer für einen Zeitraum. Kurse in Handelswährung."""
    if zeitraum == "kauf":
        if seit is None:
            raise ValueError("ohne Kaufdatum")
        tage = (date.today() - seit).days
        intervall = "1m" if tage < 6 else "5m" if tage < 55 else "1h" if tage < 700 else "1d"
        periode = {"1m": "7d", "5m": "60d", "1h": "2y", "1d": None}[intervall]
        df = _hole(ticker, intervall, periode, None if periode else seit - pd.Timedelta(days=45))
    else:
        intervall, periode, _ = ZEITRAEUME[zeitraum]
        df = _hole(ticker, intervall, periode)

    verhaeltnis = df["Volume"] / _normalvolumen(df, intervall)
    verhaeltnis = verhaeltnis.where(np.isfinite(verhaeltnis))

    tage_im_index = pd.Index(sorted(set(df.index.date)))
    if zeitraum == "heute":
        ab = tage_im_index[-1]
    elif zeitraum == "5t":
        ab = tage_im_index[-5] if len(tage_im_index) >= 5 else tage_im_index[0]
    elif zeitraum == "kauf":
        ab = seit
    else:
        ab = (pd.Timestamp(tage_im_index[-1]) - pd.Timedelta(days=ZEITRAEUME[zeitraum][2])).date()
    maske = df.index.date >= ab
    teil, teil_verh = df[maske], verhaeltnis[maske]
    if teil.empty:
        raise ValueError("keine Kerzen im Zeitraum")

    ausreisser = teil_verh[teil_verh >= AUSREISSER]
    return {
        "intervall": intervall,
        "von": teil.index[0].tz_convert("Europe/Berlin").isoformat(),
        "bis": teil.index[-1].tz_convert("Europe/Berlin").isoformat(),
        "schluss": float(teil["Close"].iloc[-1]),
        "kerzen": _anzeige_kerzen(teil, teil_verh),
        "profil": _profil(teil),
        "ausreisser": len(ausreisser),
        "aktualisiert": datetime.now(timezone.utc).isoformat(),
    }


def relatives_volumen(ticker: str) -> dict:
    """Volumen je 5 Minuten am letzten Handelstag gegen den Durchschnitt derselben Uhrzeit
    an den 20 Handelstagen davor."""
    df = _hole(ticker, "5m", "60d")
    tag = pd.Index(df.index.date, name="tag")
    slot = pd.Index(df.index.strftime("%H:%M"), name="slot")
    tabelle = df["Volume"].groupby([tag, slot]).sum().unstack().sort_index()
    if len(tabelle) < 6:
        raise ValueError("zu wenig Handelstage")
    letzter = tabelle.index[-1]
    vorher = tabelle.iloc[-21:-1]
    schnitt = vorher.mean()
    heute = df[df.index.date == letzter]
    offen = ist_offen(boerse_von(ticker)) and letzter == datetime.now(heute.index.tz).date()
    zeilen = []
    for t, v in heute["Volume"].items():
        s = t.strftime("%H:%M")
        mittel = float(schnitt.get(s, np.nan))
        zeilen.append({"zeit": t.tz_convert("Europe/Berlin").strftime("%H:%M"), "v": float(v),
                       "schnitt": None if np.isnan(mittel) else mittel,
                       "faktor": None if np.isnan(mittel) or mittel <= 0 else float(v) / mittel})
    # Summe bis jetzt: Die laufende 5-Minuten-Kerze ist noch nicht fertig, sie zählt erst danach mit.
    fertig = zeilen[:-1] if offen and len(zeilen) > 1 else zeilen
    slots = [heute.index[i].strftime("%H:%M") for i in range(len(fertig))]
    bisher = sum(z["v"] for z in fertig)
    ueblich = float(vorher.reindex(columns=slots).sum(axis=1).mean()) if slots else 0.0
    ganzer_tag = float(vorher.sum(axis=1).mean())
    return {
        "datum": letzter.isoformat(),
        "live": offen,
        "zeilen": zeilen,
        "bis": fertig[-1]["zeit"] if fertig else None,
        "bisher": bisher,
        "ueblich": ueblich,
        "faktor": bisher / ueblich if ueblich else None,
        "tagesschnitt": ganzer_tag,
        "tage": len(vorher),
        "slots_tag": int(vorher.notna().sum().gt(len(vorher) / 2).sum()),  # 5-Minuten-Kerzen eines ganzen Tages
        "schwelle": RVOL_HOCH,
    }
