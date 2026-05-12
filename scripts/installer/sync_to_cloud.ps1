param(
    [string]$SourceDir = "C:\Users\v_jac\Desktop\poket option",
    [string]$TargetDir = "C:\pocket-option-cloud",
    [switch]$IncludeEnv,
    [switch]$IncludeRuntime,
    [switch]$PauseAtEnd
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "[SYNC] $Message" -ForegroundColor Cyan
}

function Copy-Dir {
    param(
        [Parameter(Mandatory = $true)][string]$From,
        [Parameter(Mandatory = $true)][string]$To
    )

    if (-not (Test-Path -LiteralPath $From)) {
        Write-Step "Skip (missing): $From"
        return
    }

    New-Item -ItemType Directory -Path $To -Force | Out-Null
    $null = robocopy $From $To /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD __pycache__ .pytest_cache .mypy_cache .ruff_cache
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        throw "Robocopy failed for $From (exit code: $code)"
    }
}

function Copy-File {
    param(
        [Parameter(Mandatory = $true)][string]$From,
        [Parameter(Mandatory = $true)][string]$To
    )

    if (-not (Test-Path -LiteralPath $From)) {
        Write-Step "Skip (missing): $From"
        return
    }

    Copy-Item -LiteralPath $From -Destination $To -Force
}

function Assert-FileSynced {
    param(
        [Parameter(Mandatory = $true)][string]$SourceFile,
        [Parameter(Mandatory = $true)][string]$TargetFile
    )

    if (-not (Test-Path -LiteralPath $SourceFile)) {
        throw "Verification failed: source missing -> $SourceFile"
    }
    if (-not (Test-Path -LiteralPath $TargetFile)) {
        throw "Verification failed: target missing -> $TargetFile"
    }

    $srcHash = (Get-FileHash -LiteralPath $SourceFile -Algorithm SHA256).Hash
    $dstHash = (Get-FileHash -LiteralPath $TargetFile -Algorithm SHA256).Hash
    if ($srcHash -ne $dstHash) {
        throw "Verification failed: hash mismatch -> $TargetFile"
    }
}

$sourceAbs = [System.IO.Path]::GetFullPath($SourceDir)
$targetAbs = [System.IO.Path]::GetFullPath($TargetDir)

if (-not (Test-Path -LiteralPath $sourceAbs)) {
    throw "Source not found: $sourceAbs"
}
if (-not (Test-Path -LiteralPath $targetAbs)) {
    throw "Target not found: $targetAbs"
}

Write-Step "Source: $sourceAbs"
Write-Step "Target: $targetAbs"

# Carpetas de codigo/documentacion (sin perfil/cookies/sesiones)
$dirsToSync = @(
    "src",
    "scripts",
    "docs",
    "data"
)

foreach ($d in $dirsToSync) {
    Copy-Dir -From (Join-Path $sourceAbs $d) -To (Join-Path $targetAbs $d)
}

# Archivos raiz utiles
$filesToSync = @(
    "main.py",
    "requirements.txt",
    "README.md",
    ".env.example",
    ".gitignore",
    "ejemplo.md"
)

foreach ($f in $filesToSync) {
    Copy-File -From (Join-Path $sourceAbs $f) -To (Join-Path $targetAbs $f)
}

if ($IncludeEnv) {
    Copy-File -From (Join-Path $sourceAbs ".env") -To (Join-Path $targetAbs ".env")
    Write-Step "Included .env"
}

if ($IncludeRuntime) {
    Copy-Dir -From (Join-Path $sourceAbs "runtime") -To (Join-Path $targetAbs "runtime")
    Write-Step "Included runtime"
}

# Verificaciones mínimas para evitar falsos positivos de sync.
Assert-FileSynced -SourceFile (Join-Path $sourceAbs "src\core\engine.py") -TargetFile (Join-Path $targetAbs "src\core\engine.py")
Assert-FileSynced -SourceFile (Join-Path $sourceAbs "main.py") -TargetFile (Join-Path $targetAbs "main.py")
Write-Step "Verification OK: engine.py y main.py sincronizados"

Write-Host ""
Write-Host "Sync completed successfully." -ForegroundColor Green
Write-Host ""
Write-Host "Sessions and browser profile were NOT touched." -ForegroundColor Yellow

if ($PauseAtEnd) {
    Write-Host ""
    Read-Host "Presiona Enter para cerrar"
}
