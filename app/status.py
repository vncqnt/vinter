"""Status-Seite: Läuft Vinter rund? (Version, Hintergrund-Jobs, Datenquellen, Speicher, KI, letzte Fehler)

Gesammelt wird nur im Speicher des laufenden Servers (nach einem Neustart beginnt die Zählung neu):
- job(): Hintergrund-Jobs melden Start, Ende, Dauer und Fehler (Markttreiber, Smart Money, Volatilität …)
- Netz: jede Anfrage über requests und jeder Yahoo-Abruf (yfinance) wird je Quelle gezählt: Anfragen und Fehler
  heute, letzter Erfolg, letzter Fehler mit kurzem Grund. Keys oder Adressen mit Key werden nie gespeichert.
- letzte Fehler: die letzten 50 Meldungen aus Jobs und Abrufen
"""
import os
import platform
import shutil
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import date, datetime
from urllib.parse import urlsplit

from .konfig import ANMELDUNG, DATEN, PROGRAMM

VERSION = "0.9.1 (22.11.2026)"
START = datetime.now()
_sperre = threading.Lock()
_jobs: dict[str, dict] = {}
_netz: dict[str, dict] = {}
_fehler: deque = deque(maxlen=50)

# Bekannte Hintergrund-Jobs, damit sie auch erscheinen, bevor sie einmal gelaufen sind
JOBS = {
    "Markttreiber": "alle 30 Minuten zur Handelszeit und nach Börsenschluss (S&P 500, Nasdaq 100, DAX)",
    "Smart Money": "alle 6 Stunden: S&P-500-Kurse, KGVs, FINRA, 13F",
    "Volatilitätsflächen": "alle 10 Minuten geprüft: Schlussfläche nach US-Börsenschluss, live alle 30 Minuten",
    "Konsens sichern": "alle 6 Stunden: Analysten-Erwartung vor jedem Termin (für die Zahlen-Auswertung)",
    "Insider-Cluster": "einmal am Tag: Form 4 aller S&P-500- und Nasdaq-100-Werte (SEC)",
}
# Hostnamen in lesbare Quellen übersetzen
QUELLEN = {
    "finnhub.io": "Finnhub", "financialmodelingprep.com": "FMP", "www.sec.gov": "SEC", "data.sec.gov": "SEC", "efts.sec.gov": "SEC",
    "api.nasdaq.com": "nasdaq.com", "news.google.com": "Google News", "fred.stlouisfed.org": "FRED", "api.census.gov": "US-Zoll (Census)",
    "comtradeapi.un.org": "UN Comtrade", "english.customs.gov.cn": "Zoll China", "investor.tsmc.com": "TSMC",
    "services9.arcgis.com": "IMF PortWatch", "www.gdacs.org": "GDACS (Unwetter)", "earthquake.usgs.gov": "USGS (Erdbeben)",
    "www.financialjuice.com": "FinancialJuice", "api.manifold.markets": "Manifold", "markets.newyorkfed.org": "New York Fed", "www.federalreserve.gov": "Federal Reserve",
    "en.wikipedia.org": "Wikipedia", "www.ici.org": "ICI", "www.finra.org": "FINRA", "cdn.finra.org": "FINRA", "www.cftc.gov": "CFTC",
    "publicreporting.cftc.gov": "CFTC",
}


def _quelle(url: str) -> str:
    host = urlsplit(str(url)).hostname or "?"
    if host.endswith("yahoo.com"):
        return "Yahoo Finance"
    return QUELLEN.get(host, host)


def _fehler_text(f: BaseException) -> str:
    text = f"{type(f).__name__}: {f}"
    # Adressen können Keys enthalten (…&apikey=…): nur Typ und den Teil vor einer Adresse zeigen
    for marke in ("http://", "https://", "apikey", "api_key", "token", "key="):
        if marke in text.lower():
            text = text[: text.lower().index(marke)].rstrip(" :(") or type(f).__name__
    return text[:160]


def _fehler_merken(quelle: str, text: str) -> None:
    _fehler.appendleft({"zeit": datetime.now().isoformat(timespec="seconds"), "quelle": quelle, "text": text})


# ---- Jobs ----

