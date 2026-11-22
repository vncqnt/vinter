# Vinter in Home Assistant: Anleitung für den Admin

Vinter ist ein persönliches Markt- und Depot-Dashboard. Es läuft als Add-on auf dem NUC, aber nur als „Gast“: kein Eintrag in der Seitenleiste, kein Zugriff auf Geräte oder Automationen von Home Assistant. Es hat einen eigenen Port (8099) und eine eigene Anmeldung. Ein Home-Assistant-Konto braucht dafür niemand.

Du brauchst als Admin nur drei Dinge zu tun: **einmal installieren**, **ab und zu aktualisieren** und **bei Problemen ins Protokoll schauen**. Alles andere (Konten, Keys, Depot-Datei) macht der Nutzer selbst in Vinter.

> In neueren Home-Assistant-Versionen können die Menüs leicht anders heißen. Gemeint ist immer der Bereich, in dem man Add-ons installiert.

---

## 1. Einmal installieren (ca. 30–45 Minuten, meist Wartezeit)

1. In Home Assistant: **Einstellungen → Add-ons → Add-on-Store** (Knopf unten rechts).
2. Oben rechts auf die **drei Punkte → Repositories**.
3. Diese Adresse eintragen und **Hinzufügen** klicken:
   `https://github.com/vncqnt/vinter`
4. Fenster schließen, Seite neu laden. Unten im Store erscheint ein Abschnitt **Vinter** mit dem Add-on **Vinter**.
5. Add-on öffnen → **Installieren**.
   - Das dauert lange (oft 15–30 Minuten), weil der NUC das Programm selbst baut. Die Seite kann solange offen bleiben oder geschlossen werden.
6. Nach der Installation im Reiter **Info**:
   - **Beim Booten starten**: an
   - **Watchdog**: an (startet Vinter neu, falls es abstürzt)
   - **In Seitenleiste anzeigen**: aus lassen
7. **Starten** klicken.
8. Reiter **Protokoll** öffnen und ca. 1 Minute warten, dann auf **Aktualisieren**. Dort steht eine Zeile wie:
   `VINTER EINRICHTUNG: Noch kein Konto. Einrichtungscode für das erste (Admin-)Konto: …`
   **Diesen Code an deinen Sohn geben.** Mit ihm legt er sein Konto an. Danach verschwindet der Code von selbst.
9. Die Adresse für deinen Sohn ist:
   `http://<IP-ADRESSE-DES-NUC>:8099`
   Die IP-Adresse steht unter **Einstellungen → System → Netzwerk**. Oft funktioniert auch `http://homeassistant.local:8099`.

Fertig. Mehr musst du für die Einrichtung nicht tun.

### Danach (macht dein Sohn): Daten vom PC übernehmen

1. Mit dem Einrichtungscode das Admin-Konto anlegen.
2. Am PC in Vinter: **Einstellungen → Umzug auf den NUC → Umzugsdatei herunterladen**.
3. Am NUC in Vinter: **Einstellungen → Umzug auf den NUC**, die Datei auswählen, **Einspielen**.
4. Am NUC unter **Keys und Einstellungen** die Keys neu eintragen (sie ziehen bewusst nicht mit um).

Das Einspielen löscht nichts: Was am NUC schon gespeichert ist, bleibt, ersetzte Dateien kommen vorher in eine Sicherung.

---

## 2. Updates

Wenn dein Sohn eine neue Version veröffentlicht hat:

1. **Einstellungen → Add-ons → Add-on-Store → drei Punkte → Nach Updates suchen**.
2. Bei **Vinter** erscheint **Aktualisieren**. Klicken und warten (wieder einige Minuten Bauzeit).

Alle Daten (Konten, Depots, Einstellungen) bleiben bei einem Update erhalten.

---

## 3. Sicherung

Vinter speichert alles im eigenen Datenordner des Add-ons. Der ist automatisch in den **normalen Home-Assistant-Sicherungen** enthalten (Einstellungen → System → Sicherungen), solange dort beim Sichern die Add-ons mit ausgewählt sind. Extra musst du nichts einrichten.

---

## 4. Wenn etwas nicht geht

| Problem | Was tun |
|---|---|
| Seite lädt nicht | Im Add-on Reiter **Info**: läuft es? Sonst **Starten**. Dann Reiter **Protokoll** ansehen. |
| Seite ist sehr langsam direkt nach dem Start | Normal. In den ersten Minuten lädt Vinter Marktdaten nach. |
| Port 8099 ist schon belegt | Reiter **Konfiguration → Netzwerk**: anderen Port eintragen (z. B. 8098), speichern, neu starten. Die Adresse ändert sich dann entsprechend. |
| Fehlermeldung im Protokoll | Die letzten Zeilen aus dem Protokoll an deinen Sohn schicken. Darin stehen keine Passwörter oder Keys. |
| Dein Sohn hat sein Passwort vergessen | Ein Zurücksetzen per Knopf gibt es noch nicht. In dem Fall nichts löschen oder neu installieren, sondern gemeinsam schauen. |

### Bitte nicht

- **Das Add-on nicht deinstallieren**, um ein Problem zu lösen. Beim Deinstallieren löscht Home Assistant den Datenordner, also alle Konten und Depots. Ein **Neustart** reicht fast immer.
- Unter **Konfiguration → Optionen** muss nichts eingetragen werden. API-Keys trägt dein Sohn im Tool selbst ein.

---

## 5. Später (nur auf Wunsch)

- **Zugriff von unterwegs und für Freunde:** über das Tailscale-Add-on. Freigegeben wird nur der NUC und dort nur Port 8099, nicht Home Assistant selbst. Die genauen Schritte stehen in Vinter unter Konto → Einstellungen.
- **KI-Zusammenfassungen:** ein zusätzliches Ollama-Add-on. Das wird erst eingerichtet, wenn es so weit ist.

---

## Was Vinter macht und was nicht

- Vinter liest nur Marktdaten aus kostenlosen öffentlichen Quellen und zeigt das Depot aus einer selbst hochgeladenen CSV-Datei.
- Keine Bank-Zugangsdaten, keine Käufe oder Verkäufe, kein Zugriff auf Home Assistant.
- Speicherbedarf: etwa 1–2 GB Arbeitsspeicher, einige GB auf der Festplatte für Zwischenspeicher.
