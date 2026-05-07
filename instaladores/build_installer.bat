@echo off
setlocal
title Pocket Option Bot - Crear Instalador Windows

echo.
echo ============================================================
echo   POCKET OPTION BOT - CREANDO INSTALADOR WINDOWS
echo ============================================================
echo.

:: ── Verificar que el bot fue compilado ───────────────────────
if not exist "..\dist\PocketOptionBot_v1.0\PocketOptionBot.exe" (
    echo  ERROR: No se encontro PocketOptionBot.exe
    echo  Ejecuta primero build_exe.bat para compilar el bot.
    pause
    exit /b 1
)

:: ── Buscar Inno Setup ─────────────────────────────────────────
set ISCC=""
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe"       set ISCC="C:\Program Files\Inno Setup 6\ISCC.exe"

if %ISCC%=="" (
    echo  ERROR: Inno Setup no esta instalado.
    echo  Descargalo gratis desde: https://jrsoftware.org/isdl.php
    pause
    exit /b 1
)

echo Compilando instalador...
%ISCC% "setup.iss"
if %errorlevel% neq 0 (
    echo  ERROR: Fallo la compilacion.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   LISTO: dist\PocketOptionBot-Setup-1.0.0.exe
echo ============================================================
echo.
pause
