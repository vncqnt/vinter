"""Der lokale Server: liefert die Webseite und die fertig gerechneten Zahlen als JSON."""
import re
from datetime import datetime
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from .anmeldung import aktueller_benutzer
from .benutzer import Benutzer
from .konfig import vorlagen_kopieren
from .markt.bewertung import bewerte
from .markt.kopfzeile import top_mover
from .markt.squawk import schlagzeile
from .markt.zitate import auftritte
from .markt.kurse import kurs_eur, suche_werte, ticker_fuer, vorladen
from .markt.maerkte import cockpit, index_verlauf
from .markt.makro import makro_termine
from .markt.sektoren import sektor_details, sektoren
from .markt.volaflaeche import oberflaeche, starte_hintergrund
from .markt.themen import LOGO_ENDUNGEN, ThemenFehler, logo_pfad, themen_ranking
from .markt import prognosen as manifold
from .nutzer import prognosen, tafel, thesen, watchlist
from .nutzer.benchmark import benchmark
from .nutzer.exits import exit_alarme
from .nutzer.risiko import risiko
from .nutzer.makro_sensitivitaet import makro_sensitivitaet
from .nutzer.faktoren import faktor_analyse
from .nutzer.relativ import relative_staerke
from .nutzer.etf_check import etf_check
from .nutzer.reverse_dcf import zutaten as dcf_zutaten
from .nutzer.positionsgroesse import zutaten as positionsgroesse_zutaten
from .nutzer.stresstest import stresstest
from .nutzer.earnings_moves import earnings_moves
from .nutzer.earnings_sheet import earnings_sheets
from .markt.rotation import rotation as sektor_rotation
from .nutzer.verteilung import verteilung as _verteilung_fuer_rotation
from .nutzer.freibetrag import freibetrag
from .nutzer.intraday import ny_tagesverlauf, seit_ny, tr_tag
from .nutzer.kalender import frische_zahlen, kalender
from .nutzer.newsfeed import newsfeed
from .nutzer.perioden import perioden
from .nutzer.portfolio import berechne
from .nutzer.tagesverlauf import tagesverlauf
from .nutzer.thesen_news import thesen_news
from .nutzer.tr_parser import ExportFehler, lade_ertraege, lade_trades, speichere_export
from .nutzer.verlauf import depot_verlauf, stunden_verlauf
from .nutzer.verteilung import verteilung
from .nutzer.volumen import analyse as volumen_analyse
from .nutzer.wochenbericht import wochenbericht
from .nutzer.grosses_geld import fonds_seite, short_meine
from .markt.stil import stil_seite
from .markt.cashflow import cashflow_seite
from .markt.cot import cot_seite
from .markt.positionierung import positionierung_seite
from .markt.signale import signale_seite
from .markt.hintergrund import starte as starte_grosses_geld
from .markt import bewegung as marktbewegung, ki as marktki

app = FastAPI(title="Vinter")
# Jeder Endpunkt mit persönlichen Daten bekommt so den angemeldeten Benutzer (siehe anmeldung.py).
Ich = Depends(aktueller_benutzer)


@app.on_event("startup")
def hintergrund_starten():
    # Fehlende Startdateien (Themen, Krisen, News-Quellen …) im Datenverzeichnis anlegen.
    vorlagen_kopieren()
    # Status-Seite: Abrufe ins Netz und zu Yahoo zählen (ohne Inhalte oder Adressen zu speichern)
    from .status import beobachte_netz
    beobachte_netz()
    # Anmeldung auf dem NUC: Gibt es noch kein Konto, steht der Einrichtungscode für das erste Konto im Log.
    from .konfig import ANMELDUNG
    if ANMELDUNG == "passwort":
        from .konten import einrichtungscode
        code = einrichtungscode()
        if code:
            print(f"VINTER EINRICHTUNG: Noch kein Konto. Einrichtungscode für das erste (Admin-)Konto: {code}", flush=True)
    # Holt nach US-Börsenschluss die Volatilitätsoberfläche des Tages, solange der Server läuft.
    starte_hintergrund()
    from .markt.gamma import starte_hintergrund as gamma_hintergrund
    gamma_hintergrund()
    # Lädt einmal am Tag die großen Datenmengen für "Großes Geld" (S&P-500-Kurse, KGVs, FINRA, 13F).
    starte_grosses_geld()
    # Punkt 29b: "Warum bewegt sich der Markt?" alle 30 Minuten zur Handelszeit und nach Börsenschluss.
    marktbewegung.starte()
    # Punkt 31b: Handelsdaten nach dem Veröffentlichungskalender der Quellen abrufen.
    from .markt.handel import plan as handel_plan
    handel_plan.starte()
    # Punkt 24c: Schätzungen aller S&P-500-Werte einmal am Tag speichern (Historie gibt es nicht gratis).
    from .markt.fundament import hintergrund as fundament_hintergrund
    fundament_hintergrund.starte()
    # Zahlen-Auswertung: Analysten-Erwartung vor jedem Termin sichern (Yahoo schaltet danach aufs nächste Quartal um).
    from .nutzer.zahlen import starte_konsens_sicherung
    starte_konsens_sicherung()
    # Roadmap Edge 7: einmal am Tag Insider-Cluster-Käufe in S&P 500 und Nasdaq 100 suchen (SEC Form 4).
    from .markt import insider_cluster
    insider_cluster.starte()
    # Makro-Daten an Veröffentlichungstagen frisch holen, Fed-Wahrscheinlichkeiten täglich speichern (M4, M6).
    from .markt import makro_hintergrund
    makro_hintergrund.starte()


# Große JSON-Antworten (Kursreihen) gepackt übertragen
app.add_middleware(GZipMiddleware, minimum_size=2000)


# ---- Anmeldung (nur mit VINTER_ANMELDUNG=passwort, also auf dem NUC) ----
# Ohne gültige Sitzung gibt es nur die Anmeldeseite (und was sie zum Aussehen braucht).
OFFEN = ("/anmelden", "/style.css", "/vinter.css", "/manifest.webmanifest", "/api/konto/status", "/api/konto/anmelden",
         "/api/konto/einrichten", "/api/konto/einladung/annehmen", "/api/konto/einladung/pruefen")


@app.middleware("http")
async def nur_angemeldet(request: Request, call_next):
    from .anmeldung import angemeldet
    from .konfig import ANMELDUNG
    pfad = request.url.path
    # App-Symbole offen: das iPhone lädt sie beim "Zum Home-Bildschirm" ohne Anmelde-Cookie
    if ANMELDUNG == "lokal" or pfad in OFFEN or pfad.startswith(("/schriften/", "/symbole/")) or angemeldet(request):
        return await call_next(request)
    if pfad.startswith("/api/"):
        return JSONResponse({"fehler": "Bitte anmelden.", "anmelden": True}, status_code=401)
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/anmelden", status_code=303)


