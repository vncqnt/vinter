r"""Erzeugt die App-Symbole für den Home-Bildschirm (iPhone, Android) und das Favicon in app/static/symbole/.

Zeichen (seit 24.11.2026): ein "V" als Kurslinie. Erst fällt der Kurs, dann steigt er über den Start hinaus;
am Ende sitzt der goldene Live-Punkt. Mint auf dem fast schwarzen Vinter-Grund, ohne Schrift, damit es auch
als Favicon mit 32 Pixeln lesbar bleibt. Ohne Zusatzpaket: Die PNGs werden direkt geschrieben (zlib),
die Linie wird über den Abstand zu den Strecken mit weicher Kante gerechnet.
Start: .venv\Scripts\python.exe scripts\app_symbole.py
"""
import struct
import zlib
from pathlib import Path

ZIEL = Path(__file__).resolve().parent.parent / "app" / "static" / "symbole"
GRUND = (0x07, 0x0B, 0x08)
MINT = (0x7E, 0xE3, 0xAD)
GOLD = (0xE8, 0xC5, 0x6A)
# Kurslinie in Anteilen des Zeichenfelds (0..1): links oben, Tief, rechts höher als der Start
LINIE = [(0.10, 0.16), (0.46, 0.86), (0.84, 0.12)]
PUNKT = LINIE[-1]


def _abstand(px, py, a, b):
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5


def _mischen(unten, oben, deckung):
    return tuple(round(u + (o - u) * deckung) for u, o in zip(unten, oben))


def bild(groesse: int, anteil: float) -> bytes:
    """anteil: wie viel der Kantenlänge das Zeichen einnimmt (maskierbare Symbole brauchen Rand)."""
    feld = groesse * anteil
    start = (groesse - feld) / 2
    punkte = [(start + x * feld, start + y * feld) for x, y in LINIE]
    breite = max(2.2, feld * 0.105) / 2           # halbe Strichstärke
    pr = max(2.0, feld * 0.085)                   # Live-Punkt
    ring = pr + max(1.0, feld * 0.045)            # dunkler Spalt um den Punkt
    px0, py0 = punkte[-1]
    zeilen = []
    for y in range(groesse):
        zeile = bytearray([0])
        for x in range(groesse):
            mx, my = x + 0.5, y + 0.5
            d = min(_abstand(mx, my, punkte[i], punkte[i + 1]) for i in range(len(punkte) - 1))
            farbe = _mischen(GRUND, MINT, max(0.0, min(1.0, breite - d + 0.5)))
            dp = ((mx - px0) ** 2 + (my - py0) ** 2) ** 0.5
            farbe = _mischen(farbe, GRUND, max(0.0, min(1.0, ring - dp + 0.5)))
            farbe = _mischen(farbe, GOLD, max(0.0, min(1.0, pr - dp + 0.5)))
            zeile += bytes(farbe)
        zeilen.append(bytes(zeile))

    def block(art, daten):
        return struct.pack(">I", len(daten)) + art + daten + struct.pack(">I", zlib.crc32(art + daten) & 0xFFFFFFFF)
    kopf = struct.pack(">IIBBBBB", groesse, groesse, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + block(b"IHDR", kopf) + block(b"IDAT", zlib.compress(b"".join(zeilen), 9)) + block(b"IEND", b"")


def main():
    ZIEL.mkdir(parents=True, exist_ok=True)
    for name, groesse, anteil in [("apple-touch-icon.png", 180, 0.60), ("symbol-192.png", 192, 0.60),
                                  ("symbol-512.png", 512, 0.60), ("symbol-maskierbar-512.png", 512, 0.48),
                                  ("favicon-32.png", 32, 0.84)]:
        (ZIEL / name).write_bytes(bild(groesse, anteil))
        print(name)


if __name__ == "__main__":
    main()
