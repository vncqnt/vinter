"""Gewichtungs-Rechner für den Depot-Umbau (Roadmap Q3).

Du gibst vor, welcher Anteil des Depots "riskant" sein soll (Rest "etabliert"). Innerhalb jeder Gruppe schlägt
das Tool Gewichte vor, bei denen jede Position denselben Beitrag zum Risiko der Gruppe leistet (Equal Risk
Contribution: wer stark schwankt oder mit den anderen gleichläuft, bekommt weniger Geld). Grundlage sind
Tagesrenditen in Euro über ein Jahr. Positionen, die du "fest" markierst, behalten ihren heutigen Betrag.
Ergebnis: Umschichtungen in Euro (kleine unter der Mindestgröße werden weggelassen), Risikoanteile vorher und
nachher, geschätzte realisierte Gewinne der Verkäufe gegen den Freibetrag. Information, keine Empfehlung.

Einteilung ohne eigene Angabe: Fonds/ETFs und Gold etabliert, Krypto riskant, Aktien ab 45 % Jahresschwankung
riskant (Standardannahme, siehe OFFENE_FRAGEN.md). Gespeichert pro Benutzer in umbau.yaml.
"""
import math

import numpy as np
import pandas as pd
import yaml

from ..benutzer import Benutzer
from ..markt.euro_kurse import euro_kurse
from .freibetrag import TEILFREISTELLUNG, freibetrag

GRENZE_RISKANT = 0.45
STEUERSATZ = 0.26375  # Abgeltungsteuer mit Soli, ohne Kirchensteuer
STANDARD = {"riskant_ziel": 30, "mindest": 25, "gruppe": {}, "fest": []}


def einstellungen(b: Benutzer) -> dict:
    d = b.datei("umbau.yaml")
    try:
        roh = yaml.safe_load(d.read_text(encoding="utf-8")) if d.exists() else {}
    except Exception:
        roh = {}
    return {**STANDARD, **{k: v for k, v in (roh or {}).items() if k in STANDARD}}


def speichern(b: Benutzer, neu: dict) -> dict:
    e = einstellungen(b)
    if "riskant_ziel" in neu:
        e["riskant_ziel"] = max(0, min(100, int(neu["riskant_ziel"])))
    if "mindest" in neu:
        e["mindest"] = max(0, min(5000, int(neu["mindest"])))
    if isinstance(neu.get("gruppe"), dict):
        e["gruppe"] = {str(k)[:20]: v for k, v in neu["gruppe"].items() if v in ("riskant", "etabliert")}
    if isinstance(neu.get("fest"), list):
        e["fest"] = [str(x)[:20] for x in neu["fest"]][:60]
    d = b.datei("umbau.yaml")
    d.parent.mkdir(parents=True, exist_ok=True)
    zw = d.with_suffix(".tmp")
    zw.write_text(yaml.safe_dump(e, allow_unicode=True, sort_keys=False), encoding="utf-8")
    zw.replace(d)
    return e


def gleicher_risikobeitrag(cov: np.ndarray, schritte: int = 2000) -> np.ndarray:
    """Gewichte (Summe 1), bei denen w_i · (Σw)_i für alle i gleich ist. Reine Funktion (getestet)."""
    n = len(cov)
    if n == 1:
        return np.ones(1)
    w = 1 / np.sqrt(np.diag(cov))
    w /= w.sum()
    for _ in range(schritte):
        beitrag = w * (cov @ w)
        ziel = beitrag.mean()
        neu = w * np.sqrt(ziel / np.maximum(beitrag, 1e-18))
        neu /= neu.sum()
        if np.abs(neu - w).max() < 1e-10:
            return neu
        w = neu
    return w


def risikoanteile(w: np.ndarray, cov: np.ndarray) -> np.ndarray:
    varianz = float(w @ cov @ w)
    return w * (cov @ w) / varianz if varianz > 0 else np.zeros(len(w))