@contextmanager
def job(name: str):
    """with job("Markttreiber"): … meldet Start, Ende und Fehler (der Fehler wird weitergereicht)."""
    start = time.time()
    with _sperre:
        j = _jobs.setdefault(name, {"laeufe": 0, "fehler": 0})
        j.update(laeuft=True, start=datetime.now().isoformat(timespec="seconds"))
    try:
        yield
    except Exception as f:
        with _sperre:
            j.update(ok=False, letzter_fehler=_fehler_text(f), fehler=j["fehler"] + 1)
        _fehler_merken(name, _fehler_text(f))
        raise
    else:
        with _sperre:
            j.update(ok=True)
    finally:
        with _sperre:
            j.update(laeuft=False, ende=datetime.now().isoformat(timespec="seconds"), dauer=round(time.time() - start, 1), laeufe=j["laeufe"] + 1)


def hinweis(name: str, text: str) -> None:
    """Zusatz zum letzten Lauf, z. B. "3 von 12 Werten ohne Daten"."""
    with _sperre:
        _jobs.setdefault(name, {"laeufe": 0, "fehler": 0})["hinweis"] = text


# ---- Netz ----

def _zaehlen(quelle: str, ok: bool, grund: str | None = None, dauer: float | None = None) -> None:
    heute = date.today().isoformat()
    with _sperre:
        n = _netz.setdefault(quelle, {"tag": heute, "anfragen": 0, "fehler": 0})
        if n["tag"] != heute:
            n.update(tag=heute, anfragen=0, fehler=0)
        n["anfragen"] += 1
        if ok:
            n["letzter_ok"] = datetime.now().isoformat(timespec="seconds")
            if dauer is not None:
                n["dauer"] = round(dauer, 2)
        else:
            n["fehler"] += 1
            n["letzter_fehler"] = datetime.now().isoformat(timespec="seconds")
            n["grund"] = grund
    if not ok:
        _fehler_merken(quelle, grund or "Fehler")


_beobachtet = [False]


def beobachte_netz() -> None:
    """Einmal beim Start: zählt alle Abrufe über requests und yfinance (Inhalte und Adressen werden nicht gespeichert)."""
    if _beobachtet[0]:
        return
    _beobachtet[0] = True
    import requests
    original = requests.Session.request

    def anfrage(self, method, url, *args, **kwargs):
        start = time.time()
        try:
            antwort = original(self, method, url, *args, **kwargs)
        except Exception as f:
            _zaehlen(_quelle(url), False, _fehler_text(f))
            raise
        if antwort.status_code >= 400:
            _zaehlen(_quelle(url), False, f"HTTP {antwort.status_code}")
        else:
            _zaehlen(_quelle(url), True, dauer=time.time() - start)
        return antwort
    requests.Session.request = anfrage

    try:
        import yfinance as yf
        hist = yf.Ticker.history
        down = yf.download

        def history(self, *args, **kwargs):
            try:
                df = hist(self, *args, **kwargs)
            except Exception as f:
                _zaehlen("Yahoo Finance", False, _fehler_text(f))
                raise
            _zaehlen("Yahoo Finance", df is not None and not getattr(df, "empty", True), None if df is not None and not df.empty else f"keine Kurse ({self.ticker})")
            return df

        def download(*args, **kwargs):
            try:
                df = down(*args, **kwargs)
            except Exception as f:
                _zaehlen("Yahoo Finance", False, _fehler_text(f))
                raise
            _zaehlen("Yahoo Finance", df is not None and not getattr(df, "empty", True), "keine Kurse (Sammelabruf)")
            return df
        yf.Ticker.history = history
        yf.download = download
    except Exception:
        pass


# ---- System ----

def _speicher_prozess() -> int | None:
    """Arbeitsspeicher des Servers in Bytes (Windows und Linux, ohne Zusatzpaket)."""
    try:
        if sys.platform.startswith("linux"):
            for zeile in open("/proc/self/status", encoding="utf-8"):
                if zeile.startswith("VmRSS:"):
                    return int(zeile.split()[1]) * 1024
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc = PMC(); pmc.cb = ctypes.sizeof(PMC)
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
            if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                return int(pmc.WorkingSetSize)
    except Exception:
        return None
    return None


def _speicher_system() -> dict:
    try:
        if sys.platform.startswith("linux"):
            werte = {}
            for zeile in open("/proc/meminfo", encoding="utf-8"):
                k, v = zeile.split(":", 1)
                werte[k] = int(v.split()[0]) * 1024
            return {"gesamt": werte.get("MemTotal"), "frei": werte.get("MemAvailable")}
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS(); m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return {"gesamt": int(m.ullTotalPhys), "frei": int(m.ullAvailPhys)}
    except Exception:
        pass
    return {"gesamt": None, "frei": None}


_groessen = {"zeit": 0, "werte": {}}
_handel_merk = {"zeit": 0, "wert": None}


