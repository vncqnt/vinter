"""Speicher für Handelsdaten: eine SQLite-Datei bei den Marktdaten (lässt sich nicht einfach nachladen).

Tabellen
- werte:       alle Quellen im gemeinsamen Schema. Jede Änderung eines Werts ist eine neue Version,
               alte Versionen bleiben stehen (Revisionen). Angezeigt wird immer die höchste Version.
- rohdaten:    jede Lieferung unverändert als .gz-Datei im Ordner roh/, hier Pfad, Prüfsumme und Art,
               damit sich alles ohne neuen Abruf neu einlesen lässt.
- quarantaene: Lieferungen, die die Prüfung nicht bestanden haben. Sie landen nicht in "werte".
- protokoll:   Abrufe und Fehler je Quelle (für die Status-Seite).
- kontingent:  Anfragen je Quelle und Tag (Limit-Zähler).
- zustand:     ETag/Last-Modified/Prüfsumme je Adresse und Zeitpunkte je Quelle.
- neuigkeiten: wenn ein neuer Zeitraum dazukommt (für die Leiste oben).
"""
import gzip
import hashlib
import json
import sqlite3
import threading
from datetime import date, datetime, timezone

from ...konfig import MARKT_DATEN

ORDNER = MARKT_DATEN / "handel"
DATEI = ORDNER / "handel.sqlite"
ROH = ORDNER / "roh"
_sperre = threading.RLock()

# Felder des gemeinsamen Schemas (Reihenfolge = Spalten)
FELDER = ("quelle", "berichtsland", "partnerland", "richtung", "ware", "ware_text", "hs_ebene", "hs_version",
          "zeitraum", "wert_usd", "menge", "einheit", "waehrung", "wert_orig", "kurs", "kurs_quelle", "ort",
          "sonderfall", "veroeffentlicht")
PFLICHT = ("quelle", "richtung", "ware", "zeitraum")
# Ein Wert gilt als derselbe Datensatz, wenn diese Felder gleich sind
SCHLUESSEL = ("quelle", "berichtsland", "partnerland", "richtung", "ware", "zeitraum", "ort")


def jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    ORDNER.mkdir(parents=True, exist_ok=True)
    v = sqlite3.connect(DATEI, timeout=30)
    v.row_factory = sqlite3.Row
    v.execute("PRAGMA journal_mode=WAL")
    v.execute(f"""CREATE TABLE IF NOT EXISTS werte ({", ".join(f + (" REAL" if f in ("wert_usd", "menge", "wert_orig", "kurs") else " TEXT") for f in FELDER)},
                  abgerufen TEXT, version INTEGER, roh_id INTEGER)""")
    v.execute("CREATE INDEX IF NOT EXISTS werte_suche ON werte (quelle, berichtsland, richtung, ware, zeitraum)")
    v.execute("CREATE INDEX IF NOT EXISTS werte_partner ON werte (quelle, partnerland, zeitraum)")
    v.execute("CREATE TABLE IF NOT EXISTS rohdaten (id INTEGER PRIMARY KEY, quelle TEXT, zeit TEXT, art TEXT, meta TEXT, pfad TEXT, pruefsumme TEXT, groesse INTEGER)")
    v.execute("CREATE TABLE IF NOT EXISTS quarantaene (id INTEGER PRIMARY KEY, quelle TEXT, zeit TEXT, art TEXT, grund TEXT, roh_id INTEGER, zeilen INTEGER, erledigt INTEGER DEFAULT 0)")
    v.execute("CREATE TABLE IF NOT EXISTS protokoll (id INTEGER PRIMARY KEY, quelle TEXT, zeit TEXT, ok INTEGER, text TEXT)")
    v.execute("CREATE TABLE IF NOT EXISTS kontingent (quelle TEXT, tag TEXT, anfragen INTEGER, PRIMARY KEY (quelle, tag))")
    v.execute("CREATE TABLE IF NOT EXISTS zustand (schluessel TEXT PRIMARY KEY, wert TEXT)")
    v.execute("CREATE TABLE IF NOT EXISTS neuigkeiten (id INTEGER PRIMARY KEY, quelle TEXT, zeit TEXT, zeitraum TEXT, text TEXT)")
    return v


