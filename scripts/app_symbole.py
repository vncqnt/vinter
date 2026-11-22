"""Erzeugt die App-Symbole für den Home-Bildschirm (iPhone, Android) in app/static/symbole/.

Ein "V" aus LED-Punkten wie die Wortmarke, Mint auf dem dunklen Vinter-Grund. Ohne Zusatzpaket: Die PNGs
werden direkt geschrieben (zlib), die Punkte mit weicher Kante gerechnet.
Start: .venv\\Scripts\\python.exe scripts\\app_symbole.py
"""
import struct
import zlib
from pathlib import Path

ZIEL = Path(__file__).resolve().parent.parent / "app" / "static" / "symbole"
GRUND = (0x07, 0x0B, 0x08)
MINT = (0x7E, 0xE3, 0xAD)
AUS = (0x1A, 0x22, 0x1D)  # ausgeschaltete LEDs, ganz leicht sichtbar
V = ["X.....X",
     "X.....X",
     ".X...X.",
     ".X...X.",
     "..X.X..",
     "..X.X..",
     "...X..."]


def bild(groesse: int, anteil: float) -> bytes:
    """anteil: wie viel der Kantenlänge das Punktraster einnimmt (maskierbare Symbole brauchen Rand)."""
    n = len(V)
    feld = groesse * anteil
    schritt = feld / n
    radius = schritt * 0.36
    start = (groesse - feld) / 2
    zeilen = []
    for y in range(groesse):
        zeile = bytearray([0])  # Filter "none"
        for x in range(groesse):
            farbe = GRUND
            gx, gy = (x + 0.5 - start) / schritt, (y + 0.5 - start) / schritt
            i, j = int(gy), int(gx)
            if 0 <= i < n and 0 <= j < n:
                cx, cy = start + (j + 0.5) * schritt, start + (i + 0.5) * schritt
                d = ((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2) ** 0.5
                deckung = max(0.0, min(1.0, radius - d + 0.5))
                if deckung:
                    an = MINT if V[i][j] == "X" else AUS
                    farbe = tuple(round(g + (a - g) * deckung) for g, a in zip(GRUND, an))
            zeile += bytes(farbe)
        zeilen.append(bytes(zeile))
    def block(art, daten):
        return struct.pack(">I", len(daten)) + art + daten + struct.pack(">I", zlib.crc32(art + daten) & 0xFFFFFFFF)
    kopf = struct.pack(">IIBBBBB", groesse, groesse, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + block(b"IHDR", kopf) + block(b"IDAT", zlib.compress(b"".join(zeilen), 9)) + block(b"IEND", b"")


def main():
    ZIEL.mkdir(parents=True, exist_ok=True)
    for name, groesse, anteil in [("apple-touch-icon.png", 180, 0.62), ("symbol-192.png", 192, 0.62),
                                  ("symbol-512.png", 512, 0.62), ("symbol-maskierbar-512.png", 512, 0.5),
                                  ("favicon-32.png", 32, 0.86)]:
        (ZIEL / name).write_bytes(bild(groesse, anteil))
        print(name)


if __name__ == "__main__":
    main()
