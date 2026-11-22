"""Punkt 31b: Handelsströme und Handels-Frühindikatoren.

Module
- laender.py:    Länder auf ISO 3166 (Alpha-3) vereinheitlichen.
- speicher.py:   SQLite mit Versionen (Revisionen), Rohdaten-Archiv, Quarantäne, Protokoll, Limit-Zähler.
- einlesen.py:   Einleser je Quelle (reine Funktionen, getestet in tests/test_handel.py).
- quellen.py:    Abruf je Quelle (Limits, Wiederholungen, ETag/Last-Modified), Kalender.
- plan.py:       Hintergrund-Thread, der nach dem Veröffentlichungskalender abruft.
- auswertung.py: Frühindikatoren, Handelsströme, Engstellen, Status.
Konfiguration: daten/markt/handel_quellen.yaml und daten/markt/warengruppen.yaml (Vorlagen in vorlagen/markt).
"""
