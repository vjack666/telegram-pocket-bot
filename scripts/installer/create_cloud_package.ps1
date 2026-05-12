param(
    [string]$OutputDir = "./dist/cloud-installer",
    [string]$PackagePrefix = "pocket-option-cloud",
    [switch]$IncludeChromeProfile,
    [string]$ChromeUserDataDir = "$env:LOCALAPPDATA\Google\Chrome\User Data",
    [string]$ChromeProfileName = "Default"
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "[PACK] $Message" -ForegroundColor Cyan
}

function Copy-DirectoryBestEffort {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $null = robocopy $Source $Destination /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        Write-Warning "Robocopy reported issues (code=$code) for: $Source"
        Write-Warning "Some locked files may be missing. Close browsers and retry for full fidelity."
    }
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$packageName = "$PackagePrefix-$timestamp.zip"

$outputAbs = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDir))
$stagingRoot = Join-Path $outputAbs "staging_$timestamp"
$stagingProject = Join-Path $stagingRoot "project"

Write-Step "Project root: $projectRoot"
Write-Step "Output dir: $outputAbs"

New-Item -ItemType Directory -Path $outputAbs -Force | Out-Null
New-Item -ItemType Directory -Path $stagingProject -Force | Out-Null

$excludeDirs = @(
    ".git",
    ".venv",
    "venv",
    "env",
    "build",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache"
)

$excludeFiles = @(
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "Thumbs.db",
    ".DS_Store"
)

Write-Step "Copying project files..."
Get-ChildItem -LiteralPath $projectRoot -Force | ForEach-Object {
    $name = $_.Name
    if ($excludeDirs -contains $name) { return }

    $src = $_.FullName
    $dst = Join-Path $stagingProject $name

    if ($_.PSIsContainer) {
        Copy-DirectoryBestEffort -Source $src -Destination $dst
    }
    else {
        $skip = $false
        foreach ($pattern in $excludeFiles) {
            if ($name -like $pattern) {
                $skip = $true
                break
            }
        }
        if (-not $skip) {
            try {
                Copy-Item -LiteralPath $src -Destination $dst -Force
            }
            catch {
                Write-Warning "Could not copy file: $src"
            }
        }
    }
}

# Optional Chrome profile export (best-effort)
if ($IncludeChromeProfile) {
    Write-Step "Including Chrome profile data..."
    $chromeRoot = Join-Path $stagingRoot "external\chrome-user-data"
    New-Item -ItemType Directory -Path $chromeRoot -Force | Out-Null

    $localState = Join-Path $ChromeUserDataDir "Local State"
    $profileDir = Join-Path $ChromeUserDataDir $ChromeProfileName

    if (Test-Path -LiteralPath $localState) {
        Copy-Item -LiteralPath $localState -Destination (Join-Path $chromeRoot "Local State") -Force
    }
    else {
        Write-Warning "Chrome Local State not found at: $localState"
    }

    if (Test-Path -LiteralPath $profileDir) {
        Copy-DirectoryBestEffort -Source $profileDir -Destination (Join-Path $chromeRoot $ChromeProfileName)
    }
    else {
        Write-Warning "Chrome profile not found at: $profileDir"
    }
}

$manifest = [ordered]@{
    created_at = (Get-Date).ToString("o")
    project_root = $projectRoot
    include_chrome_profile = [bool]$IncludeChromeProfile
    chrome_profile_name = $ChromeProfileName
    notes = @(
        "Close Chrome before backup/restore for better consistency.",
        "Chrome cookies are OS-account protected (DPAPI) and may fail on different Windows users/machines.",
        "Telegram chats are cloud-side; local .session files preserve API login for bots/scripts."
    )
}

$manifestPath = Join-Path $stagingRoot "manifest.json"
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$zipPath = Join-Path $outputAbs $packageName
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

Write-Step "Creating zip package..."
Compress-Archive -Path (Join-Path $stagingRoot "*") -DestinationPath $zipPath -CompressionLevel Optimal

Write-Step "Cleaning staging..."
Remove-Item -LiteralPath $stagingRoot -Recurse -Force

Write-Host ""
Write-Host "Package created:" -ForegroundColor Green
Write-Host $zipPath -ForegroundColor Green
Write-Host ""
Write-Host "Tip: run with -IncludeChromeProfile only after closing Chrome." -ForegroundColor Yellow
