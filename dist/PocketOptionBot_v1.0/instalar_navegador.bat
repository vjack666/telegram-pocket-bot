@echo off
title Instalando navegador Playwright...
echo Descargando Chromium (~150MB)...
echo.
PocketOptionBot.exe --install-browsers 2>nul || (
    echo.
    where playwright >nul 2>&1 || pip install playwright
    python -m playwright install chromium
)
echo.
echo Listo. Ya puedes ejecutar PocketOptionBot.exe
pause