@app.get("/manifest.webmanifest")
def web_app_manifest():
    """Web-App-Manifest mit dem richtigen Typ (Windows kennt die Endung sonst nicht)."""
    return FileResponse(Path(__file__).parent / "static" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/anmelden")
def anmelde_seite():
    return FileResponse(Path(__file__).parent / "seiten" / "anmelden.html", headers={"Cache-Control": "no-cache"})


def _adresse(request: Request) -> str:
    return request.client.host if request.client else "?"


def _cookie_setzen(antwort, request: Request, token: str):
    from .anmeldung import COOKIE
    from .konten import SITZUNG_TAGE
    antwort.set_cookie(COOKIE, token, max_age=SITZUNG_TAGE * 86400, httponly=True, samesite="strict",
                       secure=request.url.scheme == "https", path="/")
    return antwort


async def _konto_aktion(request: Request, aktion):
    """Schreibende Konto-Aufrufe: nur mit Header x-vinter: konto (wie bei den anderen Formularen)."""
    from .konten import KontoFehler
    if request.headers.get("x-vinter") != "konto":
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    try:
        daten = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        return aktion(daten or {})
    except KontoFehler as fehler:
        return JSONResponse({"fehler": str(fehler)}, status_code=400)


@app.get("/api/konto/status")
def konto_status(request: Request):
    """Für Anmeldeseite und Kopfzeile: Modus, ob eingerichtet, wer angemeldet ist."""
    from . import konten
    from .anmeldung import angemeldet
    from .konfig import ANMELDUNG
    k = angemeldet(request)
    return {"modus": ANMELDUNG, "eingerichtet": ANMELDUNG == "lokal" or konten.hat_admin(),
            "angemeldet": bool(k), "name": k["name"] if k else None, "rolle": k["rolle"] if k else None}


@app.post("/api/konto/anmelden")
async def konto_anmelden(request: Request):
    from . import konten

    def tun(d):
        token = konten.anmelden(d.get("name"), d.get("passwort"), _adresse(request))
        return _cookie_setzen(JSONResponse({"ok": True}), request, token)
    return await _konto_aktion(request, tun)


@app.post("/api/konto/einrichten")
async def konto_einrichten(request: Request):
    from . import konten

    def tun(d):
        konten.einrichten(d.get("code"), d.get("name"), d.get("passwort"))
        token = konten.anmelden(d.get("name"), d.get("passwort"), _adresse(request))
        return _cookie_setzen(JSONResponse({"ok": True}), request, token)
    return await _konto_aktion(request, tun)


@app.post("/api/konto/einladung/pruefen")
async def konto_einladung_pruefen(request: Request):
    from . import konten
    return await _konto_aktion(request, lambda d: {"gueltig": konten.einladung_gueltig(d.get("code"))})


@app.post("/api/konto/einladung/annehmen")
async def konto_einladung_annehmen(request: Request):
    from . import konten

    def tun(d):
        konten.einladung_annehmen(d.get("code"), d.get("name"), d.get("passwort"), _adresse(request))
        token = konten.anmelden(d.get("name"), d.get("passwort"), _adresse(request))
        return _cookie_setzen(JSONResponse({"ok": True}), request, token)
    return await _konto_aktion(request, tun)


@app.post("/api/konto/abmelden")
async def konto_abmelden(request: Request):
    from . import konten
    from .anmeldung import COOKIE

    def tun(d):
        konten.abmelden(request.cookies.get(COOKIE), ueberall=bool(d.get("ueberall")))
        antwort = JSONResponse({"ok": True})
        antwort.delete_cookie(COOKIE, path="/")
        return antwort
    return await _konto_aktion(request, tun)


@app.post("/api/konto/passwort")
async def konto_passwort(request: Request):
    from . import konten
    from .anmeldung import angemeldet
    k = angemeldet(request)
    if not k or not konten.konten().get(k["name"]):
        return JSONResponse({"fehler": "Im lokalen Modus gibt es kein Passwort."}, status_code=400)
    return await _konto_aktion(request, lambda d: konten.passwort_aendern(k["name"], d.get("alt"), d.get("neu")))


# Verwaltung (nur Admin): Freunde einladen und sperren, Keys eintragen
@app.get("/api/verwaltung")
def verwaltung(request: Request):
    from . import konten
    from .anmeldung import admin
    from .einstellungen import uebersicht
    from .konfig import ANMELDUNG
    admin(request)
    return {"modus": ANMELDUNG, **(konten.uebersicht() if ANMELDUNG == "passwort" else {"konten": [], "einladungen": []}),
            "einstellungen": uebersicht()}


@app.post("/api/verwaltung/einladung")
async def verwaltung_einladung(request: Request):
    from . import konten
    from .anmeldung import admin
    from .einstellungen import key
    from .konfig import ANMELDUNG
    k = admin(request)

    def tun(d):
        if ANMELDUNG != "passwort":
            return JSONResponse({"fehler": "Einladungen gibt es erst auf dem NUC (Anmeldung mit Passwort)."}, status_code=400)
        e = konten.einladung_neu(k["name"], d.get("notiz", ""))
        basis = key("VINTER_ADRESSE").rstrip("/") or str(request.base_url).rstrip("/")
        return {**e, "link": f"{basis}/anmelden?einladung={e['code']}"}
    return await _konto_aktion(request, tun)


@app.delete("/api/verwaltung/einladung/{kennung}")
async def verwaltung_einladung_weg(kennung: str, request: Request):
    from . import konten
    from .anmeldung import admin
    admin(request)
    return await _konto_aktion(request, lambda _: konten.einladung_loeschen(kennung))


@app.post("/api/verwaltung/konto/{name}")
async def verwaltung_konto(name: str, request: Request):
    from . import konten
    from .anmeldung import admin
    k = admin(request)
    return await _konto_aktion(request, lambda d: konten.konto_setzen(name, bool(d.get("aktiv")), k["name"]))


@app.delete("/api/verwaltung/konto/{name}")
async def verwaltung_konto_weg(name: str, request: Request):
    """Freund entfernen: Konto und alle seine Daten endgültig löschen."""
    from . import konten
    from .anmeldung import admin
    k = admin(request)
    return await _konto_aktion(request, lambda _: konten.konto_entfernen(name, k["name"]))


@app.get("/api/umzug/export")
def umzug_export(request: Request):
    """Umzug PC -> NUC (nur Admin): eigene Daten und gemeinsame Marktdaten als ZIP, ohne Keys und Zwischenspeicher."""
    from starlette.background import BackgroundTask
    from .anmeldung import admin
    from .konfig import BENUTZER_DATEN, DATEN, MARKT_DATEN
    from .status import VERSION
    from .umzug import export_bauen
    k = admin(request)
    datei = DATEN / "umzug" / f"export_{datetime.now():%Y%m%d_%H%M%S}.zip"
    export_bauen(datei, MARKT_DATEN, BENUTZER_DATEN / k["ordner"], VERSION)
    # identity: die GZip-Middleware soll das ZIP nicht noch einmal packen
    return FileResponse(datei, media_type="application/zip", filename=f"vinter-umzug-{datetime.now():%Y-%m-%d}.zip",
                        headers={"Content-Encoding": "identity"}, background=BackgroundTask(datei.unlink, missing_ok=True))


@app.post("/api/umzug/import")
async def umzug_import(request: Request):
    """Umzugsdatei einspielen (nur Admin, Header x-vinter: umzug). Führt Datenbanken zusammen, sichert ersetzte Dateien."""
    import asyncio
    from .anmeldung import admin
    from .konfig import BENUTZER_DATEN, DATEN, MARKT_DATEN
    from .umzug import GRENZE, UmzugFehler, einspielen
    k = admin(request)
    if request.headers.get("x-vinter") != "umzug":
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    arbeit = DATEN / "umzug"
    arbeit.mkdir(parents=True, exist_ok=True)
    datei = arbeit / f"eingang_{datetime.now():%Y%m%d_%H%M%S}.zip"
    groesse = 0
    try:
        with open(datei, "wb") as f:
            async for stueck in request.stream():
                groesse += len(stueck)
                if groesse > GRENZE:
                    return JSONResponse({"fehler": "Die Datei ist zu groß."}, status_code=400)
                f.write(stueck)
        return await asyncio.to_thread(einspielen, datei, MARKT_DATEN, BENUTZER_DATEN / k["ordner"], arbeit)
    except UmzugFehler as fehler:
        return JSONResponse({"fehler": str(fehler)}, status_code=400)
    except Exception:
        return JSONResponse({"fehler": "Der Umzug ist fehlgeschlagen. Es wurde nichts gelöscht."}, status_code=500)
    finally:
        datei.unlink(missing_ok=True)


@app.get("/api/status")
def status_seite(request: Request):
    """Status-Seite (nur Admin): Version, Hintergrund-Jobs, Datenquellen, Speicher, KI, letzte Fehler."""
    from .anmeldung import admin
    from .status import seite
    admin(request)
    try:
        return seite()
    except Exception:
        return {"fehler": "Der Status konnte gerade nicht ermittelt werden."}


@app.put("/api/verwaltung/einstellung")
async def verwaltung_einstellung(request: Request):
    """Einen Key im Tool speichern (verschlüsselt) oder mit leerem Wert entfernen. Der Wert kommt nie zurück."""
    from .anmeldung import admin
    from .einstellungen import EINSTELLUNGEN, uebersicht
    from .geheimnisse import setzen
    admin(request)
    namen = {e["name"] for e in EINSTELLUNGEN}

    def tun(d):
        name = str(d.get("name") or "")
        if name not in namen:
            return JSONResponse({"fehler": "Unbekannte Einstellung."}, status_code=400)
        wert = str(d.get("wert") or "").strip()
        if len(wert) > 300 or "\n" in wert or "\r" in wert:
            return JSONResponse({"fehler": "Der Wert passt nicht (zu lang oder mit Zeilenumbruch)."}, status_code=400)
        setzen(name, wert or None)
        return {"ok": True, "einstellungen": uebersicht()}
    return await _konto_aktion(request, tun)


@app.middleware("http")
async def seite_immer_aktuell(request: Request, call_next):
    """Der Browser muss vor jeder Nutzung nachfragen, ob HTML, CSS und JS noch aktuell sind.

    Ohne diese Anweisung nimmt er alte Dateien aus seinem Speicher und passt dann nicht mehr
    zur neuen Seite. Unveränderte Dateien werden trotzdem nicht erneut übertragen.
    """
    antwort = await call_next(request)
    if not request.url.path.startswith("/api/"):
        antwort.headers["Cache-Control"] = "no-cache"
    return antwort


def _depot(b: Benutzer, vorboerse: bool = True) -> dict:
    """Positionen mit aktuellen Kursen und Summen. Grundlage fast aller persönlichen Ansichten."""
    try:
        daten = berechne(lade_trades(b))
    except ExportFehler as fehler:
        return {"fehler": str(fehler)}

    summe_wert = summe_einstand = 0.0
    ohne_kurs = 0
    tickers = {p["isin"]: ticker_fuer(p["isin"], p["art"]) for p in daten["positionen"]}
    vorladen([t for t in tickers.values() if t])
    for p in daten["positionen"]:
        ticker = tickers[p["isin"]]
        kurs = kurs_eur(ticker, vorboerse) if ticker else {"preis_eur": None, "stand": None, "status": "fehlt"}
        p.update(
            ticker=ticker, kurs_eur=kurs["preis_eur"], kurs_stand=kurs["stand"], kurs_status=kurs["status"],
            kurs_aktualitaet=kurs.get("aktualitaet"), kurs_aktualisiert=kurs.get("aktualisiert"),
            kurs_handelsplatz=kurs.get("handelsplatz"),
        )

        if kurs["preis_eur"] is None:
            ohne_kurs += 1
            p.update(wert_eur=None, gv_eur=None, gv_prozent=None)
            continue
        wert = p["stueck"] * kurs["preis_eur"]
        gv = wert - p["einstand_gesamt"]
        p.update(wert_eur=wert, gv_eur=gv, gv_prozent=gv / p["einstand_gesamt"] * 100)
        summe_wert += wert
        summe_einstand += p["einstand_gesamt"]

    daten["positionen"].sort(key=lambda p: p["wert_eur"] or -1, reverse=True)
    gv = summe_wert - summe_einstand
    # Gesamtergebnis: offene Positionen, bereits realisierte Gewinne und erhaltene Dividenden.
    realisiert = sum(j["gewinn"] for j in daten["realisiert"])
    dividenden = sum(e["betrag"] - e["steuer"] for e in lade_ertraege(b) if e["typ"] == "DIVIDEND")
    daten["summen"] = {
        "wert": summe_wert,
        "einstand": summe_einstand,
        "gv": gv,
        "gv_prozent": gv / summe_einstand * 100 if summe_einstand else None,
        "ohne_kurs": ohne_kurs,
        "realisiert": realisiert,
        "dividenden": dividenden,
        "gesamt": gv + realisiert + dividenden,
    }
    daten["abgerufen"] = datetime.now().strftime("%H:%M")
    daten["kurse_aktualisiert"] = max((p["kurs_aktualisiert"] for p in daten["positionen"] if p["kurs_aktualisiert"]), default=None)
    return daten


@app.get("/api/portfolio")
def portfolio(vorboerse: bool = True, b: Benutzer = Ich):
    return _depot(b, vorboerse)


@app.get("/api/earnings")
def earnings(b: Benutzer = Ich):
    try:
        return kalender(b)
    except ExportFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Der Earnings-Kalender konnte gerade nicht erstellt werden."}


@app.get("/api/earnings/markt")
def earnings_markt():
    """Earnings-Termine aller großen Firmen (ab 20 Mrd. $ Börsenwert) für den Kalender, Schalter "Ganzer Markt"."""
    from .markt.markt_termine import markt_termine
    return _sicher(markt_termine)


@app.get("/api/makro")
def makro():
    return makro_termine()


@app.get("/api/maerkte")
def maerkte():
    try:
        return cockpit()
    except Exception:
        return {"fehler": "Das Markt-Cockpit konnte gerade nicht erstellt werden."}


@app.get("/api/sektoren")
def sektoren_heatmap():
    try:
        return sektoren()
    except Exception:
        return {"fehler": "Die Sektoren konnten gerade nicht geladen werden."}


@app.get("/api/sektoren/{etf}")
def sektor_top_werte(etf: str):
    try:
        return sektor_details(etf)
    except Exception:
        return {"fehler": "Die Top-Werte konnten gerade nicht geladen werden."}


@app.get("/api/themen")
def themen():
    try:
        return themen_ranking()
    except ThemenFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Das Themen-Ranking konnte gerade nicht erstellt werden."}


@app.get("/api/logo/{ticker}")
def logo(ticker: str):
    """Ein selbst in daten/markt/logos abgelegtes Logo. Fremde Logo-Dienste werden nie gefragt."""
    pfad = logo_pfad(ticker)
    if not pfad:
        return JSONResponse({"fehler": "Kein Logo."}, status_code=404)
    return FileResponse(pfad, media_type=LOGO_ENDUNGEN[pfad.suffix.lower()])


@app.get("/api/hero-index")
def hero_index():
    return index_verlauf()


@app.get("/api/verteilung")
def portfolio_verteilung(b: Benutzer = Ich):
    daten = _depot(b)
    if "fehler" in daten:
        return daten
    try:
        return verteilung(b, daten["positionen"])
    except Exception:
        return {"fehler": "Die Verteilung konnte gerade nicht berechnet werden."}


@app.get("/api/perioden")
def gewinn_nach_zeitraum(vorboerse: bool = True, tr: bool = False, b: Benutzer = Ich):
    """tr: wie die Aktien-und-ETF-Ansicht bei Trade Republic (ohne Krypto, Tag aus dem jüngsten Kurs
    aller Handelsplätze). Dazu immer "ny": seit der Eröffnung in New York heute."""
    daten = _depot(b, vorboerse or tr)
    if "fehler" in daten:
        return daten
    try:
        ergebnis = perioden(b, daten["positionen"], ohne_krypto=tr)
        if tr:
            try:
                tag = tr_tag(b)
                for z in ergebnis["zeitraeume"]["tag"]["positionen"]:
                    if z["isin"] in tag["je_position"]:
                        z.update(tag["je_position"][z["isin"]])
                ergebnis["zeitraeume"]["tag"].update(gv_eur=tag["gv_eur"], gv_prozent=tag["gv_prozent"])
            except Exception:
                pass  # dann bleibt der normale Tageswert stehen
        try:
            ergebnis["zeitraeume"]["ny"] = seit_ny(b, daten["positionen"], ohne_krypto=tr)
        except Exception:
            ergebnis["zeitraeume"]["ny"] = {"hinweis": "Seit der NY-Eröffnung gerade nicht berechenbar.", "positionen": [], "gv_eur": 0, "gv_prozent": None, "ohne_daten": 0}
        return {**ergebnis, "aktualisiert": daten["kurse_aktualisiert"], "tr": tr}
    except Exception:
        return {"fehler": "Gewinn und Verlust nach Zeitraum konnten gerade nicht berechnet werden."}


@app.get("/api/watchlist")
def watchlist_werte(b: Benutzer = Ich):
    try:
        return watchlist.werte(b)
    except Exception:
        return {"fehler": "Die Watchlist konnte gerade nicht geladen werden."}


@app.get("/api/watchlist/suche")
def watchlist_suche(q: str = ""):
    return {"treffer": suche_werte(q.strip()[:60]) if len(q.strip()) >= 2 else []}


def _watchlist_aktion(request: Request, aktion):
    if request.headers.get("x-vinter") != "watchlist":  # siehe export_hochladen
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    try:
        return aktion()
    except watchlist.WatchlistFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Die Watchlist konnte nicht gespeichert werden."}


@app.post("/api/watchlist")
def watchlist_hinzufuegen(request: Request, daten: dict = Body(...), b: Benutzer = Ich):
    return _watchlist_aktion(request, lambda: watchlist.hinzufuegen(b, daten))


@app.delete("/api/watchlist/{ticker}")
def watchlist_entfernen(ticker: str, request: Request, b: Benutzer = Ich):
    return _watchlist_aktion(request, lambda: watchlist.entfernen(b, ticker))


@app.get("/api/tagesverlauf")
def depot_heute(vorboerse: bool = True, tr: bool = False, ny: bool = False, b: Benutzer = Ich):
    if ny:
        daten = _depot(b)
        if "fehler" in daten:
            return daten
        try:
            return ny_tagesverlauf(b, daten["positionen"])
        except Exception:
            return {"fehler": "Der Verlauf seit New York Open konnte gerade nicht berechnet werden."}
    if tr:
        try:
            return tr_tag(b)
        except Exception:
            return {"fehler": "Der Tagesverlauf wie bei Trade Republic konnte gerade nicht berechnet werden."}
    daten = _depot(b, vorboerse)
    if "fehler" in daten:
        return daten
    try:
        return tagesverlauf(b, daten["positionen"], vorboerse)
    except Exception:
        return {"fehler": "Der Tagesverlauf konnte gerade nicht berechnet werden."}


@app.get("/api/bewertung")
def kgv_bewertung(b: Benutzer = Ich):
    daten = _depot(b)
    if "fehler" in daten:
        return daten
    try:
        return {"werte": bewerte([p["ticker"] for p in daten["positionen"] if p["art"] == "STOCK" and p["ticker"]])}
    except Exception:
        return {"fehler": "Die KGV-Bewertung konnte gerade nicht geladen werden."}


@app.get("/api/freibetrag")
def freibetrag_tracker(b: Benutzer = Ich):
    try:
        return freibetrag(b)
    except ExportFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Der Freibetrag konnte gerade nicht berechnet werden. Prüfe auch deine steuer.yaml."}


@app.post("/api/upload")
async def export_hochladen(request: Request, b: Benutzer = Ich):
    # Fremde Webseiten können diesen Header nicht mitschicken und so keine Daten einschleusen.
    if request.headers.get("x-vinter") != "upload":
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    try:
        return speichere_export(b, await request.body())
    except ExportFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Die Datei konnte nicht gespeichert werden."}


@app.get("/api/news")
def news(b: Benutzer = Ich):
    try:
        return thesen_news(b)
    except Exception:
        return {"fehler": "Die News konnten gerade nicht geladen werden."}


@app.get("/api/thesen")
def thesen_liste(b: Benutzer = Ich):
    return thesen.alle_thesen(b)


async def _thesen_aktion(request: Request, aktion):
    """Gemeinsamer Rahmen für Anlegen, Ändern und Löschen."""
    if request.headers.get("x-vinter") != "thesen":  # siehe export_hochladen
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    try:
        return aktion(await request.json() if request.method != "DELETE" else None)
    except thesen.ThesenFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Die These konnte nicht gespeichert werden."}


@app.post("/api/thesen")
async def these_anlegen(request: Request, b: Benutzer = Ich):
    return await _thesen_aktion(request, lambda d: thesen.anlegen(b, d))


@app.put("/api/thesen/{thesen_id}")
async def these_aendern(thesen_id: str, request: Request, b: Benutzer = Ich):
    return await _thesen_aktion(request, lambda d: thesen.aendern(b, thesen_id, d))


@app.delete("/api/thesen/{thesen_id}")
async def these_loeschen(thesen_id: str, request: Request, b: Benutzer = Ich):
    return await _thesen_aktion(request, lambda _: thesen.loeschen(b, thesen_id))


@app.get("/api/risiko")
def risiko_tab(zeitraum: str = "1j", extra: str | None = None, b: Benutzer = Ich):
    daten = _mit_markt(b, extra)
    if "fehler" in daten:
        return daten
    try:
        return risiko(b, daten["positionen"], zeitraum)
    except Exception:
        return {"fehler": "Das Risiko konnte gerade nicht berechnet werden."}


@app.get("/api/stresstest")
def stresstest_tab(von: str | None = None, bis: str | None = None, b: Benutzer = Ich):
    daten = _depot(b)
    if "fehler" in daten:
        return daten
    try:
        return stresstest(daten["positionen"], von, bis)
    except Exception:
        return {"fehler": "Der Stresstest konnte gerade nicht berechnet werden."}


@app.get("/api/rotation")
def rotation_tab(b: Benutzer = Ich):
    """Sektorrotation (Marktdaten) plus die Sektor- und Branchenanteile des eigenen Depots."""
    try:
        aus = dict(sektor_rotation())
    except Exception:
        return {"fehler": "Die Sektorrotation konnte gerade nicht berechnet werden."}
    try:
        from .markt.rotation import breite_sektoren
        aus["breite"] = breite_sektoren()
    except Exception:
        aus["breite"] = {"fehler": "Die Sektor-Breite ist gerade nicht verfügbar (S&P-500-Kurse werden im Hintergrund geladen)."}
    aus["eigen"] = {}
    daten = _depot(b)
    if "fehler" not in daten:
        try:
            v = _verteilung_fuer_rotation(b, daten["positionen"])
            sektoren = next((d["teile"] for d in v["dimensionen"] if d["titel"] == "Sektor"), [])
            aus["eigen"] = {"ticker": [p["ticker"] for p in daten["positionen"] if p.get("ticker")],
                            "anteile": {t["name"]: round(t["anteil"], 1) for t in sektoren},
                            "positionen": {k: [{"name": x["name"], "anteil": round(x["anteil"], 1)} for x in liste] for k, liste in v["sektor_positionen"].items()}}
        except Exception:
            pass
    return aus


@app.get("/api/rotation/sektor")
def rotation_sektor(name: str = "", etf: str = ""):
    from .markt.rotation import GICS, sektor_werte
    from .markt.sektoren import BRANCHEN, SEKTOREN
    if (name and name not in GICS.values()) or (not name and etf not in BRANCHEN and etf not in SEKTOREN):
        return {"fehler": "Unbekannter Sektor."}
    try:
        return sektor_werte(name or None, None if name else etf)
    except Exception:
        return {"fehler": "Die Einzelwerte konnten gerade nicht geladen werden."}


@app.get("/api/aktie-sheet")
def aktie_sheet(ticker: str, b: Benutzer = Ich):
    """Cheat Sheet für eine beliebige Aktie (z. B. aus der Sektorliste), auch ohne anstehenden Termin."""
    from datetime import date as _tag
    from .markt.earnings_moves import erwartete_bewegung, reaktionen
    from .markt.earnings_sheet import sheet
    ticker = ticker.upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,12}", ticker):
        return {"fehler": "Ungültiger Ticker."}
    try:
        s = sheet(ticker)
    except Exception:
        return {"fehler": "Für diese Aktie gibt es gerade keine Daten."}
    datum = s.get("termin", {}).get("datum")
    tage = (_tag.fromisoformat(datum) - _tag.today()).days if datum else None
    werte = {}
    daten = _depot(b)
    if "fehler" not in daten:
        werte = {p.get("ticker"): p.get("wert_eur") for p in daten["positionen"]}
    aus = {**s, "name": s["info"].get("name") or ticker, "eigen": ticker in werte, "bezug": [], "wert_eur": werte.get(ticker), "tage": tage}
    try:
        aus["reaktionen"] = reaktionen(ticker).get("reaktionen") or []
    except Exception:
        aus["reaktionen"] = []
    if tage is not None and 0 <= tage <= 14:
        try:
            aus["erwartet"] = erwartete_bewegung(ticker, datum)
        except Exception:
            aus["erwartet"] = None
    return aus


