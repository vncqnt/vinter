"""Umzug vom PC auf den NUC: eigene Daten und gesammelte Marktdaten als eine Datei mitnehmen.

Export (am PC): ZIP mit
  markt/      alle gemeinsamen Marktdaten (bearbeitete YAML-Dateien, gesammelte Verläufe als SQLite, Handelsdaten)
  benutzer/   der Datenordner des angemeldeten Admins (Thesen, Journal, Layouts, Watchlist, TR-Export …)
  umzug.json  Beschreibung (Art, Datum, Programmversion, Dateiliste)
Nie dabei: .env, options.json, Keys und Konten (konten/), Zwischenspeicher (cache/).
SQLite-Dateien werden über die Backup-Schnittstelle kopiert, damit auch eine gerade benutzte Datenbank stimmig ist.

Import (am NUC): Die Datei wird geprüft (nur markt/ und benutzer/, keine Pfade nach außen, nichts Verbotenes)
und dann eingespielt, während Vinter läuft:
  - SQLite: Zeilen werden zusammengeführt (INSERT OR IGNORE), vorhandene Einträge am Ziel bleiben, nichts wird gelöscht.
    Tabellen ohne eindeutigen Schlüssel werden nur gefüllt, wenn sie am Ziel noch leer sind (sonst doppelte Zeilen).
  - andere Dateien: neu angelegt oder ersetzt; die alte Fassung kommt vorher nach umzug/sicherung_<Zeit>/.
"""
import json
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

ART = "vinter-umzug"
TEILE = ("markt", "benutzer")
VERBOTEN_NAMEN = {".env", "options.json", "keys.enc", "schluessel.bin", "konten.json"}
VERBOTEN_ENDUNGEN = ("-wal", "-shm", "-journal")
GRENZE = 2 * 1024 ** 3  # höchstens 2 GB entpackt


class UmzugFehler(Exception):
    pass


def _erlaubt(name: str) -> bool:
    p = PurePosixPath(name)
    return p.name not in VERBOTEN_NAMEN and not p.name.startswith(".env") and not p.name.endswith(VERBOTEN_ENDUNGEN)


