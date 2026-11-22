"""Hintergrund-Arbeiten für "Großes Geld": einmal beim Start und danach alle 6 Stunden.

Lädt, was lange dauert, damit die Seiten sofort antworten: Kurse aller S&P-500-Werte, Börsenwert und
KGV aller Mitglieder von S&P 500, Nasdaq 100, DAX und Euro Stoxx 50 (hängt einen Tag an den
KGV-Verlauf an), die FINRA-Short-Dateien und die 13F-Meldungen der Fonds.
"""
import threading
import time

from . import indizes, sec_fonds, signale, stil

_gestartet = False


def _runde() -> None:
    for schritt in (indizes.sp500_kurse, signale.finra_vorladen, lambda: sec_fonds.fonds([]),
                    *[(lambda i=i: stil.kgv_aktualisieren(i)) for i in indizes.INDIZES]):
        try:
            schritt()
        except Exception:
            pass  # nächste Runde versucht es wieder


def _schleife() -> None:
    time.sleep(20)  # erst den Server in Ruhe starten lassen
    from ..status import job
    while True:
        try:
            with job("Smart Money"):
                _runde()
        except Exception:
            pass
        time.sleep(6 * 3600)


def starte() -> None:
    global _gestartet
    if not _gestartet:
        _gestartet = True
        threading.Thread(target=_schleife, daemon=True, name="grosses-geld").start()