# ---- Zustand (kleine Schlüssel-Wert-Ablage) ----
def zustand(schluessel: str, standard=None):
    with _sperre, db() as v:
        z = v.execute("SELECT wert FROM zustand WHERE schluessel = ?", (schluessel,)).fetchone()
    return json.loads(z[0]) if z else standard


def zustand_setzen(schluessel: str, wert) -> None:
    with _sperre, db() as v:
        v.execute("INSERT OR REPLACE INTO zustand VALUES (?, ?)", (schluessel, json.dumps(wert)))


# ---- Limit-Zähler ----
def anfragen_heute(quelle: str) -> int:
    with _sperre, db() as v:
        z = v.execute("SELECT anfragen FROM kontingent WHERE quelle = ? AND tag = ?", (quelle, date.today().isoformat())).fetchone()
    return z[0] if z else 0


def anfrage_zaehlen(quelle: str) -> None:
    with _sperre, db() as v:
        v.execute("INSERT INTO kontingent VALUES (?, ?, 1) ON CONFLICT (quelle, tag) DO UPDATE SET anfragen = anfragen + 1",
                  (quelle, date.today().isoformat()))


# ---- Protokoll ----
def protokollieren(quelle: str, ok: bool, text: str) -> None:
    with _sperre, db() as v:
        v.execute("INSERT INTO protokoll (quelle, zeit, ok, text) VALUES (?, ?, ?, ?)", (quelle, jetzt(), int(ok), text[:500]))
        v.execute("DELETE FROM protokoll WHERE id IN (SELECT id FROM protokoll WHERE quelle = ? ORDER BY id DESC LIMIT -1 OFFSET 200)", (quelle,))