def _ist_sqlite(pfad: Path) -> bool:
    try:
        with open(pfad, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _sqlite_kopie(quelle: Path, ziel: Path) -> None:
    """Stimmige Kopie einer (eventuell gerade benutzten) SQLite-Datenbank."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    q = sqlite3.connect(f"file:{quelle.as_posix()}?mode=ro", uri=True, timeout=60)
    z = sqlite3.connect(ziel)
    try:
        q.backup(z)
    finally:
        z.close()
        q.close()


# ---- Export ----

def export_bauen(ziel_zip: Path, markt: Path, benutzer: Path, programm: str = "") -> dict:
    """Schreibt die Umzugsdatei. Gibt die Beschreibung zurück."""
    ziel_zip.parent.mkdir(parents=True, exist_ok=True)
    zwischen = ziel_zip.parent / "export_sqlite"
    shutil.rmtree(zwischen, ignore_errors=True)
    dateien = []
    with zipfile.ZipFile(ziel_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for teil, ordner in (("markt", markt), ("benutzer", benutzer)):
            if not ordner.exists():
                continue
            for datei in sorted(ordner.rglob("*")):
                if not datei.is_file() or "__pycache__" in datei.parts:
                    continue
                name = f"{teil}/{datei.relative_to(ordner).as_posix()}"
                if not _erlaubt(name):
                    continue
                if _ist_sqlite(datei):
                    kopie = zwischen / name
                    _sqlite_kopie(datei, kopie)
                    z.write(kopie, name)
                else:
                    z.write(datei, name)
                dateien.append(name)
        info = {"art": ART, "version": 1, "erstellt": datetime.now().isoformat(timespec="seconds"),
                "programm": programm, "dateien": dateien}
        z.writestr("umzug.json", json.dumps(info, ensure_ascii=False, indent=1))
    shutil.rmtree(zwischen, ignore_errors=True)
    return info


# ---- Import ----

def _pruefen(z: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    try:
        info = json.loads(z.read("umzug.json"))
    except (KeyError, ValueError):
        raise UmzugFehler("Das ist keine Umzugsdatei von Vinter (umzug.json fehlt).")
    if info.get("art") != ART:
        raise UmzugFehler("Das ist keine Umzugsdatei von Vinter.")
    eintraege, summe = [], 0
    for e in z.infolist():
        if e.is_dir() or e.filename == "umzug.json":
            continue
        p = PurePosixPath(e.filename)
        if "\\" in e.filename or p.is_absolute() or ".." in p.parts or p.parts[0] not in TEILE or len(p.parts) < 2:
            raise UmzugFehler(f"Ungültiger Eintrag in der Datei: {e.filename}")
        if not _erlaubt(e.filename):
            continue  # z. B. eine .env: nie einspielen
        summe += e.file_size
        eintraege.append(e)
    if summe > GRENZE:
        raise UmzugFehler("Die Datei ist zu groß.")
    return eintraege


def _schluessel(v: sqlite3.Connection, tabelle: str) -> bool:
    """Hat die Tabelle am Ziel einen Primärschlüssel oder eindeutigen Index?"""
    if any(r[5] for r in v.execute(f'PRAGMA main.table_info("{tabelle}")')):
        return True
    return any(r[2] for r in v.execute(f'PRAGMA main.index_list("{tabelle}")'))


def sqlite_zusammenfuehren(quelle: Path, ziel: Path) -> dict:
    """Übernimmt alle Zeilen aus quelle in ziel, ohne vorhandene Zeilen zu ändern."""
    v = sqlite3.connect(ziel, timeout=60)
    neu = uebersprungen = 0
    try:
        v.execute("ATTACH DATABASE ? AS q", (str(quelle),))
        tabellen = v.execute("SELECT name, sql FROM q.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        for name, sql in tabellen:
            if not v.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?", (name,)).fetchone():
                v.execute(sql)
                for (isql,) in v.execute("SELECT sql FROM q.sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (name,)).fetchall():
                    try:
                        v.execute(isql)
                    except sqlite3.Error:
                        pass
            ziel_spalten = {r[1] for r in v.execute(f'PRAGMA main.table_info("{name}")')}
            spalten = [r[1] for r in v.execute(f'PRAGMA q.table_info("{name}")') if r[1] in ziel_spalten]
            if not spalten:
                continue
            liste = ", ".join(f'"{s}"' for s in spalten)
            vorher = v.total_changes
            if _schluessel(v, name):
                v.execute(f'INSERT OR IGNORE INTO main."{name}" ({liste}) SELECT {liste} FROM q."{name}"')
            elif not v.execute(f'SELECT 1 FROM main."{name}" LIMIT 1').fetchone():
                v.execute(f'INSERT INTO main."{name}" ({liste}) SELECT {liste} FROM q."{name}"')
            else:
                uebersprungen += 1
            neu += v.total_changes - vorher
        v.commit()
        v.execute("DETACH DATABASE q")
    finally:
        v.close()
    return {"zeilen_neu": neu, "tabellen_uebersprungen": uebersprungen}


def einspielen(zip_pfad: Path, markt: Path, benutzer: Path, arbeit: Path) -> dict:
    """Spielt eine Umzugsdatei ein. arbeit = Ordner für Zwischenstand und Sicherungen."""
    zeit = datetime.now().strftime("%Y%m%d_%H%M%S")
    eingang = arbeit / f"eingang_{zeit}"
    sicherung = arbeit / f"sicherung_{zeit}"
    bericht = {"neu": 0, "ersetzt": 0, "gleich": 0, "datenbanken": 0, "zeilen_neu": 0, "tabellen_uebersprungen": 0,
               "sicherung": None}
    try:
        with zipfile.ZipFile(zip_pfad) as z:
            eintraege = _pruefen(z)
            for e in eintraege:
                z.extract(e, eingang)
    except zipfile.BadZipFile:
        raise UmzugFehler("Die Datei ist beschädigt oder kein ZIP.")
    try:
        for e in eintraege:
            teil, rest = e.filename.split("/", 1)
            quelle = eingang / e.filename
            ziel = (markt if teil == "markt" else benutzer) / rest
            alt = sicherung / e.filename
            ziel.parent.mkdir(parents=True, exist_ok=True)
            if _ist_sqlite(quelle):
                bericht["datenbanken"] += 1
                if not ziel.exists():
                    shutil.copyfile(quelle, ziel)
                    bericht["neu"] += 1
                    continue
                _sqlite_kopie(ziel, alt)
                erg = sqlite_zusammenfuehren(quelle, ziel)
                bericht["zeilen_neu"] += erg["zeilen_neu"]
                bericht["tabellen_uebersprungen"] += erg["tabellen_uebersprungen"]
                continue
            if ziel.exists():
                if ziel.read_bytes() == quelle.read_bytes():
                    bericht["gleich"] += 1
                    continue
                alt.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ziel, alt)
                bericht["ersetzt"] += 1
            else:
                bericht["neu"] += 1
            os.replace(quelle, ziel)
    finally:
        shutil.rmtree(eingang, ignore_errors=True)
    if sicherung.exists():
        bericht["sicherung"] = sicherung.name
    return bericht
