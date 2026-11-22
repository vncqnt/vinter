"""Automatische Chartanalyse: Trend, Unterstützung und Widerstand, Momentum, IPO-Modus.

Beschreibt den Chart, gibt aber nie Kauf- oder Verkaufssignale. Alle Stellschrauben stehen in
chart_konfig.py.

Aufbau:
- Reine Rechenfunktionen (sma, atr, swings, zonen_clustern, ausbrueche, ...) bekommen eine Tabelle
  mit Tageskursen und rechnen nur mit dem, was darin steht. So lassen sie sich testen, und ein
  Backtest bekommt einfach die Tabelle bis zum gewünschten Tag (kein Blick in die Zukunft).
- analysiere(df) setzt alles zu einem Ergebnis zusammen, ebenfalls ohne Netz.
- analyse(ticker) holt die Kurse (einmal pro Handelstag nach US-Schluss) und hängt den Live-Kurs an.

Begriffe:
- Swing-Hoch: ein Tag, dessen Hoch über den Hochs der n Tage davor und danach liegt. Bestätigt ist er
  erst, wenn die n Tage danach existieren. Swing-Tief entsprechend mit den Tiefs.
- Zone: Preisband aus Swing-Punkten, die höchstens 0,5 × ATR auseinanderliegen, oder ein starker
  Knoten im Volumenprofil.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from . import chart_konfig as K
from .abruf import STUNDE, gecacht

SPALTEN = ["Open", "High", "Low", "Close", "Volume"]


# ---------- Grundbausteine ----------

def sma(reihe: pd.Series, n: int) -> pd.Series:
    return reihe.rolling(n, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int = K.ATR_TAGE) -> pd.Series:
    """Average True Range nach Wilder: typische Tagesspanne inklusive Kurslücken."""
    vorher = df["Close"].shift()
    spanne = pd.concat([df["High"] - df["Low"], (df["High"] - vorher).abs(), (df["Low"] - vorher).abs()], axis=1).max(axis=1)
    return spanne.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(schluss: pd.Series, n: int = K.RSI_TAGE) -> pd.Series:
    d = schluss.diff()
    auf = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ab = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + auf / ab.replace(0, np.nan))


def swings(df: pd.DataFrame, n: int = K.SWING_N) -> list[dict]:
    """Alle bestätigten Swing-Punkte. Ein Punkt an Position i braucht die Tage i-n bis i+n;
    die letzten n Tage der Tabelle können deshalb (noch) keine Swing-Punkte sein."""
    hoch, tief = df["High"].to_numpy(), df["Low"].to_numpy()
    aus = []
    for i in range(n, len(df) - n):
        if hoch[i] > hoch[i - n:i].max() and hoch[i] > hoch[i + 1:i + n + 1].max():
            aus.append({"art": "hoch", "i": i, "preis": float(hoch[i]), "bestaetigt_i": i + n})
        if tief[i] < tief[i - n:i].min() and tief[i] < tief[i + 1:i + n + 1].min():
            aus.append({"art": "tief", "i": i, "preis": float(tief[i]), "bestaetigt_i": i + n})
    return aus


def zonen_clustern(punkte: list[dict], toleranz: float) -> list[list[dict]]:
    """Punkte nach Preis sortieren; liegt ein Punkt höchstens `toleranz` über dem niedrigsten Punkt
    der aktuellen Gruppe, gehört er dazu, sonst beginnt eine neue Gruppe. So ist keine Zone breiter
    als die Toleranz (keine Ketten über weite Preisbereiche). Ergebnis: Liste von Gruppen (je eine Zone)."""
    if not punkte:
        return []
    sortiert = sorted(punkte, key=lambda p: p["preis"])
    gruppen = [[sortiert[0]]]
    for p in sortiert[1:]:
        if p["preis"] - gruppen[-1][0]["preis"] <= toleranz:
            gruppen[-1].append(p)
        else:
            gruppen.append([p])
    return gruppen


def ausbrueche(df: pd.DataFrame, unten: float, oben: float, ab_i: int,
               prozent: float = K.AUSBRUCH_PROZENT, vol_tage: int = K.AUSBRUCH_VOLUMEN_TAGE) -> dict:
    """Wann wurde die Zone gebrochen? Bruch = Schlusskurs mehr als `prozent` % jenseits der Zone
    UND Volumen über dem Schnitt der `vol_tage` Tage davor. Gezählt wird ab dem Tag, an dem die
    Zone bekannt war (ab_i). Ergebnis: Seite am Ende ('ueber', 'unter', 'in') und alle Brüche."""
    schluss, vol = df["Close"].to_numpy(), df["Volume"].to_numpy()
    schnitt = df["Volume"].rolling(vol_tage, min_periods=vol_tage).mean().shift(1).to_numpy()
    if ab_i >= len(df):
        return {"seite": None, "ereignisse": []}
    c0 = schluss[ab_i]
    seite = "ueber" if c0 > oben else "unter" if c0 < unten else "in"
    ereignisse = []
    grenze_oben, grenze_unten = oben * (1 + prozent / 100), unten * (1 - prozent / 100)
    for j in range(ab_i + 1, len(df)):
        laut = not np.isnan(schnitt[j]) and vol[j] > schnitt[j]
        if not laut:
            continue
        if schluss[j] > grenze_oben and seite != "ueber":
            ereignisse.append({"i": j, "richtung": "oben", "vorher": seite})
            seite = "ueber"
        elif schluss[j] < grenze_unten and seite != "unter":
            ereignisse.append({"i": j, "richtung": "unten", "vorher": seite})
            seite = "unter"
    return {"seite": seite, "ereignisse": ereignisse}


def volumenprofil(df: pd.DataFrame, stufen: int = K.PROFIL_STUFEN) -> dict:
    """Volumen nach Preisstufen: das Volumen jedes Tages verteilt sich gleichmäßig über seine Spanne."""
    if df.empty:
        return {"stufen": [], "poc": None}
    tief, hoch = float(df["Low"].min()), float(df["High"].max())
    if hoch <= tief:
        hoch = tief * 1.001 + 1e-9
    kanten = np.linspace(tief, hoch, stufen + 1)
    lo, hi, vol = df["Low"].to_numpy()[:, None], df["High"].to_numpy()[:, None], df["Volume"].to_numpy()[:, None]
    breite = np.maximum(hi - lo, (hoch - tief) / stufen / 10)
    ueberlapp = np.clip(np.minimum(hi, kanten[1:][None, :]) - np.maximum(lo, kanten[:-1][None, :]), 0, None)
    # Tage ohne Spanne (Hoch = Tief) landen ganz in ihrer Stufe
    flach = (hi - lo) <= 0
    if flach.any():
        stufe = np.clip(np.searchsorted(kanten, lo[:, 0], side="right") - 1, 0, stufen - 1)
        ueberlapp[flach[:, 0]] = 0
        ueberlapp[np.where(flach[:, 0])[0], stufe[flach[:, 0]]] = breite[flach[:, 0], 0]
    menge = (ueberlapp / breite * vol).sum(axis=0)
    poc = int(menge.argmax())
    return {"stufen": [{"von": float(kanten[k]), "bis": float(kanten[k + 1]), "volumen": float(menge[k])} for k in range(stufen)],
            "poc": {"von": float(kanten[poc]), "bis": float(kanten[poc + 1])}}


def profil_knoten(profil: dict, anzahl: int = K.PROFIL_KNOTEN, mindest: float = K.PROFIL_KNOTEN_MIN) -> list[dict]:
    """Die stärksten lokalen Gipfel im Volumenprofil."""
    st = profil["stufen"]
    if len(st) < 3:
        return []
    v = np.array([s["volumen"] for s in st])
    schnitt = v.mean()
    gipfel = [k for k in range(len(v)) if v[k] >= mindest * schnitt and v[k] >= v[max(0, k - 1)] and v[k] >= v[min(len(v) - 1, k + 1)]]
    gipfel.sort(key=lambda k: -v[k])
    return [{"unten": st[k]["von"], "oben": st[k]["bis"], "volumen": float(v[k]), "faktor": round(float(v[k] / schnitt), 2)} for k in gipfel[:anzahl]]


def runde_schritte(kurs: float) -> float:
    """Ein runder Schritt von rund 5 % des Kurses (1, 2, 2,5 oder 5 mal Zehnerpotenz)."""
    ziel = kurs * 0.05
    basis = 10 ** np.floor(np.log10(ziel))
    for f in (1, 2, 2.5, 5, 10):
        if basis * f >= ziel:
            return float(basis * f)
    return float(basis * 10)


# ---------- Teile der Analyse ----------

def _datum(df, i):
    return df.index[i].strftime("%Y-%m-%d")


def trend(df: pd.DataFrame) -> dict:
    s = df["Close"]
    s50, s200 = sma(s, K.SMA_KURZ), sma(s, K.SMA_LANG)
    kurs = float(s.iloc[-1])
    aus = {"sma50": None if pd.isna(s50.iloc[-1]) else round(float(s50.iloc[-1]), 4),
           "sma200": None if pd.isna(s200.iloc[-1]) else round(float(s200.iloc[-1]), 4)}
    if aus["sma200"] is None or len(s200.dropna()) <= K.STEIGUNG_TAGE:
        aus.update(regime=None, hinweis=f"Für die {K.SMA_LANG}-Tage-Linie fehlen Daten (nur {len(s)} Handelstage).")
    else:
        aus["abstand200"] = round((kurs / aus["sma200"] - 1) * 100, 2)
        st = (s200.iloc[-1] / s200.iloc[-1 - K.STEIGUNG_TAGE] - 1) * 100
        aus["steigung200"] = round(float(st), 2)
        aus["steigung"] = "steigend" if st > K.STEIGUNG_FLACH else "fallend" if st < -K.STEIGUNG_FLACH else "flach"
        aus["regime"] = ("Aufwärtstrend" if kurs > aus["sma200"] and aus["steigung"] == "steigend"
                         else "Abwärtstrend" if kurs < aus["sma200"] and aus["steigung"] == "fallend" else "Seitwärts / unklar")
    if aus["sma50"] is not None:
        aus["abstand50"] = round((kurs / aus["sma50"] - 1) * 100, 2)
    # Golden Cross / Death Cross: Vorzeichenwechsel von SMA50 minus SMA200
    d = (s50 - s200).dropna()
    zeichen = np.sign(d.to_numpy())
    kreuze = []
    for j in range(1, len(zeichen)):
        if zeichen[j] != 0 and zeichen[j] != zeichen[j - 1]:
            i = df.index.get_loc(d.index[j])
            kreuze.append({"i": int(i), "datum": _datum(df, i), "art": "golden" if zeichen[j] > 0 else "death"})
    aus["kreuze"] = kreuze
    aus["letztes_kreuz"] = kreuze[-1] if kreuze else None
    return aus


def struktur(punkte: list[dict]) -> dict:
    """Marktstruktur aus den letzten zwei bestätigten Swing-Hochs und -Tiefs."""
    hochs = [p for p in punkte if p["art"] == "hoch"][-2:]
    tiefs = [p for p in punkte if p["art"] == "tief"][-2:]
    if len(hochs) < 2 or len(tiefs) < 2:
        return {"text": "Zu wenige Swing-Punkte für eine Struktur", "art": None}
    hh, ht = hochs[1]["preis"] > hochs[0]["preis"], tiefs[1]["preis"] > tiefs[0]["preis"]
    if hh and ht:
        return {"art": "auf", "text": "Höhere Hochs und höhere Tiefs", "hochs": hochs, "tiefs": tiefs}
    if not hh and not ht:
        return {"art": "ab", "text": "Tiefere Hochs und tiefere Tiefs", "hochs": hochs, "tiefs": tiefs}
    return {"art": "gemischt", "text": f"Gemischt: {'höhere' if hh else 'tiefere'} Hochs, {'höhere' if ht else 'tiefere'} Tiefs", "hochs": hochs, "tiefs": tiefs}


def _score(beruehrungen: int, alter: int, vol_anteil: float) -> dict:
    b = min(beruehrungen, K.BERUEHRUNG_VOLL) / K.BERUEHRUNG_VOLL
    a = 0.5 ** (alter / K.HALBWERTSZEIT_TAGE)
    v = min(vol_anteil / K.VOLUMEN_VOLL, 1.0)
    return {"score": round(100 * (K.GEWICHT_BERUEHRUNG * b + K.GEWICHT_AKTUALITAET * a + K.GEWICHT_VOLUMEN * v), 1),
            "teile": {"beruehrungen": round(b, 2), "aktualitaet": round(a, 2), "volumen": round(v, 2)}}


def zonen(df: pd.DataFrame, punkte: list[dict], atr_wert: float, mit_ausbruch: bool = True) -> list[dict]:
    """Zonen aus Swing-Punkten und Volumenknoten, mit Score, Rolle und Brüchen."""
    letzter = len(df) - 1
    kurs = float(df["Close"].iloc[-1])
    fenster = df.iloc[-K.PROFIL_TAGE:]
    typisch = ((fenster["High"] + fenster["Low"] + fenster["Close"]) / 3).to_numpy()
    fvol = fenster["Volume"].to_numpy()
    gesamt = float(fvol.sum()) or 1.0

    def anteil(unten, oben):
        return float(fvol[(typisch >= unten) & (typisch <= oben)].sum()) / gesamt

    mindest = K.ZONE_MIN_BREITE_ATR * atr_wert
    aus = []
    junge = [p for p in punkte if p["i"] >= letzter - K.ZONEN_TAGE]
    for gruppe in zonen_clustern(junge, K.ZONE_ATR_FAKTOR * atr_wert):
        unten, oben = min(p["preis"] for p in gruppe), max(p["preis"] for p in gruppe)
        if oben - unten < mindest:
            mitte = (oben + unten) / 2
            unten, oben = mitte - mindest / 2, mitte + mindest / 2
        z = {"quelle": "swing", "unten": unten, "oben": oben, "beruehrungen": len(gruppe),
             "hochs": sum(p["art"] == "hoch" for p in gruppe), "tiefs": sum(p["art"] == "tief" for p in gruppe),
             "letzte_i": max(p["i"] for p in gruppe), "ab_i": min(p["bestaetigt_i"] for p in gruppe),
             "punkte": [{"datum": _datum(df, p["i"]), "preis": round(p["preis"], 4), "art": p["art"]} for p in sorted(gruppe, key=lambda p: p["i"])]}
        z["vol_anteil"] = anteil(unten, oben)
        z.update(_score(z["beruehrungen"], letzter - z["letzte_i"], z["vol_anteil"]))
        aus.append(z)
    # Volumenknoten: eigene Zone, außer er überlappt eine Swing-Zone (dann wird diese markiert)
    for k in profil_knoten(volumenprofil(fenster)):
        treffer = [z for z in aus if z["unten"] <= k["oben"] and z["oben"] >= k["unten"]]
        if treffer:
            for z in treffer:
                z["volumenknoten"] = True
            continue
        drin = np.where((fenster["Low"].to_numpy() <= k["oben"]) & (fenster["High"].to_numpy() >= k["unten"]))[0]
        letzte_i = len(df) - len(fenster) + int(drin[-1]) if len(drin) else len(df) - len(fenster)
        z = {"quelle": "volumen", "unten": k["unten"], "oben": k["oben"], "beruehrungen": 0, "hochs": 0, "tiefs": 0,
             "letzte_i": letzte_i, "ab_i": len(df) - len(fenster), "punkte": [], "volumenknoten": True, "faktor": k["faktor"],
             "vol_anteil": anteil(k["unten"], k["oben"])}
        z.update(_score(0, letzter - letzte_i, z["vol_anteil"]))
        aus.append(z)
    for z in aus:
        z["atr_prozent"] = atr_wert / kurs * 100
        z["rolle"] = "unterstuetzung" if kurs > z["oben"] else "widerstand" if kurs < z["unten"] else "im_bereich"
        z["mitte"] = (z["unten"] + z["oben"]) / 2
        z["abstand"] = round(((z["oben"] if z["rolle"] == "unterstuetzung" else z["unten"]) / kurs - 1) * 100, 2) if z["rolle"] != "im_bereich" else 0.0
        z["letzte"] = _datum(df, z["letzte_i"])
        if mit_ausbruch:
            b = ausbrueche(df, z["unten"], z["oben"], z["ab_i"])
            z["brueche"] = [{"datum": _datum(df, e["i"]), "i": e["i"], "richtung": e["richtung"]} for e in b["ereignisse"]]
            letzter_bruch = b["ereignisse"][-1] if b["ereignisse"] else None
            # Rollentausch: zuletzt nach oben gebrochen und Kurs noch darüber (Widerstand -> Unterstützung) oder umgekehrt
            z["rollentausch"] = bool(letzter_bruch and ((letzter_bruch["richtung"] == "oben" and z["rolle"] == "unterstuetzung")
                                                        or (letzter_bruch["richtung"] == "unten" and z["rolle"] == "widerstand")))
    return aus


def auswahl(alle: list[dict], kurs: float) -> list[dict]:
    """Höchstens MAX_ZONEN Zonen in Kursnähe, die mit dem höchsten Score; die nächste Unterstützung
    und der nächste Widerstand sind immer dabei."""
    atr_p = (alle[0].get("atr_prozent") if alle else None) or 0
    grenze = max(K.NAEHE_PROZENT, K.NAEHE_ATR * atr_p)
    nah = [z for z in alle if abs(z["mitte"] / kurs - 1) * 100 <= grenze]
    gewaehlt = sorted(nah, key=lambda z: -z["score"])[:K.MAX_ZONEN]
    for pflicht in (naechste(alle, "unterstuetzung"), naechste(alle, "widerstand")):
        if pflicht and pflicht not in gewaehlt:
            if len(gewaehlt) >= K.MAX_ZONEN:
                ersetzbar = [z for z in gewaehlt if z is not naechste(alle, "unterstuetzung") and z is not naechste(alle, "widerstand")]
                gewaehlt.remove(min(ersetzbar, key=lambda z: z["score"]))
            gewaehlt.append(pflicht)
    return sorted(gewaehlt, key=lambda z: -z["mitte"])


def naechste(alle: list[dict], rolle: str) -> dict | None:
    kandidaten = [z for z in alle if z["rolle"] == rolle]
    if not kandidaten:
        return None
    return max(kandidaten, key=lambda z: z["oben"]) if rolle == "unterstuetzung" else min(kandidaten, key=lambda z: z["unten"])


def referenzen(df: pd.DataFrame) -> list[dict]:
    kurs = float(df["Close"].iloc[-1])
    aus = []
    schritt = runde_schritte(kurs)
    unter, ueber = np.floor(kurs / schritt) * schritt, np.ceil(kurs / schritt) * schritt
    if ueber == unter:
        ueber += schritt
    aus += [{"art": "rund", "name": "Runde Zahl", "preis": float(unter)}, {"art": "rund", "name": "Runde Zahl", "preis": float(ueber)}]
    woche = df.index.to_period("W-SUN")
    vorige = df[woche == woche[-1] - 1]
    if len(vorige):
        aus += [{"art": "vorwoche", "name": "Vorwochen-Hoch", "preis": float(vorige["High"].max())},
                {"art": "vorwoche", "name": "Vorwochen-Tief", "preis": float(vorige["Low"].min())}]
    jahr = df.iloc[-252:]
    aus += [{"art": "52w", "name": "52-Wochen-Hoch", "preis": float(jahr["High"].max())},
            {"art": "52w", "name": "52-Wochen-Tief", "preis": float(jahr["Low"].min())}]
    for r in aus:
        r["abstand"] = round((r["preis"] / kurs - 1) * 100, 2)
    return aus


def momentum(df: pd.DataFrame, vergleiche: dict[str, pd.Series] | None = None) -> dict:
    s = df["Close"]
    kurs = float(s.iloc[-1])
    aus = {"performance": {k: (round(float(kurs / s.iloc[-1 - n] - 1) * 100, 2) if len(s) > n else None) for k, n in K.PERFORMANCE_TAGE.items()}}
    jahr = df.iloc[-252:]
    aus["hoch52"] = float(jahr["High"].max())
    aus["abstand_hoch52"] = round((kurs / aus["hoch52"] - 1) * 100, 2)
    aus["tief52"] = float(jahr["Low"].min())
    aus["abstand_tief52"] = round((kurs / aus["tief52"] - 1) * 100, 2)
    vol = df["Volume"]
    schnitt = vol.iloc[-1 - K.RVOL_TAGE:-1].mean() if len(vol) > K.RVOL_TAGE else np.nan
    aus["rvol"] = None if not schnitt or pd.isna(schnitt) else round(float(vol.iloc[-1] / schnitt), 2)
    a = atr(df)
    aus["atr"] = None if pd.isna(a.iloc[-1]) else round(float(a.iloc[-1]), 4)
    aus["atr_prozent"] = None if aus["atr"] is None else round(aus["atr"] / kurs * 100, 2)
    r = rsi(s)
    aus["rsi"] = None if pd.isna(r.iloc[-1]) else round(float(r.iloc[-1]), 1)
    rs = {}
    for name, v in (vergleiche or {}).items():
        v = v.reindex(df.index).ffill()
        q = (s / v).dropna()
        if len(q) <= K.RS_TAGE:
            continue
        linie = q.iloc[-K.RS_LINIE_TAGE:]
        rs[name] = {"wert3m": round(float(q.iloc[-1] / q.iloc[-1 - K.RS_TAGE] - 1) * 100, 2),
                    "daten": [d.strftime("%Y-%m-%d") for d in linie.index],
                    "linie": [round(float(x / linie.iloc[0] * 100), 2) for x in linie]}
    aus["rs"] = rs
    return aus


def stops(kurs: float, atr_wert: float | None, unterstuetzung: dict | None) -> list[dict]:
    """Stop-Abstände (Information): 1,5× und 2× ATR unter der nächsten Unterstützung, sonst unter dem Kurs."""
    if not atr_wert:
        return []
    basis = unterstuetzung["unten"] if unterstuetzung else kurs
    return [{"faktor": f, "preis": round(basis - f * atr_wert, 4), "abstand": round(((basis - f * atr_wert) / kurs - 1) * 100, 2),
             "basis": "unter der nächsten Unterstützung" if unterstuetzung else "unter dem Kurs"} for f in K.STOP_ATR]


def ipo_modus(df: pd.DataFrame, ipo_preis: float | None = None) -> dict | None:
    """Unter IPO_TAGE Handelstagen: IPO-Preis, Anchored VWAP ab Tag 1, Hoch/Tief seit Listing, Profil."""
    if len(df) >= K.IPO_TAGE:
        return None
    kurs = float(df["Close"].iloc[-1])
    typisch = (df["High"] + df["Low"] + df["Close"]) / 3
    vwap = (typisch * df["Volume"]).cumsum() / df["Volume"].cumsum().replace(0, np.nan)
    hoch, tief = float(df["High"].max()), float(df["Low"].min())
    preis = ipo_preis if ipo_preis else float(df["Open"].iloc[0])
    return {"tage": len(df), "erster_tag": _datum(df, 0),
            "ipo_preis": preis, "ipo_quelle": "eingetragen" if ipo_preis else "erster Eröffnungskurs (geschätzt)",
            "abstand_ipo": round((kurs / preis - 1) * 100, 2) if preis else None,
            "vwap": [None if pd.isna(v) else round(float(v), 4) for v in vwap], "vwap_jetzt": None if pd.isna(vwap.iloc[-1]) else round(float(vwap.iloc[-1]), 4),
            "hoch": hoch, "tief": tief, "datum_hoch": _datum(df, int(df["High"].to_numpy().argmax())), "datum_tief": _datum(df, int(df["Low"].to_numpy().argmin())),
            "abstand_hoch": round((kurs / hoch - 1) * 100, 2), "abstand_tief": round((kurs / tief - 1) * 100, 2),
            "profil": volumenprofil(df)}


def ereignisse(df: pd.DataFrame, t: dict, alle_zonen: list[dict], tage: int = K.EREIGNIS_TAGE) -> list[dict]:
    """Was ist in den letzten `tage` Handelstagen passiert?"""
    ab = len(df) - tage
    aus = []
    for z in alle_zonen:
        for b in z.get("brueche", []):
            if b["i"] >= ab:
                aus.append({"datum": b["datum"], "art": "bruch", "text": f"Zone {_preis(z['unten'])} bis {_preis(z['oben'])} nach {b['richtung']} gebrochen (Schluss über 2 % jenseits, Volumen über Schnitt)"})
    for k in t.get("kreuze", []):
        if k["i"] >= ab:
            aus.append({"datum": k["datum"], "art": k["art"], "text": "Golden Cross: 50-Tage-Linie steigt über die 200-Tage-Linie" if k["art"] == "golden" else "Death Cross: 50-Tage-Linie fällt unter die 200-Tage-Linie"})
    hoch, tief, vol = df["High"].to_numpy(), df["Low"].to_numpy(), df["Volume"].to_numpy()
    schnitt = df["Volume"].rolling(K.RVOL_TAGE, min_periods=K.RVOL_TAGE).mean().shift(1).to_numpy()
    for j in range(max(ab, 252), len(df)):
        if hoch[j] > hoch[j - 252:j].max():
            aus.append({"datum": _datum(df, j), "art": "hoch52", "text": f"Neues 52-Wochen-Hoch ({_preis(hoch[j])})"})
        if tief[j] < tief[j - 252:j].min():
            aus.append({"datum": _datum(df, j), "art": "tief52", "text": f"Neues 52-Wochen-Tief ({_preis(tief[j])})"})
    for j in range(max(ab, 1), len(df)):
        if not np.isnan(schnitt[j]) and schnitt[j] > 0 and vol[j] / schnitt[j] >= K.RVOL_AUFFAELLIG:
            richtung = "steigendem" if df["Close"].iloc[j] >= df["Close"].iloc[j - 1] else "fallendem"
            aus.append({"datum": _datum(df, j), "art": "volumen", "text": f"Auffälliges Volumen: {vol[j] / schnitt[j]:.1f}-mal der 20-Tage-Schnitt bei {richtung} Kurs".replace(".", ",")})
    return sorted(aus, key=lambda e: e["datum"], reverse=True)


def _preis(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")


# ---------- Zusammensetzen ----------

def analysiere(df: pd.DataFrame, n: int = K.SWING_N, vergleiche: dict | None = None, ipo_preis: float | None = None,
               kompakt: bool = False) -> dict:
    """Komplette Analyse aus einer Tabelle mit Tageskursen (Spalten Open, High, Low, Close, Volume).
    kompakt=True: nur die Teile für einen Trade-Schnappschuss (schneller, ohne Brüche und Chartdaten)."""
    df = df.dropna(subset=["Close"])
    if len(df) < 2:
        raise ValueError("zu wenige Kurse")
    kurs = float(df["Close"].iloc[-1])
    a = atr(df)
    atr_wert = None if pd.isna(a.iloc[-1]) else float(a.iloc[-1])
    ipo = ipo_modus(df, ipo_preis)
    t = trend(df)
    aus = {"kurs": kurs, "stand": _datum(df, len(df) - 1), "tage": len(df), "trend": t, "ipo": ipo, "n": n}
    if ipo or atr_wert is None:
        aus.update(swings=[], zonen=[], alle_zonen=0, struktur={"art": None, "text": "Im IPO-Modus ausgeblendet: Für Swing-Punkte und Zonen gibt es noch zu wenig Handelstage."},
                   naechste_unterstuetzung=None, naechster_widerstand=None)
    else:
        punkte = swings(df, n)
        alle = zonen(df, punkte, atr_wert, mit_ausbruch=not kompakt)
        u, w = naechste(alle, "unterstuetzung"), naechste(alle, "widerstand")
        aus.update(struktur=struktur(punkte), naechste_unterstuetzung=u, naechster_widerstand=w, alle_zonen=len(alle))
        if kompakt:
            aus["atr"] = atr_wert
            aus["im_bereich"] = [{"unten": z["unten"], "oben": z["oben"], "hochs": z["hochs"], "tiefs": z["tiefs"]} for z in alle if z["rolle"] == "im_bereich"]
            return aus
        aus["zonen"] = auswahl(alle, kurs)
        aus["swings"] = [{"datum": _datum(df, p["i"]), "preis": p["preis"], "art": p["art"]} for p in punkte if p["i"] >= len(df) - 520]
        aus["ereignisse"] = ereignisse(df, t, alle)
        aus["_alle"] = alle
    if kompakt:
        aus["atr"] = atr_wert
        return aus
    aus["momentum"] = momentum(df, vergleiche)
    aus["stops"] = stops(kurs, atr_wert, aus.get("naechste_unterstuetzung"))
    aus["referenzen"] = referenzen(df)
    aus["profil"] = volumenprofil(df.iloc[-K.PROFIL_TAGE:])
    if ipo:
        aus["ereignisse"] = ereignisse(df, t, [])
    aus.pop("_alle", None)
    # Chartdaten: höchstens 2 Jahre, gleitende Durchschnitte über die ganze Historie gerechnet
    sicht = df.iloc[-520:]
    s50, s200 = sma(df["Close"], K.SMA_KURZ).iloc[-520:], sma(df["Close"], K.SMA_LANG).iloc[-520:]
    r = lambda reihe: [None if pd.isna(v) else round(float(v), 4) for v in reihe]
    aus["chart"] = {"daten": [d.strftime("%Y-%m-%d") for d in sicht.index], "o": r(sicht["Open"]), "h": r(sicht["High"]), "l": r(sicht["Low"]),
                    "c": r(sicht["Close"]), "v": [int(v) for v in sicht["Volume"].fillna(0)], "sma50": r(s50), "sma200": r(s200)}
    return aus


# ---------- Daten holen ----------

def handelstag_schluessel(jetzt: datetime | None = None) -> str:
    """Neu gerechnet wird einmal pro Tag nach US-Börsenschluss (22:30 deutscher Zeit)."""
    jetzt = jetzt or datetime.now(ZoneInfo("Europe/Berlin"))
    tag = jetzt.date() if (jetzt.hour, jetzt.minute) >= (22, 30) else jetzt.date() - timedelta(days=1)
    return tag.isoformat()


def tageskurse(ticker: str) -> pd.DataFrame:
    stichtag = handelstag_schluessel()

    def holen():
        h = yf.Ticker(ticker).history(period=K.HISTORIE, auto_adjust=True)
        if h.empty:
            raise ValueError(f"Keine Kurse für {ticker}")
        h = h[SPALTEN].dropna(subset=["Close"])
        return {"daten": [d.strftime("%Y-%m-%d") for d in h.index], **{k: [None if pd.isna(v) else float(v) for v in h[k]] for k in SPALTEN}}
    d = gecacht(f"chart:{K.CACHE_VERSION}:{ticker}:{stichtag}", 36 * STUNDE, holen)
    df = pd.DataFrame({k: d[k] for k in SPALTEN}, index=pd.to_datetime(d["daten"]), dtype=float)
    df["Volume"] = df["Volume"].fillna(0)
    df[["Open", "High", "Low"]] = df[["Open", "High", "Low"]].apply(lambda s: s.fillna(df["Close"]))
    # Teilweise gehandelte Tage nach dem Stichtag (z. B. Xetra am Vormittag) bleiben draußen
    return df[df.index <= pd.Timestamp(stichtag)]


def sektor_etf(ticker: str) -> str | None:
    from .bewertung import SEKTOR_ETF
    from .kurse import stammdaten
    try:
        return SEKTOR_ETF.get(stammdaten(ticker).get("sector"))
    except Exception:
        return None


def analyse(ticker: str, n: int = K.SWING_N, ipo_preis: float | None = None) -> dict:
    """Analyse eines Tickers mit Vergleich zu SPY, QQQ und Sektor-ETF. Einmal pro Handelstag gerechnet."""
    if n not in K.SWING_N_ERLAUBT:
        n = K.SWING_N

    def holen():
        df = tageskurse(ticker)
        etf = sektor_etf(ticker)
        vergleiche = {}
        for v in (*K.RS_VERGLEICH, *([etf] if etf and etf != ticker else [])):
            try:
                vergleiche[v] = tageskurse(v)["Close"]
            except Exception:
                continue
        aus = analysiere(df, n, vergleiche, ipo_preis)
        aus.update(ticker=ticker, sektor_etf=etf)
        return aus
    return gecacht(f"chartanalyse:{K.CACHE_VERSION}:{ticker}:{n}:{ipo_preis or ''}:{handelstag_schluessel()}", 36 * STUNDE, holen)


# ---------- Kerzen für den interaktiven Chart ----------
# Intervall: (Yahoo-Intervall, Zeitraum, Cache in Sekunden). Yahoo liefert 15 Minuten nur 60 Tage,
# Stunden rund 2 Jahre zurück.
INTERVALLE = {"15m": ("15m", "60d", 120), "1h": ("1h", "730d", 300), "1d": ("1d", "5y", 300),
              "1wk": ("1wk", "max", 6 * 3600), "1mo": ("1mo", "max", 6 * 3600)}


def kerzen(ticker: str, intervall: str) -> dict:
    """OHLCV-Kerzen für den Chart. Tages-, Wochen- und Monatskerzen mit Datum, Intraday mit
    Unix-Sekunden (UTC). Der laufende Tag ist enthalten (anders als in der Analyse)."""
    iv, periode, alter = INTERVALLE[intervall]

    def holen():
        h = yf.Ticker(ticker).history(period=periode, interval=iv, auto_adjust=True)
        if h.empty:
            raise ValueError(f"Keine Kurse für {ticker}")
        h = h[SPALTEN].dropna(subset=["Close"])
        h = h[~h.index.duplicated(keep="last")]
        zeiten = [int(d.timestamp()) for d in h.index] if iv in ("15m", "1h") else [d.strftime("%Y-%m-%d") for d in h.index]
        r = lambda s: [None if pd.isna(v) else round(float(v), 6) for v in s]
        return {"intervall": intervall, "t": zeiten, "o": r(h["Open"]), "h": r(h["High"]), "l": r(h["Low"]), "c": r(h["Close"]),
                "v": [0 if pd.isna(v) else int(v) for v in h["Volume"]]}
    return gecacht(f"kerzen:{ticker}:{intervall}", alter, holen)
