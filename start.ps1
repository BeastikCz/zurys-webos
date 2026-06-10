# WebOS - spousteci skript (PowerShell)
# Spusteni:  powershell -ExecutionPolicy Bypass -File .\start.ps1
Set-Location -Path $PSScriptRoot

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Nenalezeno .venv. Vytvarim a instaluji zavislosti..." -ForegroundColor Yellow
    python -m venv .venv
    & $py -m pip install -r requirements.txt
}

Write-Host "==================================================" -ForegroundColor Magenta
Write-Host "  WebOS - vernostni bodovy shop"
Write-Host "  Server: http://127.0.0.1:8000"
Write-Host "  Ukonceni: Ctrl+C"
Write-Host "==================================================" -ForegroundColor Magenta

# Otevri prohlizec, az server odpovi (max ~15 s)
Start-Job -ScriptBlock {
    for ($i = 0; $i -lt 30; $i++) {
        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health" -UseBasicParsing -TimeoutSec 1 | Out-Null
            Start-Process "http://127.0.0.1:8000"; break
        } catch { Start-Sleep -Milliseconds 500 }
    }
} | Out-Null

& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000