@app.get("/api/earnings-sheet")
def earnings_sheet_tab(extra: str | None = None, depot: bool = True, b: Benutzer = Ich):
    daten = _mit_markt(b, extra, depot)
    try:
        return earnings_sheets(b, [] if "fehler" in daten else daten["positionen"])
    except Exception:
        return {"fehler": "Das Earnings Cheat Sheet konnte gerade nicht berechnet werden."}


# ---- Zahlen-Auswertung: eingefügte Quartalsmitteilungen (app/nutzer/zahlen.py) ----

async def _zahlen_aktion(request: Request, aktion):
    """Gemeinsamer Rahmen für Anlegen, Ändern und Löschen."""
    from .nutzer import zahlen
    if request.headers.get("x-vinter") != "zahlen":  # siehe export_hochladen
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    try:
        daten = await request.json() if request.method in ("POST", "PUT") and request.headers.get("content-type", "").startswith("application/json") else {}
        return aktion(zahlen, daten or {})
    except zahlen.ZahlenFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Die Auswertung konnte gerade nicht gespeichert werden."}


@app.get("/api/auswertungen")
def zahlen_liste(b: Benutzer = Ich):
    from .nutzer import zahlen
    try:
        return zahlen.uebersicht(b)
    except Exception:
        return {"fehler": "Die Auswertungen konnten gerade nicht geladen werden."}


