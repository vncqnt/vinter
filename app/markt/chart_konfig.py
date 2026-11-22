"""Alle Stellschrauben der automatischen Chartanalyse an einem Ort.

Ändern ist erlaubt: Die Rechnung in chartanalyse.py liest nur diese Werte. Nach einer Änderung
den Cache-Schlüssel CACHE_VERSION erhöhen, sonst bleiben alte Ergebnisse bis zum nächsten Tag stehen.
"""

CACHE_VERSION = 3
HISTORIE = "5y"                 # Tagesdaten von Yahoo (mindestens 2 Jahre gefordert)

# Etappe 1: Trend
SMA_KURZ = 50
SMA_LANG = 200
STEIGUNG_TAGE = 20              # Steigung der 200-Tage-Linie über so viele Handelstage
STEIGUNG_FLACH = 0.5            # unter ±0,5 % in 20 Tagen gilt die Linie als flach

# Etappe 2: Swings und Zonen
SWING_N = 5                     # Swing-Hoch: höher als die n Tage davor und danach
SWING_N_ERLAUBT = (2, 3, 4, 5, 7, 10, 15, 20)
ATR_TAGE = 14
ZONE_ATR_FAKTOR = 0.5           # Swing-Punkte innerhalb 0,5 × ATR bilden eine Zone
ZONE_MIN_BREITE_ATR = 0.25      # eine Zone ist mindestens so breit (sonst wäre sie eine Linie)
ZONEN_TAGE = 504                # Swing-Punkte aus den letzten 2 Jahren
GEWICHT_BERUEHRUNG = 0.45       # Score = Gewichte × (Berührungen, Aktualität, Volumen), je 0 bis 1
GEWICHT_AKTUALITAET = 0.35
GEWICHT_VOLUMEN = 0.20
BERUEHRUNG_VOLL = 4             # ab so vielen Berührungen gibt es die volle Punktzahl
HALBWERTSZEIT_TAGE = 120        # nach 120 Handelstagen zählt eine Berührung nur noch halb
VOLUMEN_VOLL = 0.08             # 8 % des gesamten Volumens in der Zone = volle Punktzahl
MAX_ZONEN = 5                   # höchstens so viele Zonen im Chart
NAEHE_PROZENT = 20              # Kandidaten höchstens 20 % vom Kurs entfernt ...
NAEHE_ATR = 4                   # ... oder 4 ATR, wenn der Wert sehr schwankt

# Volumenprofil
PROFIL_TAGE = 252               # sichtbarer Zeitraum für das Profil (1 Jahr)
PROFIL_STUFEN = 48              # Preisstufen
PROFIL_KNOTEN = 3               # die stärksten Knoten als zusätzliche Zonen
PROFIL_KNOTEN_MIN = 1.4         # ein Knoten hat mindestens das 1,4-Fache des Durchschnitts

# Ausbruchsregel
AUSBRUCH_PROZENT = 2.0          # Schluss mehr als 2 % jenseits der Zone
AUSBRUCH_VOLUMEN_TAGE = 20      # und Volumen über dem 20-Tage-Schnitt

# Etappe 3: Momentum, Volumen, Volatilität
PERFORMANCE_TAGE = {"1M": 21, "3M": 63, "6M": 126, "12M": 252}
RS_TAGE = 63                    # relative Stärke als Zahl über 3 Monate
RS_LINIE_TAGE = 252             # Linie über 1 Jahr
RS_VERGLEICH = ("SPY", "QQQ")   # dazu der Sektor-ETF
RVOL_TAGE = 20
RVOL_AUFFAELLIG = 2.0           # ab dem Doppelten gilt das Volumen als auffällig
STOP_ATR = (1.5, 2.0)           # Stop-Abstände unter der nächsten Unterstützung
RSI_TAGE = 14

# Etappe 4: IPO-Modus
IPO_TAGE = 60                   # unter 60 Handelstagen: IPO-Modus

# Etappe 5: Ereignisse
EREIGNIS_TAGE = 10

# Etappe 6: Trades
TRADE_MIN = 8                   # so viele Trades je Gruppe, bevor ein Vergleich gezeigt wird
TRADE_NAH_WIDERSTAND_ATR = 1.0  # "nahe am Widerstand": höchstens 1 ATR darunter oder in der Zone
TRADE_WOCHEN = (1, 4, 12)
