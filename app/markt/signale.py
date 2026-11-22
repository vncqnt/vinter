"""Seite "Verdeckte Signale" und Marktbreite.

- FINRA-Short-Volumen: Jeden Börsentag veröffentlicht die FINRA, wie viel des außerbörslichen Volumens
  (Dark Pools und Händler-Internalisierung) je Aktie als Leerverkauf gemeldet wurde. Market Maker
  verkaufen dort meist leer, wenn große Käufer kommen. Daraus:
  * dix(): Dark-Pool-Index nach der DIX-Methode: Short-Volumen aller S&P-500-Werte, nach Dollar gewichtet,
    geteilt durch ihr gesamtes Volumen. Hohe Werte (über etwa 45 %) gingen oft mit Käufen großer Adressen einher.
  * short_quote(): dasselbe Verhältnis für einzelne Aktien mit 20-Tage-Schnitt und Ausreißern.
- korrelation(): durchschnittliche Korrelation der S&P-500-Werte über 20 und 60 Tage und die Streuung
  (wie unterschiedlich sich die Einzelwerte bewegen).
- breite(): Anteil der S&P-500-Werte über ihrem 50- und 200-Tage-Schnitt, neue 52-Wochen-Hochs und -Tiefs.
- divergenzen(): Paare, die normalerweise zusammen laufen (Punkt 37). Laufen sie über 20 Tage deutlich
  auseinander, wird gewarnt.
"""
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .abruf import TAG, als_chart, gecacht, hole, perzentil, schlusskurse
from .indizes import mitglieder, sp500_kurse

TAGE_FINRA = 250          # so viele Börsentage zurück werden geladen
MIN_VOLUMEN = 100_000     # nur Aktien mit nennenswertem Volumen speichern


def _finra_tag(tag: date) -> dict | None:
    """Short-Volumen eines Tages: {Symbol: [Short, Gesamt]}. Kein Handelstag: None."""
    def holen():
        try:
            text = hole(f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{tag:%Y%m%d}.txt", zeitlimit=60).text
        except Exception as f:
            if "404" in str(f) or "403" in str(f):
                return {"leer": True}
            raise
        tab = pd.read_csv(io.StringIO(text), sep="|")
        tab = tab[pd.to_numeric(tab["TotalVolume"], errors="coerce") >= MIN_VOLUMEN]
        return {"werte": {s.replace(".", "-"): [round(float(k)), round(float(g))] for s, k, g in zip(tab["Symbol"], tab["ShortVolume"], tab["TotalVolume"])}}
    d = gecacht(f"finra:{tag:%Y%m%d}", 3650 * TAG, holen)
    return None if d.get("leer") else d["werte"]


def _finra_tage(anzahl: int = TAGE_FINRA, hoechstens_neu: int | None = None) -> dict:
    """{ISO-Tag: Werte} der letzten Börsentage. hoechstens_neu begrenzt, wie viele Dateien jetzt geladen werden."""
    from ..cache import markt_lesen
    heute = date.today()
    kandidaten = [heute - timedelta(days=d) for d in range(0, int(anzahl * 1.5) + 10) if (heute - timedelta(days=d)).weekday() < 5]
    vorhanden = [t for t in kandidaten if markt_lesen(f"finra:{t:%Y%m%d}")[0] is not None]
    fehlend = [t for t in kandidaten if t not in vorhanden and t < heute]  # heutige Datei kommt erst abends
    if hoechstens_neu is not None:
        fehlend = fehlend[:hoechstens_neu]
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda t: _sicher_tag(t), fehlend))
    aus = {}
    for t in sorted(kandidaten):
        w = markt_lesen(f"finra:{t:%Y%m%d}")[0]
        if w and not w.get("leer"):
            aus[t.isoformat()] = w["werte"]
    return dict(list(aus.items())[-anzahl:])


def _sicher_tag(t):
    try:
        return _finra_tag(t)
    except Exception:
        return None


def finra_vorladen() -> None:
    _finra_tage()