@app.get("/api/auswertungen/{kennung}")
def zahlen_eintrag(kennung: str, b: Benutzer = Ich):
    from .nutzer import zahlen
    try:
        return zahlen.eintrag(b, kennung)
    except zahlen.ZahlenFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Die Auswertung konnte gerade nicht geladen werden."}


@app.post("/api/auswertungen")
async def zahlen_anlegen(request: Request, b: Benutzer = Ich):
    return await _zahlen_aktion(request, lambda z, d: z.anlegen(b, d))


@app.put("/api/auswertungen/{kennung}")
async def zahlen_aendern(kennung: str, request: Request, b: Benutzer = Ich):
    return await _zahlen_aktion(request, lambda z, d: z.aendern(b, kennung, d))


@app.post("/api/auswertungen/{kennung}/neu")
async def zahlen_neu(kennung: str, request: Request, b: Benutzer = Ich):
    return await _zahlen_aktion(request, lambda z, _: z.neu_auswerten(b, kennung))


@app.post("/api/auswertungen/{kennung}/ki")
async def zahlen_ki(kennung: str, request: Request, b: Benutzer = Ich):
    return await _zahlen_aktion(request, lambda z, _: z.ki_anstossen(b, kennung))


@app.delete("/api/auswertungen/{kennung}")
async def zahlen_loeschen(kennung: str, request: Request, b: Benutzer = Ich):
    return await _zahlen_aktion(request, lambda z, _: z.loeschen(b, kennung))


@app.get("/api/earnings-barometer")
def earnings_barometer_api():
    from .markt.earnings_barometer import barometer
    try:
        return barometer()
    except Exception:
        return {"fehler": "Das Earnings-Barometer konnte gerade nicht berechnet werden (Earnings-Kalender von nasdaq.com nicht erreichbar)."}


@app.get("/api/earnings-moves")
def earnings_moves_tab(extra: str | None = None, depot: bool = True, b: Benutzer = Ich):
    daten = _mit_markt(b, extra, depot)
    if "fehler" in daten:
        return daten
    try:
        return earnings_moves(daten["positionen"])
    except Exception:
        return {"fehler": "Die Earnings-Moves konnten gerade nicht berechnet werden."}


@app.get("/api/positionsgroesse")
def positionsgroesse_tab(ticker: str, b: Benutzer = Ich):
    ticker = ticker.strip().upper()[:20]
    if not re.fullmatch(r"[A-Z0-9.\-^=]{1,20}", ticker):
        return {"fehler": "Ungültiger Ticker."}
    daten = _depot(b)
    if "fehler" in daten:
        return daten
    try:
        return positionsgroesse_zutaten(daten["positionen"], ticker)
    except Exception:
        return {"fehler": "Der Positionsgrößen-Rechner konnte gerade nicht rechnen."}


@app.get("/api/reverse-dcf")
def reverse_dcf_tab(extra: str | None = None, depot: bool = True, b: Benutzer = Ich):
    daten = _mit_markt(b, extra, depot)
    if "fehler" in daten:
        return daten
    try:
        return dcf_zutaten(daten["positionen"])
    except Exception:
        return {"fehler": "Der Reverse-DCF konnte gerade nicht berechnet werden."}


@app.get("/api/etf-check")
def etf_check_tab(b: Benutzer = Ich):
    daten = _depot(b)
    if "fehler" in daten:
        return daten
    try:
        return etf_check(daten["positionen"])
    except Exception:
        return {"fehler": "Der ETF-Check konnte gerade nicht berechnet werden."}


@app.get("/api/relativ")
def relativ_tab(extra: str | None = None, depot: bool = True, b: Benutzer = Ich):
    daten = _mit_markt(b, extra, depot)
    if "fehler" in daten:
        return daten
    try:
        return relative_staerke(daten["positionen"])
    except Exception:
        return {"fehler": "Die relative Stärke konnte gerade nicht berechnet werden."}


@app.get("/api/faktoren")
def faktoren_tab(jahre: int = 3, extra: str | None = None, depot: bool = True, b: Benutzer = Ich):
    daten = _mit_markt(b, extra, depot)
    if "fehler" in daten:
        return daten
    try:
        return faktor_analyse(daten["positionen"], jahre)
    except Exception:
        return {"fehler": "Die Faktor-Analyse konnte gerade nicht berechnet werden."}


@app.get("/api/makro-sensitivitaet")
def makro_sensitivitaet_tab(jahre: int = 3, ohne_markt: bool = True, extra: str | None = None, depot: bool = True, b: Benutzer = Ich):
    daten = _mit_markt(b, extra, depot)
    if "fehler" in daten:
        return daten
    try:
        return makro_sensitivitaet(b, daten["positionen"], jahre, ohne_markt)
    except Exception:
        return {"fehler": "Die Makro-Sensitivität konnte gerade nicht berechnet werden."}


@app.get("/api/prognosen")
def prognosen_seite(b: Benutzer = Ich):
    # Wie das Depot auf steigende Zinsen reagiert, aus "Zins, Öl, Dollar" (Gesamtwirkung, 3 Jahre).
    zins = None
    daten = _depot(b)
    if "fehler" not in daten:
        try:
            ms = makro_sensitivitaet(b, daten["positionen"], 3, False)
            zins = ms["depot"]["zins"] if "depot" in ms else None
        except Exception:
            zins = None
    try:
        return prognosen.ansicht(b, zins)
    except Exception:
        return {"fehler": "Die Prognosen konnten gerade nicht geladen werden."}


@app.get("/api/prognosen/fed-tage")
def prognosen_fed_tage(b: Benutzer = Ich):
    """Wie sich das heutige Depot an früheren Fed-Entscheidungen bewegt hätte (app/nutzer/fed_tage.py)."""
    from .nutzer.fed_tage import auswertung
    daten = _depot(b)
    if "fehler" in daten:
        return daten
    try:
        return auswertung(b, daten["positionen"])
    except Exception:
        return {"fehler": "Die Fed-Tage konnten gerade nicht berechnet werden."}


@app.get("/api/prognosen/themen")
def prognosen_themen():
    try:
        return manifold.uebersicht()
    except Exception:
        return {"fehler": "Die Themen konnten gerade nicht geladen werden."}


@app.get("/api/prognosen/kurz")
def prognosen_kurz(b: Benutzer = Ich):
    """Nur Fed und verfolgte Prognosen samt Veränderung, ohne Depot-Rechnung. Für die Laufleiste."""
    try:
        return prognosen.ansicht(b, None)
    except Exception:
        return {"fehler": "Die Prognosen konnten gerade nicht geladen werden."}


@app.get("/api/prognosen/suche")
def prognosen_suche(q: str = ""):
    return manifold.suche(q)


def _prognosen_aktion(request: Request, aktion):
    if request.headers.get("x-vinter") != "prognosen":  # siehe export_hochladen
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    try:
        return aktion()
    except manifold.PrognosenFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Die Auswahl konnte nicht gespeichert werden."}


@app.post("/api/prognosen")
def prognose_hinzufuegen(request: Request, daten: dict = Body(...), b: Benutzer = Ich):
    return _prognosen_aktion(request, lambda: prognosen.hinzufuegen(b, daten))


@app.delete("/api/prognosen/{markt_id}")
def prognose_entfernen(markt_id: str, request: Request, b: Benutzer = Ich):
    return _prognosen_aktion(request, lambda: prognosen.entfernen(b, markt_id))


@app.get("/api/tafel")
def tafel_lesen(b: Benutzer = Ich):
    return tafel.lesen(b)


@app.put("/api/tafel")
def tafel_speichern(request: Request, daten: dict = Body(...), b: Benutzer = Ich):
    if request.headers.get("x-vinter") != "tafel":  # siehe export_hochladen
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    try:
        return tafel.speichern(b, daten)
    except tafel.TafelFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Das Dashboard konnte nicht gespeichert werden."}


@app.get("/api/optionen")
def optionen_aktie(ticker: str):
    """Put/Call, Open Interest, Max Pain, auffällige Aktivität, Spread, Short (app/markt/optionen.py)."""
    from .markt.optionen import analyse
    try:
        return analyse(ticker)
    except Exception:
        return {"fehler": "Die Optionsdaten konnten gerade nicht geladen werden."}


@app.get("/api/schwankung")
def schwankung_wert(ticker: str):
    """Realisierte Vola, Kegel, ATR und IV-Einordnung eines Werts (app/markt/schwankung.py)."""
    from .markt.schwankung import analyse
    try:
        return analyse(ticker)
    except Exception:
        return {"fehler": "Die Schwankung konnte gerade nicht berechnet werden."}


@app.get("/api/schwankung/depot")
def schwankung_depot(b: Benutzer = Ich, extra: str | None = None, depot: bool = True):
    """Welche Positionen schwanken gerade ungewöhnlich (app/nutzer/schwankung.py)."""
    from .nutzer.schwankung import rangliste
    daten = _mit_markt(b, extra, depot)
    if "fehler" in daten:
        return daten
    try:
        return rangliste(b, daten["positionen"])
    except Exception:
        return {"fehler": "Die Rangliste konnte gerade nicht berechnet werden."}


@app.get("/api/journal")
def journal_seite(b: Benutzer = Ich):
    """Trade-Journal mit Auswertung (app/nutzer/journal.py)."""
    from .nutzer.journal import auswertung
    from .nutzer.tr_parser import ExportFehler
    try:
        return auswertung(b)
    except ExportFehler as f:
        return {"fehler": str(f)}
    except Exception:
        return {"fehler": "Das Journal konnte gerade nicht berechnet werden."}


@app.put("/api/journal/{tid}")
async def journal_grund(tid: str, request: Request, b: Benutzer = Ich):
    from .nutzer.journal import JournalFehler, grund_speichern
    if request.headers.get("x-vinter") != "journal":  # siehe export_hochladen
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    try:
        daten = await request.json()
    except Exception:
        return JSONResponse({"fehler": "Ungültige Eingabe."}, status_code=400)
    try:
        return grund_speichern(b, tid, str(daten.get("grund", "")))
    except JournalFehler as f:
        return JSONResponse({"fehler": str(f)}, status_code=400)


@app.get("/api/exitanalyse")
def exit_analyse(b: Benutzer = Ich):
    """Deine Verkäufe gegen einfache Ausstiegsregeln (app/nutzer/exitanalyse.py)."""
    from .nutzer.exitanalyse import auswertung
    from .nutzer.tr_parser import ExportFehler
    try:
        return auswertung(b)
    except ExportFehler as f:
        return {"fehler": str(f)}
    except Exception:
        return {"fehler": "Die Exit-Analyse konnte gerade nicht berechnet werden."}


