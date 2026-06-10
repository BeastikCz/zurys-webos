@echo off
cd /d "%~dp0"
echo ==================================================
echo   WebOS - vernostni bodovy shop
echo   Server: http://127.0.0.1:8000
echo   (Toto okno nechej otevrene - bezi v nem server)
echo   Ukonceni serveru: stiskni Ctrl+C
echo ==================================================
echo.
rem Po ~2 s otevri prohlizec (server se mezitim nastartuje)
start "" /b cmd /c "ping -n 3 127.0.0.1 >nul & start http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
echo.
echo Server byl ukoncen.
pause
