"""Newsfeed, neu aufgebaut (ab 24.11.2026) als Pipeline: Auslöser → Extraktion → Nachrecherche → Bewertung.

speicher.py   SQLite-Datenbank daten/markt/nachrichten.sqlite: Meldungen, Quellen-Zustand
quellen.py    Konfiguration aus daten/markt/news_quellen.yaml (Version 2)
abruf.py      schnelle Auslöser-Quellen (RSS/Atom) mit bedingten Anfragen und Backoff, Hintergrund-Thread
eingang.py    manueller Eingang (Text oder Link, im Tool und per iOS-Kurzbefehl mit persönlichem Token)

Ersetzt den alten Feed (app/markt/squawk.py), der alles nach Thesen-Relevanz gefiltert hat.
"""