@app.get("/api/wahrscheinlichkeit")
def wahrscheinlichkeit_wert(ticker: str):
    """Optionsimplizite Verteilung eines US-Werts je Verfall (app/markt/wahrscheinlichkeit.py)."""
    from .markt.wahrscheinlichkeit import verteilung
    try:
        return verteilung(ticker)
    except Exception:
        return {"fehler": "Die Verteilung konnte gerade nicht berechnet werden."}


@app.get("/api/wahrscheinlichkeit/thesen")
def wahrscheinlichkeit_thesen(b: Benutzer = Ich):
    """Deine Thesen-Level gegen die optionsimplizite Verteilung (app/nutzer/wahrscheinlichkeit.py)."""
    from .nutzer.wahrscheinlichkeit import auswertung
    daten = _depot(b)
    if "fehler" in daten:
        return daten
    try:
        return auswertung(b, daten["positionen"])
    except Exception:
        return {"fehler": "Die Wahrscheinlichkeiten konnten gerade nicht berechnet werden."}


@app.get("/api/umbau")
def umbau_rechnen(b: Benutzer = Ich, riskant: int | None = None, mindest: int | None = None):
    """Gewichtungs-Rechner für den Depot-Umbau (app/nutzer/umbau.py)."""
    from .nutzer.umbau import rechnen
    daten = _depot(b)
    if "fehler" in daten:
        return daten
    try:
        return rechnen(b, daten["positionen"], riskant, mindest)
    except Exception:
        return {"fehler": "Der Umbau konnte gerade nicht berechnet werden."}


@app.put("/api/umbau")
async def umbau_speichern(request: Request, b: Benutzer = Ich):
    from .nutzer.umbau import speichern
    if request.headers.get("x-vinter") != "umbau":  # siehe export_hochladen
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    try:
        return speichern(b, await request.json())
    except Exception:
        return JSONResponse({"fehler": "Ungültige Eingabe."}, status_code=400)


@app.get("/api/regime")
def markt_regime():
    """Markt-Regime-Score aus VIX-Kurve, Skew, Kreditspreads, Marktbreite, Gamma (app/markt/regime.py)."""
    from .markt.regime import regime
    try:
        return regime()
    except Exception:
        return {"fehler": "Das Markt-Regime konnte gerade nicht berechnet werden."}


@app.get("/api/eventbewegung")
def event_bewegung_seite():
    """Eingepreiste gegen tatsächliche SPY-Bewegung an CPI-, Fed- und Arbeitsmarkt-Tagen (app/markt/eventbewegung.py)."""
    from .markt.eventbewegung import seite
    try:
        return seite()
    except Exception:
        return {"fehler": "Die Event-Bewegungen konnten gerade nicht berechnet werden."}


@app.get("/api/insider/cluster")
def insider_cluster_seite():
    """Insider-Cluster-Käufe in S&P 500 und Nasdaq 100 aus dem letzten täglichen Durchlauf (app/markt/insider_cluster.py)."""
    from .markt.insider_cluster import letztes
    try:
        return letztes()
    except Exception:
        return {"fehler": "Die Insider-Cluster konnten gerade nicht geladen werden."}


@app.get("/api/drift")
def drift_tracker():
    """Post-Earnings-Drift im S&P 500 (app/markt/drift.py)."""
    from .markt.drift import tracker
    try:
        return tracker()
    except Exception:
        return {"fehler": "Der Drift-Tracker konnte gerade nicht berechnet werden."}


@app.get("/api/drift/depot")
def drift_depot(b: Benutzer = Ich, extra: str | None = None):
    """Post-Earnings-Drift deiner Werte und ihrer Peers (app/nutzer/drift.py)."""
    from .nutzer.drift import auswertung
    daten = _mit_markt(b, extra)
    if "fehler" in daten:
        return daten
    try:
        return auswertung(b, daten["positionen"])
    except Exception:
        return {"fehler": "Die Drift deiner Werte konnte gerade nicht berechnet werden."}


@app.get("/api/vrp")
def vola_risikopraemie(b: Benutzer = Ich):
    """Volatilitäts-Risikoprämie deiner US-Werte (app/nutzer/vrp.py)."""
    from .nutzer.vrp import auswertung
    daten = _depot(b)
    if "fehler" in daten:
        return daten
    try:
        return auswertung(b, daten["positionen"])
    except Exception:
        return {"fehler": "Die Risikoprämie konnte gerade nicht berechnet werden."}


@app.get("/api/paare")
def paar_monitor(b: Benutzer = Ich):
    """Paar-Monitor: Kursverhältnis verwandter Werte als Z-Score (app/nutzer/paare.py)."""
    from .nutzer.paare import auswertung
    try:
        return auswertung(b)
    except Exception:
        return {"fehler": "Der Paar-Monitor konnte gerade nicht berechnet werden."}


@app.put("/api/paare")
async def paare_speichern(request: Request, b: Benutzer = Ich):
    from .nutzer.paare import speichern
    if request.headers.get("x-vinter") != "paare":  # siehe export_hochladen
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    try:
        daten = await request.json()
        return {"paare": speichern(b, daten.get("paare") or [])}
    except Exception:
        return JSONResponse({"fehler": "Ungültige Eingabe."}, status_code=400)


@app.get("/api/makrotage")
def makro_tage(b: Benutzer = Ich):
    """Event-Studie: Reaktion deiner Positionen an CPI-, Arbeitsmarkt- und Fed-Tagen nach Überraschung (app/nutzer/makro_tage.py)."""
    from .nutzer.makro_tage import auswertung
    daten = _depot(b)
    if "fehler" in daten:
        return daten
    try:
        return auswertung(b, daten["positionen"])
    except Exception:
        return {"fehler": "Die Makro-Tage konnten gerade nicht berechnet werden."}


@app.get("/api/absicherung")
def absicherung_rechnen(b: Benutzer = Ich, ziel: str = "depot", anteil: int = 50, index: str = "ndx", tage: int = 90, gebuehr: float = 1.0):
    """Absicherungs-Rechner: Index-Short oder Faktor-Zertifikat gegen das Beta (app/nutzer/absicherung.py)."""
    from .nutzer.absicherung import rechnen
    daten = _depot(b)
    if "fehler" in daten:
        return daten
    try:
        return rechnen(b, daten["positionen"], ziel if ziel in ("depot", "tech") else "depot", max(10, min(100, anteil)),
                       index if index in ("ndx", "spx") else "ndx", max(5, min(730, tage)), max(0.0, min(10.0, gebuehr)))
    except Exception:
        return {"fehler": "Die Absicherung konnte gerade nicht berechnet werden."}


@app.get("/api/backtest")
def backtest_labor(ticker: str = "SPY", regel: str = "trend", einsatz: float = 1000, spread: float = 0.1):
    """Backtest-Labor mit Kosten, Walk-Forward und Vergleich (app/markt/backtest.py). Spielwiese."""
    from .markt.backtest import REGELN, labor
    if ticker == "_regeln":
        return {k: {"name": v["name"], "text": v["text"]} for k, v in REGELN.items()}
    try:
        return labor(ticker, regel, max(50.0, min(1e6, einsatz)), max(0.0, min(5.0, spread)))
    except Exception:
        return {"fehler": "Der Backtest konnte gerade nicht gerechnet werden."}


@app.get("/api/montecarlo")
def monte_carlo(b: Benutzer = Ich, jahre: int = 3, rendite: str = "neutral"):
    """Monte-Carlo fürs Depot: Bandbreite über ein Jahr (app/nutzer/montecarlo.py)."""
    from .nutzer.montecarlo import auswertung
    daten = _depot(b)
    if "fehler" in daten:
        return daten
    try:
        return auswertung(b, daten["positionen"], jahre, rendite)
    except Exception:
        return {"fehler": "Die Simulation konnte gerade nicht gerechnet werden."}


@app.get("/api/analysecharts")
def analyse_charts(b: Benutzer = Ich, zeitraum: str = "3m"):
    """Analyse-Charts fürs Depot (app/nutzer/analyse_charts.py)."""
    from .nutzer.analyse_charts import auswertung
    daten = _depot(b)
    if "fehler" in daten:
        return daten
    try:
        return auswertung(b, daten["positionen"], zeitraum)
    except Exception:
        return {"fehler": "Die Analyse-Charts konnten gerade nicht berechnet werden."}


@app.get("/api/zinsen")
def zinsen_assets():
    """US-Zinskurve, Zinskurven-Gebirge und Cross-Asset-Heatmap (app/markt/zinsen.py)."""
    from .markt.zinsen import cross_asset, zinskurve
    aus = {}
    for name, f in (("kurve", zinskurve), ("assets", cross_asset)):
        try:
            aus[name] = f()
        except Exception:
            aus[name] = {"fehler": "Gerade nicht verfügbar."}
    return aus


@app.get("/api/chart/mini")
def chart_mini(ticker: str):
    """Kurz notiert: Sigma-Bewegung, 52-Wochen-Abstände, Drawdown, Vola-Kompression (app/markt/mini.py)."""
    from .markt.mini import mini
    try:
        return mini(ticker)
    except Exception:
        return {"fehler": "Gerade nicht verfügbar."}


@app.get("/api/chart/meine")
def chart_meine(ticker: str, b: Benutzer = Ich):
    """Dein Einstand, die Exit-Level der These (in Handelswährung) und das erste Kaufdatum für den Chart."""
    from .nutzer.exits import LEVEL
    from .nutzer.thesen import alle_thesen
    from .nutzer.tr_parser import lade_trades
    from .markt.abruf import schlusskurse
    daten = _depot(b)
    pos = next((p for p in daten.get("positionen", []) if p.get("ticker") == ticker), None)
    aus = {"ticker": ticker, "einstand": None, "level": [], "erster_kauf": None}
    try:
        lokal = float(schlusskurse(ticker, "5d").iloc[-1])
    except Exception:
        lokal = None
    faktor = lokal / pos["kurs_eur"] if pos and pos.get("kurs_eur") and lokal else None
    if not faktor and lokal:
        from .markt.kurse import kurs_eur
        eur = (kurs_eur(ticker) or {}).get("preis_eur")
        faktor = lokal / eur if eur else None
    if pos and pos.get("stueck") and faktor:
        aus["einstand"] = pos["einstand_gesamt"] / pos["stueck"] * faktor
        isin = pos["isin"]
        kaeufe = [t["zeit"] for t in lade_trades(b) if t["isin"] == isin and t["typ"] == "BUY"]
        aus["erster_kauf"] = min(kaeufe).date().isoformat() if kaeufe else None
    if faktor:
        for these in alle_thesen(b)["thesen"]:
            plan = (these.get("exits") or {}).get(ticker) or {}
            for art, name, feld, richtung in LEVEL:
                if plan.get(feld) is not None:
                    aus["level"].append({"art": art, "name": name, "preis": plan[feld] * faktor, "preis_eur": plan[feld], "richtung": richtung})
    aus["faktor"] = faktor
    return aus


