"""Telegram: öffentliche Kanäle in Echtzeit mitlesen (offizielle Telegram-API über Telethon, nur lesen).

Anmeldung im Tool (Konto → Einstellungen → Telegram, nur Admin): Telefonnummer → Code aus der Telegram-App →
falls eingerichtet das Cloud-Passwort. Das Passwort und der Code werden nie gespeichert. Gespeichert wird nur die
Sitzung (wie ein Login-Cookie) verschlüsselt in konten/keys.enc (TELEGRAM_SITZUNG) und der Vinter-Benutzer,
dem die Meldungen gehören (TELEGRAM_BENUTZER). Telegram-Meldungen sieht nur dieser Benutzer.

API-ID und API-Hash kommen aus den Einstellungen (TELEGRAM_API_ID, TELEGRAM_API_HASH; my.telegram.org).
Kanäle stehen in daten/markt/news_quellen.yaml unter telegram: kanaele.

Technik: Telethon arbeitet mit asyncio. Alles läuft in einem eigenen Thread mit eigener Ereignisschleife;
die Web-Schnittstelle schickt ihre Aufträge dorthin (_ausfuehren).
"""
import asyncio
import logging
import threading
import time
from datetime import timezone

from ... import geheimnisse
from ...einstellungen import key
from . import quellen, speicher

log = logging.getLogger(__name__)

STUFE = 2
NACHHOLEN = 30            # beim Verbinden je Kanal so viele letzte Nachrichten nachholen
PRUEFEN_ALLE = 60         # Sekunden: Kanalliste und Verbindung prüfen
_schleife: asyncio.AbstractEventLoop | None = None
_zustand = {"client": None, "kanaele": [], "verbunden": False, "fehler": None, "konto": None,
            "anmeldung": None}  # anmeldung: {client, telefon, code_hash} während des Einloggens
_sperre = threading.Lock()


class TelegramFehler(ValueError):
    pass


