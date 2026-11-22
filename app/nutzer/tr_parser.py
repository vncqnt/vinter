"""Liest die Trade-Republic-CSV-Exporte aus dem Ordner tr_export des Benutzers ein."""
import io
from datetime import datetime

import pandas as pd

from ..benutzer import Benutzer


def _ordner(b: Benutzer):
    return b.datei("tr_export")

# Nur diese Spalten werden gelesen. Namen und IBANs von Überweisungs-Empfängern
# (counterparty_*, payment_reference, description) gelangen so gar nicht ins Programm.
SPALTEN = [
    "datetime", "category", "type", "asset_class", "name", "symbol",
    "shares", "price", "amount", "fee", "tax", "currency", "transaction_id",
]


MAX_GROESSE = 5 * 1024 * 1024  # 5 MB, echte Exporte sind weit kleiner


class ExportFehler(Exception):
    """Der Export fehlt oder hat ein unerwartetes Format."""


def _alle_zeilen(b: Benutzer) -> pd.DataFrame:
    """Liest alle Exporte ein. Überlappende Zeiträume werden über die Buchungs-ID bereinigt."""
    dateien = sorted(_ordner(b).glob("*.csv"))
    if not dateien:
        raise ExportFehler("Noch kein Trade-Republic-Export vorhanden. Lade ihn auf der Seite Positionen hoch.")

    teile = []
    for datei in dateien:
        kopf = pd.read_csv(datei, nrows=0).columns
        fehlend = [s for s in SPALTEN if s not in kopf]
        if fehlend:
            raise ExportFehler(f"{datei.name}: Spalten fehlen: {', '.join(fehlend)}")
        teile.append(pd.read_csv(datei, usecols=SPALTEN, dtype=str))

    alle = pd.concat(teile, ignore_index=True)
    # Ein frischer Kauf steht manchmal noch ohne Betrag im Export, weil er noch nicht
    # abgerechnet ist. Taucht dieselbe Buchung in einem neueren Export fertig auf, gewinnt diese.
    alle = alle.sort_values("amount", key=lambda s: s.isna(), kind="stable")
    return alle.drop_duplicates("transaction_id").sort_index()


def speichere_export(b: Benutzer, inhalt: bytes) -> dict:
    """Prüft einen hochgeladenen Export und legt ihn nur ab, wenn er neue Buchungen enthält.

    Der Dateiname wird selbst vergeben, damit ein Upload nie eine vorhandene Datei ersetzt.
    """
    if not inhalt:
        raise ExportFehler("Die Datei ist leer.")
    if len(inhalt) > MAX_GROESSE:
        raise ExportFehler("Die Datei ist zu groß für einen Trade-Republic-Export.")
    try:
        neu = pd.read_csv(io.BytesIO(inhalt), dtype=str)
    except Exception:
        raise ExportFehler("Die Datei lässt sich nicht als CSV lesen.") from None

    fehlend = [s for s in SPALTEN if s not in neu.columns]
    if fehlend:
        raise ExportFehler("Das ist kein Transaktionsexport von Trade Republic, es fehlen Spalten.")
    if neu.empty or neu["transaction_id"].isna().any():
        raise ExportFehler("Die Datei enthält keine gültigen Buchungen.")
    try:
        zeiten = pd.to_datetime(neu["datetime"], format="ISO8601", utc=True)
        for spalte in ["shares", "price", "amount", "fee", "tax"]:
            pd.to_numeric(neu[spalte])
    except (ValueError, TypeError):
        raise ExportFehler("Die Datei enthält unlesbare Datums- oder Zahlenwerte.") from None
    if (neu.loc[neu["category"] == "TRADING", "currency"] != "EUR").any():
        raise ExportFehler("Die Datei enthält Trades in einer anderen Währung als Euro.")

    bekannt = fertig = set()
    if any(_ordner(b).glob("*.csv")):
        alt = _alle_zeilen(b)
        bekannt = set(alt["transaction_id"])
        fertig = set(alt.loc[alt["amount"].notna(), "transaction_id"])
    ids = set(neu["transaction_id"])
    anzahl_neu = len(ids - bekannt)
    # Buchungen, die beim letzten Mal noch ohne Betrag kamen und jetzt abgerechnet sind.
    nachgetragen = len((set(neu.loc[neu["amount"].notna(), "transaction_id"]) & bekannt) - fertig)
    if anzahl_neu or nachgetragen:
        _ordner(b).mkdir(parents=True, exist_ok=True)
        (_ordner(b) / f"upload_{datetime.now():%Y%m%d_%H%M%S}.csv").write_bytes(inhalt)
    return {
        "neu": anzahl_neu,
        "nachgetragen": nachgetragen,
        "gesamt": len(bekannt | ids),
        "von": zeiten.min().date().isoformat(),
        "bis": zeiten.max().date().isoformat(),
    }