@app.get("/api/sparklines")
def sparklines(b: Benutzer = Ich):
    """Kursverlauf der letzten 30 Handelstage in Euro je Position, für kleine Linien in der Positionsliste."""
    from .markt.euro_kurse import euro_kurse
    daten = _depot(b)
    if "fehler" in daten:
        return daten
    tk = sorted({p["ticker"] for p in daten["positionen"] if p.get("ticker")})
    try:
        kurse = euro_kurse(tk, tage=60)
    except Exception:
        return {"fehler": "Gerade nicht verfügbar."}
    return {t: [round(float(v), 4) for v in s.dropna().iloc[-30:]] for t, s in kurse.items()}


@app.get("/api/themen/breite")
def themen_breite_api():
    """Marktbreite je Thema: Anteil über 50- und 200-Tage-Linie (app/markt/mini.py)."""
    from .markt.mini import themen_breite
    try:
        return themen_breite()
    except Exception:
        return {"fehler": "Gerade nicht verfügbar."}


@app.get("/api/makrodaten")
def makro_daten():
    """Makro-Kennzahlen USA und Eurozone mit Einordnung (app/markt/makrodaten.py)."""
    from .markt.makrodaten import makrodaten
    try:
        return makrodaten()
    except Exception:
        return {"fehler": "Die Makro-Daten sind gerade nicht erreichbar."}


@app.get("/api/ezb-erwartung")
def ezb_erwartung():
    """Erwarteter EZB-Zinspfad, Näherung aus der AAA-Zinskurve der EZB (app/markt/ezb_erwartung.py)."""
    from .markt.abruf import gecacht
    from .markt.ezb_erwartung import erwartung
    try:
        return gecacht("ezb:erwartung", 3 * 3600, erwartung)
    except Exception:
        return {"fehler": "Die Zinskurve der EZB ist gerade nicht erreichbar."}


@app.get("/api/gamma")
def gamma_exposure_route(ticker: str = "SPY", modus: str = "monat"):
    from .markt.gamma import gamma_exposure
    try:
        return gamma_exposure(ticker, modus)
    except Exception:
        return {"fehler": "Das Gamma-Exposure konnte gerade nicht berechnet werden (Yahoo antwortet nicht?)."}


@app.get("/api/volaflaeche")
def volatilitaetsoberflaeche(ticker: str = "SPY"):
    try:
        return oberflaeche(ticker)
    except Exception:
        return {"fehler": "Die Volatilitätsoberfläche konnte gerade nicht erstellt werden."}


@app.get("/api/benchmark")
def benchmark_vergleich(extra: str | None = None, b: Benutzer = Ich):
    from .markt.namen import zusatz_werte
    try:
        return benchmark(b, [(w["ticker"], w["name"]) for w in zusatz_werte(extra, hoechstens=4)])
    except Exception:
        return {"fehler": "Der Benchmark-Vergleich konnte gerade nicht erstellt werden."}


@app.get("/api/volumen")
def preis_volumen_analyse(ticker: str | None = None, zeitraum: str = "5t", extra: str | None = None, depot: bool = True, b: Benutzer = Ich):
    if zeitraum not in ("heute", "5t", "1m", "3m", "kauf"):
        zeitraum = "5t"
    daten = _mit_markt(b, extra, depot)
    if "fehler" in daten:
        return daten
    try:
        return volumen_analyse(b, daten["positionen"], ticker, zeitraum)
    except Exception:
        return {"fehler": "Die Preis-Volumen-Analyse konnte gerade nicht berechnet werden."}


# ---- Großes Geld: Bewertung, Cashflow, Positionierung, Fonds & Insider, verdeckte Signale ----
def _mit_markt(b: Benutzer, extra: str | None, depot: bool = True) -> dict:
    """Depot-Positionen plus zusätzliche Werte aus dem Markt (?extra=XLK,SPY,AAPL; ?depot=false = nur diese).
    Die zusätzlichen sind mit extra=True markiert und zählen nicht in Depot-Summen oder -Gewichte."""
    from .markt.namen import zusatz_werte
    daten = _depot(b) if depot else {"positionen": []}
    if "fehler" in daten and not extra:
        return daten
    pos = [] if "fehler" in daten else list(daten["positionen"])
    pos += zusatz_werte(extra, {p.get("ticker") for p in pos})
    if not pos:
        return {"fehler": "Keine Werte ausgewählt."}
    return {"positionen": pos}


def _sicher(funktion, *args):
    try:
        return funktion(*args)
    except Exception:
        return {"fehler": "Die Daten konnten gerade nicht geladen werden."}


@app.get("/api/bewegung")
def bewegung_api(datum: str | None = None):
    aus = _sicher(marktbewegung.seite, datum)
    aus["ki_status"] = marktki.status()
    return aus


@app.get("/api/gg/stil")
def gg_stil():
    return _sicher(stil_seite)


@app.get("/api/gg/cashflow")
def gg_cashflow():
    return _sicher(cashflow_seite)


@app.get("/api/gg/cot")
def gg_cot():
    return _sicher(cot_seite)


@app.get("/api/gg/positionierung")
def gg_positionierung():
    aus = _sicher(positionierung_seite)
    aus["cot"] = _sicher(cot_seite)
    return aus


@app.get("/api/gg/fonds")
def gg_fonds(extra: str | None = None, depot: bool = True, b: Benutzer = Ich):
    daten = _mit_markt(b, extra, depot)
    return _sicher(fonds_seite, [] if "fehler" in daten else daten["positionen"])


@app.get("/api/gg/nacht")
def gg_nacht(extra: str | None = None, depot: bool = True, b: Benutzer = Ich):
    """Punkt 32: Rendite über Nacht gegen im Handel für Leitindizes und die eigenen Aktien."""
    from .nutzer.nacht import nacht_seite
    daten = _mit_markt(b, extra, depot)
    return _sicher(nacht_seite, [] if "fehler" in daten else daten["positionen"])


@app.get("/api/gg/signale")
def gg_signale(extra: str | None = None, depot: bool = True, b: Benutzer = Ich):
    daten = _mit_markt(b, extra, depot)
    aus = _sicher(signale_seite)
    aus["short"] = _sicher(short_meine, [] if "fehler" in daten else daten["positionen"])
    return aus


# ---- Weltkarte (Phase 5) ----
def _karte_aktion(request: Request, aktion):
    from .nutzer.karte import KarteFehler
    if request.headers.get("x-vinter") != "karte":  # siehe export_hochladen
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    try:
        return aktion()
    except KarteFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Das konnte nicht gespeichert werden."}


@app.get("/api/karte/maerkte")
def karte_maerkte():
    from .markt.karte import KartenFehler, maerkte
    try:
        return maerkte()
    except KartenFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Die Indexkurse konnten gerade nicht geladen werden."}


@app.get("/api/karte/geopolitik")
def karte_geopolitik(b: Benutzer = Ich):
    from .nutzer.karte import KarteFehler, geopolitik
    daten = _depot(b)
    try:
        return geopolitik(b, [] if "fehler" in daten else daten["positionen"])
    except KarteFehler as fehler:
        return {"fehler": str(fehler)}


@app.post("/api/karte/brennpunkte")
def karte_brennpunkte(request: Request, daten: dict = Body(...), b: Benutzer = Ich):
    from .nutzer.karte import brennpunkte_speichern
    return _karte_aktion(request, lambda: brennpunkte_speichern(b, daten))


@app.get("/api/karte/lage")
def karte_lage(b: Benutzer = Ich):
    from .markt.lage import lage
    try:
        aus = lage()
    except Exception:
        return {"fehler": "Die Lagedaten konnten gerade nicht geladen werden."}
    daten = _depot(b)
    aus["depot"] = [] if "fehler" in daten else sorted({p["ticker"] for p in daten["positionen"] if p.get("ticker")})
    return aus


@app.get("/api/karte/schiffe")
def karte_schiffe(box: str | None = None):
    """box: sichtbarer Ausschnitt "Breite unten,Länge links,Breite oben,Länge rechts"; dort werden zusätzlich Schiffe geholt."""
    from .markt.lage import AIS
    sicht = None
    if box:
        try:
            la1, lo1, la2, lo2 = (round(float(x), 2) for x in box.split(","))
            if -90 <= la1 < la2 <= 90 and -180 <= lo1 < lo2 <= 180:
                sicht = [[la1, lo1], [la2, lo2]]
        except ValueError:
            pass
    return AIS.stand(sicht)


@app.get("/api/karte/waehrungen")
def karte_waehrungen(b: Benutzer = Ich):
    from .nutzer.karte import waehrungen
    daten = _depot(b)
    try:
        return waehrungen(b, [] if "fehler" in daten else daten["positionen"])
    except Exception:
        return {"fehler": "Die Wechselkurse konnten gerade nicht geladen werden."}


# ---- Chartanalyse ----
def _chart_aktion(request: Request, aktion):
    from .nutzer.chartanalyse import ChartFehler
    if request.headers.get("x-vinter") != "chart":  # siehe export_hochladen
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    try:
        return aktion()
    except ChartFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Das konnte nicht gespeichert werden."}


@app.get("/api/chart/liste")
def chart_liste(b: Benutzer = Ich):
    """Auswahl für die Chartanalyse: Depotwerte und Watchlist."""
    aus = {"depot": [], "watchlist": []}
    daten = _depot(b)
    if "fehler" not in daten:
        aus["depot"] = [{"ticker": p["ticker"], "name": p["name"]} for p in sorted(daten["positionen"], key=lambda p: -(p.get("wert_eur") or 0)) if p.get("ticker")]
    try:
        aus["watchlist"] = [{"ticker": w["ticker"], "name": w.get("name") or w["ticker"]} for w in watchlist._lesen(b)]
    except Exception:
        pass
    return aus


@app.get("/api/chart/analyse")
def chart_analyse(ticker: str, n: int = 5, b: Benutzer = Ich):
    from .markt.chartanalyse import analyse as chart_rechnen
    from .nutzer.chartanalyse import ChartFehler, ipo_angaben, pruefe_ticker
    try:
        ticker = pruefe_ticker(ticker)
    except ChartFehler as fehler:
        return {"fehler": str(fehler)}
    angaben = ipo_angaben(b, ticker)
    try:
        aus = dict(chart_rechnen(ticker, n, angaben.get("ipo_preis")))
    except ValueError:
        return {"fehler": f"Für {ticker} liefert Yahoo keine Tageskurse. Stimmt der Ticker?"}
    except Exception:
        return {"fehler": "Die Chartanalyse konnte gerade nicht berechnet werden. Später noch einmal versuchen."}
    aus["ipo_angaben"] = angaben
    return aus


@app.get("/api/chart/kerzen")
def chart_kerzen(ticker: str, intervall: str = "1d"):
    from .markt.chartanalyse import INTERVALLE, kerzen
    from .nutzer.chartanalyse import ChartFehler, pruefe_ticker
    if intervall not in INTERVALLE:
        return {"fehler": "Unbekanntes Intervall."}
    try:
        return kerzen(pruefe_ticker(ticker), intervall)
    except ChartFehler as fehler:
        return {"fehler": str(fehler)}
    except ValueError:
        return {"fehler": f"Für {ticker} gibt es in diesem Intervall keine Kurse."}
    except Exception:
        return {"fehler": "Die Kurse konnten gerade nicht geladen werden."}