def dix() -> dict:
    tage = _finra_tage(hoechstens_neu=25)
    if not tage:
        raise ValueError("Noch keine FINRA-Daten")
    sp = [m["ticker"] for m in mitglieder("sp500")]
    try:
        kurse = sp500_kurse()
    except Exception:
        kurse = None
    werte = {}
    for tag, zeilen in tage.items():
        ts = pd.Timestamp(tag)
        preis = kurse.loc[:ts].iloc[-1] if kurse is not None and len(kurse.loc[:ts]) else None
        k = g = 0.0
        for t in sp:
            z = zeilen.get(t)
            if not z:
                continue
            p = float(preis.get(t, np.nan)) if preis is not None else 1.0
            if not np.isfinite(p):
                continue
            k += z[0] * p
            g += z[1] * p
        if g:
            werte[ts] = k / g * 100
    reihe = pd.Series(werte).sort_index()
    schnitt = reihe.rolling(5).mean()
    spx = schlusskurse("^GSPC", "2y")
    spx_tage = spx.reindex(spx.index.union(reihe.index)).ffill().reindex(reihe.index)
    chart = als_chart({"dix": reihe, "schnitt": schnitt, "spx": spx_tage}, runden=2)
    # Was danach geschah: S&P 500 über die folgenden 20 Börsentage, je nach DIX-Drittel
    folge = (spx.shift(-20) / spx - 1) * 100
    paare = pd.concat([schnitt, folge.reindex(reihe.index)], axis=1, keys=["d", "f"]).dropna()
    gruppen = []
    if len(paare) >= 30:
        g1, g2 = paare["d"].quantile([1 / 3, 2 / 3])
        for name, maske in (("niedrig", paare["d"] <= g1), ("mittel", (paare["d"] > g1) & (paare["d"] < g2)), ("hoch", paare["d"] >= g2)):
            x = paare.loc[maske, "f"]
            gruppen.append({"name": name, "von": round(float(paare.loc[maske, "d"].min()), 1), "bis": round(float(paare.loc[maske, "d"].max()), 1),
                            "anzahl": int(len(x)), "schnitt": round(float(x.mean()), 2), "steigt": round(float((x > 0).mean() * 100))})
    return {**chart, "aktuell": round(float(reihe.iloc[-1]), 1), "aktuell5": round(float(schnitt.iloc[-1]), 1),
            "perzentil": perzentil(schnitt, float(schnitt.iloc[-1])), "schnitt": round(float(reihe.mean()), 1), "tage": len(reihe),
            "stand": reihe.index[-1].strftime("%Y-%m-%d"), "folge": gruppen}


def short_quote(ticker: list[str]) -> list[dict]:
    tage = _finra_tage(hoechstens_neu=25)
    aus = []
    for t in ticker:
        reihe = pd.Series({pd.Timestamp(d): z[t][0] / z[t][1] * 100 for d, z in tage.items() if t in z and z[t][1]}).sort_index()
        if len(reihe) < 10:
            aus.append({"ticker": t, "fehler": "Zu wenig Daten (nur US-Aktien)."})
            continue
        schnitt = reihe.rolling(20, min_periods=10).mean()
        streu = reihe.rolling(60, min_periods=20).std()
        z_wert = (reihe - schnitt) / streu
        auffaellig = [d.strftime("%Y-%m-%d") for d, z in z_wert.items() if abs(z) >= 2]
        try:
            k = schlusskurse(t, "2y")
            kurs = k.reindex(k.index.union(reihe.index)).ffill().reindex(reihe.index)
        except Exception:
            kurs = pd.Series(np.nan, index=reihe.index)
        chart = als_chart({"quote": reihe, "schnitt": schnitt, "kurs": kurs}, runden=2)
        aus.append({"ticker": t, **chart, "aktuell": round(float(reihe.iloc[-1]), 1), "schnitt20": round(float(schnitt.iloc[-1]), 1),
                    "z": round(float(z_wert.iloc[-1]), 2) if pd.notna(z_wert.iloc[-1]) else None, "auffaellig": auffaellig})
    return aus


def _renditen() -> pd.DataFrame:
    kurse = sp500_kurse()
    return np.log(kurse).diff().iloc[1:]


def korrelation() -> dict:
    def holen():
        r = _renditen()
        ergebnisse = {}
        for fenster in (20, 60):
            werte, streu = {}, {}
            for i in range(max(fenster, len(r) - 260), len(r) + 1):
                teil = r.iloc[i - fenster:i].dropna(axis=1)
                if teil.shape[1] < 300:
                    continue
                s_i = teil.std()
                s_p = teil.mean(axis=1).std()
                n = teil.shape[1]
                q = (s_i ** 2).sum() / n ** 2
                nenner = (s_i.sum() / n) ** 2 - q
                werte[teil.index[-1]] = (s_p ** 2 - q) / nenner if nenner > 0 else np.nan
                streu[teil.index[-1]] = np.sqrt(max(0.0, (s_i ** 2).mean() - s_p ** 2)) * np.sqrt(252) * 100
            ergebnisse[fenster] = (pd.Series(werte), pd.Series(streu))
        spx = schlusskurse("^GSPC", "2y")
        k20s = ergebnisse[20][0]
        chart = als_chart({"k20": k20s, "k60": ergebnisse[60][0], "streuung": ergebnisse[20][1],
                           "spx": spx.reindex(spx.index.union(k20s.index)).ffill().reindex(k20s.index)}, runden=3)
        k20, k60 = ergebnisse[20][0].dropna(), ergebnisse[60][0].dropna()
        return {**chart, "k20": round(float(k20.iloc[-1]), 3), "k60": round(float(k60.iloc[-1]), 3),
                "perzentil20": perzentil(k20, float(k20.iloc[-1])), "streuung": round(float(ergebnisse[20][1].iloc[-1]), 1)}
    return gecacht("signale:korrelation2", 12 * 3600, holen)