def _api() -> tuple[int, str]:
    api_id, api_hash = key("TELEGRAM_API_ID"), key("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise TelegramFehler("TELEGRAM_API_ID und TELEGRAM_API_HASH fehlen (Einstellungen → Keys).")
    try:
        return int(api_id), api_hash
    except ValueError:
        raise TelegramFehler("TELEGRAM_API_ID muss eine Zahl sein.")


def _neuer_client(sitzung: str = ""):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    api_id, api_hash = _api()
    return TelegramClient(StringSession(sitzung), api_id, api_hash, device_model="Vinter", app_version="2.0",
                          system_version="Server", receive_updates=True)


def _ausfuehren(coro, zeitlimit: float = 60):
    """Coroutine in der Telegram-Schleife ausführen und auf das Ergebnis warten (aus normalem Code heraus)."""
    if _schleife is None:
        raise TelegramFehler("Telegram ist noch nicht gestartet.")
    return asyncio.run_coroutine_threadsafe(coro, _schleife).result(zeitlimit)


# ---- Meldungen ----

def _meldung(kanal: str, nachricht, gesehen: str | None = None) -> dict | None:
    text = (nachricht.message or "").strip()
    if not text:
        return None  # nur Bild ohne Text
    besitzer = key("TELEGRAM_BENUTZER") or None
    zeilen = [z.strip() for z in text.splitlines() if z.strip()]
    zeit = nachricht.date.astimezone(timezone.utc).isoformat(timespec="seconds") if nachricht.date else speicher.jetzt()
    return {
        "schluessel": speicher.schluessel(f"telegram:{kanal}", str(nachricht.id)),
        "quelle": f"Telegram @{kanal}", "art": "telegram", "stufe": STUFE,
        "titel": speicher.sauber(zeilen[0], 500), "text": speicher.sauber("\n".join(zeilen[1:]), 2000),
        "link": f"https://t.me/{kanal}/{nachricht.id}", "zeit": zeit, "gesehen": gesehen or speicher.jetzt(),
        "nur_fuer": besitzer,
    }


def _speichern(meldungen: list[dict], kanal: str) -> None:
    meldungen = [m for m in meldungen if m]
    ids = speicher.einfuegen(meldungen)
    speicher.quelle_melden(f"Telegram @{kanal}", True, neue=len(ids))
    if ids:
        from .abruf import melden
        melden(ids)


async def _verbinden() -> None:
    """Mit gespeicherter Sitzung verbinden, Kanäle abonnieren und die letzten Nachrichten nachholen."""
    from telethon import events
    sitzung = geheimnisse.alle().get("TELEGRAM_SITZUNG")
    if not sitzung:
        _zustand.update(verbunden=False, fehler=None)
        return
    alt = _zustand.get("client")
    if alt:
        await alt.disconnect()
    client = _neuer_client(sitzung)
    await client.connect()
    if not await client.is_user_authorized():
        _zustand.update(verbunden=False, fehler="Die Telegram-Sitzung ist abgelaufen. Bitte neu anmelden.")
        await client.disconnect()
        return
    ich = await client.get_me()
    kanaele = [k.lstrip("@") for k in quellen.laden()["telegram"]["kanaele"]]
    erreichbar = {}
    for k in kanaele:
        try:
            erreichbar[k] = await client.get_entity(k)
        except Exception as f:
            speicher.quelle_melden(f"Telegram @{k}", False, fehler=f"Kanal nicht gefunden ({type(f).__name__})")
    nach_id = {getattr(e, "id", None): k for k, e in erreichbar.items()}

    @client.on(events.NewMessage(chats=list(erreichbar.values())))
    async def neu(ereignis):
        chat = await ereignis.get_chat()
        kanal = nach_id.get(getattr(chat, "id", None)) or getattr(chat, "username", None) or str(ereignis.chat_id)
        _speichern([_meldung(kanal, ereignis.message)], kanal)

    for k, e in erreichbar.items():
        try:
            nachrichten = await client.get_messages(e, limit=NACHHOLEN)
            # Nachgeholt, nicht live gesehen: "gesehen" = Zeit der Nachricht
            _speichern([_meldung(k, n, gesehen=n.date.astimezone(timezone.utc).isoformat(timespec="seconds")) for n in nachrichten], k)
        except Exception as f:
            speicher.quelle_melden(f"Telegram @{k}", False, fehler=f"Nachholen fehlgeschlagen ({type(f).__name__})")
    _zustand.update(client=client, kanaele=kanaele, verbunden=True, fehler=None,
                    konto=(ich.first_name or "") + (f" (@{ich.username})" if ich.username else ""))


async def _waechter() -> None:
    """Verbindung halten, Kanaländerungen in der YAML übernehmen."""
    while True:
        try:
            kanaele = [k.lstrip("@") for k in quellen.laden()["telegram"]["kanaele"]]
            client = _zustand.get("client")
            if geheimnisse.alle().get("TELEGRAM_SITZUNG") and (
                    not client or not client.is_connected() or kanaele != _zustand.get("kanaele")):
                await _verbinden()
        except Exception as f:
            _zustand.update(verbunden=False, fehler=f"{type(f).__name__}: {str(f)[:120]}")
            log.warning("Telegram: %s", f)
        await asyncio.sleep(PRUEFEN_ALLE)


def starte() -> None:
    global _schleife
    with _sperre:
        if _schleife is not None:
            return
        try:
            import telethon  # noqa: F401
        except ImportError:
            _zustand["fehler"] = "Paket telethon fehlt."
            return
        _schleife = asyncio.new_event_loop()

    def laufen():
        asyncio.set_event_loop(_schleife)
        _schleife.create_task(_waechter())
        _schleife.run_forever()
    threading.Thread(target=laufen, name="telegram", daemon=True).start()


# ---- Anmeldung im Tool ----

def status() -> dict:
    hat_api = bool(key("TELEGRAM_API_ID") and key("TELEGRAM_API_HASH"))
    return {"api": hat_api, "angemeldet": bool(geheimnisse.alle().get("TELEGRAM_SITZUNG")),
            "verbunden": _zustand["verbunden"], "konto": _zustand["konto"], "fehler": _zustand["fehler"],
            "kanaele": [k.lstrip("@") for k in quellen.laden()["telegram"]["kanaele"]],
            "wartet_auf": (_zustand["anmeldung"] or {}).get("schritt")}


async def _code_anfordern(telefon: str) -> None:
    alt = (_zustand["anmeldung"] or {}).get("client")
    if alt:
        await alt.disconnect()
    client = _neuer_client()
    await client.connect()
    gesendet = await client.send_code_request(telefon)
    _zustand["anmeldung"] = {"client": client, "telefon": telefon, "code_hash": gesendet.phone_code_hash,
                             "schritt": "code", "seit": time.time()}


def code_anfordern(telefon: str) -> dict:
    telefon = "".join(z for z in telefon if z.isdigit() or z == "+")
    if not telefon.startswith("+") or len(telefon) < 8:
        raise TelegramFehler("Bitte die Nummer mit Ländervorwahl eingeben, z. B. +49 170 1234567.")
    try:
        _ausfuehren(_code_anfordern(telefon))
    except TelegramFehler:
        raise
    except Exception as f:
        raise TelegramFehler(f"Telegram hat abgelehnt: {type(f).__name__}")
    return status()


async def _fertig(client, benutzer: str) -> None:
    geheimnisse.setzen("TELEGRAM_SITZUNG", client.session.save())
    geheimnisse.setzen("TELEGRAM_BENUTZER", benutzer)
    await client.disconnect()
    _zustand["anmeldung"] = None
    await _verbinden()


async def _code_senden(code: str, benutzer: str) -> None:
    from telethon.errors import SessionPasswordNeededError
    a = _zustand["anmeldung"]
    try:
        await a["client"].sign_in(phone=a["telefon"], code=code, phone_code_hash=a["code_hash"])
    except SessionPasswordNeededError:
        a["schritt"] = "passwort"
        return
    await _fertig(a["client"], benutzer)


def code_senden(code: str, benutzer: str) -> dict:
    a = _zustand["anmeldung"]
    if not a or a.get("schritt") != "code":
        raise TelegramFehler("Bitte zuerst die Telefonnummer eingeben.")
    code = "".join(z for z in code if z.isdigit())
    try:
        _ausfuehren(_code_senden(code, benutzer))
    except Exception as f:
        raise TelegramFehler(f"Der Code wurde nicht angenommen ({type(f).__name__}).")
    return status()


async def _passwort_senden(passwort: str, benutzer: str) -> None:
    a = _zustand["anmeldung"]
    await a["client"].sign_in(password=passwort)
    await _fertig(a["client"], benutzer)


def passwort_senden(passwort: str, benutzer: str) -> dict:
    a = _zustand["anmeldung"]
    if not a or a.get("schritt") != "passwort":
        raise TelegramFehler("Gerade wird kein Passwort erwartet.")
    try:
        _ausfuehren(_passwort_senden(passwort, benutzer))
    except Exception as f:
        raise TelegramFehler(f"Das Passwort wurde nicht angenommen ({type(f).__name__}).")
    return status()


async def _abmelden() -> None:
    client = _zustand.get("client")
    if client:
        try:
            await client.log_out()  # beendet die Sitzung auch bei Telegram (Geräteliste)
        except Exception:
            await client.disconnect()
    _zustand.update(client=None, verbunden=False, konto=None, kanaele=[])


def abmelden() -> dict:
    try:
        _ausfuehren(_abmelden())
    finally:
        geheimnisse.setzen("TELEGRAM_SITZUNG", None)
        geheimnisse.setzen("TELEGRAM_BENUTZER", None)
    return status()
