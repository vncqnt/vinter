"""Punkt 31b, persönlicher Teil: Warengruppen einer These zuordnen.

Die Zuordnung steht in handel_thesen.yaml im Ordner des Benutzers ({these_id: [warengruppe, …]}).
Auffällige Frühindikatoren dieser Warengruppen erscheinen dann bei der These.
"""
import yaml

from ..benutzer import Benutzer
from ..markt.handel.auswertung import fruehindikatoren
from ..markt.handel.quellen import gruppen
from .thesen import alle_thesen


class HandelFehler(ValueError):
    pass


def _datei(b: Benutzer):
    return b.ordner / "handel_thesen.yaml"


def zuordnung(b: Benutzer) -> dict:
    try:
        roh = yaml.safe_load(_datei(b).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except yaml.YAMLError:
        raise HandelFehler("handel_thesen.yaml ist nicht lesbar.")
    gueltig = set(gruppen())
    return {str(k): [g for g in (v or []) if g in gueltig] for k, v in roh.items() if isinstance(v, list)}


def zuordnen(b: Benutzer, these_id: str, liste) -> dict:
    if not isinstance(liste, list):
        raise HandelFehler("Bitte eine Liste von Warengruppen schicken.")
    ids = {t["id"] for t in alle_thesen(b)["thesen"]}
    if these_id not in ids:
        raise HandelFehler("Diese These gibt es nicht.")
    gueltig = set(gruppen())
    z = zuordnung(b)
    z[these_id] = [g for g in dict.fromkeys(liste) if g in gueltig]
    if not z[these_id]:
        z.pop(these_id)
    b.ordner.mkdir(parents=True, exist_ok=True)
    _datei(b).write_text(yaml.safe_dump(z, allow_unicode=True, sort_keys=True), encoding="utf-8")
    return thesen_handel(b)


def thesen_handel(b: Benutzer) -> dict:
    z = zuordnung(b)
    thesen = alle_thesen(b)["thesen"]
    ind = fruehindikatoren()
    aus = []
    for t in thesen:
        gr = z.get(t["id"], [])
        passend = [{k: i[k] for k in ("id", "name", "zeitraum_text", "yoy", "sigma", "auffaellig", "quelle_name", "veroeffentlicht", "version", "gruppe_name")}
                   for i in ind if i["gruppe"] in gr and i["zeitraum"]]
        aus.append({"id": t["id"], "name": t["name"], "gruppen": gr, "indikatoren": passend,
                    "auffaellig": [p for p in passend if p["auffaellig"]]})
    return {"thesen": aus, "gruppen": [{"id": k, "name": v["name"]} for k, v in gruppen().items()]}
