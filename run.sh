#!/bin/sh
# Start im Add-on: Daten in /data (bleibt bei Updates erhalten), Anmeldung mit Passwort.
set -e
mkdir -p /data
cd /app
echo "Vinter startet auf Port 8099."
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8099