def breite() -> dict:
    def holen():
        kurse = sp500_kurse()
        s200 = kurse.rolling(200, min_periods=180).mean()
        s50 = kurse.rolling(50, min_periods=45).mean()
        hoch = kurse.rolling(252, min_periods=200).max()
        tief = kurse.rolling(252, min_periods=200).min()
        gueltig = s200.notna().sum(axis=1)
        ueber200 = (kurse > s200).sum(axis=1) / gueltig * 100
        ueber50 = (kurse > s50).sum(axis=1) / s50.notna().sum(axis=1) * 100
        netto = ((kurse >= hoch).sum(axis=1) - (kurse <= tief).sum(axis=1)) / gueltig * 100
        # Advance/Decline (Roadmap Edge 3): je Tag gestiegene minus gefallene Mitglieder, aufsummiert
        ad = advance_decline(kurse)
        ab = kurse.index[-1] - pd.Timedelta(days=365)
        teil = lambda s: s[(s.index >= ab) & gueltig.gt(300)]
        spx = schlusskurse("^GSPC", "2y")
        spx = spx.reindex(spx.index.union(kurse.index)).ffill().reindex(kurse.index)
        linie = teil(ad["netto"]).cumsum()
        chart = als_chart({"ueber200": teil(ueber200), "ueber50": teil(ueber50), "netto_hochs": teil(netto), "spx": teil(spx), "ad_linie": linie}, runden=1)
        # Divergenz: S&P 500 auf 3-Monats-Hoch, die A/D-Linie aber nicht (oder umgekehrt)
        spx_t, lin_t = teil(spx).iloc[-63:], linie.iloc[-63:]
        ad_div = None
        if len(lin_t) > 20:
            if spx_t.iloc[-1] >= spx_t.max() * 0.995 and lin_t.iloc[-1] < lin_t.max() - 0.1 * (lin_t.max() - lin_t.min()):
                ad_div = "negativ"
            elif spx_t.iloc[-1] <= spx_t.min() * 1.005 and lin_t.iloc[-1] > lin_t.min() + 0.1 * (lin_t.max() - lin_t.min()):
                ad_div = "positiv"
        # Divergenz: Index nahe am Hoch, aber weniger Aktien laufen mit als vor 3 Monaten
        hoch_spx = float(spx.iloc[-252:].max())
        return {**chart, "ueber200": round(float(ueber200.iloc[-1]), 1), "ueber50": round(float(ueber50.iloc[-1]), 1),
                "netto_hochs": round(float(netto.iloc[-1]), 1), "ueber200_vor3m": round(float(ueber200.iloc[-64]), 1),
                "spx_abstand_hoch": round((float(spx.iloc[-1]) / hoch_spx - 1) * 100, 1), "spx_3m": round((float(spx.iloc[-1]) / float(spx.iloc[-64]) - 1) * 100, 1),
                "perzentil200": perzentil(teil(ueber200), float(ueber200.iloc[-1])),
                "ad_auf": int(ad["auf"].iloc[-1]), "ad_ab": int(ad["ab"].iloc[-1]), "ad_10": round(float(ad["verhaeltnis10"].iloc[-1]), 2),
                "ad_10_perzentil": perzentil(teil(ad["verhaeltnis10"]), float(ad["verhaeltnis10"].iloc[-1])), "ad_divergenz": ad_div}
    return gecacht("signale:breite3", 12 * 3600, holen)


def advance_decline(kurse: pd.DataFrame) -> dict:
    """Gestiegene und gefallene Werte je Tag, Saldo und Verhältnis über 10 Tage (Auf / (Auf + Ab)). Reine Funktion."""
    veraenderung = kurse.pct_change(fill_method=None)
    auf, ab = (veraenderung > 0).sum(axis=1), (veraenderung < 0).sum(axis=1)
    summe10 = (auf.rolling(10).sum() + ab.rolling(10).sum()).replace(0, float("nan"))
    return {"auf": auf, "ab": ab, "netto": (auf - ab).astype(float), "verhaeltnis10": auf.rolling(10).sum() / summe10}


