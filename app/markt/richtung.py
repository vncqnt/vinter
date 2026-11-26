"""Mitglieder einer Marktrichtung (Sektor, Thema, Index) für die Arbeitsplätze im Dashboard: Rangliste je
Zeitraum, gleichgewichteter Korb als Kursverlauf und Marktbreite (über 50-/200-Tage-Linie, nahe am Hoch).

Ein gebündelter Yahoo-Abruf (yf.download) für alle Mitglieder, 30 Minuten im Cache. Kurse in Handelswährung;
Veränderungen in Prozent sind deshalb vergleichbar, Beträge nicht.
"""
import pandas as pd
import yfinance as yf

from .abruf import gecacht

HOECHSTENS = 40
ZEITRAEUME = {"tag": 1, "woche": 5, "monat": 21, "3m": 63, "jahr": 252}


def _namen(bezug: dict) -> dict:
    if bezug.get("mitglieder_namen"):
        return bezug["mitglieder_namen"]
    try:
        from .indizes import mitglieder
        aus = {}
        for idx in ("sp500", "ndx", "dax", "sx5e"):
            for m in mitglieder(idx):
                aus.setdefault(m["ticker"], m.get("name") or m["ticker"])
        return aus
    except Exception:  # noqa: BLE001
        return {}


def _kurse(tickers: list[str]) -> pd.DataFrame:
    def holen():
        roh = yf.download(tickers, period="14mo", auto_adjust=True, progress=False, threads=True, group_by="column")["Close"]
        if isinstance(roh, pd.Series):
            roh = roh.to_frame(tickers[0])
        roh.index = pd.DatetimeIndex(roh.index).tz_localize(None).normalize()
        return {"daten": [d.strftime("%Y-%m-%d") for d in roh.index], "reihen": {t: [None if pd.isna(v) else round(float(v), 4) for v in roh[t]] for t in roh.columns}}
    d = gecacht(f"richtung:kurse:{','.join(sorted(tickers))}", 1800, holen)
    return pd.DataFrame(d["reihen"], index=pd.to_datetime(d["daten"]), dtype=float)


def kennzahlen(df: pd.DataFrame) -> dict[str, dict]:
    """Je Spalte: Veränderung je Zeitraum, seit Jahresbeginn, über 50/200-Tage-Linie, Abstand zum 52-Wochen-Hoch. Reine Funktion."""
    aus = {}
    jahr = df.index[-1].year if len(df) else None
    for t in df.columns:
        s = df[t].dropna()
        if len(s) < 25:
            continue
        z = {k: round(float((s.iloc[-1] / s.iloc[-1 - n] - 1) * 100), 2) for k, n in ZEITRAEUME.items() if len(s) > n}
        vorjahr = s[s.index.year < jahr]
        if len(vorjahr):
            z["ytd"] = round(float((s.iloc[-1] / vorjahr.iloc[-1] - 1) * 100), 2)
        z["ueber50"] = bool(s.iloc[-1] > s.iloc[-50:].mean()) if len(s) >= 50 else None
        z["ueber200"] = bool(s.iloc[-1] > s.iloc[-200:].mean()) if len(s) >= 200 else None
        z["abstand_hoch"] = round(float((s.iloc[-1] / s.iloc[-252:].max() - 1) * 100), 2)
        aus[t] = z
    return aus


def korb(df: pd.DataFrame, tage: int = 253) -> pd.Series:
    """Gleichgewichteter Korb: jeden Tag der Durchschnitt der Tagesrenditen aller Werte mit Kurs, Start 100. Reine Funktion."""
    r = df.ffill(limit=3).pct_change(fill_method=None).iloc[1:]
    mittel = r.mean(axis=1, skipna=True).fillna(0).iloc[-tage:]
    return 100 * (1 + mittel).cumprod()


def breite(k: dict[str, dict]) -> dict:
    werte = list(k.values())
    def anteil(feld, test):
        n = [w for w in werte if w.get(feld) is not None]
        return round(100 * sum(1 for w in n if test(w[feld])) / len(n), 1) if n else None
    return {"n": len(werte), "ueber50": anteil("ueber50", bool), "ueber200": anteil("ueber200", bool),
            "nahe_hoch": anteil("abstand_hoch", lambda v: v > -5), "im_plus_monat": anteil("monat", lambda v: v > 0)}


def mitglieder(bezug: dict) -> dict:
    tickers = list(dict.fromkeys(bezug.get("mitglieder") or []))[:HOECHSTENS]
    if not tickers:
        return {"fehler": "Diese Richtung hat keine Mitglieder (Anlageklasse oder Faktor)."}
    df = _kurse(tickers)
    k = kennzahlen(df)
    namen = _namen(bezug)
    werte = [{"ticker": t, "name": namen.get(t, t), **k[t]} for t in tickers if t in k]
    kb = korb(df[[t for t in tickers if t in k]])
    kz = {n: round(float((kb.iloc[-1] / kb.iloc[-1 - z] - 1) * 100), 2) for n, z in ZEITRAEUME.items() if len(kb) > z}
    return {"bezug": bezug["id"], "name": bezug.get("name"), "werte": werte, "fehlen": [t for t in tickers if t not in k],
            "korb": {"daten": [d.strftime("%Y-%m-%d") for d in kb.index], "werte": [round(float(v), 2) for v in kb], "veraenderung": kz},
            "breite": breite(k), "stand": df.index[-1].strftime("%Y-%m-%d") if len(df) else None}
