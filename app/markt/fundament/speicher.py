"""Eigener Speicher der Fundamentalanalyse: daten/markt/fundament.sqlite (lässt sich nicht nachladen).

- schaetzungen: ab Start jeden Tag die Gewinn- und Umsatzschätzungen (laufendes und nächstes Geschäftsjahr)
  je Wert, weil es deren Historie nicht gratis gibt. Dazu die Schätzung von vor 30 und 90 Tagen laut Yahoo.
- beobachtet: Werte, die jemand angesehen hat; sie kommen 60 Tage lang in den täglichen Abruf.
"""
import sqlite3
import threading
from datetime import date, datetime, timedelta

from ...konfig import MARKT_DATEN

DATEI = MARKT_DATEN / "fundament.sqlite"
_sperre = threading.Lock()


def db() -> sqlite3.Connection:
    MARKT_DATEN.mkdir(parents=True, exist_ok=True)
    v = sqlite3.connect(DATEI, timeout=30)
    v.row_factory = sqlite3.Row
    v.execute("""CREATE TABLE IF NOT EXISTS schaetzungen (ticker TEXT, tag TEXT, sektor TEXT, im_index INTEGER, boersenwert REAL, kurs REAL,
                 eps0y REAL, eps1y REAL, eps1y_30 REAL, eps1y_90 REAL, umsatz0y REAL, umsatz1y REAL, fwd_kgv REAL, eps_ttm REAL,
                 PRIMARY KEY (ticker, tag))""")
    v.execute("CREATE TABLE IF NOT EXISTS beobachtet (ticker TEXT PRIMARY KEY, zuletzt TEXT)")
    return v


def beobachten(ticker: str) -> None:
    with _sperre, db() as v:
        v.execute("INSERT OR REPLACE INTO beobachtet VALUES (?, ?)", (ticker.upper(), datetime.now().isoformat(timespec="seconds")))


def beobachtete(tage: int = 60) -> list[str]:
    grenze = (datetime.now() - timedelta(days=tage)).isoformat()
    with _sperre, db() as v:
        return [z[0] for z in v.execute("SELECT ticker FROM beobachtet WHERE zuletzt >= ?", (grenze,))]


def schaetzung_speichern(zeilen: list[dict]) -> None:
    felder = ("ticker", "tag", "sektor", "im_index", "boersenwert", "kurs", "eps0y", "eps1y", "eps1y_30", "eps1y_90", "umsatz0y", "umsatz1y", "fwd_kgv", "eps_ttm")
    with _sperre, db() as v:
        v.executemany(f"INSERT OR REPLACE INTO schaetzungen VALUES ({','.join('?' * len(felder))})", [tuple(z.get(f) for f in felder) for z in zeilen])


def tag_vorhanden(tag: str | None = None) -> int:
    with _sperre, db() as v:
        return v.execute("SELECT COUNT(*) FROM schaetzungen WHERE tag = ? AND im_index = 1", (tag or date.today().isoformat(),)).fetchone()[0]


def letzte_schaetzungen(index_nur: bool = False) -> list[dict]:
    with _sperre, db() as v:
        tag = v.execute(f"SELECT MAX(tag) FROM schaetzungen {'WHERE im_index = 1' if index_nur else ''}").fetchone()[0]
        if not tag:
            return []
        return [dict(z) for z in v.execute(f"SELECT * FROM schaetzungen WHERE tag = ? {'AND im_index = 1' if index_nur else ''}", (tag,))]


def verlauf(ticker: str) -> list[dict]:
    with _sperre, db() as v:
        return [dict(z) for z in v.execute("SELECT tag, eps0y, eps1y, umsatz0y, umsatz1y, fwd_kgv FROM schaetzungen WHERE ticker = ? ORDER BY tag", (ticker.upper(),))]