# ---- Rohdaten ----
def roh_ablegen(quelle: str, art: str, meta: dict, inhalt: bytes, endung: str) -> tuple[int, bool]:
    """Legt die Lieferung unverändert ab. Gibt (id, neu) zurück; neu = False, wenn dieselbe Art mit denselben
    Angaben schon genau diesen Inhalt hatte (dann muss nichts neu eingelesen werden)."""
    summe = hashlib.sha256(inhalt).hexdigest()
    meta_text = json.dumps(meta, sort_keys=True)
    with _sperre, db() as v:
        alt = v.execute("SELECT id, pruefsumme FROM rohdaten WHERE quelle = ? AND art = ? AND meta = ? ORDER BY id DESC LIMIT 1",
                        (quelle, art, meta_text)).fetchone()
        if alt and alt["pruefsumme"] == summe:
            return alt["id"], False
        zeit = datetime.now()
        pfad = ROH / quelle / f"{zeit:%Y%m%d-%H%M%S}-{art}-{summe[:10]}.{endung}.gz"
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_bytes(gzip.compress(inhalt))
        cur = v.execute("INSERT INTO rohdaten (quelle, zeit, art, meta, pfad, pruefsumme, groesse) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (quelle, jetzt(), art, meta_text, str(pfad.relative_to(ORDNER)), summe, len(inhalt)))
        return cur.lastrowid, True


def roh_liste(quelle: str) -> list[dict]:
    with _sperre, db() as v:
        return [dict(z) for z in v.execute("SELECT * FROM rohdaten WHERE quelle = ? ORDER BY id", (quelle,))]


def roh_lesen(eintrag: dict) -> bytes:
    return gzip.decompress((ORDNER / eintrag["pfad"]).read_bytes())


# ---- Quarantäne ----
def quarantaene(quelle: str, art: str, grund: str, roh_id: int | None, zeilen: int) -> None:
    with _sperre, db() as v:
        v.execute("INSERT INTO quarantaene (quelle, zeit, art, grund, roh_id, zeilen) VALUES (?, ?, ?, ?, ?, ?)",
                  (quelle, jetzt(), art, grund[:500], roh_id, zeilen))


# ---- Werte mit Versionen ----
def speichern(zeilen: list[dict], roh_id: int | None, schwelle_sprung: float) -> dict:
    """Schreibt Zeilen ins Schema. Unveränderte Werte werden übersprungen, geänderte als neue Version angelegt.
    Gibt Zahlen für das Protokoll zurück und die neu hinzugekommenen Zeiträume."""
    neu = revidiert = gleich = 0
    zeitraeume = set()
    abgerufen = jetzt()
    with _sperre, db() as v:
        for z in zeilen:
            schl = tuple(z.get(f) for f in SCHLUESSEL)
            bed = " AND ".join(f"{f} IS ?" for f in SCHLUESSEL)
            alt = v.execute(f"SELECT wert_usd, menge, version, sonderfall FROM werte WHERE {bed} ORDER BY version DESC LIMIT 1", schl).fetchone()
            if alt and _gleich(alt["wert_usd"], z.get("wert_usd")) and _gleich(alt["menge"], z.get("menge")):
                gleich += 1
                continue
            version = (alt["version"] + 1) if alt else 1
            sonder = set(filter(None, (z.get("sonderfall") or "").split(",")))
            if alt:
                sonder.add("revidiert")
                revidiert += 1
            else:
                neu += 1
                vorher = _vormonat(v, z)
                if vorher and z.get("wert_usd") and abs(z["wert_usd"] / vorher - 1) * 100 > schwelle_sprung:
                    sonder.add("sprung")
                if not v.execute("SELECT 1 FROM werte WHERE quelle = ? AND zeitraum = ? LIMIT 1", (z["quelle"], z["zeitraum"])).fetchone():
                    zeitraeume.add(z["zeitraum"])
            werte = {**z, "sonderfall": ",".join(sorted(sonder)) or None}
            v.execute(f"INSERT INTO werte ({', '.join(FELDER)}, abgerufen, version, roh_id) VALUES ({', '.join('?' * (len(FELDER) + 3))})",
                      (*[werte.get(f) for f in FELDER], abgerufen, version, roh_id))
    return {"neu": neu, "revidiert": revidiert, "gleich": gleich, "zeitraeume": sorted(zeitraeume)}


def _gleich(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= max(abs(a), abs(b)) * 1e-9 + 1e-9


def _vormonat(v, z: dict) -> float | None:
    """Wert des Vorzeitraums derselben Reihe (für die Sprung-Markierung)."""
    if len(z["zeitraum"]) != 7:
        return None
    j, m = int(z["zeitraum"][:4]), int(z["zeitraum"][5:])
    vor = f"{j - 1}-12" if m == 1 else f"{j}-{m - 1:02d}"
    schl = [z.get(f) for f in SCHLUESSEL]
    schl[SCHLUESSEL.index("zeitraum")] = vor
    bed = " AND ".join(f"{f} IS ?" for f in SCHLUESSEL)
    a = v.execute(f"SELECT wert_usd FROM werte WHERE {bed} ORDER BY version DESC LIMIT 1", schl).fetchone()
    return a[0] if a and a[0] else None


def neuigkeit(quelle: str, zeitraum: str, text: str) -> None:
    with _sperre, db() as v:
        v.execute("INSERT INTO neuigkeiten (quelle, zeit, zeitraum, text) VALUES (?, ?, ?, ?)", (quelle, jetzt(), zeitraum, text))


def abfrage(sql: str, parameter=()) -> list[dict]:
    with _sperre, db() as v:
        return [dict(z) for z in v.execute(sql, parameter)]


# Nur die neueste Version je Datensatz
AKTUELL = """SELECT w.* FROM werte w JOIN (SELECT quelle, berichtsland, partnerland, richtung, ware, zeitraum, ort, MAX(version) AS v
             FROM werte {wo} GROUP BY quelle, berichtsland, partnerland, richtung, ware, zeitraum, ort) m
             ON w.quelle = m.quelle AND w.berichtsland IS m.berichtsland AND w.partnerland IS m.partnerland AND w.richtung = m.richtung
             AND w.ware = m.ware AND w.zeitraum = m.zeitraum AND w.ort IS m.ort AND w.version = m.v"""


def aktuelle(wo: str = "", parameter=()) -> list[dict]:
    return abfrage(AKTUELL.format(wo=("WHERE " + wo) if wo else ""), parameter)