def _ordner_groessen() -> dict:
    """Größe der Teile des Datenordners, höchstens alle 10 Minuten neu gezählt."""
    if time.time() - _groessen["zeit"] < 600:
        return _groessen["werte"]
    werte = {}
    if DATEN.exists():
        for teil in sorted(DATEN.iterdir()):
            if teil.is_dir():
                summe = 0
                for wurzel, _, dateien in os.walk(teil):
                    for d in dateien:
                        try:
                            summe += os.path.getsize(os.path.join(wurzel, d))
                        except OSError:
                            pass
                werte[teil.name] = summe
    _groessen.update(zeit=time.time(), werte=werte)
    return werte


def _code_stand() -> str | None:
    try:
        neueste = max((p.stat().st_mtime for p in (PROGRAMM / "app").rglob("*") if p.is_file() and "__pycache__" not in p.parts), default=None)
        return datetime.fromtimestamp(neueste).isoformat(timespec="minutes") if neueste else None
    except OSError:
        return None


def seite() -> dict:
    from .einstellungen import uebersicht as keys
    from .markt import ki
    jetzt = datetime.now()
    try:
        platte = shutil.disk_usage(DATEN)
        platte = {"gesamt": platte.total, "frei": platte.free}
    except OSError:
        platte = {"gesamt": None, "frei": None}

    with _sperre:
        jobs = {n: dict(j) for n, j in _jobs.items()}
        netz = {q: dict(n) for q, n in _netz.items()}
        fehler = list(_fehler)
    zeilen = []
    for name in list(JOBS) + [n for n in jobs if n not in JOBS]:
        j = jobs.get(name, {})
        zeilen.append({"name": name, "takt": JOBS.get(name), **j})
    # Fundamentaldaten und Handelsdaten führen ihren Zustand selbst
    try:
        from .markt.fundament.hintergrund import status as fund_status
        f = fund_status()
        zeilen.append({"name": "Fundamentaldaten (S&P 500)", "takt": "einmal am Tag nach 22:30 Uhr: Schätzungen aller S&P-500-Werte",
                       "laeuft": f.get("laeuft"), "ende": f.get("letzter"), "ok": not f.get("fehler") if f.get("letzter") or f.get("fehler") else None,
                       "letzter_fehler": f.get("fehler"), "hinweis": f"{f.get('anzahl')} Werte" if f.get("anzahl") else None})
    except Exception:
        pass
    handel = []
    try:
        # Zählt über die ganze Handelsdatenbank (rund eine Million Zeilen, einige Sekunden): 5 Minuten merken
        if time.time() - _handel_merk["zeit"] > 300 or not _handel_merk["wert"]:
            from .markt.handel.auswertung import status as handel_status
            _handel_merk.update(zeit=time.time(), wert=handel_status())
        for q in (_handel_merk["wert"].get("quellen") or []):
            handel.append({"name": q["name"], "aktiv": q["aktiv"], "grund": q.get("grund"), "letzter_erfolg": q.get("letzter_erfolg"),
                           "fehler": q.get("fehler_jetzt"), "pause_bis": q.get("pause_bis"), "naechster": q.get("naechster_termin"),
                           "kontingent": q.get("kontingent"), "neuester": q.get("neuester"), "quarantaene": q.get("quarantaene"), "laeuft": q.get("laeuft")})
    except Exception as f:
        handel = [{"name": "Handelsdaten", "fehler": _fehler_text(f)}]

    return {
        "system": {
            "version": VERSION, "code_stand": _code_stand(), "python": platform.python_version(), "plattform": f"{platform.system()} {platform.release()}",
            "modus": ANMELDUNG, "gestartet": START.isoformat(timespec="seconds"), "laufzeit_s": int((jetzt - START).total_seconds()),
            "speicher_server": _speicher_prozess(), "speicher_system": _speicher_system(), "platte": platte,
            "datenordner": str(DATEN) if ANMELDUNG == "lokal" else DATEN.name, "ordner": _ordner_groessen(),
            "threads": threading.active_count(),
        },
        "jobs": zeilen,
        "netz": [{"quelle": q, **n} for q, n in sorted(netz.items(), key=lambda x: (-x[1].get("fehler", 0), x[0]))],
        "handel": handel,
        "ki": ki.status(),
        "keys": [{"name": k["name"], "gesetzt": k["gesetzt"], "herkunft": k["herkunft"], "text": k["text"]} for k in keys()],
        "fehler": fehler,
        "stand": jetzt.isoformat(timespec="seconds"),
    }