# Punkt 37: Paare, die normalerweise in dieselbe Richtung laufen. (Kennung, Name, A, B, Name A, Name B, warum)
DIVERGENZ_PAARE = [
    ("qqq_soxx", "Nasdaq 100 gegen Halbleiter", "QQQ", "SOXX", "Nasdaq 100", "Halbleiter",
     "Halbleiter laufen dem Techsektor meist voraus. Steigt der Nasdaq, während Halbleiter fallen, fehlt dem Anstieg die Basis."),
    ("spy_iwm", "Große gegen kleine Werte", "SPY", "IWM", "S&P 500", "Russell 2000",
     "Steigen nur die Großen und die Nebenwerte nicht, ist der Aufschwung schmal und anfälliger."),
    ("spy_hyg", "Aktien gegen Hochzinsanleihen", "SPY", "HYG", "S&P 500", "Hochzinsanleihen",
     "Der Anleihemarkt reagiert oft früher auf Kreditrisiken. Fallen Hochzinsanleihen, während Aktien steigen, ist das ein klassisches Warnsignal."),
    ("kupfer_zins", "Kupfer/Gold gegen 10-jährige Rendite", "KUPFER/GOLD", "^TNX", "Kupfer/Gold", "Rendite 10 J.",
     "Das Verhältnis Kupfer zu Gold gilt als Konjunkturbarometer und läuft meist mit den Renditen. Gehen sie auseinander, passt eine Seite nicht zur anderen."),
]


def _reihe(kennung: str) -> pd.Series:
    if kennung == "KUPFER/GOLD":
        k, g = schlusskurse("HG=F", "2y"), schlusskurse("GC=F", "2y")
        return (k / g).dropna()
    return schlusskurse(kennung, "2y")


def divergenzen() -> dict:
    def holen():
        aus = []
        for kennung, name, a, b, na, nb, warum in DIVERGENZ_PAARE:
            try:
                ra, rb = _reihe(a), _reihe(b)
            except Exception:
                continue
            tab = pd.concat([ra, rb], axis=1, keys=["a", "b"]).dropna()
            if len(tab) < 150:
                continue
            # 20-Tage-Veränderung: Kurse in Prozent, die Rendite in Prozentpunkten (sonst wären es Prozent von Prozent)
            va = tab["a"].pct_change(20) * 100
            vb = tab["b"].diff(20) if b == "^TNX" else tab["b"].pct_change(20) * 100
            za, zb = (va - va.mean()) / va.std(), (vb - vb.mean()) / vb.std()
            abstand = (za - zb).dropna()
            korr = np.log(tab["a"]).diff().rolling(60).corr(np.log(tab["b"]).diff() if b != "^TNX" else tab["b"].diff())
            ja, jb, jab = float(va.iloc[-1]), float(vb.iloc[-1]), float(abstand.iloc[-1])
            gegen = ja * jb < 0
            stufe = "deutlich" if abs(jab) >= 2 and gegen else "auffaellig" if abs(jab) >= 1.5 else "normal"
            fuehrt = na if jab > 0 else nb
            de = lambda v, vz=True: (f"{v:+.1f}" if vz else f"{v:.1f}").replace(".", ",").replace("-", "−")
            einheit = " Pp." if b == "^TNX" else " %"
            if stufe == "normal":
                text = f"Beide laufen im üblichen Rahmen zusammen ({na} {de(ja)} %, {nb} {de(jb)}{einheit} in 20 Tagen)."
            else:
                text = (f"{na} {de(ja)} % gegen {nb} {de(jb)}{einheit} in 20 Tagen: {fuehrt} ist "
                        f"{de(abs(jab), False)} typische Schwankungen stärker.{' Sie laufen in entgegengesetzte Richtungen.' if gegen else ''}")
            letzte = tab.iloc[-260:]
            chart = als_chart({
                "a": letzte["a"] / letzte["a"].iloc[0] * 100,
                "b": letzte["b"] if b == "^TNX" else letzte["b"] / letzte["b"].iloc[0] * 100,
                "abstand": abstand.reindex(letzte.index),
            }, runden=3)
            aus.append({"id": kennung, "name": name, "a": na, "b": nb, "warum": warum, "va": round(ja, 2), "vb": round(jb, 2),
                        "abstand": round(jab, 2), "gegen": gegen, "stufe": stufe, "text": text,
                        "korrelation": None if pd.isna(korr.iloc[-1]) else round(float(korr.iloc[-1]), 2),
                        "warnungen_1j": int(((abstand.iloc[-260:].abs() >= 2) & (va.iloc[-260:] * vb.iloc[-260:] < 0)).sum()),
                        "b_rendite": b == "^TNX", **chart})
        if not aus:
            raise ValueError("keine Daten")
        return aus
    return {"paare": gecacht("signale:divergenzen", 6 * 3600, holen)}


def signale_seite() -> dict:
    aus = {}
    for name, f in (("dix", dix), ("korrelation", korrelation), ("breite", breite), ("divergenz", divergenzen)):
        try:
            aus[name] = f()
        except Exception as fehler:
            aus[name] = {"fehler": f"Gerade nicht verfügbar ({type(fehler).__name__}: {str(fehler)[:80]})."}
    return aus
