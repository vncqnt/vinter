@echo off
cd /d "%~dp0"
rem Beendet einen noch laufenden alten Vinter-Server samt Hilfsprozessen, sonst antwortet weiter der alte.
rem Gemeint sind: alle "uvicorn app.main:app"-Prozesse, ihre Hilfsprozesse und alles, was an Port 8000 hängt.
powershell -NoProfile -Command "$alle = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\"; $ziel = @($alle | Where-Object { $_.CommandLine -like '*uvicorn app.main:app*' } | ForEach-Object { $_.ProcessId }); $ziel += @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess }); $alle | Where-Object { $ziel -contains $_.ProcessId -or $ziel -contains $_.ParentProcessId } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Vinter startet. Dieses Fenster offen lassen, Beenden mit Strg+C.
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:8000"
rem --reload: Ändert sich der Programmcode in app\, startet der Server von selbst neu.
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir app
pause
