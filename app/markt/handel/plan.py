"""Abrufplan: ein Hintergrund-Thread entscheidet jede Minute, welche Quelle dran ist.

- Noch keine Daten: einmal alles laden (Historie), danach nur noch Neues.
- Im Veröffentlichungsfenster (Kalender in handel_quellen.yaml): alle takt_minuten kurz prüfen, ob der
  erwartete Monat da ist; sobald er da ist, laden und wieder selten prüfen.
- Außerhalb: einmal am Tag kurz prüfen (Comtrade: einmal im Monat alles, gebündelt).
- Kontingent erreicht: Pause bis morgen. Fehler nach allen Versuchen: Meldung auf der Status-Seite,
  nächster Versuch frühestens nach 30 Minuten.
- Nachladen auf Wunsch (Chord-Diagramm, Comtrade-Länder): Warteschlange, im selben Thread.
"""
import threading
import time
from datetime import datetime, timedelta

from . import speicher as S
from .quellen import AbrufFehler, Abruf, Comtrade, Pausiert, erwartet, hat_daten, hat_zeitraum, im_fenster, konfig, quelle

_gestartet = False
_warteschlange: list[str] = []
_sperre = threading.Lock()
_laeuft = {"quelle": None, "seit": None}


def nachladen_comtrade(iso: str) -> bool:
    """Stellt ein Land in die Warteschlange, höchstens einmal je Woche (meldet es nichts, nicht bei jedem Aufruf neu)."""
    zuletzt = S.zustand(f"nachgeladen:{iso}")
    if zuletzt and (datetime.now() - datetime.fromisoformat(zuletzt)).days < 7:
        return False
    with _sperre:
        if iso in _warteschlange or len(_warteschlange) > 20:
            return False
        _warteschlange.append(iso)
    return True


def in_warteschlange() -> list[str]:
    with _sperre:
        return list(_warteschlange)


def laeuft() -> dict:
    return dict(_laeuft)


def _faellig(qid: str, cfg: dict, st: dict, jetzt: datetime) -> str | None:
    """Gibt "voll", "pruefen" oder None zurück."""
    def vor(feld):
        t = st.get(feld)
        return (jetzt - datetime.fromisoformat(t)).total_seconds() / 60 if t else 1e9
    if st.get("pause_bis") and st["pause_bis"] > jetzt.isoformat():
        return None
    if vor("letzter_fehler") < 30 or (_laeuft.get("quelle") == qid):
        return None
    if not hat_daten(qid):
        return "voll" if vor("letzter_versuch") > 30 else None
    k = cfg.get("kalender") or {}
    if "monatlich" in k:
        letzt = st.get("letzter_erfolg") or ""
        return "voll" if im_fenster(cfg, jetzt) and letzt[:7] != jetzt.strftime("%Y-%m") and vor("letzter_versuch") > 60 else None
    e = erwartet(cfg, jetzt.date())
    if im_fenster(cfg, jetzt) and (not e or not hat_zeitraum(qid, e)):
        return "pruefen" if vor("letzte_pruefung") >= int(k.get("takt_minuten") or 10) else None
    return "pruefen" if vor("letzte_pruefung") >= 24 * 60 else None


def lauf_einer_quelle(qid: str, art: str) -> None:
    q, cfg = quelle(qid)
    if not q:
        return
    st = S.zustand(f"plan:{qid}", {})
    jetzt = datetime.now()
    st["letzter_versuch"] = jetzt.isoformat(timespec="seconds")
    S.zustand_setzen(f"plan:{qid}", st)  # sofort merken, damit ein langer Lauf nicht doppelt startet
    grund = q.bereit()
    if grund:
        st["fehler"] = grund
        st["letzter_fehler"] = st["letzter_versuch"]
        S.zustand_setzen(f"plan:{qid}", st)
        return
    a = Abruf(qid, cfg)
    _laeuft.update(quelle=qid, seit=st["letzter_versuch"])
    try:
        if art == "pruefen":
            st["letzte_pruefung"] = st["letzter_versuch"]
            e = erwartet(cfg, jetzt.date())
            neu = q.neu_da(a, e) if hasattr(q, "neu_da") else True
            if not neu:
                S.zustand_setzen(f"plan:{qid}", st | {"fehler": None})
                return
        erg = q.lauf(a, art == "voll")
        st.update(letzter_erfolg=datetime.now().isoformat(timespec="seconds"), fehler=None, letzte_pruefung=datetime.now().isoformat(timespec="seconds"))
        S.protokollieren(qid, True, f"{'Vollständig geladen' if art == 'voll' else 'Neue Daten geladen'}: {erg}, {a.anfragen} Anfragen")
    except Pausiert as f:
        st["pause_bis"] = (datetime.now().replace(hour=0, minute=5) + timedelta(days=1)).isoformat(timespec="seconds")
        st["fehler"] = str(f)
        S.protokollieren(qid, False, f"Pausiert: {f}")
    except Exception as f:  # noqa: BLE001 - jede Quelle darf ausfallen, ohne den Rest zu stören
        st["fehler"] = f"{type(f).__name__}: {f}"[:300]
        st["letzter_fehler"] = datetime.now().isoformat(timespec="seconds")
        S.protokollieren(qid, False, st["fehler"])
    finally:
        _laeuft.update(quelle=None, seit=None)
        S.zustand_setzen(f"plan:{qid}", st)


def _nachladen() -> None:
    with _sperre:
        iso = _warteschlange[0] if _warteschlange else None
    if not iso:
        return
    q, cfg = quelle("comtrade")
    try:
        if isinstance(q, Comtrade) and not q.bereit():
            _laeuft.update(quelle="comtrade", seit=datetime.now().isoformat(timespec="seconds"))
            S.zustand_setzen(f"nachgeladen:{iso}", datetime.now().isoformat(timespec="seconds"))
            q.land_jahr(Abruf("comtrade", cfg), iso)
    except Exception as f:  # noqa: BLE001
        S.protokollieren("comtrade", False, f"Nachladen {iso}: {f}")
    finally:
        _laeuft.update(quelle=None, seit=None)
        with _sperre:
            if iso in _warteschlange:
                _warteschlange.remove(iso)


def _schleife() -> None:
    time.sleep(20)  # Start des Servers nicht ausbremsen
    while True:
        try:
            _nachladen()
            jetzt = datetime.now()
            for qid, cfg in (konfig().get("quellen") or {}).items():
                q, cfg = quelle(qid)
                if not q:
                    continue
                art = _faellig(qid, cfg, S.zustand(f"plan:{qid}", {}), jetzt)
                if art:
                    lauf_einer_quelle(qid, art)
                    _nachladen()
        except Exception as f:  # noqa: BLE001
            S.protokollieren("plan", False, f"Abrufplan: {f}")
        time.sleep(60)


def starte() -> None:
    global _gestartet
    if _gestartet:
        return
    _gestartet = True
    threading.Thread(target=_schleife, name="handel", daemon=True).start()