def lade_ertraege(b: Benutzer) -> list[dict]:
    """Gibt Dividenden und Zinsen zurück. Der Betrag steht im Export brutto in Euro."""
    df = _alle_zeilen(b)
    df = df[(df["category"] == "CASH") & df["type"].isin(["DIVIDEND", "INTEREST_PAYMENT"])].copy()
    for spalte in ["amount", "tax"]:
        df[spalte] = pd.to_numeric(df[spalte]).fillna(0.0)
    # In deutsche Zeit umrechnen, sonst landet eine Buchung kurz nach Silvester im falschen Steuerjahr.
    df["zeit"] = pd.to_datetime(df["datetime"], format="ISO8601", utc=True).dt.tz_convert("Europe/Berlin")
    return [
        {"zeit": z.zeit, "typ": z.type, "art": z.asset_class, "isin": z.symbol, "betrag": z.amount, "steuer": abs(z.tax)}
        for z in df.itertuples()
    ]


def lade_trades(b: Benutzer) -> list[dict]:
    """Gibt alle Käufe und Verkäufe zurück, ältester zuerst."""
    df = _alle_zeilen(b)
    df = df[(df["category"] == "TRADING") & df["type"].isin(["BUY", "SELL"])].copy()

    fremd = df.loc[df["currency"] != "EUR", "currency"].unique()
    if len(fremd):
        raise ExportFehler(f"Unerwartete Währung im Export: {', '.join(fremd)}")

    for spalte in ["shares", "price", "amount", "fee"]:
        df[spalte] = pd.to_numeric(df[spalte])
    # Noch nicht abgerechnete Trades haben keinen Betrag. Ohne Schätzung zählten sie als
    # Kauf für 0 € und drückten den Einstand. Schätzung: Stück × Kurs plus 1 € Fremdkostenpauschale,
    # so rechnet auch Trade Republic den vorläufigen Einstand. Der nächste Export ersetzt das.
    df["vorlaeufig"] = df["amount"].isna()
    offen = df["vorlaeufig"]
    df.loc[offen, "amount"] = (-df.loc[offen, "shares"] * df.loc[offen, "price"]).round(2)
    df.loc[offen & df["fee"].isna(), "fee"] = -1.0
    df[["amount", "fee"]] = df[["amount", "fee"]].fillna(0.0)
    # In deutsche Zeit umrechnen, sonst landet eine Buchung kurz nach Silvester im falschen Steuerjahr.
    df["zeit"] = pd.to_datetime(df["datetime"], format="ISO8601", utc=True).dt.tz_convert("Europe/Berlin")
    df = df.sort_values("zeit")

    return [
        {
            "zeit": z.zeit,
            "typ": z.type,
            "art": z.asset_class,
            "name": z.name,
            "isin": z.symbol,
            "stueck": z.shares,
            "betrag": z.amount,
            "gebuehr": abs(z.fee),
            "vorlaeufig": z.vorlaeufig,
        }
        for z in df.itertuples()
    ]
