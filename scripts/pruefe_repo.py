"""Prüft vor einem Commit, dass keine Keys oder persönlichen Daten ins Repository gelangen.

Aufruf:
  python scripts/pruefe_repo.py                       prüft alles, was in einen Commit käme
  python scripts/pruefe_repo.py --hook-installieren   richtet die Prüfung als Git-Hook ein:
                                                     danach läuft sie bei jedem "git commit" von selbst

Geprüft wird:
  1. verbotene Dateien: .env, daten/, CSV-Exporte, Datenbanken, options.json
  2. die echten Werte aus deiner .env: Steht einer davon irgendwo im Code, schlägt die Prüfung an
  3. typische Muster: Schlüssel-Zuweisungen mit langem Wert, IBANs, E-Mail-Adressen
Gefundene Werte werden nie ausgegeben, nur Datei, Zeile und welche Art von Treffer.
Rückgabe 0 = alles sauber, 1 = Fund (der Commit wird dann abgebrochen).
"""
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

PROJEKT = Path(__file__).resolve().parent.parent
VERBOTEN = [
    (re.compile(r"(^|/)\.env(\..*)?$"), ".env-Datei"),
    (re.compile(r"(^|/)options\.json$"), "Add-on-Optionen"),
    (re.compile(r"^daten/"), "Datenverzeichnis"),
    (re.compile(r"\.csv$", re.I), "CSV-Datei (z. B. Trade-Republic-Export)"),
    (re.compile(r"\.sqlite(-journal)?$", re.I), "Datenbank"),
    (re.compile(r"(^|/)tr_export/"), "Trade-Republic-Export"),
]
MUSTER = [
    # Wert mit 16+ Zeichen, aber kein Variablenname wie "key_env: COMTRADE_API_KEY" (Großbuchstaben mit _ dazwischen)
    (re.compile(r"(?i)\b[A-Z0-9_]*(KEY|TOKEN|SECRET|PASSWORT|PASSWORD)[A-Z0-9_]*\s*[:=]\s*['\"]?(?-i:(?![A-Z0-9]+(?:_[A-Z0-9]+)+\b)[A-Za-z0-9_\-]{16,})"), "Schlüssel mit Wert"),
    (re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}\b"), "IBAN-ähnliche Nummer"),
    (re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.(com|org|de)\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "E-Mail-Adresse"),
]
UEBERSPRINGEN = {".venv", "__pycache__", ".git", "daten", "node_modules"}
TEXT_ENDUNGEN = {".py", ".js", ".html", ".css", ".md", ".yaml", ".yml", ".json", ".txt", ".bat", ".sh", ".toml", ".cfg", ".ini", ""}


def _dateien() -> list[str]:
    """Im Git-Repo: was gerade für den Commit vorgemerkt ist (oder alle bekannten Dateien).
    Ohne Git: alle Dateien im Projekt außer den ausgenommenen Ordnern."""
    if (PROJEKT / ".git").exists():
        vorgemerkt = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                                    cwd=PROJEKT, capture_output=True, text=True).stdout.split()
        if vorgemerkt or "--hook" in sys.argv:
            return vorgemerkt
        return subprocess.run(["git", "ls-files"], cwd=PROJEKT, capture_output=True, text=True).stdout.split()
    alle = [str(p.relative_to(PROJEKT)).replace("\\", "/") for p in PROJEKT.rglob("*")
            if p.is_file() and not (set(p.relative_to(PROJEKT).parts) & UEBERSPRINGEN)]
    return [d for d in alle if not _ignoriert(d)]


def _ignoriert(name: str) -> bool:
    """Grobe Nachbildung von .gitignore für den Fall ohne Git (einfache Muster reichen hier)."""
    datei = PROJEKT / ".gitignore"
    if not datei.exists():
        return False
    for muster in datei.read_text(encoding="utf-8").splitlines():
        muster = muster.strip()
        if not muster or muster.startswith("#"):
            continue
        if muster.endswith("/"):
            if name.startswith(muster) or f"/{muster}" in f"/{name}":
                return True
        elif fnmatch(name.rsplit("/", 1)[-1], muster) or fnmatch(name, muster):
            return True
    return False


def _geheime_werte() -> dict[str, str]:
    """Die echten Werte aus den .env-Dateien, damit sie nirgends sonst auftauchen."""
    werte = {}
    for datei in (PROJEKT / ".env", PROJEKT / "daten" / ".env"):
        if datei.exists():
            for zeile in datei.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "=" in zeile and not zeile.lstrip().startswith("#"):
                    name, wert = zeile.split("=", 1)
                    wert = wert.strip().strip("'\"")
                    if len(wert) >= 8:
                        werte[name.strip()] = wert
    return werte


def pruefen() -> list[str]:
    funde = []
    geheim = _geheime_werte()
    for name in _dateien():
        for muster, art in VERBOTEN:
            if muster.search(name):
                funde.append(f"{name}: {art} gehört nicht ins Repository")
                break
        else:
            pfad = PROJEKT / name
            if pfad.suffix.lower() not in TEXT_ENDUNGEN or not pfad.exists() or pfad.stat().st_size > 2_000_000:
                continue
            text = pfad.read_text(encoding="utf-8", errors="ignore")
            for key, wert in geheim.items():
                if wert in text:
                    funde.append(f"{name}: enthält den Wert von {key} aus deiner .env")
            for nummer, zeile in enumerate(text.splitlines(), start=1):
                for muster, art in MUSTER:
                    if muster.search(zeile):
                        funde.append(f"{name}:{nummer}: {art}")
    return funde


def hook_installieren() -> None:
    hooks = PROJEKT / ".git" / "hooks"
    if not hooks.exists():
        print("Hier gibt es noch kein Git-Repository (git init fehlt).")
        sys.exit(1)
    (hooks / "pre-commit").write_text(
        "#!/bin/sh\n# Prüft vor jedem Commit auf Keys und persönliche Daten (scripts/pruefe_repo.py)\n"
        '".venv/Scripts/python.exe" scripts/pruefe_repo.py --hook 2>/dev/null || python scripts/pruefe_repo.py --hook\n',
        encoding="utf-8")
    print("Git-Hook eingerichtet: Die Prüfung läuft jetzt bei jedem Commit.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--hook-installieren" in sys.argv:
        hook_installieren()
        sys.exit(0)
    gefunden = pruefen()
    if gefunden:
        print("STOPP: Diese Dateien oder Stellen dürfen nicht ins Repository:")
        for f in gefunden:
            print("  -", f)
        print("Werte werden absichtlich nicht angezeigt. Datei prüfen, bereinigen oder in .gitignore aufnehmen.")
        sys.exit(1)
    print("Sauber: keine Keys und keine persönlichen Daten gefunden.")