@app.get("/api/chart/earnings")
def chart_earnings(ticker: str):
    """Quartalstermine für die Markierungen unten im Chart (Chartanalyse)."""
    from .markt.earnings_moves import chart_termine
    from .nutzer.chartanalyse import ChartFehler, pruefe_ticker
    try:
        return chart_termine(pruefe_ticker(ticker))
    except ChartFehler as fehler:
        return {"fehler": str(fehler), "termine": []}
    except Exception:
        return {"termine": []}


@app.get("/api/chart/kurs")
def chart_kurs(ticker: str):
    from .nutzer.chartanalyse import ChartFehler, pruefe_ticker
    try:
        k = kurs_eur(pruefe_ticker(ticker))
    except ChartFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Kein Kurs."}
    return {"preis": k.get("preis"), "stand": k.get("stand"), "status": k.get("status"), "aktualitaet": k.get("aktualitaet"), "aktualisiert": k.get("aktualisiert")}


@app.get("/api/chart/alerts")
def chart_alerts(b: Benutzer = Ich):
    from .nutzer.chartanalyse import alerts
    try:
        return alerts(b)
    except Exception:
        return {"fehler": "Die Alerts konnten gerade nicht geladen werden."}


@app.post("/api/chart/alerts")
def chart_alert_neu(request: Request, daten: dict = Body(...), b: Benutzer = Ich):
    from .nutzer.chartanalyse import alert_neu
    return _chart_aktion(request, lambda: alert_neu(b, daten))


@app.post("/api/chart/alerts/{kennung}/quittieren")
def chart_alert_quittieren(kennung: str, request: Request, b: Benutzer = Ich):
    from .nutzer.chartanalyse import alert_quittieren
    return _chart_aktion(request, lambda: alert_quittieren(b, kennung))


@app.delete("/api/chart/alerts/{kennung}")
def chart_alert_weg(kennung: str, request: Request, b: Benutzer = Ich):
    from .nutzer.chartanalyse import alert_loeschen
    return _chart_aktion(request, lambda: alert_loeschen(b, kennung))


@app.post("/api/chart/ipo/{ticker}")
def chart_ipo(ticker: str, request: Request, daten: dict = Body(...), b: Benutzer = Ich):
    from .nutzer.chartanalyse import ipo_speichern
    return _chart_aktion(request, lambda: ipo_speichern(b, ticker, daten))


@app.get("/api/chart/trades")
def chart_trades(b: Benutzer = Ich):
    from .nutzer.chartanalyse import trades
    try:
        return trades(b)
    except ExportFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Die Trade-Auswertung konnte gerade nicht berechnet werden."}


@app.get("/api/gg/geldfluss")
def gg_geldfluss():
    from .markt.geldfluss import geldfluss_seite
    return _sicher(geldfluss_seite)


@app.get("/api/exits")
def exits(b: Benutzer = Ich):
    daten = _depot(b)
    positionen = [] if "fehler" in daten else daten["positionen"]
    try:
        return exit_alarme(b, positionen)
    except Exception:
        return {"fehler": "Die Exit-Alarme konnten gerade nicht berechnet werden."}


