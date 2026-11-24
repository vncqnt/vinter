r"""Erzeugt die App-Symbole für den Home-Bildschirm (iPhone, Android) und das Favicon in app/static/symbole/.

Zeichen (seit 25.11.2026, vom Nutzer aus Entwürfen gewählt): Mint-Fläche mit einem dunklen, kantigen "V" aus zwei
Balken; der rechte Balken liegt über dem linken, getrennt durch einen feinen Mint-Schnitt. Ohne Zusatzpaket außer
numpy: Polygone mit 4x4-Unterabtastung (glatte Kanten), PNG direkt über zlib geschrieben.
Start: .venv\Scripts\python.exe scripts\app_symbole.py
"""
import struct
import zlib
from pathlib import Path

import numpy as np

ZIEL = Path(__file__).resolve().parent.parent / "app" / "static" / "symbole"
MINT = np.array([0x7E, 0xE3, 0xAD], float)
DUNKEL = np.array([0x07, 0x0B, 0x08], float)
SS = 4
# Form in Einheitskoordinaten (0..1): Rand links/rechts, Balkenbreite, oben, unten, Breite des Schnitts
RAND, DICKE, OBEN, UNTEN, SCHNITT = 0.21, 0.16, 0.26, 0.76, 0.022


def _polygone() -> list[tuple[list, np.ndarray]]:
    m = 0.5
    links = [(RAND, OBEN), (RAND + DICKE, OBEN), (m + DICKE / 2, UNTEN), (m - DICKE / 2, UNTEN)]
    rechts = [(1 - RAND - DICKE, OBEN), (1 - RAND, OBEN), (m + DICKE / 2, UNTEN), (m - DICKE / 2, UNTEN)]
    schnitt = [(1 - RAND - DICKE - SCHNITT, OBEN), (1 - RAND, OBEN), (m + DICKE / 2, UNTEN), (m - DICKE / 2 - SCHNITT, UNTEN)]
    return [(links, DUNKEL), (schnitt, MINT), (rechts, DUNKEL)]


def bild(groesse: int, skala: float = 1.0) -> bytes:
    """skala < 1 verkleinert das Zeichen zur Mitte hin (maskierbare Android-Symbole brauchen Rand)."""
    o = (np.arange(SS) + 0.5) / SS
    yy, xx = np.meshgrid(np.arange(groesse), np.arange(groesse), indexing="ij")
    sx = (xx[..., None, None] + o[None, None, None, :]) / groesse
    sy = (yy[..., None, None] + o[None, None, :, None]) / groesse
    px = np.tile(MINT, (groesse, groesse, 1))
    for punkte, farbe in _polygone():
        p = 0.5 + (np.array(punkte, float) - 0.5) * skala
        richtung = 1 if np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1]) > 0 else -1
        drin = np.ones(sx.shape[:2] + (SS, SS), bool)
        for (x1, y1), (x2, y2) in zip(p, np.roll(p, -1, axis=0)):
            drin &= ((x2 - x1) * (sy - y1) - (y2 - y1) * (sx - x1)) * richtung >= 0
        a = drin.mean(axis=(2, 3))[..., None]
        px = px * (1 - a) + farbe * a
    z = np.clip(px.round(), 0, 255).astype(np.uint8)
    roh = b"".join(b"\x00" + z[y].tobytes() for y in range(groesse))

    def block(art, daten):
        return struct.pack(">I", len(daten)) + art + daten + struct.pack(">I", zlib.crc32(art + daten) & 0xFFFFFFFF)
    kopf = struct.pack(">IIBBBBB", groesse, groesse, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + block(b"IHDR", kopf) + block(b"IDAT", zlib.compress(roh, 9)) + block(b"IEND", b"")


def main():
    ZIEL.mkdir(parents=True, exist_ok=True)
    for name, groesse, skala in [("apple-touch-icon.png", 180, 1.0), ("symbol-192.png", 192, 1.0), ("symbol-512.png", 512, 1.0),
                                 ("symbol-maskierbar-512.png", 512, 0.8), ("favicon-32.png", 32, 1.15)]:
        (ZIEL / name).write_bytes(bild(groesse, skala))
        print(name)


if __name__ == "__main__":
    main()
