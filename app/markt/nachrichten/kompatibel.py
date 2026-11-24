"""Für Teile von Vinter, die Einzelmeldungen statt Geschichten brauchen (Markttreiber, Reden & Zitate,
Leiste oben). Liefert Meldungen im Format des alten Feeds: titel, zeit, link, quellen, ticker.

Nur öffentliche Meldungen (keine aus Telegram oder dem eigenen Eingang), ohne Ausgefiltertes.
"""
import json
from datetime import datetime, timedelta, timezone

from . import geschichten, speicher


def meldungen(stunden: int = 30, quellen: set[str] | None = None) -> list[dict]:
    grenze = (datetime.now(timezone.utc) - timedelta(hours=stunden)).isoformat()
    db = speicher.verbinden()
    try:
        zeilen = db.execute(
            "SELECT quelle, titel, link, zeit, gesehen, extrakt FROM meldungen WHERE nur_fuer IS NULL AND filter IS NULL "
            "AND gesehen >= ? ORDER BY gesehen DESC LIMIT 3000", (grenze,)).fetchall()
    finally:
        db.close()
    aus = []
    for r in zeilen:
        if quellen and r["quelle"] not in quellen:
            continue
        x = json.loads(r["extrakt"]) if r["extrakt"] else {}
        aus.append({"titel": r["titel"], "zeit": r["zeit"] or r["gesehen"], "link": r["link"], "quellen": [r["quelle"]],
                    "ticker": x.get("ticker", []), "tags": [], "wichtig": False})
    return aus


def schlagzeile() -> dict | None:
    """Für die Leiste oben: die wichtigste Geschichte der letzten 6 Stunden ab Wichtigkeit 45,
    sonst die neueste ab 30 (öffentliche Quellen)."""
    stories = [s for s in geschichten.liste("", 6) if not s.get("nur_fuer")]
    s = next((s for s in stories if s["score"] >= 45), None) or next(
        (s for s in sorted(stories, key=lambda s: s["letztes"], reverse=True) if s["score"] >= 30), None)
    if not s:
        return None
    m = next((m for m in s["meldungen"] if m["titel"] == s["titel"]), s["meldungen"][0])
    return {"titel": s["titel"], "quelle": m["quelle"], "zeit": s["letztes"], "link": m["link"]}