@app.get("/api/wochenbericht")
def wochenbericht_export(schwelle: int = 35, b: Benutzer = Ich):
    """Lädt den Wochenbericht als Markdown-Datei herunter. schwelle = Relevanz-Regler der Seite in %."""
    daten = _depot(b)
    positionen = [] if "fehler" in daten else daten["positionen"]
    try:
        text = wochenbericht(b, positionen, daten.get("summen"), max(0, min(100, schwelle)) / 100)
    except Exception:
        return JSONResponse({"fehler": "Der Wochenbericht konnte gerade nicht erstellt werden."}, status_code=500)
    name = f"vinter-wochenbericht-{datetime.now():%Y-%m-%d}.md"
    return Response(text, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/zahlen")
def zahlen(b: Benutzer = Ich):
    try:
        return frische_zahlen(b)
    except Exception:
        return {"fehler": "Die Quartalszahlen konnten gerade nicht geladen werden."}


@app.get("/api/kopf")
def kopfzeile():
    try:
        oben = schlagzeile()
    except Exception:
        oben = None
    try:
        from .markt.handel.auswertung import neuigkeiten
        handel = neuigkeiten()
    except Exception:
        handel = []
    return {"mover": top_mover(), "schlagzeile": oben, "handel": handel}


@app.get("/api/zitate")
def reden_und_zitate():
    try:
        return auftritte()
    except Exception:
        return {"fehler": "Die Reden und Zitate konnten gerade nicht geladen werden."}


@app.get("/api/newsfeed")
def live_newsfeed(b: Benutzer = Ich):
    try:
        return newsfeed(b)
    except Exception:
        return {"fehler": "Der Newsfeed konnte gerade nicht geladen werden."}


@app.get("/api/verlauf")
def verlauf(vorboerse: bool = True, tr: bool = False, b: Benutzer = Ich):
    return depot_verlauf(b, vorboerse or tr)


@app.get("/api/verlauf/stunden")
def verlauf_stunden(vorboerse: bool = True, tr: bool = False, b: Benutzer = Ich):
    return stunden_verlauf(b, vorboerse or tr)


# ---- Handelsströme und Frühindikatoren (Punkt 31b) ----
@app.get("/api/handel")
def handel_seite():
    from .markt.handel import auswertung as A
    from .markt.handel.quellen import gruppen
    try:
        from .markt.handel.laender import liste
        return {"indikatoren": A.fruehindikatoren(), "gruppen": list(gruppen().values()), "laender": A.berichtslaender(),
                "iso": {str(v["num"]).zfill(3): k for k, v in liste().items()}}
    except Exception as fehler:
        return {"fehler": f"Die Handelsdaten konnten nicht gelesen werden ({type(fehler).__name__})."}


@app.get("/api/handel/stroeme")
def handel_stroeme(land: str = "CHN", richtung: str = "export", gruppe: str | None = None):
    from .markt.handel.auswertung import stroeme
    if richtung not in ("export", "import") or not re.fullmatch(r"[A-Za-z]{3}", land):
        return {"fehler": "Ungültige Auswahl."}
    try:
        return stroeme(land, richtung, gruppe or None)
    except Exception as fehler:
        return {"fehler": f"Die Handelsströme konnten nicht berechnet werden ({type(fehler).__name__})."}


@app.get("/api/handel/fluss")
def handel_fluss(land: str = "USA", jahr: str | None = None, gruppe: str | None = None, ordnung: str = "gesamt"):
    from .markt.handel.auswertung import fluss
    if not re.fullmatch(r"[A-Za-z]{3}", land) or (jahr and not re.fullmatch(r"\d{4}", jahr)) or ordnung not in ("gesamt", "export", "import"):
        return {"fehler": "Ungültige Auswahl."}
    try:
        return fluss(land, jahr, gruppe or None, ordnung)
    except Exception as fehler:
        return {"fehler": f"Der Handelsfluss konnte nicht berechnet werden ({type(fehler).__name__})."}


@app.get("/api/handel/paar")
def handel_paar(land: str, partner: str):
    from .markt.handel.auswertung import paar
    if not (re.fullmatch(r"[A-Za-z]{3}", land) and re.fullmatch(r"[A-Za-z]{3}", partner)):
        return {"fehler": "Ungültige Auswahl."}
    try:
        return paar(land, partner)
    except Exception as fehler:
        return {"fehler": f"Die Details konnten nicht berechnet werden ({type(fehler).__name__})."}


@app.get("/api/handel/engstellen")
def handel_engstellen():
    from .markt.handel.auswertung import engstellen
    return _sicher(engstellen)


@app.get("/api/handel/status")
def handel_status():
    from .markt.handel.auswertung import status
    return _sicher(status)


@app.post("/api/handel/neu-einlesen/{quelle}")
def handel_neu_einlesen(quelle: str, request: Request):
    """Gemeinsame Handelsdatenbank neu einlesen: nur der Admin (betrifft alle Nutzer)."""
    from .anmeldung import admin
    from .markt.handel.quellen import konfig, neu_einlesen
    if request.headers.get("x-vinter") != "handel":  # siehe export_hochladen
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    admin(request)
    if quelle not in (konfig().get("quellen") or {}):
        return {"fehler": "Unbekannte Quelle."}
    return {"ergebnis": neu_einlesen(quelle)}


@app.get("/api/handel/thesen")
def handel_thesen(b: Benutzer = Ich):
    from .nutzer.handel import HandelFehler, thesen_handel
    try:
        return thesen_handel(b)
    except HandelFehler as fehler:
        return {"fehler": str(fehler)}
    except Exception:
        return {"fehler": "Die Zuordnung zu den Thesen konnte nicht gelesen werden."}


@app.post("/api/handel/thesen/{these_id}")
def handel_these_zuordnen(these_id: str, request: Request, daten: dict = Body(...), b: Benutzer = Ich):
    from .nutzer.handel import HandelFehler, zuordnen
    if request.headers.get("x-vinter") != "handel":  # siehe export_hochladen
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    try:
        return zuordnen(b, these_id, daten.get("gruppen"))
    except HandelFehler as fehler:
        return {"fehler": str(fehler)}


# ---- Fundamentalanalyse (Punkt 24c) ----
_TICKER = re.compile(r"[A-Za-z0-9.\-^=]{1,15}")
_KENNZAHL = re.compile(r"[a-z0-9_]{1,40}")


def _ticker_liste(text: str | None, max_anzahl: int = 40) -> list[str]:
    return [t.strip().upper() for t in (text or "").split(",") if _TICKER.fullmatch(t.strip())][:max_anzahl]


def _fund(funktion, *args, **kw):
    from .markt.fundament.kennzahlen import FormelFehler
    from .markt.fundament.peers import PeerFehler
    try:
        return funktion(*args, **kw)
    except (LookupError, PeerFehler, FormelFehler) as fehler:
        return {"fehler": str(fehler)}
    except Exception as fehler:
        return {"fehler": f"Die Fundamentaldaten konnten gerade nicht geladen werden ({type(fehler).__name__})."}


@app.get("/api/fund/bibliothek")
def fund_bibliothek(b: Benutzer = Ich):
    from .markt.fundament.kennzahlen import KATEGORIEN, bibliothek
    from .nutzer.fundament import SetFehler, alle_sets
    bib = bibliothek()
    try:
        sets = alle_sets(b)
    except SetFehler as fehler:
        sets = [{**s, "eigen": False} for s in bib["sets"].values()]
        bib["fehler"].append(str(fehler))
    daten = _depot(b)
    depot = [p["ticker"] for p in daten.get("positionen", []) if p.get("ticker")] if "fehler" not in daten else []
    return {"kennzahlen": [{k: v for k, v in x.items() if k != "positiv"} for x in bib["kennzahlen"]],
            "kategorien": KATEGORIEN, "sets": sets, "fehler": bib["fehler"], "depot": depot}


@app.get("/api/fund/aktie")
def fund_aktie(ticker: str, art: str = "ttm", modus: str = "absolut", waehrung: str = "USD", bereinigt: bool = False):
    from .markt.fundament import speicher
    from .markt.fundament.kennzahlen import rechnen
    if not _TICKER.fullmatch(ticker) or art not in ("quartal", "jahr", "ttm") or modus not in ("absolut", "je_aktie", "wachstum") or waehrung not in ("USD", "EUR"):
        return {"fehler": "Ungültige Auswahl."}
    speicher.beobachten(ticker)
    aus = _fund(rechnen, ticker.upper(), art, modus, waehrung, bereinigt)
    if not aus.get("fehler"):
        aus["schaetzung_verlauf"] = speicher.verlauf(ticker)
    return aus


@app.get("/api/fund/kurs-gewinn")
def fund_kurs_gewinn(ticker: str, basis: str = "gewinn", jahre: int = 10, waehrung: str = "USD", bereinigt: bool = False):
    from .markt.fundament.ansichten import kurs_gewinn
    if not _TICKER.fullmatch(ticker) or basis not in ("gewinn", "umsatz", "fcf") or not 3 <= jahre <= 10 or waehrung not in ("USD", "EUR"):
        return {"fehler": "Ungültige Auswahl."}
    return _fund(kurs_gewinn, ticker.upper(), basis, jahre, waehrung, bereinigt)


@app.get("/api/fund/rendite")
def fund_rendite(ticker: str, von: str, bis: str | None = None, waehrung: str = "USD"):
    from .markt.fundament.ansichten import rendite_zerlegung
    if not _TICKER.fullmatch(ticker) or not re.fullmatch(r"\d{4}-\d{2}", von) or (bis and not re.fullmatch(r"\d{4}-\d{2}", bis)) or waehrung not in ("USD", "EUR"):
        return {"fehler": "Ungültige Auswahl."}
    return _fund(rendite_zerlegung, ticker.upper(), von, bis, waehrung)


@app.get("/api/fund/zeitreihen")
def fund_zeitreihen(werte: str, kennzahlen: str, art: str = "ttm", modus: str = "absolut", waehrung: str = "USD", bereinigt: bool = False):
    from .markt.fundament.ansichten import zeitreihen
    ks = [k for k in kennzahlen.split(",") if _KENNZAHL.fullmatch(k)][:6]
    if art not in ("quartal", "jahr", "ttm") or modus not in ("absolut", "je_aktie", "wachstum") or waehrung not in ("USD", "EUR"):
        return {"fehler": "Ungültige Auswahl."}
    return _fund(zeitreihen, _ticker_liste(werte, 6), ks, art, modus, waehrung, bereinigt)


@app.get("/api/fund/gruppen")
def fund_gruppen(ticker: str | None = None):
    from .markt.fundament.peers import gruppe_fuer, gruppen
    def holen():
        aus = {}
        if ticker and _TICKER.fullmatch(ticker):
            aus["fuer"] = gruppe_fuer(ticker)
        aus["gruppen"] = gruppen()
        return aus
    return _fund(holen)


@app.post("/api/fund/gruppe")
def fund_gruppe_speichern(request: Request, daten: dict = Body(...)):
    """Peer-Gruppen liegen in daten/markt/peers.yaml und gelten für alle: ändern darf nur der Admin."""
    from .anmeldung import admin
    from .markt.fundament.peers import speichern
    if request.headers.get("x-vinter") != "fundament":  # siehe export_hochladen
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    admin(request)
    return _fund(speichern, daten.get("id"), daten.get("name"), daten.get("werte") or [], daten.get("fuer"))


@app.delete("/api/fund/gruppe/{gid}")
def fund_gruppe_loeschen(gid: str, request: Request):
    from .anmeldung import admin
    from .markt.fundament.peers import loeschen
    if request.headers.get("x-vinter") != "fundament":
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    admin(request)
    loeschen(gid)
    return {"ok": True}


def _universum(gruppe: str | None, werte: str | None, b: Benutzer) -> list[str]:
    if werte:
        return _ticker_liste(werte, 60)
    if gruppe == "depot":
        d = _depot(b)
        return [p["ticker"] for p in d.get("positionen", []) if p.get("ticker")] if "fehler" not in d else []
    if gruppe and gruppe.startswith("sektor:"):
        from .markt.indizes import kennzahlen, mitglieder
        sek = gruppe[7:]
        k = kennzahlen("sp500")
        drin = [m["ticker"] for m in mitglieder("sp500") if m.get("sektor") == sek and m["ticker"] in k]
        return sorted(drin, key=lambda t: -(k[t].get("cap") or 0))[:40]
    from .markt.fundament.peers import gruppen
    g = next((x for x in gruppen() if x["id"] == gruppe), None)
    return g["werte"] if g else []


@app.get("/api/fund/vergleich")
def fund_vergleich(gruppe: str | None = None, werte: str | None = None, kennzahlen: str | None = None, fokus: str | None = None, waehrung: str = "USD", b: Benutzer = Ich):
    from .markt.fundament.peers import vergleich
    ks = [k for k in (kennzahlen or "").split(",") if _KENNZAHL.fullmatch(k)][:14] or None
    liste = _universum(gruppe, werte, b)
    if len(liste) < 2:
        return {"fehler": "Die Gruppe braucht mindestens zwei Werte."}
    return _fund(vergleich, liste, ks, fokus.upper() if fokus and _TICKER.fullmatch(fokus) else None, waehrung if waehrung in ("USD", "EUR") else "USD")


@app.get("/api/fund/normiert")
def fund_normiert(werte: str, zeitraum: str = "1y"):
    from .markt.fundament.peers import normiert
    if zeitraum not in ("6mo", "1y", "3y", "5y"):
        return {"fehler": "Ungültiger Zeitraum."}
    return _fund(normiert, _ticker_liste(werte, 30), zeitraum)


@app.get("/api/fund/streu")
def fund_streu(gruppe: str | None = None, werte: str | None = None, x: str = "wachstum_2j", y: str = "fwd_kgv", waehrung: str = "USD", b: Benutzer = Ich):
    from .markt.fundament.ansichten import streu
    if not (_KENNZAHL.fullmatch(x) and _KENNZAHL.fullmatch(y)):
        return {"fehler": "Ungültige Kennzahl."}
    liste = _universum(gruppe, werte, b)
    if len(liste) < 2:
        return {"fehler": "Zu wenige Werte in dieser Auswahl."}
    return _fund(streu, liste, x, y, waehrung if waehrung in ("USD", "EUR") else "USD")


@app.get("/api/fund/gegen")
def fund_gegen(wert: str, kennzahl: str = "nettogewinn", art: str = "ttm", jahre: int = 10, waehrung: str = "USD", b: Benutzer = Ich):
    from .markt.fundament.gegen import gegen
    from .markt.fundament.peers import gruppen
    if art not in ("ttm", "quartal", "jahr") or not 2 <= jahre <= 60 or waehrung not in ("USD", "EUR") or not _KENNZAHL.fullmatch(kennzahl) or len(wert) > 80:
        return {"fehler": "Ungültige Auswahl."}
    if wert == "index:sp500" or _TICKER.fullmatch(wert):
        return _fund(gegen, wert if wert == "index:sp500" else wert.upper(), kennzahl, art, jahre, waehrung)
    name = {"depot": "Mein Depot"}.get(wert) or (f"S&P 500 {wert[7:]}" if wert.startswith("sektor:") else next((g["name"] for g in gruppen() if g["id"] == wert), wert))
    liste = _universum(wert, None, b)[:25]
    if len(liste) < 2:
        return {"fehler": "In dieser Auswahl sind zu wenige Werte."}
    return _fund(gegen, wert, kennzahl, art, jahre, waehrung, liste, name)


@app.get("/api/fund/quartal")
def fund_quartal(wert: str, datum: str, art: str = "ttm", waehrung: str = "USD", b: Benutzer = Ich):
    from .markt.fundament.gegen import quartal_detail
    from .markt.fundament.peers import gruppen
    if art not in ("ttm", "quartal", "jahr") or waehrung not in ("USD", "EUR") or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", datum) or len(wert) > 80:
        return {"fehler": "Ungültige Auswahl."}
    if wert == "index:sp500":
        return {"fehler": "Für den S&P 500 gibt es nur die Gewinne, keine Quartalszahlen je Firma."}
    if _TICKER.fullmatch(wert):
        return _fund(quartal_detail, wert.upper(), datum, art, waehrung)
    name = {"depot": "Mein Depot"}.get(wert) or (f"S&P 500 {wert[7:]}" if wert.startswith("sektor:") else next((g["name"] for g in gruppen() if g["id"] == wert), wert))
    return _fund(quartal_detail, wert, datum, art, waehrung, _universum(wert, None, b)[:25], name)


@app.get("/api/fund/gebiete")
def fund_gebiete(kennzahl: str = "nettogewinn", art: str = "ttm", jahre: int = 10, waehrung: str = "USD"):
    from .markt.fundament.gegen import KENNZAHLEN, gebiete
    if kennzahl not in KENNZAHLEN or art not in ("ttm", "quartal") or not 2 <= jahre <= 15 or waehrung not in ("USD", "EUR"):
        return {"fehler": "Ungültige Auswahl."}
    return _fund(gebiete, kennzahl, art, jahre, waehrung)


@app.get("/api/fund/kachel")
def fund_kachel(ticker: str, waehrung: str = "USD"):
    from .markt.fundament.peers import kachel
    if not _TICKER.fullmatch(ticker):
        return {"fehler": "Ungültiger Ticker."}
    return _fund(kachel, ticker.upper(), waehrung if waehrung in ("USD", "EUR") else "USD")


@app.get("/api/fund/markt")
def fund_markt():
    from .markt.fundament import aggregat, ansichten, hintergrund
    return {"gesamt": _fund(ansichten.gesamtmarkt), "koerbe": _fund(aggregat.uebersicht), "schaetzungen": _fund(ansichten.schaetzungs_trend),
            "abruf": hintergrund.status()}


@app.post("/api/fund/sets")
def fund_set_speichern(request: Request, daten: dict = Body(...), b: Benutzer = Ich):
    from .nutzer.fundament import SetFehler, set_speichern
    if request.headers.get("x-vinter") != "fundament":
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    try:
        return {"sets": set_speichern(b, daten.get("name"), daten.get("kennzahlen"))}
    except SetFehler as fehler:
        return {"fehler": str(fehler)}


@app.delete("/api/fund/sets/{kid}")
def fund_set_loeschen(kid: str, request: Request, b: Benutzer = Ich):
    from .nutzer.fundament import set_loeschen
    if request.headers.get("x-vinter") != "fundament":
        return JSONResponse({"fehler": "Nicht erlaubt."}, status_code=403)
    return {"sets": set_loeschen(b, kid)}


# Muss als Letztes stehen, sonst würde es die /api-Adressen überdecken.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="seite")
