r"""Erstellt die öffentliche Kopie für GitHub (Home-Assistant-Add-on-Repository) und lädt sie hoch.

Warum eine eigene Kopie: Das private Repository enthält Projektnotizen (PROJEKT.md usw.) mit Depot-Ergebnissen
und deren ganze Geschichte. Öffentlich wird nur der Code aus der Liste ERLAUBT, mit eigener, sauberer Geschichte.

Aufruf (aus dem Projektordner):
  .venv\Scripts\python.exe scripts\veroeffentlichen.py            Kopie bauen, prüfen, committen, hochladen
  .venv\Scripts\python.exe scripts\veroeffentlichen.py --trocken  nur bauen und prüfen, nichts hochladen

Genommen wird immer der letzte Commit (git archive HEAD), nie halbfertige Änderungen.
Vor dem Hochladen: Prüfung auf Muster (scripts/pruefe_repo.py) und auf die echten Werte aus der .env,
ohne sie je auszugeben.
"""
import io
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from dotenv import dotenv_values

PROJEKT = Path(__file__).resolve().parent.parent
ZIEL = PROJEKT.parent / "vinter-oeffentlich"
ADRESSE = "https://github.com/vncqnt/vinter.git"
ERLAUBT = ["app", "vorlagen", "tests", "scripts", "config.yaml", "repository.yaml", "Dockerfile", "run.sh",
           ".dockerignore", ".gitattributes", ".gitignore", "requirements.txt", "DOCS.md", "README.md", "start.bat"]


def git(*args, cwd=ZIEL, pruefen=True):
    return subprocess.run(["git", *args], cwd=cwd, check=pruefen, capture_output=True, text=True)


def bauen() -> None:
    if not (ZIEL / ".git").exists():
        ZIEL.mkdir(exist_ok=True)
        git("init", "-b", "main")
        git("config", "user.name", "Vinter")
        git("config", "user.email", "vinter@localhost")
        git("remote", "add", "origin", ADRESSE)
    for teil in ZIEL.iterdir():          # alte Kopie leeren, nur .git bleibt
        if teil.name != ".git":
            shutil.rmtree(teil) if teil.is_dir() else teil.unlink()
    archiv = subprocess.run(["git", "archive", "--format=tar", "HEAD", *ERLAUBT], cwd=PROJEKT, check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archiv)) as tar:
        tar.extractall(ZIEL, filter="data")


def pruefen() -> bool:
    ok = True
    werte = [v for v in dotenv_values(PROJEKT / ".env").values() if v and len(v) >= 8] if (PROJEKT / ".env").exists() else []
    for datei in ZIEL.rglob("*"):
        if ".git" in datei.parts or not datei.is_file():
            continue
        try:
            text = datei.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(w in text for w in werte):
            print(f"FUND: ein Wert aus der .env steht in {datei.relative_to(ZIEL)}")
            ok = False
    for name in ("PROJEKT.md", "QUANT_ROADMAP.md", "OFFENE_FRAGEN.md", "OFFENE_PUNKTE.md", "CLAUDE.md", "daten", ".env"):
        if (ZIEL / name).exists():
            print(f"FUND: {name} gehört nicht in die öffentliche Kopie")
            ok = False
    r = subprocess.run([sys.executable, "scripts/pruefe_repo.py"], cwd=ZIEL, capture_output=True, text=True)
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "")
    return ok and r.returncode == 0


def main() -> int:
    bauen()
    if not pruefen():
        print("Abgebrochen: nichts hochgeladen.")
        return 1
    git("add", "-A")
    if git("diff", "--cached", "--quiet", pruefen=False).returncode == 0:
        print("Keine Änderungen seit dem letzten Hochladen.")
    else:
        stand = git("log", "-1", "--format=%h %s", cwd=PROJEKT).stdout.strip()
        git("commit", "-m", f"Vinter-Stand {stand.split()[0]}")
        print("Neuer Stand vorbereitet.")
    if "--trocken" in sys.argv:
        print(f"Trockenlauf: Kopie liegt in {ZIEL}, nichts hochgeladen.")
        return 0
    r = subprocess.run(["git", "push", "-u", "origin", "main"], cwd=ZIEL)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