def rechnen(b: Benutzer, positionen: list[dict], riskant_ziel: int | None = None, mindest: int | None = None) -> dict:
    e = einstellungen(b)
    ziel = e["riskant_ziel"] if riskant_ziel is None else max(0, min(100, riskant_ziel))
    mindest = e["mindest"] if mindest is None else mindest
    pos = [p for p in positionen if p.get("wert_eur") and not p.get("extra")]
    if not pos:
        return {"fehler": "Keine Positionen mit Kurs."}
    gesamt = sum(p["wert_eur"] for p in pos)
    kurse = euro_kurse(sorted({p["ticker"] for p in pos if p.get("ticker")}), tage=400)
    # Auf Börsentage (Mo–Fr) ausrichten und Lücken mit dem letzten Kurs füllen: Krypto handelt auch am Wochenende,
    # sonst bekämen Aktien dort 0 % und Krypto würde mit zu vielen Tagen gerechnet
    preise = pd.DataFrame({p["isin"]: kurse[p["ticker"]] for p in pos if p.get("ticker") in kurse})
    preise = preise[preise.index.day_of_week < 5].ffill(limit=5)
    renditen = preise.pct_change().iloc[-252:]
    renditen = renditen.dropna(axis=1, thresh=120).fillna(0.0)  # mindestens ein halbes Jahr Daten
    vol = (renditen.std() * math.sqrt(252)).to_dict()

    def gruppe(p):
        if p["isin"] in e["gruppe"]:
            return e["gruppe"][p["isin"]], "eigene Angabe"
        if p["art"] == "CRYPTO":
            return "riskant", "Krypto"
        if p["art"] == "FUND":
            return "etabliert", "Fonds/ETF"
        v = vol.get(p["isin"])
        if v is None:
            return "riskant", "ohne Kursverlauf"
        return ("riskant", f"Schwankung {v * 100:.0f} %") if v >= GRENZE_RISKANT else ("etabliert", f"Schwankung {v * 100:.0f} %")

    zeilen = []
    for p in pos:
        g, warum = gruppe(p)
        zeilen.append({"isin": p["isin"], "name": p["name"], "ticker": p.get("ticker"), "art": p["art"], "gruppe": g, "warum": warum,
                       "fest": p["isin"] in e["fest"] or p["isin"] not in renditen.columns, "ohne_daten": p["isin"] not in renditen.columns,
                       "wert": p["wert_eur"], "vol": vol.get(p["isin"]), "kurs": p.get("kurs_eur"),
                       "einstand_stueck": p["einstand_gesamt"] / p["stueck"] if p.get("stueck") else None})
    ziel_eur = {"riskant": gesamt * ziel / 100, "etabliert": gesamt * (100 - ziel) / 100}
    cov_alle = renditen.cov().to_numpy() * 252
    spalten = list(renditen.columns)
    for g in ("riskant", "etabliert"):
        frei = [z for z in zeilen if z["gruppe"] == g and not z["fest"]]
        fest_summe = sum(z["wert"] for z in zeilen if z["gruppe"] == g and z["fest"])
        budget = max(0.0, ziel_eur[g] - fest_summe)
        if not frei:
            continue
        idx = [spalten.index(z["isin"]) for z in frei]
        w = gleicher_risikobeitrag(cov_alle[np.ix_(idx, idx)])
        for z, wi in zip(frei, w):
            z["vorschlag"] = budget * float(wi)
    for z in zeilen:
        z.setdefault("vorschlag", z["wert"])
        diff = z["vorschlag"] - z["wert"]
        z["umschichten"] = diff if abs(diff) >= mindest else 0.0
        z["neu"] = z["wert"] + z["umschichten"]
        # Gewinn, der bei einem Verkauf realisiert würde (Durchschnittseinstand, grobe Näherung zu FIFO)
        if z["umschichten"] < 0 and z["kurs"] and z["einstand_stueck"]:
            gewinn = -z["umschichten"] * (1 - z["einstand_stueck"] / z["kurs"])
            z["gewinn_verkauf"] = gewinn
            z["gewinn_steuer"] = gewinn * TEILFREISTELLUNG if z["art"] == "FUND" and gewinn > 0 else gewinn

    # Risiko vorher und nachher (nur Positionen mit Kursverlauf)
    mit = [z for z in zeilen if not z["ohne_daten"]]
    idx = [spalten.index(z["isin"]) for z in mit]
    cov = cov_alle[np.ix_(idx, idx)]
    w_alt = np.array([z["wert"] for z in mit]); w_alt /= w_alt.sum()
    w_neu = np.array([z["neu"] for z in mit]); w_neu /= w_neu.sum()
    for z, a, n in zip(mit, risikoanteile(w_alt, cov), risikoanteile(w_neu, cov)):
        z["risiko_heute"], z["risiko_neu"] = float(a) * 100, float(n) * 100

    def anteil(feld, g):
        return sum(z[feld] for z in zeilen if z["gruppe"] == g) / sum(z[feld] for z in zeilen) * 100

    gewinne = sum(z.get("gewinn_steuer", 0) for z in zeilen)
    try:
        frei_rest = freibetrag(b)["frei"]
    except Exception:
        frei_rest = None
    steuer = max(0.0, gewinne - (frei_rest or 0)) * STEUERSATZ if gewinne > 0 else 0.0
    return {
        "zeilen": sorted(zeilen, key=lambda z: (z["gruppe"] != "riskant", -z["wert"])),
        "ziel": ziel, "mindest": mindest, "gesamt": gesamt,
        "riskant_heute": anteil("wert", "riskant"), "riskant_neu": anteil("neu", "riskant"),
        "vol_heute": float(math.sqrt(w_alt @ cov @ w_alt)) * 100, "vol_neu": float(math.sqrt(w_neu @ cov @ w_neu)) * 100,
        "verkaufen": -sum(z["umschichten"] for z in zeilen if z["umschichten"] < 0), "kaufen": sum(z["umschichten"] for z in zeilen if z["umschichten"] > 0),
        "gewinne": gewinne, "freibetrag_frei": frei_rest, "steuer": steuer,
        "grenze_riskant": GRENZE_RISKANT * 100,
    }
