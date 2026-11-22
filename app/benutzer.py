"""Ein Benutzer und wo seine persönlichen Daten liegen.

Jede Funktion, die persönliche Daten liest oder schreibt, bekommt einen Benutzer übergeben.
So kann später jeder Freund sein eigenes Depot haben, ohne dass sich etwas vermischt.
"""
import re
from dataclasses import dataclass
from pathlib import Path

from .konfig import BENUTZER_DATEN, CACHE

_GUELTIG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class BenutzerFehler(ValueError):
    pass


@dataclass(frozen=True)
class Benutzer:
    name: str

    def __post_init__(self):
        # Der Name wird Teil eines Ordnerpfads: nur Kleinbuchstaben, Ziffern, - und _, damit
        # niemand mit "../" in fremde Ordner gelangt.
        if not _GUELTIG.match(self.name):
            raise BenutzerFehler("Ungültiger Benutzername.")

    @property
    def ordner(self) -> Path:
        return BENUTZER_DATEN / self.name

    @property
    def cache(self) -> Path:
        return CACHE / "benutzer" / f"{self.name}.sqlite"

    def datei(self, *teile: str) -> Path:
        """Pfad zu einer Datei im persönlichen Ordner, z. B. b.datei("steuer.yaml")."""
        return self.ordner.joinpath(*teile)
